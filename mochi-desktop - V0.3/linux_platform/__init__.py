"""
Soporte de Linux (KDE Plasma / Wayland) para Mochi Desktop.

En Windows no se usa nada de este paquete: cada módulo de Mochi
sigue con su implementación original (win32, UIA, PowerShell).
En Linux, esos módulos importan de acá el reemplazo equivalente.

- kwin.py          ventanas (scripts de KWin por D-Bus)
- win32_compat.py  lo poco de win32gui/win32con que usa Mochi
- media.py         multimedia (MPRIS) y volumen (wpctl)
- desktop.py       portapapeles, captura y notificaciones
- applications.py  aplicaciones instaladas (.desktop)
- folders.py       carpetas del usuario (xdg-user-dir)
- atspi.py         accesibilidad (AT-SPI) por D-Bus
- web.py           web_inspect / web_act sobre Chromium/Chrome
- keyboard.py      Enter/Espacio por el portal RemoteDesktop
"""

import os
import subprocess
import sys


IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


# =========================================================
# ABRIR CON LA APLICACIÓN PREDETERMINADA
# =========================================================

def system_open(target):
    """
    Equivalente de os.startfile: abre un archivo, carpeta
    o URI (spotify:, steam://...) con la aplicación del sistema.
    """

    if IS_WINDOWS:
        os.startfile(target)
        return

    subprocess.Popen(
        ["xdg-open", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
