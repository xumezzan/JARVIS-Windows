"""Code-drawn dashboard artwork: resolution independent, local, and microphone-free."""

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QHideEvent,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QRadialGradient,
    QShowEvent,
)
from PySide6.QtWidgets import QWidget

from jarvis.ui.states import UiState


def line_icon(name: str, color: str = "#a5afbe", size: int = 24) -> QIcon:
    """Small consistent outline icons, without platform-dependent font glyphs."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    paths: dict[str, list[list[tuple[float, float]]]] = {
        "home": [
            [(3, 11), (12, 3), (21, 11)],
            [(5, 10), (5, 21), (10, 21), (10, 14), (14, 14), (14, 21), (19, 21), (19, 10)],
        ],
        "chat": [
            [(4, 4), (20, 4), (20, 16), (10, 16), (5, 21), (5, 16), (4, 16), (4, 4)],
            [(8, 9), (16, 9)],
            [(8, 12), (13, 12)],
        ],
        "tasks": [[(20, 12), (20, 20), (4, 20), (4, 4), (14, 4)], [(8, 10), (12, 14), (21, 4)]],
        "calendar": [
            [(4, 6), (20, 6), (20, 21), (4, 21), (4, 6)],
            [(4, 10), (20, 10)],
            [(8, 3), (8, 8)],
            [(16, 3), (16, 8)],
            [(8, 14), (10, 14)],
        ],
        "arrow": [[(5, 12), (20, 12)], [(14, 6), (20, 12), (14, 18)]],
        "close": [[(5, 5), (19, 19)], [(19, 5), (5, 19)]],
        "check": [[(4, 12), (10, 18), (20, 6)]],
        "clock": [[(12, 6), (12, 12), (17, 15)]],
        "keyboard": [[(2, 5), (22, 5), (22, 19), (2, 19), (2, 5)], [(7, 16), (17, 16)]],
        "mic": [
            [(5, 10), (5, 13), (7, 17), (12, 19), (17, 17), (19, 13), (19, 10)],
            [(12, 19), (12, 23)],
            [(8, 23), (16, 23)],
        ],
        "bulb": [[(9, 18), (15, 18)], [(10, 21), (14, 21)]],
        "shield": [
            [(12, 2), (21, 6), (19, 16), (12, 22), (5, 16), (3, 6), (12, 2)],
            [(8, 11), (11, 14), (16, 8)],
        ],
    }
    for points in paths.get(name, []):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        painter.drawPath(path)
    if name in {"clock", "settings"}:
        painter.drawEllipse(QRectF(3, 3, 18, 18))
    if name == "settings":
        painter.drawEllipse(QRectF(8, 8, 8, 8))
        for angle in range(0, 360, 45):
            radians = math.radians(angle)
            painter.drawLine(
                QPointF(12 + 9 * math.cos(radians), 12 + 9 * math.sin(radians)),
                QPointF(12 + 11 * math.cos(radians), 12 + 11 * math.sin(radians)),
            )
    if name == "apps":
        for x in (4, 14):
            for y in (4, 14):
                painter.drawRoundedRect(QRectF(x, y, 6, 6), 1.5, 1.5)
    if name == "keyboard":
        for x in (6, 10, 14, 18):
            for y in (9, 12):
                painter.drawPoint(QPointF(x, y))
    if name == "mic":
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(9, 2, 6, 13), 3, 3)
    if name == "bulb":
        painter.drawEllipse(QRectF(6, 2, 12, 13))
    painter.end()
    icon = QIcon(pixmap)
    icon.addPixmap(pixmap, QIcon.Mode.Disabled)
    return icon


class OrbWidget(QWidget):
    """An ambient light sculpture; faster motion represents demo work, never recording."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(300, 330)
        self.setAccessibleName("Световая сфера состояния Jarvis")
        self._phase = 0.0
        self._state = UiState.IDLE
        self._artwork: QImage | None = self._render_orb()
        self._timer = QTimer(self)
        self._timer.setInterval(66)
        self._timer.timeout.connect(self._advance)

    def sizeHint(self) -> QSize:
        return QSize(420, 420)

    def set_state(self, state: UiState) -> None:
        if (state == UiState.ERROR) != (self._state == UiState.ERROR):
            self._artwork = None
        self._state = state
        self.update()

    def _advance(self) -> None:
        self._phase += 0.035 if self._state in {UiState.THINKING, UiState.EXECUTING} else 0.008
        self.update()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        side = min(self.width(), self.height())
        painter.translate(self.width() / 2, self.height() / 2 - 12)
        painter.scale(side / 420, side / 420)
        if self._artwork is None:
            self._artwork = self._render_orb()
        painter.save()
        painter.rotate(3 * math.sin(self._phase))
        pulse = 1 + 0.012 * math.sin(self._phase * 2)
        painter.scale(pulse, pulse)
        painter.drawImage(QRectF(-210, -210, 420, 420), self._artwork)
        painter.restore()
        # Decorative waveform follows the demo animation, not microphone input.
        for bar in range(-22, 23):
            height = 2 + 18 * math.exp(-abs(bar) / 4) * abs(math.cos(bar * 0.65 + self._phase))
            painter.setPen(QPen(QColor(113, 182, 255, max(30, 210 - abs(bar) * 8)), 1.5))
            painter.drawLine(
                QPointF(bar * 3.5, 208 - height / 2), QPointF(bar * 3.5, 208 + height / 2)
            )
        painter.end()

    def _render_orb(self) -> QImage:
        """Cache expensive light paths; animation only composites the local artwork."""
        artwork = QImage(420, 420, QImage.Format.Format_ARGB32_Premultiplied)
        artwork.fill(Qt.GlobalColor.transparent)
        painter = QPainter(artwork)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(210, 210)
        hue = "#759fff" if self._state != UiState.ERROR else "#c488bf"
        glow = QRadialGradient(QPointF(0, 0), 205)
        glow.setColorAt(0, QColor(26, 51, 96, 28))
        glow.setColorAt(0.62, QColor(54, 99, 210, 35))
        glow.setColorAt(0.78, QColor(73, 125, 255, 30))
        glow.setColorAt(1, QColor(20, 40, 80, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-205, -205, 410, 410))
        core = QRadialGradient(QPointF(-25, -45), 200)
        core.setColorAt(0, QColor(10, 17, 29, 12))
        core.setColorAt(0.6, QColor(24, 44, 84, 50))
        core.setColorAt(0.86, QColor(56, 102, 211, 95))
        core.setColorAt(1, QColor(110, 167, 255, 25))
        painter.setBrush(core)
        painter.drawEllipse(QRectF(-147, -147, 294, 294))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for ribbon in range(72):
            path = QPainterPath()
            offset = ribbon * math.tau / 72
            for step in range(181):
                angle = step * math.tau / 180
                radius = 143 + 8 * math.sin(angle * 3 + offset + 0.0)
                radius += 5 * math.sin(angle * 2 - offset * 2 + 0.0)
                turn = 0.45 * math.sin(offset + 0.0)
                x = radius * math.cos(angle)
                y = radius * math.sin(angle) * (0.82 + 0.16 * math.cos(offset))
                x, y = (
                    x * math.cos(turn) - y * math.sin(turn),
                    (x * math.sin(turn) + y * math.cos(turn)),
                )
                if step == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            path.closeSubpath()
            gradient = QLinearGradient(-150, -150, 150, 150)
            light = QColor(hue)
            light.setAlpha(50 + ribbon % 6 * 9)
            gradient.setColorAt(0, light)
            gradient.setColorAt(0.3, QColor(164, 207, 255, 180 if ribbon % 7 == 0 else 65))
            gradient.setColorAt(0.55, QColor(56, 102, 250, 30))
            gradient.setColorAt(0.8, QColor(179, 219, 255, 190 if ribbon % 5 == 0 else 65))
            gradient.setColorAt(1, light)
            painter.setOpacity(0.10)
            painter.setPen(QPen(gradient, 9))
            painter.drawPath(path)
            painter.setOpacity(1.0)
            painter.setPen(QPen(gradient, 1.15))
            painter.drawPath(path)
        painter.end()
        return artwork
