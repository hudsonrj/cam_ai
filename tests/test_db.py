# tests/test_db.py
import sqlite3
import pytest
from cam.db import init_db, insert_frame, insert_event, insert_action_log, get_recent_events

@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()

def test_init_creates_tables(db_conn):
    cursor = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert {"frames", "events", "actions_log"}.issubset(tables)

def test_insert_frame(db_conn):
    frame_id = insert_frame(db_conn, "frames/test.jpg", "Uma pessoa na sala", '{"raw": true}')
    assert frame_id > 0

def test_insert_event(db_conn):
    frame_id = insert_frame(db_conn, "frames/test.jpg", "desc", "{}")
    event_id = insert_event(db_conn, frame_id, "person_approaching", 0.9)
    assert event_id > 0

def test_insert_action_log(db_conn):
    frame_id = insert_frame(db_conn, "frames/test.jpg", "desc", "{}")
    event_id = insert_event(db_conn, frame_id, "person_approaching", 0.9)
    log_id = insert_action_log(db_conn, event_id, "telegram", '{"msg": "ok"}', "success")
    assert log_id > 0

def test_get_recent_events(db_conn):
    frame_id = insert_frame(db_conn, "frames/test.jpg", "desc", "{}")
    insert_event(db_conn, frame_id, "door_open", 0.85)
    events = get_recent_events(db_conn, limit=10)
    assert len(events) == 1
    assert events[0]["event_type"] == "door_open"
