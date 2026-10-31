#!/usr/bin/env bash
# Corre los tests con un HOME temporal (no toca ~/.mochi ni ~/.config).
# El agente necesita una copia del config.json real: vacío, el Brain
# queda desactivado y los tests fallan al importar.
#   ./probar.sh                     todos
#   ./probar.sh tests.test_pc_audio uno
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

home=$(mktemp -d)
trap 'rm -rf "$home"' EXIT
mkdir -p "$home/.mochi"
[ -f "$HOME/.mochi/config.json" ] && cp "$HOME/.mochi/config.json" "$home/.mochi/"

if [ $# -gt 0 ]; then
    HOME="$home" .venv/bin/python -m unittest "$@"
else
    HOME="$home" .venv/bin/python -m unittest discover -s tests -t .
fi
