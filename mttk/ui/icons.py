"""Vector icons painted at runtime (no image files needed)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme


def _canvas(size=64):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def _icon(draw, size=64) -> QIcon:
    pm, p = _canvas(size)
    draw(p, size)
    p.end()
    return QIcon(pm)


def record(color=theme.RED) -> QIcon:
    def d(p, s):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        r = s * 0.22
        p.drawEllipse(QPointF(s / 2, s / 2), r, r)
    return _icon(d)


def play(color=theme.GREEN) -> QIcon:
    def d(p, s):
        path = QPainterPath()
        path.moveTo(s * 0.38, s * 0.28)
        path.lineTo(s * 0.74, s * 0.5)
        path.lineTo(s * 0.38, s * 0.72)
        path.closeSubpath()
        p.setPen(QPen(QColor(color), s * 0.05, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor(color))
        p.drawPath(path)
    return _icon(d)


def stop(color=theme.TEXT) -> QIcon:
    def d(p, s):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(s * 0.32, s * 0.32, s * 0.36, s * 0.36), s * 0.06, s * 0.06)
    return _icon(d)


def target(color=theme.CYAN) -> QIcon:
    def d(p, s):
        pen = QPen(QColor(color), s * 0.075, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.2, s * 0.2)
        for a, b in (((0.5, 0.16), (0.5, 0.28)), ((0.5, 0.72), (0.5, 0.84)),
                     ((0.16, 0.5), (0.28, 0.5)), ((0.72, 0.5), (0.84, 0.5))):
            p.drawLine(QPointF(s * a[0], s * a[1]), QPointF(s * b[0], s * b[1]))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.05, s * 0.05)
    return _icon(d)


def pin(color=theme.TEXT_DIM) -> QIcon:
    def d(p, s):
        p.setPen(QPen(QColor(color), s * 0.075, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(s * 0.38, s * 0.2)
        path.lineTo(s * 0.62, s * 0.2)
        path.lineTo(s * 0.58, s * 0.42)
        path.lineTo(s * 0.7, s * 0.56)
        path.lineTo(s * 0.3, s * 0.56)
        path.lineTo(s * 0.42, s * 0.42)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(s * 0.5, s * 0.56), QPointF(s * 0.5, s * 0.82))
    return _icon(d)


def gear(color=theme.TEXT_DIM) -> QIcon:
    def d(p, s):
        import math
        p.setPen(QPen(QColor(color), s * 0.075, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        c = s / 2
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(c + math.cos(a) * s * 0.22, c + math.sin(a) * s * 0.22),
                       QPointF(c + math.cos(a) * s * 0.32, c + math.sin(a) * s * 0.32))
        p.drawEllipse(QPointF(c, c), s * 0.2, s * 0.2)
        p.drawEllipse(QPointF(c, c), s * 0.08, s * 0.08)
    return _icon(d)


def folder(color=theme.TEXT_DIM) -> QIcon:
    def d(p, s):
        p.setPen(QPen(QColor(color), s * 0.075, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(s * 0.18, s * 0.3)
        path.lineTo(s * 0.4, s * 0.3)
        path.lineTo(s * 0.46, s * 0.36)
        path.lineTo(s * 0.82, s * 0.36)
        path.lineTo(s * 0.82, s * 0.74)
        path.lineTo(s * 0.18, s * 0.74)
        path.closeSubpath()
        p.drawPath(path)
    return _icon(d)


def save(color=theme.TEXT_DIM) -> QIcon:
    def d(p, s):
        p.setPen(QPen(QColor(color), s * 0.075, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56), s * 0.06, s * 0.06)
        p.drawRect(QRectF(s * 0.34, s * 0.22, s * 0.32, s * 0.16))
        p.drawRect(QRectF(s * 0.32, s * 0.54, s * 0.36, s * 0.24))
    return _icon(d)


def new_file(color=theme.TEXT_DIM) -> QIcon:
    def d(p, s):
        p.setPen(QPen(QColor(color), s * 0.075, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(s * 0.26, s * 0.18)
        path.lineTo(s * 0.58, s * 0.18)
        path.lineTo(s * 0.74, s * 0.34)
        path.lineTo(s * 0.74, s * 0.82)
        path.lineTo(s * 0.26, s * 0.82)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(s * 0.5, s * 0.44), QPointF(s * 0.5, s * 0.66))
        p.drawLine(QPointF(s * 0.39, s * 0.55), QPointF(s * 0.61, s * 0.55))
    return _icon(d)


def compact(color=theme.TEXT_DIM) -> QIcon:
    def d(p, s):
        p.setPen(QPen(QColor(color), s * 0.075, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s * 0.18, s * 0.26, s * 0.64, s * 0.48), s * 0.06, s * 0.06)
        p.drawLine(QPointF(s * 0.18, s * 0.42), QPointF(s * 0.82, s * 0.42))
    return _icon(d)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm, p = _canvas(size)
        s = size
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(s * 0.06, s * 0.06, s * 0.88, s * 0.88), s * 0.22, s * 0.22)
        # timeline bars
        p.setBrush(QColor(255, 255, 255, 110))
        p.drawRoundedRect(QRectF(s * 0.2, s * 0.66, s * 0.26, s * 0.08), s * 0.04, s * 0.04)
        p.drawRoundedRect(QRectF(s * 0.52, s * 0.66, s * 0.28, s * 0.08), s * 0.04, s * 0.04)
        # record dot + play
        p.setBrush(QColor(theme.RED))
        p.drawEllipse(QPointF(s * 0.34, s * 0.4), s * 0.12, s * 0.12)
        path = QPainterPath()
        path.moveTo(s * 0.54, s * 0.27)
        path.lineTo(s * 0.8, s * 0.4)
        path.lineTo(s * 0.54, s * 0.53)
        path.closeSubpath()
        p.setBrush(QColor("white"))
        p.drawPath(path)
        p.end()
        icon.addPixmap(pm)
    return icon


def write_style_assets(directory: str) -> dict:
    """Render small PNGs used by the stylesheet (arrows, check mark)."""
    import os
    os.makedirs(directory, exist_ok=True)
    paths = {}

    def arrow(name, up, color):
        pm, p = _canvas(24)
        path = QPainterPath()
        if up:
            path.moveTo(6, 15); path.lineTo(12, 9); path.lineTo(18, 15)
        else:
            path.moveTo(6, 9); path.lineTo(12, 15); path.lineTo(18, 9)
        p.setPen(QPen(QColor(color), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.end()
        f = os.path.join(directory, name + ".png")
        pm.save(f)
        paths[name] = f.replace("\\", "/")

    arrow("up", True, theme.TEXT_DIM)
    arrow("down", False, theme.TEXT_DIM)
    arrow("up_off", True, theme.TEXT_FAINT)
    arrow("down_off", False, theme.TEXT_FAINT)
    pm, p = _canvas(24)
    p.setPen(QPen(QColor("white"), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    path = QPainterPath()
    path.moveTo(6, 12.5); path.lineTo(10.5, 17); path.lineTo(18, 7.5)
    p.drawPath(path)
    p.end()
    f = os.path.join(directory, "check.png")
    pm.save(f)
    paths["check"] = f.replace("\\", "/")
    return paths
