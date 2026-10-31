#!/usr/bin/env bash
# =========================================================
# Hace que Chromium / Google Chrome se abran SIEMPRE con
# --force-renderer-accessibility, también desde el menú de KDE.
#
# Sin ese flag Chromium no publica el contenido de las páginas
# por accesibilidad (AT-SPI) y web_inspect / web_act de Mochi
# devuelven "web_content_unavailable". Mochi ya lo agrega cuando
# abre el navegador, pero si el navegador ya estaba abierto desde
# el menú, las pestañas nuevas usan el proceso viejo (sin flag).
#
# Copia el lanzador del sistema a ~/.local/share/applications/
# (solo para este usuario, sin sudo) con el flag agregado.
# Para deshacerlo: borrar esa copia.
#
# Uso: bash linux/chromium-con-accesibilidad.sh
# Después cerrar el navegador del todo y volver a abrirlo.
# =========================================================

set -euo pipefail

FLAG="--force-renderer-accessibility"
DEST="$HOME/.local/share/applications"

mkdir -p "$DEST"

found=0

for name in chromium.desktop google-chrome.desktop; do

    source="/usr/share/applications/$name"
    [ -f "$source" ] || continue
    found=1

    target="$DEST/$name"

    if [ -f "$target" ] && grep -q -- "$FLAG" "$target"; then
        echo "$name ya tenía el flag."
        continue
    fi

    # Agrega el flag después del ejecutable en cada línea Exec=.
    sed -E "s|^(Exec=[^ ]+)|\1 $FLAG|" "$source" > "$target"
    echo "Listo: $target"
done

if [ "$found" = 0 ]; then
    echo "No encontré chromium.desktop ni google-chrome.desktop en /usr/share/applications." >&2
    exit 1
fi

command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

echo "Cerrá el navegador del todo (todas las ventanas) y abrilo de nuevo."
