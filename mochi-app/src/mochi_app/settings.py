"""Preferencias de Mochi.

- Las de la app (volumen, micrófono, conectar sola...) van en QSettings:
  ~/.config/Mochi/Mochi.conf.
- Las del LLM van en ~/.mochi/config.json (sección "brain"), que es donde
  las lee el agente.
"""

import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QSettings


AGENT_CONFIG = Path.home() / ".mochi" / "config.json"

DEFAULT_LLM = {
    "base_url": "http://localhost:8080",
    "model": "qwen3.5-9b",
    "api_key": "",
}

APP_DEFAULTS = {
    "autoconnect": True,
    "wake_word": True,          # micrófono siempre abierto esperando «Mochi»
    "speak_replies": True,
    "voice_on_pc": False,       # con la placa, la voz sale por la PC y no por Mochi
    "stt": "vosk",
    "microphone": "",
    "speaker": "",
    "volume": 70,
    "mic_sensitivity": "normal",   # alta (como antes), normal o baja
    "profile": "Protegido (limpio)",
}


# =========================================================
# APP
# =========================================================

def app_settings():

    return QSettings("Mochi", "Mochi")


def get(key, default=None):

    default = APP_DEFAULTS.get(key, default)
    value = app_settings().value(key, default)

    # QSettings guarda todo como texto en el .conf.
    if isinstance(default, bool):
        return value in (True, "true", "1", 1)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return value


def put(key, value):

    app_settings().setValue(key, value)


# =========================================================
# LLM
# =========================================================

def load_llm():

    try:
        config = json.loads(AGENT_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}

    brain = config.get("brain") or {}

    return {
        "base_url": brain.get("base_url") or DEFAULT_LLM["base_url"],
        "model": brain.get("model") or DEFAULT_LLM["model"],
        "api_key": brain.get("api_key") or "",
        "enabled": bool(brain.get("enabled")),
    }


def save_llm(base_url, model, api_key):
    """Guarda el servidor del LLM sin tocar el resto de la configuración."""

    try:
        config = json.loads(AGENT_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}

    brain = config.setdefault("brain", {})
    brain.update({
        "enabled": True,
        "runtime": "llama_cpp",
        "model": model,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key or None,
        "thinking_enabled": False,
    })
    brain.setdefault("context_size", 16384)

    AGENT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=AGENT_CONFIG.parent)

    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=4, ensure_ascii=False)

    # Tiene la clave: solo para el usuario.
    os.chmod(temporary, 0o600)
    os.replace(temporary, AGENT_CONFIG)


def test_llm(base_url, api_key, timeout=6):
    """Pregunta los modelos a llama-server. Devuelve (ok, texto)."""

    request = urllib.request.Request(base_url.rstrip("/") + "/v1/models")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 401:
            return False, "La clave no es correcta."
        return False, f"El servidor respondió {error.code}."
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        return False, f"No responde ({reason}). ¿Está prendido llama-server?"
    except ValueError:
        return False, "Respondió algo que no es llama-server."

    names = [model.get("id") for model in data.get("data", []) if model.get("id")]
    return True, "Conectado" + (f": {', '.join(names)}" if names else "")
