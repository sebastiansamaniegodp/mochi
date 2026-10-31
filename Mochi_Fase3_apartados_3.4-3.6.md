# 3 4 Componentes y diagrama de conexiones

Seleccionamos los componentes que el equipo ya tiene. La pantalla del cronograma pasa de OLED a TFT IPS ST7789 de 1,69 pulgadas (240 × 280 píxeles). El ESP32-S3 usa sus dos controladores I2S, uno para el micrófono y otro para el amplificador, y un bus SPI para la pantalla. Todo el dispositivo se alimenta y se comunica por un único cable USB-C conectado a la notebook.

| Cantidad | Componente | Interfaz | Función en Mochi |
| :-: | :-: | :-: | :-: |
| 1 | ESP32-S3 DevKitC (con PSRAM) | USB nativo | Controlar los periféricos y mantener el enlace con la PC. |
| 1 | TFT IPS ST7789 1,69" | SPI | Ojos, boca y expresiones. |
| 1 | Micrófono INMP441 | I2S0 (entrada) | Captura de voz a 16 kHz, 16 bits, mono. |
| 1 | Amplificador MAX98357A | I2S1 (salida) | Convierte el audio digital y alimenta el parlante. |
| 1 | Parlante 4 Ω, 3 W | Analógica | Respuestas habladas y sonidos. |
| 1 | Pulsador (botón BOOT del DevKit en el prototipo) | GPIO 0 | Activar la escucha mientras no exista palabra de activación. |

## Asignación de pines

| Periférico | Pin del módulo | Pin ESP32-S3 | Nota |
| :-: | :-: | :-: | :-: |
| ST7789 | VCC / GND | 3V3 / GND | Alimentación lógica |
| ST7789 | SCL | GPIO 12 | SPI reloj |
| ST7789 | SDA | GPIO 11 | SPI datos |
| ST7789 | RES | GPIO 10 | Reinicio |
| ST7789 | DC | GPIO 13 | Dato / comando |
| ST7789 | CS | GPIO 14 | Selección de chip |
| ST7789 | BLK | GPIO 9 | Brillo por PWM |
| INMP441 | VDD / GND | 3V3 / GND | Alimentación lógica |
| INMP441 | L/R | GND | Canal izquierdo |
| INMP441 | WS | GPIO 15 | I2S0 selección de palabra |
| INMP441 | SCK | GPIO 16 | I2S0 reloj de bits |
| INMP441 | SD | GPIO 17 | I2S0 datos de entrada |
| MAX98357A | VIN / GND | 5V (VBUS) / GND | Potencia desde USB |
| MAX98357A | LRC | GPIO 4 | I2S1 selección de palabra |
| MAX98357A | BCLK | GPIO 5 | I2S1 reloj de bits |
| MAX98357A | DIN | GPIO 6 | I2S1 datos de salida |
| MAX98357A | SD | GPIO 7 | Apagado del amplificador (LOW = reposo) |
| Pulsador | — | GPIO 0 | Botón BOOT del DevKit |
| USB-C nativo | D− / D+ | GPIO 19 / 20 | Enlace con la PC, no usar para otra función |

## Bosquejo del diagrama

```
                         ┌──────────────────────────────┐
  Notebook ══ USB-C ═════╡ USB nativo   ESP32-S3 DevKitC │
  (5 V + datos)          │                              │
                         │ 3V3 ───────────┬─────────────┬┼──── VCC  TFT ST7789
                         │ GPIO12 SCL ────┼─────────────┼┼──── SCL
                         │ GPIO11 SDA ────┼─────────────┼┼──── SDA
                         │ GPIO10/13/14 ──┼─────────────┼┼──── RES / DC / CS
                         │ GPIO9  PWM ────┼─────────────┼┼──── BLK
                         │                │             │
                         │ 3V3 ───────────┘             └───── VDD  INMP441
                         │ GPIO15/16/17 ────────────────────── WS / SCK / SD   (L/R → GND)
                         │                                    
                         │ 5V (VBUS) ──────────┬───────────── VIN  MAX98357A ── (+) Parlante
                         │                     ═ 470 µF        │                 4 Ω 3 W
                         │ GPIO4/5/6 ──────────┼───────────── LRC / BCLK / DIN ─ (−)
                         │ GPIO7 ──────────────┼───────────── SD
                         │ GND ────────────────┴───────────── GND (común a todos)
                         └──────────────────────────────┘
```

## Observaciones de diseño

