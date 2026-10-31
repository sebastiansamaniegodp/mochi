"""Arranque de Mochi.

    mochi                         normal: se conecta sola a la placa al enchufarla
    mochi --port /dev/ttyACM0     un puerto en particular
    mochi --echo                  sin LLM: Mochi repite lo que oyó
    mochi --stt whisper           Whisper en vez de Vosk (si está instalado)
    mochi --save-wav CARPETA      guardar cada grabación
    mochi --list                  ver los puertos serie y salir

(Desde el código: ./mochi.sh con los mismos argumentos.)
"""

import argparse
import faulthandler
import locale
import os
import signal
import sys

from mochi_app import __version__, paths


# =========================================================
# ARGUMENTOS
# =========================================================

def parse_args(argv=None):

    parser = argparse.ArgumentParser(prog="mochi", description="Mochi: hablale al robot o a la PC")
    parser.add_argument("--port", help="puerto serie del ESP32 (por defecto, se busca solo)")
    parser.add_argument("--list", action="store_true", help="listar puertos serie y salir")
    parser.add_argument("--echo", action="store_true", help="no usar el LLM: solo repetir lo que oyó")
    parser.add_argument("--no-tts", action="store_true", help="no hablar, solo mostrar texto")
    parser.add_argument("--stt", choices=["vosk", "whisper"], help="motor de voz a texto")
    parser.add_argument("--whisper-model", default="small", help="tamaño de Whisper: base, small...")
    parser.add_argument("--vosk-model", help="carpeta del modelo de Vosk")
    parser.add_argument("--piper-voice", help="voz de Piper (.onnx)")
    parser.add_argument("--save-wav", metavar="CARPETA", help="guardar cada grabación (para ajustar el micrófono)")
    parser.add_argument("--version", action="version", version=f"Mochi {__version__}")
    return parser.parse_args(argv)


# =========================================================
# REGISTRO
#
# Todo lo que imprimen el agente y la sesión va también a
# ~/.cache/mochi/mochi.log (desde el menú de KDE no hay terminal).
# =========================================================

class _Tee:

    def __init__(self, *streams):

        self.streams = [stream for stream in streams if stream is not None]


    def write(self, text):

        for stream in self.streams:
            try:
                stream.write(text)
                stream.flush()
            except (OSError, ValueError):
                pass
        return len(text)


    def flush(self):

        for stream in self.streams:
            try:
                stream.flush()
            except (OSError, ValueError):
                pass


    def isatty(self):

        return False


def _start_log():

    paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if paths.LOG_FILE.exists():
        paths.LOG_FILE.replace(paths.LOG_FILE.with_suffix(".log.1"))

    log = open(paths.LOG_FILE, "w", encoding="utf-8", errors="replace", buffering=1)
    faulthandler.enable(log)

    sys.stdout = _Tee(sys.__stdout__, log)
    sys.stderr = _Tee(sys.__stderr__, log)


def _certificates():
    """El Python del AppImage busca los certificados en otra ruta (yt-dlp, descargas)."""

    if not paths.APPIMAGE or os.environ.get("SSL_CERT_FILE"):
        return

    for path in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem"):
        if os.path.isfile(path):
            os.environ["SSL_CERT_FILE"] = path
            return


# =========================================================
# REINICIAR (después de cambiar la configuración)
# =========================================================

def _restart_command():

    if paths.APPIMAGE:
        return paths.APPIMAGE, sys.argv[1:]

    return sys.executable, ["-m", "mochi_app", *sys.argv[1:]]


# =========================================================
# MAIN
# =========================================================

def main(argv=None):

    args = parse_args(argv)

    if args.list:
        from serial.tools import list_ports
        for port in list_ports.comports():
            vid = f"{port.vid:04X}" if port.vid is not None else "----"
            print(f"{port.device}  VID={vid}  {port.description}")
        return 0

    _start_log()
    _certificates()
    print(f"Mochi {__version__} ({paths.APP_DIR})")

    from mochi_app import kde
    style_name = kde.prepare()
    print(f"Estilo: {style_name}")

    from PySide6.QtCore import QProcess, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from mochi_app.controller import Controller
    from mochi_app.ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    # Qt pone el locale del sistema (es_AR: coma decimal) y Vosk escribe su
    # JSON con printf: "conf" : 1,000000 deja de ser JSON y se cae la sesión
    # USB. Los números quedan en C, como con la ventana Tk.
    locale.setlocale(locale.LC_NUMERIC, "C")
    app.setApplicationName("Mochi")
    app.setOrganizationName("Mochi")
    app.setApplicationVersion(__version__)
    # En Wayland, KDE asocia la ventana con mochi.desktop (ícono, menú).
    app.setDesktopFileName("mochi")
    app.setWindowIcon(QIcon(str(paths.ICON)))

    controller = Controller(args)
    restart_requested = []

    def restart():
        restart_requested.append(True)
        window.close()

    window = MainWindow(controller, style_name, restart)
    window.show()
    controller.start()

    # Ctrl+C o kill: cerrar bien (se quita la salida «Mochi», se avisa a la placa).
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: window.close())
    signals = QTimer()
    signals.timeout.connect(lambda: None)    # deja correr los manejadores de Python
    signals.start(300)

    code = app.exec()
    controller.shutdown()

    if restart_requested:
        program, arguments = _restart_command()
        QProcess.startDetached(program, arguments)

    return code
