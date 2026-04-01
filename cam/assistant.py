"""
cam/assistant.py — Assistente conversacional com visão de câmera.

Combina o feed da câmera, histórico de eventos do dia e voz do usuário
para manter uma conversa natural sobre o ambiente e a rotina.
"""
import base64
import os
import subprocess
import sys
import tempfile
import threading
import wave

import httpx
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1

SYSTEM_PROMPT = """\
Você é um assistente inteligente chamado CAM que monitora a casa e conversa com o morador Hudson.

Você tem acesso a:
1. Uma foto do que a câmera está vendo AGORA
2. Um resumo dos eventos e descrições registradas durante o dia
3. O histórico da conversa atual

Sua personalidade:
- Amigável, direto e útil
- Fala como um colega atento, não como um robô formal
- Comenta proativamente sobre o que vê quando relevante
- Lembra de eventos do dia e os menciona quando pertinente

Você pode:
- Descrever o que está vendo na câmera em tempo real
- Responder perguntas sobre o que aconteceu durante o dia
- Dar dicas baseadas no que observou (postura, iluminação, visitas, etc.)
- Conversar sobre qualquer assunto, usando o contexto visual como pano de fundo

Responda sempre em português, de forma concisa (2-4 frases) a menos que mais detalhes sejam pedidos.
Não repita o contexto nem os dados técnicos — apenas converse naturalmente.\
"""


class ConversationAssistant:
    """Gerencia histórico de conversa e chamadas ao Bedrock com visão."""

    def __init__(self, region: str, model_id: str, bearer_token: str):
        self.region = region
        self.model_id = model_id
        self.bearer_token = bearer_token
        self._history: list[dict] = []

    def chat(
        self,
        user_text: str,
        jpeg_bytes: bytes | None = None,
        context_summary: str = "",
    ) -> str:
        """Envia mensagem e retorna a resposta do assistente."""
        content: list[dict] = []

        if context_summary:
            content.append({
                "type": "text",
                "text": f"[Eventos do dia: {context_summary}]",
            })

        if jpeg_bytes:
            b64 = base64.standard_b64encode(jpeg_bytes).decode()
            content.append({"type": "text", "text": "Câmera agora:"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })

        content.append({"type": "text", "text": user_text})

        messages = list(self._history) + [{"role": "user", "content": content}]

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "system": SYSTEM_PROMPT,
            "messages": messages,
        }

        url = (
            f"https://bedrock-runtime.{self.region}.amazonaws.com"
            f"/model/{self.model_id}/invoke"
        )
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        resp = httpx.post(
            url, json=payload, headers=headers,
            timeout=httpx.Timeout(connect=10, read=60, write=60, pool=10),
        )
        resp.raise_for_status()
        reply = resp.json()["content"][0]["text"].strip()

        # Persiste histórico sem imagem (eficiência)
        self._history.append({
            "role": "user",
            "content": [{"type": "text", "text": user_text}],
        })
        self._history.append({"role": "assistant", "content": reply})

        # Mantém até 20 turnos (40 mensagens)
        if len(self._history) > 40:
            self._history = self._history[-40:]

        return reply

    def clear(self) -> None:
        self._history.clear()


class MicRecorder:
    """Grava áudio do microfone enquanto estiver ativo."""

    def __init__(self, device: int | None = None):
        self.device = device
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._thread: threading.Thread | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        self._frames = []
        self._recording = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop_and_save(self) -> str | None:
        """Para gravação e salva WAV em arquivo temporário. Retorna path ou None."""
        self._recording = False
        if self._thread:
            self._thread.join(timeout=5)

        if not self._frames:
            return None

        audio = np.concatenate(self._frames, axis=0)
        if len(audio) < SAMPLE_RATE * 0.5:  # menos de 0.5s — ignora
            return None

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(tmp.name, "w") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            pcm = (audio * 32767).astype(np.int16)
            wf.writeframes(pcm.tobytes())

        return tmp.name

    def _loop(self) -> None:
        chunk = 1024
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS,
            dtype="float32", device=self.device, blocksize=chunk,
        ) as stream:
            while self._recording:
                data, _ = stream.read(chunk)
                self._frames.append(data[:, 0].copy())


def transcribe_wav(wav_path: str) -> str:
    """Transcreve WAV via subprocess isolado (evita segfault do CTranslate2)."""
    result = subprocess.run(
        [sys.executable, "-m", "cam.transcribe_once", wav_path],
        capture_output=True, text=True, timeout=60,
    )
    return result.stdout.strip()
