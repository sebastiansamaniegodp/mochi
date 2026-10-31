"""
Carpetas del usuario en Linux según xdg-user-dirs (en un sistema
en español son Escritorio, Documentos, Descargas, etc.).
"""

import subprocess
from pathlib import Path


XDG_FOLDER_KEYS = {
    "desktop": "DESKTOP",
    "documents": "DOCUMENTS",
    "downloads": "DOWNLOAD",
    "pictures": "PICTURES",
    "music": "MUSIC",
    "videos": "VIDEOS",
}


def read_xdg_folder(name):

    key = XDG_FOLDER_KEYS.get(name)

    if key is None:
        return None

    try:
        result = subprocess.run(
            ["xdg-user-dir", key],
            capture_output=True,
            text=True,
            timeout=5,
        )

    except (OSError, subprocess.SubprocessError):
        return None

    value = result.stdout.strip()

    if not value:
        return None

    path = Path(value)

    # Sin configurar, xdg-user-dir devuelve el HOME: no sirve.
    if path == Path.home() or not path.exists():
        return None

    return path
