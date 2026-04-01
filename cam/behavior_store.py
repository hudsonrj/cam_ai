"""
cam/behavior_store.py - Persistencia de eventos comportamentais e perfis de pessoas.

Armazena classificacoes extraidas tanto da camera quanto do audio:
- Agua, refeicoes, tempo no PC, reunioes, tom de voz, topicos, empresas
- Perfis de pessoas: quem foi visto, quando, com que frequencia, o que fez
"""
import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any


# ── Schema ────────────────────────────────────────────────────────────────────

def init_behavior_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS behavior_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            source      TEXT NOT NULL,   -- 'camera' | 'audio'
            event_type  TEXT NOT NULL,   -- ver tipos abaixo
            person_name TEXT,            -- se aplicavel
            metadata    TEXT,            -- JSON com detalhes
            confidence  REAL DEFAULT 1.0,
            camera_id   TEXT DEFAULT 'main'
        );

        CREATE INDEX IF NOT EXISTS idx_bev_timestamp
            ON behavior_events (timestamp);
        CREATE INDEX IF NOT EXISTS idx_bev_type
            ON behavior_events (event_type);

        CREATE TABLE IF NOT EXISTS person_profiles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            label        TEXT UNIQUE NOT NULL,  -- nome ou 'desconhecido_N'
            display_name TEXT,
            first_seen   TEXT DEFAULT (datetime('now')),
            last_seen    TEXT DEFAULT (datetime('now')),
            seen_count   INTEGER DEFAULT 1,
            total_time_min REAL DEFAULT 0,
            notes        TEXT,
            photo_path   TEXT
        );
    """)
    conn.commit()


# ── Escrita de eventos comportamentais ────────────────────────────────────────

def insert_behavior_event(
    conn: sqlite3.Connection,
    timestamp: str,
    source: str,
    event_type: str,
    person_name: str | None = None,
    metadata: dict | None = None,
    confidence: float = 1.0,
    camera_id: str = "main",
) -> int:
    cur = conn.execute(
        """INSERT INTO behavior_events
           (timestamp, source, event_type, person_name, metadata, confidence, camera_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            timestamp, source, event_type, person_name,
            json.dumps(metadata or {}), confidence, camera_id,
        ),
    )
    conn.commit()
    return cur.lastrowid


def upsert_person_profile(
    conn: sqlite3.Connection,
    label: str,
    display_name: str | None = None,
    time_delta_min: float = 0,
    photo_path: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO person_profiles (label, display_name, photo_path, total_time_min)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(label) DO UPDATE SET
             last_seen = datetime('now'),
             seen_count = seen_count + 1,
             total_time_min = total_time_min + excluded.total_time_min,
             display_name = COALESCE(excluded.display_name, display_name),
             photo_path = COALESCE(excluded.photo_path, photo_path)""",
        (label, display_name or label, photo_path, time_delta_min),
    )
    conn.commit()


# ── Consultas ─────────────────────────────────────────────────────────────────

def get_behavior_events(
    conn: sqlite3.Connection,
    date_str: str,
    event_type: str | None = None,
    source: str | None = None,
) -> list[dict]:
    filters = ["DATE(timestamp) = ?"]
    params: list = [date_str]
    if event_type:
        filters.append("event_type = ?")
        params.append(event_type)
    if source:
        filters.append("source = ?")
        params.append(source)

    where = " AND ".join(filters)
    cur = conn.execute(
        f"SELECT * FROM behavior_events WHERE {where} ORDER BY timestamp",
        params,
    )
    cols = [d[0] for d in cur.description]
    rows = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        try:
            d["metadata"] = json.loads(d["metadata"] or "{}")
        except Exception:
            d["metadata"] = {}
        rows.append(d)
    return rows


def get_daily_insights(conn: sqlite3.Connection, date_str: str) -> dict:
    """
    Agrega todos os eventos comportamentais do dia em um dicionario de insights.
    Retorna metricas prontas para exibir no dashboard.
    """
    events = get_behavior_events(conn, date_str)

    # Contadores
    water_count = sum(1 for e in events if e["event_type"] == "drink_water")
    drink_other = [e for e in events if e["event_type"] == "drink_other"]
    meals = [e for e in events if e["event_type"] == "eat"]
    meetings = [e for e in events if e["event_type"] == "meeting"]
    phone_calls = [e for e in events if e["event_type"] == "phone_call"]
    stress_events = [e for e in events if e["event_type"] == "stress_detected"]

    # Tempo no PC (janelas de person_sitting_at_pc)
    pc_events = get_behavior_events(conn, date_str, event_type="time_at_pc")
    total_pc_min = sum(e["metadata"].get("duration_min", 0) for e in pc_events)

    # Topicos
    topic_events = [e for e in events if e["event_type"].startswith("topic_")]
    topics: dict[str, int] = {}
    for e in topic_events:
        t = e["event_type"].replace("topic_", "")
        topics[t] = topics.get(t, 0) + 1

    # Empresas e entidades mencionadas
    entity_events = [e for e in events if e["event_type"] == "entity_mentioned"]
    entities: dict[str, int] = {}
    for e in entity_events:
        name = e["metadata"].get("entity", "")
        if name:
            entities[name] = entities.get(name, 0) + 1

    # Tom de voz / humor dominante
    mood_events = [e for e in events if e["event_type"].startswith("mood_")]
    mood_counts: dict[str, int] = {}
    for e in mood_events:
        m = e["event_type"].replace("mood_", "")
        mood_counts[m] = mood_counts.get(m, 0) + 1
    dominant_mood = max(mood_counts, key=mood_counts.get) if mood_counts else "neutro"

    # Pessoas vistas
    people_events = [e for e in events if e["event_type"] == "person_seen"
                     and e.get("person_name")]
    people_seen: dict[str, int] = {}
    for e in people_events:
        n = e["person_name"]
        people_seen[n] = people_seen.get(n, 0) + 1

    return {
        "date": date_str,
        "water_count": water_count,
        "drinks_other": [e["metadata"].get("what", "") for e in drink_other],
        "meals": [e["metadata"] for e in meals],
        "total_pc_minutes": round(total_pc_min),
        "meetings_count": len(meetings),
        "meetings": [e["metadata"] for e in meetings],
        "phone_calls": len(phone_calls),
        "stress_events": len(stress_events),
        "dominant_mood": dominant_mood,
        "mood_distribution": mood_counts,
        "topics": topics,
        "entities": entities,
        "people_seen": people_seen,
    }


def get_person_profiles(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT * FROM person_profiles ORDER BY seen_count DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_weekly_summary(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    result = []
    today = date.today()
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        result.append(get_daily_insights(conn, d))
    return result
