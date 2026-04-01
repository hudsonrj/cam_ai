"""
cam/ambient_store.py - Persistencia e consulta das transcricoes de ambiente.

Usa SQLite com FTS5 para busca por texto e consultas por intervalo de tempo.
O assistente pode consultar o que foi dito em qualquer dia/hora/minuto,
e fazer buscas semanticas passando os resultados como contexto para o Claude.
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Any


# ── Schema ────────────────────────────────────────────────────────────────────

def init_ambient_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ambient_transcripts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_start  TEXT NOT NULL,
            chunk_end    TEXT NOT NULL,
            device_name  TEXT,
            device_index INTEGER,
            text         TEXT NOT NULL,
            duration_s   REAL,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ambient_start
            ON ambient_transcripts (chunk_start);

        CREATE VIRTUAL TABLE IF NOT EXISTS ambient_fts
            USING fts5(text, content=ambient_transcripts, content_rowid=id);

        CREATE TRIGGER IF NOT EXISTS ambient_fts_ins
            AFTER INSERT ON ambient_transcripts BEGIN
                INSERT INTO ambient_fts(rowid, text) VALUES (new.id, new.text);
            END;

        CREATE TRIGGER IF NOT EXISTS ambient_fts_del
            BEFORE DELETE ON ambient_transcripts BEGIN
                DELETE FROM ambient_fts WHERE rowid = old.id;
            END;
    """)
    conn.commit()


# ── Escrita ───────────────────────────────────────────────────────────────────

