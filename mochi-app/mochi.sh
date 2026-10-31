#!/usr/bin/env bash
# Corre Mochi desde el código (sin armar el AppImage).
#   ./mochi.sh               normal
#   ./mochi.sh --echo        sin LLM
#   ./mochi.sh --help        el resto de las opciones
# La primera vez crea .venv/ con requirements.txt.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [ ! -x .venv/bin/python ]; then
    echo "Creando .venv (una sola vez)…"
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
fi
# Sin importar: soundcard falla con "python -c" (lee sys.argv).
if ! .venv/bin/python -c "import importlib.util as u, sys; sys.exit(any(u.find_spec(m) is None for m in ('PySide6', 'vosk', 'piper', 'serial', 'soundcard', 'jeepney')))"; then
    .venv/bin/pip install -r requirements.txt
fi

PYTHONPATH="$PWD/src" exec .venv/bin/python -m mochi_app "$@"
