"""
cam/behavior_classifier.py - Classificacao comportamental de frames e audio.

Extrai eventos de alto nivel a partir de:
1. Analise de camera: beber, comer, reuniao, ligacao, etc.
2. Analise de audio em lote: topico, humor, entidades, tipo de conversa.

Usa Bedrock (Claude) para classificacao semantica.
Audio e processado em lotes de ~5 minutos para reduzir custo de API.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

import httpx


# ── Tipos de eventos comportamentais ─────────────────────────────────────────

CAMERA_BEHAVIOR_EVENTS = [
    "drink_water",          # bebendo agua
    "drink_other",          # bebendo outra bebida
    "eat",                  # comendo (descrever o que)
    "multiple_people",      # mais de uma pessoa visivel
    "phone_call",           # pessoa ao telefone
    "meeting_in_person",    # reuniao presencial
    "person_sleeping",      # dormindo/descansando
    "person_exercising",    # exercicio fisico
    "person_seen",          # pessoa identificada (quem)
]

AUDIO_CLASSIFICATION_PROMPT = """\
Voce analisa transcricoes de audio para classificar comportamento e contexto.

Dado um conjunto de transcricoes com timestamps, retorne um JSON com:
{
  "mood": "neutro|feliz|estressado|cansado|animado|triste",
  "stress_level": 0-10,
  "topics": ["trabalho", "pessoal", "familia", "saude", "entretenimento", "outro"],
  "conversation_type": "monologue|conversa|reuniao|ligacao|musica|silencio",
  "entities": [{"name": "...", "type": "empresa|pessoa|lugar|produto"}],
  "highlights": ["frase ou ponto importante 1", "frase ou ponto importante 2"],
  "language_detected": "pt|en|es|outro",
  "meeting_participants": 0,
  "summary": "resumo em uma frase do que foi falado"
}

Retorne APENAS o JSON, sem texto adicional.\
"""

CAMERA_BEHAVIOR_PROMPT = """\
Analise a imagem e identifique APENAS os seguintes comportamentos se presentes.
Retorne JSON com a lista de eventos observados:
{
  "behaviors": [
    {"type": "drink_water", "confidence": 0.9},
    {"type": "eat", "confidence": 0.8, "what": "descricao do alimento"},
    {"type": "phone_call", "confidence": 0.95},
    {"type": "multiple_people", "confidence": 1.0, "count": 2},
    {"type": "meeting_in_person", "confidence": 0.7},
    {"type": "person_exercising", "confidence": 0.6}
  ]
}

Tipos validos: drink_water, drink_other, eat, multiple_people, phone_call,
meeting_in_person, person_sleeping, person_exercising.
Se nenhum comportamento especial for observado, retorne {"behaviors": []}.
Retorne APENAS o JSON.\
"""


# ── Classificador de camera ───────────────────────────────────────────────────

def classify_camera_behaviors(
    jpeg_bytes: bytes,
    region: str,
    model_id: str,
    bearer_token: str,
) -> list[dict]:
    """
    Analisa um frame e retorna lista de eventos comportamentais detectados.
    Retorna lista de dicts com type, confidence, e campos extras (ex: what).
    """
    import base64
    b64 = base64.standard_b64encode(jpeg_bytes).decode()

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "system": CAMERA_BEHAVIOR_PROMPT,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": b64,
                }},
                {"type": "text", "text": "Identifique os comportamentos nesta imagem."},
            ],
        }],
    }

    url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers,
                          timeout=httpx.Timeout(connect=10, read=60, write=60, pool=10))
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        clean = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(clean)
        return data.get("behaviors", [])
    except Exception:
        return []


# ── Classificador de audio em lote ───────────────────────────────────────────

class AudioBatchClassifier:
    """
    Acumula transcricoes e processa em lotes de ~5 minutos.
    Extrai humor, topicos, entidades, tipo de conversa.
    """

    BATCH_SIZE = 10     # chunks por lote (10 x 30s = 5 minutos)

    def __init__(self, db_path: str, region: str, model_id: str, bearer_token: str):
        self.db_path = db_path
        self.region = region
        self.model_id = model_id
        self.bearer_token = bearer_token
        self._buffer: list[dict] = []
        self._lock = threading.Lock()

    def add_transcript(self, chunk_start: str, chunk_end: str,
                       device_name: str, text: str) -> None:
        with self._lock:
            self._buffer.append({
                "start": chunk_start,
                "end": chunk_end,
                "device": device_name,
                "text": text,
            })
            if len(self._buffer) >= self.BATCH_SIZE:
                batch = self._buffer[:]
                self._buffer.clear()
                threading.Thread(
                    target=self._process_batch, args=(batch,), daemon=True,
                ).start()

    def flush(self) -> None:
        with self._lock:
            if self._buffer:
                batch = self._buffer[:]
                self._buffer.clear()
                self._process_batch(batch)

    def _process_batch(self, batch: list[dict]) -> None:
        if not batch:
            return

        # Monta contexto para o Claude
        lines = [f"[{c['start'][11:16]}] ({c['device']}) {c['text']}" for c in batch]
        observations = "\n".join(lines)
        batch_start = batch[0]["start"]
        batch_end   = batch[-1]["end"]

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "system": AUDIO_CLASSIFICATION_PROMPT,
            "messages": [{
                "role": "user",
                "content": f"Transcricoes de {batch_start[:16]} a {batch_end[:16]}:\n\n{observations}",
            }],
        }

        url = (f"https://bedrock-runtime.{self.region}.amazonaws.com"
               f"/model/{self.model_id}/invoke")
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        try:
            resp = httpx.post(url, json=payload, headers=headers,
                              timeout=httpx.Timeout(connect=10, read=60, write=60, pool=10))
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"].strip()
            clean = text.lstrip("```json").lstrip("```").rstrip("```").strip()
            result = json.loads(clean)
        except Exception:
            return

        self._save_audio_classification(batch_start, result)

    def _save_audio_classification(self, timestamp: str, result: dict) -> None:
        from cam.behavior_store import insert_behavior_event

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            mood = result.get("mood", "neutro")
            stress = result.get("stress_level", 0)
            conv_type = result.get("conversation_type", "monologue")
            summary = result.get("summary", "")

            # Evento de humor
            if mood != "neutro":
                insert_behavior_event(conn, timestamp, "audio", f"mood_{mood}",
                                      metadata={"stress_level": stress, "summary": summary})

            # Estresse alto
            if stress >= 7:
                insert_behavior_event(conn, timestamp, "audio", "stress_detected",
                                      metadata={"level": stress, "summary": summary})

            # Tipo de conversa
            if conv_type in ("reuniao", "meeting", "ligacao", "phone_call"):
                insert_behavior_event(conn, timestamp, "audio", "meeting",
                                      metadata={
                                          "type": conv_type,
                                          "participants": result.get("meeting_participants", 0),
                                          "summary": summary,
                                      })

            # Topicos
            for topic in result.get("topics", []):
                insert_behavior_event(conn, timestamp, "audio", f"topic_{topic}",
                                      metadata={"summary": summary})

            # Entidades
            for entity in result.get("entities", []):
                name = entity.get("name", "")
                etype = entity.get("type", "")
                if name:
                    insert_behavior_event(conn, timestamp, "audio", "entity_mentioned",
                                          metadata={"entity": name, "type": etype})

        finally:
            conn.close()
