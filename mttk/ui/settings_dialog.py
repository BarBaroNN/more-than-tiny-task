"""Settings dialog: window behaviour, recording, playback, checkpoints & alerts, centring, hotkeys."""
from __future__ import annotations

import copy
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
                               QSizePolicy, QSpinBox, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from .. import keys as K
from .. import notify
from ..backend import EASINGS
from ..settings import DEFAULT_HOTKEYS, HOTKEY_LABELS, Settings
from . import theme


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


class _Relay(QObject):
    """Brings results of background network calls back to the GUI thread."""
    done = Signal(str, bool, str)   # what, ok, text


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Faint")
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.RichText)
    lbl.setOpenExternalLinks(True)
    return lbl


def _form():
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(18, 18, 18, 18)
    f.setHorizontalSpacing(18)
    f.setVerticalSpacing(12)
    f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return w, f


class SettingsDialog(QDialog):
    TABS = ("general", "recording", "playback", "alerts", "centring", "hotkeys")

    def __init__(self, settings: Settings, parent=None, tab: str = ""):
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

        tabs.addTab(self._alerts_tab(), "Checkpoints && alerts")

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
        for action in ("record", "play", "stop", "center", "checkpoint"):
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
                      "Record, play, centre and checkpoint keys are never recorded and don't reach other apps.\n"
                      "The stop-playback key only does something while a macro is playing.")
        note.setObjectName("Faint")
        f.addRow("", note)
        tabs.addTab(w, "Hotkeys")

        if tab in self.TABS:
            tabs.setCurrentIndex(self.TABS.index(tab))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setObjectName("Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    # ------------------------------------------------- checkpoints & alerts
    def _alerts_tab(self):
        s = self.s
        w, f = _form()
        self._relay = _Relay(self)
        self._relay.done.connect(self._network_done)

        title = QLabel("SCREEN CHECKPOINTS")
        title.setObjectName("CardTitle")
        f.addRow(title)
        self.cp_threshold = QSpinBox()
        self.cp_threshold.setRange(10, 100)
        self.cp_threshold.setSuffix(" %")
        self.cp_threshold.setValue(s.checkpoint_threshold)
        f.addRow("New checkpoints need", _row(self.cp_threshold, QLabel("of their area to look the same"), None))
        self.cp_timeout = QDoubleSpinBox()
        self.cp_timeout.setRange(0, 600)
        self.cp_timeout.setDecimals(1)
        self.cp_timeout.setSuffix(" s")
        self.cp_timeout.setValue(s.checkpoint_timeout)
        f.addRow("and wait up to", _row(self.cp_timeout, QLabel("for the screen to catch up"), None))
        f.addRow("", _hint("Press the checkpoint key (F7) while recording, or when playback is stopped at the "
                           "right spot, to save a picture of the screen at that moment. During playback the macro "
                           "pauses there until the screen looks the same. If it doesn't, the macro stops, "
                           "releases all keys and sends an alert. Double-click a checkpoint on the timeline to "
                           "choose its area and change its settings."))

        title = QLabel("PHONE ALERTS")
        title.setObjectName("CardTitle")
        f.addRow(title)
        self.service = QComboBox()
        for key, label in notify.SERVICES.items():
            self.service.addItem(label, key)
        i = self.service.findData(s.notify_service)
        self.service.setCurrentIndex(i if i >= 0 else 0)
        f.addRow("Send alerts with", self.service)

        self.svc_stack = QStackedWidget()
        self.svc_stack.addWidget(QWidget())  # off

        # ntfy
        page, pf = self._subform()
        self.ntfy_server = QLineEdit(s.ntfy_server or notify.NTFY_DEFAULT_SERVER)
        pf.addRow("Server", self.ntfy_server)
        self.ntfy_topic = QLineEdit(s.ntfy_topic)
        self.ntfy_topic.setPlaceholderText("e.g. mttk-3f9a1c0b7d2e")
        gen = QPushButton("New random topic")
        gen.clicked.connect(lambda: self.ntfy_topic.setText(notify.random_topic()))
        pf.addRow("Topic", _row(self.ntfy_topic, gen))
        self.ntfy_token = QLineEdit(s.ntfy_token)
        self.ntfy_token.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.ntfy_token.setPlaceholderText("optional, for protected topics")
        pf.addRow("Access token", self.ntfy_token)
        pf.addRow("", _hint("Install the free <b>ntfy</b> app (Android / iPhone), tap <b>+</b> and subscribe to "
                            "the same topic. Anyone who knows a topic name can read it, so keep it hard to guess. "
                            f"<a href='https://ntfy.sh' style='color:{theme.ACCENT}'>ntfy.sh</a>"))
        self.svc_stack.addWidget(page)

        # telegram
        page, pf = self._subform()
        self.tg_token = QLineEdit(s.telegram_token)
        self.tg_token.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.tg_token.setPlaceholderText("123456789:AA...")
        pf.addRow("Bot token", self.tg_token)
        self.tg_chat = QLineEdit(s.telegram_chat_id)
        find = QPushButton("Find my chat ID")
        find.clicked.connect(self._find_chat_id)
        pf.addRow("Chat ID", _row(self.tg_chat, find))
        pf.addRow("", _hint("In Telegram, message <b>@BotFather</b>, send <b>/newbot</b> and paste the token it "
                            "gives you. Then open your new bot, send it any message and press "
                            "<b>Find my chat ID</b>."))
        self.svc_stack.addWidget(page)

        # discord
        page, pf = self._subform()
        self.discord_url = QLineEdit(s.discord_webhook)
        self.discord_url.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.discord_url.setPlaceholderText("https://discord.com/api/webhooks/...")
        pf.addRow("Webhook URL", self.discord_url)
        pf.addRow("", _hint("In Discord: <b>Server settings, Integrations, Webhooks, New webhook</b>, pick a "
                            "channel and press <b>Copy webhook URL</b>. Turn on notifications for that channel "
                            "in the phone app."))
        self.svc_stack.addWidget(page)
        f.addRow("", self.svc_stack)
        self.service.currentIndexChanged.connect(self._service_changed)

        self.attach = QCheckBox("Attach a screenshot of the screen when it went wrong")
        self.attach.setChecked(s.notify_attach_screenshot)
        f.addRow("Alert", self.attach)
        self.on_finish = QCheckBox("Also send a message when a macro finishes normally")
        self.on_finish.setChecked(s.notify_on_finish)
        f.addRow("", self.on_finish)
        self.test_btn = QPushButton("Send a test alert")
        self.test_btn.clicked.connect(self._send_test)
        self.net_status = QLabel("")
        self.net_status.setWordWrap(True)
        f.addRow("", _row(self.test_btn, self.net_status, None))
        self._service_changed()
        return w

    @staticmethod
    def _subform():
        page = QWidget()
        pf = QFormLayout(page)
        pf.setContentsMargins(0, 0, 0, 0)
        pf.setHorizontalSpacing(12)
        pf.setVerticalSpacing(8)
        return page, pf

    def _service_changed(self, *_):
        idx = self.service.currentIndex()
        self.svc_stack.setCurrentIndex(idx)
        for i in range(self.svc_stack.count()):  # size the stack to the shown page only
            self.svc_stack.widget(i).setSizePolicy(
                QSizePolicy.Preferred, QSizePolicy.Preferred if i == idx else QSizePolicy.Ignored)
        self.svc_stack.adjustSize()
        on = self.service.currentData() != "none"
        for wdg in (self.attach, self.on_finish, self.test_btn):
            wdg.setEnabled(on)
        self.net_status.setText("")

    def _notify_config(self) -> dict:
        return {
            "service": self.service.currentData(),
            "ntfy_server": self.ntfy_server.text().strip() or notify.NTFY_DEFAULT_SERVER,
            "ntfy_topic": self.ntfy_topic.text().strip(),
            "ntfy_token": self.ntfy_token.text().strip(),
            "telegram_token": self.tg_token.text().strip(),
            "telegram_chat_id": self.tg_chat.text().strip(),
            "discord_webhook": self.discord_url.text().strip(),
        }

    def _send_test(self):
        cfg = self._notify_config()
        err = notify.problem(cfg)
        if err:
            self.net_status.setText("\u26a0 " + err)
            return
        self.net_status.setText("Sending...")
        self.test_btn.setEnabled(False)
        notify.send_async(cfg, "More Than Tiny Task", "Test alert: phone alerts work.", priority="default",
                          tags="white_check_mark",
                          on_done=lambda ok, text: self._relay.done.emit("test", ok, text))

    def _find_chat_id(self):
        token = self.tg_token.text().strip()
        self.net_status.setText("Asking Telegram...")

        def run():
            try:
                chat, name = notify.telegram_chat_id(token)
                self._relay.done.emit("chat", True, chat + "|" + name)
            except Exception as exc:
                self._relay.done.emit("chat", False, str(exc))
        threading.Thread(target=run, name="mttk-telegram", daemon=True).start()

    def _network_done(self, what: str, ok: bool, text: str):
        self.test_btn.setEnabled(self.service.currentData() != "none")
        if what == "chat" and ok:
            chat, _, name = text.partition("|")
            self.tg_chat.setText(chat)
            self.net_status.setText("\u2713 Found the chat with " + (name or "you"))
        elif ok:
            self.net_status.setText("\u2713 Sent. Check your phone.")
        else:
            self.net_status.setText("\u26a0 " + text)

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
        s.checkpoint_threshold = self.cp_threshold.value()
        s.checkpoint_timeout = self.cp_timeout.value()
        cfg = self._notify_config()
        s.notify_service = cfg["service"]
        s.ntfy_server = cfg["ntfy_server"]
        s.ntfy_topic = cfg["ntfy_topic"]
        s.ntfy_token = cfg["ntfy_token"]
        s.telegram_token = cfg["telegram_token"]
        s.telegram_chat_id = cfg["telegram_chat_id"]
        s.discord_webhook = cfg["discord_webhook"]
        s.notify_attach_screenshot = self.attach.isChecked()
        s.notify_on_finish = self.on_finish.isChecked()
        return s
