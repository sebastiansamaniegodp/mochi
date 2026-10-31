"""Que Mochi se vea como el resto de KDE.

PySide6 trae su propio Qt. Si es la misma versión que el Qt del sistema, se
cargan los plugins de KDE del sistema: el estilo Breeze y el tema de Plasma
(colores, fuentes, íconos y diálogos de KDE). Si no coincide, Qt usa Fusion
con los colores, la fuente y los íconos de KDE (los lee solo de kdeglobals),
que también queda bien.

La primera vez (y cada vez que se actualiza KDE) los plugins se prueban en
otro proceso: si algo no es compatible, se cae ese proceso y no Mochi.

    MOCHI_ESTILO=fusion    no usar los plugins de KDE
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from mochi_app import paths


HOST_PLUGIN_DIRS = (
    "/usr/lib/x86_64-linux-gnu/qt6/plugins",     # Debian, Ubuntu
    "/usr/lib64/qt6/plugins",                    # Fedora
    "/usr/lib/qt6/plugins",                      # Arch
)

# Lo que se toma del sistema (el resto de los plugins son los de PySide6).
PLUGINS = (
    "styles/breeze6.so",
    "platformthemes/KDEPlasmaPlatformTheme6.so",
    "iconengines/KIconEnginePlugin.so",
    "kf6",
    "kiconthemes6",
)

PROBE_SECONDS = 20


def _host_plugins():

    for directory in HOST_PLUGIN_DIRS:
        if Path(directory, "styles/breeze6.so").is_file():
            return Path(directory)

    return None


def _host_qt_version(plugins):
    """Versión del Qt del sistema, por el nombre real de libQt6Core."""

    library = (plugins.parent.parent / "libQt6Core.so.6").resolve()
    match = re.search(r"libQt6Core\.so\.(\d+\.\d+\.\d+)$", library.name)
    return match.group(1) if match else None


def _link_plugins(host, target):

    for name in PLUGINS:
        source = host / name
        link = target / name

        if not source.exists():
            continue

        link.parent.mkdir(parents=True, exist_ok=True)

        if link.is_symlink() or link.exists():
            if link.is_symlink() and os.readlink(link) == str(source):
                continue
            link.unlink()

        link.symlink_to(source)


def _probe(target):

    env = dict(os.environ, PYTHONPATH=str(paths.SRC_DIR))

    try:
        result = subprocess.run(
            [sys.executable, "-m", "mochi_app.kde", str(target)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.returncode == 0


def prepare():
    """Antes de crear la QApplication. Devuelve qué estilo se va a usar."""

    from PySide6.QtCore import QCoreApplication, qVersion

    if os.environ.get("MOCHI_ESTILO", "").lower() == "fusion":
        return "Fusion (MOCHI_ESTILO)"

    host = _host_plugins()

    if host is None:
        return "Fusion (sin Breeze en el sistema)"

    host_version = _host_qt_version(host)

    if host_version != qVersion():
        return f"Fusion (Qt {qVersion()}, el sistema tiene {host_version})"

    target = paths.CACHE_DIR / f"qt-kde-{qVersion()}"
    _link_plugins(host, target)

    # Se vuelve a probar si cambian los plugins (actualización de KDE).
    key = " ".join(
        str(int((host / name).stat().st_mtime))
        for name in PLUGINS[:2]
    )
    stamp = target / "probado"

    try:
        known = stamp.read_text().split("\n")
    except OSError:
        known = []

    if len(known) == 2 and known[0] == key:
        works = known[1] == "ok"
    else:
        works = _probe(target)
        stamp.write_text(f"{key}\n{'ok' if works else 'falla'}")

    if not works:
        return "Fusion (los plugins de KDE no andan con este Qt)"

    QCoreApplication.addLibraryPath(str(target))
    return "Breeze (KDE)"


# =========================================================
# PRUEBA (en otro proceso): python -m mochi_app.kde <carpeta>
# =========================================================

def _probe_main(target):

    from PySide6.QtCore import QCoreApplication, QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

    QCoreApplication.addLibraryPath(target)
    app = QApplication(["mochi-kde-probe"])

    widget = QPushButton("Mochi")
    widget.ensurePolished()
    QFileDialog(widget)

    QTimer.singleShot(200, app.quit)
    app.exec()

    return 0 if app.style() is not None else 1


if __name__ == "__main__":
    sys.exit(_probe_main(sys.argv[1]))
