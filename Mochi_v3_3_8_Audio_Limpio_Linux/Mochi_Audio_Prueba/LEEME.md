# Mochi Voz v3.2: órdenes habladas por USB

Esta versión añade control por voz al proyecto de pantalla y audio que ya probaste. Usa el micrófono INMP441 de Mochi, envía sus muestras por USB y reconoce órdenes en español en la PC mediante Vosk. La PC devuelve una letra al ESP32 para ejecutar la acción.

La revisión 3.2 amplía la grabación de 1,5 a 6 segundos cuando hay RAM disponible y muestra su duración real. Conserva las mejoras de inicio USB de 3.1. **Carga este firmware y actualiza `Mochi_PC`** para usarla.

El ZIP incluye dos carpetas:

- `Mochi_Audio_Prueba`: firmware para cargar desde Arduino IDE. Conserva juntos el `.ino`, `AudioMochi.h`, `MochiUSB.h` y `MochiFrame.h`.
- `Mochi_PC`: programa de Windows. Abre `Iniciar_Mochi.bat` después de cargar el firmware.

El reconocimiento funciona mientras el programa de PC está abierto y conectado. Al cerrar ese programa, la cara sigue funcionando en el ESP32, pero el reconocimiento de palabras se detiene.

## 1. Carga el firmware nuevo

1. Extrae todo el ZIP en una carpeta nueva. Ejecuta los archivos desde la carpeta extraída.
2. Abre `Mochi_Audio_Prueba/Mochi_Audio_Prueba.ino`.
3. Usa el paquete **esp32 de Espressif Systems 3.x**. Compilación validada con **3.3.12**.
4. Selecciona **ESP32S3 Dev Module** dentro del menú de ese paquete.
5. Para el USB nativo: **USB Mode: Hardware CDC and JTAG**, **USB CDC On Boot: Enabled**, **Upload Mode: UART0 / Hardware CDC**.
   En el ESP32-S3 detectado con **2 MB de PSRAM integrada**, selecciona **PSRAM: QSPI PSRAM** para reservar la grabación de seis segundos. Si usas otra placa, elige el tipo de PSRAM que le corresponda.
6. Selecciona el puerto del ESP32 (antes COM12) y pulsa Subir.
7. **Cierra el Monitor Serie y el Serial Plotter** al terminar. El programa de voz necesita acceder al mismo puerto.
8. Pulsa **RESET/EN una vez, sin mantener BOOT**, y espera que la cara vuelva a animarse. Usa el conector USB nativo del ESP32-S3 (normalmente marcado USB/OTG), no el USB-to-UART, si tu placa tiene ambos. En el programa de voz, actualiza la lista y elige el puerto que aparece con ese conector.

Bibliotecas de Arduino: Adafruit GFX Library, Adafruit ST7735 and ST7789 Library y Adafruit BusIO. I2S está incluido en el paquete de placas.

El firmware transmite audio y mensajes en paquetes binarios a 921600 baudios. El Monitor Serie no es la interfaz de esta versión; el panel de Windows muestra los mensajes y el nivel del micrófono. El firmware anterior de prueba no envía este audio: hay que cargar el nuevo.

## 2. Abre el programa de Windows

1. Conecta el USB de Mochi a la PC.
2. En la carpeta `Mochi_PC`, haz doble clic en **Iniciar_Mochi.bat**.
3. La primera ejecución prepara un entorno Python privado con Vosk y pySerial. Necesita Internet para instalar esas dependencias.
4. En la ventana, selecciona el puerto del ESP32 y pulsa **Conectar**. Se preselecciona un puerto nativo Espressif si hay uno solo, o COM12 si está disponible. Verifica que sea el de tu ESP32.
5. La primera conexión descarga el modelo español oficial, de unos 39 MB. Se guarda para las siguientes ejecuciones.
6. Espera a que diga **Escuchando a través del micrófono de Mochi**. Habla cerca del INMP441 y comprueba que sube la barra de nivel.

Si el lanzador pide Python, instala Python para Windows **x64 de 64 bits**, versión 3.10 o superior, incluyendo pip y Tkinter. En el instalador clásico, activa “Add Python to PATH”. El programa de PC se validó con Python 3.12. No necesita el micrófono de la laptop ni una biblioteca de captura de audio de Windows.

