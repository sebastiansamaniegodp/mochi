"""
Lo poco de win32gui / win32con que usa window_manager.py,
implementado sobre KWin. Así window_manager.py queda igual en
Windows y en Linux: solo cambia de dónde importa.

En Linux el "hwnd" de una ventana es el internalId de KWin
(un UUID en texto).
"""

from linux_platform import kwin


# =========================================================
# CONSTANTES (win32con)
# =========================================================

SW_SHOW = 5
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9

WM_CLOSE = 0x0010


_SHOW_ACTIONS = {
    SW_SHOW: "restore",
    SW_MINIMIZE: "minimize",
    SW_MAXIMIZE: "maximize",
    SW_RESTORE: "restore",
}


# =========================================================
# FUNCIONES (win32gui)
# =========================================================

def _check(found, hwnd):

    if not found:
        raise kwin.KWinError(f"La ventana {hwnd} ya no existe")


def IsIconic(hwnd):

    return kwin.is_minimized(hwnd)


def ShowWindow(hwnd, command):

    action = _SHOW_ACTIONS.get(command)

    if action is None:
        raise ValueError(f"ShowWindow: comando no soportado ({command})")

    _check(kwin.window_action(hwnd, action), hwnd)


def SetForegroundWindow(hwnd):

    _check(kwin.window_action(hwnd, "focus"), hwnd)


def GetForegroundWindow():

    return kwin.active_window_id()


def PostMessage(hwnd, message, _wparam=0, _lparam=0):

    if message != WM_CLOSE:
        raise ValueError(f"PostMessage: mensaje no soportado ({message})")

    _check(kwin.window_action(hwnd, "close"), hwnd)
