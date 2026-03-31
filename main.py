# main.py
import queue
import sys
import tkinter as tk

from cam.service import CameraService


def cmd_start():
    gui_queue: queue.Queue = queue.Queue(maxsize=10)
    service = CameraService(gui_queue=gui_queue)
    service.start()

    root = tk.Tk()

    from cam.gui import CameraGUI
    CameraGUI(root, service, gui_queue)

    def on_close():
        service.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def cmd_stop():
    print("O servico encerra quando a janela GUI for fechada ou o botao Stop for clicado.")
    print("Para forcar encerramento: feche a janela GUI ou pressione Ctrl+C.")


def cmd_status():
    print("Use 'python main.py start' para iniciar o monitor.")
    print("O servico roda enquanto a janela GUI estiver aberta.")


def main():
    command = sys.argv[1] if len(sys.argv) >= 2 else "start"
    if command == "start":
        cmd_start()
    elif command == "stop":
        cmd_stop()
    elif command == "status":
        cmd_status()
    else:
        print("Uso: python main.py [start|stop|status]")
        sys.exit(1)


if __name__ == "__main__":
    main()