El modelo se descarga en `%LOCALAPPDATA%/MochiVoz/modelos/vosk-model-small-es-0.42`. Después de instalar las dependencias y descargar el modelo, el reconocimiento funciona sin Internet. El audio se procesa localmente; este programa no guarda grabaciones en archivos ni envía el audio a un servicio externo.

## 3. Dale una orden

Di la llamada y la orden en una misma frase, con naturalidad:

| Frase | Acción |
|---|---|
| Mochi, duerme | Mantiene la expresión dormida |
| Mochi, despierta | Vuelve a las animaciones automáticas |
| Mochi, feliz | Sonrisa y ojos felices |
| Mochi, normal | Expresión normal |
| Mochi, curioso | Expresión curiosa |
| Mochi, sorpresa | Expresión sorprendida |
| Mochi, parpadea | Parpadeo |
| Mochi, guiño | Guiño; se aprecia mejor con ojos normales |
| Mochi, sonido | Tres notas suaves por el parlante |
| Mochi, graba | Graba hasta 6 segundos; el panel muestra el límite disponible |
| Mochi, reproduce | Reproduce la última grabación |
| Mochi, quieto | Detiene el desplazamiento de la cara |
| Mochi, muévete | Activa el desplazamiento; si está dormido, despiértalo primero |
| Mochi, automático | Recupera las animaciones automáticas |

También puedes decir “Hola Mochi, duerme”. Si dices solo “Mochi” y haces una pausa, tienes una ventana de cinco segundos para decir la orden. Las acciones se ejecutan después de que el reconocimiento completa la frase, normalmente tras una breve pausa.

Para probar la grabación: di **Mochi, graba**, espera a que el panel indique **GRABANDO 6 segundos**, pronuncia una frase que quepa en ese tiempo y espera a que vuelva a Escuchando. Después di **Mochi, reproduce**. El registro muestra los segundos capturados y reproducidos. La grabación se guarda en RAM del ESP32 y se pierde al reiniciar.

El tiempo empieza cuando el ESP32 acepta la orden, después del reconocimiento de voz. Hablar antes de GRABANDO puede perder el principio. Esta versión graba una ventana fija; una frase de más de seis segundos se corta al alcanzar el límite. Sin PSRAM, reserva la mayor duración que quepa en la RAM interna, dejando espacio para las tareas: puede ser menor. El panel muestra ese límite real para que no parezca una grabación de seis segundos cuando no lo es.

Durante la grabación y reproducción, el programa pausa el reconocimiento para evitar que el sonido del parlante genere nuevas órdenes. Espera al mensaje “Escuchando…” antes de hablar otra vez. El sueño de la cara mantiene el micrófono activo para poder recibir “Mochi, despierta”.

Esta versión controla las acciones de la tabla. Las respuestas conversacionales y la voz sintetizada son una siguiente ampliación.

## 4. Si no entiende o no conecta

