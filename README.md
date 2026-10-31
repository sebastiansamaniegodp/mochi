# Mochi

Asistente de escritorio por voz con un ESP32-S3 como "cuerpo".

```
voz ─► ESP32-S3 ─USB─► notebook (voice_bridge: Vosk o Whisper) ─► mochi-desktop (agent loop)
                                                            │  HTTP (red de casa)
                                                            ▼
                                     PC con RX 580: llama-server + Qwen3.5-9B
                                                            │  tool call (JSON)
          parlante ◄─ ESP32-S3 ◄─USB─ notebook (Piper) ◄─ ejecuta la herramienta
          pantalla: cara + texto
```

| Carpeta | Corre en | Qué es |
|---|---|---|
| `llm-server/` | PC (Fedora, RX 580) | llama.cpp con Vulkan + Qwen3.5-9B Q4_K_M, API compatible con OpenAI |
| `mochi-desktop - V0.3/` | notebook (Linux KDE o Windows) | Mochi Desktop + `voice_bridge/` (puente con el ESP32) |
| `firmware/Mochi/` | ESP32-S3 | firmware Mochi 3.5 (Arduino IDE): cara, voz, parlante, LLM. `.bin` listos en `binarios/` |
| `mochi-app/` | notebook Linux | **la app de Mochi para Linux** (Qt + AppImage, con o sin ESP32): ver `mochi-app/README.md` |
| `linux/` | notebook Linux | regla udev para el ESP32; `chromium-con-accesibilidad.sh` (para `web_inspect`) |

---

## 1. PC del LLM (`llm-server/`)

- `start.sh` levanta `llama-server` en `0.0.0.0:8080` con todas las capas en la GPU,
  `--jinja` (tools con la plantilla de Qwen) y **razonamiento apagado** (`--reasoning off`).
- La clave de la API está en `llm-server/api_key.txt`. La notebook la necesita.
- El firewall (zona FedoraWorkstation) ya deja pasar el 8080 en la red local. El router no lo expone a internet.

Arrancar a mano: `./llm-server/start.sh`

Arrancar solo al prender la PC: ver las instrucciones dentro de `llm-server/mochi-llm.service`.

Rendimiento medido en la RX 580:

| | |
|---|---|
| Lectura del prompt | ~210 tokens/s |
| Generación | ~12 tokens/s (límite de Polaris: sin fp16 ni int dot) |
| Prompt de Mochi (sistema + 30 tools) | ~4.850 tokens |
| Primera orden sin caché | ~80 s |
| Paso del LLM con caché | **~2,7 s** (una orden con herramienta son 2 pasos: 5–6 s) |

Por eso el puente de voz **precalienta** la caché al arrancar. Si se reinicia `llama-server`,
hay que reiniciar también el puente.

La caché solo sirve si el comienzo del prompt es idéntico (sistema + lista de tools). Cualquier
cambio en la lista de tools cuesta 80 s y además borra la caché para la orden siguiente.
Ver "Gotchas" en `CLAUDE.md`.

## 2. Notebook (`mochi-desktop - V0.3/`)

Funciona en **Windows** (el código original) y en **Linux con KDE Plasma (Wayland)**:
en Linux, los módulos que usaban win32/UIA/PowerShell toman el equivalente de
`linux_platform/` (ventanas por KWin, multimedia por MPRIS, volumen con `wpctl`,
portapapeles con `wl-clipboard`, aplicaciones desde los `.desktop`).

### Instalar (Linux)

Desde la raíz del proyecto:

```bash
python3 -m venv .venv
.venv/bin/pip install -r "mochi-desktop - V0.3/requirements-linux.txt" -r "mochi-desktop - V0.3/requirements-voice.txt" platformio
```

Para el ESP32 (una sola vez, pide la clave de sudo):

```bash
sudo cp linux/99-mochi-esp32.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger
```

La regla da acceso al puerto sin `sudo` ni grupo `dialout` y evita que ModemManager
le mande comandos AT al ESP32. Después de instalarla, reconectar el cable.

En Windows: `pip install -r requirements-voice.txt` (y los requisitos de siempre de Mochi).

### Conectar Mochi Desktop con el LLM

En `~/.mochi/config.json` (en Windows, el que muestra `print_configuration()` al arrancar), sección `brain`:

```json
"brain": {
  "enabled": true,
  "runtime": "llama_cpp",
  "model": "qwen3.5-9b",
  "base_url": "http://<IP-de-la-PC>:8080",
  "api_key": "<contenido de llm-server/api_key.txt>",
  "thinking_enabled": false,
  "context_size": 16384
}
```

