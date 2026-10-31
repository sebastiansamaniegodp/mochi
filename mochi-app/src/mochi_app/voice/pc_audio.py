"""Sin placa: Mochi escucha por el micrófono de la PC y habla por sus parlantes.

PcAudioSession tiene lo que el asistente usa de la sesión USB del ESP32
(post, show_state, show_text, hold_music, speak), así el mismo asistente
sirve con y sin placa. La grabación termina sola al dejar de hablar, con
los mismos tiempos que «Hablarle a Mochi» con la placa.

Con la escucha de «Mochi» activada (set_wake), el micrófono queda abierto
y Vosk, con la gramática de las órdenes fijas, espera la llamada; al oírla
empieza a escuchar la orden como con el botón, sin perder lo dicho justo
después de «Mochi».
"""

from collections import deque
import json
import threading
import time

import numpy as np

from mochi_app.device.commands import (
    DEFAULT_WAKE,
    DEFAULT_WAKE_PHRASES,
    WAKE_LLM_CONFIDENCE,
    AudioTail,
    mic_sensitivity,
    CommandRouter,
    recognition_grammar,
    wake_call,
)
from mochi_app.device.pc_speaker import MOCHI_SINK
from mochi_app.device.protocol import LLM_IDLE, LLM_LISTENING
from mochi_app.device.session import (
    END_SILENCE_SECONDS,
    NOISE_FACTOR,
    NO_SPEECH_SECONDS,
    PTT_MAX_SECONDS,
    VOICE_RMS,
)


RATE = 16000
BLOCK = 320                 # 20 ms

# El micrófono de la notebook no es el INMP441: el umbral de voz sube si
# hay ruido de fondo (el mínimo de los últimos 2 s, como con la placa).
# La saturación se avisa después del primer medio segundo.
CALIBRATION_SECONDS = 0.5

# Con este ruido de fondo el micrófono está saturado (ganancia al máximo):
# la voz no se distingue. Pasa con el micrófono interno de la notebook con
# "Internal Mic Boost" al máximo.
SATURATED_NOISE = 0.12
SATURATED_WARNING = (
    "El micrófono de la PC está saturado: solo se oye ruido. Bajá su volumen "
    "en el sonido del sistema (icono de volumen de KDE, micrófono a ~40 %) "
    "o elegí otro micrófono en la configuración de Mochi."
)

# Escucha de «Mochi»: el ruido de fondo es el mínimo de los últimos 2 s y,
# si el micrófono falla, se reintenta cada tanto.
NOISE_HISTORY_BLOCKS = 100
WAKE_RETRY_SECONDS = 5.0
VOSK_CHUNK = 2560           # bytes por llamada a Vosk (80 ms), como con la placa


# =========================================================
# DISPOSITIVOS DE LA PC
# =========================================================

def microphones():
    """[(id, nombre)] de los micrófonos (sin los monitores de salida)."""

    import soundcard as sc
    return [(mic.id, mic.name) for mic in sc.all_microphones()]


def speakers():
    """[(id, nombre)] de las salidas, sin la salida «Mochi» (va a la placa)."""

    import soundcard as sc
    return [(out.id, out.name) for out in sc.all_speakers() if out.id != MOCHI_SINK]


def _open_microphone(mic_id):

    import soundcard as sc

    if mic_id:
        try:
            return sc.get_microphone(mic_id)
        except (IndexError, RuntimeError):
            pass

    return sc.default_microphone()


def _open_speaker(speaker_id):

    import soundcard as sc

    if speaker_id:
        try:
            return sc.get_speaker(speaker_id)
        except (IndexError, RuntimeError):
            pass

    default = sc.default_speaker()

    # Si la salida predeterminada es «Mochi» (sink nulo que va a la placa),
    # sin placa no se oiría nada.
    if default.id == MOCHI_SINK:
        others = [out for out in sc.all_speakers() if out.id != MOCHI_SINK]
        if others:
            return others[0]

    return default


