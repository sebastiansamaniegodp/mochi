"""Une la ventana con la placa, el audio de la PC y el asistente.

Todo lo que pasa en los hilos (sesión USB, asistente, micrófono de la PC)
llega por una sola cola de eventos (tipo, valor), igual que en la ventana
Tk; un QTimer la vacía y la pasa a la ventana con la señal `event`.

Con la placa conectada, hablar y escribir van por la placa (su micrófono,
su pantalla y su parlante). Sin placa, por el micrófono y los parlantes de
la PC. La placa se conecta sola al enchufarla.

Con «Escuchar siempre» el micrófono queda abierto y «Mochi» abre la
escucha de la orden: el de la placa (VoiceSession.wake_llm) o, sin placa,
el de la PC (PcAudioSession.set_wake).
"""

import json
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from serial.tools import list_ports

from mochi_app import paths, settings
from mochi_app.assistant import Assistant
from mochi_app.device.audio_dsp import AudioControls
from mochi_app.device.pc_speaker import audio_outputs, ensure_mochi_sink, remove_mochi_sink, MOCHI_SINK
from mochi_app.device.session import VoiceSession
from mochi_app.voice.pc_audio import PcAudioSession


ESPRESSIF_VID = 0x303A

DEVICE_CONFIG = paths.PACKAGE_DIR / "device" / "config.json"

PORT_WATCH_MS = 1500
EVENTS_MS = 40


def find_esp32_ports():

    return sorted(port.device for port in list_ports.comports() if port.vid == ESPRESSIF_VID)


def list_serial_ports():
    """[(puerto, descripción, es_esp32, es_usb)]"""

    return [
        (port.device, port.description, port.vid == ESPRESSIF_VID, port.vid is not None)
        for port in sorted(list_ports.comports(), key=lambda port: port.device)
    ]


