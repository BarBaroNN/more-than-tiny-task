"""Settings dialog: window behaviour, recording, playback, centring, hotkeys."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import keys as K
from ..backend import EASINGS
from ..settings import DEFAULT_HOTKEYS, HOTKEY_LABELS, Settings


class HotkeyEdit(QLineEdit):
    """Click, then press the key combination you want."""
    changed = Signal(str)

    def __init__(self, value: str = ""):
        super().__init__()
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(170)
        self.value = value
        self._refresh()

    def _refresh(self):
        self.setText(K.format_hotkey(self.value) if self.value else "None")

    def set_value(self, v: str):
        self.value = v
        self._refresh()
        self.changed.emit(v)

    def focusInEvent(self, e):
        self.setText("Press a key…")
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._refresh()
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        name = K.qt_key_to_name(e.key(), e.text())
        if name is None:  # a lone modifier - wait for the real key
            return
        mods = []
        m = e.modifiers()
        if m & Qt.ControlModifier:
            mods.append("ctrl")
        if m & Qt.ShiftModifier:
            mods.append("shift")
        if m & Qt.AltModifier:
            mods.append("alt")
        if m & Qt.MetaModifier:
            mods.append("win")
        self.set_value("+".join(mods + [name]))
        self.clearFocus()


def _row(*widgets):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        if isinstance(x, int):
            lay.addSpacing(x)
        elif x is None:
            lay.addStretch(1)
        else:
            lay.addWidget(x)
    return w


def _form():
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(18, 18, 18, 18)
    f.setHorizontalSpacing(18)
    f.setVerticalSpacing(12)
    f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return w, f


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(700)
        self.s = copy.deepcopy(settings)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- General
        w, f = _form()
        self.on_top = QCheckBox("Keep the window above other windows")
        self.on_top.setChecked(self.s.always_on_top)
        f.addRow("Stay on top", self.on_top)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(self.s.opacity)
        self.opacity_lbl = QLabel(f"{self.s.opacity}%")
        self.opacity_lbl.setMinimumWidth(40)
        self.opacity.valueChanged.connect(lambda v: self.opacity_lbl.setText(f"{v}%"))
        f.addRow("Window opacity", _row(self.opacity, self.opacity_lbl))
        self.tray = QCheckBox("Show icon in the system tray")
        self.tray.setChecked(self.s.show_tray_icon)
        f.addRow("Tray", self.tray)
        self.min_tray = QCheckBox("Minimize to tray instead of the taskbar")
        self.min_tray.setChecked(self.s.minimize_to_tray)
        f.addRow("", self.min_tray)
        self.hide_rec = QCheckBox("Hide window while recording")
        self.hide_rec.setChecked(self.s.hide_while_recording)
        f.addRow("Auto-hide", self.hide_rec)
        self.hide_play = QCheckBox("Hide window while playing")
        self.hide_play.setChecked(self.s.hide_while_playing)
        f.addRow("", self.hide_play)
        self.osd = QCheckBox("Show a small on-screen REC / PLAY indicator")
        self.osd.setChecked(self.s.show_osd)
        f.addRow("Indicator", self.osd)
        self.confirm = QCheckBox("Ask before discarding unsaved changes")
        self.confirm.setChecked(self.s.confirm_unsaved)
        f.addRow("Safety", self.confirm)
        tabs.addTab(w, "General")

        # --- Recording
        w, f = _form()
        self.rec_mouse = QCheckBox("Mouse clicks and scrolling")
        self.rec_mouse.setChecked(self.s.record_mouse)
        f.addRow("Record", self.rec_mouse)
        self.rec_moves = QCheckBox("Mouse movement")
        self.rec_moves.setChecked(self.s.record_mouse_moves)
        f.addRow("", self.rec_moves)
        self.rec_kb = QCheckBox("Keyboard")
        self.rec_kb.setChecked(self.s.record_keyboard)
        f.addRow("", self.rec_kb)
        self.move_ms = QSpinBox()
        self.move_ms.setRange(0, 200)
        self.move_ms.setSuffix(" ms")
        self.move_ms.setValue(self.s.move_interval_ms)
        self.move_ms.setToolTip("Minimum time between recorded mouse moves. 0 = record every move.")
        f.addRow("Mouse move sampling", self.move_ms)
        self.countdown = QSpinBox()
        self.countdown.setRange(0, 30)
        self.countdown.setSuffix(" s")
        self.countdown.setSpecialValueText("Off")
        self.countdown.setValue(self.s.countdown_s)
        f.addRow("Countdown before recording", self.countdown)
        self.move_before = QCheckBox("Move the cursor to where the recording was before re-recording a part")
        self.move_before.setChecked(self.s.move_cursor_before_rerecord)
        self.move_before.setToolTip("Keeps the new take seamlessly connected to the rest of the macro.")
        f.addRow("Re-record", self.move_before)
        tabs.addTab(w, "Recording")

        # --- Playback
        w, f = _form()
        self.release = QCheckBox("Release any held keys / mouse buttons when playback stops")
        self.release.setChecked(self.s.release_keys_on_stop)
        f.addRow("Safety", self.release)
        self.mouse_mode = QComboBox()
        self.mouse_mode.addItem("Like a real mouse (movement)", "relative")
        self.mouse_mode.addItem("Like TinyTask (screen position)", "absolute")
        self.mouse_mode.addItem("Only place the pointer (old)", "set")
        i = self.mouse_mode.findData(self.s.mouse_move_mode)
        self.mouse_mode.setCurrentIndex(i if i >= 0 else 0)
        self.mouse_mode.setToolTip(
            "How mouse movement is sent during playback. Every mode ends with the pointer on the recorded pixel.\n"
            "• Like a real mouse: sends movement, which games that read the mouse directly (selection wheels,\n"
            "  camera, drag) respond to.\n"
            "• Like TinyTask: sends an absolute screen position, exactly as TinyTask does.\n"
            "• Only place the pointer: no mouse input at all; some programs ignore it.")
        f.addRow("Mouse movement", self.mouse_mode)
        f.addRow(QLabel("Repeat count, delay between runs and speed are set on the main window."))
        tabs.addTab(w, "Playback")

        # --- Mouse centring
        w, f = _form()
        self.center_target = QComboBox()
        self.center_target.addItem("Monitor the cursor is on", "cursor")
        self.center_target.addItem("Primary monitor", "primary")
        self.center_target.setCurrentIndex(0 if self.s.center_target == "cursor" else 1)
        f.addRow("Centre of", self.center_target)
        self.center_ms = QSpinBox()
        self.center_ms.setRange(0, 3000)
        self.center_ms.setSingleStep(50)
        self.center_ms.setSuffix(" ms")
        self.center_ms.setValue(self.s.center_duration_ms)
        f.addRow("Animation duration", self.center_ms)
        self.easing = QComboBox()
        self.easing.addItems(list(EASINGS))
        self.easing.setCurrentText(self.s.center_easing)
        f.addRow("Animation style", self.easing)
        f.addRow(QLabel("While recording, the glide is recorded as normal mouse movement."))
        tabs.addTab(w, "Mouse centring")

        # --- Hotkeys
        w, f = _form()
        self.hotkey_edits = {}
        for action in ("record", "play", "stop", "center"):
            edit = HotkeyEdit(self.s.hotkeys.get(action, ""))
            clear = QPushButton("Clear")
            clear.clicked.connect(lambda _=False, e=edit: e.set_value(""))
            reset = QPushButton("Default")
            reset.clicked.connect(lambda _=False, e=edit, a=action: e.set_value(DEFAULT_HOTKEYS[a]))
            self.hotkey_edits[action] = edit
            f.addRow(HOTKEY_LABELS[action], _row(edit, clear, reset))
        self.hk_warning = QLabel("")
        self.hk_warning.setObjectName("Faint")
        f.addRow("", self.hk_warning)
        for e in self.hotkey_edits.values():
            e.changed.connect(self._check_conflicts)
        note = QLabel("Click a box, then press the key or combination (e.g. Ctrl+Shift+R).\n"
                      "Hotkeys work globally, even when another window is focused.\n"
                      "Record, play and centre keys are never recorded and don't reach other apps.\n"
                      "The stop-playback key only does something while a macro is playing.")
        note.setObjectName("Faint")
        f.addRow("", note)
        tabs.addTab(w, "Hotkeys")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setObjectName("Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _check_conflicts(self, *_):
        seen, dup = {}, []
        for a, e in self.hotkey_edits.items():
            if e.value:
                key = K.parse_hotkey(e.value)
                if key in seen:
                    dup.append(K.format_hotkey(e.value))
                seen[key] = a
        self.hk_warning.setText(f"⚠ Duplicate hotkey: {', '.join(dup)}" if dup else "")

    def result_settings(self) -> Settings:
        s = self.s
        s.always_on_top = self.on_top.isChecked()
        s.opacity = self.opacity.value()
        s.show_tray_icon = self.tray.isChecked()
        s.minimize_to_tray = self.min_tray.isChecked()
        s.hide_while_recording = self.hide_rec.isChecked()
        s.hide_while_playing = self.hide_play.isChecked()
        s.show_osd = self.osd.isChecked()
        s.confirm_unsaved = self.confirm.isChecked()
        s.record_mouse = self.rec_mouse.isChecked()
        s.record_mouse_moves = self.rec_moves.isChecked()
        s.record_keyboard = self.rec_kb.isChecked()
        s.move_interval_ms = self.move_ms.value()
        s.countdown_s = self.countdown.value()
        s.move_cursor_before_rerecord = self.move_before.isChecked()
        s.release_keys_on_stop = self.release.isChecked()
        s.mouse_move_mode = self.mouse_mode.currentData()
        s.center_target = self.center_target.currentData()
        s.center_duration_ms = self.center_ms.value()
        s.center_easing = self.easing.currentText()
        s.hotkeys = {a: e.value for a, e in self.hotkey_edits.items()}
        return s
