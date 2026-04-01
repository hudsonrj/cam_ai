# cam/gui.py
import io
import os
import queue
import threading
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
        tk.Button(bot, text="🎤 Assistente", bg="#1a3a5c", fg="#7ec8e3",
                  command=self._open_assistant,
                  relief=tk.FLAT, font=("Helvetica", 9), padx=8).pack(side=tk.LEFT, padx=2)
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

    # ── Assistant ─────────────────────────────────────────────────────────────

    def _open_assistant(self) -> None:
        AssistantWindow(self.root, self.service, self._history, self._live_jpeg)

    # ── Controls ──────────────────────────────────────────────────────────────

    def _stop(self) -> None:
        self.service.stop()
        self.status_label.config(text="● Parado", fg="#aaa")


# ── Assistant Window ──────────────────────────────────────────────────────────

class AssistantWindow:
    """Janela de chat com o assistente de IA com visão de câmera."""

    BG = "#0d1117"
    BG2 = "#161b22"
    ACCENT = "#7ec8e3"
    USER_COLOR = "#e0e0e0"
    BOT_COLOR = "#7ec8e3"
    INPUT_BG = "#1c2128"

    def __init__(self, parent, service, gui_history: deque, live_jpeg_ref):
        self._service = service
        self._gui_history = gui_history
        self._live_jpeg_ref = live_jpeg_ref  # mutable reference holder

        self._assistant = None  # inicializado lazy na primeira mensagem
        self._recorder = None
        self._busy = False

        self.win = tk.Toplevel(parent)
        self.win.title("🤖 Assistente CAM")
        self.win.configure(bg=self.BG)
        self.win.geometry("520x560")
        self.win.resizable(True, True)
        self.win.transient(parent)

        self._build_ui()
        self._append_bot(
            "Olá! Sou o assistente CAM. Posso ver o que a câmera está captando "
            "e lembro do que aconteceu durante o dia. Como posso ajudar?"
        )

    def _build_ui(self) -> None:
        # Header
        hdr = tk.Frame(self.win, bg="#1f2937", pady=6)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="🤖  Assistente com Visão", bg="#1f2937", fg=self.ACCENT,
                 font=("Helvetica", 11, "bold")).pack(side=tk.LEFT, padx=12)
        tk.Button(hdr, text="Limpar", bg="#1f2937", fg="#555",
                  command=self._clear_chat, relief=tk.FLAT,
                  font=("Helvetica", 8)).pack(side=tk.RIGHT, padx=8)

        # Chat area
        chat_frame = tk.Frame(self.win, bg=self.BG)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        sb = tk.Scrollbar(chat_frame, bg=self.BG)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._chat = tk.Text(
            chat_frame, bg=self.BG2, fg=self.USER_COLOR,
            font=("Helvetica", 10), wrap=tk.WORD,
            relief=tk.FLAT, state=tk.DISABLED,
            yscrollcommand=sb.set, padx=8, pady=8,
        )
        self._chat.pack(fill=tk.BOTH, expand=True)
        sb.config(command=self._chat.yview)

        self._chat.tag_config("user", foreground="#e0e0e0", font=("Helvetica", 10, "bold"))
        self._chat.tag_config("bot", foreground=self.BOT_COLOR, font=("Helvetica", 10))
        self._chat.tag_config("label", foreground="#555", font=("Helvetica", 8))
        self._chat.tag_config("thinking", foreground="#555", font=("Helvetica", 9, "italic"))

        # Status bar
        self._status = tk.Label(self.win, text="", bg=self.BG, fg="#555",
                                font=("Helvetica", 8))
        self._status.pack(fill=tk.X, padx=12)

        # Input area
        input_frame = tk.Frame(self.win, bg=self.BG, pady=6)
        input_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        self._input = tk.Text(input_frame, bg=self.INPUT_BG, fg=self.USER_COLOR,
                              font=("Helvetica", 10), height=3, relief=tk.FLAT,
                              wrap=tk.WORD, insertbackground="white")
        self._input.pack(fill=tk.X, pady=(0, 6))
        self._input.bind("<Return>", self._on_enter)
        self._input.bind("<Shift-Return>", lambda e: None)  # nova linha

        btn_row = tk.Frame(input_frame, bg=self.BG)
        btn_row.pack(fill=tk.X)

        self._btn_mic = tk.Button(
            btn_row, text="🎤 Gravar", bg="#1a3a5c", fg=self.ACCENT,
            command=self._toggle_recording, relief=tk.FLAT,
            font=("Helvetica", 9), padx=10,
        )
        self._btn_mic.pack(side=tk.LEFT)

        tk.Button(
            btn_row, text="Enviar ↵", bg="#0f9b58", fg="white",
            command=self._send_text, relief=tk.FLAT,
            font=("Helvetica", 9), padx=10,
        ).pack(side=tk.RIGHT)

    # ── Chat helpers ─────────────────────────────────────────────────────────

    def _append_user(self, text: str) -> None:
        self._chat.config(state=tk.NORMAL)
        self._chat.insert(tk.END, "Você\n", "label")
        self._chat.insert(tk.END, text + "\n\n", "user")
        self._chat.config(state=tk.DISABLED)
        self._chat.see(tk.END)

    def _append_bot(self, text: str) -> None:
        self._chat.config(state=tk.NORMAL)
        self._chat.insert(tk.END, "CAM\n", "label")
        self._chat.insert(tk.END, text + "\n\n", "bot")
        self._chat.config(state=tk.DISABLED)
        self._chat.see(tk.END)

    def _set_thinking(self, active: bool) -> None:
        self._chat.config(state=tk.NORMAL)
        if active:
            self._chat.insert(tk.END, "⏳ pensando...\n", "thinking")
            self._chat.mark_set("thinking_start",
                                self._chat.index(tk.END + "-2l"))
        else:
            # Remove linha "pensando"
            try:
                self._chat.delete("thinking_start", tk.END)
            except Exception:
                pass
        self._chat.config(state=tk.DISABLED)
        self._chat.see(tk.END)

    def _clear_chat(self) -> None:
        self._chat.config(state=tk.NORMAL)
        self._chat.delete("1.0", tk.END)
        self._chat.config(state=tk.DISABLED)
        if self._assistant:
            self._assistant.clear()

    # ── Send logic ────────────────────────────────────────────────────────────

    def _on_enter(self, event) -> str:
        if not event.state & 0x1:  # Shift não pressionado
            self._send_text()
            return "break"
        return None

    def _send_text(self) -> None:
        text = self._input.get("1.0", tk.END).strip()
        if not text or self._busy:
            return
        self._input.delete("1.0", tk.END)
        self._dispatch(text)

    def _dispatch(self, text: str) -> None:
        self._busy = True
        self._append_user(text)
        self._set_thinking(True)
        self._status.config(text="Consultando IA...")

        # Captura frame atual antes da thread
        jpeg = self._service.get_last_jpeg() if hasattr(self._service, "get_last_jpeg") else None

        thread = threading.Thread(
            target=self._process, args=(text, jpeg), daemon=True
        )
        thread.start()

    def _process(self, text: str, jpeg: bytes | None) -> None:
        try:
            if self._assistant is None:
                from cam.assistant import ConversationAssistant
                cfg = self._service.config
                token = __import__("os").environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
                self._assistant = ConversationAssistant(
                    region=cfg["bedrock"]["region"],
                    model_id=cfg["bedrock"]["model_id"],
                    bearer_token=token,
                )

            context = self._build_context()
            reply = self._assistant.chat(text, jpeg, context)
        except Exception as e:
            reply = f"Erro ao consultar IA: {e}"

        self.win.after(0, self._on_reply, reply)

    def _on_reply(self, reply: str) -> None:
        self._set_thinking(False)
        self._append_bot(reply)
        self._busy = False
        self._status.config(text="")
        # Fala a resposta
        self._speak(reply)

    def _build_context(self) -> str:
        """Monta resumo do dia a partir do histórico da GUI."""
        items = list(self._gui_history)[:8]
        parts = []
        for entry in reversed(items):
            ts = entry["ts"].strftime("%H:%M")
            desc = entry["description"][:80]
            events = [e["event_type"] for e in entry.get("triggered", [])]
            if events:
                parts.append(f"{ts} [{', '.join(events)}] {desc}")
            else:
                parts.append(f"{ts} {desc}")
        return "; ".join(parts) if parts else "sem eventos registrados"

    # ── Recording ─────────────────────────────────────────────────────────────

    def _toggle_recording(self) -> None:
        if self._busy:
            return

        if self._recorder and self._recorder.is_recording:
            self._btn_mic.config(text="🎤 Gravar", bg="#1a3a5c", fg=self.ACCENT)
            self._status.config(text="Transcrevendo...")
            wav_path = self._recorder.stop_and_save()
            if wav_path:
                threading.Thread(
                    target=self._transcribe_and_send,
                    args=(wav_path,), daemon=True
                ).start()
            else:
                self._status.config(text="Áudio muito curto.")
        else:
            from cam.assistant import MicRecorder
            cfg = self._service.config
            device = cfg.get("audio", {}).get("device")
            self._recorder = MicRecorder(device=device)
            self._recorder.start()
            self._btn_mic.config(text="⏹ Parar", bg="#8b0000", fg="white")
            self._status.config(text="Gravando... clique para parar")

    def _transcribe_and_send(self, wav_path: str) -> None:
        try:
            from cam.assistant import transcribe_wav
            text = transcribe_wav(wav_path)
        except Exception:
            text = ""
        finally:
            try:
                __import__("os").unlink(wav_path)
            except Exception:
                pass

        if text:
            self.win.after(0, lambda: self._input.insert(tk.END, text))
            self.win.after(50, self._send_text)
        else:
            self.win.after(0, self._status.config, {"text": "Nenhuma fala detectada."})

    # ── TTS ───────────────────────────────────────────────────────────────────

    def _speak(self, text: str) -> None:
        import subprocess
        safe = text.replace('"', "'").replace("\n", " ")
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command",
             f'Add-Type -AssemblyName System.Speech; '
             f'$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
             f'$s.Rate = 1; $s.Speak("{safe}")'],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
