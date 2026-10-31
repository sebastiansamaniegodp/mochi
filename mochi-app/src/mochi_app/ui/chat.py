"""La conversación con Mochi: globos de lo que dijiste y de lo que contestó."""

import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


SUGGESTIONS = (
    "¿Qué ventanas tengo abiertas?",
    "¿Qué está sonando?",
    "Poné el volumen al 40 %",
)


def _mix(first, second, amount):

    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
    )


# =========================================================
# GLOBOS
# =========================================================

class Bubble(QFrame):

    def __init__(self, text, role, parent=None):

        super().__init__(parent)

        self.role = role
        self.text = text
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.label.setOpenExternalLinks(True)

        # Las respuestas del LLM a veces traen Markdown.
        if role == "mochi":
            self.label.setTextFormat(Qt.MarkdownText)
        else:
            self.label.setTextFormat(Qt.PlainText)
        self.label.setText(text)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.addWidget(self.label)

        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        self._apply_text_color()


    def _apply_text_color(self):

        palette = self.label.palette()
        role = QPalette.HighlightedText if self.role == "user" else QPalette.Text
        palette.setColor(QPalette.WindowText, self.palette().color(role))
        self.label.setPalette(palette)


    def changeEvent(self, event):

        if event.type() == event.Type.PaletteChange:
            self._apply_text_color()
        super().changeEvent(event)


    def background(self):

        palette = self.palette()
        if self.role == "user":
            return palette.color(QPalette.Highlight)
        return _mix(palette.color(QPalette.Base), palette.color(QPalette.Text), 0.07)


    def paintEvent(self, event):

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.background())
        painter.drawRoundedRect(QRectF(self.rect()), 14, 14)


class ThinkingBubble(Bubble):
    """Mochi está pensando: tres puntitos."""

    def __init__(self, parent=None):

        super().__init__("", "mochi", parent)
        self.label.setMinimumSize(34, 14)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(60)


    def paintEvent(self, event):

        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        color = self.palette().color(QPalette.Text)
        now = time.monotonic()
        center_y = self.height() / 2

        for index in range(3):
            phase = (now * 1.8 - index * 0.2) % 1.0
            dot = QColor(color)
            dot.setAlphaF(0.25 + 0.55 * max(0.0, 1 - abs(phase * 2 - 1)))
            painter.setBrush(dot)
            painter.drawEllipse(QRectF(16 + index * 12, center_y - 3.5, 7, 7))


# =========================================================
# CONVERSACIÓN
# =========================================================

class ChatView(QScrollArea):

    suggestion = Signal(str)

    def __init__(self, parent=None):

        super().__init__(parent)

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content = QWidget()
        self._content.setBackgroundRole(QPalette.Base)
        self._content.setAutoFillBackground(True)
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(self._content)

        self._bubbles = []
        self._thinking = None
        self._empty = self._empty_state()
        self._layout.addWidget(self._empty)
        self._layout.addStretch(1)


    def _empty_state(self):

        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setSpacing(10)

        title = QLabel("Hablale o escribile a Mochi")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignCenter)

        hint = QLabel("Con la placa conectada, Mochi te escucha y te contesta por su parlante. "
                      "Sin la placa, usa el micrófono y los parlantes de la PC.")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        hint.setForegroundRole(QPalette.PlaceholderText)

        layout.addWidget(title)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch(1)
        for text in SUGGESTIONS:
            button = QPushButton(text)
            button.setFlat(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked=False, text=text: self.suggestion.emit(text))
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        return box


    # -----------------------------------------------------

    def add(self, role, text):
        """role: "user", "mochi" o "notice"."""

        self._hide_empty()

        if role == "mochi":
            self.set_thinking(False)

        if role == "notice":
            widget = QLabel(text)
            widget.setWordWrap(True)
            widget.setAlignment(Qt.AlignCenter)
            widget.setForegroundRole(QPalette.PlaceholderText)
            row = QHBoxLayout()
            row.addWidget(widget)
        else:
            widget = Bubble(text, role)
            row = self._row(widget, role)
            self._bubbles.append(widget)
            self._fit(widget)

        self._insert(row)


    def set_thinking(self, active):

        if active and self._thinking is None:
            self._hide_empty()
            self._thinking = ThinkingBubble()
            self._thinking_row = self._row(self._thinking, "mochi")
            self._insert(self._thinking_row)
        elif not active and self._thinking is not None:
            self._thinking.deleteLater()
            self._layout.removeItem(self._thinking_row)
            self._thinking_row.deleteLater()
            self._thinking = None


    def clear(self):
        """Nuevo chat: vuelve al estado vacío, con las sugerencias."""

        self.set_thinking(False)

        # Todo lo que está entre el estiramiento de arriba y los últimos
        # dos (el estado vacío y el estiramiento final).
        while self._layout.count() > 3:
            row = self._layout.takeAt(1).layout()
            if row is None:
                continue
            while row.count():
                widget = row.takeAt(0).widget()
                if widget is not None:
                    widget.deleteLater()
            row.deleteLater()

        self._bubbles = []
        self._empty.show()
        self._layout.setStretch(self._layout.count() - 1, 1)


    def _hide_empty(self):

        # Con mensajes, quedan abajo de todo (el estiramiento de arriba los empuja).
        if self._empty.isVisible() or self._layout.stretch(self._layout.count() - 1):
            self._empty.hide()
            self._layout.setStretch(self._layout.count() - 1, 0)


    def _row(self, widget, role):

        row = QHBoxLayout()
        if role == "user":
            row.addStretch(1)
            row.addWidget(widget)
        else:
            row.addWidget(widget)
            row.addStretch(1)
        return row


    def _insert(self, row):

        # Antes de los últimos dos: el estado vacío y el estiramiento final.
        self._layout.insertLayout(self._layout.count() - 2, row)
        QTimer.singleShot(30, self._scroll_to_end)


    def _scroll_to_end(self):

        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


    def _fit(self, bubble):
        """Ancho del globo: el del texto, hasta el 72 % de la conversación.

        (Un QLabel con ajuste de línea solo pide un ancho chico.)
        """

        limit = max(220, int(self.viewport().width() * 0.72))

        document = QTextDocument()
        document.setDefaultFont(bubble.label.font())
        document.setDocumentMargin(0)
        if bubble.role == "mochi":
            document.setMarkdown(bubble.text)
        else:
            document.setPlainText(bubble.text)
        document.setTextWidth(limit)

        bubble.label.setMinimumWidth(min(limit, int(document.idealWidth()) + 4))
        bubble.label.setMaximumWidth(limit)


    def resizeEvent(self, event):

        super().resizeEvent(event)
        for bubble in self._bubbles:
            self._fit(bubble)
