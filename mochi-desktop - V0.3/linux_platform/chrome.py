"""
Chrome / Chromium en Linux: ejecutable y carpeta de perfiles.
Se prefiere Google Chrome; si no está, Chromium.
"""

import shutil
from pathlib import Path


# (ejecutable, carpeta de perfiles dentro de ~/.config)
BROWSERS = [
    ("google-chrome-stable", "google-chrome"),
    ("google-chrome", "google-chrome"),
    ("chromium", "chromium"),
    ("chromium-browser", "chromium"),
]


def _installed():

    for executable, config_name in BROWSERS:
        path = shutil.which(executable)
        if path:
            return path, Path.home() / ".config" / config_name

    return None, None


def find_chrome_executable():

    return _installed()[0]


def get_chrome_user_data_dir():

    user_data_dir = _installed()[1]

    if user_data_dir is not None and user_data_dir.exists():
        return user_data_dir

    return None
