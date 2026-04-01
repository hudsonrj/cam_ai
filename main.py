# main.py
import queue
import sys


def cmd_start():
    import tkinter as tk
    from cam.service import load_config, MultiCameraService
    from cam.gui import CameraGUI

    cfg = load_config()
    gui_queue: queue.Queue = queue.Queue(maxsize=10)
    service = MultiCameraService(cfg, gui_queue=gui_queue)
    service.start()

    root = tk.Tk()
    CameraGUI(root, service, gui_queue)

    def on_close():
        service.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def cmd_web():
    import queue as _queue
    import uvicorn
    from cam.service import load_config, MultiCameraService
    from cam.web_server import app, set_service, set_ambient
    from cam.ambient_recorder import AmbientRecorder
    from cam.ambient_transcriber import AmbientTranscriber

    cfg = load_config()

    # Camera service
    service = MultiCameraService(cfg)
    service.start()
    set_service(service)

    # Ambient audio recording
    ambient_cfg = cfg.get("ambient", {})
    if ambient_cfg.get("enabled", True):
        chunk_queue = _queue.Queue(maxsize=200)
        device_indices = ambient_cfg.get("devices")  # None = todos
        recorder = AmbientRecorder(chunk_queue, device_indices=device_indices)
        transcriber = AmbientTranscriber(chunk_queue)
        started = recorder.start()
        transcriber.start()
        set_ambient(recorder, transcriber)
        if started:
            names = ", ".join(d["name"] for d in started)
            print(f"Gravacao de ambiente: {len(started)} microfone(s) — {names}")
        else:
            print("Aviso: nenhum microfone encontrado para gravacao de ambiente.")

    host = cfg.get("web", {}).get("host", "0.0.0.0")
    port = cfg.get("web", {}).get("port", 8080)
    print(f"CAM AI Web Dashboard: http://localhost:{port}")
    uvicorn.run(app, host=host, port=port)


def cmd_stop():
    print("O servico encerra quando a janela GUI for fechada ou o botao Stop for clicado.")
    print("Para forcar encerramento: feche a janela GUI ou pressione Ctrl+C.")


def cmd_status():
    print("Use 'python main.py start' para iniciar o monitor (GUI).")
    print("Use 'python main.py web'   para iniciar o dashboard web.")


def main():
    command = sys.argv[1] if len(sys.argv) >= 2 else "start"
    if command == "start":
        cmd_start()
    elif command == "web":
        cmd_web()
    elif command == "stop":
        cmd_stop()
    elif command == "status":
        cmd_status()
    else:
        print("Uso: python main.py [start|web|stop|status]")
        sys.exit(1)


if __name__ == "__main__":
    main()
