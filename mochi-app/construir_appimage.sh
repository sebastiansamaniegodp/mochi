#!/usr/bin/env bash
# =========================================================
# Arma dist/Mochi-x86_64.AppImage: un solo archivo con Python,
# Qt, las dependencias, el agente, los modelos de voz y la app.
#
#   ./construir_appimage.sh
#
# Usa build/ como caché (descargas y pip): la primera vez baja
# ~45 MB de GitHub (Python portable, appimagetool, runtime) y las
# dependencias de PyPI; después anda sin internet si no cambia
# requirements.txt.
# =========================================================

set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

ROOT="$PWD"
BUILD="$ROOT/build"
DOWNLOADS="$BUILD/descargas"
APPDIR="$BUILD/AppDir"
OUTPUT="$ROOT/dist/Mochi-x86_64.AppImage"

PYTHON_IMAGE="python3.13.16-cp313-cp313-manylinux_2_28_x86_64.AppImage"
PYTHON_URL="https://github.com/niess/python-appimage/releases/download/python3.13/$PYTHON_IMAGE"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"

PY="$APPDIR/opt/python3.13/bin/python3.13"
SITE="$APPDIR/opt/python3.13/lib/python3.13/site-packages"


# ---------------------------------------------------------
# Descargas (una sola vez)
# ---------------------------------------------------------

mkdir -p "$DOWNLOADS" "$ROOT/dist"

download() {
    if [ ! -s "$DOWNLOADS/$2" ]; then
        echo "Bajando $2…"
        curl -fSL --progress-bar -o "$DOWNLOADS/$2.part" "$1"
        mv "$DOWNLOADS/$2.part" "$DOWNLOADS/$2"
    fi
}

download "$PYTHON_URL" "$PYTHON_IMAGE"
download "$TOOL_URL" appimagetool-x86_64.AppImage
download "$RUNTIME_URL" runtime-x86_64
chmod +x "$DOWNLOADS/$PYTHON_IMAGE" "$DOWNLOADS/appimagetool-x86_64.AppImage"


# ---------------------------------------------------------
# Python portable
# ---------------------------------------------------------

echo "Preparando la carpeta del AppImage…"
rm -rf "$APPDIR" "$BUILD/squashfs-root"
(cd "$BUILD" && "$DOWNLOADS/$PYTHON_IMAGE" --appimage-extract > /dev/null)
mv "$BUILD/squashfs-root" "$APPDIR"

# Lo del AppImage de Python que no sirve (su arranque, ícono, Tk).
rm -f "$APPDIR"/AppRun "$APPDIR"/*.desktop "$APPDIR"/*.png "$APPDIR"/.DirIcon
rm -rf "$APPDIR/usr/share/tcltk" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo"


# ---------------------------------------------------------
# Dependencias
# ---------------------------------------------------------

echo "Instalando dependencias…"
"$PY" -m pip install --quiet --no-warn-script-location --disable-pip-version-check \
    --cache-dir "$BUILD/pip-cache" \
    -r "$ROOT/requirements.txt"

# PySide6: solo lo que usa una app de widgets (sin herramientas de
# desarrollo, QML, Designer ni traducciones que no sean en español).
# Las bibliotecas de Quick quedan: las necesita el tema de Plasma.
QT="$SITE/PySide6"
rm -rf "$QT"/{assistant,designer,linguist,lrelease,lupdate,qmlformat,qmllint,qmlls,svgtoqml} \
       "$QT"/{doc,glue,include,scripts,support,typesystems} \
       "$QT"/Qt/{libexec,metatypes,qml} \
       "$QT"/Qt/plugins/{designer,qmltooling,sqldrivers,wayland-graphics-integration-server} \
       "$QT"/Qt/plugins/imageformats/libqpdf.so
rm -f "$QT"/*.pyi \
      "$QT"/{QtDesigner,QtHelp,QtUiTools,QtTest,QtQuickTest,QtExampleIcons}.abi3.so \
      "$QT"/Qt/lib/libQt6{Designer,DesignerComponents,Help,UiTools,QmlCompiler,WaylandCompositor,QuickTest}.so.6 \
      "$QT"/Qt/lib/libQt6QuickControls2{Imagine,Material,Universal,FluentWinUI3}*.so.6 \
      "$QT"/Qt/lib/libQt6QuickParticles.so.6
find "$QT/Qt/translations" -type f ! -name "*_es.qm" -delete

# pip ya no hace falta adentro.
"$PY" -m pip uninstall --quiet --yes --disable-pip-version-check pip


# ---------------------------------------------------------
# Mochi
# ---------------------------------------------------------

echo "Copiando Mochi…"
mkdir -p "$APPDIR/opt/mochi"
rsync -a --exclude "__pycache__" "$ROOT/src" "$ROOT/models" "$APPDIR/opt/mochi/"
"$PY" -m compileall -q -j 0 "$APPDIR/opt/mochi/src"

# Python encuentra src/ por un .pth (relativo a site-packages), así
# AppRun no exporta PYTHONPATH a los programas que abre Mochi.
echo "../../../../mochi/src" > "$SITE/mochi.pth"

cp "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp "$ROOT/packaging/mochi.desktop" "$APPDIR/mochi.desktop"
cp "$ROOT/src/mochi_app/resources/mochi.svg" "$APPDIR/mochi.svg"

# Ícono PNG para .DirIcon (lo dibuja Qt desde el SVG).
QT_QPA_PLATFORM=offscreen "$PY" -s -E - "$APPDIR/mochi.svg" "$APPDIR/.DirIcon" <<'EOF'
import sys
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import Qt
app = QGuiApplication([])
image = QImage(256, 256, QImage.Format_ARGB32)
image.fill(Qt.transparent)
painter = QPainter(image)
QSvgRenderer(sys.argv[1]).render(painter)
painter.end()
image.save(sys.argv[2], "PNG")
EOF


# ---------------------------------------------------------
# Comprobar que no falte ninguna biblioteca
# ---------------------------------------------------------

# (Qt tiene que salir siempre de adentro: si sale del sistema es que
# se borró de más arriba.)
links=$(find "$APPDIR/opt" -name "*.so*" -type f -exec ldd {} + 2>/dev/null || true)
missing=$(grep "not found" <<< "$links" | sort -u || true)
host_qt=$(grep -E "libQt6[^ ]* => /usr" <<< "$links" | sort -u || true)
if [ -n "$missing" ]; then
    echo "AVISO: bibliotecas que no están ni en el AppImage ni en el sistema:" >&2
    echo "$missing" >&2
fi
if [ -n "$host_qt" ]; then
    echo "ERROR: partes de Qt que se toman del sistema:" >&2
    echo "$host_qt" >&2
    exit 1
fi


# ---------------------------------------------------------
# AppImage
# ---------------------------------------------------------

echo "Armando el AppImage…"
rm -f "$OUTPUT"
ARCH=x86_64 "$DOWNLOADS/appimagetool-x86_64.AppImage" --appimage-extract-and-run \
    --runtime-file "$DOWNLOADS/runtime-x86_64" \
    --comp zstd \
    --no-appstream \
    "$APPDIR" "$OUTPUT" > "$BUILD/appimagetool.log" 2>&1 \
    || { cat "$BUILD/appimagetool.log" >&2; exit 1; }

echo "Listo: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
