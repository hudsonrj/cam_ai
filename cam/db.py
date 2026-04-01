# cam/db.py
import sqlite3
from typing import Any


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS frames (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
            frame_path  TEXT,
            description TEXT,
            raw_response TEXT,
            camera_id   TEXT DEFAULT 'main'
        );
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            frame_id    INTEGER REFERENCES frames(id),
            event_type  TEXT,
            confidence  REAL,
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS actions_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER REFERENCES events(id),
            action_type TEXT,
            payload     TEXT,
            status      TEXT,
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS daily_summaries (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT UNIQUE,
            summary   TEXT,
            generated DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS known_visitors (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT UNIQUE,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen  DATETIME DEFAULT CURRENT_TIMESTAMP,
            seen_count INTEGER DEFAULT 1
        );
    """)
    # Migrate: add camera_id column if missing (existing DBs)
    try:
        conn.execute("ALTER TABLE frames ADD COLUMN camera_id TEXT DEFAULT 'main'")
        conn.commit()
    except Exception:
        pass

    # Inicializa tabelas de audio ambiente
    from cam.ambient_store import init_ambient_tables
    init_ambient_tables(conn)


def insert_frame(conn: sqlite3.Connection, frame_path: str, description: str,
                 raw_response: str, camera_id: str = "main") -> int:
    cursor = conn.execute(
        "INSERT INTO frames (frame_path, description, raw_response, camera_id)"
        " VALUES (?, ?, ?, ?)",
        (frame_path, description, raw_response, camera_id),
    )
    conn.commit()
    return cursor.lastrowid


def insert_event(conn: sqlite3.Connection, frame_id: int, event_type: str,
                 confidence: float) -> int:
    cursor = conn.execute(
        "INSERT INTO events (frame_id, event_type, confidence) VALUES (?, ?, ?)",
        (frame_id, event_type, confidence),
    )
    conn.commit()
    return cursor.lastrowid


def insert_action_log(conn: sqlite3.Connection, event_id: int, action_type: str,
                      payload: str, status: str) -> int:
    cursor = conn.execute(
        "INSERT INTO actions_log (event_id, action_type, payload, status)"
        " VALUES (?, ?, ?, ?)",
        (event_id, action_type, payload, status),
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_events(conn: sqlite3.Connection, limit: int = 20,
                      camera_id: str | None = None) -> list[dict[str, Any]]:
    if camera_id:
        cursor = conn.execute(
            """SELECT e.id, e.event_type, e.confidence, e.timestamp,
                      f.frame_path, f.description, f.id as frame_id, f.camera_id
               FROM events e JOIN frames f ON e.frame_id = f.id
               WHERE f.camera_id = ?
               ORDER BY e.timestamp DESC LIMIT ?""",
            (camera_id, limit),
        )
    else:
        cursor = conn.execute(
            """SELECT e.id, e.event_type, e.confidence, e.timestamp,
                      f.frame_path, f.description, f.id as frame_id, f.camera_id
               FROM events e JOIN frames f ON e.frame_id = f.id
               ORDER BY e.timestamp DESC LIMIT ?""",
            (limit,),
        )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def get_recent_frames(conn: sqlite3.Connection, limit: int = 50,
                      camera_id: str | None = None) -> list[dict[str, Any]]:
    if camera_id:
        cursor = conn.execute(
            """SELECT f.id, f.timestamp, f.description, f.camera_id,
                      GROUP_CONCAT(e.event_type) as events
               FROM frames f LEFT JOIN events e ON e.frame_id = f.id
               WHERE f.camera_id = ?
               GROUP BY f.id ORDER BY f.id DESC LIMIT ?""",
            (camera_id, limit),
        )
    else:
        cursor = conn.execute(
            """SELECT f.id, f.timestamp, f.description, f.camera_id,
                      GROUP_CONCAT(e.event_type) as events
               FROM frames f LEFT JOIN events e ON e.frame_id = f.id
               GROUP BY f.id ORDER BY f.id DESC LIMIT ?""",
            (limit,),
        )
    cols = [d[0] for d in cursor.description]
    rows = []
    for row in cursor.fetchall():
        d = dict(zip(cols, row))
        d["events"] = d["events"].split(",") if d["events"] else []
        rows.append(d)
    return rows


def get_frames_for_date(conn: sqlite3.Connection, date: str,
                        camera_id: str | None = None) -> list[dict[str, Any]]:
    """date: YYYY-MM-DD"""
    if camera_id:
        cursor = conn.execute(
            """SELECT f.id, f.timestamp, f.description,
                      GROUP_CONCAT(e.event_type) as events
               FROM frames f LEFT JOIN events e ON e.frame_id = f.id
               WHERE DATE(f.timestamp) = ? AND f.camera_id = ?
               GROUP BY f.id ORDER BY f.id""",
            (date, camera_id),
        )
    else:
        cursor = conn.execute(
            """SELECT f.id, f.timestamp, f.description,
                      GROUP_CONCAT(e.event_type) as events
               FROM frames f LEFT JOIN events e ON e.frame_id = f.id
               WHERE DATE(f.timestamp) = ?
               GROUP BY f.id ORDER BY f.id""",
            (date,),
        )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def insert_daily_summary(conn: sqlite3.Connection, date: str, summary: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO daily_summaries (date, summary) VALUES (?, ?)",
        (date, summary),
    )
    conn.commit()


def get_recent_summaries(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    cursor = conn.execute(
        "SELECT date, summary FROM daily_summaries ORDER BY date DESC LIMIT ?",
        (days,),
    )
    return [{"date": r[0], "summary": r[1]} for r in cursor.fetchall()]


def upsert_visitor(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(
        """INSERT INTO known_visitors (name) VALUES (?)
           ON CONFLICT(name) DO UPDATE SET
             last_seen = CURRENT_TIMESTAMP,
             seen_count = seen_count + 1""",
        (name,),
    )
    conn.commit()


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    init_db(conn)
    return conn
