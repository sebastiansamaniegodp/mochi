#!/usr/bin/env bash
# Sube el firmware Mochi 3.5 unificado (firmware/Mochi) ya compilado al ESP32-S3.
# No hace falta compilar ni tener instalado el core ESP32 del Arduino IDE.
#
# Uso:  bash subir_firmware.sh [puerto]      (por defecto /dev/ttyACM0)
#
# Compilado con esp32:esp32 3.3.12, ESP32S3 Dev Module:
#   USB Mode = Hardware CDC and JTAG, USB CDC On Boot = Enabled,
#   Upload Mode = UART0 / Hardware CDC, PSRAM = QSPI PSRAM,
#   Flash 4MB, particiones por defecto.
# Si se cambia el .ino, recompilar y regenerar estos .bin
# (Arduino IDE: Sketch > Exportar binario compilado).

set -euo pipefail
cd "$(dirname "$0")"

PUERTO="${1:-/dev/ttyACM0}"
BIN="Mochi.ino.merged.bin"

# ==== Buscar esptool ====
ESPTOOL=""
if command -v esptool >/dev/null 2>&1; then
    ESPTOOL="esptool"
elif command -v esptool.py >/dev/null 2>&1; then
    ESPTOOL="esptool.py"
else
    for f in "$HOME"/.arduino15/packages/esp32/tools/esptool_py/*/esptool; do
        [ -x "$f" ] && ESPTOOL="$f"
    done
fi
if [ -z "$ESPTOOL" ] && [ -f "$HOME/.platformio/packages/tool-esptoolpy/esptool.py" ]; then
    for py in "$HOME/.platformio/penv/bin/python" python3; do
        if command -v "$py" >/dev/null 2>&1; then
            ESPTOOL="$py $HOME/.platformio/packages/tool-esptoolpy/esptool.py"
            break
        fi
    done
fi
if [ -z "$ESPTOOL" ]; then
    echo "No se encontro esptool. Instalalo con: pip install esptool"
    exit 1
fi

# ==== Revisar puerto ====
if [ ! -e "$PUERTO" ]; then
    echo "No existe $PUERTO. Conecta el ESP32 por el USB nativo (USB/OTG)."
    ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true
    exit 1
fi
if command -v fuser >/dev/null 2>&1 && fuser "$PUERTO" >/dev/null 2>&1; then
    echo "El puerto $PUERTO esta ocupado (Monitor Serie, Mochi_PC...). Cerralo y volve a intentar."
    exit 1
fi

# ==== Subir ====
echo "Subiendo $BIN a $PUERTO con $ESPTOOL"
$ESPTOOL --chip esp32s3 --port "$PUERTO" --baud 921600 \
    --before default_reset --after hard_reset \
    write_flash -z 0x0 "$BIN"

echo
echo "Listo. Si la cara no arranca, apreta RESET una vez (sin BOOT)."
