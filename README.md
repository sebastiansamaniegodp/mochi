# Mochi

Asistente de escritorio por voz con un ESP32-S3 como "cuerpo": pantalla ST7789 (cara),
micrófono INMP441 y parlante con MAX98357A. La voz se procesa en la notebook y un LLM
local (Qwen3.5-9B en otra PC) decide qué herramienta usar.

```
voz ─► ESP32-S3 ─USB─► notebook (Vosk/Whisper) ─► agente de Mochi ─HTTP─► PC: llama-server + Qwen3.5-9B
parlante + cara ◄─ ESP32-S3 ◄─USB─ notebook (Piper) ◄─ ejecuta la herramienta ◄─ tool call
```

| Carpeta | Qué es |
|---|---|
| `mochi-app/` | **App de Mochi para Linux** (Qt, AppImage), con o sin ESP32. Ver [`mochi-app/README.md`](mochi-app/README.md) |
| `mochi-desktop - V0.3/` | Mochi Desktop (Windows y Linux KDE) + `voice_bridge/`, la ventana Tk que conecta el ESP32 con el LLM |
| `firmware/Mochi/` | Firmware Mochi 3.5 (Arduino IDE), con los `.bin` listos en `binarios/` |
| `Mochi_v3_3_8_Audio_Limpio*/` | El Mochi limpio original (firmware de prueba + `Mochi_PC`), para Windows y Linux |
| `linux/` | Regla udev del ESP32 y lanzador de Chromium con accesibilidad |

## LLM (PC con RX 580)

`llama-server` (llama.cpp con Vulkan) con Qwen3.5-9B Q4_K_M en el puerto 8080, con `--jinja`
y `--reasoning off`. El script de arranque y la clave de la API están solo en esa PC; no
están en el repositorio.

En la RX 580 lee el prompt a ~210 tokens/s y genera ~12 tokens/s. El prompt de Mochi
(sistema + 30 tools) tiene ~4.850 tokens: sin caché tarda ~80 s, con caché ~2,7 s por paso.
Por eso Mochi precalienta la caché al arrancar, y el comienzo del prompt (sistema + tools)
no puede cambiar entre órdenes. Si se reinicia `llama-server`, hay que reiniciar Mochi.

Configuración en `~/.mochi/config.json`, sección `brain` (en `mochi-app`, desde Configuración):

```json
"brain": {
  "enabled": true,
  "runtime": "llama_cpp",
  "model": "qwen3.5-9b",
  "base_url": "http://<IP-de-la-PC>:8080",
  "api_key": "<clave de la API>",
  "thinking_enabled": false,
  "context_size": 16384
}
```

Fuera de casa se puede usar la IP de la PC en Tailscale.

## Notebook

**Linux:** usar `mochi-app/` (instalación y uso en su README). Escucha siempre la palabra
«Mochi»: si sigue una orden fija («Mochi, duerme») la hace; si no, lo que sigue va al LLM.

**Windows / ventana Tk:** desde `mochi-desktop - V0.3/` (en Windows, `python` en lugar de
`../.venv/bin/python`, e instalar solo `requirements-voice.txt` además de lo de siempre):

```bash
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements-linux.txt -r requirements-voice.txt
../.venv/bin/python -m voice_bridge.esp32_bridge                 # autodetecta el ESP32
../.venv/bin/python -m voice_bridge.esp32_bridge --stt whisper   # Whisper en vez de Vosk
../.venv/bin/python -m voice_bridge.simulador "cuál es el volumen"  # sin placa (solo Linux)
../.venv/bin/python -m brain.llama_cpp_provider                  # prueba la conexión con el LLM
```

En la ventana Tk el LLM se usa con el botón **🎤 Hablarle a Mochi**, el cuadro de texto o
manteniendo BOOT en la placa; por voz solo responden las órdenes fijas. `python main.py`
sigue sirviendo la API HTTP de Mochi Desktop (puerto 8765, para Android).

En Linux, para el ESP32 (una vez; después reconectar el cable):

```bash
sudo cp linux/99-mochi-esp32.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger
```

Da acceso al puerto sin `sudo` y evita que ModemManager le mande comandos AT al ESP32.

**Modelos de voz** (no están en el repositorio, ~170 MB): van en `mochi-app/models/` y en
`mochi-desktop - V0.3/voice_bridge/models/`.

