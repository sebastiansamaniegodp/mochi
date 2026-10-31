#!/usr/bin/env bash
# =========================================================
# Arma para_pc/ y para_pc.zip con lo que se trabajó en la
# notebook, para subirlo a Drive y llevarlo a la PC.
#
# Es el camino inverso de armar_notebook.sh (que corre en la PC).
# No editar para_pc/ a mano: se borra y se rearma cada vez.
# =========================================================

set -euo pipefail

cd "$(dirname "$0")"

OUT="para_pc"
MOCHI="mochi-desktop - V0.3"

rm -rf "$OUT" "$OUT.zip"
mkdir -p "$OUT/$MOCHI"


# ---------------------------------------------------------
# mochi-desktop: archivos nuevos o cambiados respecto de la
# V0.3 original, más el patch (diff contra la V0.3).
# ---------------------------------------------------------

MOCHI_FILES=(
    actions/apps.py
    actions/browser.py
    application_registry.py
    brain/agent_loop.py
    brain/browser_tools.py
    brain/brain_factory.py
    brain/brain_router.py
    brain/llama_cpp_provider.py
    chrome_profiles.py
    config_manager.py
    desktop_tools.py
    discovery/applications.py
    discovery/games.py
    discovery/user_folders.py
    discovery/windows.py
    file_manager.py
    game_registry.py
    hardware_profiler.py
    media_controller.py
    mochi_command_service.py
    window_manager.py
    requirements-linux.txt
    requirements-voice.txt
    Iniciar_Mochi_LLM.sh
    tests/test_phase1_capabilities.py
    tests/test_voice_and_cache.py
    tests/test_linux_web.py
    cambios-mochi-desktop.patch
)

for file in "${MOCHI_FILES[@]}"; do
    mkdir -p "$OUT/$MOCHI/$(dirname "$file")"
    cp "$MOCHI/$file" "$OUT/$MOCHI/$file"
done

# Paquetes nuevos completos. Los modelos de voz (~170 MB) no
# viajan: la PC ya los tiene.
for package in linux_platform voice_bridge; do
    rsync -a \
        --exclude "__pycache__" \
        --exclude "models" \
        "$MOCHI/$package" "$OUT/$MOCHI/"
done


# ---------------------------------------------------------
# Firmware, archivos de Linux y documentación
# ---------------------------------------------------------

rsync -a --exclude ".pio" --exclude "build" firmware "$OUT/"
cp -r linux "$OUT/"
cp README.md CLAUDE.md armar_para_pc.sh "$OUT/"
[ -f HANDOFF.md ] && cp HANDOFF.md "$OUT/"


# ---------------------------------------------------------
# Nunca: la clave de la API, el config de Mochi, perfiles de
# Chrome. Se verifica por las dudas.
# ---------------------------------------------------------

if grep -rqsF "$(cat llm-server/api_key.txt 2>/dev/null || echo NO-KEY)" "$OUT"; then
    echo "ERROR: la clave de la API quedó dentro de $OUT" >&2
    exit 1
fi

zip -qr "$OUT.zip" "$OUT"

echo "Listo: $OUT.zip ($(du -h "$OUT.zip" | cut -f1)). Subirlo a Drive."
