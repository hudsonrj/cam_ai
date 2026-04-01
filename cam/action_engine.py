# cam/action_engine.py
import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone

# Estado de controle da agenda
_last_agenda_announced: datetime | None = None
_owner_away_since: datetime | None = None
AWAY_THRESHOLD_MIN = 30

# Cooldown por tipo de evento para alertas Telegram
_telegram_last_sent: dict[str, datetime] = {}


def _telegram_cooldown_ok(event_type: str, cooldown_min: float) -> bool:
    """Retorna True se o cooldown para este tipo de evento já passou."""
    last = _telegram_last_sent.get(event_type)
    if last is None:
        return True
    return (datetime.now() - last).total_seconds() / 60 >= cooldown_min


def record_owner_away() -> None:
    """Chame quando owner sair do local."""
    global _owner_away_since
    if _owner_away_since is None:
        _owner_away_since = datetime.now()


def _should_announce_agenda() -> bool:
    global _last_agenda_announced, _owner_away_since
    if _last_agenda_announced is None:
        return True  # primeira vez
    if _owner_away_since is None:
        return False  # nunca foi embora desde o último anúncio
    away_minutes = (datetime.now() - _owner_away_since).total_seconds() / 60
    return away_minutes >= AWAY_THRESHOLD_MIN


def save_frame_to_disk(jpeg_bytes: bytes, frames_dir: str = "frames") -> str:
    os.makedirs(frames_dir, exist_ok=True)
    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".jpg"
    path = os.path.join(frames_dir, filename)
    with open(path, "wb") as f:
        f.write(jpeg_bytes)
    return path


def _build_agenda_speech(dashboard_url: str = "http://127.0.0.1:8888") -> str:
    """Busca compromissos via API e monta texto para TTS."""
    try:
        req = urllib.request.urlopen(f"{dashboard_url}/api/calendar", timeout=8)
        data = json.loads(req.read())
    except Exception as e:
        return f"Não foi possível acessar a agenda. {e}"

    sao_paulo = timezone(timedelta(hours=-3))
    now = datetime.now(tz=sao_paulo)
    events = data.get("events", [])

    upcoming = []
    for ev in events:
        start = ev.get("start", "")
        if not start or "T" not in start:
            continue
        try:
            s = re.sub(r'\.\d+', '', start)
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            dt_start = datetime.fromisoformat(s).astimezone(sao_paulo)
        except Exception:
            continue
        if dt_start.date() != now.date():
            continue
        end = ev.get("end", "")
        dt_end = dt_start
        if end and "T" in end:
            try:
                e2 = re.sub(r'\.\d+', '', end)
                if e2.endswith('Z'):
                    e2 = e2[:-1] + '+00:00'
                dt_end = datetime.fromisoformat(e2).astimezone(sao_paulo)
            except Exception:
                pass
        if dt_end >= now:
            ev["_dt_start"] = dt_start
            ev["_dt_end"] = dt_end
            upcoming.append(ev)

    upcoming.sort(key=lambda e: e["_dt_start"])

    if not upcoming:
        return "Olá Hudson, bem vindo! Você não tem mais compromissos hoje."

    lines = [f"Olá Hudson, bem vindo! Você tem {len(upcoming)} compromisso{'s' if len(upcoming) > 1 else ''} hoje."]
    for ev in upcoming:
        dt_s: datetime = ev["_dt_start"]
        title = ev.get("title", "sem título")
        location = ev.get("location", "")
        diff = int((dt_s - now).total_seconds() / 60)

        hora = dt_s.strftime("%H").lstrip("0") or "0"
        minuto = dt_s.strftime("%M")
        horario = f"às {hora} e {minuto}" if minuto != "00" else f"às {hora} horas"

        if diff <= 0:
            quando = "em andamento agora"
        elif diff < 60:
            quando = f"em {diff} minutos"
        else:
            h, m = divmod(diff, 60)
            quando = f"em {h} hora{'s' if h > 1 else ''}" + (f" e {m} minutos" if m else "")

        partes = [f"{horario}, {quando}: {title}"]
        if location:
            partes.append(f"Local: {location}")
        lines.append(". ".join(partes))

    return " ".join(lines)


