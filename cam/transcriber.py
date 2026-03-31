# cam/transcriber.py
import os
import subprocess
import sys
import threading


class AudioTranscriber:
    """
    Monitora registros/audio/ e transcreve .wav sem .txt correspondente.
    Roda o worker como subprocesso separado para isolar o CTranslate2.
    """
    POLL_INTERVAL = 60  # segundos entre verificações

    def __init__(self, audio_dir: str = "registros/audio"):
        self.audio_dir = audio_dir
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="Transcriber")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _has_pending(self) -> bool:
        try:
            files = os.listdir(self.audio_dir)
        except FileNotFoundError:
            return False
        wavs = {f[:-4] for f in files if f.endswith(".wav")}
        txts = {f[:-4] for f in files if f.endswith(".txt")}
        return bool(wavs - txts)

    def _run_worker(self) -> None:
        """Dispara o worker como subprocesso isolado."""
        subprocess.run(
            [sys.executable, "-m", "cam.transcribe_worker", self.audio_dir],
            capture_output=True,
        )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            if self._has_pending():
                self._run_worker()
            self._stop_event.wait(self.POLL_INTERVAL)