- **Puerto ocupado / acceso denegado**: cierra Monitor Serie, Serial Plotter y cualquier otra instancia del programa de voz. Pulsa Conectar otra vez.
- **Write timeout / No se pudo enviar la consulta inicial**: Windows no completó el envío por USB. Cierra el programa, pulsa RESET/EN sin mantener BOOT y vuelve a abrirlo; pulsa Actualizar y verifica el puerto del USB nativo. Si persiste, confirma **USB CDC On Boot: Enabled**, **USB Mode: Hardware CDC and JTAG**, **Upload Mode: UART0 / Hardware CDC**, y vuelve a cargar este firmware. Prueba un cable de datos y otro puerto USB directo de la PC si hay desconexiones. La revisión del programa permite 2 segundos por envío, espera 1 segundo tras abrir el puerto y da hasta 10 segundos para identificar el firmware; no repite acciones si un envío falla.
- **No respondió el firmware de voz**: confirma que subiste este `.ino`, que elegiste ESP32S3 Dev Module y USB CDC al inicio está Enabled. Pulsa RESET y actualiza la lista de puertos.
- **La barra no sube al hablar**: revisa VDD a 3V3, GND, L/R a GND y los pines 4/5/6 del INMP441. Que el controlador RX inicie no confirma que el módulo esté bien conectado.
- **La grabación reproduce solo parte de la frase**: espera el aviso GRABANDO antes de hablar y termina dentro de la duración indicada. Si el panel muestra menos de 6 segundos, activa la PSRAM correcta (QSPI para los 2 MB detectados), vuelve a cargar este firmware y reinicia. El registro debe indicar cerca de 96000 muestras (6,00 s). Si indica menos pese a mostrar un límite de 6 s, revisa los mensajes de captura y el cableado del micrófono.
- **La barra permanece alta / audio saturado**: reduce `MIC_GAIN` en `AudioMochi.h`, por ejemplo de 8 a 4. Separa el micrófono del parlante.
- **La barra se mueve pero no ejecuta la orden**: mira el texto reconocido en el panel. Empieza diciendo la frase completa “Mochi, duerme” o “Hola Mochi, duerme”, a 15–30 cm del micrófono, con el ambiente silencioso.
- **Reconoce el nombre de otra forma**: `Mochi_PC/config.json` incluye variantes del nombre. Acepta “mochi”, “moche” y “mochila” al inicio de la frase; las variantes ausentes del vocabulario no se añaden a la gramática. También incluye llamadas alternativas completas “hola noche” y “hola mucho”. Podemos ajustar las variantes a partir del texto que muestre tu prueba.
- **Descarga bloqueada**: descarga el modelo desde https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip y extrae su carpeta en `%LOCALAPPDATA%/MochiVoz/modelos/`. Debe existir `vosk-model-small-es-0.42/am/final.mdl` dentro de esa ruta.
- **Faltan dependencias**: abre el lanzador `.bat` otra vez con Internet y comparte el error que muestre su ventana.

Para cerrar, pulsa Desconectar o cierra la ventana. El puerto se libera para Arduino. El firmware corta el envío de audio si deja de recibir la señal de presencia de la PC durante ocho segundos.

## 5. Cableado usado por este proyecto

Cambia conexiones únicamente con el USB desconectado. Usa los números GPIO impresos en la placa y contactos firmes, soldados.

| Componente | Pin | ESP32-S3 / destino |
|---|---|---|
| TFT | GND | GND |
| TFT | VCC y BLK | 3V3 |
| TFT | SCL | GPIO12 |
| TFT | SDA | GPIO11 |
| TFT | RES | GPIO8 |
| TFT | DC | GPIO9 |
| TFT | CS | GPIO10 |
| INMP441 | VDD / VCC | 3V3 |
| INMP441 | GND y L/R | GND |
| INMP441 | SCK / BCLK | GPIO4 |
| INMP441 | WS | GPIO5 |
| INMP441 | SD / DOUT | GPIO6 |
| MAX98357A | VIN | 5V del ESP32 alimentado por USB |
| MAX98357A | GND | GND |
| MAX98357A | BCLK | GPIO16 |
| MAX98357A | LRC / WS | GPIO17 |
| MAX98357A | DIN | GPIO18 |
| MAX98357A | SD / EN, si existe | 3V3 |
| MAX98357A | GAIN | Sin conectar |
| Parlante | Sus dos cables | SPK+ y SPK− del MAX98357A |

El micrófono debe alimentarse a 3,3 V. Ninguno de los terminales del parlante va a GND: ambos son salidas activas del amplificador. Para la prueba, conserva el volumen bajo incluido. SPI está configurado a 20 MHz; si la TFT muestra corrupción, revisa primero los contactos y prueba 10 MHz.

## 5b. Estéreo con dos parlantes (firmware 3.4)

Cada parlante tiene su propio MAX98357A. Los dos módulos comparten las mismas señales I2S; el pin SD/EN decide qué canal toma cada uno.

