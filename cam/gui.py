# cam/gui.py
import io
import os
import queue
import tkinter as tk
from collections import deque
from datetime import datetime

from PIL import Image, ImageTk

EVENT_COLORS = {
    "person_approaching": "#FF4444",
    "door_open": "#FF8C00",
    "person_leaving_house": "#FFD700",
    "person_away_from_pc": "#90EE90",
    "owner_recognized": "#00BFFF",
    "unknown_person_detected": "#FF4444",
}
MAX_HISTORY = 100


class CameraGUI:
    def __init__(self, root: tk.Tk, service, gui_queue: queue.Queue):
        self.root = root
        self.service = service
        self.gui_queue = gui_queue
        self._history: deque = deque(maxlen=MAX_HISTORY)
        self._photo = None
        self._live_jpeg: bytes | None = None
        self._viewing_history: bool = False
        self._last_description: str = ""
        self._history_visible: bool = True

        root.title("CAM")
        root.configure(bg="#1a1a2e")
        root.geometry("1000x620")
        root.minsize(320, 240)
        root.resizable(True, True)

        self._build_layout()
        self._poll_queue()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        # Top bar
        top = tk.Frame(self.root, bg="#16213e", pady=4)
        top.pack(fill=tk.X)
        tk.Label(top, text="CAM Monitor", bg="#16213e", fg="white",
                 font=("Helvetica", 11, "bold")).pack(side=tk.LEFT, padx=10)
        tk.Button(top, text="Stop", bg="#e94560", fg="white",
                  command=self._stop, relief=tk.FLAT, padx=8).pack(side=tk.RIGHT, padx=4)
        tk.Button(top, text="Ao vivo", bg="#0f9b58", fg="white",
                  command=self._show_live, relief=tk.FLAT, padx=8).pack(side=tk.RIGHT, padx=2)
        tk.Button(top, text="Histórico ▶", bg="#16213e", fg="#aaa",
                  command=self._toggle_history, relief=tk.FLAT, padx=8,
                  activebackground="#16213e").pack(side=tk.RIGHT, padx=2)
        self._btn_history_toggle = top.winfo_children()[-1]

        # Main area
        self._main = tk.Frame(self.root, bg="#1a1a2e")
        self._main.pack(fill=tk.BOTH, expand=True)

        # Left: feed (fills all available space)
        self._left = tk.Frame(self._main, bg="#0f3460")
        self._left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.feed_label = tk.Label(self._left, bg="#0f3460",
                                   text="Aguardando feed...", fg="#aaa")
        self.feed_label.pack(fill=tk.BOTH, expand=True)

        # Bottom bar (over feed)
        bot = tk.Frame(self._left, bg="#0a2040", pady=3)
        bot.pack(fill=tk.X)
        tk.Button(bot, text="📄 Descrição", bg="#0a2040", fg="#e0e0e0",
                  command=self._show_description_modal,
                  relief=tk.FLAT, font=("Helvetica", 9), padx=8).pack(side=tk.LEFT, padx=4)
        self.ts_label = tk.Label(bot, text="", bg="#0a2040", fg="#555",
                                 font=("Helvetica", 8))
        self.ts_label.pack(side=tk.RIGHT, padx=8)
        self.status_label = tk.Label(bot, text="● Rodando", bg="#0a2040", fg="#90EE90",
                                     font=("Helvetica", 8))
        self.status_label.pack(side=tk.RIGHT, padx=4)

        # Right: history panel
        self._right = tk.Frame(self._main, bg="#16213e", width=280)
        self._right.pack(side=tk.RIGHT, fill=tk.Y)
        self._right.pack_propagate(False)
        self._build_history_panel()

        # Resize event — atualiza imagem quando janela muda de tamanho
        self.feed_label.bind("<Configure>", self._on_feed_resize)

    def _build_history_panel(self) -> None:
        tk.Label(self._right, text="DETECÇÕES", bg="#16213e", fg="#aaa",
                 font=("Helvetica", 8, "bold")).pack(pady=(8, 2))

        frame = tk.Frame(self._right, bg="#16213e")
        frame.pack(fill=tk.BOTH, expand=True, padx=2)

        self._canvas = tk.Canvas(frame, bg="#16213e", highlightthickness=0)
        sb = tk.Scrollbar(frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._history_inner = tk.Frame(self._canvas, bg="#16213e")
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._history_inner, anchor="nw")
        self._history_inner.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
            lambda e: self._canvas.itemconfig(self._canvas_win, width=e.width))

        tk.Label(self._right, text="ÚLTIMA AÇÃO", bg="#16213e", fg="#555",
                 font=("Helvetica", 7, "bold")).pack(pady=(4, 0))
        self.action_label = tk.Label(self._right, text="-", bg="#16213e", fg="#90EE90",
                                     font=("Helvetica", 8), wraplength=270)
        self.action_label.pack(padx=4, pady=(0, 6))

    # ── Queue polling ─────────────────────────────────────────────────────────

    def _poll_queue(self) -> None:
        try:
            while True:
                self._handle_message(self.gui_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_message(self, msg: dict) -> None:
        t = msg["type"]
        if t == "live_feed":
            self._live_jpeg = msg["jpeg_bytes"]
            if not self._viewing_history:
                self._update_feed(msg["jpeg_bytes"])

        elif t == "analysis":
            self._live_jpeg = msg["jpeg_bytes"]
            self._last_description = msg["description"]
            if not self._viewing_history:
                self._update_feed(msg["jpeg_bytes"])
            self.ts_label.config(text=datetime.now().strftime("%H:%M:%S"))

            entry = {
                "ts": datetime.now(),
                "frame_path": msg.get("frame_path", ""),
                "description": msg["description"],
                "jpeg_bytes": msg["jpeg_bytes"],
                "triggered": msg.get("triggered", []),
            }
            self._history.appendleft(entry)
            self._add_history_row(entry)

            if msg.get("action_results"):
                last = msg["action_results"][-1]
                self.action_label.config(
                    text=f"{last['action_type']} [{last['status']}]",
                    fg="#90EE90" if last["status"] == "success" else "#FF4444"
                )

        elif t == "error":
            self.status_label.config(text=f"● {msg['message'][:30]}", fg="#FF4444")

    # ── Feed ─────────────────────────────────────────────────────────────────

    def _on_feed_resize(self, event=None) -> None:
        if self._live_jpeg and not self._viewing_history:
            self._update_feed(self._live_jpeg)

    def _update_feed(self, jpeg_bytes: bytes) -> None:
        w = max(self.feed_label.winfo_width(), 160)
        h = max(self.feed_label.winfo_height(), 120)
        img = Image.open(io.BytesIO(jpeg_bytes))
        img.thumbnail((w, h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.feed_label.config(image=self._photo, text="")

    # ── Description modal ─────────────────────────────────────────────────────

    def _show_description_modal(self) -> None:
        desc = self._last_description or "Nenhuma análise ainda."
        win = tk.Toplevel(self.root)
        win.title("Descrição da cena")
        win.configure(bg="#1a1a2e")
        win.geometry("520x300")
        win.resizable(True, True)
        win.transient(self.root)

        tk.Label(win, text="Última análise da IA", bg="#1a1a2e", fg="#aaa",
                 font=("Helvetica", 9, "bold")).pack(pady=(10, 4))

        frame = tk.Frame(win, bg="#1a1a2e")
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        sb = tk.Scrollbar(frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        txt = tk.Text(frame, bg="#0f3460", fg="#e0e0e0", font=("Helvetica", 10),
                      wrap=tk.WORD, relief=tk.FLAT, yscrollcommand=sb.set)
        txt.insert("1.0", desc)
        txt.config(state=tk.DISABLED)
        txt.pack(fill=tk.BOTH, expand=True)
        sb.config(command=txt.yview)

        tk.Button(win, text="Fechar", bg="#e94560", fg="white",
                  command=win.destroy, relief=tk.FLAT, padx=12).pack(pady=(0, 10))

    # ── History ───────────────────────────────────────────────────────────────

    def _toggle_history(self) -> None:
        self._history_visible = not self._history_visible
        if self._history_visible:
            self._right.pack(side=tk.RIGHT, fill=tk.Y)
            self._btn_history_toggle.config(text="Histórico ▶")
        else:
            self._right.pack_forget()
            self._btn_history_toggle.config(text="Histórico ◀")

    def _add_history_row(self, entry: dict) -> None:
        triggered = entry.get("triggered", [])
        if triggered:
            color = EVENT_COLORS.get(triggered[0]["event_type"], "#FF4444")
            label = " | ".join(e["event_type"] for e in triggered)
        else:
            color = "#444"
            label = "análise"

        ts_str = entry["ts"].strftime("%H:%M:%S")
        bg = "#1e2a3a" if triggered else "#151c27"

        row = tk.Frame(self._history_inner, bg=bg, pady=2, padx=4, cursor="hand2")
        row.pack(fill=tk.X, pady=1)

        hdr = tk.Frame(row, bg=bg)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="●", bg=bg, fg=color,
                 font=("Helvetica", 8)).pack(side=tk.LEFT)
        tk.Label(hdr, text=f"{ts_str}  {label}", bg=bg, fg="#ddd",
                 font=("Helvetica", 7, "bold"), anchor="w").pack(side=tk.LEFT, padx=3)

        snippet = entry["description"][:70] + ("…" if len(entry["description"]) > 70 else "")
        tk.Label(row, text=snippet, bg=bg, fg="#888",
                 font=("Helvetica", 7), wraplength=255,
                 justify=tk.LEFT, anchor="w").pack(fill=tk.X)

        jpeg = entry["jpeg_bytes"]
        desc = entry["description"]
        for w in (row, hdr) + tuple(row.winfo_children()):
            w.bind("<Button-1>", lambda e, j=jpeg, d=desc: self._show_snapshot(j, d))

        self._canvas.yview_moveto(0)

    def _show_snapshot(self, jpeg_bytes: bytes, description: str) -> None:
        self._viewing_history = True
        self._update_feed(jpeg_bytes)
        self._last_description = description

    def _show_live(self) -> None:
        self._viewing_history = False
        if self._live_jpeg:
            self._update_feed(self._live_jpeg)

    # ── Controls ──────────────────────────────────────────────────────────────

    def _stop(self) -> None:
        self.service.stop()
        self.status_label.config(text="● Parado", fg="#aaa")
