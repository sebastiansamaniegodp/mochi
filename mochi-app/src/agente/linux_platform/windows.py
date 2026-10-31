"""
Descubrimiento de ventanas en Linux (KWin). Devuelve la misma
forma de diccionario que discovery/windows.py en Windows.
"""

import configparser
import functools
import os
from pathlib import Path

import psutil

from linux_platform import kwin


# =========================================================
# DATOS DEL .desktop DE CADA APLICACIÓN
# =========================================================
#
# KWin dice qué .desktop abrió cada ventana (org.kde.dolphin).
# Ahí está cómo la llama el sistema: "Gestor de archivos",
# "explorador", "navegador web"... Con eso Mochi entiende
# "cerrá el explorador de archivos" sin una tabla propia.

def _application_dirs():

    data_home = os.environ.get("XDG_DATA_HOME") or str(
        Path.home() / ".local" / "share"
    )
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"

    dirs = [data_home] + data_dirs.split(":") + [
        str(Path.home() / ".local/share/flatpak/exports/share"),
        "/var/lib/flatpak/exports/share",
    ]
    return [Path(d) / "applications" for d in dirs if d]


def _localized(section, key):

    for suffix in ("[es_AR]", "[es]", ""):
        value = section.get(key + suffix)
        if value:
            return value.strip()
    return ""


@functools.lru_cache(maxsize=128)
def desktop_entry_info(desktop_file):
    """Nombre, nombre genérico y palabras clave de un .desktop."""

    name = str(desktop_file or "").strip()
    if not name:
        return {}
    if not name.endswith(".desktop"):
        name += ".desktop"

    for directory in _application_dirs():
        path = directory / name
        if not path.is_file():
            continue

        parser = configparser.RawConfigParser(strict=False, interpolation=None)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
            section = parser["Desktop Entry"]
        except Exception:
            return {}

        keywords = [
            word.strip()
            for word in (
                _localized(section, "Keywords").split(";")
                + section.get("Keywords", "").split(";")
            )
            if word.strip()
        ]
        return {
            "app_name": _localized(section, "Name"),
            "generic_name": _localized(section, "GenericName"),
            "keywords": list(dict.fromkeys(keywords)),
        }

    return {}


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


        entry = desktop_entry_info(item.get("desktop_file"))

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

            # Cómo llama el sistema a la aplicación (del .desktop).
            "app_name":
                entry.get("app_name", ""),

            "generic_name":
                entry.get("generic_name", ""),

            "keywords":
                entry.get("keywords", []),
        })


    return windows
