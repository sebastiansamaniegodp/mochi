"""Utilidades locales de escritorio para Mochi.

No se ejecutan al importar el módulo. Leer el portapapeles o capturar la
pantalla requiere siempre una llamada explícita a su herramienta.
"""

import ctypes
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from mochi_environment import environment

if os.name != "nt":
    from linux_platform import desktop as linux_desktop


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
MAX_CLIPBOARD_CHARS = 1_000_000


class DesktopTools:
    @staticmethod
    def _available():
        return os.name == "nt" and hasattr(ctypes, "windll")

    @staticmethod
    def _clipboard_apis():
        """Configure pointer-sized Win32 signatures before using handles."""

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = ctypes.c_bool
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = ctypes.c_bool
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = ctypes.c_bool
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = ctypes.c_bool
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p
        return user32, kernel32

    @staticmethod
    def _powershell(script, timeout=15):
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return False, str(error)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "PowerShell no pudo completar la operación.").strip()
        return True, ""

    def read_clipboard(self):
        if os.name != "nt":
            return linux_desktop.read_clipboard()

        if not self._available():
            return False, "El portapapeles nativo solo está disponible en Windows."

        user32, kernel32 = self._clipboard_apis()
        if not user32.OpenClipboard(None):
            return False, "No pude acceder al portapapeles en este momento."

        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return False, "El portapapeles no contiene texto Unicode."
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return False, "No pude leer el contenido del portapapeles."
            try:
                return True, ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        except Exception as error:
            return False, f"No pude leer el portapapeles: {error}"
        finally:
            user32.CloseClipboard()

    def write_clipboard(self, text):
        if os.name == "nt" and not self._available():
            return False, "El portapapeles nativo solo está disponible en Windows."

        text = str(text or "")
        if not text:
            return False, "No se indicó texto para copiar."
        if len(text) > MAX_CLIPBOARD_CHARS:
            return False, "El texto es demasiado largo para copiarlo de forma segura."

        if os.name != "nt":
            return linux_desktop.write_clipboard(text)

        user32, kernel32 = self._clipboard_apis()
        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False, "No pude reservar memoria para el portapapeles."

        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False, "No pude preparar el texto para el portapapeles."
        try:
            ctypes.memmove(pointer, buffer, size)
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            return False, "No pude acceder al portapapeles en este momento."

        try:
            if not user32.EmptyClipboard():
                return False, "No pude reemplazar el contenido del portapapeles."
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                return False, "No pude copiar el texto al portapapeles."
            # After SetClipboardData succeeds, Windows owns the allocation.
            handle = None
            return True, "Texto copiado al portapapeles."
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

    @staticmethod
    def _safe_screenshot_path(name=None):
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "").strip()).strip("._")
        if not stem:
            stem = datetime.now().strftime("captura_%Y%m%d_%H%M%S")
        if not stem.lower().endswith(".png"):
            stem += ".png"
        return environment.local_data_dir / "screenshots" / stem

    @staticmethod
    def _ps_quote(value):
        return str(value).replace("'", "''")

    def capture_screenshot(self, name=None):
        path = self._safe_screenshot_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)

        if os.name != "nt":
            return linux_desktop.capture_screenshot(path)

        quoted_path = self._ps_quote(path)
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -AssemblyName System.Drawing; "
            "$bounds=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
            "$bitmap=New-Object System.Drawing.Bitmap($bounds.Width,$bounds.Height); "
            "$graphics=[System.Drawing.Graphics]::FromImage($bitmap); "
            "$graphics.CopyFromScreen($bounds.Left,$bounds.Top,0,0,$bitmap.Size); "
            f"$bitmap.Save('{quoted_path}',[System.Drawing.Imaging.ImageFormat]::Png); "
            "$graphics.Dispose(); $bitmap.Dispose()"
        )
        success, detail = self._powershell(script)
        if not success or not path.exists():
            return False, detail or "Windows no creó la captura de pantalla."
        return True, path

    def show_notification(self, title, message):
        title = str(title or "").strip()[:120]
        message = str(message or "").strip()[:500]
        if not title or not message:
            return False, "La notificación necesita título y mensaje."

        if os.name != "nt":
            return linux_desktop.show_notification(title, message)

        quoted_title = self._ps_quote(title)
        quoted_message = self._ps_quote(message)
        # NotifyIcon provides a non-blocking Windows notification without a
        # third-party package. Windows may suppress it due to Focus Assist;
        # a successful dispatch is not delivery evidence.
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -AssemblyName System.Drawing; "
            "$notification=New-Object System.Windows.Forms.NotifyIcon; "
            "$notification.Icon=[System.Drawing.SystemIcons]::Information; "
            f"$notification.BalloonTipTitle='{quoted_title}'; "
            f"$notification.BalloonTipText='{quoted_message}'; "
            "$notification.Visible=$true; $notification.ShowBalloonTip(5000); "
            "Start-Sleep -Milliseconds 1200; $notification.Dispose()"
        )
        success, detail = self._powershell(script, timeout=5)
        if not success:
            return False, detail or "No pude enviar la notificación."
        return True, "Orden de notificación enviada a Windows."


desktop_tools = DesktopTools()
