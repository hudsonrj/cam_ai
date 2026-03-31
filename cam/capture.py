# cam/capture.py
import cv2


def build_rtsp_url(host: str, user: str, password: str, rtsp_path: str) -> str:
    return f"rtsp://{user}:{password}@{host}:554{rtsp_path}"


def capture_frame(rtsp_url: str) -> bytes | None:
    cap = cv2.VideoCapture(rtsp_url)
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        return None

    _, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes()


class CameraCapture:
    """Mantem conexao RTSP persistente para evitar reconexao a cada frame."""

    def __init__(self, rtsp_url: str):
        self._url = rtsp_url
        self._cap: cv2.VideoCapture | None = None

    def _ensure_open(self) -> bool:
        if self._cap is None or not self._cap.isOpened():
            self._cap = cv2.VideoCapture(self._url)
        return self._cap.isOpened()

    def read(self) -> bytes | None:
        if not self._ensure_open():
            return None

        for _ in range(5):
            ret, frame = self._cap.read()
            if ret and frame is not None:
                _, buffer = cv2.imencode(".jpg", frame)
                return buffer.tobytes()

        # Só reconecta após 5 tentativas consecutivas falharem
        self._cap.release()
        self._cap = None
        return None

    def release(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None
