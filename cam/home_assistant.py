"""
cam/home_assistant.py — Integração com Home Assistant.

Duas modalidades:
1. Push automático: envia todos os eventos detectados para o webhook do HA.
2. Action type "home_assistant": disparado por regras em config.yaml.

Configuração em config.yaml:
    home_assistant:
      webhook_url: "http://homeassistant.local:8123/api/webhook/cam_ai"
      token: "SEU_LONG_LIVED_TOKEN"   # opcional, para REST API
      cooldown_minutes: 1             # evita spam (padrão: 1 minuto)
"""
import json
import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

_last_sent: dict[str, datetime] = {}  # event_type → última vez enviado


def _cooldown_ok(event_type: str, cooldown_min: float) -> bool:
    last = _last_sent.get(event_type)
    if last is None:
        return True
    return (datetime.now() - last).total_seconds() / 60 >= cooldown_min


def push_event(
    event_type: str,
    confidence: float,
    description: str,
    camera_id: str,
    frame_id: int | None,
    ha_cfg: dict,
) -> dict:
    """
    Envia um evento para o Home Assistant via webhook.

    O HA recebe um POST com JSON:
        {
          "event_type": "unknown_person_detected",
          "confidence": 0.95,
          "description": "Pessoa desconhecida na porta",
          "camera_id": "main",
          "frame_id": 42,
          "timestamp": "2024-01-15T14:30:00"
        }

    No HA, configure um webhook automation trigger com o mesmo webhook_url.
    """
    webhook_url = ha_cfg.get("webhook_url", "").strip()
    if not webhook_url:
        return {"status": "skipped", "reason": "webhook_url não configurado"}

    cooldown = ha_cfg.get("cooldown_minutes", 1)
    if not _cooldown_ok(event_type, cooldown):
        return {"status": "skipped", "reason": "cooldown ativo"}

    payload = {
        "event_type": event_type,
        "confidence": confidence,
        "description": description,
        "camera_id": camera_id,
        "frame_id": frame_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    headers = {"Content-Type": "application/json"}
    token = ha_cfg.get("token", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = httpx.post(
            webhook_url,
            json=payload,
            headers=headers,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
        )
        resp.raise_for_status()
        _last_sent[event_type] = datetime.now()
        return {"status": "success", "http_status": resp.status_code}
    except Exception as e:
        logger.warning("Home Assistant push failed: %s", e)
        return {"status": "error", "reason": str(e)}


def push_state(
    entity_id: str,
    state: str,
    attributes: dict,
    ha_cfg: dict,
) -> dict:
    """
    Atualiza um sensor no Home Assistant via REST API.
    Requer token de longa duração (Long-Lived Access Token).

    Exemplo de uso em regras:
        - type: home_assistant
          entity_id: sensor.cam_ai_last_event
          state: "unknown_person_detected"
    """
    base_url = ha_cfg.get("base_url", "").strip().rstrip("/")
    token = ha_cfg.get("token", "").strip()
    if not base_url or not token:
        return {"status": "skipped", "reason": "base_url/token não configurados"}

    url = f"{base_url}/api/states/{entity_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {"state": state, "attributes": attributes}
    try:
        resp = httpx.post(url, json=body, headers=headers,
                          timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5))
        resp.raise_for_status()
        return {"status": "success", "http_status": resp.status_code}
    except Exception as e:
        return {"status": "error", "reason": str(e)}
