"""
Puente de voz ESP32 <-> Mochi Desktop (firmware Mochi 3.5).

BOOT apretado -> ESP32 -> USB -> ventana de voz (mochi_pc) -> Vosk
    -> command_service -> LLM en la PC -> herramienta -> respuesta
    -> Piper -> ventana -> ESP32 (pantalla y parlante)

Abre la ventana de voz de Mochi: ahí siguen andando las órdenes fijas
("Mochi, duerme") y el Parlante PC. Se conecta sola si hay un ESP32.

Uso (desde la carpeta de mochi-desktop):

    python -m voice_bridge.esp32_bridge
    python -m voice_bridge.esp32_bridge --port COM5
    python -m voice_bridge.esp32_bridge --echo     # sin LLM, repite lo que oyó
    python -m voice_bridge.esp32_bridge --list     # puertos disponibles
"""

import argparse
import threading
import time
import traceback
import wave
from pathlib import Path

from serial.tools import list_ports

from voice_bridge.mochi_pc.protocol import (
    LLM_DONE,
    LLM_ERROR,
    LLM_IDLE,
    LLM_SPEAKING,
    LLM_THINKING,
)
from voice_bridge.speech import (
    SpeechToText,
    TextToSpeech,
    WhisperSpeechToText,
    to_speech_text,
)


ESPRESSIF_VID = 0x303A

BRIDGE_DIR = Path(__file__).resolve().parent
DEFAULT_VOSK_MODEL = BRIDGE_DIR / "models" / "vosk-model-small-es-0.42"
DEFAULT_PIPER_VOICE = BRIDGE_DIR / "models" / "es_AR-daniela-high.onnx"

# Menos que esto (en segundos) se considera un toque accidental.
MIN_AUDIO_SECONDS = 0.3

READY_TEXT = "Listo: pulsá «Hablarle a Mochi», escribile, o mantené BOOT"

# Se agrega al final de cada orden hablada. Respuestas cortas: la
# RX 580 genera ~12 tokens/s y cada oración son segundos de audio.
#
# Ojo: una indicación del tipo "respondé en una oración" hace que
# el modelo conteste sin usar herramientas (e invente). Por eso
# aclara que las herramientas se usan igual que siempre.
VOICE_REPLY_HINT = (
    "orden dictada por voz; puede tener errores de transcripción. "
    "Usá las herramientas igual que siempre. Solo cuando termines, "
    "la respuesta final se lee en voz alta: que sea corta, sin listas "
    "ni formato"
)


# =========================================================
# PUERTO SERIE
# =========================================================

def find_esp32_port():

    for port in list_ports.comports():
        if port.vid == ESPRESSIF_VID:
            return port.device

    return None


# =========================================================
# EJECUCIÓN DE ÓRDENES
# =========================================================

def create_executor(echo):

    if echo:

        def run_echo(text):
            return {"ok": True, "text": f"Escuché: {text}"}

        return run_echo


    # Se importa recién acá: carga todo Mochi Desktop
    # (herramientas, Brain, conexión con el LLM).
    from mochi_command_service import command_service

    threading.Thread(
        target=warm_up_llm,
        args=(command_service,),
        daemon=True,
    ).start()

    def run_command(text):
        return command_service.execute(
            text=text,
            source="esp32",
            verbose=True,
            reply_hint=VOICE_REPLY_HINT,
        )

    return run_command


def warm_up_llm(command_service):
    """
    Procesa una vez el prompt de sistema + herramientas (~4.700
    tokens) para que llama-server lo deje en caché. Sin esto, la
    primera orden tarda ~25 s extra en la RX 580.
    """

    from brain.agent_loop import AGENT_SYSTEM_PROMPT

    started = time.monotonic()

    try:
        router = command_service.agent.router

        # Las mismas tools que manda el modo "auto" del router (las
        # reales + request_deep_reasoning, al final). Si difieren,
        # la caché no sirve y la primera orden tarda ~80 s.
        tools = (
            command_service.registry.get_tool_schemas()
            + [router._deep_request_tool()]
        )

        result = router.fast(
            messages=[
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": "hola"},
            ],
            tools=tools,
            options={"num_predict": 1},
        )
    except Exception as error:
        print(f"[llm] no pude precalentar: {error}")
        return

    if result.success:
        print(f"[llm] listo ({time.monotonic() - started:.1f} s)")
    else:
        print(f"[llm] no responde: {result.error}")


# =========================================================
# ASISTENTE
#
# La ventana de voz (mochi_pc) es la dueña del puerto serie. Cuando se
# suelta BOOT le pasa el audio a submit(); el asistente transcribe, le
# pide la respuesta a Mochi Desktop y la dice por el parlante de Mochi
# a través de la misma sesión (show_state, show_text, speak).
# =========================================================

