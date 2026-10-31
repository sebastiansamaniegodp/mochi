#!/usr/bin/env bash
# Mochi con el LLM (Linux): abre la ventana de voz de Mochi conectada al
# ESP32 (firmware Mochi 3.5) y al LLM de la PC (llama-server en la PC, por la LAN o por Tailscale).
#
#   bash Iniciar_Mochi_LLM.sh            normal
#   bash Iniciar_Mochi_LLM.sh --echo     sin LLM: Mochi repite lo que oyo
#
# Usa el venv de la carpeta ROBOTICA (../.venv).
cd "$(dirname "$0")" || exit 1

PY="../.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No encuentro $PY (el venv del proyecto)."
  read -r -p "Enter para cerrar"
  exit 1
fi

"$PY" -m voice_bridge.esp32_bridge "$@"
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "Mochi se cerro con un error (codigo $status). Revisa los mensajes de arriba."
  read -r -p "Enter para cerrar"
fi
exit $status
