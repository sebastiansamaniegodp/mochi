"""Pestaña «Parlante PC»: el audio de la PC sale por el parlante de Mochi."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mochi_app.device.audio_dsp import PROFILES
from mochi_app.ui.device_page import small


# Sensibilidad del micrófono, de izquierda a derecha (la misma que la de la
# configuración: mic_sensitivity).
SENSITIVITIES = [("baja", "Baja"), ("normal", "Normal"), ("alta", "Alta")]


class SpeakerPage(QWidget):

    def __init__(self, controller, parent=None):

        super().__init__(parent)

        self.controller = controller
        self.active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        layout.addWidget(small(QLabel(
            "Con la placa conectada aparece la salida «Mochi» en el sonido del sistema "
            "(icono de volumen de KDE). Lo que suene ahí sale por el parlante de Mochi. "
            "Mientras Mochi escucha o responde, la música se pausa."
        )))

        # Salida que se captura
        capture = QGroupBox("Audio de la PC")
        capture_layout = QVBoxLayout(capture)

        row = QHBoxLayout()
        self.output = QComboBox()
        self.output.setMinimumContentsLength(14)
        self.output.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        refresh = QToolButton()
        refresh.setIcon(QIcon.fromTheme("view-refresh"))
        refresh.setToolTip("Buscar salidas de audio")
        refresh.clicked.connect(self.refresh_outputs)
        row.addWidget(self.output, 1)
        row.addWidget(refresh)
        capture_layout.addLayout(row)

        row = QHBoxLayout()
        self.toggle = QPushButton("Activar parlante PC")
        self.toggle.setIcon(QIcon.fromTheme("audio-speakers"))
        self.toggle.clicked.connect(self._toggle)
        self.tone = QPushButton("Prueba de tono")
        self.tone.setToolTip("Un tono por el USB (en estéreo: primero izquierda, después derecha)")
        self.tone.clicked.connect(self._tone)
        row.addWidget(self.toggle, 1)
        row.addWidget(self.tone)
        capture_layout.addLayout(row)

        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(8)
        capture_layout.addWidget(self.level)
        capture_layout.addWidget(small(QLabel("Nivel de la música o el video (no es el micrófono).")))
        layout.addWidget(capture)

        # Sonido de Mochi (también se aplica a su voz)
        sound = QGroupBox("Sonido de Mochi")
        form = QFormLayout(sound)

        volume_row = QHBoxLayout()
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(30, 170)
        self.volume.setValue(70)
        self.volume_label = QLabel()
        self.volume_label.setMinimumWidth(40)
        self.volume.valueChanged.connect(self._volume)
        volume_row.addWidget(self.volume, 1)
        volume_row.addWidget(self.volume_label)
        form.addRow("Volumen:", volume_row)

        self.profile = QComboBox()
        self.profile.addItems(PROFILES)
        self.profile.currentTextChanged.connect(controller.set_profile)
        form.addRow("Perfil:", self.profile)

        self.stereo = QCheckBox("Estéreo: izquierdo y derecho en dos parlantes")
        self.stereo.setToolTip("Necesita firmware 3.4 o más y el segundo MAX98357A en el canal derecho.")
        self.stereo.toggled.connect(controller.set_stereo)
        form.addRow(self.stereo)
        form.addRow(small(QLabel("Empezá en 70 %; si distorsiona, no lo subas.")))

        self.voice_on_pc = QCheckBox("Voz de Mochi por los parlantes de la PC")
        self.voice_on_pc.setToolTip("Con la placa conectada, Mochi contesta por la PC (los parlantes "
                                    "de la configuración) y no por su parlante. La cara sigue en la placa.")
        self.voice_on_pc.toggled.connect(controller.set_voice_on_pc)
        form.addRow(self.voice_on_pc)
        layout.addWidget(sound)

        # Micrófono (el de Mochi o, sin la placa, el de la PC)
        mic = QGroupBox("Micrófono")
        form = QFormLayout(mic)

        sensitivity_row = QHBoxLayout()
        self.sensitivity = QSlider(Qt.Horizontal)
        self.sensitivity.setRange(0, len(SENSITIVITIES) - 1)
        self.sensitivity.setPageStep(1)
        self.sensitivity.setTickPosition(QSlider.TicksBelow)
        self.sensitivity.setTickInterval(1)
        self.sensitivity.setToolTip("Cuándo deja de escucharte: con menos sensibilidad, los ruidos "
                                    "y las voces lejanas no alargan la grabación de la orden.")
        self.sensitivity_label = QLabel()
        self.sensitivity_label.setMinimumWidth(50)
        self.sensitivity.valueChanged.connect(self._sensitivity)
        sensitivity_row.addWidget(self.sensitivity, 1)
        sensitivity_row.addWidget(self.sensitivity_label)
        form.addRow("Sensibilidad:", sensitivity_row)
        form.addRow(small(QLabel("Es la misma de la configuración. Si oye la tele o a gente "
                                 "lejos, bajala; si te corta, subila.")))
        layout.addWidget(mic)

        layout.addStretch(1)

        self.set_connected(False)


    def load_settings(self, volume, profile, stereo, sensitivity, voice_on_pc=False):

        self.volume.setValue(volume)
        self._volume(volume)
        if profile in PROFILES:
            self.profile.setCurrentText(profile)
        self.stereo.setChecked(stereo)
        self.voice_on_pc.setChecked(voice_on_pc)
        self.set_sensitivity(sensitivity)


    def set_sensitivity(self, level):
        """Muestra la sensibilidad sin volver a guardarla (al abrir o desde la configuración)."""

        levels = [key for key, name in SENSITIVITIES]
        index = levels.index(level) if level in levels else levels.index("normal")
        self.sensitivity.blockSignals(True)
        self.sensitivity.setValue(index)
        self.sensitivity.blockSignals(False)
        self.sensitivity_label.setText(SENSITIVITIES[index][1])


    # -----------------------------------------------------

    def refresh_outputs(self):

        try:
            outputs, default_id, mochi = self.controller.audio_outputs()
        except Exception as error:
            self.output.clear()
            self.output.addItem(f"No encontré salidas de audio ({error})", None)
            return

        current = self.output.currentData()
        self.output.clear()
        for device_id, name in outputs:
            self.output.addItem(name.replace("  (monitor PulseAudio/PipeWire)", ""), device_id)

        for choice in (current, mochi, default_id):
            index = self.output.findData(choice) if choice else -1
            if index >= 0:
                self.output.setCurrentIndex(index)
                break


    def _toggle(self):

        if self.active:
            self.controller.set_pc_audio(False)
        else:
            self.controller.set_pc_audio(True, self.output.currentData())


    def _tone(self):

        self.controller.test_usb_tone()


    def _volume(self, value):

        self.volume_label.setText(f"{value} %")
        self.controller.set_volume(value)


    def _sensitivity(self, index):

        level, name = SENSITIVITIES[index]
        self.sensitivity_label.setText(name)
        self.controller.set_mic_sensitivity(level)


    def set_connected(self, connected):

        self.toggle.setEnabled(connected)
        self.tone.setEnabled(connected and not self.active)
        if not connected:
            self.set_active(False)


    def set_active(self, active):

        self.active = bool(active)
        self.toggle.setText("Detener parlante PC" if active else "Activar parlante PC")
        self.tone.setEnabled(self.toggle.isEnabled() and not active)
        self.output.setEnabled(not active)
        if not active:
            self.level.setValue(0)
