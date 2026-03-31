# cam/service.py
import os
import queue
import sqlite3
import subprocess
import threading
from typing import Any

import cv2
import numpy as np
import yaml

from cam.analyzer import analyze_frame
from cam.audio_recorder import AudioRecorder
from cam.transcriber import AudioTranscriber
from cam.capture import build_rtsp_url, CameraCapture
from cam.db import insert_action_log, insert_event, insert_frame, open_db
from cam.detector import detect_events
from cam.action_engine import execute_actions, record_owner_away, save_frame_to_disk


def _save_registro(frame_id: int, jpeg_bytes: bytes, description: str) -> None:
    os.makedirs("registros", exist_ok=True)
    img_path = os.path.join("registros", f"{frame_id}.jpg")
    txt_path = os.path.join("registros", f"{frame_id}.txt")
    with open(img_path, "wb") as f:
        f.write(jpeg_bytes)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"ID: {frame_id}\n{description}\n")


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _frames_are_similar(jpeg_a: bytes, jpeg_b: bytes, threshold: float = 0.92) -> bool:
    arr_a = np.frombuffer(jpeg_a, np.uint8)
    arr_b = np.frombuffer(jpeg_b, np.uint8)
    img_a = cv2.imdecode(arr_a, cv2.IMREAD_COLOR)
    img_b = cv2.imdecode(arr_b, cv2.IMREAD_COLOR)
    if img_a is None or img_b is None:
        return False
    img_a = cv2.resize(img_a, (64, 64))
    img_b = cv2.resize(img_b, (64, 64))
    hist_a = cv2.calcHist([img_a], [0, 1, 2], None, [8, 8, 8], [0,256,0,256,0,256])
    hist_b = cv2.calcHist([img_b], [0, 1, 2], None, [8, 8, 8], [0,256,0,256,0,256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    return cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL) >= threshold


class CameraService:
    def __init__(self, config_path: str = "config.yaml", gui_queue: queue.Queue | None = None):
        self.config = load_config(config_path)
        self.gui_queue = gui_queue
        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._analysis_thread: threading.Thread | None = None
        self._analysis_queue: queue.Queue = queue.Queue(maxsize=2)
        self.conn: sqlite3.Connection | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
        self._capture_thread.start()
        self._analysis_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        for t in (self._capture_thread, self._analysis_thread):
            if t:
                t.join(timeout=10)

    def is_running(self) -> bool:
        return self._capture_thread is not None and self._capture_thread.is_alive()

    def _capture_loop(self) -> None:
        """Captura frames rapido (1s) e alimenta feed ao vivo + fila de analise."""
        cam_cfg = self.config["camera"]
        rtsp_url = build_rtsp_url(
            cam_cfg["host"], cam_cfg["user"], cam_cfg["password"], cam_cfg["rtsp_path"]
        )
        feed_interval = 0.1  # atualiza feed a cada 100ms (~10fps)
        analysis_interval = cam_cfg.get("interval_seconds", 10)

        camera = CameraCapture(rtsp_url)
        prev_analysis_jpeg: bytes | None = None
        ticks_since_analysis = 0
        consecutive_failures = 0

        try:
            audio_cfg = self.config.get("audio", {})
            audio_device = audio_cfg.get("device", None)
            audio = AudioRecorder(audio_dir="registros/audio", device=audio_device)
            audio.start()
        except Exception:
            audio = None

        transcriber = AudioTranscriber(audio_dir="registros/audio")
        transcriber.start()

        while not self._stop_event.is_set():
            jpeg_bytes = camera.read()

            if jpeg_bytes is None:
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    self._notify_gui({"type": "error", "message": f"Reconectando camera..."})
                    camera = CameraCapture(rtsp_url)
                    consecutive_failures = 0
                    self._stop_event.wait(2)
                continue

            consecutive_failures = 0

            # Atualiza feed ao vivo sempre
            self._notify_gui({"type": "live_feed", "jpeg_bytes": jpeg_bytes})

            # Envia para analise se passaram N segundos E imagem mudou
            ticks_since_analysis += feed_interval
            if ticks_since_analysis >= analysis_interval:
                if prev_analysis_jpeg is None or not _frames_are_similar(jpeg_bytes, prev_analysis_jpeg):
                    try:
                        self._analysis_queue.put_nowait(jpeg_bytes)
                        prev_analysis_jpeg = jpeg_bytes
                    except queue.Full:
                        pass
                ticks_since_analysis = 0

            self._stop_event.wait(feed_interval)

        camera.release()
        transcriber.stop()
        if audio:
            try:
                audio.stop()
            except Exception:
                pass

    def _analysis_loop(self) -> None:
        """Consome frames da fila, chama Bedrock e salva resultados."""
        self.conn = open_db("data/cam.db")

        bedrock_cfg = self.config["bedrock"]
        telegram_cfg = self.config.get("telegram")
        rules = self.config.get("rules", [])
        bearer_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")

        owner_photo_path = self.config.get("owner_photo", "data/owner.jpg")
        owner_jpeg: bytes | None = None
        if os.path.exists(owner_photo_path):
            with open(owner_photo_path, "rb") as f:
                owner_jpeg = f.read()

        while not self._stop_event.is_set():
            try:
                jpeg_bytes = self._analysis_queue.get(timeout=2)
            except queue.Empty:
                continue

            try:
                analysis = analyze_frame(
                    jpeg_bytes,
                    region=bedrock_cfg["region"],
                    model_id=bedrock_cfg["model_id"],
                    bearer_token=bearer_token,
                    owner_jpeg=owner_jpeg,
                )

                frame_path = save_frame_to_disk(jpeg_bytes, frames_dir="frames")
                frame_id = insert_frame(
                    self.conn, frame_path, analysis["description"], analysis.get("raw", "")
                )
                _save_registro(frame_id, jpeg_bytes, analysis["description"])

                triggered = detect_events(analysis, rules)

                # Registra quando owner sai — para controle do cooldown da agenda
                away_events = {"person_away_from_pc", "person_leaving_house"}
                if any(e["event_type"] in away_events for e in triggered):
                    record_owner_away()

                action_results = execute_actions(
                    triggered, jpeg_bytes, frames_dir="frames",
                    telegram_cfg=telegram_cfg, tts_engine=None
                )

                for event in triggered:
                    event_id = insert_event(
                        self.conn, frame_id, event["event_type"], event["confidence"]
                    )
                    for res in action_results:
                        if res.get("event_type") == event["event_type"]:
                            insert_action_log(
                                self.conn, event_id,
                                res["action_type"], str(res.get("payload", "")), res["status"]
                            )

                self._notify_gui({
                    "type": "analysis",
                    "jpeg_bytes": jpeg_bytes,
                    "frame_path": frame_path,
                    "description": analysis["description"],
                    "triggered": triggered,
                    "action_results": action_results,
                })

            except Exception as e:
                self._notify_gui({"type": "error", "message": str(e)})

    def _notify_gui(self, payload: dict[str, Any]) -> None:
        if self.gui_queue:
            try:
                self.gui_queue.put_nowait(payload)
            except queue.Full:
                pass
