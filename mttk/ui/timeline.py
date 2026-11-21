"""Video-editor style timeline for a macro.

Tracks: mouse path (X/Y graph), clicks, scroll and keys. Supports zooming,
scrolling, range selection with draggable edges, a draggable playhead,
selecting events and dragging them in time.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import QScrollBar, QToolTip, QWidget

from ..model import CLICK, KEY, MOVE, SCROLL, Event
from . import theme


def fmt_time(t: float, decimals: int = 2) -> str:
    t = max(0.0, t)
    m, s = divmod(t, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{int(h)}:{int(m):02d}:{s:0{3 + decimals if decimals else 2}.{decimals}f}"
    if m >= 1:
        return f"{int(m)}:{s:0{3 + decimals if decimals else 2}.{decimals}f}"
    return f"{s:.{decimals}f}s"


@dataclass
class Bar:
    t0: float
    t1: float
    label: str
    indices: Tuple[int, ...]
    lane: int = 0
    kind: str = KEY


_NICE_STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5,
               1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]


class Timeline(QWidget):
    playheadChanged = Signal(float)
    selectionChanged = Signal(object)          # (start, end) or None
    eventsSelected = Signal(list)              # list of event indices
    eventActivated = Signal(int)               # double click on an event
    moveEventsRequested = Signal(list, float)  # indices, dt
    contextMenuRequested = Signal(QPoint, float)

    HEADER_W = 104
    RULER_H = 30
    SCROLL_H = 12
    MIN_PPS = 0.5
    MAX_PPS = 6000.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(220)
        self.events: List[Event] = []
        self._times: List[float] = []
        self._move_idx: List[int] = []
        self._move_times: List[float] = []
        self._click_bars: List[Bar] = []
        self._key_bars: List[Bar] = []
        self._scroll_idx: List[int] = []
        self._key_lanes = 1
        self._xbounds = (0, 1)
        self._ybounds = (0, 1)
        self.pps = 100.0      # pixels per second
        self.offset = 0.0     # time at left edge of the track area
        self.playhead = 0.0
        self.selection: Optional[Tuple[float, float]] = None
        self.selected: set = set()
        self.overlay_text = ""
        self.read_only = False
        self._hover_t: Optional[float] = None
        self._drag = None     # dict describing the current drag
        self._tracks = []
        self._layout_tracks()

        self.hbar = QScrollBar(Qt.Horizontal, self)
        self.hbar.valueChanged.connect(self._on_scroll)
        self._small = QFont(self.font())
        self._small.setPointSizeF(8)
        self._label_font = QFont(self.font())
        self._label_font.setPointSizeF(8.5)
        self._label_font.setWeight(QFont.DemiBold)

    # ------------------------------------------------------------ data
    def set_events(self, events: Sequence[Event]) -> None:
        self.events = list(events)
        self._times = [e.t for e in self.events]
        self._rebuild()
        self.selected = {i for i in self.selected if i < len(self.events)}
        self._layout_tracks()
        self.hbar.setGeometry(self.HEADER_W, self._content_bottom + 4, self.track_width(), self.SCROLL_H)
        self._update_scrollbar()
        self.update()

    def duration(self) -> float:
        return self._times[-1] if self._times else 0.0

    def _rebuild(self) -> None:
        ev = self.events
        self._move_idx = [i for i, e in enumerate(ev) if e.kind == MOVE]
        self._move_times = [ev[i].t for i in self._move_idx]
        self._scroll_idx = [i for i, e in enumerate(ev) if e.kind == SCROLL]
        xs = [e.x for e in ev if e.kind in (MOVE, CLICK, SCROLL)]
        ys = [e.y for e in ev if e.kind in (MOVE, CLICK, SCROLL)]
        self._xbounds = (min(xs), max(xs)) if xs else (0, 1)
        self._ybounds = (min(ys), max(ys)) if ys else (0, 1)
        end = self.duration()

        # clicks: pair down/up per button
        bars, open_ = [], {}
        for i, e in enumerate(ev):
            if e.kind != CLICK:
                continue
            if e.pressed:
                open_[e.button] = (e.t, i)
            elif e.button in open_:
                t0, i0 = open_.pop(e.button)
                bars.append(Bar(t0, e.t, e.button[:1].upper(), (i0, i), kind=CLICK))
            else:
                bars.append(Bar(e.t, e.t, e.button[:1].upper(), (i,), kind=CLICK))
        for b, (t0, i0) in open_.items():
            bars.append(Bar(t0, end, b[:1].upper(), (i0,), kind=CLICK))
        self._click_bars = sorted(bars, key=lambda b: b.t0)

        # keys: pair by key id, assign lanes so overlapping keys stack
        bars, open_ = [], {}
        for i, e in enumerate(ev):
            if e.kind != KEY:
                continue
            if e.pressed:
                if e.key_id in open_:  # auto-repeat: keep the first press
                    continue
                open_[e.key_id] = (e.t, i, e.key_label)
            elif e.key_id in open_:
                t0, i0, label = open_.pop(e.key_id)
                bars.append(Bar(t0, e.t, label, (i0, i)))
            else:
                bars.append(Bar(e.t, e.t, e.key_label, (i,)))
        for _k, (t0, i0, label) in open_.items():
            bars.append(Bar(t0, end, label, (i0,)))
        bars.sort(key=lambda b: b.t0)
        lane_end: List[float] = []
        for b in bars:
            for lane, t_end in enumerate(lane_end):
                if b.t0 >= t_end:
                    b.lane = lane
                    lane_end[lane] = b.t1 + 1e-9
                    break
            else:
                if len(lane_end) < 6:
                    b.lane = len(lane_end)
                    lane_end.append(b.t1 + 1e-9)
                else:
                    b.lane = 5
        self._key_lanes = max(1, len(lane_end))
        self._key_bars = bars

    # --------------------------------------------------------- geometry
    def _layout_tracks(self) -> None:
        key_h = max(34, self._key_lanes * 18 + 10)
        spec = [("Mouse path", MOVE, 58), ("Clicks", CLICK, 34),
                ("Scroll", SCROLL, 26), ("Keys", KEY, key_h)]
        y = self.RULER_H
        self._tracks = []
        for name, kind, h in spec:
            self._tracks.append((name, kind, y, h))
            y += h
        self._content_bottom = y
        self.setMinimumHeight(y + self.SCROLL_H + 6)
        self.updateGeometry()

    def _track(self, kind):
        for name, k, y, h in self._tracks:
            if k == kind:
                return y, h
        return 0, 0

    def sizeHint(self) -> QSize:
        return QSize(900, self._content_bottom + self.SCROLL_H + 8)

    def track_width(self) -> int:
        return max(10, self.width() - self.HEADER_W)

    def t_to_x(self, t: float) -> float:
        return self.HEADER_W + (t - self.offset) * self.pps

    def x_to_t(self, x: float) -> float:
        return max(0.0, self.offset + (x - self.HEADER_W) / self.pps)

    def visible_range(self) -> Tuple[float, float]:
        return self.offset, self.offset + self.track_width() / self.pps

    def resizeEvent(self, e):
        self.hbar.setGeometry(self.HEADER_W, self._content_bottom + 4,
                              self.track_width(), self.SCROLL_H)
        self._update_scrollbar()
        super().resizeEvent(e)

    def _update_scrollbar(self) -> None:
        vis = self.track_width() / self.pps
        total = max(self.duration(), self.playhead) + vis * 0.3
        maximum = max(0, int((total - vis) * 1000))
        self.offset = min(self.offset, maximum / 1000.0)
        self.hbar.blockSignals(True)
        self.hbar.setRange(0, maximum)
        self.hbar.setPageStep(max(1, int(vis * 1000)))
        self.hbar.setSingleStep(max(1, int(vis * 50)))
        self.hbar.setValue(int(self.offset * 1000))
        self.hbar.blockSignals(False)
        self.hbar.setVisible(maximum > 0)

    def _on_scroll(self, v):
        self.offset = v / 1000.0
        self.update()

    # -------------------------------------------------------------- view
    def set_zoom(self, pps: float, anchor_x: Optional[float] = None) -> None:
        pps = min(self.MAX_PPS, max(self.MIN_PPS, pps))
        if anchor_x is None:
            anchor_x = self.t_to_x(self.playhead)
            if not (self.HEADER_W <= anchor_x <= self.width()):
                anchor_x = self.HEADER_W + self.track_width() / 2
        anchor_t = self.x_to_t(anchor_x)
        self.pps = pps
        self.offset = max(0.0, anchor_t - (anchor_x - self.HEADER_W) / self.pps)
        self._update_scrollbar()
        self.update()

    def zoom_in(self):
        self.set_zoom(self.pps * 1.5)

    def zoom_out(self):
        self.set_zoom(self.pps / 1.5)

    def zoom_fit(self):
        d = max(self.duration(), 1.0)
        self.pps = min(self.MAX_PPS, max(self.MIN_PPS, (self.track_width() - 24) / d))
        self.offset = 0.0
        self._update_scrollbar()
        self.update()

    def zoom_selection(self):
        if not self.selection:
            return
        a, b = self.selection
        if b - a <= 0:
            return
        self.pps = min(self.MAX_PPS, max(self.MIN_PPS, (self.track_width() - 40) / (b - a)))
        self.offset = max(0.0, a - 20 / self.pps)
        self._update_scrollbar()
        self.update()

    def ensure_visible(self, t: float) -> None:
        a, b = self.visible_range()
        if t < a or t > b:
            self.offset = max(0.0, t - (b - a) * 0.1)
            self._update_scrollbar()

    def set_playhead(self, t: float, follow: bool = False, emit: bool = False) -> None:
        self.playhead = max(0.0, t)
        if follow:
            self.ensure_visible(self.playhead)
        if emit:
            self.playheadChanged.emit(self.playhead)
        self.update()

    def set_selection(self, sel: Optional[Tuple[float, float]], emit: bool = True) -> None:
        if sel is not None:
            a, b = sorted(sel)
            sel = (max(0.0, a), max(0.0, b))
            if sel[1] - sel[0] < 1e-6:
                sel = None
        self.selection = sel
        if emit:
            self.selectionChanged.emit(sel)
        self.update()

    def select_events(self, indices, emit: bool = False) -> None:
        self.selected = set(indices)
        if emit:
            self.eventsSelected.emit(sorted(self.selected))
        self.update()

    def set_overlay(self, text: str) -> None:
        self.overlay_text = text
        self.update()

    # ---------------------------------------------------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(theme.SURFACE))

        t_a, t_b = self.visible_range()
        step, minor = self._tick_steps()

        # track backgrounds + grid
        for n, (name, kind, y, th) in enumerate(self._tracks):
            p.fillRect(QRectF(self.HEADER_W, y, w - self.HEADER_W, th),
                       QColor("#191c22" if n % 2 == 0 else "#1c1f26"))
        self._paint_grid(p, t_a, t_b, step, minor)

        # selection
        if self.selection:
            a, b = self.selection
            xa, xb = self.t_to_x(a), self.t_to_x(b)
            rect = QRectF(xa, self.RULER_H, xb - xa, self._content_bottom - self.RULER_H)
            p.fillRect(rect, QColor(91, 140, 255, 40))
            p.setPen(QPen(QColor(theme.ACCENT), 1.5))
            p.drawLine(QPointF(xa, 0), QPointF(xa, self._content_bottom))
            p.drawLine(QPointF(xb, 0), QPointF(xb, self._content_bottom))
            p.fillRect(QRectF(xa, 0, xb - xa, self.RULER_H), QColor(91, 140, 255, 55))

        # recorded region end marker
        if self.events:
            xe = self.t_to_x(self.duration())
            if xe < w:
                p.fillRect(QRectF(max(self.HEADER_W, xe), self.RULER_H, w - max(self.HEADER_W, xe),
                                  self._content_bottom - self.RULER_H), QColor(0, 0, 0, 60))

        p.save()
        p.setClipRect(QRectF(self.HEADER_W, 0, w - self.HEADER_W, h))
        self._paint_moves(p, t_a, t_b)
        self._paint_bars(p, self._click_bars, CLICK, t_a, t_b)
        self._paint_scrolls(p, t_a, t_b)
        self._paint_bars(p, self._key_bars, KEY, t_a, t_b)
        p.restore()

        self._paint_ruler(p, t_a, t_b, step, minor)
        self._paint_headers(p)

        # hover line
        if self._hover_t is not None and self._drag is None:
            x = self.t_to_x(self._hover_t)
            if x >= self.HEADER_W:
                p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.DashLine))
                p.drawLine(QPointF(x, self.RULER_H), QPointF(x, self._content_bottom))

        # playhead
        x = self.t_to_x(self.playhead)
        if x >= self.HEADER_W - 1:
            p.setPen(QPen(QColor(theme.RED), 1.6))
            p.drawLine(QPointF(x, 10), QPointF(x, self._content_bottom))
            head = QPolygonF([QPointF(x - 6, 2), QPointF(x + 6, 2), QPointF(x + 6, 9),
                              QPointF(x, 15), QPointF(x - 6, 9)])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.RED))
            p.drawPolygon(head)

        # drag ghost label
        if self._drag and self._drag.get("type") == "move_events":
            dt = self._drag.get("dt", 0.0)
            p.setPen(QColor(theme.TEXT))
            p.setFont(self._label_font)
            sign = "+" if dt >= 0 else "−"
            p.drawText(QPointF(self._drag["x"] + 10, self._drag["y"] - 8), f"{sign}{abs(dt):.3f}s")

        if not self.events or self.overlay_text:
            self._paint_overlay(p)
        p.end()

    def _tick_steps(self):
        target_px = 90
        step = _NICE_STEPS[-1]
        for s in _NICE_STEPS:
            if s * self.pps >= target_px:
                step = s
                break
        minor = step / (4 if step in (0.2, 2, 0.002, 0.02) else 5)
        if step in (15, 30, 60, 120, 1800, 3600):
            minor = step / 6 if step in (60, 3600) else step / 3
        return step, minor

    def _paint_grid(self, p, t_a, t_b, step, minor):
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        k = int(t_a / minor)
        t = k * minor
        while t <= t_b + minor:
            x = self.t_to_x(t)
            if x >= self.HEADER_W:
                major = abs(t / step - round(t / step)) < 1e-6
                p.setPen(QPen(QColor(255, 255, 255, 26 if major else 9), 1))
                p.drawLine(QPointF(x, self.RULER_H), QPointF(x, self._content_bottom))
            t += minor

    def _paint_ruler(self, p, t_a, t_b, step, minor):
        w = self.width()
        p.fillRect(QRectF(0, 0, w, self.RULER_H), QColor(theme.SURFACE_2))
        if self.selection:
            a, b = self.selection
            xa, xb = max(self.HEADER_W, self.t_to_x(a)), self.t_to_x(b)
            if xb > xa:
                p.fillRect(QRectF(xa, 0, xb - xa, self.RULER_H), QColor(91, 140, 255, 60))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(0, self.RULER_H - 1, w, self.RULER_H - 1)
        p.setFont(self._small)
        decimals = 0 if step >= 1 else (1 if step >= 0.1 else (2 if step >= 0.01 else 3))
        k = int(t_a / minor)
        t = k * minor
        while t <= t_b + minor:
            x = self.t_to_x(t)
            if x >= self.HEADER_W:
                major = abs(t / step - round(t / step)) < 1e-6
                p.setPen(QPen(QColor(theme.TEXT_FAINT if not major else theme.TEXT_DIM), 1))
                p.drawLine(QPointF(x, self.RULER_H - (10 if major else 5)), QPointF(x, self.RULER_H - 1))
                if major:
                    p.setPen(QColor(theme.TEXT_DIM))
                    p.drawText(QPointF(x + 4, 13), fmt_time(round(t / step) * step, decimals))
            t += minor

    def _paint_headers(self, p):
        p.fillRect(QRectF(0, 0, self.HEADER_W, self._content_bottom), QColor(theme.SURFACE))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(self.HEADER_W - 1, 0, self.HEADER_W - 1, self._content_bottom)
        p.setFont(self._small)
        p.setPen(QColor(theme.TEXT_FAINT))
        p.drawText(QRectF(10, 0, self.HEADER_W - 12, self.RULER_H), Qt.AlignVCenter | Qt.AlignLeft,
                   fmt_time(self.playhead, 3))
        for name, kind, y, h in self._tracks:
            color = QColor(theme.TRACK_COLORS[kind])
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(10, y + h / 2 - 4, 8, 8), 2, 2)
            p.setPen(QColor(theme.TEXT_DIM))
            p.setFont(self._label_font)
            p.drawText(QRectF(24, y, self.HEADER_W - 26, h), Qt.AlignVCenter | Qt.AlignLeft, name)
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawLine(0, y + h, self.width(), y + h)
        if self._tracks:
            _n, _k, y, h = self._tracks[0]
            p.setFont(self._small)
            p.setPen(QColor(theme.ACCENT))
            p.drawText(QPointF(24, y + h - 6), "X")
            p.setPen(QColor(theme.PURPLE))
            p.drawText(QPointF(36, y + h - 6), "Y")

    def _paint_moves(self, p, t_a, t_b):
        y0, h = self._track(MOVE)
        if not self._move_idx:
            return
        i = max(0, bisect.bisect_left(self._move_times, t_a) - 1)
        j = min(len(self._move_idx), bisect.bisect_right(self._move_times, t_b) + 1)
        if j <= i:
            return
        xmin, xmax = self._xbounds
        ymin, ymax = self._ybounds
        sx = max(1, xmax - xmin)
        sy = max(1, ymax - ymin)
        pad = 7
        path_x, path_y = QPainterPath(), QPainterPath()
        last_px = None
        first = True
        dots = []
        for n in range(i, j):
            idx = self._move_idx[n]
            e = self.events[idx]
            px = self.t_to_x(e.t)
            if last_px is not None and int(px) == int(last_px) and n != j - 1 and idx not in self.selected:
                continue
            last_px = px
            vx = y0 + pad + (1 - (e.x - xmin) / sx) * (h - 2 * pad)
            vy = y0 + pad + (1 - (e.y - ymin) / sy) * (h - 2 * pad)
            if first:
                path_x.moveTo(px, vx)
                path_y.moveTo(px, vy)
                first = False
            else:
                path_x.lineTo(px, vx)
                path_y.lineTo(px, vy)
            if idx in self.selected:
                dots.append(QPointF(px, vx))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(180, 140, 255, 150), 1.3))
        p.drawPath(path_y)
        p.setPen(QPen(QColor(theme.ACCENT), 1.5))
        p.drawPath(path_x)
        # density ticks along the bottom
        if self.pps > 30:
            p.setPen(QPen(QColor(91, 140, 255, 70), 1))
            for n in range(i, j):
                px = self.t_to_x(self._move_times[n])
                p.drawLine(QPointF(px, y0 + h - 3), QPointF(px, y0 + h - 1))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        for d in dots:
            p.drawEllipse(d, 3.5, 3.5)

    def _bar_rect(self, b: Bar, kind: str) -> QRectF:
        y0, h = self._track(kind)
        xa = self.t_to_x(b.t0)
        xb = self.t_to_x(b.t1)
        width = max(6.0, xb - xa)
        if kind == KEY:
            lane_h = 16
            return QRectF(xa, y0 + 5 + b.lane * (lane_h + 2), width, lane_h)
        return QRectF(xa, y0 + 6, width, h - 12)

    def _paint_bars(self, p, bars: List[Bar], kind: str, t_a, t_b):
        color = QColor(theme.TRACK_COLORS[kind])
        fm = QFontMetrics(self._small)
        p.setFont(self._small)
        drag_dt = self._drag.get("dt", 0.0) if self._drag and self._drag.get("type") == "move_events" else 0.0
        for b in bars:
            if b.t1 < t_a - 0.5 or b.t0 > t_b + 0.5:
                continue
            selected = any(i in self.selected for i in b.indices)
            r = self._bar_rect(b, kind)
            if selected and drag_dt:
                r.translate(drag_dt * self.pps, 0)
            fill = QColor(color)
            fill.setAlpha(230 if selected else 150)
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0, fill.lighter(115))
            grad.setColorAt(1, fill)
            p.setBrush(QBrush(grad))
            p.setPen(QPen(QColor("white"), 1.5) if selected else QPen(color.darker(140), 1))
            p.drawRoundedRect(r, 4, 4)
            if r.width() > fm.horizontalAdvance(b.label) + 8:
                p.setPen(QColor("#0d0f13"))
                p.drawText(r.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft, b.label)

    def _paint_scrolls(self, p, t_a, t_b):
        y0, h = self._track(SCROLL)
        color = QColor(theme.CYAN)
        for idx in self._scroll_idx:
            e = self.events[idx]
            if e.t < t_a - 0.1 or e.t > t_b + 0.1:
                continue
            x = self.t_to_x(e.t)
            cy = y0 + h / 2
            up = (e.dy or e.dx) > 0
            tri = QPolygonF([QPointF(x - 5, cy + (4 if up else -4)), QPointF(x + 5, cy + (4 if up else -4)),
                             QPointF(x, cy + (-5 if up else 5))])
            p.setPen(QPen(QColor("white"), 1.2) if idx in self.selected else Qt.NoPen)
            p.setBrush(color)
            p.drawPolygon(tri)

    def _paint_overlay(self, p):
        rect = QRectF(self.HEADER_W, self.RULER_H, self.track_width(), self._content_bottom - self.RULER_H)
        if self.overlay_text:
            p.fillRect(rect, QColor(10, 12, 16, 150))
            text = self.overlay_text
        else:
            text = "No recording yet — press Record (or your record hotkey) to start"
        f = QFont(self.font())
        f.setPointSizeF(11)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(rect, Qt.AlignCenter, text)

    # ------------------------------------------------------- hit testing
    def _kind_at_y(self, y) -> Optional[str]:
        for _n, kind, ty, h in self._tracks:
            if ty <= y < ty + h:
                return kind
        return None

    def event_hit(self, pos) -> Optional[Tuple[int, ...]]:
        kind = self._kind_at_y(pos.y())
        if kind in (CLICK, KEY):
            bars = self._click_bars if kind == CLICK else self._key_bars
            for b in reversed(bars):
                if self._bar_rect(b, kind).adjusted(-2, -1, 2, 1).contains(QPointF(pos)):
                    return b.indices
        elif kind == SCROLL:
            for idx in self._scroll_idx:
                if abs(self.t_to_x(self.events[idx].t) - pos.x()) <= 6:
                    return (idx,)
        elif kind == MOVE and self._move_idx:
            t = self.x_to_t(pos.x())
            n = bisect.bisect_left(self._move_times, t)
            best = None
            for c in (n - 1, n):
                if 0 <= c < len(self._move_times):
                    dx = abs(self.t_to_x(self._move_times[c]) - pos.x())
                    if dx <= 4 and (best is None or dx < best[0]):
                        best = (dx, self._move_idx[c])
            if best:
                return (best[1],)
        return None

    def _edge_hit(self, x) -> Optional[int]:
        if not self.selection:
            return None
        a, b = self.selection
        if abs(self.t_to_x(a) - x) <= 5:
            return 0
        if abs(self.t_to_x(b) - x) <= 5:
            return 1
        return None

    def _bar_at(self, pos):
        kind = self._kind_at_y(pos.y())
        if kind in (CLICK, KEY):
            bars = self._click_bars if kind == CLICK else self._key_bars
            for b in reversed(bars):
                if self._bar_rect(b, kind).contains(QPointF(pos)):
                    return b
        return None

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        x = pos.x()
        if x < self.HEADER_W or pos.y() >= self._content_bottom:
            return super().mousePressEvent(e)
        t = self.x_to_t(x)
        if e.button() == Qt.RightButton:
            self.contextMenuRequested.emit(e.globalPosition().toPoint(), t)
            return
        if e.button() != Qt.LeftButton:
            return
        if pos.y() < self.RULER_H:
            self._drag = {"type": "playhead"}
            self.set_playhead(t, emit=True)
            return
        edge = self._edge_hit(x)
        if edge is not None:
            self._drag = {"type": "edge", "edge": edge}
            return
        hit = None if self.read_only else self.event_hit(pos)
        if hit:
            if e.modifiers() & Qt.ControlModifier:
                sel = set(self.selected)
                if all(i in sel for i in hit):
                    sel -= set(hit)
                else:
                    sel |= set(hit)
                self.select_events(sel, emit=True)
            elif not all(i in self.selected for i in hit):
                self.select_events(hit, emit=True)
            self._drag = {"type": "move_events", "x0": x, "x": x, "y": pos.y(), "dt": 0.0}
            return
        if e.modifiers() & Qt.ShiftModifier:
            anchor = self.selection[0] if self.selection else self.playhead
            self._drag = {"type": "select", "anchor": anchor, "moved": True}
            self.set_selection((anchor, t))
            return
        self._drag = {"type": "select", "anchor": t, "x0": x, "moved": False}

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        x = pos.x()
        t = self.x_to_t(x)
        self._hover_t = t if x >= self.HEADER_W else None
        d = self._drag
        if d is None:
            if self._edge_hit(x) is not None and self.RULER_H <= pos.y() < self._content_bottom:
                self.setCursor(Qt.SizeHorCursor)
            elif x >= self.HEADER_W and pos.y() < self._content_bottom and self.event_hit(pos):
                self.setCursor(Qt.PointingHandCursor)
                hit = self.event_hit(pos)
                bar = self._bar_at(pos)
                if bar and len(bar.indices) == 2:
                    a, b = (self.events[i] for i in bar.indices)
                    tip = f"{a.describe()}\nheld {b.t - a.t:.3f}s  ·  at {fmt_time(a.t, 3)}"
                else:
                    ev = self.events[hit[0]]
                    tip = f"{ev.describe()}\nat {fmt_time(ev.t, 3)}"
                QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            else:
                self.unsetCursor()
                QToolTip.hideText()
            self.update()
            return
        if d["type"] == "playhead":
            self.set_playhead(t, emit=True)
        elif d["type"] == "edge":
            a, b = self.selection
            if d["edge"] == 0:
                a = t
            else:
                b = t
            if a > b:
                a, b = b, a
                d["edge"] = 1 - d["edge"]
            self.selection = (a, b)
            self.selectionChanged.emit(self.selection)
            self.update()
        elif d["type"] == "select":
            if not d["moved"] and abs(x - d.get("x0", x)) < 4:
                return
            d["moved"] = True
            self.set_selection((d["anchor"], t))
        elif d["type"] == "move_events":
            dx = x - d["x0"]
            d["x"], d["y"] = x, pos.y()
            if abs(dx) >= 3:
                dt = dx / self.pps
                if self.selected:
                    earliest = min(self.events[i].t for i in self.selected)
                    dt = max(dt, -earliest)
                d["dt"] = dt
            self.update()
        self._autoscroll(x)

    def _autoscroll(self, x):
        if x > self.width() - 10:
            self.offset += 20 / self.pps
            self._update_scrollbar()
        elif x < self.HEADER_W + 10 and self.offset > 0:
            self.offset = max(0.0, self.offset - 20 / self.pps)
            self._update_scrollbar()

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        if not d:
            return
        if d["type"] == "select" and not d["moved"]:
            self.set_selection(None)
            if self.selected:
                self.select_events([], emit=True)
            self.set_playhead(d["anchor"], emit=True)
        elif d["type"] == "move_events" and abs(d.get("dt", 0.0)) > 1e-6 and self.selected:
            self.moveEventsRequested.emit(sorted(self.selected), d["dt"])
        self.update()

    def mouseDoubleClickEvent(self, e):
        hit = self.event_hit(e.position().toPoint())
        if hit:
            self.eventActivated.emit(hit[0])

    def leaveEvent(self, e):
        self._hover_t = None
        self.update()

    def wheelEvent(self, e):
        delta = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.2 ** (delta / 120)
            self.set_zoom(self.pps * factor, e.position().x())
        else:
            vis = self.track_width() / self.pps
            self.offset = max(0.0, self.offset - (delta / 120) * vis * 0.1)
            self._update_scrollbar()
            self.update()
        e.accept()