- Alimentación: un puerto USB 2.0 entrega 500 mA. El parlante a plena potencia podría superar ese consumo y reiniciar el ESP32-S3. Proponemos limitar el volumen por software, dejar la ganancia del MAX98357A en el valor por defecto y colocar un capacitor de 470 µF entre 5V y GND junto al amplificador.
- Ruido: conviene separar físicamente el micrófono del parlante y apagar el amplificador con el pin SD mientras Mochi escucha.
- Pines evitados: GPIO 19 y 20 (USB), GPIO 26 a 37 (memoria flash y PSRAM) y los pines de arranque, salvo GPIO 0, que se usa como botón.
- Pendiente: confirmar la variante exacta del DevKit y su PSRAM, y medir el consumo real con el volumen máximo previsto.


# 3 5 Protocolo de comunicación entre el ESP32 y la PC

## Capas

El protocolo tiene dos tramos:

1. ESP32-S3 ↔ notebook: USB CDC (puerto serie virtual) sobre el USB nativo del ESP32-S3. Transporta audio, expresiones y estados.
2. Notebook ↔ servidor LLM (PC con la RX 580): HTTP dentro de la red local, con una API compatible con OpenAI. Solo transporta texto.

## Identificación automática de Mochi

El firmware publica el nombre de producto USB "Mochi". El software de la PC revisa los puertos serie, elige el que tenga ese nombre y abre la conexión. Después confirma el enlace con un saludo (`hello` / `hello_ack`). Mientras el enlace está activo, la PC envía un `ping` cada 2 segundos. Si Mochi no recibe tres seguidos, pasa al modo mascota hasta que se recupere la conexión.

## Formato de trama

Todos los mensajes viajan en tramas binarias, para poder mezclar audio y control en el mismo puerto sin confusiones:

| Campo | Tamaño | Contenido |
| :-: | :-: | :-: |
| Sincronismo | 2 bytes | `0xA5 0x5A` |
| Tipo | 1 byte | `0x01` control (JSON), `0x02` audio del micrófono, `0x03` audio de respuesta |
| Secuencia | 1 byte | Contador de 0 a 255 para detectar pérdidas |
| Longitud | 2 bytes | Tamaño de los datos, little-endian, máximo 1024 |
| Datos | N bytes | JSON en UTF-8 o audio PCM |
| CRC16 | 2 bytes | Verificación de integridad (CRC-16/CCITT) |

Audio en ambos sentidos: PCM de 16 kHz, 16 bits, mono, en bloques de 512 bytes (16 ms). Cada sentido ocupa unos 32 KB/s, muy por debajo de la capacidad del USB.

## Mensajes de control (tipo 0x01)

| Sentido | Mensaje | Ejemplo | Significado |
| :-: | :-: | :-: | :-: |
| ESP → PC | hello | `{"t":"hello","fw":"0.1","proto":1}` | Mochi se presenta |
| PC → ESP | hello_ack | `{"t":"hello_ack","proto":1}` | Enlace establecido, modo asistente |
| PC → ESP | ping / ESP → PC pong | `{"t":"ping"}` | Señal de vida |
| ESP → PC | listen_start | `{"t":"listen_start"}` | Comienza el envío de audio |
| ESP → PC | listen_end | `{"t":"listen_end","ms":3000}` | Fin del pedido de voz |
| PC → ESP | face | `{"t":"face","expr":"thinking"}` | Cambiar expresión |
| PC → ESP | say_start / say_end | `{"t":"say_start","ms":2100}` | Comienza o termina el audio de respuesta |
| ESP → PC | play_done | `{"t":"play_done"}` | Mochi terminó de hablar |
| PC → ESP | result | `{"t":"result","ok":true,"intent":"abrir_aplicacion"}` | Resultado de la acción |
| PC → ESP | config | `{"t":"config","volume":60,"brightness":80}` | Ajustes |
| Ambos | error | `{"t":"error","code":"CRC"}` | Error de trama o de proceso |

Expresiones (`expr`): `sleep`, `idle`, `listening`, `thinking`, `talking`, `happy`, `confused`, `error`.

## Secuencia de una interacción

1. Botón → ESP: `listen_start`, expresión `listening` y apagado del amplificador.
2. ESP → PC: unos 3 s de audio en tramas 0x02, luego `listen_end`.
3. PC: Vosk transcribe. Si es un atajo rápido, ejecuta y salta al paso 5.
4. PC → ESP: `face: thinking`. La notebook consulta al LLM y valida la acción.
5. PC: ejecuta la acción y sintetiza la respuesta. PC → ESP: `result`, `say_start`, audio en tramas 0x03, `say_end`.
6. ESP: expresión `talking` durante la reproducción, luego `happy` o `confused`, y finalmente `idle`.

## Notebook ↔ servidor LLM

