"""Mochi en el menú de aplicaciones de KDE (solo para el AppImage)."""

import shutil
from pathlib import Path

from mochi_app import paths


APPLICATIONS = Path.home() / ".local" / "share" / "applications"
ICONS = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"

ENTRY = """[Desktop Entry]
Type=Application
Name=Mochi
GenericName=Asistente por voz
Comment=Hablale a Mochi: el robot ESP32 con el LLM de la PC
Exec="{appimage}" %U
Icon=mochi
Terminal=false
Categories=Utility;AudioVideo;
Keywords=mochi;voz;llm;esp32;robot;
StartupWMClass=mochi
"""


def install(appimage):
    """Crea el acceso en el menú (y su ícono). Devuelve la ruta del .desktop."""

    APPLICATIONS.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(paths.ICON, ICONS / "mochi.svg")

    entry = APPLICATIONS / "mochi.desktop"
    entry.write_text(ENTRY.format(appimage=appimage), encoding="utf-8")
    return entry
