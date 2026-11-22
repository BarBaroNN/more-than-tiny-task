"""Small click-through on-screen indicator shown while recording / playing."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from . import theme


class OSD(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._text = ""
        self._color = theme.RED
        self._font = QFont(self.font())
        self._font.setPointSizeF(10.5)
        self._font.setWeight(QFont.DemiBold)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def exclude_from_capture(self, on: bool) -> None:
        """Keep the indicator out of screenshots (checkpoint pictures)."""
        from .. import winutil
        winutil.set_exclude_from_capture(int(self.winId()), on)

    def show_message(self, text: str, color: str = theme.RED, hide_after_ms: int = 0):
        self._hide_timer.stop()
        if hide_after_ms:
            self._hide_timer.start(hide_after_ms)
        self._text = text
        self._color = color
        from PySide6.QtGui import QFontMetrics
        w = QFontMetrics(self._font).horizontalAdvance(text) + 46
        self.resize(w, 34)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.x() + (screen.width() - w) // 2, screen.y() + 14)
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QColor(255, 255, 255, 30))
        p.setBrush(QColor(20, 22, 27, 225))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(QRectF(14, r.center().y() - 5, 10, 10))
        p.setPen(QColor(theme.TEXT))
        p.setFont(self._font)
        p.drawText(r.adjusted(32, 0, -12, 0), Qt.AlignVCenter | Qt.AlignLeft, self._text)
        p.end()