`POST http://<ip-pc>:8080/v1/chat/completions` (llama-server). La petición incluye las instrucciones de personalidad, el catálogo de acciones y el texto transcrito. La respuesta se obliga a un esquema JSON fijo (apartado 3 6). Si el servidor no responde en 15 s, Mochi muestra `confused` e informa que no pudo procesar el pedido.


# 3 6 LLM local y catálogo de intenciones

## Selección del modelo

El servidor LLM corre en la PC con AMD RX 580 (8 GB de VRAM), Ryzen 3 3200G y 16 GB de RAM, usando llama.cpp con Vulkan. La RX 580 no es compatible con ROCm, el sistema de cómputo de AMD, por lo que se usa Vulkan.

| Rol | Modelo | VRAM aprox. | Motivo |
| :-: | :-: | :-: | :-: |
| Principal | Qwen3.5-9B Q4_K_M | ~7 GB con contexto de 8K | Modelo más reciente, buen español, uso de herramientas incorporado. |
| Respaldo | Qwen3-8B Q4_K_M | ~5,5 GB | Más margen de memoria si el principal no entra o va lento. |
| Comparación | Hermes 3 8B Q4_K_M | ~5 GB | Ajustado para llamadas a funciones; sirve como referencia. |

Ajustes del servidor:
- Contexto de 4 a 8 mil tokens.
- Opción `--jinja` activada.
- Monitor conectado a la gráfica integrada del 3200G, para liberar la VRAM de la RX 580.
- Sin el módulo de visión por ahora.

Criterio de elección: preparar entre 20 y 30 frases reales del usuario, probarlas con los tres modelos y elegir el que acierte más acciones manteniendo un tiempo de respuesta menor a 5 s.

## Formato de respuesta del LLM

El LLM responde siempre con este esquema JSON. El servidor obliga al modelo a respetarlo:

```json
{"accion": "abrir_aplicacion", "parametros": {"nombre": "spotify"}, "respuesta": "¡Listo! Abriendo Spotify."}
```

`respuesta` es la frase breve que Mochi dice con su personalidad. Si el pedido es solo conversación, `accion` vale `conversar`.

## Catálogo de intenciones y acciones

| Prioridad | Intención (`accion`) | Parámetros | Acción en la PC | Regla de seguridad |
| :-: | :-: | :-: | :-: | :-: |
| 1 | `abrir_archivo` | `nombre`, `tipo?` | Busca por nombre en carpetas configuradas y lo abre | Solo lectura; si hay varias coincidencias, pide aclaración |
| 1 | `abrir_carpeta` | `nombre` | Abre la carpeta en el explorador | Solo carpetas configuradas |
| 1 | `abrir_aplicacion` | `nombre` | Lanza un programa o juego registrado | Solo programas del catálogo |
| 1 | `multimedia` | `control`: play, pausa, siguiente, anterior | Teclas multimedia del sistema | — |
| 1 | `conversar` | — | Solo responde | Sin acción |
| 1 | `aclarar` | `pregunta`, `opciones?` | Pregunta al usuario | Sin acción |
| 1 | `cancelar` | — | Detiene el audio o la acción en curso | — |
| 2 | `minimizar_ventana` | `nombre` | Minimiza la ventana identificada | — |
| 2 | `cerrar_aplicacion` | `nombre` | Cierre normal (WM_CLOSE) | Pide confirmación; nunca fuerza el cierre |
| 2 | `buscar_web` | `texto` | Abre una búsqueda en el navegador | — |
| 2 | `volumen` | `nivel` o `cambio` | Ajusta el volumen del sistema | Límite de 0 a 100 |
| 2 | `configurar_personalidad` | `perfil`, `humor` | Cambia el perfil de respuestas | Perfiles predefinidos |
| 3 | `youtube_buscar` | `texto` | Busca y abre un video | — |
| 3 | `youtube_control` | `control`, `segundo?` | Reproducir, pausar, ir a un minuto | — |

## Atajos rápidos (sin LLM)

"pausa", "reproduce", "siguiente", "anterior", "silencio", "cancela" y "sube" o "baja el volumen" se reconocen directamente del texto de Vosk y se ejecutan en menos de 1 s, aunque el servidor LLM esté apagado.

## Reglas del validador (notebook)

- Se rechaza cualquier `accion` que no figure en el catálogo, al igual que parámetros faltantes o fuera de rango. En esos casos Mochi muestra `confused` y pide reformular.
- No existen acciones de terminal libre, ni de borrar o modificar archivos.
- Las acciones de prioridad 2 y 3 se habilitan solo después de validar las de prioridad 1.