`<IP-de-la-PC>` es la IP de la PC en la LAN. Con Tailscale (anda desde cualquier red) se usa la IP de la PC en la tailnet (`100.x.y.z`), que no cambia.

Prueba rápida (toma la URL y la clave del `config.json`), desde `mochi-desktop - V0.3/`:

```bash
../.venv/bin/python -m brain.llama_cpp_provider
```

### Puente de voz

Abre la **ventana de voz de Mochi** (la del Mochi limpio, `voice_bridge/mochi_pc/`) y se
conecta sola al ESP32. En la misma ventana:

- **Órdenes fijas** como siempre: "Mochi, duerme", "Mochi, feliz"…
- **LLM sin tocar la placa:** botón **🎤 Hablarle a Mochi** (se envía solo al dejar de
  hablar) o el cuadro de texto. También sirve mantener apretado BOOT en la placa.
- **Parlante PC:** en Linux aparece la salida de audio **«Mochi»** en el sonido del sistema
  (icono de volumen de KDE). Lo que suena ahí sale por el parlante de Mochi. Mientras Mochi
  escucha o responde, la música se pausa.

En Linux, la forma normal ahora es la app `mochi-app/` (doble clic en `Mochi.desktop`, en la raíz de la carpeta). La ventana Tk sigue en `Iniciar_Mochi_LLM.sh`.

Desde `mochi-desktop - V0.3/` (en Windows, `python` en lugar de `../.venv/bin/python`):

```bash
../.venv/bin/python -m voice_bridge.esp32_bridge                      # autodetecta el ESP32
../.venv/bin/python -m voice_bridge.esp32_bridge --echo               # sin LLM: repite lo que oyó
../.venv/bin/python -m voice_bridge.esp32_bridge --save-wav grabaciones
../.venv/bin/python -m voice_bridge.esp32_bridge --stt whisper        # Whisper en vez de Vosk
../.venv/bin/python -m voice_bridge.esp32_bridge --list               # ver puertos
../.venv/bin/python -m voice_bridge.simulador "cuál es el volumen"      # sin placa (Linux): ESP32 simulado
../.venv/bin/python -m voice_bridge.mochi_pc.app                      # solo la ventana, sin LLM
```

Voz a texto:

| Motor | Con voz sintética (12 órdenes) | Tiempo en la i3 |
|---|---|---|
| Vosk `small-es` (por defecto) | 2/12 exactas | 0,4 s |
| Whisper `small` (`--stt whisper`) | 7/12 exactas | 2,2 s |

Falta medir con el micrófono real: con `--save-wav` se guardan las grabaciones para comparar.
Whisper necesita `pip install faster-whisper` y baja el modelo la primera vez.

