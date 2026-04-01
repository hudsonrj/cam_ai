# tests/test_analyzer.py
import base64
import json
import pytest
import httpx
from unittest.mock import patch, MagicMock
from cam.analyzer import analyze_frame, SYSTEM_PROMPT

FAKE_JPEG = b"\xff\xd8\xff" + b"\x00" * 100

FAKE_BEDROCK_RESPONSE = {
    "content": [
        {
            "type": "text",
            "text": json.dumps({
                "description": "Uma pessoa se aproximando de um computador",
                "events": [
                    {"event_type": "person_approaching", "confidence": 0.95}
                ]
            })
        }
    ]
}

def test_analyze_frame_returns_description_and_events():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = FAKE_BEDROCK_RESPONSE

    with patch("cam.analyzer.httpx.post", return_value=mock_response):
        result = analyze_frame(
            jpeg_bytes=FAKE_JPEG,
            region="us-east-1",
            model_id="anthropic.claude-3-5-sonnet",
            bearer_token="fake-token"
        )

    assert result["description"] == "Uma pessoa se aproximando de um computador"
    assert len(result["events"]) == 1
    assert result["events"][0]["event_type"] == "person_approaching"

def test_analyze_frame_returns_empty_events_on_bad_json():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": "resposta invalida"}]
    }

    with patch("cam.analyzer.httpx.post", return_value=mock_response):
        result = analyze_frame(FAKE_JPEG, "us-east-1", "model", "token")

    assert result["description"] == "resposta invalida"
    assert result["events"] == []
