"""Pestaña «Placa»: el ESP32 por USB, su micrófono y las órdenes fijas."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mochi_app.controller import list_serial_ports


# Las órdenes fijas del firmware (las mismas que por voz: «Mochi, duerme»).
COMMANDS = (
    ("D", "Dormir"),
    ("A", "Despertar"),
    ("F", "Feliz"),
    ("C", "Curioso"),
    ("O", "Sorpresa"),
    ("S", "Bostezar"),
    ("B", "Parpadear"),
    ("G", "Guiño"),
    ("T", "Sonido"),
    ("R", "Grabar"),
    ("P", "Reproducir"),
    ("X", "Quieto"),
    ("Y", "Muévete"),
    ("N", "Normal"),
)


def small(label):

    label.setWordWrap(True)
    label.setForegroundRole(QPalette.PlaceholderText)
    return label


class DevicePage(QWidget):

    command = Signal(str)

    def __init__(self, controller, parent=None):

        super().__init__(parent)

        self.controller = controller
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Puerto
        row = QHBoxLayout()
        self.port = QComboBox()
        self.port.setMinimumContentsLength(10)
        self.port.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        refresh = QToolButton()
        refresh.setIcon(QIcon.fromTheme("view-refresh"))
        refresh.setToolTip("Buscar puertos")
        refresh.clicked.connect(self.refresh_ports)
        self.connect_button = QPushButton("Conectar")
        self.connect_button.clicked.connect(self._toggle_connection)
        self.port.currentIndexChanged.connect(self._update_connect_button)
        row.addWidget(QLabel("Puerto:"))
        row.addWidget(self.port, 1)
        row.addWidget(refresh)
        row.addWidget(self.connect_button)
        layout.addLayout(row)

        self.autoconnect = QCheckBox("Conectar sola al enchufarla")
        self.autoconnect.setChecked(controller.autoconnect)
        self.autoconnect.toggled.connect(controller.set_autoconnect)
        layout.addWidget(self.autoconnect)

        self.status = QLabel("Enchufá Mochi por el USB nativo (no el que dice UART).")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # Micrófono de Mochi
        mic = QGroupBox("Micrófono de Mochi")
        mic_layout = QVBoxLayout(mic)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(8)
        self.heard = small(QLabel("Acá vas a ver lo que entiende…"))
        mic_layout.addWidget(self.level)
        mic_layout.addWidget(self.heard)
        layout.addWidget(mic)

        # Órdenes fijas
        orders = QGroupBox("Órdenes")
        grid = QGridLayout(orders)
        grid.setSpacing(6)
        self._command_buttons = []
        for index, (letter, text) in enumerate(COMMANDS):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, letter=letter: self.command.emit(letter))
            grid.addWidget(button, index // 3, index % 3)
            self._command_buttons.append(button)
        voice = small(QLabel("También por voz: «Mochi, duerme», «Mochi, feliz»…"))
        grid.addWidget(voice, grid.rowCount(), 0, 1, 3)
        layout.addWidget(orders)

        layout.addStretch(1)

        self.refresh_ports()
        self.set_connected(False)


    # -----------------------------------------------------

    def refresh_ports(self):

        current = self.port.currentData()
        self.port.clear()

        esp32 = None
        for device, description, is_esp32, is_usb in list_serial_ports():
            # Los ttyS de la placa madre no son Mochi.
            if not is_usb:
                continue
            label = f"{device}  (Mochi)" if is_esp32 else device
            self.port.addItem(label, device)
            self.port.setItemData(self.port.count() - 1, description, Qt.ToolTipRole)
            if is_esp32 and esp32 is None:
                esp32 = device

        # El puerto de la sesión (pty del simulador, alias) aunque no se liste.
        session_port = self.controller.port if self.controller.session else None
        if session_port and self.port.findData(session_port) < 0:
            self.port.addItem(session_port, session_port)

        if self.port.count() == 0:
            self.port.addItem("No hay placas USB", None)

        for choice in (session_port, current, esp32):
            index = self.port.findData(choice) if choice else -1
            if index >= 0:
                self.port.setCurrentIndex(index)
                break

        self._update_connect_button()


    def _update_connect_button(self):

        self.connect_button.setEnabled(self._busy or self.port.currentData() is not None)


    def _toggle_connection(self):

        if self._busy:
            self.controller.disconnect_device()
            self.status.setText("Desconectando…")
        else:
            self.controller.connect_device(self.port.currentData())


    def set_connecting(self, port):

        self._busy = True
        self.refresh_ports()
        self.port.setEnabled(False)
        self.connect_button.setText("Cancelar")
        self._update_connect_button()
        self.status.setText(f"Conectando a {port}…")


    def set_connected(self, connected):

        for button in self._command_buttons:
            button.setEnabled(connected)
        if connected:
            self.connect_button.setText("Desconectar")
        else:
            self.level.setValue(0)


    def set_finished(self, by_user):

        self._busy = False
        self.port.setEnabled(True)
        self.connect_button.setText("Conectar")
        self.refresh_ports()
        self.set_connected(False)
        if by_user:
            self.status.setText("Desconectada. Pulsá Conectar (o desenchufala y enchufala) para volver.")