def to_pcm16(block):

    return (np.clip(block, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def voice_in_float(pcm, threshold):
    """(¿hay voz?, segundos de silencio al final) en bloques de 20 ms."""

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    heard = False
    quiet = 0.0

    for start in range(0, len(samples) - BLOCK + 1, BLOCK):
        block = samples[start:start + BLOCK]
        if float(np.sqrt(np.mean(block * block))) > threshold:
            heard = True
            quiet = 0.0
        else:
            quiet += BLOCK / RATE

    return heard, quiet


# =========================================================
# SESIÓN
# =========================================================

class PcAudioSession:

    def __init__(self, events, assistant, config=None):

        self.events = events
        self.assistant = assistant
        self.config = config or {}
        self.microphone_id = None
        self.speaker_id = None

        self._lock = threading.Lock()
        self._finish = None         # threading.Event mientras graba
        self._requested = False     # el botón pidió escuchar (lo atiende el hilo de «Mochi»)
        self._hush = threading.Event()
        self._warned_noise = False

        # Escucha de «Mochi»
        self._wake_thread = None
        self._wake_stop = threading.Event()
        self._wake_model_path = None
        self._wake_model = None


    @property
    def wake_active(self):

        return self._wake_thread is not None and self._wake_thread.is_alive()


    @property
    def ready_text(self):

        if self.wake_active:
            return "Listo: decí «Mochi», pulsá «Hablarle a Mochi» o escribile"
        return "Listo: pulsá «Hablarle a Mochi» o escribile"


    def post(self, kind, value):

        self.events.put((kind, value))


    # Con la placa van a su pantalla; acá la cara la muestra la ventana.
    def show_state(self, state): pass
    def show_text(self, text): pass
    def hold_music(self, active): pass


    @property
    def listening(self):

        return self._finish is not None


    def listen_toggle(self):
        """Empieza a escuchar o, si ya escucha, manda lo grabado."""

        with self._lock:
            if self._finish is not None:
                self._finish.set()
                return

            if self.assistant.busy:
                self.post("log", "Mochi todavía está respondiendo la orden anterior.")
                return

            self._finish = threading.Event()
            finish = self._finish

            # Con el micrófono ya abierto, lo usa el hilo de «Mochi».
            if self.wake_active:
                self._requested = True
                return

        threading.Thread(target=self._listen, args=(finish,), daemon=True).start()


    def hush(self):
        """Corta la voz que está sonando por los parlantes de la PC."""

        self._hush.set()


    def cancel(self):

        self._hush.set()
        with self._lock:
            if self._finish is not None:
                self._finish.set()


    # -----------------------------------------------------
    # Escuchar
    # -----------------------------------------------------

    def _listen(self, finish, recorder=None, pre=b"", noise=None):

        self._hush.clear()
        self.post("listening", True)
        self.post("face", LLM_LISTENING)
        self.post("llm", "Escuchando… (se envía solo cuando dejás de hablar)")

        try:
            pcm, heard = self._record(finish, recorder, pre, noise)
        except Exception as error:
            pcm, heard = None, False
            self.post("log", f"No pude usar el micrófono de la PC: {error}")
            self.post("chat", ("notice", "No pude usar el micrófono de la PC. Revisalo en la configuración."))
        finally:
            with self._lock:
                self._finish = None
            self.post("listening", False)
            self.post("level", 0.0)

        if pcm is None or self._hush.is_set():
            self.post("face", LLM_IDLE)
            self.post("llm", self.ready_text)
            return

        if not heard and not finish.is_set():
            self.post("face", LLM_IDLE)
            self.post("llm", "No escuché nada. Decí «Mochi» o pulsá «Hablarle a Mochi» y hablá cerca del micrófono."
                      if self.wake_active else
                      "No escuché nada. Pulsá «Hablarle a Mochi» y hablá cerca del micrófono.")
            return

        self.assistant.submit(self, pcm, RATE)


    def _record(self, finish, recorder=None, pre=b"", noise=None):
        """
        Graba hasta el silencio final, «Enviar ya» o el máximo.

        Desde la escucha de «Mochi» llegan el micrófono ya abierto, lo
        dicho después de la llamada (pre) y el ruido de fondo medido.
        """

        if recorder is None:
            microphone = _open_microphone(self.microphone_id)
            print(f"[pc] micrófono: {microphone.name}")
            with microphone.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as recorder:
                return self._record_from(recorder, finish, pre, noise)

        return self._record_from(recorder, finish, pre, noise)


    def _record_from(self, recorder, finish, pre, noise):

        pcm = bytearray(pre)
        started = time.monotonic()
        last_voice = started
        heard = False

        # Ruido de fondo: el mínimo de los últimos 2 s (sigue a un ventilador
        # o a música que empieza a sonar mientras se habla).
        levels = deque([noise] if noise is not None else [], maxlen=NOISE_HISTORY_BLOCKS)

        # Sensibilidad: el umbral también sigue a la voz más fuerte oída.
        factor, fraction = mic_sensitivity(self.config)
        peak = 0.0

        def threshold():
            base = max(VOICE_RMS, min(levels) * NOISE_FACTOR) if levels else VOICE_RMS
            return max(base * factor, peak * fraction)

        if pre:
            heard, quiet = voice_in_float(pre, threshold())
            last_voice = started - quiet

        while not finish.is_set():
            block = recorder.record(numframes=BLOCK)[:, 0]
            now = time.monotonic()

            rms = float(np.sqrt(np.mean(block * block))) if block.size else 0.0
            self.post("level", min(100.0, rms * 1500))
            pcm += to_pcm16(block)
            levels.append(rms)

            if now - started >= CALIBRATION_SECONDS and min(levels) > SATURATED_NOISE:
                self._warn_saturated()

            if rms > threshold():
                heard = True
                last_voice = now
                peak = max(peak, rms)

            if heard and now - last_voice > END_SILENCE_SECONDS:
                print(f"[pc] fin de la voz: ruido {min(levels):.4f}, umbral {threshold():.4f}")
                break
            if not heard and now - started > NO_SPEECH_SECONDS:
                break
            if now - started > PTT_MAX_SECONDS:
                self.post("log", f"Hablar con el LLM: máximo {PTT_MAX_SECONDS:g} segundos.")
                break

        return bytes(pcm), heard


    def _warn_saturated(self):

        if not self._warned_noise:
            self._warned_noise = True
            self.post("log", SATURATED_WARNING)
            self.post("chat", ("notice", SATURATED_WARNING))


    # -----------------------------------------------------
    # Escucha de «Mochi» (micrófono siempre abierto)
    # -----------------------------------------------------

    def set_wake(self, enabled, model_path=None):
        """Abre o cierra el micrófono para esperar «Mochi»."""

        if model_path:
            self._wake_model_path = model_path

        if not enabled:
            self._wake_stop.set()
            return

        # Andando (o cerrándose, por ejemplo mientras graba una orden): sigue.
        if self.wake_active:
            self._wake_stop.clear()
            return

        if not self._wake_model_path:
            self.post("log", "Escucha de «Mochi»: falta el modelo chico de Vosk.")
            return

        self._wake_stop = threading.Event()
        self._wake_thread = threading.Thread(target=self._wake_loop, args=(self._wake_stop,), daemon=True)
        self._wake_thread.start()


    def _wake_loop(self, stop):

        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel

            if self._wake_model is None:
                SetLogLevel(-1)
                self._wake_model = Model(str(self._wake_model_path))
            model = self._wake_model

            words = self.config.get("wake_words", DEFAULT_WAKE)
            phrases = self.config.get("wake_phrases", DEFAULT_WAKE_PHRASES)
            grammar = json.dumps(recognition_grammar(model, words, phrases, llm_words=True), ensure_ascii=False)
            router = CommandRouter(words, phrases, self.config.get("wake_window_seconds", 5))
            recognizer = KaldiRecognizer(model, RATE, grammar)
            recognizer.SetWords(True)
        except Exception as error:
            self.post("log", f"Escucha de «Mochi» desactivada: {error}")
            return

        self.post("log", "Escucha de «Mochi» activa con el micrófono de la PC.")
        self.post("llm", self.ready_text)
        warned = False

        while not stop.is_set():
            mic_id = self.microphone_id
            try:
                microphone = _open_microphone(mic_id)
                print(f"[pc] escucha de «Mochi» con: {microphone.name}")
                with microphone.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as recorder:
                    self._wake_listen(stop, recorder, recognizer, router, lambda: self.microphone_id != mic_id)
                warned = False
            except Exception as error:
                if not warned:
                    warned = True
                    self.post("log", f"Escucha de «Mochi»: no pude usar el micrófono de la PC ({error}). Reintento.")
                stop.wait(WAKE_RETRY_SECONDS)

        print("[pc] escucha de «Mochi» cerrada")
        if not self.listening:
            self.post("llm", self.ready_text)


    def _wake_listen(self, stop, recorder, recognizer, router, mic_changed):

        tail = AudioTail()
        levels = deque(maxlen=NOISE_HISTORY_BLOCKS)
        chunk = bytearray()

        def restart():
            recognizer.Reset()
            router.reset()
            tail.reset()
            chunk.clear()

        while not stop.is_set() and not mic_changed():

            # El botón «Hablarle a Mochi» con el micrófono ya abierto.
            with self._lock:
                finish = self._finish if self._requested else None
                self._requested = False
            if finish is not None:
                self._listen(finish, recorder, noise=min(levels) if levels else None)
                restart()
                continue

            block = recorder.record(numframes=BLOCK)[:, 0]

            # Mientras Mochi piensa o habla (por los parlantes de la PC) no
            # se busca la llamada: se oiría a sí mismo.
            if self.assistant.busy or self.listening:
                if tail.data or chunk:
                    restart()
                continue

            rms = float(np.sqrt(np.mean(block * block))) if block.size else 0.0
            levels.append(rms)
            if len(levels) == levels.maxlen and min(levels) > SATURATED_NOISE:
                self._warn_saturated()

            chunk += to_pcm16(block)
            if len(chunk) < VOSK_CHUNK:
                continue
            data = bytes(chunk)
            chunk.clear()
            tail.extend(data)

            if not recognizer.AcceptWaveform(data):
                continue

            result = json.loads(recognizer.Result())
            text = result.get("text", "")
            if not text:
                continue

            decision = router.process(text)
            end, confidence = wake_call(result, decision)

            # Sin placa no hay órdenes fijas: lo que siga a «Mochi» va al LLM.
            if end is None:
                continue

            if confidence < WAKE_LLM_CONFIDENCE:
                print(f"[pc] llamada dudosa: «{text}» ({confidence:.2f})")
                continue

            print(f"[pc] oí «{text}» ({confidence:.2f}): escucho la orden")
            pre = tail.since(end)

            with self._lock:
                if self._finish is not None or self.assistant.busy:
                    continue
                self._finish = threading.Event()
                finish = self._finish

            self._listen(finish, recorder, pre, noise=min(levels) if levels else None)
            restart()


    # -----------------------------------------------------
    # Hablar
    # -----------------------------------------------------

    def speak(self, pcm, rate):
        """Dice la respuesta por los parlantes de la PC (espera a que termine)."""

        self._hush.clear()
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768

        try:
            speaker = _open_speaker(self.speaker_id)
            step = rate // 10
            with speaker.player(samplerate=rate, channels=1) as player:
                for start in range(0, len(samples), step):
                    if self._hush.is_set():
                        break
                    player.play(samples[start:start + step])
        except Exception as error:
            self.post("log", f"No pude usar los parlantes de la PC: {error}")
            return False

        return True
