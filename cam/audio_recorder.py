# cam/audio_recorder.py
import os
import threading
import wave
from datetime import datetime

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 44100
CHANNELS = 1
DTYPE = "int16"
BYTES_PER_SAMPLE = 2
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
SILENCE_THRESHOLD = 500        # RMS abaixo disso = silencio (int16, 0-32767)
SILENCE_TIMEOUT_S = 3.0        # segundos de silencio para fechar arquivo


class AudioRecorder:
    def __init__(self, audio_dir: str = "registros/audio", device: int | None = None):
        self.audio_dir = audio_dir
        self.device = device
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq_id = self._next_id()

    def _next_id(self) -> int:
        os.makedirs(self.audio_dir, exist_ok=True)
        existing = [
            f for f in os.listdir(self.audio_dir) if f.endswith(".wav")
        ]
        if not existing:
            return 1
        ids = []
        for name in existing:
            try:
                ids.append(int(name.split("_")[0]))
            except ValueError:
                pass
        return max(ids) + 1 if ids else 1

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    @staticmethod
    def _rms(data: np.ndarray) -> float:
        return float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))

    def _record_loop(self) -> None:
        chunk_size = SAMPLE_RATE // 10  # 100ms por chunk

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=chunk_size,
            device=self.device,
        ) as stream:
            wf: wave.Wave_write | None = None
            bytes_written = 0
            silent_chunks = 0
            silence_chunks_limit = int(SILENCE_TIMEOUT_S / 0.1)  # chunks até fechar

            while not self._stop_event.is_set():
                try:
                    data, _ = stream.read(chunk_size)
                except sd.PortAudioError:
                    break
                rms = self._rms(data)

                if rms < SILENCE_THRESHOLD:
                    silent_chunks += 1
                    # Fecha arquivo se estava gravando e silencio excedeu limite
                    if wf is not None and silent_chunks >= silence_chunks_limit:
                        wf.close()
                        wf = None
                        bytes_written = 0
                    continue

                # Audio detectado — abre novo arquivo se necessário
                silent_chunks = 0
                if wf is None:
                    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    filename = f"{self._seq_id:05d}_{ts}.wav"
                    filepath = os.path.join(self.audio_dir, filename)
                    self._seq_id += 1
                    wf = wave.open(filepath, "w")
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(BYTES_PER_SAMPLE)
                    wf.setframerate(SAMPLE_RATE)

                wf.writeframes(data.tobytes())
                bytes_written += len(data.tobytes())

                if bytes_written >= MAX_BYTES:
                    wf.close()
                    wf = None
                    bytes_written = 0

            if wf is not None:
                wf.close()
