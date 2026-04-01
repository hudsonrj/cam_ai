# tests/test_detector.py
from cam.detector import detect_events

RULES = [
    {
        "event": "person_approaching",
        "actions": [{"type": "telegram", "message": "Alerta"}]
    },
    {
        "event": "door_open",
        "actions": [{"type": "save_frame"}]
    }
]

def test_detects_matching_event():
    analysis = {
        "description": "Pessoa na sala",
        "events": [{"event_type": "person_approaching", "confidence": 0.9}]
    }
    triggered = detect_events(analysis, RULES)
    assert len(triggered) == 1
    assert triggered[0]["event_type"] == "person_approaching"
    assert triggered[0]["actions"][0]["type"] == "telegram"

def test_no_match_returns_empty():
    analysis = {
        "description": "Sala vazia",
        "events": [{"event_type": "unknown_event", "confidence": 0.9}]
    }
    triggered = detect_events(analysis, RULES)
    assert triggered == []

def test_multiple_events_detected():
    analysis = {
        "description": "Pessoa e porta",
        "events": [
            {"event_type": "person_approaching", "confidence": 0.85},
            {"event_type": "door_open", "confidence": 0.78},
        ]
    }
    triggered = detect_events(analysis, RULES)
    assert len(triggered) == 2

def test_low_confidence_below_threshold_ignored():
    analysis = {
        "description": "Incerto",
        "events": [{"event_type": "person_approaching", "confidence": 0.3}]
    }
    triggered = detect_events(analysis, RULES, min_confidence=0.5)
    assert triggered == []
