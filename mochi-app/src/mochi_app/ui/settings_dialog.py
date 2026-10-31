"""Configuración: el servidor del LLM, la voz y el audio de la PC."""

import importlib.util
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from mochi_app import settings
from mochi_app.ui.device_page import small
from mochi_app.voice import pc_audio


class _Result(QObject):

    done = Signal(bool, str)


class SettingsDialog(QDialog):

    def __init__(self, controller, parent=None):

        super().__init__(parent)

        self.controller = controller
        self.setWindowTitle("Configuración de Mochi")
        self.setMinimumWidth(520)

        self.llm = settings.load_llm()
        self.restart_needed = False

        layout = QVBoxLayout(self)

        # Servidor del LLM
        server = QGroupBox("Servidor del LLM (llama-server en la PC)")
        form = QFormLayout(server)

        self.url = QLineEdit(self.llm["base_url"])
        self.url.setPlaceholderText("http://<IP de la PC>:8080")
        form.addRow("Dirección:", self.url)

        key_row = QHBoxLayout()
        self.key = QLineEdit(self.llm["api_key"])
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("contenido de llm-server/api_key.txt")
        show = QPushButton()
        show.setIcon(QIcon.fromTheme("view-visible"))
        show.setToolTip("Mostrar la clave")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)
        form.addRow("Clave:", key_row)

        self.model = QLineEdit(self.llm["model"])
        form.addRow("Modelo:", self.model)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("Probar conexión")
        self.test_button.clicked.connect(self._test)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        form.addRow(test_row)
        form.addRow(small(QLabel("Por Tailscale anda desde cualquier red; en casa también sirve "
                                 "la IP de la LAN de la PC.")))
        layout.addWidget(server)

        # Voz
        voice = QGroupBox("Voz")
        form = QFormLayout(voice)

        self.stt = QComboBox()
        self.stt.addItem("Vosk (rápido)", "vosk")
        self.stt.addItem("Whisper (más preciso, más lento)", "whisper")
        if importlib.util.find_spec("faster_whisper") is None:
            self.stt.model().item(1).setEnabled(False)
            self.stt.setItemText(1, "Whisper (no está instalado)")
        self.stt.setCurrentIndex(max(0, self.stt.findData(settings.get("stt"))))
        form.addRow("Reconocimiento:", self.stt)

        self.speak = QCheckBox("Leer las respuestas en voz alta")
        self.speak.setChecked(controller.assistant.speak_replies)
        form.addRow(self.speak)

        self.wake = QCheckBox("Escuchar siempre: «Mochi» abre la escucha de la orden")
        self.wake.setToolTip("El micrófono (el de Mochi o, sin la placa, el de la PC) queda abierto "
                             "esperando la llamada. «Mochi, duerme» sigue siendo una orden fija.")
        self.wake.setChecked(controller.wake_word)
        form.addRow(self.wake)

        self.sensitivity = QComboBox()
        self.sensitivity.addItem("Alta (oye todo el ambiente)", "alta")
        self.sensitivity.addItem("Normal", "normal")
        self.sensitivity.addItem("Baja (solo voz cerca del micrófono)", "baja")
        self.sensitivity.setToolTip("Cuándo deja de escucharte: con menos sensibilidad, los ruidos "
                                    "y las voces lejanas no alargan la grabación de la orden.")
        self.sensitivity.setCurrentIndex(max(0, self.sensitivity.findData(settings.get("mic_sensitivity"))))
        form.addRow("Sensibilidad:", self.sensitivity)
        layout.addWidget(voice)

        # Sin placa
        pc = QGroupBox("Sin la placa: audio de esta PC")
        form = QFormLayout(pc)
        self.microphone = QComboBox()
        self.speaker = QComboBox()
        self._fill(self.microphone, pc_audio.microphones, controller.pc.microphone_id)
        self._fill(self.speaker, pc_audio.speakers, controller.pc.speaker_id)
        form.addRow("Micrófono:", self.microphone)
        form.addRow("Parlantes:", self.speaker)
        layout.addWidget(pc)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._result = _Result()
        self._result.done.connect(self._tested)


    @staticmethod
    def _fill(combo, source, current):

        combo.addItem("El predeterminado del sistema", "")
        try:
            for device_id, name in source():
                combo.addItem(name, device_id)
        except Exception as error:
            combo.addItem(f"No pude listar los dispositivos ({error})", "")
        index = combo.findData(current or "")
        combo.setCurrentIndex(max(0, index))


    def _test(self):

        self.test_button.setEnabled(False)
        self.test_result.setText("Probando…")
        url, key = self.url.text().strip(), self.key.text().strip()

        def run():
            ok, text = settings.test_llm(url, key)
            self._result.done.emit(ok, text)

        threading.Thread(target=run, daemon=True).start()


    def _tested(self, ok, text):

        self.test_button.setEnabled(True)
        self.test_result.setText(("✔ " if ok else "✘ ") + text)


    def _save(self):

        url = self.url.text().strip() or settings.DEFAULT_LLM["base_url"]
        model = self.model.text().strip() or settings.DEFAULT_LLM["model"]
        key = self.key.text().strip()

        changed = (
            url.rstrip("/") != self.llm["base_url"].rstrip("/")
            or model != self.llm["model"]
            or key != self.llm["api_key"]
            or not self.llm["enabled"]
        )
        if changed:
            settings.save_llm(url, model, key)
            self.restart_needed = True

        stt = self.stt.currentData()
        if stt != settings.get("stt"):
            settings.put("stt", stt)
            self.restart_needed = True

        self.controller.set_speak_replies(self.speak.isChecked())
        self.controller.set_wake_word(self.wake.isChecked())
        self.controller.set_mic_sensitivity(self.sensitivity.currentData())
        self.controller.set_pc_devices(self.microphone.currentData(), self.speaker.currentData())
        self.accept()
