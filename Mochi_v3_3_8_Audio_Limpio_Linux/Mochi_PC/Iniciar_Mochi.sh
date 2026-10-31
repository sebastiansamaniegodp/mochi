#!/usr/bin/env bash
# Lanzador de Mochi_PC para Linux (equivale a Iniciar_Mochi.bat).
# La primera vez crea .venv/ e instala las dependencias (requiere Internet).
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  if ! python3 -c "import sys; assert sys.version_info >= (3,10)" 2>/dev/null; then
    echo "Hace falta Python 3.10 o superior (paquete python3)."
    exit 1
  fi
  if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "Falta Tkinter. Instalalo con:  sudo apt install python3-tk"
    exit 1
  fi
  if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
    echo "Falta venv. Instalalo con:  sudo apt install python3-venv"
    exit 1
  fi
  python3 -m venv .venv || exit 1
fi

# "mochi" es un argumento de relleno: SoundCard lee sys.argv[1] al importarse
# con "python -c" y, sin el, la comprobacion fallaba siempre.
if ! .venv/bin/python -c "import vosk,serial,tkinter,numpy,soundcard" mochi 2>/dev/null; then
  echo "Preparando Mochi. La primera vez requiere Internet..."
  .venv/bin/python -m pip install -r requirements.txt || {
    echo "Mochi no pudo instalar las dependencias. Revisa el error de arriba."
    exit 1
  }
fi

exec .venv/bin/python app.py "$@"
