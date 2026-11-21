"""Main application window."""
from __future__ import annotations

import os
import time
from typing import List, Optional

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QSpinBox, QSplitter, QSystemTrayIcon, QTableView,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from .. import keys as K
from .. import model as M
from .. import winutil
from ..model import MOVE, Event, Macro
from ..settings import Settings
from . import icons, theme
from .event_table import EventFilter, EventTableModel
from .osd import OSD
from .settings_dialog import SettingsDialog
from .timeline import Timeline, fmt_time

IDLE, COUNTDOWN, RECORDING, PLAYING = "idle", "countdown", "recording", "playing"


class Bridge(QObject):
    """Thread-safe hop from background threads to the GUI thread."""
    hotkey = Signal(str)
    progress = Signal(int, int, float)
    waiting = Signal(float, int)
    finished = Signal(bool)


class SetEventsCommand(QUndoCommand):
    def __init__(self, win: "MainWindow", old, new, text):
        super().__init__(text)
        self.win, self.old, self.new = win, list(old), list(new)

    def redo(self):
        self.win._set_events(self.new)

    def undo(self):
        self.win._set_events(self.old)


def card(title: Optional[str] = None):
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 10, 14, 12)
    lay.setSpacing(8)
    if title:
        lbl = QLabel(title.upper())
        lbl.setObjectName("CardTitle")
        lay.addWidget(lbl)
    return frame, lay


def labeled(title: str, *widgets):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)
    lbl = QLabel(title.upper())
    lbl.setObjectName("CardTitle")
    v.addWidget(lbl)
    row = QHBoxLayout()
    row.setSpacing(6)
    for x in widgets:
        row.addWidget(x)
    v.addLayout(row)
    return w


def vsep():
    f = QFrame()
    f.setFixedWidth(1)
    f.setStyleSheet(f"background: {theme.BORDER};")
    return f