- `vosk-model-small-es-0.42` (voz a texto): https://alphacephei.com/vosk/models
- `es_AR-daniela-high.onnx` + `.onnx.json` (voz de Mochi, Piper): https://huggingface.co/rhasspy/piper-voices (`es/es_AR/daniela/high/`)

### Linux (KDE Plasma, Wayland)

Lo que en Windows usa win32/UIA/PowerShell, en Linux lo hace `linux_platform/`: ventanas
por scripts de KWin (D-Bus), multimedia por MPRIS, volumen con `wpctl`, portapapeles con
`wl-clipboard`, capturas con Spectacle y aplicaciones desde los `.desktop`.

Las páginas web (`web_inspect`, `web_act`) se leen por accesibilidad (AT-SPI) en el
Chromium/Chrome del usuario, que tiene que abrirse con `--force-renderer-accessibility`:
`bash linux/chromium-con-accesibilidad.sh` instala un lanzador con el flag. Para escribir,
Mochi pega desde el menú contextual; Enter y Espacio van por el portal RemoteDesktop (KDE
pide permiso la primera vez).

## ESP32-S3 (`firmware/Mochi/`)

Firmware **Mochi 3.5**: el Mochi limpio v3.3.8 (cara animada, órdenes fijas, grabar y
reproducir, Parlante PC) más el modo LLM. Sirve para `Mochi_PC`, la ventana Tk y `mochi-app`.

- **Subir sin compilar** (~30 s): ver `firmware/Mochi/binarios/LEEME.txt` (flasheador web
  en Chrome/Edge o `subir_firmware.sh`).
- **Arduino IDE:** core esp32 de Espressif 3.x (probado 3.3.12), ESP32S3 Dev Module,
  USB CDC On Boot: Enabled, USB Mode: Hardware CDC and JTAG, PSRAM: QSPI PSRAM. Bibliotecas
  Adafruit GFX, Adafruit ST7735 and ST7789 y Adafruit BusIO. Si se cambia el código,
  regenerar los `.bin` (Sketch → Exportar binario compilado).
- El cable va al puerto **USB** nativo, no al que dice UART.

| Módulo | Pin del módulo | ESP32-S3 |
|---|---|---|
| **ST7789** 240×280 | VCC y BLK / GND | 3V3 / GND |
| | SCL / SDA | GPIO12 / GPIO11 |
| | RES / DC / CS | GPIO8 / GPIO9 / GPIO10 |
| **INMP441** | VDD / GND y L/R | 3V3 / GND |
| | SCK / WS / SD | GPIO4 / GPIO5 / GPIO6 |
| **MAX98357A** | VIN / GND | 5V / GND |
| | BCLK / LRC / DIN | GPIO16 / GPIO17 / GPIO18 |
| | SD/EN | 3V3 (canal izquierdo); 2.º amplificador estéreo: divisor 22k/10k (~1 V) |
| **Botón** | | BOOT de la placa (GPIO0), opcional |

Con el LLM la cara muestra el estado: escuchando (curiosa, parpadea), pensando (curiosa,
con lo que entendió), hablando (feliz, la boca sigue la voz) y listo/error (feliz/sorprendida
3 s, después vuelve a las animaciones de siempre).

### Protocolo serie

El del Mochi limpio v3.3.8 (el `Mochi_PC` viejo sigue funcionando) más el modo LLM:

- ESP32 → PC: paquetes `MCHI` con CRC32 (micrófono, HELLO, STATUS, TEXT). El HELLO trae
  `"llm":1` y BOOT manda STATUS `{"button":"down"}` / `{"button":"up"}`.
- PC → ESP32: COBS(`A5 5A | tipo | largo | datos | CRC16`) + `00`. Órdenes `C` de una letra,
  audio `A`/`B`/`S`; orden `0`..`5` = estado del LLM y paquete `T` = texto (ASCII). La voz de
  Piper viaja como el Parlante PC (`K`, paquetes `B`, `J`).

Tiene que coincidir entre `firmware/Mochi/` (`MochiFrame.h`, `MochiPCLink.h`, `handleCommand`
de `Mochi.ino`) y `voice_bridge/mochi_pc/` (`protocol.py`, `frame_packet` de `pc_speaker.py`).

## Pendiente

- Elegir entre Vosk y Whisper con el micrófono real. Con voz sintética, Vosk acertó 2 de 12
  órdenes (0,4 s) y Whisper small 7 de 12 (2,2 s).
