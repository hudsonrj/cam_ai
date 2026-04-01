# cam/service.py
import os
import queue
import sqlite3
import threading
from datetime import datetime, time as dt_time
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
from cam.event_bus import EventBus
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
        cfg = yaml.safe_load(f)

    # Normaliza: garante lista 'cameras' mesmo com config legado 'camera'
    if "cameras" not in cfg and "camera" in cfg:
        cam = cfg["camera"]
        cfg["cameras"] = [{"id": "main", "name": "Principal", **cam}]
    elif "cameras" not in cfg:
        cfg["cameras"] = []

    return cfg


def _frames_are_similar(jpeg_a: bytes, jpeg_b: bytes, threshold: float = 0.92) -> bool:
    arr_a = np.frombuffer(jpeg_a, np.uint8)
    arr_b = np.frombuffer(jpeg_b, np.uint8)
    img_a = cv2.imdecode(arr_a, cv2.IMREAD_COLOR)
    img_b = cv2.imdecode(arr_b, cv2.IMREAD_COLOR)
    if img_a is None or img_b is None:
        return False
    img_a = cv2.resize(img_a, (64, 64))
    img_b = cv2.resize(img_b, (64, 64))
    hist_a = cv2.calcHist([img_a], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
    hist_b = cv2.calcHist([img_b], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    return cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL) >= threshold


def _get_analysis_interval(cfg: dict, base_interval: float) -> float:
    """Retorna o intervalo de análise baseado no horário (night mode)."""
    schedule = cfg.get("schedule", [])
    if not schedule:
        return base_interval

    now = datetime.now().time()
    for slot in schedule:
        try:
            start = dt_time(*map(int, slot["start"].split(":")))
            end = dt_time(*map(int, slot["end"].split(":")))
            interval = slot.get("interval_seconds", base_interval)
            if start <= end:
                if start <= now <= end:
                    return interval
            else:  # overnight: e.g. 22:00 - 07:00
                if now >= start or now <= end:
                    return interval
        except Exception:
            pass
    return base_interval


class CameraService:
    def __init__(self, camera_cfg: dict, config: dict,
                 event_bus: EventBus,
                 gui_queue: queue.Queue | None = None,
                 camera_id: str = "main"):
        self.config = config
        self.camera_cfg = camera_cfg
        self.camera_id = camera_id
        self.event_bus = event_bus
        self.gui_queue = gui_queue  # mantido para compat com GUI Tkinter
        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._analysis_thread: threading.Thread | None = None
        self._analysis_queue: queue.Queue = queue.Queue(maxsize=2)
        self._last_jpeg: bytes | None = None
        # Proactive mode state
        self._sitting_since: datetime | None = None

    @classmethod
    def from_config(cls, config: dict, gui_queue: queue.Queue | None = None,
                    event_bus: EventBus | None = None) -> "CameraService":
        """Cria serviço da câmera principal (compatibilidade com modo legado)."""
        bus = event_bus or EventBus()
        cameras = config.get("cameras", [])
        cam_cfg = cameras[0] if cameras else config.get("camera", {})
        return cls(cam_cfg, config, bus, gui_queue, camera_id=cam_cfg.get("id", "main"))

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

    def get_last_jpeg(self) -> bytes | None:
        return self._last_jpeg

    def _capture_loop(self) -> None:
        cam_cfg = self.camera_cfg
        rtsp_url = build_rtsp_url(
            cam_cfg["host"], cam_cfg["user"], cam_cfg["password"], cam_cfg["rtsp_path"]
        )
        feed_interval = 0.1
        base_interval = cam_cfg.get("interval_seconds", 10)

        camera = CameraCapture(rtsp_url)
        prev_analysis_jpeg: bytes | None = None
        ticks_since_analysis = 0.0
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
                    self._notify({"type": "error", "message": "Reconectando camera...",
                                  "camera_id": self.camera_id})
                    camera = CameraCapture(rtsp_url)
                    consecutive_failures = 0
                    self._stop_event.wait(2)
                continue

            consecutive_failures = 0
            self._last_jpeg = jpeg_bytes
            self._notify({"type": "live_feed", "jpeg_bytes": jpeg_bytes,
                          "camera_id": self.camera_id})

            analysis_interval = _get_analysis_interval(self.config, base_interval)
            ticks_since_analysis += feed_interval
            if ticks_since_analysis >= analysis_interval:
                if prev_analysis_jpeg is None or not _frames_are_similar(jpeg_bytes, prev_analysis_jpeg):
                    try:
                        self._analysis_queue.put_nowait(jpeg_bytes)
                        prev_analysis_jpeg = jpeg_bytes
                    except queue.Full:
                        pass
                ticks_since_analysis = 0.0

            self._stop_event.wait(feed_interval)

        camera.release()
        transcriber.stop()
        if audio:
            try:
                audio.stop()
            except Exception:
                pass

    def _analysis_loop(self) -> None:
        conn = open_db("data/cam.db")

        bedrock_cfg = self.config["bedrock"]
        telegram_cfg = self.config.get("telegram")
        ha_cfg = self.config.get("home_assistant")
        rules = self.config.get("rules", [])

        # Classificador comportamental de camera
        from cam.behavior_classifier import classify_camera_behaviors
        from cam.behavior_store import (
            insert_behavior_event, upsert_person_profile,
        )
        behavior_enabled = self.config.get("behavior", {}).get("enabled", True)
        behavior_interval = self.config.get("behavior", {}).get("interval_seconds", 60)
        behavior_ticks = 0.0
        bearer_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")

        # Owner photo
        owner_jpeg: bytes | None = None
        owner_path = self.config.get("owner_photo", "data/owner.jpg")
        if os.path.exists(owner_path):
            with open(owner_path, "rb") as f:
                owner_jpeg = f.read()

        # Known people (visitor recognition)
        known_people: list[dict] = []
        for person in self.config.get("known_people", []):
            photo_path = person.get("photo")
            if photo_path and os.path.exists(photo_path):
                with open(photo_path, "rb") as f:
                    known_people.append({"name": person["name"], "jpeg": f.read()})

        # Proactive mode config
        proactive_cfg = self.config.get("proactive", {})
        proactive_enabled = proactive_cfg.get("enabled", False)
        proactive_threshold_min = proactive_cfg.get("threshold_minutes", 90)

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
                    known_people=known_people if known_people else None,
                )

                frame_path = save_frame_to_disk(jpeg_bytes, frames_dir="frames")
                frame_id = insert_frame(
                    conn, frame_path, analysis["description"],
                    analysis.get("raw", ""), camera_id=self.camera_id,
                )
                _save_registro(frame_id, jpeg_bytes, analysis["description"])

                triggered = detect_events(analysis, rules)

                # Proactive: detecta tempo contínuo no PC
                if proactive_enabled:
                    self._check_proactive(triggered, proactive_threshold_min, conn)

                # Registra ausência para cooldown da agenda
                away_events = {"person_away_from_pc", "person_leaving_house"}
                if any(e["event_type"] in away_events for e in triggered):
                    record_owner_away()
                    self._sitting_since = None

                # Registra visitantes conhecidos + perfis de pessoas
                ts_now = datetime.now().isoformat(timespec="seconds")
                for ev in triggered:
                    if ev["event_type"].startswith("visitor_") and "_recognized" in ev["event_type"]:
                        name = ev["event_type"].replace("visitor_", "").replace("_recognized", "").replace("_", " ")
                        try:
                            from cam.db import upsert_visitor
                            upsert_visitor(conn, name)
                            upsert_person_profile(conn, name)
                            insert_behavior_event(conn, ts_now, "camera", "person_seen",
                                                  person_name=name, camera_id=self.camera_id)
                        except Exception:
                            pass
                    elif ev["event_type"] == "owner_recognized":
                        try:
                            upsert_person_profile(conn, "Hudson", "Hudson")
                            insert_behavior_event(conn, ts_now, "camera", "person_seen",
                                                  person_name="Hudson", camera_id=self.camera_id)
                        except Exception:
                            pass
                    elif ev["event_type"] == "unknown_person_detected":
                        try:
                            upsert_person_profile(conn, "desconhecido")
                            insert_behavior_event(conn, ts_now, "camera", "person_seen",
                                                  person_name="desconhecido", camera_id=self.camera_id)
                        except Exception:
                            pass

                # Classificacao comportamental da camera (intervalo proprio)
                behavior_ticks += base_interval
                if behavior_enabled and behavior_ticks >= behavior_interval:
                    behavior_ticks = 0.0
                    try:
                        behaviors = classify_camera_behaviors(
                            jpeg_bytes,
                            region=bedrock_cfg["region"],
                            model_id=bedrock_cfg["model_id"],
                            bearer_token=bearer_token,
                        )
                        for b in behaviors:
                            btype = b.get("type", "")
                            bconf = b.get("confidence", 1.0)
                            meta = {k: v for k, v in b.items()
                                    if k not in ("type", "confidence")}
                            insert_behavior_event(
                                conn, ts_now, "camera", btype,
                                metadata=meta, confidence=bconf,
                                camera_id=self.camera_id,
                            )
                    except Exception:
                        pass

                # Push automático para Home Assistant (todos os eventos)
                if ha_cfg and ha_cfg.get("webhook_url"):
                    from cam.home_assistant import push_event as ha_push
                    for ev in triggered:
                        ha_push(
                            event_type=ev["event_type"],
                            confidence=ev.get("confidence", 1.0),
                            description=analysis["description"],
                            camera_id=self.camera_id,
                            frame_id=frame_id,
                            ha_cfg=ha_cfg,
                        )

                action_results = execute_actions(
                    triggered, jpeg_bytes, frames_dir="frames",
                    telegram_cfg=telegram_cfg, ha_cfg=ha_cfg, tts_engine=None,
                )

                for event in triggered:
                    event_id = insert_event(conn, frame_id, event["event_type"], event["confidence"])
                    for res in action_results:
                        if res.get("event_type") == event["event_type"]:
                            insert_action_log(conn, event_id, res["action_type"],
                                              str(res.get("payload", "")), res["status"])

                payload = {
                    "type": "analysis",
                    "camera_id": self.camera_id,
                    "jpeg_bytes": jpeg_bytes,
                    "frame_path": frame_path,
                    "frame_id": frame_id,
                    "description": analysis["description"],
                    "triggered": triggered,
                    "action_results": action_results,
                    "ts": datetime.now().strftime("%H:%M:%S"),
                }
                self._notify(payload)

            except Exception as e:
                self._notify({"type": "error", "message": str(e),
                              "camera_id": self.camera_id})

    def _check_proactive(self, triggered: list, threshold_min: int, conn) -> None:
        """Monitora tempo contínuo sentado — dispara alerta de pausa se necessário."""
        sitting = any(e["event_type"] == "person_sitting_at_pc" for e in triggered)
        if sitting:
            if self._sitting_since is None:
                self._sitting_since = datetime.now()
            else:
                elapsed = (datetime.now() - self._sitting_since).total_seconds() / 60
                if elapsed >= threshold_min:
                    msg = (f"Hudson, você está no computador há {int(elapsed)} minutos. "
                           "Que tal fazer uma pausa?")
                    self._notify({"type": "proactive", "text": msg,
                                  "camera_id": self.camera_id})
                    self._sitting_since = datetime.now()  # reset
        else:
            self._sitting_since = None

    def _notify(self, payload: dict[str, Any]) -> None:
        self.event_bus.publish(payload)
        if self.gui_queue:
            try:
                self.gui_queue.put_nowait(payload)
            except queue.Full:
                pass


class MultiCameraService:
    """Gerencia múltiplas câmeras em paralelo com event bus compartilhado."""

    def __init__(self, config: dict, gui_queue: queue.Queue | None = None):
        self.config = config
        self.event_bus = EventBus()
        self._services: list[CameraService] = []

        cameras = config.get("cameras", [])
        for cam_cfg in cameras:
            svc = CameraService(
                camera_cfg=cam_cfg,
                config=config,
                event_bus=self.event_bus,
                gui_queue=gui_queue,
                camera_id=cam_cfg.get("id", "main"),
            )
            self._services.append(svc)

    def start(self) -> None:
        for svc in self._services:
            svc.start()

    def stop(self) -> None:
        for svc in self._services:
            svc.stop()

    def get_last_jpeg(self, camera_id: str = "main") -> bytes | None:
        for svc in self._services:
            if svc.camera_id == camera_id:
                return svc.get_last_jpeg()
        return self._services[0].get_last_jpeg() if self._services else None

    def get_camera_ids(self) -> list[str]:
        return [svc.camera_id for svc in self._services]

    def get_cameras(self) -> list[dict]:
        return [{"id": c.get("id", "main"), "name": c.get("name", c.get("id", "main"))}
                for c in self.config.get("cameras", [])]
