"""
cam/transcribe_once.py — Transcrição de um único WAV para stdout.

Executado como subprocesso pelo assistente para evitar segfault do CTranslate2.
Uso: python -m cam.transcribe_once <wav_path>
"""
import sys


def run(wav_path: str) -> None:
    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        wav_path,
        language="pt",
        beam_size=5,
        vad_filter=True,
        vad_parameters={
            "threshold": 0.7,
            "min_speech_duration_ms": 250,
            "min_silence_duration_ms": 500,
        },
        no_speech_threshold=0.7,
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    print(text)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    run(sys.argv[1])
