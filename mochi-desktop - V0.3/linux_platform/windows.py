"""
Descubrimiento de ventanas en Linux (KWin). Devuelve la misma
forma de diccionario que discovery/windows.py en Windows.
"""

import psutil

from linux_platform import kwin


def discover_windows(normalize_window_name):

    windows = []

    try:
        kwin_windows = kwin.list_windows()

    except kwin.KWinError as error:
        print("No pude listar las ventanas de KWin:", error)
        return windows


    for item in kwin_windows:

        title = str(item.get("title") or "").strip()

        # Igual que en Windows: sin título no se puede nombrar.
        if not title:
            continue


        pid = int(item.get("pid") or 0)

        process_name = ""
        executable = ""

        try:
            process = psutil.Process(pid)
            process_name = process.name().strip()

            try:
                executable = process.exe()
            except Exception:
                executable = ""

        except Exception:
            pass


        # Si el proceso no tiene un nombre útil (flatpak, wrappers),
        # se usa la clase de la ventana (firefox, org.kde.dolphin...).
        if not process_name:
            process_name = str(item.get("resource_class") or "")


        windows.append({

            "hwnd":
                item["id"],

            "title":
                title,

            "normalized_title":
                normalize_window_name(title),

            "pid":
                pid,

            "process":
                process_name,

            "normalized_process":
                normalize_window_name(process_name),

            "executable":
                executable,

            "window_class":
                str(item.get("resource_class") or ""),

            "minimized":
                bool(item.get("minimized")),

            "active":
                bool(item.get("active")),
        })


    return windows
