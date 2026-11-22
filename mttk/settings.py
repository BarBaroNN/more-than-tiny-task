"""Persistent user settings (stored as JSON through QSettings)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

DEFAULT_HOTKEYS = {
    "record": "f8",
    "play": "f9",
    "center": "f10",
    "stop": "esc",
    "checkpoint": "f7",
}

HOTKEY_LABELS = {
    "record": "Record / stop",
    "play": "Play / stop",
    "center": "Centre mouse",
    "stop": "Stop playback",
    "checkpoint": "Add checkpoint",
}


@dataclass
class Settings:
    # window
    always_on_top: bool = False
    opacity: int = 100
    minimize_to_tray: bool = True
    show_tray_icon: bool = True
    hide_while_recording: bool = False
    hide_while_playing: bool = False
    show_osd: bool = True
    compact_mode: bool = False
    confirm_unsaved: bool = True
    # recording
    record_mouse_moves: bool = True
    record_mouse: bool = True
    record_keyboard: bool = True
    move_interval_ms: int = 8
    countdown_s: int = 0
    move_cursor_before_rerecord: bool = True
    # playback
    repeats: int = 1
    infinite: bool = False
    delay_value: float = 0.0
    delay_unit: str = "seconds"
    speed: float = 1.0
    release_keys_on_stop: bool = True
    relative_start: bool = False      # start from the current cursor position
    adapt_resolution: bool = True     # rescale mouse positions to this screen
    mouse_move_mode: str = "relative"  # 'relative' (like a mouse), 'absolute' (TinyTask) or 'set'
    # screen checkpoints
    checkpoints_enabled: bool = True   # check the screen at checkpoints during playback
    checkpoint_threshold: int = 80     # default % match for new checkpoints
    checkpoint_timeout: float = 5.0    # default seconds to wait for a match
    # phone alerts
    notify_service: str = "none"       # 'none', 'ntfy', 'telegram' or 'discord'
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook: str = ""
    notify_attach_screenshot: bool = True
    notify_on_finish: bool = False
    # mouse centring
    center_target: str = "cursor"  # 'cursor' or 'primary'
    center_duration_ms: int = 350
    center_easing: str = "Smooth (ease in-out)"
    # hotkeys
    hotkeys: dict = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    # misc
    last_dir: str = ""

    @property
    def delay_seconds(self) -> float:
        mult = {"seconds": 1, "minutes": 60, "hours": 3600}.get(self.delay_unit, 1)
        return max(0.0, self.delay_value) * mult

    # -- persistence
    @staticmethod
    def _qsettings():
        from PySide6.QtCore import QSettings
        return QSettings("MoreThanTinyTask", "MoreThanTinyTask")

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            raw = cls._qsettings().value("settings_json", "")
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {}
        names = {f.name for f in fields(cls)}
        for k, v in data.items():
            if k in names:
                setattr(s, k, v)
        hk = dict(DEFAULT_HOTKEYS)
        hk.update(s.hotkeys or {})
        s.hotkeys = hk
        return s

    def save(self) -> None:
        q = self._qsettings()
        q.setValue("settings_json", json.dumps(asdict(self)))
        q.sync()
