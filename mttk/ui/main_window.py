"""Main application window."""
from __future__ import annotations

import contextlib
import os
import time
from typing import List, Optional

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QSpinBox, QSplitter, QSystemTrayIcon, QTableView,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from .. import keys as K
from .. import model as M
from .. import notify
from .. import vision as V
from .. import winutil
from ..model import CHECK, MOVE, Event, Macro
from ..settings import Settings
from . import icons, theme
from .checkpoint_dialog import DELETE, CheckFailedDialog, CheckpointDialog
from .event_table import EventFilter, EventTableModel
from .osd import OSD
from .settings_dialog import SettingsDialog
from .timeline import Timeline, fmt_time

IDLE, COUNTDOWN, RECORDING, PLAYING = "idle", "countdown", "recording", "playing"
ARMED = "armed"  # re-record / insert chosen, waiting for the record hotkey to start
PHOTO_MAX_SIDE = 1920  # screenshots sent by screenshot points are shrunk to at most this size


def fmt_duration(t: float) -> str:
    """A length of time for phone messages: 0.7s, 12s, 1:52, 1:02:03."""
    return f"{t:.1f}s" if t < 9.95 else fmt_time(t, 0)


class Bridge(QObject):
    """Thread-safe hop from background threads to the GUI thread."""
    hotkey = Signal(str)
    progress = Signal(int, int, float)
    waiting = Signal(float, int)
    finished = Signal(bool)
    check_status = Signal(str, float, float, float)   # name, score, needed, seconds left
    check_result = Signal(object)                     # vision.CheckResult
    notified = Signal(bool, str)                      # phone alert sent?, error text
    log_sent = Signal(bool, str)                      # other phone message (log, picture) sent?, error


