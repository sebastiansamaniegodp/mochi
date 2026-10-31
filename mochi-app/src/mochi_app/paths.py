"""Dónde está cada cosa, tanto desde el código como dentro del AppImage.

    mochi-app/                (en el AppImage: $APPDIR/opt/mochi/)
    ├── models/               voz a texto (Vosk) y voz de Mochi (Piper)
    └── src/
        ├── mochi_app/        esta app
        └── agente/           el agente de Mochi Desktop (LLM + herramientas)
"""

import os
import sys
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
APP_DIR = SRC_DIR.parent

AGENT_DIR = SRC_DIR / "agente"
MODELS_DIR = APP_DIR / "models"
RESOURCES_DIR = PACKAGE_DIR / "resources"

VOSK_MODEL = MODELS_DIR / "vosk-model-small-es-0.42"
PIPER_VOICE = MODELS_DIR / "es_AR-daniela-high.onnx"
ICON = RESOURCES_DIR / "mochi.svg"
UDEV_RULE = RESOURCES_DIR / "99-mochi-esp32.rules"

# Ruta del .AppImage cuando corre empaquetada (la pone su runtime).
APPIMAGE = os.environ.get("APPIMAGE")

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "mochi"
LOG_FILE = CACHE_DIR / "mochi.log"


def enable_agent_imports():
    """El agente importa sus módulos como de primer nivel (brain, actions...)."""

    agent = str(AGENT_DIR)
    if agent not in sys.path:
        sys.path.insert(0, agent)
