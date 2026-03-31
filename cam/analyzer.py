# cam/analyzer.py
import base64
import json
import httpx

SYSTEM_PROMPT = """Voce e um sistema de monitoramento de segurança residencial.
Analise a imagem e retorne um JSON com dois campos:
- "description": string com descricao objetiva da cena em portugues
- "events": lista de objetos com "event_type" e "confidence" (0.0 a 1.0)

Tipos de eventos possiveis:
- person_sitting_at_pc: pessoa sentada na cadeira gamer proxima ao computador
- person_approaching: pessoa se aproximando do computador
- person_leaving_house: pessoa saindo de casa
- person_away_from_pc: pessoa se afastando do computador
- door_open: porta visivelmente aberta
- owner_recognized: a pessoa visivel na cena E a mesma pessoa da foto de referencia fornecida
- unknown_person_detected: ha uma pessoa visivel na cena que NAO E a pessoa da foto de referencia

Retorne APENAS o JSON, sem texto adicional."""

SYSTEM_PROMPT_NO_REF = """Voce e um sistema de monitoramento de segurança residencial.
Analise a imagem e retorne um JSON com dois campos:
- "description": string com descricao objetiva da cena em portugues
- "events": lista de objetos com "event_type" e "confidence" (0.0 a 1.0)

Tipos de eventos possiveis:
- person_sitting_at_pc: pessoa sentada na cadeira gamer proxima ao computador
- person_approaching: pessoa se aproximando do computador
- person_leaving_house: pessoa saindo de casa
- person_away_from_pc: pessoa se afastando do computador
- door_open: porta visivelmente aberta

Retorne APENAS o JSON, sem texto adicional."""


def analyze_frame(
    jpeg_bytes: bytes,
    region: str,
    model_id: str,
    bearer_token: str,
    owner_jpeg: bytes | None = None,
) -> dict:
    image_b64 = base64.standard_b64encode(jpeg_bytes).decode("utf-8")

    if owner_jpeg:
        owner_b64 = base64.standard_b64encode(owner_jpeg).decode("utf-8")
        content = [
            {"type": "text", "text": "FOTO DE REFERENCIA DO MORADOR (para identificacao):"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": owner_b64}},
            {"type": "text", "text": "IMAGEM ATUAL DA CAMERA:"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": "Compare as imagens e analise a cena atual."},
        ]
        system = SYSTEM_PROMPT
    else:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": "Analise esta imagem."},
        ]
        system = SYSTEM_PROMPT_NO_REF

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }

    url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }

    response = httpx.post(url, json=payload, headers=headers,
                          timeout=httpx.Timeout(connect=10, read=90, write=90, pool=10))
    response.raise_for_status()

    text = response.json()["content"][0]["text"]

    # Strip markdown code fences if present (```json ... ```)
    clean = text.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        # parts = ['', 'json\n{...}', ''] — content is always index 1
        clean = parts[1].lstrip("json").strip() if len(parts) >= 2 else clean

    try:
        data = json.loads(clean)
        description = data.get("description", clean)
        # Ensure description is plain text — no JSON markers
        if isinstance(description, str):
            description = description.strip().strip("`").strip()
        return {
            "description": description,
            "events": data.get("events", []),
            "raw": text,
        }
    except json.JSONDecodeError:
        return {"description": clean, "events": [], "raw": text}
