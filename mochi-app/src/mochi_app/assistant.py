"""El asistente del LLM: voz -> texto -> agente de Mochi -> respuesta -> voz.

Es el LlmAssistant del puente de voz (voice_bridge/esp32_bridge.py). Anda
con cualquier "salida" que tenga post, show_state, show_text, hold_music y
speak:

- con la placa: la sesión USB (device.session.VoiceSession). La cara y el
  texto van a la pantalla de Mochi y la voz a su parlante;
- sin placa: el micrófono y los parlantes de la PC (voice.pc_audio).

Además de los eventos que ya mandaba el puente, le avisa a la ventana:

    ("face", estado)                LLM_IDLE ... LLM_ERROR (device.protocol)
    ("chat", (quién, texto))        quién: "user", "mochi" o "notice"
    ("llm_status", (nivel, texto))  nivel: "loading", "ok", "error" u "off"
    ("assistant_ready", None)       ya cargó la voz y el agente
"""

import json
import threading
import time
import traceback
import wave
from pathlib import Path

from mochi_app import paths
from mochi_app.device.protocol import (
    LLM_DONE,
    LLM_ERROR,
    LLM_IDLE,
    LLM_SPEAKING,
    LLM_THINKING,
)
from mochi_app.voice.speech import (
    SpeechToText,
    TextToSpeech,
    WhisperSpeechToText,
    to_speech_text,
)


# Menos que esto (en segundos) se considera un toque accidental.
MIN_AUDIO_SECONDS = 0.3

READY_TEXT = "Listo: pulsá «Hablarle a Mochi», escribile, o mantené BOOT"

# Se agrega al final de cada orden. Respuestas cortas: la RX 580 genera
# ~12 tokens/s y cada oración son segundos de audio.
#
# Ojo: una indicación del tipo "respondé en una oración" hace que el
# modelo conteste sin usar herramientas (e invente). Por eso aclara que
# las herramientas se usan igual que siempre. Va al final del mensaje del
# usuario (no en el sistema) para no romper la caché de llama-server.
VOICE_REPLY_HINT = (
    "orden dictada por voz; puede tener errores de transcripción. "
    "Usá las herramientas igual que siempre. Solo cuando termines, "
    "la respuesta final se lee en voz alta: que sea corta, sin listas "
    "ni formato"
)

# La fecha y la hora también van al final de la orden (el modelo no tiene
# reloj). En el prompt de sistema cambiarían en cada petición y romperían
# la caché.
WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def now_hint():

    now = time.localtime()
    return (
        f"dato real del reloj de la computadora: hoy es "
        f"{WEEKDAYS[now.tm_wday]} {now.tm_mday} de "
        f"{MONTHS[now.tm_mon - 1]} de {now.tm_year} y son las "
        f"{now.tm_hour:02d}:{now.tm_min:02d}; si preguntan la hora o la "
        f"fecha, respondé con este dato"
    )


# Si el precalentamiento falla (llama-server apagado), se reintenta.
WARM_UP_RETRY_SECONDS = 30

# Cada cuánto se fija si ya pasó media hora sin órdenes
# (agente/conversation_memory.py).
MEMORY_CHECK_SECONDS = 30


# =========================================================
# CONFIGURACIÓN DEL LLM (~/.mochi/config.json, sección brain)
# =========================================================

