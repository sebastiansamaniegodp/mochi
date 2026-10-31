"""La cara de Mochi: los mismos estados que la pantalla de la placa.

    idle        mira a los costados y parpadea
    listening   ojos grandes, atentos
    thinking    mira para arriba, con puntitos
    speaking    contenta, la boca se mueve
    happy       ojos ^ ^ (listo, feliz)
    surprised   ojos redondos (error, sorpresa)
    sleeping    ojos cerrados y zzz
    curious     un ojo más grande
    wink        guiño
    yawn        bostezo

Se dibuja en un lienzo de 280 x 200 y se escala al tamaño del widget.
"""

import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


WIDTH, HEIGHT = 280.0, 200.0

SCREEN = QColor("#0c1418")
EYE = QColor("#7fe7d3")

# Expresiones que vuelven solas a idle (como la placa, a los 3 s).
TEMPORARY = {
    "happy": 3.0,
    "surprised": 3.0,
    "curious": 3.0,
    "wink": 0.8,
    "yawn": 2.2,
}


class _Eye:
    """Valores que se animan (se acercan de a poco al objetivo)."""

    def __init__(self):

        self.w = 62.0
        self.h = 78.0
        self.happy = 0.0      # 0: rectángulo, 1: arco ^
        self.round = 0.0      # 0: rectángulo, 1: círculo
        self.closed = 0.0     # 1: línea (dormido)

    def approach(self, target, amount):

        for name in ("w", "h", "happy", "round", "closed"):
            value = getattr(self, name)
            setattr(self, name, value + (getattr(target, name) - value) * amount)


