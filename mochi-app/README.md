# Mochi (app para Linux)

La app de escritorio de Mochi: ventana en Qt (se ve como el resto de KDE),
empaquetada en un solo archivo **AppImage**. Es independiente de
`mochi-desktop - V0.3/`: tiene adentro todo lo que necesita.

- **Con la placa (ESP32)**: se conecta sola al enchufarla. Funciona igual que la
  ventana Tk anterior: órdenes fijas por voz («Mochi, duerme»), BOOT para hablarle
  al LLM, la cara y el texto en la pantalla, la voz por su parlante y el Parlante PC.
- **Sin la placa**: se le habla por el micrófono de la PC y contesta por sus
  parlantes. O se le escribe.

## Usar

Doble clic en **`ROBOTICA/Mochi.desktop`**, o directamente en
`dist/Mochi-x86_64.AppImage`. En el menú (☰) de la ventana:
**Agregar Mochi al menú de aplicaciones**, para abrirlo desde el lanzador de KDE.

La primera vez: menú → **Configuración** → dirección y clave del servidor del LLM
(la clave está en `llm-server/api_key.txt`). Se guarda en `~/.mochi/config.json`.

| | |
|---|---|
| Hablar | decir **«Mochi»** y la orden («Mochi, ¿qué hora es?», de corrido o en dos veces), **Hablarle a Mochi** o BOOT en la placa. Se envía solo al dejar de hablar |
| Órdenes fijas | «Mochi, duerme», «Mochi, feliz»… (solo con la placa) |
| Escribir | el cuadro de abajo y Enter |
| Memoria | recuerda la conversación (se le pueden pedir cosas seguidas: «abrí YouTube», «ahora poné música»). Al llegar al 80 % del contexto resume lo más viejo; después de media hora sin hablarle se olvida. **Nuevo chat** (arriba, o Ctrl+N) para empezar de cero |
| Memoria permanente | una bóveda de [Obsidian](https://obsidian.md) en `ROBOTICA/boveda` (otra carpeta: `"vault_path"` en `~/.mochi/config.json`). «Acordate de que…» guarda el dato en `Mochi/Recuerdos.md`, que Mochi tiene presente en todas las conversaciones aunque se cierre la app. Cada orden y su respuesta quedan en `Conversaciones/AAAA-MM-DD.md`: «¿qué te pedí ayer?» las busca, igual que cualquier nota que escribas ahí. Son archivos de texto: se corrigen o borran a mano o con Obsidian (*Abrir carpeta como bóveda*); Obsidian no hace falta para que funcione |
| Indicador **LLM** | verde: listo; rojo: no responde (se reintenta solo cada 30 s). Si se reinicia llama-server: menú → **Precalentar el LLM** |
| Pestaña **Placa** | puerto, nivel del micrófono de Mochi, órdenes fijas con botones |
| Pestaña **Parlante PC** | el audio de la PC sale por Mochi (salida «Mochi» del sonido de KDE) |
| Registro completo | `~/.cache/mochi/mochi.log` (menú → Abrir el registro completo) |

Opciones (del AppImage o de `./mochi.sh`): `--echo` (sin LLM, repite lo que oyó),
`--port /dev/ttyACM0`, `--stt whisper`, `--save-wav CARPETA`, `--no-tts`, `--list`.

## Carpetas

```
mochi-app/
├── mochi.sh                  correr desde el código (crea .venv/ la primera vez)
├── construir_appimage.sh     arma dist/Mochi-x86_64.AppImage
├── probar.sh                 tests (con un HOME temporal)
├── requirements.txt
├── models/                   Vosk (voz a texto) y Piper (voz de Mochi)
├── packaging/                AppRun y .desktop del AppImage
├── tests/
├── src/
│   ├── mochi_app/            la app
│   │   ├── main.py           arranque, argumentos, registro
│   │   ├── controller.py     une la ventana con la placa, la PC y el asistente
│   │   ├── assistant.py      voz -> texto -> LLM -> respuesta -> voz
│   │   ├── kde.py            estilo Breeze del sistema (si coincide la versión de Qt)
│   │   ├── settings.py       preferencias y servidor del LLM
│   │   ├── device/           el ESP32: protocolo serie, sesión USB, órdenes fijas, Parlante PC
│   │   ├── voice/            Vosk/Whisper/Piper y el audio de la PC (sin placa)
│   │   ├── ui/               ventana, cara, conversación, pestañas, configuración
│   │   ├── dev/simulador.py  ESP32 simulado para probar sin la placa
│   │   └── resources/        ícono, regla udev
│   └── agente/               el agente de Mochi Desktop: LLM + herramientas
└── dist/                     el AppImage armado
```

`src/agente/` es una copia de `mochi-desktop - V0.3` (sin `voice_bridge/`): sus
módulos se importan como de primer nivel (`brain`, `actions`…); `paths.py` lo agrega
a `sys.path`.

## Desarrollo

```bash
./mochi.sh                         # la app desde el código
./probar.sh                        # tests
./construir_appimage.sh            # AppImage (~2 min; la primera vez baja ~45 MB)
```

Probar sin la placa (crea un puerto falso que habla como el firmware 3.5):

```bash
PYTHONPATH=src .venv/bin/python -m mochi_app.dev.simulador "mochi feliz" --orden -- --echo
PYTHONPATH=src .venv/bin/python -m mochi_app.dev.simulador "qué volumen tiene la pc"
PYTHONPATH=src .venv/bin/python -m mochi_app.dev.simulador "mochí, qué hora es" --llamada -- --echo
PYTHONPATH=src .venv/bin/python -m mochi_app.dev.simulador --interactivo
```

Con `--programa dist/Mochi-x86_64.AppImage` prueba el AppImage en vez del código.
Ojo: Vosk entiende mal la voz sintética de Piper (falla igual que con la ventana Tk);
sin `--echo`, lo mal entendido va al LLM y ejecuta herramientas de verdad.

## Cosas a saber

- **La sesión USB (`device/session.py`) es la de la ventana Tk copiada sin cambios de
  lógica.** El protocolo tiene que coincidir con `firmware/Mochi/` (`MochiFrame.h`,
  `MochiPCLink.h`, `handleCommand`).
- **Locale:** Qt pone el del sistema (coma decimal) y Vosk escribe su JSON con printf:
  `"conf" : 1,000000` rompe el JSON y se cae la sesión USB. `main.py` deja
  `LC_NUMERIC` en `C` después de crear la `QApplication`. No sacarlo.
- **Breeze:** PySide6 6.8.2.1 trae Qt 6.8.2, igual que KDE en Debian 13. `kde.py`
  carga el estilo y el tema de Plasma del sistema solo si las versiones coinciden, y
  los prueba antes en otro proceso; si no, Fusion con los colores de KDE.
  `MOCHI_ESTILO=fusion` lo desactiva. Si se actualiza PySide6, tiene que seguir
  coincidiendo con el Qt del sistema.
- **Escuchar siempre «Mochi»** (Configuración → Voz, activado por defecto): con la placa
  usa su micrófono, que ya llegaba siempre a la PC; sin placa deja abierto el de la PC.
  Vosk con la gramática de las órdenes fijas espera la llamada (`device/commands.py`):
  si sigue una orden fija, la hace; si no, escucha la orden para el LLM, desde el final
  de «Mochi» (guarda los últimos segundos: `AudioTail`). Sola, esa gramática fuerza
  cualquier frase a una orden («Mochi, sube el volumen» → «mochi duerme»): por eso suma
  palabras comunes de órdenes para el LLM (`LLM_WORDS`). Si se agregan órdenes fijas o
  cambian las palabras, medir con varias frases de Piper.
  La llamada necesita confianza ≥ `WAKE_LLM_CONFIDENCE` (0,65): lo que siga va al LLM, y
  el LLM ejecuta herramientas. Mientras suena el Parlante PC, el firmware no manda el
  micrófono y no se oye «Mochi». En el simulador decir «Mochí» (con tilde): Piper dice
  «Mochi» y Vosk entiende «noche».
- **Caché del LLM:** `assistant.warm_up_llm` manda las mismas tools que el modo `auto`
  y la indicación de voz va al final del mensaje (ver `CLAUDE.md` de ROBOTICA).
- **Micrófono de la notebook:** con «Internal Mic Boost» al máximo satura (solo ruido) y
  no se detecta la voz; Mochi avisa. Volumen del micrófono en KDE ~50 %.
- **Puerto USB sin permiso:** regla udev en `src/mochi_app/resources/99-mochi-esp32.rules`
  (la ventana muestra cómo instalarla si hace falta).
- El AppImage no exporta `PYTHONPATH` ni `LD_LIBRARY_PATH` (encuentra `src/` con un
  `.pth`), así los programas que abre Mochi arrancan normales.