def _tts_speak(message: str) -> None:
    """Fala via PowerShell (nativo Windows, funciona em qualquer thread)."""
    safe = message.replace('"', "'").replace('\n', ' ')
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-Command",
         f'Add-Type -AssemblyName System.Speech; '
         f'$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
         f'$s.Rate = 0; $s.Speak("{safe}")'],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _run_telegram(action: dict, description: str, frame_path: str | None, telegram_cfg: dict) -> dict:
    try:
        import telegram
        import asyncio

        bot = telegram.Bot(token=telegram_cfg["bot_token"])
        chat_id = telegram_cfg["chat_id"]
        msg = action.get("message", description)

        async def send():
            async with bot:
                await bot.send_message(chat_id=chat_id, text=msg)
                if frame_path and os.path.exists(frame_path):
                    with open(frame_path, "rb") as img:
                        await bot.send_photo(chat_id=chat_id, photo=img)

        asyncio.run(send())
        return {"action_type": "telegram", "payload": msg, "status": "success"}
    except Exception as e:
        return {"action_type": "telegram", "payload": str(e), "status": "error"}


def execute_actions(
    triggered_events: list[dict],
    jpeg_bytes: bytes,
    frames_dir: str,
    telegram_cfg: dict | None,
    tts_engine,
    ha_cfg: dict | None = None,
) -> list[dict]:
    results = []
    saved_frame_path = None

    for event in triggered_events:
        for action in event.get("actions", []):
            action_type = action.get("type")
            result = {"action_type": action_type, "event_type": event["event_type"]}

            try:
                if action_type == "save_frame":
                    path = save_frame_to_disk(jpeg_bytes, frames_dir)
                    saved_frame_path = path
                    result.update({"payload": path, "status": "success"})

                elif action_type == "tts":
                    msg = action.get("message", "Alerta")
                    _tts_speak(msg)
                    result.update({"payload": msg, "status": "success"})

                elif action_type == "announce_agenda":
                    if _should_announce_agenda():
                        global _last_agenda_announced, _owner_away_since
                        url = action.get("dashboard_url", "http://127.0.0.1:8888")
                        speech = _build_agenda_speech(url)
                        _tts_speak(speech)
                        _last_agenda_announced = datetime.now()
                        _owner_away_since = None  # reseta — precisa sair de novo
                        result.update({"payload": speech[:120], "status": "success"})
                    else:
                        result.update({"payload": "agenda ja anunciada recentemente", "status": "skipped"})

                elif action_type == "exec":
                    cmd = action.get("command", "")
                    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                    status = "success" if proc.returncode == 0 else "error"
                    result.update({"payload": cmd, "status": status})

                elif action_type == "telegram" and telegram_cfg:
                    cooldown = telegram_cfg.get("cooldown_minutes", 10)
                    ev_type = event.get("event_type", "")
                    if _telegram_cooldown_ok(ev_type, cooldown):
                        res = _run_telegram(action, ev_type, saved_frame_path, telegram_cfg)
                        if res["status"] == "success":
                            _telegram_last_sent[ev_type] = datetime.now()
                        result.update(res)
                    else:
                        result.update({"payload": "cooldown ativo", "status": "skipped"})

                elif action_type == "home_assistant" and ha_cfg:
                    from cam.home_assistant import push_event, push_state
                    ev_type = event.get("event_type", "")
                    entity_id = action.get("entity_id", "")
                    if entity_id:
                        # Atualiza sensor via REST API
                        res = push_state(
                            entity_id=entity_id,
                            state=ev_type,
                            attributes={
                                "event_type": ev_type,
                                "confidence": event.get("confidence", 1.0),
                                "frame_path": saved_frame_path or "",
                            },
                            ha_cfg=ha_cfg,
                        )
                    else:
                        # Push via webhook
                        res = push_event(
                            event_type=ev_type,
                            confidence=event.get("confidence", 1.0),
                            description=ev_type,
                            camera_id=event.get("camera_id", "main"),
                            frame_id=None,
                            ha_cfg=ha_cfg,
                        )
                    result.update({"payload": str(res), "status": res.get("status", "error")})

                else:
                    result.update({"payload": None, "status": "skipped"})

            except Exception as e:
                result.update({"payload": str(e), "status": "error"})

            results.append(result)

    return results