class FaceWidget(QWidget):

    def __init__(self, parent=None):

        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(168, 120)
        self.setToolTip("Mochi")

        self.state = "idle"
        self._until = None
        self._started = time.monotonic()

        self._eyes = [_Eye(), _Eye()]
        self._look = QPointF(0, 0)
        self._look_target = QPointF(0, 0)
        self._next_look = 0.0
        self._blink = 0.0           # 0..1, se suma a "closed"
        self._blink_start = None
        self._next_blink = time.monotonic() + 2.0
        self._mouth = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)


    # -----------------------------------------------------
    # Estado
    # -----------------------------------------------------

    def set_state(self, state):

        self.state = state
        seconds = TEMPORARY.get(state)
        self._until = time.monotonic() + seconds if seconds else None

        if state in ("listening", "speaking", "surprised"):
            self._look_target = QPointF(0, 0)

        self.update()


    def blink(self):

        self._blink_start = time.monotonic()


    def mousePressEvent(self, event):

        # Hacerle mimos a Mochi.
        if self.state in ("idle", "sleeping"):
            self.set_state("happy")


    # -----------------------------------------------------
    # Animación
    # -----------------------------------------------------

    def _targets(self, now):

        left, right = _Eye(), _Eye()
        state = self.state

        if state == "listening":
            pulse = 4 * math.sin(now * 5)
            for eye in (left, right):
                eye.w, eye.h = 68, 92 + pulse
        elif state == "thinking":
            for eye in (left, right):
                eye.w, eye.h = 58, 62
        elif state in ("speaking", "happy"):
            for eye in (left, right):
                eye.happy = 1.0
        elif state == "surprised":
            for eye in (left, right):
                eye.round, eye.w, eye.h = 1.0, 70, 70
        elif state == "sleeping":
            for eye in (left, right):
                eye.closed = 1.0
        elif state == "curious":
            left.w, left.h = 70, 94
            right.w, right.h = 56, 58
        elif state == "wink":
            right.happy = 1.0
        elif state == "yawn":
            for eye in (left, right):
                eye.h = 26

        return left, right


    def _tick(self):

        now = time.monotonic()

        if self._until is not None and now >= self._until:
            self.state = "idle"
            self._until = None

        # Mirar alrededor.
        if self.state == "idle" and now >= self._next_look:
            self._look_target = QPointF(random.uniform(-16, 16), random.uniform(-8, 8))
            if random.random() < 0.35:
                self._look_target = QPointF(0, 0)
            self._next_look = now + random.uniform(1.4, 3.8)
        elif self.state == "thinking":
            self._look_target = QPointF(14, -14)
        elif self.state == "curious":
            self._look_target = QPointF(-6, -4)

        self._look += (self._look_target - self._look) * 0.18

        # Parpadeo (no dormido, ni con los ojos ^ ^).
        if self.state in ("idle", "listening", "thinking", "curious") and now >= self._next_blink:
            self._blink_start = now
            self._next_blink = now + random.uniform(2.2, 6.0)

        if self._blink_start is not None:
            phase = (now - self._blink_start) / 0.16
            self._blink = 1.0 - abs(1.0 - phase * 2) if phase < 1 else 0.0
            if phase >= 1:
                self._blink_start = None

        for eye, target in zip(self._eyes, self._targets(now)):
            eye.approach(target, 0.25)

        if self.state == "speaking":
            self._mouth = 0.25 + 0.75 * abs(math.sin(now * 11)) * (0.6 + 0.4 * math.sin(now * 3.1))
        elif self.state == "yawn":
            self._mouth += (1.0 - self._mouth) * 0.15
        else:
            self._mouth += (0.0 - self._mouth) * 0.3

        self.update()


    # -----------------------------------------------------
    # Dibujo
    # -----------------------------------------------------

    def paintEvent(self, event):

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        scale = min(self.width() / WIDTH, self.height() / HEIGHT)
        painter.translate((self.width() - WIDTH * scale) / 2, (self.height() - HEIGHT * scale) / 2)
        painter.scale(scale, scale)

        # Pantalla
        painter.setPen(Qt.NoPen)
        painter.setBrush(SCREEN)
        painter.drawRoundedRect(QRectF(0, 0, WIDTH, HEIGHT), 34, 34)

        now = time.monotonic()
        centers = (QPointF(WIDTH / 2 - 58, 92), QPointF(WIDTH / 2 + 58, 92))

        for eye, center in zip(self._eyes, centers):
            self._draw_eye(painter, eye, center + self._look)

        if self._mouth > 0.03:
            self._draw_mouth(painter, self._mouth)

        if self.state == "thinking":
            self._draw_dots(painter, now)
        elif self.state == "sleeping":
            self._draw_zzz(painter, now)


    def _draw_eye(self, painter, eye, center):

        closed = min(1.0, eye.closed + self._blink)

        # Ojo normal o redondo (se achata al parpadear).
        normal = 1.0 - eye.happy
        if normal > 0.02:
            w = eye.w
            h = max(7.0, eye.h * (1.0 - closed))
            radius = min(w, h) * (0.3 + 0.2 * eye.round)
            rect = QRectF(center.x() - w / 2, center.y() - h / 2, w, h)

            glow = QColor(EYE)
            glow.setAlphaF(0.16 * normal)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(rect.adjusted(-6, -6, 6, 6), radius + 6, radius + 6)

            color = QColor(EYE)
            color.setAlphaF(normal)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, radius, radius)

        # Ojos contentos: un arco ^
        if eye.happy > 0.02:
            color = QColor(EYE)
            color.setAlphaF(eye.happy)
            pen = QPen(color, 13)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)

            w = eye.w
            path = QPainterPath(QPointF(center.x() - w / 2, center.y() + 10))
            path.quadTo(QPointF(center.x(), center.y() - 34), QPointF(center.x() + w / 2, center.y() + 10))
            painter.drawPath(path)


    def _draw_mouth(self, painter, amount):

        w = 30 + 10 * amount
        h = 4 + 20 * amount
        painter.setPen(Qt.NoPen)
        painter.setBrush(EYE)
        painter.drawRoundedRect(QRectF(WIDTH / 2 - w / 2, 160 - h / 2, w, h), h / 2, h / 2)


    def _draw_dots(self, painter, now):

        painter.setPen(Qt.NoPen)
        for index in range(3):
            phase = (now * 2.2 - index * 0.25) % 1.0
            color = QColor(EYE)
            color.setAlphaF(0.25 + 0.75 * max(0.0, math.sin(phase * math.pi)))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(WIDTH / 2 - 22 + index * 22, 166), 6, 6)


    def _draw_zzz(self, painter, now):

        font = QFont()
        font.setBold(True)
        for index in range(3):
            phase = (now * 0.5 + index / 3) % 1.0
            color = QColor(EYE)
            color.setAlphaF(math.sin(phase * math.pi))
            painter.setPen(color)
            font.setPixelSize(int(14 + 10 * phase))
            painter.setFont(font)
            painter.drawText(QPointF(206 + 22 * phase, 66 - 44 * phase), "z")
