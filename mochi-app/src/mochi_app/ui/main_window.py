"""La ventana de Mochi.

    ┌──────────────────────────────────────────────────────────┐
    │ (cara)  Mochi                        ● Placa  ● LLM   ☰  │
    │         lo que está haciendo                             │
    ├─────────────────────────────────┬────────────────────────┤
    │ conversación                    │ Placa | Parlante PC |  │
    │                                 │ Registro               │
    ├─────────────────────────────────┴────────────────────────┤
    │ [Hablarle a Mochi] [ Escribile a Mochi…        ] [Enviar] │
    └──────────────────────────────────────────────────────────┘
"""

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFontMetrics, QIcon, QKeySequence, QPainter, QPalette, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mochi_app import __version__, desktop_entry, paths, settings
from mochi_app.device.commands import COMMANDS
from mochi_app.device.protocol import (
    LLM_DONE,
    LLM_ERROR,
    LLM_IDLE,
    LLM_LISTENING,
    LLM_SPEAKING,
    LLM_THINKING,
)
from mochi_app.ui.chat import ChatView
from mochi_app.ui.device_page import DevicePage
from mochi_app.ui.face import FaceWidget
from mochi_app.ui.settings_dialog import SettingsDialog
from mochi_app.ui.speaker_page import SpeakerPage


# Colores de estado de Breeze (positivo, neutro, negativo).
GREEN = QColor("#27ae60")
YELLOW = QColor("#f67400")
RED = QColor("#da4453")

LLM_FACES = {
    LLM_IDLE: "idle",
    LLM_LISTENING: "listening",
    LLM_THINKING: "thinking",
    LLM_SPEAKING: "speaking",
    LLM_DONE: "happy",
    LLM_ERROR: "surprised",
}

# La cara de la ventana imita a la de la placa con las órdenes fijas.
COMMAND_FACES = {
    "D": "sleeping", "A": "idle", "N": "idle", "F": "happy", "C": "curious",
    "O": "surprised", "S": "yawn", "G": "wink", "T": "happy",
}
LABEL_TO_COMMAND = {label: letter for letter, label in COMMANDS.values()}

UDEV_HELP = (
    "Linux no deja abrir el puerto del ESP32. Instalá la regla udev de Mochi "
    "(pide la clave de sudo, una sola vez) y reconectá el cable:\n\n"
    "sudo cp '{rule}' /etc/udev/rules.d/ && "
    "sudo udevadm control --reload-rules && sudo udevadm trigger"
)


# =========================================================
# INDICADORES (Placa, LLM)
# =========================================================

class StatusChip(QWidget):

    clicked = Signal()

    def __init__(self, title, parent=None):

        super().__init__(parent)
        self.title = title
        self.text = ""
        self.color = None
        self.setCursor(Qt.PointingHandCursor)


    def set(self, text, color=None):

        self.text = text
        self.color = color
        self.updateGeometry()
        self.update()


    def sizeHint(self):

        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(f"{self.title}: {self.text}") + 34, metrics.height() + 12)


    def mousePressEvent(self, event):

        self.clicked.emit()


    def paintEvent(self, event):

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()

        painter.setPen(Qt.NoPen)
        painter.setBrush(palette.color(QPalette.AlternateBase))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        dot = self.color or palette.color(QPalette.PlaceholderText)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(10, rect.center().y() - 4, 8, 8))

        painter.setPen(palette.color(QPalette.Text))
        painter.drawText(rect.adjusted(24, 0, -10, 0), Qt.AlignVCenter, f"{self.title}: {self.text}")


# =========================================================
# VENTANA
# =========================================================

