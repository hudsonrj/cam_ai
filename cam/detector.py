# cam/detector.py

def detect_events(
    analysis: dict,
    rules: list[dict],
    min_confidence: float = 0.5,
) -> list[dict]:
    rule_map = {r["event"]: r["actions"] for r in rules}
    triggered = []

    for event in analysis.get("events", []):
        event_type = event.get("event_type")
        confidence = event.get("confidence", 0.0)

        if confidence < min_confidence:
            continue

        if event_type in rule_map:
            triggered.append({
                "event_type": event_type,
                "confidence": confidence,
                "actions": rule_map[event_type],
            })

    return triggered
