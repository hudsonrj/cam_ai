# tests/test_capture.py
from unittest.mock import MagicMock, patch
import numpy as np
import pytest
from cam.capture import build_rtsp_url, capture_frame

def test_build_rtsp_url():
    url = build_rtsp_url("192.168.15.13", "user", "pass", "/stream1")
    assert url == "rtsp://user:pass@192.168.15.13:554/stream1"

def test_capture_frame_returns_jpeg_bytes():
    mock_cap = MagicMock()
    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mock_cap.read.return_value = (True, fake_frame)

    with patch("cam.capture.cv2.VideoCapture", return_value=mock_cap):
        result = capture_frame("rtsp://fake")

    assert result is not None
    assert isinstance(result, bytes)
    assert len(result) > 0

def test_capture_frame_returns_none_on_failure():
    mock_cap = MagicMock()
    mock_cap.read.return_value = (False, None)

    with patch("cam.capture.cv2.VideoCapture", return_value=mock_cap):
        result = capture_frame("rtsp://fake")

    assert result is None
