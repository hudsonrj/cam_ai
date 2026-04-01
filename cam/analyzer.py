# cam/analyzer.py
import base64
import json
import httpx

SYSTEM_PROMPT_BASE = """Voce e um sistema de monitoramento residencial inteligente.
Analise a imagem e retorne um JSON com dois campos:
- "description": string com descricao objetiva e detalhada da cena em portugues
- "events": lista de objetos com "event_type", "confidence" (0.0 a 1.0) e campos opcionais

Tipos de eventos possiveis:

Presenca e movimento:
- person_sitting_at_pc: pessoa sentada na cadeira gamer proxima ao computador
- person_approaching: pessoa se aproximando do computador
- person_leaving_house: pessoa saindo de casa
- person_away_from_pc: pessoa se afastando do computador
- door_open: porta visivelmente aberta
- multiple_people: mais de uma pessoa visivel — adicione "count": N

Comportamento e habitos:
- drink_water: pessoa bebendo agua (copo, garrafa de agua)
- drink_other: pessoa bebendo outra bebida — adicione "what": "cafe|refrigerante|suco|etc"
- eat: pessoa comendo — adicione "what": "descricao do alimento se visivel"
- phone_call: pessoa ao telefone (celular no ouvido ou headset)
- meeting_in_person: reuniao presencial com duas ou mais pessoas
- person_sleeping: pessoa dormindo ou com cabeca apoiada como se dormisse
- person_exercising: pessoa fazendo exercicio fisico

{extra_events}
Retorne APENAS o JSON, sem texto adicional."""

# Mantém aliases para compatibilidade com imports existentes
SYSTEM_PROMPT = SYSTEM_PROMPT_BASE.format(extra_events=(
    "- owner_recognized: a pessoa visivel na cena E a mesma pessoa da foto de referencia fornecida\n"
    "- unknown_person_detected: ha uma pessoa visivel na cena que NAO E a pessoa da foto de referencia\n"
))
SYSTEM_PROMPT_NO_REF = SYSTEM_PROMPT_BASE.format(extra_events="")


def analyze_frame(
    jpeg_bytes: bytes,
    region: str,
    model_id: str,
    bearer_token: str,
    owner_jpeg: bytes | None = None,
    known_people: list[dict] | None = None,
) -> dict:
    """
    known_people: lista de {"name": str, "jpeg": bytes} para reconhecimento de visitantes.
    """
    image_b64 = base64.standard_b64encode(jpeg_bytes).decode("utf-8")

    all_people = []
    if owner_jpeg:
        all_people.append({"name": "morador (Hudson)", "jpeg": owner_jpeg})
    if known_people:
        all_people.extend(known_people)

    if all_people:
        names = ", ".join(p["name"] for p in all_people)
        extra = (
            f"- owner_recognized: a pessoa visivel E o morador Hudson\n"
            f"- unknown_person_detected: ha pessoa visivel que NAO E nenhuma das referencias\n"
            + "".join(
                f"- visitor_{p['name'].lower().replace(' ', '_')}_recognized:"
                f" a pessoa visivel e {p['name']}\n"
                for p in all_people if p["name"] != "morador (Hudson)"
            )
        )
        system = SYSTEM_PROMPT_BASE.format(extra_events=extra)

        content: list[dict] = []
        for p in all_people:
            b64 = base64.standard_b64encode(p["jpeg"]).decode()
            content.append({"type": "text", "text": f"FOTO DE REFERENCIA — {p['name']}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": b64}})
        content.append({"type": "text", "text": "IMAGEM ATUAL DA CAMERA:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": image_b64}})
        content.append({"type": "text", "text": f"Compare com as {len(all_people)} foto(s) de referencia e analise."})
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