| Pin | MAX98357A izquierdo (el actual) | MAX98357A derecho (nuevo) |
|---|---|---|
| VIN | 5V | 5V |
| GND | GND | GND |
| BCLK | GPIO16 | GPIO16 (en paralelo) |
| LRC/WS | GPIO17 | GPIO17 (en paralelo) |
| DIN | GPIO18 | GPIO18 (en paralelo) |
| SD/EN | 3V3 directo (canal izquierdo) | Divisor: 22 kΩ desde 3V3 y 10 kΩ a GND (~1 V, canal derecho) |
| GAIN | Sin conectar | Sin conectar |
| SPK+/SPK− | Parlante izquierdo | Parlante derecho |

- El MAX98357A elige el canal según la tensión en SD/EN: más de 1,4 V es izquierdo; entre 0,77 y 1,4 V es derecho; entre 0,36 y 0,77 V es la mezcla (L+R)/2; menos de 0,16 V lo apaga. El divisor deja ~1 V, en el centro de la ventana del derecho, aunque el módulo tenga sus propias resistencias en ese pin.
- En el programa de PC, marca **Estéreo**. **Prueba tono USB** suena 1 s a la izquierda (880 Hz) y 1 s a la derecha (660 Hz). Si salen al revés, intercambia los parlantes.
- Sin la casilla marcada, o con el tono local y la grabación, los dos parlantes suenan igual.
- Dos amplificadores pueden pedir picos de casi 1 A cada uno. Con el USB de la PC, mantén el volumen moderado; si el ESP32 se reinicia o el COM se desconecta al subir el volumen, alimenta los VIN desde una fuente de 5 V y 2 A con GND común.
- Por USB viajan ~49 kB/s en estéreo (paquete `S`, 960 bytes PCM12 cada 20 ms), el doble que en mono.

## 6. Validación y límites de la prueba

Se compiló el firmware para ESP32-S3 con Espressif 3.3.12 tanto con QSPI PSRAM como con PSRAM desactivada. Pasaron pruebas del encuadre de paquetes C++/Python, recepción fragmentada, recuperación tras datos corruptos, límite de buffers, interpretación de órdenes con llamada previa, tiempo de activación y carga del modelo español real.

Además, una frase de voz sintética “Mochi, duerme” recorrió el programa real de PC: audio PCM enviado por un puerto serie simulado, reconocimiento Vosk y devolución del comando D. Falta verificar la conexión USB física, tu voz, el nivel del INMP441 y la ventana de Windows en tu equipo.

La revisión USB pasó cuatro pruebas de transporte: arranque lento, aviso claro al agotarse un envío inicial, ausencia de repetición de una acción cuyo envío falló y cancelación durante el inicio. Volvió a pasar la prueba completa de voz sintética y puerto simulado. Estas pruebas no sustituyen la comprobación del USB físico en Windows.

Para la revisión 3.2 se probó el cuerpo real del controlador de audio con interfaces de hardware simuladas y comprobaciones de límites de memoria: captura de 96000 muestras, reproducción de las 96000 en ambos canales sin perder el final, continuación de una escritura I2S parcial, límite de espera con salida bloqueada, reserva reducida sin PSRAM y fallo controlado sin RAM suficiente. El panel también pasó una prueba con capacidades reales anunciadas de 6 y 3 segundos. Falta escuchar y comprobar la grabación física con tu INMP441 y MAX98357A.

El lienzo usa 134400 bytes y la grabación de seis segundos 192000 bytes, además de buffers de audio/USB y tareas. El buffer de grabación intenta usar PSRAM primero. Si no puede, limita su duración a la RAM interna disponible conservando al menos 32768 bytes para otras reservas. La placa detectada anteriormente tenía 2 MB de PSRAM Quad SPI; activa QSPI PSRAM en Arduino para aprovecharla.

## Referencias

- Vosk y reconocimiento local: https://alphacephei.com/vosk/
- Modelo español pequeño, licencia Apache 2.0: https://alphacephei.com/vosk/models
- pySerial: https://pyserial.readthedocs.io/en/latest/pyserial_api.html
- Arduino-ESP32 e I2S: https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/peripherals/i2s.html
- Tipo de PSRAM integrada del ESP32-S3: https://documentation.espressif.com/esp-hardware-design-guidelines/en/latest/esp32s3/index.html

El modelo y las dependencias se descargan desde sus distribuidores; sus licencias se conservan en esas distribuciones. Este ZIP contiene el firmware y el programa de control.
