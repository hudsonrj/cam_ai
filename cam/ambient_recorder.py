"""
cam/ambient_recorder.py - Gravacao continua de ambiente com failover entre microfones.

Suporta lista de dispositivos em ordem de prioridade: o primeiro e o primario,
os demais sao contingencia. A cada 3 minutos verifica se o dispositivo ativo
esta respondendo — se nao, troca para o proximo da lista.
"""
import os
import queue
import tempfile
import threading
import wave
from datetime import datetime
from typing import NamedTuple

import numpy as np
import sounddevice as sd

CHUNK_SECONDS = 30
SAMPLE_RATE = 16000
CHANNELS = 1
RMS_SILENCE_THRESHOLD = 0.001   # descarta chunks de silencio puro
HEALTH_CHECK_INTERVAL = 180     # segundos entre verificacoes de saude
STARTUP_GRACE = 30              # segundos de graca apos iniciar antes de checar saude


class AudioChunk(NamedTuple):
    wav_path: str
    device_index: int
    device_name: str
    chunk_start: datetime
    chunk_end: datetime


def list_input_devices() -> list[dict]:
    """Retorna todos os dispositivos de entrada disponiveis no sistema."""
    devices = []
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                devices.append({
                    "index": i,
                    "name": dev["name"],
                    "channels": min(dev["max_input_channels"], CHANNELS),
                    "samplerate": int(dev["default_samplerate"]),
                })
    except Exception:
        pass
    return devices


class DeviceRecorder:
    """Grava continuamente de um unico dispositivo em chunks."""

    def __init__(self, device_index: int, device_name: str,
                 chunk_queue: queue.Queue):
        self.device_index = device_index
        self.device_name = device_name
        self.chunk_queue = chunk_queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at: datetime | None = None
        self.last_successful_read: datetime | None = None

    def start(self) -> None:
        self._stop.clear()
        self.started_at = datetime.now()
        self.last_successful_read = None
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name=f"ambient-rec-{self.device_index}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=CHUNK_SECONDS + 5)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_healthy(self) -> bool:
        """Retorna False se o dispositivo parece ter falhado."""
        if self.started_at is None or not self.is_alive():
            return False
        age = (datetime.now() - self.started_at).total_seconds()
        if age < STARTUP_GRACE:
            return True  # graca no inicio
        if self.last_successful_read is None:
            return False  # nunca leu nada
        stale = (datetime.now() - self.last_successful_read).total_seconds()
        return stale < HEALTH_CHECK_INTERVAL

    def _loop(self) -> None:
        while not self._stop.is_set():
            frames: list[np.ndarray] = []
            chunk_start = datetime.now()

            try:
                with sd.InputStream(
                    device=self.device_index,
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="float32",
                    blocksize=1024,
                ) as stream:
                    target = SAMPLE_RATE * CHUNK_SECONDS
                    collected = 0
                    while collected < target and not self._stop.is_set():
                        data, _ = stream.read(1024)
                        self.last_successful_read = datetime.now()
                        frames.append(data[:, 0].copy())
                        collected += len(data)
            except Exception:
                self._stop.wait(3)
                continue

            if not frames or self._stop.is_set():
                continue

            chunk_end = datetime.now()
            audio = np.concatenate(frames)

            # Descarta silencio
            rms = float(np.sqrt(np.mean(audio ** 2)))
            if rms < RMS_SILENCE_THRESHOLD:
                continue

            self._save_chunk(audio, chunk_start, chunk_end)

    def _save_chunk(self, audio: np.ndarray,
                    chunk_start: datetime, chunk_end: datetime) -> None:
        try:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False,
                prefix=f"amb_{self.device_index}_",
            )
            with wave.open(tmp.name, "w") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                pcm = (audio * 32767).astype(np.int16)
                wf.writeframes(pcm.tobytes())

            self.chunk_queue.put(AudioChunk(
                wav_path=tmp.name,
                device_index=self.device_index,
                device_name=self.device_name,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
            ))
        except Exception:
            pass


class AmbientRecorder:
    """
    Grava de um dispositivo por vez, em ordem de prioridade.
    A cada HEALTH_CHECK_INTERVAL segundos verifica se o dispositivo ativo
    ainda esta respondendo; se nao, troca para o proximo da lista.
    """

    def __init__(self, chunk_queue: queue.Queue | None = None,
                 device_indices: list[int] | None = None):
        self.chunk_queue = chunk_queue or queue.Queue(maxsize=200)
        self._priority = device_indices   # None = descobrir na hora
        self._current: DeviceRecorder | None = None
        self._current_pos = 0             # posicao na lista _priority
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._lock = threading.Lock()

    # ── public ───────────────────────────────────────────────────────────────

    def start(self) -> list[dict]:
        """Inicia gravacao. Retorna lista com o dispositivo ativo."""
        if self._priority is None:
            self._priority = [d["index"] for d in list_input_devices()]

        self._stop.clear()
        self._current_pos = 0
        started = self._activate(pos=0)

        self._monitor = threading.Thread(
            target=self._monitor_loop, daemon=True, name="ambient-monitor",
        )
        self._monitor.start()
        return started

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._current:
                self._current.stop()
                self._current = None
        if self._monitor:
            self._monitor.join(timeout=10)

    @property
    def active_devices(self) -> list[str]:
        with self._lock:
            if self._current and self._current.is_alive():
                idx = self._current_pos
                total = len(self._priority) if self._priority else 1
                label = f"{self._current.device_name} [{idx + 1}/{total}]"
                return [label]
        return []

    # ── internals ────────────────────────────────────────────────────────────

    def _device_info(self, dev_index: int) -> dict | None:
        for d in list_input_devices():
            if d["index"] == dev_index:
                return d
        return None

    def _activate(self, pos: int) -> list[dict]:
        """Para o gravador atual e inicia o dispositivo na posicao pos."""
        if not self._priority:
            return []

        total = len(self._priority)
        # tenta cada dispositivo a partir de pos, uma volta completa
        for offset in range(total):
            candidate_pos = (pos + offset) % total
            dev_index = self._priority[candidate_pos]
            dev = self._device_info(dev_index)
            if dev is None:
                continue

            rec = DeviceRecorder(dev["index"], dev["name"], self.chunk_queue)
            try:
                rec.start()
                with self._lock:
                    self._current = rec
                    self._current_pos = candidate_pos
                print(
                    f"[ambient] ativo: '{dev['name']}' "
                    f"(device {dev_index}, posicao {candidate_pos + 1}/{total})"
                )
                return [dev]
            except Exception as e:
                print(f"[ambient] falha ao abrir device {dev_index}: {e}")

        print("[ambient] nenhum dispositivo disponivel")
        return []

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(HEALTH_CHECK_INTERVAL)
            if self._stop.is_set():
                break

            with self._lock:
                rec = self._current
                pos = self._current_pos

            if rec is None:
                # sem dispositivo ativo — tenta o primeiro
                self._activate(pos=0)
                continue

            if not rec.is_healthy():
                print(
                    f"[ambient-monitor] '{rec.device_name}' sem resposta "
                    f"ha {HEALTH_CHECK_INTERVAL}s — tentando proximo..."
                )
                rec.stop()
                next_pos = (pos + 1) % len(self._priority)
                self._activate(pos=next_pos)