class Controller(QObject):

    event = Signal(str, object)

    def __init__(self, args):

        super().__init__()

        self.args = args
        self.events = queue.Queue()
        self.device_config = json.loads(DEVICE_CONFIG.read_text(encoding="utf-8"))
        # La sesión de la placa y la de la PC leen la sensibilidad de acá.
        self.device_config["mic_sensitivity"] = settings.get("mic_sensitivity")

        self.audio_controls = AudioControls(
            percent=settings.get("volume"),
            profile=settings.get("profile"),
        )
        # Estéreo: necesita firmware 3.4 y el 2.º MAX98357A en el canal derecho.
        self.audio_controls.set_stereo(settings.get("stereo", self.device_config.get("stereo", False)))

        self.assistant = Assistant(
            self.events,
            save_dir=Path(args.save_wav) if args.save_wav else None,
        )
        self.assistant.speak_replies = settings.get("speak_replies")

        self.pc = PcAudioSession(self.events, self.assistant, self.device_config)
        self.wake_word = settings.get("wake_word")
        self.pc.microphone_id = settings.get("microphone") or None
        self.pc.speaker_id = settings.get("speaker") or None
        self.voice_on_pc = settings.get("voice_on_pc", False)
        self.assistant.voice_on_pc = self.pc if self.voice_on_pc else None

        # Placa
        self.session = None
        self.connected = False
        self.port = None
        self.autoconnect = settings.get("autoconnect")
        self._known_ports = set()
        self._blocked_ports = set()      # no reconectar sola hasta desenchufarla
        self._sink_module = None

        # Las órdenes fijas usan el mismo modelo chico de Vosk (si es ese).
        model = Path(args.vosk_model) if args.vosk_model else paths.VOSK_MODEL
        self.window_model = str(model) if (model / "am" / "final.mdl").is_file() else None

        self._event_timer = QTimer(self)
        self._event_timer.timeout.connect(self._drain_events)

        self._port_timer = QTimer(self)
        self._port_timer.timeout.connect(self._watch_ports)


    def start(self):

        self._event_timer.start(EVENTS_MS)

        self.assistant.load(
            stt=self.args.stt or settings.get("stt"),
            whisper_model=self.args.whisper_model,
            vosk_model=self.args.vosk_model,
            piper_voice=self.args.piper_voice,
            echo=self.args.echo,
            tts=not self.args.no_tts,
        )

        # --port: un puerto que no se lista (pty del simulador, alias udev).
        if self.args.port:
            self.connect_device(self.args.port)

        self._watch_ports()
        self._port_timer.start(PORT_WATCH_MS)


    def shutdown(self):

        self._port_timer.stop()
        self.pc.set_wake(False)
        self.pc.cancel()

        session = self.session
        if session is not None:
            session.close()
            # Le da tiempo a mandar J/0/Q, como al cerrar la ventana Tk.
            session.thread.join(timeout=2.0)

        remove_mochi_sink(self._sink_module)
        self._sink_module = None


    # =====================================================
    # EVENTOS
    # =====================================================

    def _drain_events(self):

        for _ in range(400):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break

            self._track(kind, value)
            self.event.emit(kind, value)


    def _track(self, kind, value):

        # Al registro (~/.cache/mochi/mochi.log) lo que no es de cada instante.
        if kind in ("log", "error", "action", "connected", "finished"):
            print(f"[placa] {kind}: {value}")

        if kind == "connected":
            self.connected = bool(value)
            if value and self._sink_module is None:
                threading.Thread(target=self._create_sink, daemon=True).start()
            self._update_wake()

        elif kind == "assistant_ready":
            self._update_wake()

        elif kind == "finished":
            # Terminó sola (error, cable) o la cortó el usuario: no se
            # vuelve a conectar sola hasta desenchufarla y enchufarla.
            if self.port:
                self._blocked_ports.add(self.port)
            self.session = None
            self.connected = False
            self._update_wake()


    def _create_sink(self):

        # Linux: salida "Mochi" en el sonido del sistema (se quita al cerrar).
        module = ensure_mochi_sink()
        if module:
            self._sink_module = module
            self.events.put(("log", "Salida de audio «Mochi» lista: elegila en el sonido del sistema "
                             "(icono de volumen) o en «Parlante PC», y pulsá Activar parlante PC. "
                             "Lo que suene ahí sale por el parlante de Mochi."))
        self.events.put(("outputs_changed", None))


    # =====================================================
    # PLACA
    # =====================================================

    def _watch_ports(self):

        present = set(find_esp32_ports())

        # Desenchufada: la próxima vez que aparezca se conecta sola.
        self._blocked_ports &= present

        if present != self._known_ports:
            self._known_ports = present
            self.event.emit("ports_changed", None)

        if self.session is None and self.autoconnect:
            candidates = sorted(present - self._blocked_ports)
            if candidates:
                self.connect_device(candidates[0])


    def connect_device(self, port):

        if self.session is not None or not port:
            return

        self.port = port
        self.session = VoiceSession(
            port,
            self.events,
            self.device_config,
            self.window_model,
            audio_controls=self.audio_controls,
            assistant=self.assistant,
        )
        self.session.wake_llm = self.wake_word
        # Con la placa escucha su micrófono: el de la PC se cierra.
        self.pc.set_wake(False)
        self.session.start()
        self.event.emit("connecting", port)


    def disconnect_device(self):

        if self.session is not None:
            self.session.close()


    def send_command(self, letter):

        if self.session is not None and self.connected:
            self.session.send(letter)


    def set_autoconnect(self, enabled):

        self.autoconnect = bool(enabled)
        settings.put("autoconnect", self.autoconnect)


    # =====================================================
    # HABLAR Y ESCRIBIR
    # =====================================================

    @property
    def uses_device(self):
        """Con la placa conectada, la voz va por la placa."""

        return self.session is not None and self.connected


    def talk(self):
        """«Hablarle a Mochi»: empieza a escuchar o manda lo dicho."""

        if self.uses_device:
            self.session.listen_toggle()
        else:
            self.pc.listen_toggle()


    def cancel(self):
        """
        Cancela lo que Mochi esté haciendo (escuchar, entender, pensar o
        hablar) y descarta el resultado, para repetir la orden.
        """

        self.assistant.cancel()

        if self.uses_device:
            self.session.cancel()
        else:
            self.pc.cancel()


    def ask(self, text):

        text = text.strip()

        if not text:
            return False

        if self.assistant.busy:
            self.event.emit("chat", ("notice", "Todavía estoy respondiendo lo anterior."))
            return False

        if self.uses_device:
            self.session.ask_text(text)
            return True

        return self.assistant.submit_text(self.pc, text)


    def set_wake_word(self, enabled):

        self.wake_word = bool(enabled)
        settings.put("wake_word", self.wake_word)
        self._update_wake()


    def _update_wake(self):
        """«Mochi» por el micrófono de la placa o, sin placa, por el de la PC."""

        if self.session is not None:
            self.session.wake_llm = self.wake_word
            self.pc.set_wake(False)
        elif self.assistant.ready:
            self.pc.set_wake(self.wake_word, self.window_model)

        if not self.assistant.ready or self.pc.listening:
            return
        ready = self.session.ready_text if self.uses_device else (None if self.session else self.pc.ready_text)
        if ready:
            self.event.emit("llm", ready)


    def set_speak_replies(self, enabled):

        self.assistant.speak_replies = bool(enabled)
        settings.put("speak_replies", bool(enabled))


    def set_voice_on_pc(self, enabled):
        """Con la placa: Mochi habla por los parlantes de la PC (sin placa, siempre)."""

        self.voice_on_pc = bool(enabled)
        self.assistant.voice_on_pc = self.pc if self.voice_on_pc else None
        settings.put("voice_on_pc", self.voice_on_pc)


    def set_mic_sensitivity(self, level):

        self.device_config["mic_sensitivity"] = level
        settings.put("mic_sensitivity", level)


    def set_pc_devices(self, microphone_id, speaker_id):

        self.pc.microphone_id = microphone_id or None
        self.pc.speaker_id = speaker_id or None
        settings.put("microphone", microphone_id or "")
        settings.put("speaker", speaker_id or "")


    def rewarm_llm(self):

        self.assistant.rewarm()


    def new_chat(self):

        self.assistant.forget()


    # =====================================================
    # PARLANTE PC
    # =====================================================

    def audio_outputs(self):
        """([(id, nombre)], id predeterminado, id de «Mochi» o None)"""

        outputs, default_id = audio_outputs()
        mochi = "sc:" + MOCHI_SINK
        return outputs, default_id, mochi if any(devid == mochi for devid, _ in outputs) else None


    def set_pc_audio(self, active, device_id=None):

        if self.session is not None:
            self.session.set_pc_audio(active, device_id)


    def test_usb_tone(self):

        if self.session is not None:
            self.session.test_usb_audio()


    def set_volume(self, percent):

        self.audio_controls.set_volume(percent)
        settings.put("volume", int(percent))


    def set_profile(self, profile):

        self.audio_controls.set_profile(profile)
        settings.put("profile", profile)


    def set_stereo(self, enabled):

        self.audio_controls.set_stereo(bool(enabled))
        settings.put("stereo", bool(enabled))