Las órdenes que llegan por voz llevan una indicación al final ("respuesta corta, sin listas
ni formato") y la respuesta se limpia de Markdown antes de pasarla a Piper.

Modelos en `voice_bridge/models/` y en `mochi-app/models/` (no están en el repositorio: pesan ~170 MB, se bajan aparte):

- `vosk-model-small-es-0.42`: voz a texto. De https://alphacephei.com/vosk/models (descomprimir el zip ahí).
- `es_AR-daniela-high.onnx` (+ `.onnx.json`): voz de Mochi (Piper). De https://huggingface.co/rhasspy/piper-voices (`es/es_AR/daniela/high/`).

Mochi Desktop sigue funcionando igual por HTTP (`python main.py`, puerto 8765, para Android). El puente es otra entrada al mismo `command_service`.

### Qué funciona en Linux

| | Linux (KDE Wayland) |
|---|---|
| Ventanas: listar, enfocar, minimizar, maximizar, cerrar | ✅ (scripts de KWin por D-Bus) |
| Multimedia: estado, play/pausa, saltar, siguiente | ✅ (MPRIS) |
| Volumen | ✅ (`wpctl`) |
| Portapapeles, notificaciones | ✅ |
| Aplicaciones instaladas, carpetas del usuario, Steam | ✅ |
| Abrir sitios en Chrome/Chromium | ✅ |
| Captura de pantalla | ✅ (Spectacle) |
| Leer y tocar páginas web (`web_inspect`, `web_act`) | ✅ en Chromium/Chrome, por accesibilidad (AT-SPI); ver abajo |

#### Páginas web en Linux (`web_inspect` / `web_act`)

`linux_platform/web.py` hace lo mismo que `web_interaction/` (UI Automation) pero por
AT-SPI, con el navegador real del usuario y sus sesiones (sin DevTools). Devuelve el mismo
formato que en Windows, así que el prompt y las tools no cambian.

- **Chromium/Chrome tiene que abrirse con `--force-renderer-accessibility`**; si no, publica
  la ventana pero no el contenido de las páginas y `web_inspect` devuelve
  `web_content_unavailable`. Mochi agrega el flag cuando abre el navegador, pero si ya
  estaba abierto desde el menú las pestañas nuevas usan ese proceso. Para que valga siempre:
  `bash linux/chromium-con-accesibilidad.sh` (copia el lanzador a `~/.local/share/applications/`
  con el flag, sin sudo) y reabrir el navegador. La variable `ACCESSIBILITY_ENABLED=1` no alcanza
  en Chromium 155.
- Leer, clic, casillas, radios, opciones, pestañas, sliders y desplazar: todo por AT-SPI.
- **Escribir (`type`):** Chromium no implementa `EditableText` y Wayland no deja mandar teclas
  a otra ventana. Mochi trae la ventana al frente, copia el texto al portapapeles, usa
  «Pegar como texto sin formato» del menú contextual (por AT-SPI) y después restaura lo que
  había en el portapapeles.
- **Enter / Espacio (`submit`, `keyboard_activate`):** por el portal RemoteDesktop. La primera
  vez KDE pregunta si Mochi puede controlar el teclado; aceptando con «Recordar» no vuelve a
  preguntar (`~/.mochi/linux_remote_desktop_token`). Sin permiso, `submit` da error y
  `keyboard_activate` usa la acción predeterminada del control.
- Mientras Mochi escribe o aprieta Enter, la ventana del navegador pasa al frente.

## 3. ESP32-S3 (`firmware/Mochi/`)

Firmware **Mochi 3.5 unificado**: es el Mochi limpio v3.3.8 (cara animada, órdenes fijas,
grabar/reproducir, Parlante PC mono/estéreo) más el modo LLM. Sirve para los dos programas:
la ventana sola (`Mochi_PC`) y el puente del LLM.

**Subir sin compilar** (recomendado, ~30 s): `firmware/Mochi/binarios/LEEME.txt`
(flasheador web en Chrome/Edge o `bash firmware/Mochi/binarios/subir_firmware.sh`).

**Arduino IDE:** abrir `firmware/Mochi/Mochi.ino`. Paquete esp32 de Espressif 3.x
(probado 3.3.12), placa ESP32S3 Dev Module, USB CDC On Boot: Enabled, USB Mode: Hardware
CDC and JTAG, Upload Mode: UART0 / Hardware CDC, PSRAM: QSPI PSRAM. Bibliotecas Adafruit
GFX, Adafruit ST7735 and ST7789 y Adafruit BusIO. Si se cambia el código, regenerar los
`.bin` (Sketch → Exportar binario compilado) antes de pasarlo al equipo.

Conectar el cable al puerto **USB** nativo, no al que dice UART.

### Conexiones (las del Mochi limpio v3.3.8, NO las del informe Fase 3)

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
| **Botón** | | BOOT de la placa (GPIO0), opcional: la ventana tiene «Hablarle a Mochi» |

### Cara con el LLM

| Estado | Cara (abajo, el texto) |
|---|---|
| escuchando | curiosa, parpadea; "Escuchando" |
| pensando | curiosa; "Pensando..." y lo que entendió (`> ...`) |
| hablando | feliz, la boca se mueve con la voz; la respuesta |
| listo / error | feliz / sorprendida, y a los 3 s vuelve a las animaciones automáticas |

Las órdenes fijas, el bostezo, dormir, guiño, la reacción al sonido, etc. siguen igual.

## Protocolo serie

El del Mochi limpio v3.3.8 (sigue funcionando el `Mochi_PC` viejo) más el modo LLM:

- ESP32 → PC: paquetes `MCHI` con CRC32 (PCM del micrófono, HELLO, STATUS, TEXT).
  Nuevo: STATUS `{"button":"down"}` / `{"button":"up"}` con BOOT; el HELLO trae `"llm":1`.
- PC → ESP32: COBS(`A5 5A | tipo | largo | datos | CRC16`) + `00`. Órdenes `C` de una letra,
  audio `A`/`B`/`S`. Nuevo: orden `0`..`5` = estado del LLM y paquete `T` = texto (ASCII).
  La voz de Piper viaja como el Parlante PC (`K`, paquetes `B`, `J`).

Tiene que coincidir en:

- `firmware/Mochi/MochiFrame.h`, `MochiPCLink.h` y `Mochi.ino` (`handleCommand`)
- `mochi-desktop - V0.3/voice_bridge/mochi_pc/protocol.py` y `pc_speaker.py` (`frame_packet`)

El firmware viejo del puente (0.1, PlatformIO, pines de la Fase 3) ya no existe.

## Pendiente

- Palabra de activación ("Mochi, …" con texto libre) para el LLM, sin botón.
- Probar Vosk y Whisper con el micrófono real y elegir uno.
