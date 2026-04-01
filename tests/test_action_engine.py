# tests/test_action_engine.py
import os
from unittest.mock import patch, MagicMock, call
import pytest
from cam.action_engine import execute_actions, save_frame_to_disk

FAKE_JPEG = b"\xff\xd8\xff" + b"\x00" * 50

def test_save_frame_to_disk(tmp_path):
    path = save_frame_to_disk(FAKE_JPEG, frames_dir=str(tmp_path))
    assert os.path.exists(path)
    assert path.endswith(".jpg")

def test_execute_save_frame_action(tmp_path):
    triggered = [{"event_type": "door_open", "confidence": 0.9, "actions": [{"type": "save_frame"}]}]
    results = execute_actions(triggered, FAKE_JPEG, frames_dir=str(tmp_path), telegram_cfg=None, tts_engine=None)
    assert results[0]["status"] == "success"
    assert results[0]["action_type"] == "save_frame"

def test_execute_tts_action():
    mock_engine = MagicMock()
    triggered = [{"event_type": "person_approaching", "confidence": 0.9,
                  "actions": [{"type": "tts", "message": "Alerta"}]}]
    results = execute_actions(triggered, FAKE_JPEG, frames_dir="/tmp", telegram_cfg=None, tts_engine=mock_engine)
    mock_engine.say.assert_called_once_with("Alerta")
    mock_engine.runAndWait.assert_called_once()
    assert results[0]["status"] == "success"

def test_execute_exec_action():
    triggered = [{"event_type": "door_open", "confidence": 0.9,
                  "actions": [{"type": "exec", "command": "echo test"}]}]
    with patch("cam.action_engine.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        results = execute_actions(triggered, FAKE_JPEG, frames_dir="/tmp", telegram_cfg=None, tts_engine=None)
    assert results[0]["status"] == "success"

def test_execute_telegram_action(tmp_path):
    telegram_cfg = {"bot_token": "fake-token", "chat_id": "123"}
    triggered = [{"event_type": "person_approaching", "confidence": 0.9,
                  "actions": [{"type": "telegram", "message": "Alerta teste"}]}]

    with patch("cam.action_engine._run_telegram", return_value={"action_type": "telegram", "payload": "Alerta teste", "status": "success"}) as mock_tg:
        results = execute_actions(triggered, FAKE_JPEG, frames_dir=str(tmp_path), telegram_cfg=telegram_cfg, tts_engine=None)

    mock_tg.assert_called_once()
    assert results[0]["status"] == "success"
    assert results[0]["action_type"] == "telegram"
