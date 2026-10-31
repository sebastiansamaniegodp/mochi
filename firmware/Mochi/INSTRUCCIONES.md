# Mochi 3.5 — cómo subir el firmware y usarlo

Un solo firmware para todo:

- **La carita de siempre** (Mochi limpio v3.3.8): ojos animados, parpadeo, guiño, mirar alrededor, bostezo, dormir, reacción al sonido.
- **Las órdenes programadas por voz:** "Mochi, feliz", "Mochi, duerme", "Mochi, sorpresa", "Mochi, guiño", "Mochi, graba", "Mochi, reproduce", etc.
- **Parlante PC:** la música o los videos de la PC suenan por el parlante de Mochi.
- **El LLM:** le hablás o le escribís a Mochi desde la ventana. La cara reacciona: curiosa cuando escucha y piensa, feliz con la boca moviéndose cuando habla, sorprendida si hay un error. La respuesta aparece abajo de la cara.

El cableado es el del Mochi limpio. No hay que cambiar nada de la placa.

**Todo está en una sola carpeta: `~/Escritorio/ROBOTICA`.** El firmware está en `ROBOTICA/firmware/Mochi/` y el lanzador de Mochi en `ROBOTICA/`.

---

## Parte 1 — Subir el firmware a la placa

Hay dos maneras. Las dos dejan el mismo firmware en la placa.

### Manera A: con el Arduino IDE

Sirve si querés ver o cambiar el código.

**Una sola vez por computadora** (si el IDE ya compila para ESP32-S3, saltear):

1. *Archivo → Preferencias → Gestor de URLs adicionales de tarjetas:* agregar
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. *Herramientas → Placa → Gestor de tarjetas:* buscar **esp32** (de Espressif Systems) e instalar la versión **3.3.12**.
   Ocupa ~2 GB y la primera vez tarda (depende de internet). Si no hay tiempo, usar la **manera B**: no necesita nada de esto.
3. *Herramientas → Administrar bibliotecas:* instalar **Adafruit GFX Library**, **Adafruit ST7735 and ST7789 Library** y **Adafruit BusIO**.

**Cada vez:**

1. Abrir `ROBOTICA/firmware/Mochi/Mochi.ino` (*Archivo → Abrir*; los `.h` se abren solos en pestañas).
2. En *Herramientas*:

   | Opción | Valor |
   |---|---|
   | Placa | **ESP32S3 Dev Module** |
   | USB CDC On Boot | **Enabled** |
   | USB Mode | **Hardware CDC and JTAG** |
   | Upload Mode | **UART0 / Hardware CDC** |
   | PSRAM | **QSPI PSRAM** |
   | Puerto | el del ESP32 (`/dev/ttyACM0` en Linux, `COMx` en Windows) |

3. Conectar el ESP32 por el USB **nativo** (el que dice USB/OTG, no UART).
4. Cerrar el Monitor Serie y la ventana de Mochi si están abiertos (ocupan el puerto).
5. **Subir** (flecha →). Compilar tarda ~1 minuto.
6. Al terminar, apretar **RESET** una vez (sin BOOT).

> Si cambiás el código: antes de pasarlo al equipo, *Sketch → Exportar binario compilado* y copiar `Mochi.ino.merged.bin` a `binarios/`. Así nadie tiene que compilar.

### Manera B: sin compilar (30 segundos, no instala nada)

El firmware ya está compilado en `binarios/Mochi.ino.merged.bin`.

**En cualquier computadora (Windows, Linux, Mac), con Chrome o Edge:**

1. Conectar el ESP32 por el USB nativo. Cerrar el Arduino IDE o la ventana de Mochi si están abiertos.
2. Abrir https://espressif.github.io/esptool-js/
3. Baudrate **921600** → **Connect** → elegir el puerto del ESP32.
   Si no aparece: mantener BOOT, apretar RESET, soltar BOOT y reintentar.
4. *Flash Address* **0x0** y elegir el archivo `binarios/Mochi.ino.merged.bin`.
5. **Program**. Cuando termine, apretar **RESET** una vez.

**En la notebook Linux, con un comando:**

```bash
bash ~/Escritorio/ROBOTICA/firmware/Mochi/binarios/subir_firmware.sh
```

---

## Parte 2 — Usar a Mochi desde la PC

### Con el LLM (todo desde la ventana, sin apretar nada en la placa)

1. En la PC del LLM, prender `llm-server/start.sh` (si no, Mochi responde con error). En esa PC no hay que instalar ni cambiar nada más.
2. En la notebook, abrir la carpeta `ROBOTICA` del Escritorio y doble clic en **«Mochi (voz + LLM)»**.
   Otra forma: doble clic en `mochi-desktop - V0.3/Iniciar_Mochi_LLM.sh`, o en una terminal:

   ```bash
   cd ~/Escritorio/ROBOTICA/"mochi-desktop - V0.3" && ../.venv/bin/python -m voice_bridge.esp32_bridge
   ```

   En Windows, desde la carpeta `mochi-desktop - V0.3`: `python -m voice_bridge.esp32_bridge`

3. Se abre la ventana de Mochi y se conecta sola al ESP32.
4. Para hablarle al LLM:
   - Clic en **🎤 Hablarle a Mochi**, hablar, y se envía solo cuando dejás de hablar.
   - O escribir en el cuadro y **Enviar texto** (o Enter).
   - Opcional: mantener apretado **BOOT** en la placa mientras hablás.
5. Las órdenes programadas ("Mochi, duerme"...) siguen funcionando en la misma ventana.

Para probar sin el LLM: `bash Iniciar_Mochi_LLM.sh --echo` (Mochi repite lo que oyó).

### Parlante PC (que la PC suene por Mochi)

Solo en Linux: al abrir la ventana aparece una salida de audio nueva llamada **«Mochi»**.

1. En la ventana, *Audio que sale de la PC:* ya viene elegida **Mochi**. Clic en **Activar parlante PC**.
2. En el icono de volumen de KDE, elegir **Mochi** como salida (o solo para una app, en el mezclador).
   Lo que suena ahí sale por el parlante de Mochi y no por la notebook.
3. Para volver: elegir otra vez los parlantes de la notebook en el icono de volumen.

Mientras Mochi te escucha o responde, la música se pausa sola y después sigue.
Al cerrar la ventana, la salida «Mochi» desaparece.

### Solo la ventana de prueba (sin LLM)

Sirve igual que antes el `Mochi_PC` del Mochi limpio (`Mochi_v3_3_8_Audio_Limpio_Linux/Mochi_PC/Iniciar_Mochi.sh`). Este firmware es compatible.

---

## Si algo falla

| Pasa | Probar |
|---|---|
| No aparece el puerto | Cable de datos (no solo carga), USB nativo; mantener BOOT + RESET para entrar en modo descarga |
| "Puerto ocupado" | Cerrar Monitor Serie, Arduino IDE, la ventana de Mochi o el flasheador web |
| La ventana dice "Firmware sin modo LLM" | La placa tiene el Mochi limpio viejo: subir este firmware (parte 1) |
| La cara no arranca después de subir | Apretar RESET una vez (sin BOOT) |
| El LLM no contesta | Prender `llm-server/start.sh` en la PC del LLM; la primera orden puede tardar |
| "No escuché nada" | Hablar más cerca del micrófono (INMP441) |
