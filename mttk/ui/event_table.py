"""Editable list of all events (precise numeric editing)."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, List

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from .. import model as M
from .. import vision
from ..model import CHECK, CLICK, KEY, MOVE, SCROLL, Event
from . import theme

COLS = ["#", "Time (s)", "Delay (s)", "Action", "X", "Y", "Details"]
EDITABLE = {1, 2, 4, 5}

KIND_NAMES = {MOVE: "Mouse move", CLICK: "Click", SCROLL: "Scroll", KEY: "Key", CHECK: "Checkpoint"}
NO_POSITION = (KEY, CHECK)


class EventTableModel(QAbstractTableModel):
    def __init__(self, apply: Callable[[List[Event], str], None]):
        super().__init__()
        self.events: List[Event] = []
        self.checkpoints: dict = {}   # checkpoint id -> data, for the details column
        self.apply = apply

    def set_events(self, events):
        self.beginResetModel()
        self.events = list(events)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.events)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section]
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() in EDITABLE:
            e = self.events[index.row()]
            if index.column() in (4, 5) and e.kind in NO_POSITION:
                return f
            f |= Qt.ItemIsEditable
        return f

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        e = self.events[r]
        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == 0:
                return r + 1
            if c == 1:
                return round(e.t, 4) if role == Qt.EditRole else f"{e.t:.3f}"
            if c == 2:
                d = e.t - (self.events[r - 1].t if r > 0 else 0.0)
                return round(d, 4) if role == Qt.EditRole else f"{d:.3f}"
            if c == 3:
                if e.kind == CLICK:
                    return f"{e.button.title()} {'down' if e.pressed else 'up'}"
                if e.kind == KEY:
                    return f"Key {'down' if e.pressed else 'up'}"
                if e.kind == CHECK and self.checkpoints.get(e.ref) and not vision.compares(self.checkpoints[e.ref]):
                    return "Screenshot"
                return KIND_NAMES.get(e.kind, e.kind)
            if c == 4:
                return "" if e.kind in NO_POSITION else e.x
            if c == 5:
                return "" if e.kind in NO_POSITION else e.y
            if c == 6:
                if e.kind == KEY:
                    return e.key_label
                if e.kind == SCROLL:
                    return f"dx {e.dx}, dy {e.dy}"
                if e.kind == CHECK:
                    spec = self.checkpoints.get(e.ref)
                    return f"{(spec or {}).get('name', '')}  ·  {vision.describe(spec)}"
                return ""
        if role == Qt.ForegroundRole:
            if c == 3:
                if e.kind == CHECK and self.checkpoints.get(e.ref) and not vision.compares(self.checkpoints[e.ref]):
                    return QColor(theme.ACCENT)   # screenshot point, blue like on the timeline
                return QColor(theme.TRACK_COLORS.get(e.kind, theme.TEXT))
            if c in (0, 2):
                return QColor(theme.TEXT_FAINT)
        if role == Qt.TextAlignmentRole and c in (0, 1, 2, 4, 5):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        r, c = index.row(), index.column()
        e = self.events[r]
        try:
            if c == 1:
                new = M.set_event(self.events, r, e.at(float(value)))
                self.apply(new, "Edit event time")
            elif c == 2:
                self.apply(M.set_delay(self.events, r, float(value)), "Edit delay")
            elif c == 4:
                self.apply(M.set_event(self.events, r, replace(e, x=int(value))), "Edit X")
            elif c == 5:
                self.apply(M.set_event(self.events, r, replace(e, y=int(value))), "Edit Y")
            else:
                return False
        except (TypeError, ValueError):
            return False
        return True


class EventFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.show_moves = False

    def set_show_moves(self, on: bool):
        self.show_moves = on
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if self.show_moves:
            return True
        return self.sourceModel().events[row].kind != MOVE
