"""
cam/web_server.py — Servidor web FastAPI para o dashboard CAM AI.

Serve:
- GET  /             → index.html (dashboard)
- GET  /stream/{id}  → MJPEG live stream
- WS   /ws           → eventos em tempo real + chat do assistente
- GET  /api/history  → eventos recentes (JSON)
- GET  /api/snapshot/{frame_id} → JPEG salvo
- GET  /api/cameras  → lista de câmeras
- GET  /api/summary  → resumos diários (memória persistente)
- POST /api/summary/generate → gera resumo de ontem
"""
import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from cam.db import get_recent_frames, get_recent_summaries, open_db

_multi_service = None   # MultiCameraService
_db_path = "data/cam.db"
_ws_clients: set[WebSocket] = set()
_web_queue: asyncio.Queue | None = None  # set on startup

app = FastAPI(title="CAM AI", docs_url=None, redoc_url=None)

_STATIC = Path(__file__).parent / "web"


def set_service(service) -> None:
    global _multi_service
    _multi_service = service


# ── Startup: bridge threading service → async queue ──────────────────────────

@app.on_event("startup")
async def _startup() -> None:
    global _web_queue
    _web_queue = asyncio.Queue(maxsize=200)
    asyncio.create_task(_event_dispatcher())

    if _multi_service:
        # Registra queue no event bus — alimentada pela thread de captura
        sync_q = _multi_service.event_bus.subscribe(maxsize=200)
        asyncio.create_task(_bridge_sync_queue(sync_q))


