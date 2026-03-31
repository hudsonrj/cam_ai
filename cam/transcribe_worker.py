"""
Script standalone para transcrição de áudio.
Executado como subprocesso pelo AudioTranscriber para isolar o CTranslate2.
Uso: python -m cam.transcribe_worker <audio_dir>
"""
import os
import sys


def run(audio_dir: str) -> None:
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")

    wavs = {f[:-4] for f in os.listdir(audio_dir) if f.endswith(".wav")}
    txts = {f[:-4] for f in os.listdir(audio_dir) if f.endswith(".txt")}
    pending = sorted(wavs - txts)

    for name in pending:
        wav_path = os.path.join(audio_dir, name + ".wav")
        txt_path = os.path.join(audio_dir, name + ".txt")
        try:
            segments, info = model.transcribe(
                wav_path, language="pt", beam_size=5,
                vad_filter=True,
                vad_parameters={
                    "threshold": 0.7,
                    "min_speech_duration_ms": 250,
                    "min_silence_duration_ms": 500,
                },
                no_speech_threshold=0.7,
            )
            lines = [f"[{s.start:05.1f}s -> {s.end:05.1f}s] {s.text.strip()}" for s in segments]
            text = "\n".join(lines) if lines else "(sem fala detectada)"
            header = f"Arquivo: {name}.wav\nIdioma: {info.language} ({info.language_probability:.0%})\n\n"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(header + text + "\n")
        except Exception as e:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"ERRO NA TRANSCRICAO: {e}\n")


if __name__ == "__main__":
    audio_dir = sys.argv[1] if len(sys.argv) > 1 else "registros/audio"
    if os.path.isdir(audio_dir):
        run(audio_dir)