def llm_configured():

    try:
        config = json.loads((Path.home() / ".mochi" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    brain = config.get("brain") or {}
    return bool(brain.get("enabled") and brain.get("model"))


# =========================================================
# PRECALENTAR LA CACHÉ DEL LLM
# =========================================================

def warm_up_llm(command_service):
    """
    Procesa una vez el prompt de sistema + herramientas (~4.850 tokens)
    para que llama-server lo deje en caché. Sin esto, la primera orden
    tarda ~80 s en la RX 580. Devuelve (ok, texto).

    Si hay una conversación (después de compactarla), va también:
    así la orden siguiente no tiene que procesarla.
    """

    from brain.agent_loop import AGENT_SYSTEM_PROMPT

    memory = command_service.memory
    history = memory.messages()
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
            messages=(
                [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
                + history
                + [{"role": "user", "content": "hola"}]
            ),
            tools=tools,
            options={"num_predict": 1},
        )
    except Exception as error:
        print(f"[llm] no pude precalentar: {error}")
        return False, str(error)

    if result.success:
        seconds = time.monotonic() - started
        print(f"[llm] listo ({seconds:.1f} s)")

        # Lo que mide llama-server, para saber cuándo compactar.
        if not history:
            usage = (result.raw or {}).get("usage") or {}
            memory.base_tokens = usage.get("prompt_tokens") or memory.base_tokens

        context_size = getattr(router.provider, "context_size", None)
        context_size = context_size() if context_size else None
        memory.context_size = context_size or memory.context_size

        return True, f"{seconds:.1f} s"

    print(f"[llm] no responde: {result.error}")
    return False, str(result.error)


# =========================================================
# ASISTENTE
# =========================================================

class Assistant:

    def __init__(self, events, save_dir=None):

        self.events = events
        self.save_dir = save_dir
        self.speak_replies = True
        # Con la placa: si es la sesión de la PC, la voz sale por los
        # parlantes de la PC y no por el de Mochi (lo pone el controlador).
        self.voice_on_pc = None

        self.stt = None
        self.tts = None
        self.execute = None
        self.ready = False
        self.busy = False

        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._command_service = None
        self._warm_request = threading.Event()


    def post(self, kind, value):

        self.events.put((kind, value))


    # -----------------------------------------------------
    # Carga (en otro hilo: la ventana se abre enseguida)
    # -----------------------------------------------------

    def load(self, stt="vosk", whisper_model="small", vosk_model=None,
             piper_voice=None, echo=False, tts=True):

        threading.Thread(
            target=self._load,
            args=(stt, whisper_model, vosk_model, piper_voice, echo, tts),
            daemon=True,
        ).start()


    def _load(self, stt, whisper_model, vosk_model, piper_voice, echo, tts):

        vosk_model = vosk_model or paths.VOSK_MODEL
        piper_voice = piper_voice or paths.PIPER_VOICE

        try:
            if stt == "whisper":
                self.post("log", f"Cargando Whisper ({whisper_model})…")
                self.stt = WhisperSpeechToText(whisper_model)
            else:
                self.stt = SpeechToText(vosk_model)
        except Exception as error:
            traceback.print_exc()
            self.post("log", f"Sin voz a texto ({error}): solo se puede escribir.")

        if tts:
            try:
                self.tts = TextToSpeech(piper_voice)
            except Exception as error:
                traceback.print_exc()
                self.post("log", f"Sin voz ({error}): Mochi solo contesta con texto.")

        if echo:
            self.execute = lambda text: {"ok": True, "text": f"Escuché: {text}"}
            self.post("llm_status", ("ok", "modo eco (sin LLM)"))
        else:
            self.execute = self._load_agent()

        self.ready = True
        self.post("assistant_ready", None)


    def _load_agent(self):

        if not llm_configured():
            self.post("llm_status", ("off", "sin configurar"))
            return self._not_configured

        self.post("llm_status", ("loading", "cargando el agente…"))

        try:
            # Se importa recién acá: carga todo Mochi Desktop
            # (herramientas, Brain, conexión con el LLM).
            paths.enable_agent_imports()
            from mochi_command_service import command_service
        except Exception as error:
            traceback.print_exc()
            self.post("llm_status", ("error", "no cargó el agente"))
            self.post("log", f"No pude cargar el agente de Mochi: {error}")
            return self._not_configured

        self._command_service = command_service

        threading.Thread(target=self._keep_warm, daemon=True).start()
        threading.Thread(target=self._watch_memory, daemon=True).start()

        def run_command(text):
            return command_service.execute(
                text=text,
                source="esp32",
                verbose=True,
                reply_hint=f"{now_hint()}. {VOICE_REPLY_HINT}",
            )

        return run_command


    def _not_configured(self, text):

        return {
            "ok": False,
            "text": "Todavía no tengo configurado el servidor del LLM. "
                    "Abrí la configuración y poné su dirección y su clave.",
        }


    def _keep_warm(self):
        """Precalienta al arrancar; si el LLM no responde, reintenta."""

        while True:
            self.post("llm_status", ("loading", "precalentando…"))
            ok, detail = warm_up_llm(self._command_service)

            if ok:
                self.post("llm_status", ("ok", f"listo ({detail})"))
                self._warm_request.wait()
            else:
                self.post("llm_status", ("error", "no responde"))
                self.post("log", f"El LLM no responde: {detail}")
                self._warm_request.wait(WARM_UP_RETRY_SECONDS)

            self._warm_request.clear()


    def rewarm(self):
        """Precalentar otra vez (por ejemplo, si se reinició llama-server)."""

        if self._command_service is not None:
            self._warm_request.set()


    # -----------------------------------------------------
    # Memoria de la conversación (agente/conversation_memory.py)
    # -----------------------------------------------------

    def _watch_memory(self):
        """Después de media hora sin órdenes, la conversación se olvida."""

        while True:
            time.sleep(MEMORY_CHECK_SECONDS)

            if self.busy or not self._command_service.memory.expired():
                continue

            self._forget("Pasó media hora sin hablarnos: empiezo una conversación nueva.")


    def forget(self):
        """Nuevo chat (botón de la ventana, que ya vacía la conversación)."""

        if self._command_service is not None:
            threading.Thread(target=self._forget, daemon=True).start()


    def _forget(self, notice=None):

        # Espera a que termine la orden en curso, si hay una.
        if not self._command_service.forget():
            return

        print(f"[memoria] {notice or 'nuevo chat'}")

        if notice:
            self.post("chat", ("notice", notice))

        # Cambió el comienzo de la conversación: que la orden
        # siguiente encuentre la caché lista.
        self.rewarm()


    def _check_memory(self):
        """Después de cada orden: si la conversación llegó al 80 %, se resume."""

        if self._command_service is None:
            return

        memory = self._command_service.memory
        print(f"[memoria] {memory.describe()}")

        if memory.needs_compaction():
            threading.Thread(target=self._compact, daemon=True).start()


    def _compact(self):

        memory = self._command_service.memory

        try:
            # Mientras resume, la orden siguiente espera.
            if not self._command_service.compact_memory():
                return
        except Exception:
            traceback.print_exc()
            return

        print(f"[memoria] compactada: {memory.describe()}")
        self.post("chat", (
            "notice",
            f"La conversación llegó al 80 % de mi memoria: resumí lo más "
            f"viejo (ahora ocupa el {round(memory.usage() * 100)} %).",
        ))
        self.rewarm()


    # -----------------------------------------------------
    # Órdenes
    # -----------------------------------------------------

    def submit(self, session, pcm, rate):
        """Audio de BOOT o de «Hablarle a Mochi». Una orden por vez."""

        return self._start(session, self._handle_utterance, session, pcm, rate)


    def submit_text(self, session, text):
        """Orden escrita en la ventana: no pasa por la voz a texto."""

        return self._start(session, self._handle_text, session, text)


    def _start(self, session, target, *args):

        if not self.ready:
            self._state(session, LLM_ERROR)
            session.show_text("Cargando, espera")
            self.post("chat", ("notice", "Todavía estoy cargando, probá en unos segundos."))
            return False

        with self._lock:
            if self.busy:
                return False
            self.busy = True
            self._cancelled.clear()

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
            self._state(session, LLM_ERROR)
            session.show_text("Error interno del puente")
            session.post("llm", "Error interno (ver el registro)")
            self.post("chat", ("notice", "Hubo un error interno (ver el registro)."))
        finally:
            session.hold_music(False)
            self.busy = False
            self._check_memory()


    # -----------------------------------------------------
    # Cancelar
    # -----------------------------------------------------

    def cancel(self):
        """
        Corta la orden en curso (entendiendo, pensando o hablando) y
        descarta lo que salga de ella. Devuelve False si no había ninguna.
        La voz la corta quien llama (session.stop_speaking()).
        """

        with self._lock:
            if not self.busy:
                return False
            self._cancelled.set()

        # La voz por los parlantes de la PC se corta acá (la de la placa no).
        if self.voice_on_pc is not None:
            self.voice_on_pc.hush()

        # Si ya está en el LLM, corta la petición y las herramientas que faltan.
        if self._command_service is not None:
            import cancellation
            cancellation.cancel()

        return True


    @property
    def cancelled(self):

        return self._cancelled.is_set()


    def _cancel_done(self, session):
        """Deja todo como antes de la orden: la cara quieta y listo para otra."""

        print("[mochi] orden cancelada")
        self._state(session, LLM_IDLE)
        session.show_text("Cancelado")
        session.post("llm", self._ready_text(session))
        self.post("chat", ("notice", "Cancelado. Podés repetirlo."))


    def _state(self, session, state):

        session.show_state(state)
        self.post("face", state)


    def _ready_text(self, session):

        return getattr(session, "ready_text", READY_TEXT)


    def _speak(self, session, text):

        if not text or self.tts is None or not self.speak_replies:
            return

        speech = to_speech_text(text)

        if not speech:
            return

        audio = self.tts.synthesize(speech)

        # Se canceló mientras Piper sintetizaba.
        if self.cancelled:
            return

        (self.voice_on_pc or session).speak(audio, self.tts.sample_rate)


    def _handle_utterance(self, session, pcm, rate):

        seconds = len(pcm) / 2 / rate

        if seconds < MIN_AUDIO_SECONDS:
            self._state(session, LLM_IDLE)
            session.post("llm", self._ready_text(session))
            return

        if self.save_dir:
            self._save_wav(pcm, rate)

        self._state(session, LLM_THINKING)
        session.post("llm", "Entendiendo…")

        if self.stt is None:
            self._state(session, LLM_ERROR)
            session.post("llm", "No tengo el reconocimiento de voz: escribile.")
            self.post("chat", ("notice", "No cargó el reconocimiento de voz: por ahora, escribime."))
            return

        started = time.monotonic()
        text = self.stt.transcribe(pcm, rate)
        print(f"\n[voz] {seconds:.1f} s -> \"{text}\" ({time.monotonic() - started:.2f} s)")

        if self.cancelled:
            self._cancel_done(session)
            return

        if not text:
            self._state(session, LLM_ERROR)
            session.show_text("No te entendi")
            session.post("llm", "No te entendí. Probá de nuevo.")
            self.post("chat", ("notice", "No te entendí. Probá de nuevo."))
            self._speak(session, "No te entendí.")
            return

        # «Mochi» ... «duerme» dicho en dos veces: la llamada abrió la
        # escucha del LLM, pero es una orden fija de la placa.
        fixed_command = getattr(session, "fixed_command", None)

        if fixed_command is not None and fixed_command(text):
            self._state(session, LLM_IDLE)
            session.post("llm", self._ready_text(session))
            return

        self._handle_text(session, text)


    def _handle_text(self, session, text):

        self._state(session, LLM_THINKING)
        session.show_text(f"> {text}")
        session.post("transcript", f"LLM: {text}")
        session.post("llm", f"Pensando: «{text}»")
        self.post("chat", ("user", text))

        if self.cancelled:
            self._cancel_done(session)
            return

        started = time.monotonic()
        result = self.execute(text)

        if self.cancelled:
            self._cancel_done(session)
            return

        reply = (result.get("text") or "").strip()
        print(f"[mochi] {result.get('status')} -> \"{reply}\" ({time.monotonic() - started:.2f} s)")

        if reply:
            session.show_text(to_speech_text(reply))
            self._state(session, LLM_SPEAKING)
            session.post("llm", f"Responde: {to_speech_text(reply)}")
            session.post("log", f"LLM: {reply}")
            self.post("chat", ("mochi", reply))
            self._speak(session, reply)

            if self.cancelled:
                self._cancel_done(session)
                return

        self._state(session, LLM_DONE if result.get("ok") else LLM_ERROR)
        session.post("llm", self._ready_text(session))


    def _save_wav(self, pcm, rate):

        self.save_dir.mkdir(parents=True, exist_ok=True)
        path = self.save_dir / time.strftime("mochi-%Y%m%d-%H%M%S.wav")

        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm)

        print(f"[voz] guardado {path}")