async def _bridge_sync_queue(sync_q) -> None:
    """Transfere eventos da queue de threads para a asyncio queue."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            item = await loop.run_in_executor(None, _blocking_get, sync_q)
            if item and _web_queue:
                await _web_queue.put(item)
        except Exception:
            await asyncio.sleep(0.1)


def _blocking_get(q, timeout: float = 1.0):
    import queue as _q
    try:
        return q.get(timeout=timeout)
    except _q.Empty:
        return None


async def _event_dispatcher() -> None:
    """Consome web_queue e envia para todos os WebSocket clients."""
    while True:
        try:
            payload = await asyncio.wait_for(_web_queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            continue
        except Exception:
            await asyncio.sleep(0.1)
            continue

        # Serializa: remove jpeg_bytes (binário) antes de enviar como JSON
        msg = {k: v for k, v in payload.items() if k != "jpeg_bytes"}
        data = json.dumps(msg)

        dead = set()
        for ws in list(_ws_clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        _ws_clients.difference_update(dead)


# ── Static + Dashboard ────────────────────────────────────────────────────────

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


# ── MJPEG Stream ──────────────────────────────────────────────────────────────

@app.get("/stream/{camera_id}")
async def stream(camera_id: str = "main") -> StreamingResponse:
    async def generate() -> AsyncGenerator[bytes, None]:
        while True:
            jpeg = None
            if _multi_service:
                jpeg = _multi_service.get_last_jpeg(camera_id)
            if jpeg:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + jpeg + b"\r\n"
                )
            await asyncio.sleep(0.08)  # ~12fps

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/stream")
async def stream_default() -> StreamingResponse:
    return await stream("main")


# ── REST API ──────────────────────────────────────────────────────────────────

@app.get("/api/cameras")
async def get_cameras():
    if _multi_service:
        return _multi_service.get_cameras()
    return [{"id": "main", "name": "Principal"}]


@app.get("/api/history")
async def get_history(limit: int = 60, camera_id: str | None = None):
    def _query():
        conn = sqlite3.connect(_db_path, check_same_thread=False)
        rows = get_recent_frames(conn, limit=limit, camera_id=camera_id)
        conn.close()
        return rows
    return await run_in_threadpool(_query)


@app.get("/api/snapshot/{frame_id}")
async def get_snapshot(frame_id: int):
    path = f"registros/{frame_id}.jpg"
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), media_type="image/jpeg")
    return Response(status_code=404)


@app.get("/api/summary")
async def get_summary(days: int = 7):
    def _query():
        conn = sqlite3.connect(_db_path, check_same_thread=False)
        rows = get_recent_summaries(conn, days=days)
        conn.close()
        return rows
    return await run_in_threadpool(_query)


@app.post("/api/summary/generate")
async def generate_summary():
    async def _run():
        from datetime import date, timedelta
        import os
        from cam.daily_summary import generate_daily_summary
        target = (date.today() - timedelta(days=1)).isoformat()
        cfg = _multi_service.config if _multi_service else {}
        token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
        conn = sqlite3.connect(_db_path, check_same_thread=False)
        summary = await run_in_threadpool(
            generate_daily_summary, conn, target,
            cfg.get("bedrock", {}).get("region", "us-east-1"),
            cfg.get("bedrock", {}).get("model_id", ""),
            token,
        )
        conn.close()
        return {"date": target, "summary": summary}
    return await _run()


# ── WebSocket ─────────────────────────────────────────────────────────────────

_assistant_instances: dict[str, object] = {}  # ws_id → ConversationAssistant


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.add(ws)
    ws_id = str(id(ws))

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "chat":
                await _handle_chat(ws, ws_id, msg)

            elif msg_type == "get_patterns":
                await _handle_patterns(ws)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(ws)
        _assistant_instances.pop(ws_id, None)


async def _handle_chat(ws: WebSocket, ws_id: str, msg: dict) -> None:
    text = msg.get("text", "").strip()
    camera_id = msg.get("camera_id", "main")
    if not text:
        return

    await ws.send_text(json.dumps({"type": "chat_reply", "text": "", "thinking": True}))

    async def _process():
        try:
            if ws_id not in _assistant_instances:
                from cam.assistant import ConversationAssistant
                from cam.daily_summary import get_context_for_assistant
                cfg = _multi_service.config if _multi_service else {}
                token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
                asst = ConversationAssistant(
                    region=cfg.get("bedrock", {}).get("region", "us-east-1"),
                    model_id=cfg.get("bedrock", {}).get("model_id", ""),
                    bearer_token=token,
                )
                _assistant_instances[ws_id] = asst
            else:
                asst = _assistant_instances[ws_id]

            jpeg = _multi_service.get_last_jpeg(camera_id) if _multi_service else None

            # Contexto histórico do dia + memória persistente
            def _ctx():
                conn = sqlite3.connect(_db_path, check_same_thread=False)
                from cam.daily_summary import get_context_for_assistant
                ctx = get_context_for_assistant(conn, days=7)
                conn.close()
                return ctx

            context = await run_in_threadpool(_ctx)

            reply = await run_in_threadpool(asst.chat, text, jpeg, context)
            await ws.send_text(json.dumps(
                {"type": "chat_reply", "text": reply, "thinking": False}
            ))
        except Exception as e:
            await ws.send_text(json.dumps(
                {"type": "chat_reply", "text": f"Erro: {e}", "thinking": False}
            ))

    asyncio.create_task(_process())


async def _handle_patterns(ws: WebSocket) -> None:
    async def _process():
        try:
            def _analyze():
                from cam.db import get_frames_for_date
                from datetime import date, timedelta
                import os
                import httpx

                conn = sqlite3.connect(_db_path, check_same_thread=False)
                lines = []
                for i in range(7):
                    d = (date.today() - timedelta(days=i)).isoformat()
                    frames = get_frames_for_date(conn, d)
                    for f in frames[:20]:
                        ts = f["timestamp"][11:16] if f["timestamp"] else ""
                        ev = ",".join(f["events"] or [])
                        lines.append(f"{d} {ts} [{ev}] {(f['description'] or '')[:80]}")
                conn.close()

                if not lines:
                    return "Dados insuficientes para análise de padrões."

                observations = "\n".join(lines[:120])
                cfg = _multi_service.config if _multi_service else {}
                token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
                payload = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 512,
                    "system": (
                        "Você é um analista de rotina residencial. "
                        "Com base nas observações dos últimos 7 dias, identifique: "
                        "1) Padrões de rotina (horários, hábitos). "
                        "2) Anomalias ou desvios do padrão usual. "
                        "3) Insights úteis para o morador. "
                        "Responda em português, de forma clara e concisa."
                    ),
                    "messages": [{"role": "user", "content": f"Observações:\n{observations}"}],
                }
                url = (f"https://bedrock-runtime."
                       f"{cfg.get('bedrock', {}).get('region', 'us-east-1')}.amazonaws.com"
                       f"/model/{cfg.get('bedrock', {}).get('model_id', '')}/invoke")
                headers = {"Authorization": f"Bearer {token}",
                           "Content-Type": "application/json"}
                resp = httpx.post(url, json=payload, headers=headers,
                                  timeout=httpx.Timeout(connect=10, read=90, write=90, pool=10))
                resp.raise_for_status()
                return resp.json()["content"][0]["text"].strip()

            result = await run_in_threadpool(_analyze)
            await ws.send_text(json.dumps({"type": "patterns", "text": result}))
        except Exception as e:
            await ws.send_text(json.dumps({"type": "patterns", "text": f"Erro: {e}"}))

    asyncio.create_task(_process())
