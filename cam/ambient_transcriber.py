"""
cam/ambient_transcriber.py - Transcricao de chunks de audio ambiente.

Consome AudioChunks da queue, transcreve com faster-whisper,
filtra alucinacoes, persiste no banco e descarta o WAV.
"""
import os
import queue
import sqlite3
import threading

from cam.ambient_recorder import AudioChunk

# Padroes de alucinacao comuns do Whisper em silencio ou ruido
_TRASH = {
    "", ".", "..", "...", "obrigado.", "obrigado", "legenda", "legendas",
    "legenda por", "transcrição", "[música]", "[applause]", "[music]",
    "thanks for watching", "se inscreva", "curta e compartilhe",
}


def _is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    if t in _TRASH:
        return True
    words = t.split()
    # menos de 3 palavras
    if len(words) < 3:
        return True
    # texto muito repetitivo (ex: "e e e e e e")
    if len(set(words)) <= 2 and len(words) > 4:
        return True
    # frases repetidas (ex: "o que e isso? o que e isso?")
    import re
    sentences = [s.strip() for s in re.split(r'[.!?]+', t) if s.strip()]
    if len(sentences) >= 2:
        unique = set(sentences)
        # 50%+ das frases sao identicas (2 frases iguais = 1 unica / 2 total = 0.5)
        if len(unique) / len(sentences) <= 0.5:
            return True
    # segmentos repetidos de 4+ palavras
    if len(words) >= 8:
        half = len(words) // 2
        # verifica se a primeira metade se repete na segunda
        chunk = " ".join(words[:half])
        rest = " ".join(words[half:])
        if chunk in rest or rest in chunk:
            return True
    return False


class AmbientTranscriber:
    """Consome AudioChunks, transcreve e persiste no banco."""

    def __init__(self, chunk_queue: queue.Queue, db_path: str = "data/cam.db",
                 bedrock_cfg: dict | None = None, bearer_token: str = ""):
        self.chunk_queue = chunk_queue
        self.db_path = db_path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model = None      # lazy: inicializado na primeira transcricao

        # Classificador de audio em lote
        self._batch_classifier = None
        if bedrock_cfg and bedrock_cfg.get("model_id"):
            from cam.behavior_classifier import AudioBatchClassifier
            self._batch_classifier = AudioBatchClassifier(
                db_path=db_path,
                region=bedrock_cfg.get("region", "us-east-1"),
                model_id=bedrock_cfg["model_id"],
                bearer_token=bearer_token,
            )

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="ambient-transcriber",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)

    # ── model ────────────────────────────────────────────────────────────────

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel("small", device="cpu", compute_type="int8")
        return self._model

    # ── loop principal ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk: AudioChunk = self.chunk_queue.get(timeout=2)
            except queue.Empty:
                continue

            try:
                self._process(chunk)
            except Exception:
                pass
            finally:
                try:
                    os.unlink(chunk.wav_path)
                except Exception:
                    pass

    def _process(self, chunk: AudioChunk) -> None:
        model = self._get_model()
        segments, _ = model.transcribe(
            chunk.wav_path,
            language="pt",
            beam_size=5,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.6,
                "min_speech_duration_ms": 300,
                "min_silence_duration_ms": 500,
            },
            no_speech_threshold=0.7,
        )

        text = " ".join(s.text.strip() for s in segments).strip()
        if _is_hallucination(text):
            return

        duration_s = (chunk.chunk_end - chunk.chunk_start).total_seconds()
        chunk_start_iso = chunk.chunk_start.strftime("%Y-%m-%d %H:%M:%S")
        chunk_end_iso   = chunk.chunk_end.strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            from cam.ambient_store import insert_transcript
            insert_transcript(
                conn,
                chunk_start=chunk_start_iso,
                chunk_end=chunk_end_iso,
                device_name=chunk.device_name,
                device_index=chunk.device_index,
                text=text,
                duration_s=duration_s,
            )
        finally:
            conn.close()

        # Envia para o classificador de audio em lote
        if self._batch_classifier:
            self._batch_classifier.add_transcript(
                chunk_start_iso, chunk_end_iso, chunk.device_name, text,
            )
