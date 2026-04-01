"""
cam/ambient_recorder.py - Gravacao continua de ambiente em todos os microfones.

Descobre todos os dispositivos de entrada ativos, grava chunks de CHUNK_SECONDS
segundos em paralelo, e coloca AudioChunk na queue para transcricao.
Os WAVs sao temporarios e descartados apos a transcricao.
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

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name=f"ambient-rec-{self.device_index}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=CHUNK_SECONDS + 5)

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
    """Inicia e gerencia gravadores para todos os microfones disponiveis."""

    def __init__(self, chunk_queue: queue.Queue | None = None,
                 device_indices: list[int] | None = None):
        self.chunk_queue = chunk_queue or queue.Queue(maxsize=200)
        self._device_indices = device_indices   # None = todos
        self._recorders: list[DeviceRecorder] = []

    def start(self) -> list[dict]:
        """Inicia gravacao. Retorna lista de dispositivos iniciados."""
        devices = list_input_devices()
        started = []
        for dev in devices:
            if (self._device_indices is not None
                    and dev["index"] not in self._device_indices):
                continue
            rec = DeviceRecorder(dev["index"], dev["name"], self.chunk_queue)
            try:
                rec.start()
                self._recorders.append(rec)
                started.append(dev)
            except Exception:
                pass
        return started

    def stop(self) -> None:
        for rec in self._recorders:
            rec.stop()
        self._recorders.clear()

    @property
    def active_devices(self) -> list[str]:
        return [r.device_name for r in self._recorders]