class SetEventsCommand(QUndoCommand):
    def __init__(self, win: "MainWindow", old, new, text, old_meta=None, new_meta=None):
        super().__init__(text)
        self.win, self.old, self.new = win, list(old), list(new)
        self.old_meta = dict(old_meta or {})
        self.new_meta = dict(new_meta if new_meta is not None else self.old_meta)

    def redo(self):
        self.win.meta = dict(self.new_meta)
        self.win._set_events(self.new)

    def undo(self):
        self.win.meta = dict(self.old_meta)
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
        self.meta: dict = {}   # macro metadata, e.g. {"screen": [left, top, w, h]}
        self.state = IDLE
        self.clipboard = None  # (events, length)
        self.recorder = None
        self.player = None
        self._rec_ctx = {}
        self._countdown_left = 0
        self._play_offset = 0.0
        self._resume_at = 0.0
        self._play_duration = 0.0
        self._play_runs = 1
        self._play_shift = (0, 0)
        self._play_map = None        # screens.ScreenMap the current playback uses
        self._was_visible = True
        self._rec_checkpoints = {}   # checkpoints taken during the current recording
        self._runner = None          # vision.CheckpointRunner while a macro with checkpoints plays
        self._check_failure = None
        self._fail_dialog = None
        self._alert_text = ""
        self.outbox = notify.Outbox()   # phone messages, sent in order (progress log, pictures, alerts)
        self._play_preview = False
        self._closing = False
        self._play_t0 = 0.0             # time.time() when playback started / the current run started
        self._run_t0 = 0.0
        self._run_end = None            # time.time() the last run ended (waiting for the next one)
        self._osd_note = ("", 0.0)   # short message shown instead of the REC / PLAY status
        self._capture_excluded = False  # this window and the indicator kept out of screenshots

        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(lambda _c: self._update_title())
        self.bridge = Bridge()
        self.bridge.hotkey.connect(self._on_hotkey)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.waiting.connect(self._on_waiting)
        self.bridge.finished.connect(self._on_play_finished)
        self.bridge.check_status.connect(self._on_check_status)
        self.bridge.check_result.connect(self._on_check_result)
        self.bridge.notified.connect(self._on_notified)
        self.bridge.log_sent.connect(self._on_log_sent)
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

        mouse_opts = QWidget()
        mv = QVBoxLayout(mouse_opts)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(4)
        lbl = QLabel("MOUSE")
        lbl.setObjectName("CardTitle")
        mv.addWidget(lbl)
        self.chk_relative = QCheckBox("Start from current cursor")
        self.chk_relative.setChecked(s.relative_start)
        self.chk_relative.setToolTip("Shift the whole recording so it starts where your mouse pointer is now,\n"
                                     "instead of where it was when you recorded it.")
        self.chk_adapt = QCheckBox("Adapt to screen resolution")
        self.chk_adapt.setChecked(s.adapt_resolution)
        self.chk_adapt.setToolTip("Rescale mouse positions when the macro was recorded on a different resolution\n"
                                  "(e.g. recorded on 2560×1440, played on 1920×1080).")
        mv.addWidget(self.chk_relative)
        mv.addWidget(self.chk_adapt)
        prow.addWidget(mouse_opts)

        screen_opts = QWidget()
        sg = QGridLayout(screen_opts)
        sg.setContentsMargins(0, 0, 0, 0)
        sg.setHorizontalSpacing(6)
        sg.setVerticalSpacing(4)
        lbl = QLabel("SCREEN")
        lbl.setObjectName("CardTitle")
        sg.addWidget(lbl, 0, 0, 1, 2)
        self.cmb_record_on = QComboBox()
        self.cmb_record_on.setToolTip("Which screen new recordings belong to. Positions are stored relative to it,\n"
                                      "so the macro can be played on any screen, whatever is connected.")
        self.cmb_play_on = QComboBox()
        self.cmb_play_on.setToolTip("Which screen to play on. Mouse positions are moved (and rescaled) from the\n"
                                    "screen they were recorded on, and never end up off-screen.")
        sg.addWidget(QLabel("Record on"), 1, 0)
        sg.addWidget(self.cmb_record_on, 1, 1)
        sg.addWidget(QLabel("Play on"), 2, 0)
        sg.addWidget(self.cmb_play_on, 2, 1)
        prow.addWidget(screen_opts)

        safety = QWidget()
        sv = QVBoxLayout(safety)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(4)
        lbl = QLabel("SAFETY")
        lbl.setObjectName("CardTitle")
        sv.addWidget(lbl)
        self.chk_checks = QCheckBox("Screen checkpoints")
        self.chk_checks.setChecked(s.checkpoints_enabled)
        self.chk_checks.setToolTip("At each checkpoint (\u25c6 on the timeline) the macro pauses until the screen "
                                   "looks like its picture.\nIf it doesn't within the wait time, the macro stops, "
                                   "releases all keys and sends an alert.\nScreenshot points (\U0001f4f7) send a "
                                   "picture to your phone. Off: checkpoints and screenshot points are skipped.")
        self.lbl_alerts = QLabel("")
        self.lbl_alerts.setTextFormat(Qt.RichText)
        self.lbl_alerts.setToolTip("Where alerts go when a checkpoint fails (Settings \u2192 Checkpoints & alerts)")
        self.lbl_alerts.linkActivated.connect(lambda _l: self.open_settings("alerts"))
        sv.addWidget(self.chk_checks)
        sv.addWidget(self.lbl_alerts)
        prow.addWidget(safety)
        self._update_alerts_label()

        prog = QWidget()
        pv = QVBoxLayout(prog)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(6)
        ptop = QHBoxLayout()
        lbl = QLabel("PROGRESS")
        lbl.setObjectName("CardTitle")
        self.lbl_run = QLabel("")
        self.lbl_run.setObjectName("Dim")
        self.lbl_run.setTextFormat(Qt.RichText)
        self.lbl_run.linkActivated.connect(self._playhead_to_start)
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
        self.chk_relative.toggled.connect(self._save_playback_options)
        self.chk_adapt.toggled.connect(self._save_playback_options)
        self.chk_checks.toggled.connect(self._save_playback_options)
        self.chk_adapt.toggled.connect(lambda _on: self._update_screen_label())
        self.cmb_record_on.activated.connect(self._screen_choice_changed)
        self.cmb_play_on.activated.connect(self._screen_choice_changed)
        self._remember_layout()
        self._refresh_screen_choices()
        app = QGuiApplication.instance()
        for sig in (app.screenAdded, app.screenRemoved, app.primaryScreenChanged):
            sig.connect(lambda *_: QTimer.singleShot(400, self._on_screens_changed))
        for scr in app.screens():
            scr.geometryChanged.connect(lambda *_: QTimer.singleShot(400, self._on_screens_changed))
        app.screenAdded.connect(lambda scr: scr.geometryChanged.connect(
            lambda *_: QTimer.singleShot(400, self._on_screens_changed)))
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
        b = ebtn("rerecord", "● Re-record selection", "Get ready to record a new take that replaces the selected part; recording starts when you press the record key (Ctrl+R)",
                 self.rerecord_selection)
        b.setStyleSheet(f"QToolButton {{ color: {theme.RED}; }}")
        ebtn("insertrec", "● Insert recording", "Get ready to record a new take inserted at the playhead; recording starts when you press the record key (Ctrl+I)",
             self.insert_recording)
        cp_menu = QMenu(self)
        cp_menu.addAction("\u25c6 Screen checkpoint at the playhead\u2026", self.add_checkpoint_dialog)
        cp_menu.addAction("\U0001f4f7 Send a screenshot at the playhead\u2026",
                          lambda: self.add_checkpoint_dialog(photo=True))
        b = ebtn("checkpoint", "\u25c6 Checkpoint", "", self.add_checkpoint_dialog, menu=cp_menu)
        b.setStyleSheet(f"QToolButton {{ color: {theme.GREEN}; }}")
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
        self.sb_screen = QLabel()
        self.sb_hotkeys = QLabel()
        sb.addWidget(self.sb_events)
        sb.addWidget(self.sb_duration)
        sb.addWidget(self.sb_screen)
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
        s.relative_start = self.chk_relative.isChecked()
        s.adapt_resolution = self.chk_adapt.isChecked()
        s.checkpoints_enabled = self.chk_checks.isChecked()
        s.save()

    def open_settings(self, tab=""):
        with self._hotkeys_paused():
            dlg = SettingsDialog(self.settings, self, tab=tab if isinstance(tab, str) else "")
            if dlg.exec():
                self.settings = dlg.result_settings()
                self.settings.save()
                self._apply_window_settings()
                if self.hotkeys:
                    self.hotkeys.set_bindings(self.settings.hotkeys)
                self._update_hotkey_hint()
                self._update_alerts_label()

    def _update_hotkey_hint(self):
        hk = self.settings.hotkeys
        parts = [f"{K.format_hotkey(hk.get('record', ''))} record",
                 f"{K.format_hotkey(hk.get('play', ''))} play",
                 f"{K.format_hotkey(hk.get('stop', ''))} stop",
                 f"{K.format_hotkey(hk.get('center', ''))} centre mouse"]
        if hk.get("checkpoint"):
            parts.append(f"{K.format_hotkey(hk['checkpoint'])} checkpoint")
        self.sb_hotkeys.setText("   ·   ".join(parts))
        cp = self._hk("checkpoint")
        self._edit_buttons["checkpoint"].setToolTip(
            "Add a screen checkpoint at the playhead: a picture of how the screen should look at that moment. "
            "During playback the macro checks it and stops (with an alert) if the screen looks wrong."
            + (f"\n{cp} adds one straight away, also while recording or playing." if cp else "")
            + "\nThe arrow adds a screenshot point instead: it sends a picture of the screen to your phone "
              "when playback gets there.")

    def _hk(self, action):
        return K.format_hotkey(self.settings.hotkeys.get(action, ""))

    # ============================================================ hotkeys
    def _start_hotkeys(self):
        self.hotkeys = None
        try:
            from ..backend import HotkeyManager
            self.hotkeys = HotkeyManager(self.bridge.hotkey.emit, should_consume=self._should_consume_hotkey)
            self.hotkeys.set_bindings(self.settings.hotkeys)
            self.hotkeys.start()
        except Exception as exc:  # e.g. no display server on Linux
            self.hotkeys = None
            self.lbl_status.setText(f"Global hotkeys unavailable: {str(exc).splitlines()[0]}")

    def _should_consume_hotkey(self, action: str) -> bool:
        """Called from the keyboard hook thread: swallow this key press system-wide?

        The stop key (Esc by default) only does something during playback, so
        everywhere else it keeps working normally - including inside recordings.
        """
        if action == "stop":
            return self.state == PLAYING
        if action == "checkpoint":
            return self.state in (IDLE, RECORDING, PLAYING)
        return True

    def _on_hotkey(self, action: str):
        if action == "record":
            if self.state == RECORDING:
                self.stop_recording()
            elif self.state == COUNTDOWN:
                self._cancel_countdown()
            elif self.state == ARMED:
                self._launch_recording()
            elif self.state == IDLE:
                if self._sel():
                    self.rerecord_selection(start_now=True)
                else:
                    self.start_recording("new")
        elif action == "play":
            if self.state == PLAYING:
                self.stop_playback()
            elif self.state == IDLE:
                self.play()
        elif action == "stop":
            # stops playback (or cancels an armed re-record); recording is
            # stopped with the record key
            if self.state == PLAYING:
                self.stop_playback()
            elif self.state == ARMED:
                self._cancel_countdown()
        elif action == "center":
            self.center_mouse()
        elif action == "checkpoint":
            self.hotkey_checkpoint()

    # ============================================================ events
    def _set_events(self, events):
        self.events = list(events)
        cps = self._checkpoints()
        self.timeline.set_checkpoint_info(cps)
        self.timeline.set_events(self.events)
        self.table_model.checkpoints = cps
        self.table_model.set_events(self.events)
        n = len(self.events)
        moves = sum(1 for e in self.events if e.kind == MOVE)
        checks = sum(1 for e in self.events if e.kind == CHECK)
        txt = f"{n:,} events ({n - moves - checks:,} actions, {moves:,} moves)"
        if checks:
            txt += f"  \u00b7  {checks} checkpoint{'s' if checks != 1 else ''}"
        self.sb_events.setText(txt)
        self.sb_duration.setText(f"Length {fmt_time(M.duration(self.events), 2)}")
        self._update_screen_label()
        self._update_time_label()
        self._update_state_ui()

    def apply_edit(self, new_events, text: str, meta: Optional[dict] = None):
        if self.state != IDLE:
            return
        self.undo.push(SetEventsCommand(self, self.events, new_events, text, self.meta, meta))

    # ------------------------------------------------- screens / resolution
    def _refresh_screen_choices(self):
        """Fill the Record on / Play on lists with the screens connected now."""
        from ..screens import describe
        mons = winutil.monitors()
        screens = [(describe(mons, i), i) for i in range(len(mons))]
        lists = ((self.cmb_record_on, [("Screen under the mouse", "auto")] + screens + [("All screens", "all")],
                  self.settings.record_on),
                 (self.cmb_play_on, [("Same screen(s) as recorded", "recorded")] + screens,
                  self.settings.play_on))
        for combo, items, current in lists:
            combo.blockSignals(True)
            combo.clear()
            for text, data in items:
                combo.addItem(text, data)
            i = next((n for n, (_t, d) in enumerate(items) if d == current and type(d) is type(current)), 0)
            combo.setCurrentIndex(i)
            combo.blockSignals(False)

    def _screen_choice_changed(self, *_):
        self.settings.record_on = self.cmb_record_on.currentData()
        self.settings.play_on = self.cmb_play_on.currentData()
        self.settings.save()
        self._update_screen_label()

    def _on_screens_changed(self):
        self._remember_layout()
        self._refresh_screen_choices()
        self._update_screen_label()

    def _remember_layout(self):
        """Keep the monitor layouts this PC has had, so old macros recorded on a
        screen that is unplugged now can still be placed exactly."""
        from ..screens import remember_layout, same_layout
        mons = winutil.monitors()
        known = self.settings.known_layouts or []
        if not known or not same_layout(known[0], mons):
            self.settings.known_layouts = remember_layout(known, mons)
            self.settings.save()

    def _upgrade_legacy_meta(self) -> bool:
        """Macros saved before 0.0.4 only stored the main screen. Work out the
        layout they were recorded on (connected now or seen before) and keep it
        in the macro, so it's written to the file on the next save."""
        from ..screens import find_legacy_layout
        if self.meta.get("monitors") or not self.events:
            return False
        found = find_legacy_layout(self.meta, self.events,
                                   [winutil.monitors()] + list(self.settings.known_layouts or []))
        if found:
            self.meta.update(found)
        return bool(found)

    def _screen_map(self):
        """Maps this macro's positions onto the screens connected right now."""
        from ..screens import ScreenMap
        return ScreenMap(self.meta, winutil.monitors(), self.settings.play_on,
                         scale=self.chk_adapt.isChecked(), events=self.events,
                         known_layouts=self.settings.known_layouts)

    def _update_screen_label(self):
        if not self.events:
            self.sb_screen.setText("")
            return
        m = self._screen_map()
        txt = m.summary()
        if not self.chk_adapt.isChecked() and not m.is_identity():
            txt += "  (not rescaling)"
        self.sb_screen.setText(txt)

    def _prepare_for_playback(self, events, first_run=None):
        """Map to this screen and, for "start from current cursor", shift so the
        clip starts at the pointer. ``first_run`` (resuming mid-macro) gets the
        same shift as ``events`` so later full runs line up with it."""
        screen_map = self._screen_map()
        events = screen_map.map_events(events)
        if first_run is not None:
            first_run = screen_map.map_events(first_run)
        self._play_map = screen_map  # checkpoint areas are mapped like the positions
        self._play_shift = (0, 0)
        if self.chk_relative.isChecked():
            start = M.first_position(first_run if first_run is not None else events)
            if start:
                cx, cy = winutil.cursor_pos()
                dx, dy = cx - start[0], cy - start[1]
                self._play_shift = (dx, dy)  # checkpoint areas move with the mouse positions
                events = M.offset_events(events, dx, dy)
                if first_run is not None:
                    first_run = M.offset_events(first_run, dx, dy)
        return events, first_run

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
        self.btn_record.setChecked(self.state in (RECORDING, COUNTDOWN, ARMED))
        self.btn_play.setChecked(self.state == PLAYING)
        self.btn_record.setEnabled(self.state in (IDLE, RECORDING, COUNTDOWN, ARMED))
        self.btn_play.setEnabled((idle and has) or self.state == PLAYING)
        self.btn_stop.setEnabled(not idle)
        for w in (self.spin_repeat, self.chk_infinite, self.spin_delay, self.cmb_unit, self.spin_speed,
                  self.chk_relative, self.chk_adapt, self.cmb_record_on, self.cmb_play_on, self.chk_checks):
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
        b["checkpoint"].setEnabled(idle)
        b["playsel"].setEnabled(idle and sel)
        if idle:
            self._update_play_hint()
        self.timeline.read_only = not idle
        self.table.setEnabled(idle)
        self._update_title()

    def _resume_point(self) -> float:
        """Where Play starts when nothing is selected: the playhead, unless it's
        at the very start or at / past the end (then the macro plays from 0)."""
        t = self.timeline.playhead
        return t if self.events and 0.0 < t < M.duration(self.events) else 0.0

    def _update_play_hint(self):
        """The "▶ ..." line under the transport: what Play will do right now."""
        if self.state != IDLE:
            return
        sel = self.timeline.selection
        rec = self._hk("record")
        if sel:
            a, z = sel
            self.lbl_run.setText(f"▶ Play uses selection {fmt_time(a, 2)} → {fmt_time(z, 2)}  ·  "
                                 f"● {rec} re-records it")
            self.btn_play.setToolTip("Play the selected part (click an empty spot on the timeline to clear)")
            self.btn_record.setToolTip(f"Re-record the selected part (starts when you press {rec}; "
                                       "click an empty spot on the timeline to clear the selection)")
            return
        self.btn_record.setToolTip(f"Record a new macro ({rec})")
        if self._resume_point() > 0:
            self.lbl_run.setText(f"▶ Play continues from {fmt_time(self._resume_point(), 2)}  ·  "
                                 f"<a href='start' style='color:{theme.ACCENT}'>play from the start</a>")
            self.btn_play.setToolTip("Play from the playhead (the red line)")
        else:
            if self.lbl_run.text().startswith("▶"):
                self.lbl_run.setText("")
            self.btn_play.setToolTip("Play the macro")

    def _playhead_to_start(self, *_):
        self.timeline.set_playhead(0.0, follow=True, emit=True)

    # --------------------------------------------------------- selections
    def _on_playhead(self, t):
        self._update_time_label(t)
        self._update_play_hint()

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
        if self.events[index].kind == CHECK:
            self.edit_checkpoint(index)
            return
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
        clip = M.slice_range(self.events, a, b)
        cps = self._checkpoints()
        # checkpoint pictures travel with the clip, so pasting into another macro keeps them
        specs = {r: cps[r] for r in M.checkpoint_refs(clip) if r in cps}
        self.clipboard = (clip, b - a, specs)
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
        clip, length, specs = self.clipboard
        t = self.timeline.playhead
        self.apply_edit(M.insert_at(self.events, t, clip, length), "Paste",
                        M.with_checkpoints(self.meta, specs) if specs else None)
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
        m.addAction("\u25c6 Add checkpoint here…", lambda: (self.timeline.set_playhead(t, emit=True),
                                                            self.add_checkpoint_dialog()))
        m.addAction("\U0001f4f7 Send a screenshot here…", lambda: (self.timeline.set_playhead(t, emit=True),
                                                                        self.add_checkpoint_dialog(photo=True)))
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
        elif self.state == ARMED:
            self._launch_recording()
        elif self.state == IDLE:
            if self._sel():
                self.rerecord_selection()  # arms; F8 starts it
            else:
                self.start_recording("new")
        self._update_state_ui()

    def rerecord_selection(self, start_now: bool = False):
        """Re-record the selected range. From the UI this arms and waits for the
        record key; ``start_now`` (the record key itself) starts right away."""
        if self.state != IDLE:
            return
        if not self._sel():
            QMessageBox.information(self, APP_NAME, "Drag on the timeline to select the part you want to re-record.")
            return
        self.start_recording("replace", wait_for_key=not start_now)

    def insert_recording(self):
        if self.state == IDLE:
            self.start_recording("insert")

    def start_recording(self, mode: str, wait_for_key: bool = True):
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

        if mode in ("replace", "insert") and wait_for_key and self.hotkeys:
            # Clicking Re-record / Insert happens in our window, so don't start
            # yet: wait until the user is set up and presses the record key.
            self.state = ARMED
            self._show_armed()
            self._update_state_ui()
            return
        self._launch_recording()

    def _show_armed(self):
        what = "Re-record" if self._rec_ctx["mode"] == "replace" else "Insert recording"
        rec, stop = self._hk("record"), self._hk("stop")
        self.lbl_status.setText(f"{what} ready  ·  press {rec} to start  ·  {stop} or Stop to cancel")
        self.timeline.set_overlay(f"● {what} ready\nPress {rec} to start recording  ·  {stop} to cancel")
        if self.settings.show_osd:
            self.osd.show_message(f"{what}: press {rec} to start   ·   {stop} to cancel", theme.YELLOW)

    def _launch_recording(self):
        """Start the recording set up in ``_rec_ctx`` (after a countdown, if set)."""
        if self.state not in (IDLE, ARMED):
            return
        mode = self._rec_ctx["mode"]
        if mode in ("replace", "insert") and self.settings.move_cursor_before_rerecord:
            pos = M.cursor_position_at(self.events, self._rec_ctx["start"])
            if pos:
                pos = self._screen_map().point(*pos)
            if pos:
                try:
                    from ..backend import set_mouse_position
                    set_mouse_position(pos[0], pos[1], self.settings.mouse_move_mode)
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
        msg = f"Recording starts in {self._countdown_left}…   ({self._hk('record')} to cancel)"
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
                                 ignore_key=self._recording_ignored_keys())
        self._rec_checkpoints = {}
        self._osd_note = ("", 0.0)
        from ..screens import resolve_record_on
        mons = winutil.monitors()
        self._rec_ctx["monitors"] = mons
        self._rec_ctx["record_on"] = resolve_record_on(s.record_on, mons, winutil.cursor_pos())
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

    def _recording_ignored_keys(self):
        """Hotkeys that act during recording (record/play/centre) are never recorded.
        The stop key is not one of them, so e.g. Esc is recorded like any other key."""
        if not self.hotkeys:
            return None
        keys = frozenset(self.hotkeys.keys_for(("record", "play", "center", "checkpoint")))
        return lambda name: name in keys

    def _tick_recording(self):
        if not self.recorder:
            return
        el = self.recorder.elapsed()
        self.lbl_time.setText(fmt_time(el, 1))
        if self._noting():
            note = self._osd_note[0]
            self.lbl_status.setText(note)
            if self.settings.show_osd:
                self.osd.show_message(note, theme.GREEN)
            return
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
        meta = dict(self.meta)
        rec_checkpoints, self._rec_checkpoints = self._rec_checkpoints, {}
        if ctx["mode"] == "new" or not self.events:
            if clip:
                from ..screens import layout_meta
                meta.update(layout_meta(ctx["monitors"], ctx["record_on"]))
        else:
            # a take recorded now (maybe on other screens): store it in the
            # macro's own coordinates so it lines up with the rest
            from ..screens import ALL
            screen_map = self._screen_map()
            clip = screen_map.inverse_events(clip)
            # checkpoints taken during the take: their areas too
            rec_checkpoints = {ref: self._spec_to_macro(spec, screen_map)
                               for ref, spec in rec_checkpoints.items()}
            meta["monitors"] = [list(r) for r in screen_map.recorded]
            meta["record_on"] = screen_map.record_on
            ref = screen_map.record_on if screen_map.record_on != ALL else 0
            meta["screen"] = list(screen_map.recorded[ref])
        if rec_checkpoints:
            meta = M.with_checkpoints(meta, rec_checkpoints)
        if ctx["mode"] == "new":
            if clip:
                self.apply_edit(clip, "Record", meta)
                self.timeline.set_selection(None)
                self.timeline.set_playhead(0.0, emit=True)
                self.timeline.zoom_fit()
            self.lbl_status.setText(f"Recorded {len(clip)} events ({fmt_time(M.duration(clip), 2)})")
        elif ctx["mode"] == "replace":
            a, b = ctx["start"], ctx["end"]
            self.apply_edit(M.replace_range(self.events, a, b, clip, length), "Re-record selection", meta)
            self.timeline.set_selection((a, a + length))
            self.lbl_status.setText(f"Replaced {b - a:.2f}s with a new {length:.2f}s take")
        else:
            t = ctx["start"]
            self.apply_edit(M.insert_at(self.events, t, clip, length), "Insert recording", meta)
            self.timeline.set_selection((t, t + length))
            self.lbl_status.setText(f"Inserted {length:.2f}s at {fmt_time(t, 2)}")
        self._update_state_ui()

    # =========================================================== playback
    def _play_clicked(self):
        if self.state == PLAYING:
            self.stop_playback()
        elif self.state == IDLE:
            self.play()
        self._update_state_ui()

    def play(self):
        """Play button / play hotkey: the selected range if there is one, otherwise
        the macro from the playhead (the first run continues from there, any
        further runs play the whole macro)."""
        sel = self._sel()
        if sel:
            i, j = M.index_range(self.events, *sel)
            if j > i:
                self.start_playback(events=M.slice_range(self.events, *sel), offset=sel[0])
                return
        self.start_playback(resume_at=self._resume_point())

    def play_selection(self):
        sel = self._sel()
        if sel and self.state == IDLE:
            self.start_playback(events=M.slice_range(self.events, *sel), offset=sel[0], repeats=1, delay=0.0,
                                preview=True)

    def start_playback(self, events=None, offset: float = 0.0, repeats=None, delay=None, preview=False,
                       resume_at: float = 0.0):
        """``resume_at`` > 0 makes the first run start that far into ``events``."""
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
        self._resume_at = resume_at if 0.0 < resume_at < M.duration(events) else 0.0
        start_text = self._start_text(events, repeats, delay, offset)
        first_run = M.resume_from(events, self._resume_at) if self._resume_at else None
        b = self.bridge
        events, first_run = self._prepare_for_playback(events, first_run)
        self._runner = None
        self._check_failure = None
        on_check = None
        refs = M.checkpoint_refs(events) | M.checkpoint_refs(first_run or [])
        if refs and self.chk_checks.isChecked():
            runner = V.CheckpointRunner(self._checkpoints(), to_screen=self._playback_to_screen,
                                        on_status=b.check_status.emit, on_result=b.check_result.emit)
            runner.prepare(refs)  # checkpoints without a picture are skipped (shown red on the timeline)
            offset, resume_at = self._play_offset, self._resume_at

            def check_at(e, run):  # player thread
                return runner.check(e.ref, run, offset + e.t + (resume_at if run == 1 else 0.0))
            self._runner, on_check = runner, check_at
        self.player = Player(events, repeats=repeats, delay=delay, speed=self.spin_speed.value(),
                             release_on_stop=s.release_keys_on_stop,
                             on_progress=b.progress.emit, on_wait=b.waiting.emit, on_finished=b.finished.emit,
                             first_run=first_run, mouse_mode=s.mouse_move_mode, on_check=on_check)
        if self._runner:
            self._runner.cancel = self.player.stop_event
            self._exclude_us_from_capture(True)
        self.state = PLAYING
        self._play_started = time.time()
        self._last_run = 0
        self._play_preview = preview
        self._play_t0 = self._run_t0 = time.time()
        self._run_end = None
        if not preview and s.notify_on_start:
            self._phone("▶️ Playback started", start_text)
        if s.hide_while_playing and not preview:
            self._hide_for_task()
        if self._runner:
            time.sleep(0.1)  # let Windows apply the screenshot exclusion before the first check
        self.player.start()
        self._update_state_ui()

    def _run_text(self, run, total):
        return f"Run {run:,} of {'∞' if total == 0 else f'{total:,}'}"

    # ----------------------------------------------- phone progress log
    def _macro_name(self) -> str:
        return os.path.basename(self.file_path) if self.file_path else "Untitled macro"

    def _phone(self, title: str, message: str, image=b"", priority: str = "default", droppable: bool = False,
               ping: bool = False, on_done=None) -> bool:
        """Queue a phone message; messages go out one by one, in order. False if alerts are off."""
        cfg = notify.config_from_settings(self.settings)
        if not notify.enabled(cfg):
            return False
        self.outbox.put(cfg, title, message, image, priority=priority, ping=ping, droppable=droppable,
                        on_done=on_done or self.bridge.log_sent.emit)
        return True

    def _on_log_sent(self, ok: bool, error: str):
        if not ok:
            self.sb_screen.setText(f"⚠ Phone message failed: {error}")

    def _start_text(self, events, repeats, delay, offset) -> str:
        """What the "playback started" message says."""
        parts = ["runs forever" if repeats == 0 else f"{repeats:,} run{'s' if repeats != 1 else ''}"]
        if delay > 0 and repeats != 1:
            parts.append(f"{fmt_duration(delay)} between runs")
        parts.append(f"{fmt_duration(M.duration(events))} each")
        speed = self.spin_speed.value()
        if abs(speed - 1.0) > 1e-6:
            parts.append(f"speed {speed:g}×")
        txt = f"{self._macro_name()}: " + ", ".join(parts) + "."
        if offset > 0:
            txt += f" Only the selection {fmt_time(offset, 1)}–{fmt_time(offset + M.duration(events), 1)}."
        if self._resume_at:
            txt += f" Starting at {fmt_time(offset + self._resume_at, 1)}."
        cps = self._checkpoints()
        refs = [e.ref for e in events if e.kind == CHECK and e.ref in cps]
        if refs and self.chk_checks.isChecked():
            compare = sum(1 for r in refs if V.compares(cps[r]))
            photos = sum(1 for r in refs if V.sends(cps[r]))
            bits = []
            if compare:
                bits.append(f"{compare} checkpoint{'s' if compare != 1 else ''}")
            if photos:
                bits.append(f"{photos} screenshot{'s' if photos != 1 else ''}")
            if bits:
                txt += " Each run: " + " and ".join(bits) + "."
        return txt

    def _on_run_started(self, run, total):
        now = time.time()
        took = ((self._run_end or now) - self._run_t0) if run > 1 else None
        self._run_t0, self._run_end = now, None
        s = self.settings
        if self._play_preview or not s.notify_each_run or (run == 1 and s.notify_on_start):
            return
        txt = self._macro_name()
        if took is None:
            txt += ": starting."
        else:
            txt += (f": run {run - 1:,} took {fmt_duration(took)}, "
                    f"{fmt_duration(now - self._play_t0)} since the start.")
        self._phone(f"\U0001f501 {self._run_text(run, total)}", txt, priority="low", droppable=True)

    def _on_progress(self, run, total, t):
        if self.state != PLAYING:
            return
        if run != self._last_run:
            self._on_run_started(run, total)
        self._last_run = run
        if run == 1:
            t += self._resume_at  # the first run started part-way in
        abs_t = self._play_offset + t
        self.timeline.set_playhead(abs_t, follow=True)
        self._update_time_label(abs_t)
        self.progress.setValue(int(1000 * t / self._play_duration))
        self.lbl_run.setText(self._run_text(run, total))
        if self._noting():
            return
        self.lbl_status.setText(f"Playing  ·  {self._run_text(run, total)}  ·  {self._hk('stop')} to stop")
        if self.settings.show_osd:
            self.osd.show_message(f"PLAY  {self._run_text(run, total)}   ·   {self._hk('stop')} to stop", theme.GREEN)

    def _on_waiting(self, remaining, next_run):
        if self.state != PLAYING:
            return
        if self._run_end is None:
            self._run_end = time.time()  # the run before the wait ended: its length leaves the wait out
        total = self._play_runs
        txt = f"Next run ({next_run:,} of {'∞' if total == 0 else f'{total:,}'}) in {fmt_time(remaining, 0) if remaining >= 60 else f'{remaining:.1f}s'}"
        self.lbl_run.setText(txt)
        self.progress.setValue(0)
        if self._noting():
            return
        self.lbl_status.setText(f"Waiting  ·  {txt}  ·  {self._hk('stop')} to stop")
        if self.settings.show_osd:
            self.osd.show_message(f"WAIT  {txt}", theme.YELLOW)

    def _on_play_finished(self, completed: bool):
        self.player = None
        self.state = IDLE
        if self._runner:
            self._runner = None
            self._exclude_us_from_capture(False)
        failure, self._check_failure = self._check_failure, None
        self.osd.hide()
        self._restore_after_task()
        runs = self._last_run
        self.lbl_status.setText(f"Finished {runs:,} run(s)" if completed else f"Stopped during run {runs:,}")
        self.progress.setValue(1000 if completed else self.progress.value())
        self._update_state_ui()
        if failure:
            self.lbl_status.setText(f"\u25a0 Stopped at checkpoint \u201c{failure.name}\u201d: {failure.summary}")
            self.lbl_run.setText(f"<span style='color:{theme.RED}'>Checkpoint failed in run {failure.run:,}</span>")
            if self.settings.show_osd:
                self.osd.show_message(f"STOPPED  \u00b7  checkpoint \u201c{failure.name}\u201d failed", theme.RED,
                                      hide_after_ms=8000)
            self._show_failure(failure)
        elif self.settings.notify_on_finish and not self._play_preview and not self._closing:
            took = fmt_duration(time.time() - self._play_t0)
            if completed:
                self._phone("✅ Macro finished",
                            f"{self._macro_name()} finished {runs:,} run{'s' if runs != 1 else ''} in {took}.")
            else:
                self._phone("⏹️ Macro stopped",
                            f"{self._macro_name()} was stopped during {self._run_text(runs, self._play_runs).lower()}, "
                            f"{fmt_time(self.timeline.playhead, 1)} into the macro, after {took}.")

    def stop_playback(self):
        if self.player:
            self.player.stop()

    def stop_all(self, via_ui: bool = False):
        if self.state == PLAYING:
            self.stop_playback()
        elif self.state == RECORDING:
            self.stop_recording(via_ui=via_ui)
        elif self.state in (COUNTDOWN, ARMED):
            self._cancel_countdown()

    # ======================================================= checkpoints
    def _checkpoints(self) -> dict:
        return M.checkpoints_of(self.meta)

    def _next_checkpoint_name(self, photo: bool = False) -> str:
        cps = self._checkpoints()
        refs = [e.ref for e in self.events if e.kind == CHECK]
        photos = sum(1 for r in refs if r in cps and not V.compares(cps[r]))
        if photo:
            return f"Screenshot {photos + 1}"
        return f"Checkpoint {len(refs) - photos + len(self._rec_checkpoints) + 1}"

    @contextlib.contextmanager
    def _hotkeys_paused(self):
        if self.hotkeys:
            self.hotkeys.enabled = False
        try:
            yield
        finally:
            if self.hotkeys:
                self.hotkeys.enabled = True

    @contextlib.contextmanager
    def _without_us(self):
        """Screenshots taken inside this block leave out our own windows (this
        window, dialogs, the indicator), so they never end up in a checkpoint."""
        # already left out for the whole playback: they stay that way afterwards
        staying = (self, self.osd) if self._capture_excluded else ()
        hidden = [w for w in QApplication.topLevelWidgets()
                  if w.isVisible() and not any(w is s for s in staying)
                  and winutil.set_exclude_from_capture(int(w.winId()), True)]
        if hidden:
            time.sleep(0.12)  # give Windows a frame or two to redraw without them
        try:
            yield
        finally:
            for w in hidden:
                winutil.set_exclude_from_capture(int(w.winId()), False)

    def _grab_without_us(self, rect):
        with self._without_us():
            return V.grab(rect)

    def _exclude_us_from_capture(self, on: bool):
        """While a macro with checkpoints plays, keep this window and the
        indicator out of the screenshots it compares."""
        self._capture_excluded = on
        winutil.set_exclude_from_capture(int(self.winId()), on)
        self.osd.exclude_from_capture(on)

    def _take_checkpoint_picture(self, near=None):
        """Screenshot of the whole monitor at ``near`` (default: the mouse pointer)."""
        x, y = near or winutil.cursor_pos()
        mon = winutil.monitor_rect_at(x, y)
        img = self._grab_without_us(mon) if mon else None
        if img is None or img.isNull():
            return None, None
        return img, mon

    def _new_checkpoint_spec(self, img, mon, photo: bool = False) -> dict:
        """``photo``: a screenshot point (sends a picture, doesn't compare)."""
        s = self.settings
        spec = V.new_checkpoint(img, mon, winutil.primary_screen_rect(), self._next_checkpoint_name(photo),
                                s.checkpoint_threshold, s.checkpoint_timeout)
        if photo:
            spec["compare"] = False
            spec["send"] = True
        return spec

    # Checkpoint areas are stored in the macro's coordinates, like its mouse
    # positions, and go through the same Record on / Play on screen mapping.
    def _checkpoint_to_screen(self, rect, screen=None):
        """A checkpoint area -> where it is on the screens now (Play on, rescaling)."""
        return list(self._screen_map().rect(rect))

    def _playback_to_screen(self, rect, screen=None):
        """The same during playback: the mapping it started with, plus "start from current cursor"."""
        x, y, w, h = (self._play_map or self._screen_map()).rect(rect)
        dx, dy = self._play_shift
        return [x + dx, y + dy, w, h]

    def _spec_to_macro(self, spec: dict, screen_map=None, shift=(0, 0)) -> dict:
        """A checkpoint just taken (areas on the screens now) -> the macro's
        coordinates. ``shift`` is undone first ("start from current cursor")."""
        if screen_map is None:
            if not self.events and not self.meta.get("monitors"):
                return spec  # nothing recorded yet: the recording will use the screens now
            screen_map = self._screen_map()
        out = dict(spec)
        for key in ("image_rect", "region"):
            x, y, w, h = spec[key]
            out[key] = list(screen_map.inverse_rect([x - shift[0], y - shift[1], w, h]))
        return out

    def _note(self, text: str, color: str = theme.GREEN, ms: int = 3000):
        """A short message in the status line and the on-screen indicator. While
        recording or playing it stands in for the REC / PLAY status for ``ms``."""
        self.lbl_status.setText(text)
        self._osd_note = (text, time.time() + ms / 1000)
        if self.state == RECORDING:
            self._tick_recording()
        elif self.settings.show_osd:
            # while playing, the next progress update brings PLAY back instead of hiding it
            self.osd.show_message(text, color, hide_after_ms=0 if self.state == PLAYING else ms)

    def _noting(self) -> bool:
        """Is a short message from ``_note`` still showing?"""
        note, until = self._osd_note
        return bool(note) and time.time() < until

    def _playback_moment(self) -> Optional[float]:
        """Where in the macro playback is right now, in macro time (None before it starts)."""
        pos = self.player.position() if self.player else None
        if pos is None:
            return None
        run, t = pos
        if run == 1:
            t += self._resume_at  # the first run started part-way in
        return self._play_offset + t

    def _checkpoint_during_playback(self):
        """Checkpoint key while playing: the screen as it is now, at the moment
        of the macro that's playing. The run in progress plays on unchanged; the
        new checkpoint is checked from the next playback on."""
        mon = winutil.monitor_rect_at(*winutil.cursor_pos())
        img = t = None
        if mon:
            with self._without_us():
                # read right before the screenshot: everything played so far is in
                # it, so the checkpoint goes after those events and before the rest
                t = self._playback_moment()
                img = V.grab(mon)
        if img is None or img.isNull() or t is None:
            self._note("Couldn't take a screenshot for the checkpoint", theme.RED)
            return
        # undo what this playback did to the positions (Play on screen, "start from
        # current cursor"), so next time the picture moves with them again
        spec = self._spec_to_macro(self._new_checkpoint_spec(img, mon), self._play_map or self._screen_map(),
                                   self._play_shift)
        ref = V.new_id()
        # apply_edit refuses edits while playing; this one doesn't touch the events being played
        self.undo.push(SetEventsCommand(self, self.events, M.add_event(self.events, Event(t, CHECK, ref=ref)),
                                        "Add checkpoint", self.meta, M.with_checkpoints(self.meta, {ref: spec})))
        self._note(f"◆ {spec['name']} added at {fmt_time(t, 2)}  ·  checked from the next playback")

    def hotkey_checkpoint(self):
        """Checkpoint key: save the screen as it is now. While recording or
        playing, at the current moment of the recording / macro; otherwise at
        the playhead."""
        if self.state == PLAYING:
            self._checkpoint_during_playback()
            return
        if self.state not in (RECORDING, IDLE):
            return
        img, mon = self._take_checkpoint_picture()
        if img is None:
            self._note("Couldn't take a screenshot for the checkpoint", theme.RED)
            return
        ref = V.new_id()
        spec = self._new_checkpoint_spec(img, mon)
        if self.state == RECORDING and self.recorder:
            self._rec_checkpoints[ref] = spec  # converted with the take when recording stops
            t = self.recorder.add_checkpoint(ref)
            self._note(f"\u25c6 {spec['name']} saved at {fmt_time(t, 1)}")
        elif self.state == IDLE:
            t = self.timeline.playhead
            spec = self._spec_to_macro(spec)
            self.apply_edit(M.add_event(self.events, Event(t, CHECK, ref=ref)), "Add checkpoint",
                            M.with_checkpoints(self.meta, {ref: spec}))
            self._note(f"\u25c6 {spec['name']} added at {fmt_time(t, 2)}  \u00b7  double-click it on the "
                       "timeline to choose the area")

    def add_checkpoint_dialog(self, *_, photo: bool = False):
        """Button / menu: picture of the screen now, edited before it's added at the playhead.
        ``photo``: a screenshot point, which sends a picture instead of comparing."""
        if self.state != IDLE:
            return
        t = self.timeline.playhead
        near = None
        pos = M.cursor_position_at(self.events, t)
        if pos:  # the monitor the macro is working on at that moment
            near = self._screen_map().point(*pos)
        img, mon = self._take_checkpoint_picture(near)
        if img is None:
            QMessageBox.warning(self, APP_NAME, "Couldn't take a screenshot.")
            return
        with self._hotkeys_paused():
            dlg = CheckpointDialog(self._spec_to_macro(self._new_checkpoint_spec(img, mon, photo)), t,
                                   self._grab_without_us, self._checkpoint_to_screen, self, is_new=True,
                                   max_time=max(t, M.duration(self.events)),
                                   notify_cfg=notify.config_from_settings(self.settings))
            if dlg.exec() != QDialog.Accepted:
                return
        ref = V.new_id()
        spec, t = dlg.result_spec(), dlg.result_time()
        self.apply_edit(M.add_event(self.events, Event(t, CHECK, ref=ref)),
                        "Add checkpoint" if V.compares(spec) else "Add screenshot point",
                        M.with_checkpoints(self.meta, {ref: spec}))
        what = "Checkpoint" if V.compares(spec) else "Screenshot point"
        self.lbl_status.setText(f"{what} added at {fmt_time(t, 2)}")

    def edit_checkpoint(self, index: int):
        if self.state != IDLE or not 0 <= index < len(self.events) or self.events[index].kind != CHECK:
            return
        e = self.events[index]
        spec = self._checkpoints().get(e.ref)
        if not spec:
            r = QMessageBox.question(self, APP_NAME, "This checkpoint has no picture (it may have been pasted "
                                     "from another macro). Delete it?")
            if r == QMessageBox.Yes:
                self.apply_edit(M.delete_indices(self.events, [index]), "Delete checkpoint")
            return
        with self._hotkeys_paused():
            dlg = CheckpointDialog(spec, e.t, self._grab_without_us, self._checkpoint_to_screen, self,
                                   max_time=max(e.t, M.duration(self.events)),
                                   notify_cfg=notify.config_from_settings(self.settings))
            r = dlg.exec()
        if r == DELETE:
            self.apply_edit(M.delete_indices(self.events, [index]), "Delete checkpoint")
            self.timeline.select_events([])
        elif r == QDialog.Accepted:
            t = dlg.result_time()
            events = self.events if t == e.t else M.set_event(self.events, index, e.at(t))
            self.apply_edit(events, "Edit checkpoint", M.with_checkpoints(self.meta, {e.ref: dlg.result_spec()}))

    def _checkpoint_index(self, ref: str, t: float) -> Optional[int]:
        found = [i for i, e in enumerate(self.events) if e.kind == CHECK and e.ref == ref]
        return min(found, key=lambda i: abs(self.events[i].t - t)) if found else None

    def _show_checkpoint(self, ref: str, t: float):
        i = self._checkpoint_index(ref, t)
        if i is not None:
            self.timeline.set_selection(None)
            self.timeline.select_events([i], emit=True)
            self.timeline.set_playhead(self.events[i].t, follow=True, emit=True)
        self._restore_window()

    def _edit_checkpoint_ref(self, ref: str, t: float):
        i = self._checkpoint_index(ref, t)
        if i is not None:
            self.edit_checkpoint(i)

    # -------------------------------------------------- during playback
    def _on_check_status(self, name, score, needed, left):
        if self.state != PLAYING:
            return
        txt = f"Checking \u201c{name}\u201d: {score * 100:.0f}% (needs {needed * 100:.0f}%), waiting {left:.1f}s more"
        self.lbl_status.setText(txt + f"  \u00b7  {self._hk('stop')} to stop")
        if self.settings.show_osd:
            self.osd.show_message(f"CHECK  {txt}", theme.YELLOW)

    def _on_check_result(self, res):
        if res.passed:
            if res.photo is not None or res.error:  # a checkpoint that sends a picture
                self._send_photo(res)
            return
        self._check_failure = res
        self._alert_failure(res)

    def _send_photo(self, res):
        lines = [self._failure_when(res) + "."]
        if res.compared:
            lines.append(f"✓ The screen matches ({res.score * 100:.0f}%).")
        if res.error:
            lines.append(f"⚠ {res.error}.")
        img = res.photo
        self._phone(f"\U0001f4f7 {res.name}", "\n".join(lines),
                    (lambda: V.image_bytes(img, PHOTO_MAX_SIDE)) if img is not None else b"")

    def _failure_when(self, res) -> str:
        total = self._play_runs
        runs = "\u221e" if total == 0 else f"{total:,}"
        where = f"Run {res.run:,} of {runs}, {fmt_time(res.t, 2)} into the macro"
        if self.file_path:
            where += f" ({os.path.basename(self.file_path)})"
        return where

    def _alert_failure(self, res):
        """Sound, tray balloon and phone alert, as soon as a checkpoint fails."""
        QApplication.beep()
        title = f"Macro stopped: {res.name}"
        msg = f"{res.summary}.\n{self._failure_when(res)}."
        if self.tray.isVisible():
            self.tray.showMessage(title, msg, QSystemTrayIcon.Warning, 15000)
        image = b""
        img = res.screenshot if res.screenshot is not None else res.actual
        if self.settings.notify_attach_screenshot and img is not None:
            image = lambda: V.image_bytes(img)  # noqa: E731  (encoded on the sending thread)
        # Discord: pings the people in Settings, so it stands out from the progress log
        if self._phone("\U0001f6a8 " + title, msg, image, priority="high", ping=True,
                       on_done=self.bridge.notified.emit):
            self._alert_text = "Sending phone alert\u2026"
        else:
            self._alert_text = "Phone alerts are off. Set them up in Settings \u2192 Checkpoints & alerts."

    def _on_notified(self, ok: bool, error: str):
        self._alert_text = "\u2713 Phone alert sent." if ok else f"\u26a0 Phone alert failed: {error}"
        if self._fail_dialog is not None:
            self._fail_dialog.set_alert_text(self._alert_text)
        if not ok:
            self.sb_screen.setText(self._alert_text)

    def _show_failure(self, res):
        if self._fail_dialog is not None:
            self._fail_dialog.close()
        dlg = CheckFailedDialog(res, self._failure_when(res), self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.set_alert_text(self._alert_text)
        dlg.showOnTimeline.connect(lambda: self._show_checkpoint(res.ref, res.t))
        dlg.editCheckpoint.connect(lambda: self._edit_checkpoint_ref(res.ref, res.t))
        dlg.destroyed.connect(lambda *_: setattr(self, "_fail_dialog", None))
        self._fail_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _update_alerts_label(self):
        cfg = notify.config_from_settings(self.settings)
        if cfg["service"] == "none":
            text, color = "Phone alerts: off", theme.TEXT_FAINT
        elif notify.problem(cfg):
            text, color = "Phone alerts: not set up", theme.YELLOW
        else:
            text, color = f"Phone alerts: {cfg['service']} \u2713", theme.TEXT_DIM
        self.lbl_alerts.setText(f"<a href='alerts' style='color:{color}; text-decoration:none'>{text}</a>")

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
                                on_step=rec.add_synthetic_move if rec else None,
                                mouse_mode=s.mouse_move_mode)

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
        self.meta = {}
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
        self.meta = dict(macro.meta or {})
        self.events = list(macro.events)
        upgraded = self._upgrade_legacy_meta()
        self._set_events(macro.events)
        self.timeline.set_selection(None)
        self.timeline.set_playhead(0.0, emit=True)
        self.timeline.zoom_fit()
        self.lbl_status.setText(f"Opened {os.path.basename(path)}"
                                + ("  ·  screen layout found, saved with the macro on the next save" if upgraded else ""))

    def save(self) -> bool:
        if not self.file_path:
            return self.save_as()
        self._upgrade_legacy_meta()
        try:
            Macro(self.events, meta={**self.meta, "app_version": __version__}).save(self.file_path)
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
        if self.state == PLAYING and not self._play_preview and self.settings.notify_on_finish:
            self._phone("⏹️ Macro stopped",
                        f"{self._macro_name()} was stopped during "
                        f"{self._run_text(self._last_run, self._play_runs).lower()}: the app was closed.")
        self._closing = True
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
        self.outbox.flush(3.0)  # let the last messages (e.g. "stopped") get out