def insert_transcript(
    conn: sqlite3.Connection,
    chunk_start: str,
    chunk_end: str,
    device_name: str,
    device_index: int,
    text: str,
    duration_s: float,
) -> int:
    cur = conn.execute(
        """INSERT INTO ambient_transcripts
           (chunk_start, chunk_end, device_name, device_index, text, duration_s)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (chunk_start, chunk_end, device_name, device_index, text, duration_s),
    )
    conn.commit()
    return cur.lastrowid


# ── Consulta por tempo ────────────────────────────────────────────────────────

def query_by_timerange(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    device_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    Retorna transcricoes entre start e end (ISO 8601).
    Exemplos:
        start="2024-01-15 14:00:00"  end="2024-01-15 15:00:00"
    """
    if device_name:
        cur = conn.execute(
            """SELECT id, chunk_start, chunk_end, device_name, text, duration_s
               FROM ambient_transcripts
               WHERE chunk_start >= ? AND chunk_start < ? AND device_name = ?
               ORDER BY chunk_start""",
            (start, end, device_name),
        )
    else:
        cur = conn.execute(
            """SELECT id, chunk_start, chunk_end, device_name, text, duration_s
               FROM ambient_transcripts
               WHERE chunk_start >= ? AND chunk_start < ?
               ORDER BY chunk_start""",
            (start, end),
        )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def query_by_day(conn: sqlite3.Connection, date: str) -> list[dict]:
    """date: YYYY-MM-DD"""
    return query_by_timerange(conn, f"{date} 00:00:00", f"{date} 23:59:59")


def query_by_hour(conn: sqlite3.Connection, date: str, hour: int) -> list[dict]:
    """date: YYYY-MM-DD, hour: 0-23"""
    start = f"{date} {hour:02d}:00:00"
    end   = f"{date} {hour:02d}:59:59"
    return query_by_timerange(conn, start, end)


def query_by_minute(conn: sqlite3.Connection,
                    date: str, hour: int, minute: int) -> list[dict]:
    start = f"{date} {hour:02d}:{minute:02d}:00"
    end   = f"{date} {hour:02d}:{minute:02d}:59"
    return query_by_timerange(conn, start, end)


# ── Busca por texto (FTS5) ────────────────────────────────────────────────────

def search_text(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    date: str | None = None,
) -> list[dict[str, Any]]:
    """
    Busca por palavras-chave usando FTS5.
    Retorna trechos relevantes com timestamp.
    """
    try:
        if date:
            cur = conn.execute(
                """SELECT t.id, t.chunk_start, t.chunk_end, t.device_name,
                          t.text, t.duration_s,
                          snippet(ambient_fts, 0, '[', ']', '...', 20) as snippet
                   FROM ambient_fts
                   JOIN ambient_transcripts t ON ambient_fts.rowid = t.id
                   WHERE ambient_fts MATCH ? AND DATE(t.chunk_start) = ?
                   ORDER BY rank LIMIT ?""",
                (query, date, limit),
            )
        else:
            cur = conn.execute(
                """SELECT t.id, t.chunk_start, t.chunk_end, t.device_name,
                          t.text, t.duration_s,
                          snippet(ambient_fts, 0, '[', ']', '...', 20) as snippet
                   FROM ambient_fts
                   JOIN ambient_transcripts t ON ambient_fts.rowid = t.id
                   WHERE ambient_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


# ── Contexto para o assistente ────────────────────────────────────────────────

def get_context_for_time(
    conn: sqlite3.Connection,
    date: str,
    hour: int | None = None,
    minute: int | None = None,
    max_chars: int = 4000,
) -> str:
    """
    Retorna transcricoes formatadas como contexto para o assistente.
    Quanto mais especifico o tempo, menos registros retorna.
    """
    if minute is not None and hour is not None:
        rows = query_by_minute(conn, date, hour, minute)
    elif hour is not None:
        rows = query_by_hour(conn, date, hour)
    else:
        rows = query_by_day(conn, date)

    if not rows:
        return ""

    lines = []
    total = 0
    for r in rows:
        ts = r["chunk_start"][11:16]    # HH:MM
        dev = r["device_name"] or "mic"
        line = f"[{ts}] ({dev}) {r['text']}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)

    return "\n".join(lines)


def get_recent_ambient(
    conn: sqlite3.Connection,
    minutes: int = 30,
    max_chars: int = 2000,
) -> str:
    """Retorna transcricoes dos ultimos N minutos (para contexto em tempo real)."""
    now = datetime.now()
    since = (now - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    end   = now.isoformat(timespec="seconds")
    rows = query_by_timerange(conn, since, end)
    if not rows:
        return ""

    lines = []
    total = 0
    for r in rows:
        ts = r["chunk_start"][11:16]
        line = f"[{ts}] {r['text']}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)

    return "\n".join(lines)


def search_context(
    conn: sqlite3.Connection,
    query: str,
    date: str | None = None,
    max_chars: int = 3000,
) -> str:
    """
    Busca por texto e formata como contexto para o assistente.
    Usado quando o usuario pergunta sobre algo especifico que foi dito.
    """
    rows = search_text(conn, query, limit=15, date=date)
    if not rows:
        return ""

    lines = []
    total = 0
    for r in rows:
        ts = r["chunk_start"][:16]
        line = f"[{ts}] {r['text']}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)

    return "\n".join(lines)


def get_timeline(
    conn: sqlite3.Connection,
    date: str,
    hour: int | None = None,
    minute: int | None = None,
) -> list[dict]:
    """
    Retorna um panorama combinado de camera (frames/eventos) e audio (transcricoes),
    ordenado por timestamp. Cada entrada tem origem 'camera' ou 'audio'.
    """
    # Monta filtro de tempo
    if minute is not None and hour is not None:
        time_start = f"{date} {hour:02d}:{minute:02d}:00"
        time_end   = f"{date} {hour:02d}:{minute:02d}:59"
    elif hour is not None:
        time_start = f"{date} {hour:02d}:00:00"
        time_end   = f"{date} {hour:02d}:59:59"
    else:
        time_start = f"{date} 00:00:00"
        time_end   = f"{date} 23:59:59"

    # Entradas de camera (frames + eventos agregados)
    cur = conn.execute(
        """SELECT f.timestamp as ts, 'camera' as origin,
                  f.camera_id as source,
                  f.description as text,
                  GROUP_CONCAT(e.event_type) as events,
                  f.id as ref_id
           FROM frames f
           LEFT JOIN events e ON e.frame_id = f.id
           WHERE f.timestamp >= ? AND f.timestamp <= ?
           GROUP BY f.id
           ORDER BY f.timestamp""",
        (time_start, time_end),
    )
    camera_rows = []
    for row in cur.fetchall():
        camera_rows.append({
            "ts": row[0],
            "origin": "camera",
            "source": row[2] or "main",
            "text": row[3] or "",
            "events": row[4].split(",") if row[4] else [],
            "ref_id": row[5],
        })

    # Entradas de audio ambiente
    cur = conn.execute(
        """SELECT chunk_start as ts, 'audio' as origin,
                  device_name as source, text, NULL as events, id as ref_id
           FROM ambient_transcripts
           WHERE chunk_start >= ? AND chunk_start <= ?
           ORDER BY chunk_start""",
        (time_start, time_end),
    )
    audio_rows = []
    for row in cur.fetchall():
        audio_rows.append({
            "ts": row[0],
            "origin": "audio",
            "source": row[2] or "mic",
            "text": row[3] or "",
            "events": [],
            "ref_id": row[5],
        })

    # Merge ordenado por timestamp
    combined = sorted(camera_rows + audio_rows, key=lambda r: r["ts"])
    return combined


def get_ambient_stats(conn: sqlite3.Connection, date: str) -> dict:
    """Estatisticas do dia: total de transcricoes, duracao, dispositivos."""
    cur = conn.execute(
        """SELECT COUNT(*) as total,
                  COALESCE(SUM(duration_s), 0) as total_seconds,
                  COUNT(DISTINCT device_name) as devices
           FROM ambient_transcripts
           WHERE DATE(chunk_start) = ?""",
        (date,),
    )
    row = cur.fetchone()
    return {
        "date": date,
        "total_chunks": row[0],
        "total_minutes": round(row[1] / 60, 1) if row[1] else 0,
        "devices": row[2],
    }