class MainWindow(QMainWindow):
    def __init__(self, open_path: Optional[str] = None):
        super().__init__()
        self.settings = Settings.load()
        self.events: List[Event] = []
        self.file_path: Optional[str] = None
        self.state = IDLE
        self.clipboard = None  # (events, length)
        self.recorder = None
        self.player = None
        self._rec_ctx = {}
        self._countdown_left = 0
        self._play_offset = 0.0
        self._play_duration = 0.0
        self._was_visible = True

        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(lambda _c: self._update_title())
        self.bridge = Bridge()
        self.bridge.hotkey.connect(self._on_hotkey)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.waiting.connect(self._on_waiting)
        self.bridge.finished.connect(self._on_play_finished)
        self.osd = OSD()

        self.setWindowIcon(icons.app_icon())
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_actions()
        self._build_tray()
        self._apply_window_settings()
        self._start_hotkeys()

        self.rec_timer = QTimer(self)
        self.rec_timer.setInterval(100)
        self.rec_timer.timeout.connect(self._tick_recording)
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._tick_countdown)

        self._set_events([])
        self._update_state_ui()
        self.resize(1180, 760)
        if open_path:
            QTimer.singleShot(0, lambda: self.open_file(open_path))

    # ================================================================= UI
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 10)
        root.setSpacing(12)

        # ---------- transport bar
        top, tl = card()
        row = QHBoxLayout()
        row.setSpacing(10)
        tl.addLayout(row)

        def transport(icon, tip, name="Transport"):
            b = QToolButton()
            b.setObjectName("Transport")
            b.setIcon(icon)
            b.setIconSize(QSize(30, 30))
            b.setToolTip(tip)
            b.setFixedSize(48, 48)
            return b

        self.btn_record = transport(icons.record(), "Record a new macro")
        self.btn_record.setProperty("role", "rec")
        self.btn_record.setCheckable(True)
        self.btn_record.clicked.connect(self._record_clicked)
        self.btn_play = transport(icons.play(), "Play the macro")
        self.btn_play.setProperty("role", "play")
        self.btn_play.setCheckable(True)
        self.btn_play.clicked.connect(self._play_clicked)
        self.btn_stop = transport(icons.stop(), "Stop")
        self.btn_stop.clicked.connect(lambda: self.stop_all(via_ui=True))
        for b in (self.btn_record, self.btn_play, self.btn_stop):
            row.addWidget(b)
        row.addSpacing(8)

        info = QVBoxLayout()
        info.setSpacing(0)
        self.lbl_time = QLabel("0.00s")
        self.lbl_time.setObjectName("BigTime")
        self.lbl_status = QLabel("Ready")
        self.lbl_status.setObjectName("Status")
        self.lbl_status.setMinimumWidth(10)
        from PySide6.QtWidgets import QSizePolicy
        self.lbl_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        info.addWidget(self.lbl_time)
        info.addWidget(self.lbl_status)
        row.addLayout(info, 1)

        def tool(icon, tip, slot, checkable=False):
            b = QToolButton()
            b.setIcon(icon)
            b.setIconSize(QSize(22, 22))
            b.setToolTip(tip)
            b.setFixedSize(40, 40)
            b.setCheckable(checkable)
            b.clicked.connect(slot)
            row.addWidget(b)
            return b

        self.btn_center = tool(icons.target(), "Glide the mouse to the screen centre", self.center_mouse)
        row.addSpacing(6)
        row.addWidget(vsep())
        row.addSpacing(6)
        tool(icons.new_file(), "New macro (Ctrl+N)", self.new_macro)
        tool(icons.folder(), "Open macro (Ctrl+O)", self.open_dialog)
        tool(icons.save(), "Save macro (Ctrl+S)", self.save)
        row.addSpacing(6)
        row.addWidget(vsep())
        row.addSpacing(6)
        self.btn_compact = tool(icons.compact(), "Compact mode — hide the editor", self._toggle_compact, True)
        self.btn_pin = tool(icons.pin(), "Stay on top of other windows", self._toggle_on_top, True)
        tool(icons.gear(), "Settings & hotkeys", self.open_settings)
        root.addWidget(top)

        # ---------- playback options
        pb, pl = card()
        prow = QHBoxLayout()
        prow.setSpacing(22)
        pl.addLayout(prow)
        s = self.settings
        self.spin_repeat = QSpinBox()
        self.spin_repeat.setRange(1, 1_000_000)
        self.spin_repeat.setValue(max(1, s.repeats))
        self.spin_repeat.setSuffix(" ×")
        self.spin_repeat.setMinimumWidth(90)
        self.chk_infinite = QCheckBox("Unlimited")
        self.chk_infinite.setChecked(s.infinite)
        self.chk_infinite.toggled.connect(lambda on: self.spin_repeat.setEnabled(not on))
        self.spin_repeat.setEnabled(not s.infinite)
        prow.addWidget(labeled("Repeat", self.spin_repeat, self.chk_infinite))

        self.spin_delay = QDoubleSpinBox()
        self.spin_delay.setRange(0, 100000)
        self.spin_delay.setDecimals(1)
        self.spin_delay.setValue(s.delay_value)
        self.spin_delay.setMinimumWidth(90)
        self.cmb_unit = QComboBox()
        self.cmb_unit.addItems(["seconds", "minutes", "hours"])
        self.cmb_unit.setCurrentText(s.delay_unit)
        prow.addWidget(labeled("Delay between runs", self.spin_delay, self.cmb_unit))

        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setRange(0.1, 20)
        self.spin_speed.setSingleStep(0.25)
        self.spin_speed.setDecimals(2)
        self.spin_speed.setSuffix(" ×")
        self.spin_speed.setValue(s.speed)
        self.spin_speed.setMinimumWidth(90)
        prow.addWidget(labeled("Speed", self.spin_speed))

        prog = QWidget()
        pv = QVBoxLayout(prog)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(6)
        ptop = QHBoxLayout()
        lbl = QLabel("PROGRESS")
        lbl.setObjectName("CardTitle")
        self.lbl_run = QLabel("")
        self.lbl_run.setObjectName("Dim")
        ptop.addWidget(lbl)
        ptop.addStretch(1)
        ptop.addWidget(self.lbl_run)
        pv.addLayout(ptop)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        pv.addWidget(self.progress)
        prow.addWidget(prog, 1)
        for w in (self.spin_repeat, self.spin_delay, self.spin_speed):
            w.valueChanged.connect(self._save_playback_options)
        self.chk_infinite.toggled.connect(self._save_playback_options)
        self.cmb_unit.currentTextChanged.connect(self._save_playback_options)
        root.addWidget(pb)

        # ---------- editor
        self.editor_card, el = card()
        erow = QHBoxLayout()
        erow.setSpacing(5)
        el.addLayout(erow)
        title = QLabel("TIMELINE EDITOR")
        title.setObjectName("CardTitle")
        erow.addWidget(title)
        erow.addSpacing(10)
        self._edit_buttons = {}

        def ebtn(key, text, tip, slot, menu=None):
            b = QToolButton()
            b.setObjectName("EditBtn")
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            if menu:
                b.setMenu(menu)
                b.setPopupMode(QToolButton.MenuButtonPopup)
            erow.addWidget(b)
            self._edit_buttons[key] = b
            return b

        ebtn("undo", "↶", "Undo (Ctrl+Z)", self.undo.undo)
        ebtn("redo", "↷", "Redo (Ctrl+Y)", self.undo.redo)
        erow.addSpacing(6)
        ebtn("cut", "Cut", "Cut selection (Ctrl+X)", self.cut)
        ebtn("copy", "Copy", "Copy selection (Ctrl+C)", self.copy)
        ebtn("paste", "Paste", "Paste at playhead (Ctrl+V)", self.paste)
        ebtn("delete", "Delete", "Delete selection or selected events (Del)", self.delete)
        erow.addSpacing(6)
        b = ebtn("rerecord", "● Re-record selection", "Record a new take that replaces the selected part (Ctrl+R)",
                 self.rerecord_selection)
        b.setStyleSheet(f"QToolButton {{ color: {theme.RED}; }}")
        ebtn("insertrec", "● Insert recording", "Record a new take and insert it at the playhead (Ctrl+I)",
             self.insert_recording)
        erow.addSpacing(6)
        more = QMenu(self)
        more.addAction("Insert pause at playhead…", self.insert_pause)
        more.addAction("Change speed of selection…", self.speed_selection)
        more.addAction("Trim macro to selection", self.trim_to_selection)
        more.addSeparator()
        more.addAction("Remove mouse moves (selection / all)", self.remove_moves)
        more.addAction("Simplify mouse moves…", self.thin_moves)
        more.addSeparator()
        more.addAction("Select all (Ctrl+A)", self.select_all)
        more.addAction("Clear selection", lambda: self.timeline.set_selection(None))
        tools = QToolButton()
        tools.setObjectName("EditBtn")
        tools.setText("More ▾")
        tools.setMenu(more)
        tools.setPopupMode(QToolButton.InstantPopup)
        erow.addWidget(tools)
        erow.addStretch(1)
        ebtn("playsel", "▶ Preview selection", "Play only the selected part once", self.play_selection)
        erow.addSpacing(6)
        ebtn("zout", "−", "Zoom out (Ctrl+scroll)", lambda: self.timeline.zoom_out())
        ebtn("zin", "+", "Zoom in (Ctrl+scroll)", lambda: self.timeline.zoom_in())
        ebtn("fit", "Fit", "Fit whole macro (Ctrl+0)", lambda: self.timeline.zoom_fit())

        self.timeline = Timeline()
        self.timeline.playheadChanged.connect(self._on_playhead)
        self.timeline.selectionChanged.connect(self._on_selection)
        self.timeline.eventsSelected.connect(self._on_timeline_events_selected)
        self.timeline.eventActivated.connect(self._focus_event_row)
        self.timeline.moveEventsRequested.connect(self._move_events)
        self.timeline.contextMenuRequested.connect(self._timeline_menu)

        table_panel = QWidget()
        tv = QVBoxLayout(table_panel)
        tv.setContentsMargins(0, 4, 0, 0)
        tv.setSpacing(6)
        th = QHBoxLayout()
        lbl = QLabel("EVENTS")
        lbl.setObjectName("CardTitle")
        th.addWidget(lbl)
        self.lbl_sel = QLabel("")
        self.lbl_sel.setObjectName("Faint")
        th.addWidget(self.lbl_sel, 1)
        self.chk_moves = QCheckBox("Show mouse moves")
        th.addWidget(self.chk_moves)
        tv.addLayout(th)
        self.table_model = EventTableModel(self.apply_edit)
        self.table_proxy = EventFilter()
        self.table_proxy.setSourceModel(self.table_model)
        self.chk_moves.toggled.connect(self.table_proxy.set_show_moves)
        self.table = QTableView()
        self.table.setModel(self.table_proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, w in enumerate((60, 90, 90, 120, 70, 70)):
            self.table.setColumnWidth(c, w)
        self.table.selectionModel().selectionChanged.connect(self._on_table_selection)
        tv.addWidget(self.table)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.timeline)
        split.addWidget(table_panel)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        el.addWidget(split, 1)
        root.addWidget(self.editor_card, 1)

        # ---------- status bar
        sb = self.statusBar()
        self.sb_events = QLabel()
        self.sb_duration = QLabel()
        self.sb_hotkeys = QLabel()
        sb.addWidget(self.sb_events)
        sb.addWidget(self.sb_duration)
        sb.addPermanentWidget(self.sb_hotkeys)
        self._update_hotkey_hint()

    def _build_actions(self):
        def act(text, seq, slot):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(seq))
            a.setShortcutContext(Qt.WindowShortcut)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        act("Undo", QKeySequence.Undo, self.undo.undo)
        act("Redo", "Ctrl+Y", self.undo.redo)
        act("Redo", "Ctrl+Shift+Z", self.undo.redo)
        act("Cut", QKeySequence.Cut, self.cut)
        act("Copy", QKeySequence.Copy, self.copy)
        act("Paste", QKeySequence.Paste, self.paste)
        act("Delete", QKeySequence.Delete, self.delete)
        act("Select all", QKeySequence.SelectAll, self.select_all)
        act("Save", QKeySequence.Save, self.save)
        act("Save as", "Ctrl+Shift+S", self.save_as)
        act("Open", QKeySequence.Open, self.open_dialog)
        act("New", QKeySequence.New, self.new_macro)
        act("Re-record", "Ctrl+R", self.rerecord_selection)
        act("Insert recording", "Ctrl+I", self.insert_recording)
        act("Zoom fit", "Ctrl+0", lambda: self.timeline.zoom_fit())
        act("Zoom in", "Ctrl+=", lambda: self.timeline.zoom_in())
        act("Zoom out", "Ctrl+-", lambda: self.timeline.zoom_out())
        act("Settings", "Ctrl+,", self.open_settings)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Show window", self._restore_window)
        menu.addSeparator()
        menu.addAction("Record", self._record_clicked)
        menu.addAction("Play", self._play_clicked)
        menu.addAction("Stop", lambda: self.stop_all())
        menu.addAction("Centre mouse", self.center_mouse)
        menu.addSeparator()
        menu.addAction("Quit", self._quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._restore_window() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)

    # =========================================================== settings
    def _apply_window_settings(self):
        s = self.settings
        self._set_on_top(s.always_on_top)
        self.setWindowOpacity(max(0.3, s.opacity / 100))
        self.tray.setVisible(s.show_tray_icon and QSystemTrayIcon.isSystemTrayAvailable())
        self._set_compact(s.compact_mode)

    def _set_on_top(self, on: bool):
        self.btn_pin.setChecked(on)
        self.btn_pin.setIcon(icons.pin(theme.ACCENT_HOVER if on else theme.TEXT_DIM))
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) != on:
            visible = self.isVisible()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
            if visible:
                self.show()

    def _toggle_on_top(self):
        self.settings.always_on_top = self.btn_pin.isChecked()
        self._set_on_top(self.settings.always_on_top)
        self.settings.save()

    def _set_compact(self, on: bool):
        self.btn_compact.setChecked(on)
        self.editor_card.setVisible(not on)
        self.statusBar().setVisible(not on)
        if on:
            self._full_size = self.size()
            QTimer.singleShot(0, lambda: self.resize(self.minimumSizeHint().width(), 10))
        elif getattr(self, "_full_size", None):
            self.resize(self._full_size)

    def _toggle_compact(self):
        self.settings.compact_mode = self.btn_compact.isChecked()
        self._set_compact(self.settings.compact_mode)
        self.settings.save()

    def _save_playback_options(self, *_):
        s = self.settings
        s.repeats = self.spin_repeat.value()
        s.infinite = self.chk_infinite.isChecked()
        s.delay_value = self.spin_delay.value()
        s.delay_unit = self.cmb_unit.currentText()
        s.speed = self.spin_speed.value()
        s.save()

    def open_settings(self):
        if self.hotkeys:
            self.hotkeys.enabled = False
        dlg = SettingsDialog(self.settings, self)
        try:
            if dlg.exec():
                self.settings = dlg.result_settings()
                self.settings.save()
                self._apply_window_settings()
                if self.hotkeys:
                    self.hotkeys.set_bindings(self.settings.hotkeys)
                self._update_hotkey_hint()
        finally:
            if self.hotkeys:
                self.hotkeys.enabled = True

    def _update_hotkey_hint(self):
        hk = self.settings.hotkeys
        parts = [f"{K.format_hotkey(hk.get('record', ''))} record",
                 f"{K.format_hotkey(hk.get('play', ''))} play",
                 f"{K.format_hotkey(hk.get('stop', ''))} stop",
                 f"{K.format_hotkey(hk.get('center', ''))} centre mouse"]
        self.sb_hotkeys.setText("   ·   ".join(parts))

    def _hk(self, action):
        return K.format_hotkey(self.settings.hotkeys.get(action, ""))

    # ============================================================ hotkeys
    def _start_hotkeys(self):
        self.hotkeys = None
        try:
            from ..backend import HotkeyManager
            self.hotkeys = HotkeyManager(self.bridge.hotkey.emit)
            self.hotkeys.set_bindings(self.settings.hotkeys)
            self.hotkeys.start()
        except Exception as exc:  # e.g. no display server on Linux
            self.hotkeys = None
            self.lbl_status.setText(f"Global hotkeys unavailable: {str(exc).splitlines()[0]}")

    def _on_hotkey(self, action: str):
        if action == "record":
            if self.state == RECORDING:
                self.stop_recording()
            elif self.state == COUNTDOWN:
                self._cancel_countdown()
            elif self.state == IDLE:
                self.start_recording("new")
        elif action == "play":
            if self.state == PLAYING:
                self.stop_playback()
            elif self.state == IDLE:
                self.start_playback()
        elif action == "stop":
            self.stop_all()
        elif action == "center":
            self.center_mouse()

    # ============================================================ events
    def _set_events(self, events):
        self.events = list(events)
        self.timeline.set_events(self.events)
        self.table_model.set_events(self.events)
        n = len(self.events)
        moves = sum(1 for e in self.events if e.kind == MOVE)
        self.sb_events.setText(f"{n:,} events ({n - moves:,} actions, {moves:,} moves)")
        self.sb_duration.setText(f"Length {fmt_time(M.duration(self.events), 2)}")
        self._update_time_label()
        self._update_state_ui()

    def apply_edit(self, new_events, text: str):
        if self.state != IDLE:
            return
        self.undo.push(SetEventsCommand(self, self.events, new_events, text))

    def _update_title(self):
        name = os.path.basename(self.file_path) if self.file_path else "Untitled"
        dirty = "" if self.undo.isClean() else " •"
        self.setWindowTitle(f"{name}{dirty} — {APP_NAME}")

    def _update_time_label(self, t: Optional[float] = None):
        if t is None:
            t = self.timeline.playhead
        self.lbl_time.setText(f"{fmt_time(t, 2)}  /  {fmt_time(M.duration(self.events), 2)}")

    def _update_state_ui(self):
        idle = self.state == IDLE
        has = bool(self.events)
        sel = self.timeline.selection is not None
        self.btn_record.setChecked(self.state in (RECORDING, COUNTDOWN))
        self.btn_play.setChecked(self.state == PLAYING)
        self.btn_record.setEnabled(self.state in (IDLE, RECORDING, COUNTDOWN))
        self.btn_play.setEnabled((idle and has) or self.state == PLAYING)
        self.btn_stop.setEnabled(not idle)
        for w in (self.spin_repeat, self.chk_infinite, self.spin_delay, self.cmb_unit, self.spin_speed):
            w.setEnabled(idle)
        if idle:
            self.spin_repeat.setEnabled(not self.chk_infinite.isChecked())
        b = self._edit_buttons
        b["undo"].setEnabled(idle and self.undo.canUndo())
        b["redo"].setEnabled(idle and self.undo.canRedo())
        b["cut"].setEnabled(idle and sel)
        b["copy"].setEnabled(idle and sel)
        b["paste"].setEnabled(idle and self.clipboard is not None)
        b["delete"].setEnabled(idle and (sel or bool(self.timeline.selected)))
        b["rerecord"].setEnabled(idle and sel)
        b["insertrec"].setEnabled(idle)
        b["playsel"].setEnabled(idle and sel)
        self.timeline.read_only = not idle
        self.table.setEnabled(idle)
        self._update_title()

    # --------------------------------------------------------- selections
    def _on_playhead(self, t):
        self._update_time_label(t)

    def _on_selection(self, sel):
        if sel:
            a, b = sel
            i, j = M.index_range(self.events, a, b)
            self.lbl_sel.setText(f"Selection {fmt_time(a, 3)} → {fmt_time(b, 3)}  ({b - a:.3f}s, {j - i} events)")
        else:
            self.lbl_sel.setText("")
        self._update_state_ui()

    def _on_timeline_events_selected(self, indices):
        self._syncing = True
        try:
            sm = self.table.selectionModel()
            sm.clearSelection()
            from PySide6.QtCore import QItemSelection, QItemSelectionModel
            selection = QItemSelection()
            first = None
            for i in indices:
                src = self.table_model.index(i, 0)
                px = self.table_proxy.mapFromSource(src)
                if px.isValid():
                    selection.select(px, self.table_proxy.index(px.row(), self.table_proxy.columnCount() - 1))
                    first = first or px
            sm.select(selection, QItemSelectionModel.Select)
            if first is not None:
                self.table.scrollTo(first, QAbstractItemView.PositionAtCenter)
        finally:
            self._syncing = False
        self._update_state_ui()

    def _on_table_selection(self, *_):
        if getattr(self, "_syncing", False):
            return
        rows = {self.table_proxy.mapToSource(ix).row() for ix in self.table.selectionModel().selectedRows()}
        self.timeline.select_events(rows)
        if len(rows) == 1:
            t = self.events[next(iter(rows))].t
            self.timeline.set_playhead(t, follow=True)
            self._update_time_label(t)
        self._update_state_ui()

    def _focus_event_row(self, index):
        if self.events[index].kind == MOVE and not self.chk_moves.isChecked():
            self.chk_moves.setChecked(True)
        self._on_timeline_events_selected([index])
        self.table.setFocus()

    # ============================================================ editing
    def _sel(self):
        return self.timeline.selection

    def copy(self):
        sel = self._sel()
        if not sel or self.state != IDLE:
            return
        a, b = sel
        self.clipboard = (M.slice_range(self.events, a, b), b - a)
        self.lbl_status.setText(f"Copied {b - a:.2f}s")
        self._update_state_ui()

    def cut(self):
        sel = self._sel()
        if not sel or self.state != IDLE:
            return
        self.copy()
        a, b = sel
        self.apply_edit(M.delete_range(self.events, a, b), "Cut")
        self.timeline.set_selection(None)
        self.timeline.set_playhead(a, emit=True)

    def paste(self):
        if not self.clipboard or self.state != IDLE:
            return
        clip, length = self.clipboard
        t = self.timeline.playhead
        self.apply_edit(M.insert_at(self.events, t, clip, length), "Paste")
        self.timeline.set_selection((t, t + length))

    def delete(self):
        if self.state != IDLE:
            return
        if self.table.hasFocus() or (not self._sel() and self.timeline.selected):
            idx = sorted(self.timeline.selected)
            if idx:
                self.apply_edit(M.delete_indices(self.events, idx), f"Delete {len(idx)} event(s)")
                self.timeline.select_events([])
            return
        sel = self._sel()
        if sel:
            self.apply_edit(M.delete_range(self.events, *sel), "Delete range")
            self.timeline.set_selection(None)
            self.timeline.set_playhead(sel[0], emit=True)

    def select_all(self):
        if self.events:
            self.timeline.set_selection((0.0, M.duration(self.events) + 0.001))

    def insert_pause(self):
        sec, ok = QInputDialog.getDouble(self, "Insert pause", "Pause length (seconds):", 1.0, 0.01, 86400, 2)
        if ok:
            self.apply_edit(M.insert_gap(self.events, self.timeline.playhead, sec), "Insert pause")

    def speed_selection(self):
        sel = self._sel()
        if not sel:
            QMessageBox.information(self, APP_NAME, "Select a range on the timeline first.")
            return
        pct, ok = QInputDialog.getDouble(self, "Change speed", "Speed of the selected part (%):\n"
                                         "200% = twice as fast, 50% = half speed", 100, 1, 10000, 0)
        if ok and pct != 100:
            factor = 100.0 / pct
            a, b = sel
            self.apply_edit(M.scale_range(self.events, a, b, factor), "Change speed")
            self.timeline.set_selection((a, a + (b - a) * factor))

    def trim_to_selection(self):
        sel = self._sel()
        if sel:
            self.apply_edit(M.trim_to(self.events, *sel), "Trim")
            self.timeline.set_selection(None)
            self.timeline.zoom_fit()

    def remove_moves(self):
        sel = self._sel() or (0.0, M.duration(self.events) + 1)
        self.apply_edit(M.remove_kind_in_range(self.events, sel[0], sel[1], MOVE), "Remove mouse moves")

    def thin_moves(self):
        ms, ok = QInputDialog.getInt(self, "Simplify mouse moves",
                                     "Keep at most one mouse move every N milliseconds:", 30, 1, 2000)
        if not ok:
            return
        sel = self._sel() or (0.0, M.duration(self.events) + 1)
        i, j = M.index_range(self.events, *sel)
        new = self.events[:i] + M.thin_moves(self.events[i:j], ms / 1000) + self.events[j:]
        self.apply_edit(new, "Simplify mouse moves")

    def _move_events(self, indices, dt):
        self.apply_edit(M.shift_events(self.events, indices, dt), "Move events")
        self.timeline.select_events([])

    def _timeline_menu(self, pos, t):
        m = QMenu(self)
        sel = self._sel()
        m.addAction("Set playhead here", lambda: (self.timeline.set_playhead(t, emit=True)))
        m.addSeparator()
        a = m.addAction("Cut", self.cut)
        a.setEnabled(bool(sel))
        a = m.addAction("Copy", self.copy)
        a.setEnabled(bool(sel))
        a = m.addAction("Paste at playhead", self.paste)
        a.setEnabled(self.clipboard is not None)
        a = m.addAction("Delete", self.delete)
        a.setEnabled(bool(sel or self.timeline.selected))
        m.addSeparator()
        a = m.addAction("Re-record selection", self.rerecord_selection)
        a.setEnabled(bool(sel))
        m.addAction("Insert recording here", lambda: (self.timeline.set_playhead(t, emit=True),
                                                      self.insert_recording()))
        m.addAction("Insert pause here…", lambda: (self.timeline.set_playhead(t, emit=True), self.insert_pause()))
        m.addSeparator()
        a = m.addAction("Preview selection", self.play_selection)
        a.setEnabled(bool(sel))
        a = m.addAction("Zoom to selection", self.timeline.zoom_selection)
        a.setEnabled(bool(sel))
        m.setEnabled(self.state == IDLE)
        m.exec(pos)

    # ========================================================== recording
    def _record_clicked(self):
        if self.state == RECORDING:
            self.stop_recording(via_ui=True)
        elif self.state == COUNTDOWN:
            self._cancel_countdown()
        elif self.state == IDLE:
            self.start_recording("new")
        self._update_state_ui()

    def rerecord_selection(self):
        if self.state != IDLE:
            return
        if not self._sel():
            QMessageBox.information(self, APP_NAME, "Drag on the timeline to select the part you want to re-record.")
            return
        self.start_recording("replace")

    def insert_recording(self):
        if self.state == IDLE:
            self.start_recording("insert")

    def start_recording(self, mode: str):
        if self.state != IDLE:
            return
        try:
            import pynput  # noqa: F401
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Input capture is unavailable:\n{exc}")
            return
        if mode == "replace":
            a, b = self._sel()
            self._rec_ctx = {"mode": mode, "start": a, "end": b}
        elif mode == "insert":
            self._rec_ctx = {"mode": mode, "start": self.timeline.playhead}
        else:
            self._rec_ctx = {"mode": "new", "start": 0.0}

        if mode in ("replace", "insert") and self.settings.move_cursor_before_rerecord:
            pos = M.cursor_position_at(self.events, self._rec_ctx["start"])
            if pos:
                try:
                    from pynput.mouse import Controller
                    Controller().position = pos
                except Exception:
                    pass

        if self.settings.countdown_s > 0:
            self.state = COUNTDOWN
            self._countdown_left = self.settings.countdown_s
            self._show_countdown()
            self.countdown_timer.start()
        else:
            self._begin_recording()
        self._update_state_ui()

    def _show_countdown(self):
        msg = f"Recording starts in {self._countdown_left}…   ({self._hk('stop')} to cancel)"
        self.lbl_status.setText(msg)
        self.timeline.set_overlay(msg)
        if self.settings.show_osd:
            self.osd.show_message(msg, theme.YELLOW)

    def _tick_countdown(self):
        self._countdown_left -= 1
        if self._countdown_left <= 0:
            self.countdown_timer.stop()
            self._begin_recording()
            self._update_state_ui()
        else:
            self._show_countdown()

    def _cancel_countdown(self):
        self.countdown_timer.stop()
        self.state = IDLE
        self.osd.hide()
        self.timeline.set_overlay("")
        self.lbl_status.setText("Recording cancelled")
        self._update_state_ui()

    def _begin_recording(self):
        from ..backend import Recorder
        s = self.settings
        self.recorder = Recorder(record_moves=s.record_mouse_moves, move_interval=s.move_interval_ms / 1000,
                                 record_keyboard=s.record_keyboard, record_mouse=s.record_mouse,
                                 ignore_key=self.hotkeys.is_hotkey_key if self.hotkeys else None)
        self.recorder.start()
        self.state = RECORDING
        ctx = self._rec_ctx
        if ctx["mode"] == "replace":
            desc = f"Re-recording {fmt_time(ctx['start'], 2)} → {fmt_time(ctx['end'], 2)}"
        elif ctx["mode"] == "insert":
            desc = f"Recording insert at {fmt_time(ctx['start'], 2)}"
        else:
            desc = "Recording"
        self._rec_desc = desc
        self.timeline.set_overlay(f"● {desc}…\nPress {self._hk('record')} to stop")
        if s.hide_while_recording:
            self._hide_for_task()
        self.rec_timer.start()
        self._tick_recording()

    def _tick_recording(self):
        if not self.recorder:
            return
        el = self.recorder.elapsed()
        self.lbl_time.setText(fmt_time(el, 1))
        msg = f"{self._rec_desc}  {fmt_time(el, 1)}  ·  {self.recorder.event_count()} events  ·  {self._hk('record')} to stop"
        self.lbl_status.setText(msg)
        if self.settings.show_osd:
            self.osd.show_message(f"REC  {fmt_time(el, 1)}   ·   {self._hk('record')} to stop", theme.RED)

    def stop_recording(self, via_ui: bool = False):
        if self.state != RECORDING or not self.recorder:
            return
        trim = winutil.window_rect_physical(self) if (via_ui and self.isVisible()) else None
        clip, length = self.recorder.stop(trim)
        self.recorder = None
        self.rec_timer.stop()
        self.state = IDLE
        self.osd.hide()
        self.timeline.set_overlay("")
        self._restore_after_task()
        ctx = self._rec_ctx
        if ctx["mode"] == "new":
            if clip:
                self.apply_edit(clip, "Record")
                self.timeline.set_selection(None)
                self.timeline.set_playhead(0.0, emit=True)
                self.timeline.zoom_fit()
            self.lbl_status.setText(f"Recorded {len(clip)} events ({fmt_time(M.duration(clip), 2)})")
        elif ctx["mode"] == "replace":
            a, b = ctx["start"], ctx["end"]
            self.apply_edit(M.replace_range(self.events, a, b, clip, length), "Re-record selection")
            self.timeline.set_selection((a, a + length))
            self.lbl_status.setText(f"Replaced {b - a:.2f}s with a new {length:.2f}s take")
        else:
            t = ctx["start"]
            self.apply_edit(M.insert_at(self.events, t, clip, length), "Insert recording")
            self.timeline.set_selection((t, t + length))
            self.lbl_status.setText(f"Inserted {length:.2f}s at {fmt_time(t, 2)}")
        self._update_state_ui()

    # =========================================================== playback
    def _play_clicked(self):
        if self.state == PLAYING:
            self.stop_playback()
        elif self.state == IDLE:
            self.start_playback()
        self._update_state_ui()

    def play_selection(self):
        sel = self._sel()
        if sel and self.state == IDLE:
            self.start_playback(events=M.slice_range(self.events, *sel), offset=sel[0], repeats=1, delay=0.0,
                                preview=True)

    def start_playback(self, events=None, offset: float = 0.0, repeats=None, delay=None, preview=False):
        if self.state != IDLE:
            return
        events = self.events if events is None else events
        if not events:
            self.lbl_status.setText("Nothing to play — record something first")
            return
        try:
            from ..backend import Player
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Playback is unavailable:\n{exc}")
            return
        s = self.settings
        if repeats is None:
            repeats = 0 if self.chk_infinite.isChecked() else self.spin_repeat.value()
        if delay is None:
            delay = self.spin_delay.value() * {"seconds": 1, "minutes": 60, "hours": 3600}[self.cmb_unit.currentText()]
        self._play_offset = offset
        self._play_duration = max(M.duration(events), 1e-6)
        self._play_runs = repeats
        b = self.bridge
        self.player = Player(events, repeats=repeats, delay=delay, speed=self.spin_speed.value(),
                             release_on_stop=s.release_keys_on_stop,
                             on_progress=b.progress.emit, on_wait=b.waiting.emit, on_finished=b.finished.emit)
        self.state = PLAYING
        self._play_started = time.time()
        self._last_run = 0
        if s.hide_while_playing and not preview:
            self._hide_for_task()
        self.player.start()
        self._update_state_ui()

    def _run_text(self, run, total):
        return f"Run {run:,} of {'∞' if total == 0 else f'{total:,}'}"

    def _on_progress(self, run, total, t):
        if self.state != PLAYING:
            return
        self._last_run = run
        abs_t = self._play_offset + t
        self.timeline.set_playhead(abs_t, follow=True)
        self._update_time_label(abs_t)
        self.progress.setValue(int(1000 * t / self._play_duration))
        self.lbl_run.setText(self._run_text(run, total))
        self.lbl_status.setText(f"Playing  ·  {self._run_text(run, total)}  ·  {self._hk('stop')} to stop")
        if self.settings.show_osd:
            self.osd.show_message(f"PLAY  {self._run_text(run, total)}   ·   {self._hk('stop')} to stop", theme.GREEN)

    def _on_waiting(self, remaining, next_run):
        if self.state != PLAYING:
            return
        total = self._play_runs
        txt = f"Next run ({next_run:,} of {'∞' if total == 0 else f'{total:,}'}) in {fmt_time(remaining, 0) if remaining >= 60 else f'{remaining:.1f}s'}"
        self.lbl_status.setText(f"Waiting  ·  {txt}  ·  {self._hk('stop')} to stop")
        self.lbl_run.setText(txt)
        self.progress.setValue(0)
        if self.settings.show_osd:
            self.osd.show_message(f"WAIT  {txt}", theme.YELLOW)

    def _on_play_finished(self, completed: bool):
        self.player = None
        self.state = IDLE
        self.osd.hide()
        self._restore_after_task()
        runs = self._last_run
        self.lbl_status.setText(f"Finished {runs:,} run(s)" if completed else f"Stopped during run {runs:,}")
        self.progress.setValue(1000 if completed else self.progress.value())
        self._update_state_ui()

    def stop_playback(self):
        if self.player:
            self.player.stop()

    def stop_all(self, via_ui: bool = False):
        if self.state == PLAYING:
            self.stop_playback()
        elif self.state == RECORDING:
            self.stop_recording(via_ui=via_ui)
        elif self.state == COUNTDOWN:
            self._cancel_countdown()

    # =========================================================== centring
    def center_mouse(self):
        try:
            from ..backend import animate_mouse_to_center
        except Exception as exc:
            self.lbl_status.setText(f"Unavailable: {exc}")
            return
        s = self.settings
        rec = self.recorder if self.state == RECORDING else None
        animate_mouse_to_center(s.center_target, s.center_duration_ms, s.center_easing,
                                on_step=rec.add_synthetic_move if rec else None)

    # ================================================== window behaviour
    def _hide_for_task(self):
        self._was_visible = self.isVisible()
        if self._was_visible:
            self.hide()

    def _restore_after_task(self):
        if self._was_visible and not self.isVisible():
            self._restore_window()
        self._was_visible = True

    def _restore_window(self):
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_()
        self.activateWindow()

    def changeEvent(self, e):
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.WindowStateChange and self.isMinimized() and \
                self.settings.minimize_to_tray and self.tray.isVisible():
            QTimer.singleShot(0, self.hide)
        super().changeEvent(e)

    # ============================================================== files
    def _confirm_discard(self) -> bool:
        if self.undo.isClean() or not self.events or not self.settings.confirm_unsaved:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to the current macro?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new_macro(self):
        if self.state != IDLE or not self._confirm_discard():
            return
        self.file_path = None
        self.undo.clear()
        self._set_events([])
        self.timeline.set_selection(None)
        self.timeline.set_playhead(0.0, emit=True)

    def open_dialog(self):
        if self.state != IDLE or not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open macro", self.settings.last_dir,
                                              f"Macros (*{M.FILE_EXTENSION});;All files (*)")
        if path:
            self.open_file(path)

    def open_file(self, path: str):
        try:
            macro = Macro.load(path)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Could not open file:\n{exc}")
            return
        self.file_path = path
        self.settings.last_dir = os.path.dirname(path)
        self.settings.save()
        self.undo.clear()
        self._set_events(macro.events)
        self.timeline.set_selection(None)
        self.timeline.set_playhead(0.0, emit=True)
        self.timeline.zoom_fit()
        self.lbl_status.setText(f"Opened {os.path.basename(path)}")

    def save(self) -> bool:
        if not self.file_path:
            return self.save_as()
        try:
            Macro(self.events, meta={"app_version": __version__}).save(self.file_path)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Could not save:\n{exc}")
            return False
        self.undo.setClean()
        self.lbl_status.setText(f"Saved {os.path.basename(self.file_path)}")
        self._update_title()
        return True

    def save_as(self) -> bool:
        start = os.path.join(self.settings.last_dir or "", "macro" + M.FILE_EXTENSION)
        path, _ = QFileDialog.getSaveFileName(self, "Save macro", start, f"Macros (*{M.FILE_EXTENSION})")
        if not path:
            return False
        if not path.lower().endswith(M.FILE_EXTENSION):
            path += M.FILE_EXTENSION
        self.file_path = path
        self.settings.last_dir = os.path.dirname(path)
        self.settings.save()
        return self.save()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls and self.state == IDLE and self._confirm_discard():
            self.open_file(urls[0].toLocalFile())

    # =============================================================== quit
    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self._shutdown()
        e.accept()
        QApplication.quit()

    def _quit(self):
        self.close()

    def _shutdown(self):
        self.stop_all()
        if self.recorder:
            self.recorder.stop()
        if self.player:
            self.player.stop()
        if self.hotkeys:
            self.hotkeys.stop()
        self.osd.close()
        self.tray.hide()
        self._save_playback_options()