class LlmAssistant:

    def __init__(self, stt, tts, execute, save_dir=None):

        self.stt = stt
        self.tts = tts
        self.execute = execute
        self.save_dir = save_dir
        self.busy = False

        self._lock = threading.Lock()


    def submit(self, session, pcm, rate):
        """Audio de BOOT o de «Hablarle a Mochi». Una orden por vez."""

        return self._start(session, self._handle_utterance, session, pcm, rate)


    def submit_text(self, session, text):
        """Orden escrita en la ventana: no pasa por la voz a texto."""

        return self._start(session, self._handle_text, session, text)


    def _start(self, session, target, *args):

        with self._lock:
            if self.busy:
                return False
            self.busy = True

        # Mientras escucha y responde, el Parlante PC queda en pausa.
        session.hold_music(True)
        threading.Thread(
            target=self._run,
            args=(session, target, args),
            daemon=True,
        ).start()
        return True


    def _run(self, session, target, args):

        try:
            target(*args)
        except Exception:
            traceback.print_exc()
            session.show_state(LLM_ERROR)
            session.show_text("Error interno del puente")
            session.post("llm", "Error interno (ver la terminal)")
        finally:
            session.hold_music(False)
            self.busy = False


    def _speak(self, session, text):

        if not text or self.tts is None:
            return

        speech = to_speech_text(text)

        if not speech:
            return

        session.speak(self.tts.synthesize(speech), self.tts.sample_rate)


    def _handle_utterance(self, session, pcm, rate):

        seconds = len(pcm) / 2 / rate

        if seconds < MIN_AUDIO_SECONDS:
            session.show_state(LLM_IDLE)
            session.post("llm", READY_TEXT)
            return

        if self.save_dir:
            self._save_wav(pcm, rate)

        session.show_state(LLM_THINKING)
        session.post("llm", "Entendiendo…")

        started = time.monotonic()
        text = self.stt.transcribe(pcm, rate)
        print(f"\n[voz] {seconds:.1f} s -> \"{text}\" ({time.monotonic() - started:.2f} s)")

        if not text:
            session.show_state(LLM_ERROR)
            session.show_text("No te entendi")
            session.post("llm", "No te entendí. Probá de nuevo.")
            self._speak(session, "No te entendí.")
            return

        self._handle_text(session, text)


    def _handle_text(self, session, text):

        session.show_state(LLM_THINKING)
        session.show_text(f"> {text}")
        session.post("transcript", f"LLM: {text}")
        session.post("llm", f"Pensando: «{text}»")

        started = time.monotonic()
        result = self.execute(text)
        reply = (result.get("text") or "").strip()
        print(f"[mochi] {result.get('status')} -> \"{reply}\" ({time.monotonic() - started:.2f} s)")

        if reply:
            session.show_text(to_speech_text(reply))
            session.show_state(LLM_SPEAKING)
            session.post("llm", f"Responde: {to_speech_text(reply)}")
            session.post("log", f"LLM: {reply}")
            self._speak(session, reply)

        session.show_state(LLM_DONE if result.get("ok") else LLM_ERROR)
        session.post("llm", READY_TEXT)


    def _save_wav(self, pcm, rate):

        self.save_dir.mkdir(parents=True, exist_ok=True)
        path = self.save_dir / time.strftime("mochi-%Y%m%d-%H%M%S.wav")

        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm)

        print(f"[voz] guardado {path}")


# =========================================================
# MAIN
# =========================================================

def main():

    parser = argparse.ArgumentParser(description="Puente de voz ESP32 <-> Mochi")
    parser.add_argument("--port", help="puerto serie del ESP32 (por defecto, autodetectar)")
    parser.add_argument("--list", action="store_true", help="listar puertos serie y salir")
    parser.add_argument("--echo", action="store_true", help="no ejecutar órdenes: solo repetir lo que oyó")
    parser.add_argument("--no-tts", action="store_true", help="no hablar, solo mostrar texto")
    parser.add_argument("--stt", choices=["vosk", "whisper"], default="vosk", help="motor de voz a texto")
    parser.add_argument("--whisper-model", default="small", help="tamaño de Whisper: base, small...")
    parser.add_argument("--vosk-model", default=str(DEFAULT_VOSK_MODEL))
    parser.add_argument("--piper-voice", default=str(DEFAULT_PIPER_VOICE))
    parser.add_argument("--save-wav", metavar="CARPETA", help="guardar cada grabación (para ajustar el micrófono)")
    args = parser.parse_args()

    if args.list:
        for port in list_ports.comports():
            vid = f"{port.vid:04X}" if port.vid is not None else "----"
            print(f"{port.device}  VID={vid}  {port.description}")
        return

    # Sin ESP32 conectado igual abre la ventana: se elige el puerto ahí.
    port = args.port or find_esp32_port()

    if args.stt == "whisper":
        print(f"Cargando Whisper: {args.whisper_model}")
        stt = WhisperSpeechToText(args.whisper_model)
    else:
        print(f"Cargando Vosk: {args.vosk_model}")
        stt = SpeechToText(args.vosk_model)

    tts = None
    if not args.no_tts:
        try:
            print(f"Cargando Piper: {args.piper_voice}")
            tts = TextToSpeech(args.piper_voice)
        except Exception as error:
            print(f"Sin voz ({error}). Sigo solo con texto en pantalla.")

    execute = create_executor(args.echo)

    assistant = LlmAssistant(
        stt,
        tts,
        execute,
        save_dir=Path(args.save_wav) if args.save_wav else None,
    )

    # Las órdenes fijas usan el mismo modelo chico de Vosk (si es ese).
    window_model = args.vosk_model if (Path(args.vosk_model) / "am" / "final.mdl").is_file() else None

    from voice_bridge.mochi_pc.app import run

    print("Abriendo la ventana de Mochi: «Hablarle a Mochi», escribirle, o mantener BOOT.")
    print("En Linux, si no abre el puerto: regla udev de linux/99-mochi-esp32.rules (ver README.md).")

    try:
        run(port, window_model, assistant=assistant, autoconnect=port is not None)
    except KeyboardInterrupt:
        print("\nCerrando puente.")


if __name__ == "__main__":
    main()