class MainWindow(QMainWindow):

    def __init__(self, controller, style_name, restart):

        super().__init__()

        self.controller = controller
        self.style_name = style_name
        self.restart = restart
        self.listening = False
        self._face_state = LLM_IDLE
        self._udev_warned = False
        self._llm_level = "loading"

        self.setWindowTitle("Mochi")
        self.setWindowIcon(QIcon(str(paths.ICON)))
        self.resize(1040, 700)
        self.setMinimumSize(780, 540)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header())

        self.splitter = QSplitter(Qt.Horizontal)
        self.chat = ChatView()
        self.chat.suggestion.connect(self._send_suggestion)
        self.splitter.addWidget(self.chat)
        self.splitter.addWidget(self._side_panel())
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)

        layout.addWidget(self._input_bar())
        self.setCentralWidget(central)

        self._restore()

        controller.event.connect(self.on_event)
        self.device_page.command.connect(self._command)
        self.speaker_page.refresh_outputs()

        self.text.setFocus()


    # -----------------------------------------------------
    # Partes
    # -----------------------------------------------------

    def _header(self):

        header = QWidget()
        header.setBackgroundRole(QPalette.Window)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(16)

        self.face = FaceWidget()
        layout.addWidget(self.face)

        texts = QVBoxLayout()
        texts.setSpacing(4)
        title = QLabel("Mochi")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.7)
        font.setBold(True)
        title.setFont(font)
        self.state_text = QLabel("Cargando la voz y el agente…")
        self.state_text.setWordWrap(True)
        self.where_text = QLabel()
        self.where_text.setForegroundRole(QPalette.PlaceholderText)
        texts.addStretch(1)
        texts.addWidget(title)
        texts.addWidget(self.state_text)
        texts.addWidget(self.where_text)
        texts.addStretch(1)
        layout.addLayout(texts, 1)

        chips = QVBoxLayout()
        chips.setSpacing(6)
        self.device_chip = StatusChip("Placa")
        self.device_chip.setToolTip("Ver la placa")
        self.device_chip.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.llm_chip = StatusChip("LLM")
        self.llm_chip.clicked.connect(self._llm_chip_clicked)
        chips.addStretch(1)
        chips.addWidget(self.device_chip, 0, Qt.AlignRight)
        chips.addWidget(self.llm_chip, 0, Qt.AlignRight)
        chips.addStretch(1)
        layout.addLayout(chips)

        # Nuevo chat: vacía la conversación y Mochi se olvida de lo
        # hablado. Mientras responde, no se puede (ver _update_cancel).
        self.new_chat_action = QAction(QIcon.fromTheme("document-new", QIcon.fromTheme("list-add")), "Nuevo chat", self)
        self.new_chat_action.setShortcut(QKeySequence.New)
        self.new_chat_action.setToolTip("Empezar una conversación nueva: Mochi se olvida de lo que hablaron (Ctrl+N)")
        self.new_chat_action.triggered.connect(self._new_chat)
        self.addAction(self.new_chat_action)

        new_chat_button = QToolButton()
        new_chat_button.setDefaultAction(self.new_chat_action)
        new_chat_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        new_chat_button.setAutoRaise(True)
        new_chat_button.setIconSize(QSize(22, 22))
        layout.addWidget(new_chat_button, 0, Qt.AlignTop)

        menu_button = QToolButton()
        menu_button.setIcon(QIcon.fromTheme("application-menu", QIcon.fromTheme("open-menu")))
        menu_button.setToolTip("Menú")
        menu_button.setPopupMode(QToolButton.InstantPopup)
        menu_button.setAutoRaise(True)
        menu_button.setIconSize(QSize(22, 22))
        menu_button.setMenu(self._menu())
        layout.addWidget(menu_button, 0, Qt.AlignTop)

        self._set_device_chip("off")
        self.llm_chip.set("cargando…", YELLOW)
        return header


    def _menu(self):

        menu = QMenu(self)

        configure = QAction(QIcon.fromTheme("configure"), "Configuración…", self)
        configure.setShortcut(QKeySequence.Preferences)
        configure.triggered.connect(self.open_settings)
        menu.addAction(configure)
        self.addAction(configure)

        warm = QAction(QIcon.fromTheme("view-refresh"), "Precalentar el LLM", self)
        warm.setToolTip("Si se reinició llama-server en la PC")
        warm.triggered.connect(self._rewarm)
        menu.addAction(warm)

        menu.addAction(self.new_chat_action)

        menu.addSeparator()

        log = QAction(QIcon.fromTheme("text-x-log", QIcon.fromTheme("document-open")), "Abrir el registro completo", self)
        log.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.LOG_FILE))))
        menu.addAction(log)

        if paths.APPIMAGE:
            install = QAction(QIcon.fromTheme("list-add"), "Agregar Mochi al menú de aplicaciones", self)
            install.triggered.connect(self._install_menu_entry)
            menu.addAction(install)

        about = QAction(QIcon.fromTheme("help-about"), "Acerca de Mochi", self)
        about.triggered.connect(self._about)
        menu.addAction(about)

        menu.addSeparator()

        quit_action = QAction(QIcon.fromTheme("application-exit"), "Salir", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        self.addAction(quit_action)

        return menu


    def _side_panel(self):

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.device_page = DevicePage(self.controller)
        self.speaker_page = SpeakerPage(self.controller)
        self.speaker_page.load_settings(
            settings.get("volume"),
            settings.get("profile"),
            self.controller.audio_controls.stereo(),
            settings.get("mic_sensitivity"),
            self.controller.voice_on_pc,
        )

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.log.setFrameShape(QPlainTextEdit.NoFrame)

        self.tabs.addTab(self._scrollable(self.device_page), QIcon.fromTheme("drive-removable-media-usb"), "Placa")
        self.tabs.addTab(self._scrollable(self.speaker_page), QIcon.fromTheme("audio-speakers"), "Parlante PC")
        self.tabs.addTab(self.log, QIcon.fromTheme("view-list-text"), "Registro")
        return self.tabs


    @staticmethod
    def _scrollable(page):

        area = QScrollArea()
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return area


    def _input_bar(self):

        bar = QWidget()
        bar.setBackgroundRole(QPalette.Window)
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(12, 8, 12, 10)
        outer.setSpacing(6)

        self.input_level = QProgressBar()
        self.input_level.setRange(0, 100)
        self.input_level.setTextVisible(False)
        self.input_level.setFixedHeight(4)
        self.input_level.hide()
        outer.addWidget(self.input_level)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.talk_button = QPushButton("Hablarle a Mochi")
        self.talk_button.setIcon(QIcon.fromTheme("audio-input-microphone"))
        self.talk_button.setMinimumHeight(34)
        self.talk_button.clicked.connect(self.controller.talk)

        # Corta lo que Mochi esté haciendo (escuchar, pensar o hablar) para
        # repetir la orden. Solo se ve mientras hay algo que cancelar.
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setIcon(QIcon.fromTheme("dialog-cancel", QIcon.fromTheme("process-stop")))
        self.cancel_button.setToolTip("Cancela la orden en curso (Esc)")
        self.cancel_button.setMinimumHeight(34)
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._cancel)

        self.text = QLineEdit()
        self.text.setPlaceholderText("Escribile a Mochi…")
        self.text.setClearButtonEnabled(True)
        self.text.setMinimumHeight(34)
        self.text.returnPressed.connect(self._send)

        self.send_button = QPushButton("Enviar")
        self.send_button.setIcon(QIcon.fromTheme("document-send"))
        self.send_button.setMinimumHeight(34)
        self.send_button.clicked.connect(self._send)

        row.addWidget(self.talk_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.text, 1)
        row.addWidget(self.send_button)
        outer.addLayout(row)

        self._update_where()
        return bar


    # -----------------------------------------------------
    # Acciones
    # -----------------------------------------------------

    def _send(self):

        text = self.text.text().strip()
        if text and self.controller.ask(text):
            self.text.clear()


    def _cancel(self):

        if self.cancel_button.isVisible():
            self.controller.cancel()


    def _update_cancel(self):

        busy = self.listening or self._face_state in (LLM_LISTENING, LLM_THINKING, LLM_SPEAKING)
        self.cancel_button.setVisible(busy)
        self.new_chat_action.setEnabled(not busy)


    def _new_chat(self):

        self.chat.clear()
        self.controller.new_chat()
        self.text.setFocus()


    def _send_suggestion(self, text):

        self.text.setText(text)
        self._send()


    def _command(self, letter):

        self.controller.send_command(letter)
        self.face.set_state(COMMAND_FACES.get(letter, self.face.state))
        if letter == "B":
            self.face.blink()


    def _rewarm(self):

        self.controller.rewarm_llm()


    def _llm_chip_clicked(self):

        if self._llm_level in ("off", "error"):
            self.open_settings()


    def open_settings(self):

        dialog = SettingsDialog(self.controller, self)
        accepted = dialog.exec()
        if accepted:
            # La sensibilidad también está en la pestaña «Parlante PC».
            self.speaker_page.set_sensitivity(settings.get("mic_sensitivity"))
        if accepted and dialog.restart_needed:
            answer = QMessageBox.question(
                self,
                "Reiniciar Mochi",
                "Para usar la configuración nueva hay que reiniciar Mochi. ¿Lo reinicio ahora?",
            )
            if answer == QMessageBox.Yes:
                self.restart()


    def _install_menu_entry(self):

        try:
            path = desktop_entry.install(paths.APPIMAGE)
        except OSError as error:
            QMessageBox.warning(self, "Mochi", f"No pude agregar Mochi al menú: {error}")
            return
        QMessageBox.information(self, "Mochi", f"Listo: Mochi está en el menú de aplicaciones.\n\n{path}")


    def _about(self):

        QMessageBox.about(
            self,
            "Acerca de Mochi",
            f"<h3>Mochi {__version__}</h3>"
            "<p>El robot de escritorio por voz: ESP32-S3 + LLM en la PC.</p>"
            f"<p>Estilo: {self.style_name}<br>"
            f"Registro: {paths.LOG_FILE}</p>",
        )


    # -----------------------------------------------------
    # Eventos de la placa, el asistente y la PC
    # -----------------------------------------------------

    def on_event(self, kind, value):

        if kind == "status":
            self.device_page.status.setText(value)
        elif kind == "transcript":
            if self.controller.connected:
                self.device_page.heard.setText(value)
        elif kind == "level":
            if self.listening:
                self.input_level.setValue(int(value))
            if self.controller.connected:
                self.device_page.level.setValue(int(value))
        elif kind == "log":
            self._log(value)
        elif kind == "error":
            self.device_page.status.setText(value)
            self._log("Aviso: " + value)
            self._maybe_udev_help(value)
        elif kind == "pc_meter":
            self.speaker_page.level.setValue(int(value))
        elif kind == "pc_state":
            self.speaker_page.set_active(value)
        elif kind == "action":
            self.device_page.status.setText("Orden recibida: " + value)
            letter = LABEL_TO_COMMAND.get(value)
            if letter in COMMAND_FACES:
                self.face.set_state(COMMAND_FACES[letter])
        elif kind == "llm":
            self.state_text.setText(value)
        elif kind == "listening":
            self._set_listening(value)
        elif kind == "connecting":
            self.device_page.set_connecting(value)
            self._set_device_chip("connecting", value)
        elif kind == "connected":
            self.device_page.set_connected(value)
            self.speaker_page.set_connected(value)
            self._set_device_chip("on" if value else "connecting", self.controller.port)
            self._update_where()
        elif kind == "finished":
            self.device_page.set_finished(value)
            self.speaker_page.set_connected(False)
            self._set_device_chip("off")
            self._update_where()
        elif kind == "ports_changed":
            if self.controller.session is None:
                self.device_page.refresh_ports()
        elif kind == "outputs_changed":
            self.speaker_page.refresh_outputs()
        elif kind == "face":
            self._set_face(value)
        elif kind == "chat":
            role, text = value
            self.chat.add(role, text)
        elif kind == "llm_status":
            self._set_llm_chip(*value)
        elif kind == "assistant_ready":
            self.state_text.setText(
                self.controller.session.ready_text
                if self.controller.uses_device
                else self.controller.pc.ready_text
            )


    def _log(self, text):

        self.log.appendPlainText(text)


    def _set_face(self, state):

        self._face_state = state
        self.face.set_state(LLM_FACES.get(state, "idle"))
        self.chat.set_thinking(state == LLM_THINKING)
        self._update_cancel()


    def _set_listening(self, active):

        self.listening = bool(active)
        self.input_level.setVisible(self.listening)
        self.input_level.setValue(0)
        if active:
            self.talk_button.setText("Enviar ya")
            self.talk_button.setIcon(QIcon.fromTheme("media-playback-stop"))
            self.face.set_state("listening")
        else:
            self.talk_button.setText("Hablarle a Mochi")
            self.talk_button.setIcon(QIcon.fromTheme("audio-input-microphone"))
        self._update_cancel()


    def _set_device_chip(self, state, port=None):

        if state == "on":
            self.device_chip.set(port or "conectada", GREEN)
        elif state == "connecting":
            self.device_chip.set("conectando…", YELLOW)
        else:
            self.device_chip.set("sin conectar")


    def _set_llm_chip(self, level, text):

        self._llm_level = level
        colors = {"ok": GREEN, "loading": YELLOW, "error": RED}
        self.llm_chip.set(text, colors.get(level))
        tips = {
            "off": "Clic para configurar el servidor del LLM",
            "error": "Clic para revisar la configuración. Se reintenta solo cada 30 s.",
        }
        self.llm_chip.setToolTip(tips.get(level, ""))


    def _update_where(self):

        if self.controller.uses_device:
            self.where_text.setText("Escucha y habla por la placa")
            self.talk_button.setToolTip("Usa el micrófono de Mochi (o mantené BOOT en la placa)")
        else:
            self.where_text.setText("Sin placa: escucha y habla por esta PC")
            self.talk_button.setToolTip("Usa el micrófono de la PC; se envía solo cuando dejás de hablar")


    def _maybe_udev_help(self, message):

        if self._udev_warned or "Permission denied" not in message and "Errno 13" not in message:
            return

        self._udev_warned = True
        text = UDEV_HELP.format(rule=paths.UDEV_RULE)
        self._log(text)
        box = QMessageBox(QMessageBox.Warning, "Mochi: permiso del puerto USB", text, QMessageBox.Ok, self)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.open()


    # -----------------------------------------------------
    # Ventana
    # -----------------------------------------------------

    def _restore(self):

        store = settings.app_settings()
        geometry = store.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter = store.value("window/splitter")
        if splitter is not None:
            self.splitter.restoreState(splitter)
        else:
            QTimer.singleShot(0, lambda: self.splitter.setSizes([640, 400]))


    def closeEvent(self, event):

        store = settings.app_settings()
        store.setValue("window/geometry", self.saveGeometry())
        store.setValue("window/splitter", self.splitter.saveState())
        super().closeEvent(event)
