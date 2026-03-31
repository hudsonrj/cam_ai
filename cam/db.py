# cam/db.py
import sqlite3
from typing import Any


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            frame_path TEXT,
            description TEXT,
            raw_response TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            frame_id INTEGER REFERENCES frames(id),
            event_type TEXT,
            confidence REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS actions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER REFERENCES events(id),
            action_type TEXT,
            payload TEXT,
            status TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def insert_frame(conn: sqlite3.Connection, frame_path: str, description: str, raw_response: str) -> int:
    cursor = conn.execute(
        "INSERT INTO frames (frame_path, description, raw_response) VALUES (?, ?, ?)",
        (frame_path, description, raw_response)
    )
    conn.commit()
    return cursor.lastrowid


def insert_event(conn: sqlite3.Connection, frame_id: int, event_type: str, confidence: float) -> int:
    cursor = conn.execute(
        "INSERT INTO events (frame_id, event_type, confidence) VALUES (?, ?, ?)",
        (frame_id, event_type, confidence)
    )
    conn.commit()
    return cursor.lastrowid


def insert_action_log(conn: sqlite3.Connection, event_id: int, action_type: str, payload: str, status: str) -> int:
    cursor = conn.execute(
        "INSERT INTO actions_log (event_id, action_type, payload, status) VALUES (?, ?, ?, ?)",
        (event_id, action_type, payload, status)
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_events(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    cursor = conn.execute(
        """SELECT e.id, e.event_type, e.confidence, e.timestamp, f.frame_path, f.description
           FROM events e JOIN frames f ON e.frame_id = f.id
           ORDER BY e.timestamp DESC LIMIT ?""",
        (limit,)
    )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    init_db(conn)
    return conn
