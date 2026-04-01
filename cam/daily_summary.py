"""
cam/daily_summary.py — Geração e recuperação de resumos diários persistentes.

O resumo é gerado pelo Claude com base nos frames e eventos do dia,
e armazenado no SQLite para alimentar a memória de longo prazo do assistente.
"""
import os
import sqlite3
from datetime import date, timedelta

import httpx

from cam.db import get_frames_for_date, get_recent_summaries, insert_daily_summary

SUMMARY_PROMPT = """\
Você recebeu uma lista de observações de câmera de segurança residencial durante um dia.
Cada entrada tem um horário, uma descrição da cena e os eventos detectados.

Gere um resumo conciso (máximo 4 frases) do dia, cobrindo:
- O que aconteceu de mais relevante
- Rotina observada (horários de chegada, saída, trabalho)
- Qualquer anomalia ou evento incomum

Responda apenas com o resumo, sem títulos ou formatação.\
"""


def generate_daily_summary(
    conn: sqlite3.Connection,
    target_date: str,
    region: str,
    model_id: str,
    bearer_token: str,
) -> str:
    """Gera resumo do dia via Bedrock e salva no banco. Retorna o texto."""
    frames = get_frames_for_date(conn, target_date)
    if not frames:
        return f"Nenhuma observação registrada em {target_date}."

    lines = []
    for f in frames:
        ts = f["timestamp"][11:16] if f["timestamp"] else "??"
        events = f["events"] or []
        ev_str = f"[{', '.join(events)}]" if events else ""
        desc = (f["description"] or "")[:120]
        lines.append(f"{ts} {ev_str} {desc}")

    observations = "\n".join(lines[:80])  # limita para não exceder tokens

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "system": SUMMARY_PROMPT,
        "messages": [
            {"role": "user", "content": f"Data: {target_date}\n\n{observations}"}
        ],
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
        summary = resp.json()["content"][0]["text"].strip()
    except Exception as e:
        summary = f"Erro ao gerar resumo: {e}"

    insert_daily_summary(conn, target_date, summary)
    return summary


def ensure_yesterday_summarized(
    conn: sqlite3.Connection,
    region: str,
    model_id: str,
    bearer_token: str,
) -> None:
    """Gera o resumo de ontem se ainda não existir."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    existing = conn.execute(
        "SELECT 1 FROM daily_summaries WHERE date = ?", (yesterday,)
    ).fetchone()
    if not existing:
        generate_daily_summary(conn, yesterday, region, model_id, bearer_token)


def get_context_for_assistant(conn: sqlite3.Connection, days: int = 7) -> str:
    """Retorna últimos N resumos formatados para o contexto do assistente."""
    summaries = get_recent_summaries(conn, days)
    if not summaries:
        return ""
    parts = [f"{s['date']}: {s['summary']}" for s in summaries]
    return "\n".join(parts)
