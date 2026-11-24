"""Render the README pictures: docs/preview-*.png and docs/preview-playback.gif.

The app is drawn offscreen at 2x with a made-up demo macro and a made-up
1920x1080 screen. Nothing is recorded, played or sent, and your settings and
monitors are left alone. The GIF needs ffmpeg on the PATH.

    python tools/make_previews.py            # everything
    python tools/make_previews.py editor gif # only some (names below)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"
if sys.platform == "win32":  # the offscreen platform only finds fonts it's pointed at
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mttk import notify  # noqa: E402
from mttk import settings as S  # noqa: E402
from mttk import winutil  # noqa: E402

notify.send = lambda *a, **kw: None   # never send anything (the pictures show Discord as set up)
TMP = tempfile.mkdtemp(prefix="mttk-previews-")
S.Settings._qsettings = staticmethod(lambda: QSettings(os.path.join(TMP, "settings.ini"), QSettings.IniFormat))
SCREEN = [0, 0, 1920, 1080]           # the screen the pictures pretend to play on
RECORDED = [0, 0, 2560, 1440]         # the demo macro was "recorded" on this one
winutil.monitors = lambda: [list(SCREEN)]
winutil.primary_screen_rect = lambda: list(SCREEN)
winutil.monitor_rect_at = lambda x, y: list(SCREEN)
winutil.cursor_pos = lambda: (960, 540)
winutil.set_exclude_from_capture = lambda hwnd, on: False

APP = QApplication(sys.argv)

from mttk import APP_NAME  # noqa: E402
from mttk import model as M  # noqa: E402
from mttk import vision as V  # noqa: E402
from mttk.model import CHECK, CLICK, KEY, MOVE, SCROLL, Event  # noqa: E402
from mttk.ui import icons  # noqa: E402
from mttk.ui.theme import build_stylesheet  # noqa: E402

# styled exactly like mttk/app.py does it
APP.setApplicationName(APP_NAME)
APP.setOrganizationName("MoreThanTinyTask")
APP.setStyle("Fusion")
APP.setStyleSheet(build_stylesheet(icons.write_style_assets(os.path.join(TMP, "style"))))
WIN_SIZE = (1520, 820)
GIF_WIDTH = 1000


# ------------------------------------------------------------- demo macro

def game_screen(variant: int = 0) -> QImage:
    """A made-up game screen at the recorded resolution, for the checkpoint pictures."""
    w, h = RECORDED[2], RECORDED[3]
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    sky = QLinearGradient(0, 0, 0, h * 0.6)
    sky.setColorAt(0, QColor("#24345a"))
    sky.setColorAt(1, QColor("#5c7fb0"))
    p.fillRect(0, 0, w, int(h * 0.6), sky)
    p.fillRect(0, int(h * 0.6), w, h, QColor("#3b5a3a"))
    for i, (x, bw, bh) in enumerate([(120, 380, 520), (620, 300, 380), (1700, 460, 600), (2240, 260, 300)]):
        p.fillRect(x, int(h * 0.6) - bh, bw, bh, QColor("#2e3d55" if i % 2 else "#34465f"))
        for wy in range(int(h * 0.6) - bh + 40, int(h * 0.6) - 40, 90):
            for wx in range(x + 30, x + bw - 40, 80):
                p.fillRect(wx, wy, 36, 48, QColor("#f2c96b") if (wx + wy) % 3 else QColor("#1e2a3b"))
    # shop window
    shop = QRectF(880, 300, 800, 620)
    p.setPen(QPen(QColor("#c9a25b"), 6))
    p.setBrush(QColor(22, 26, 36, 235))
    p.drawRoundedRect(shop, 24, 24)
    p.setPen(QColor("#f5e6c4"))
    f = QFont("Segoe UI", 44, QFont.Bold)
    p.setFont(f)
    p.drawText(shop.adjusted(0, 30, 0, 0), Qt.AlignHCenter | Qt.AlignTop, "SHOP")
    colors = ["#e06c75", "#61afef", "#98c379", "#e5c07b", "#c678dd", "#56b6c2"]
    for i, c in enumerate(colors):
        cx, cy = 960 + (i % 3) * 230, 470 + (i // 3) * 220
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2b3142"))
        p.drawRoundedRect(QRectF(cx, cy, 180, 180), 18, 18)
        p.setBrush(QColor(c))
        p.drawEllipse(QPointF(cx + 90, cy + 80), 46, 46)
        p.setPen(QColor("#e8d9b0"))
        p.setFont(QFont("Segoe UI", 20))
        p.drawText(QRectF(cx, cy + 130, 180, 40), Qt.AlignCenter, f"{(i + 1) * 150} g")
    # HUD: health / gold, top left; stats panel, top right
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawRoundedRect(QRectF(40, 40, 620, 150), 18, 18)
    p.setBrush(QColor("#e06c75"))
    p.drawRoundedRect(QRectF(70, 70, 460 - variant * 120, 34), 10, 10)
    p.setBrush(QColor("#61afef"))
    p.drawRoundedRect(QRectF(70, 124, 360, 34), 10, 10)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawRoundedRect(QRectF(2040, 40, 480, 260), 18, 18)
    p.setPen(QColor("#f5e6c4"))
    p.setFont(QFont("Segoe UI", 26, QFont.Bold))
    for i, line in enumerate(["Gold  12,480", "Wave  37", "Kills  1,204", "Time  1:34"]):
        p.drawText(QRectF(2080, 60 + i * 56, 400, 56), Qt.AlignLeft | Qt.AlignVCenter, line)
    p.end()
    return img


def demo_macro():
    """About 7 seconds of clicks, typing, scrolling and a save, plus a checkpoint
    and a screenshot point. Positions are on the 2560x1440 recording screen."""
    ev = []
    path = [(0.0, (1500, 640)), (0.6, (1240, 900)), (2.1, (1240, 900)), (2.6, (1700, 420)),
            (3.6, (1700, 420)), (3.9, (1500, 760)), (5.0, (1520, 700)), (5.4, (1900, 640)),
            (6.4, (1880, 660)), (6.84, (2100, 500))]
    t = 0.0
    while t <= 6.84:
        for (t0, a), (t1, b) in zip(path, path[1:]):
            if t0 <= t <= t1:
                k = 0 if t1 == t0 else (t - t0) / (t1 - t0)
                k = k * k * (3 - 2 * k)
                ev.append(Event(round(t, 3), MOVE, x=round(a[0] + (b[0] - a[0]) * k),
                                y=round(a[1] + (b[1] - a[1]) * k)))
                break
        t += 0.025

    def click(t, x, y, button="left", hold=0.09):
        ev.append(Event(t, CLICK, x=x, y=y, button=button, pressed=True))
        ev.append(Event(t + hold, CLICK, x=x, y=y, button=button, pressed=False))

    def key(t, ch, hold=0.08, name=""):
        kw = {"key_name": name} if name else {"vk": ord(ch.upper()), "char": ch}
        ev.append(Event(t, KEY, pressed=True, **kw))
        ev.append(Event(t + hold, KEY, pressed=False, **kw))

    click(0.6, 1240, 900)
    for i, ch in enumerate("hello"):
        key(0.84 + i * 0.14, ch)
    key(1.56, "", name="enter")
    click(2.6, 1700, 420)
    for i in range(4):
        ev.append(Event(2.82 + i * 0.12, SCROLL, x=1700, y=420, dy=-1))
    click(3.8, 1500, 760, "right", 0.12)
    ev.append(Event(4.3, KEY, pressed=True, key_name="ctrl_l"))
    key(4.42, "s")
    ev.append(Event(4.62, KEY, pressed=False, key_name="ctrl_l"))
    click(5.2, 1900, 640)
    for i, ch in enumerate("done"):
        key(5.5 + i * 0.14, ch)
    click(6.75, 2100, 500)
    ev.append(Event(2.45, CHECK, ref="shop"))
    ev.append(Event(4.95, CHECK, ref="stats"))
    events = M.sort_events(ev)

    img = game_screen()
    shop = V.new_checkpoint(img, RECORDED, RECORDED, "Shop open")
    shop["region"] = [880, 300, 800, 620]
    stats = V.new_checkpoint(img, RECORDED, RECORDED, "Stats")
    stats.update(region=[2040, 40, 480, 260], compare=False, send=True)
    meta = {"monitors": [list(RECORDED)], "record_on": 0, "screen": list(RECORDED)}
    meta = M.with_checkpoints(meta, {"shop": shop, "stats": stats})
    return events, meta, img


def new_window(compact=False):
    from mttk.ui.main_window import MainWindow
    win = MainWindow()
    win.settings.notify_service = "discord"   # "Phone alerts: discord ✓" (sending is switched off above)
    win.settings.discord_webhook = "https://discord.com/api/webhooks/0/example"
    win._update_alerts_label()
    events, meta, _img = demo_macro()
    win.meta = meta
    win._set_events(events)
    win.file_path = os.path.join(TMP, "farm-run.mttk")
    win._update_title()
    win.undo.setClean()
    win.spin_repeat.setValue(10)
    win.chk_infinite.setChecked(False)
    win.spin_delay.setValue(1.0)
    win.cmb_unit.setCurrentText("minutes")
    win.resize(*WIN_SIZE)
    win.show()
    APP.processEvents()
    win.timeline.zoom_fit()
    APP.processEvents()
    return win


def close(win):
    win.undo.setClean()
    if win.hotkeys:
        win.hotkeys.stop()
    win.osd.close()
    win.tray.hide()
    win.close()
    APP.processEvents()


def save(widget, name):
    APP.processEvents()
    path = os.path.join(DOCS, name)
    widget.grab().toImage().save(path)
    print("wrote", os.path.relpath(path, ROOT))


# ------------------------------------------------------------- pictures

def editor():
    win = new_window()
    win.timeline.set_selection((2.35, 3.55))
    win.timeline.set_playhead(1.2, emit=True)
    save(win, "preview-editor.png")
    close(win)


def rerecord():
    win = new_window()
    win.timeline.set_selection((2.35, 3.55))
    win.timeline.set_playhead(1.2, emit=True)

    class FakeRecorder:
        running = True

        def elapsed(self):
            return 2.4

        def event_count(self):
            return 57

    win.recorder = FakeRecorder()
    win._rec_ctx = {"mode": "replace", "start": 2.35, "end": 3.55}
    win.state = "recording"
    win._rec_desc = "Re-recording 2.35s → 3.55s"
    win.timeline.set_overlay(f"● {win._rec_desc}…\nPress {win._hk('record')} to stop")
    win._update_state_ui()
    win._tick_recording()
    save(win, "preview-rerecord.png")
    win.state, win.recorder = "idle", None
    close(win)


def compact():
    win = new_window()
    win.btn_pin.setChecked(True)   # shown pinned (stay on top), without changing the window flags
    win._set_compact(True)
    APP.processEvents()
    win.resize(WIN_SIZE[0], win.minimumSizeHint().height())
    save(win, "preview-compact.png")
    close(win)


def hotkeys():
    from mttk.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(S.Settings(), tab="hotkeys")
    dlg.resize(760, 470)
    dlg.show()
    save(dlg, "preview-hotkeys.png")
    dlg.close()


def alerts():
    from mttk.ui.settings_dialog import SettingsDialog
    s = S.Settings()
    s.notify_service = "discord"
    s.discord_webhook = "https://discord.com/api/webhooks/0/example"
    s.notify_on_start = s.notify_each_run = s.notify_on_finish = True
    dlg = SettingsDialog(s, tab="alerts")
    dlg.resize(760, 760)
    dlg.show()
    APP.processEvents()
    from PySide6.QtWidgets import QScrollArea
    for area in dlg.findChildren(QScrollArea):   # the alerts tab scrolls: show its phone part
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    save(dlg, "preview-alerts.png")
    dlg.close()


def _dialog(spec, at, max_time, name, **kw):
    from mttk.ui.checkpoint_dialog import CheckpointDialog
    img = V.decode_image(spec["image"])

    def grab(rect):
        x, y, w, h = rect
        f = img.width() / RECORDED[2]
        return img.copy(round(x * f), round(y * f), round(w * f), round(h * f))

    cfg = {"service": "discord", "discord_webhook": "https://discord.com/api/webhooks/0/example"}
    dlg = CheckpointDialog(spec, at, grab, lambda r, scr=None: list(r), max_time=max_time, notify_cfg=cfg, **kw)
    dlg.resize(860, 760)
    dlg.show()
    save(dlg, name)
    dlg.close()


def checkpoint():
    _events, meta, _img = demo_macro()
    spec = dict(M.checkpoints_of(meta)["shop"])
    _dialog(spec, 2.45, 6.84, "preview-checkpoint.png")


def screenshot_point():
    _events, meta, _img = demo_macro()
    spec = dict(M.checkpoints_of(meta)["stats"], name="Stats at 1:34")
    _dialog(spec, 94.0, 600.0, "preview-screenshot-point.png", is_new=True)


def gif():
    """Run 3 of 10 playing through (pausing at the checkpoint), then the wait for run 4."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("skipped the GIF: ffmpeg isn't on the PATH")
        return
    win = new_window()
    frames = os.path.join(TMP, "frames")
    os.makedirs(frames, exist_ok=True)
    n = 0

    def frame():
        nonlocal n
        APP.processEvents()
        win.grab().toImage().save(os.path.join(frames, f"f{n:04d}.png"))
        n += 1

    events = win.events
    win.state = "playing"
    win._play_offset, win._resume_at = 0.0, 0.0
    win._play_duration = M.duration(events)
    win._play_runs, win._last_run, win._play_preview = 10, 3, True
    win._update_state_ui()
    step = 0.1
    t, shown_check = 0.0, False
    while t <= win._play_duration + 1e-9:
        win._on_progress(3, 10, t)
        if not shown_check and t >= 2.45:
            shown_check = True
            win._on_progress(3, 10, 2.45)
            for k, score in enumerate((0.41, 0.58, 0.77)):   # the shop is still opening: wait for it
                win._on_check_status("Shop open", score, 0.8, 4.75 - k * 0.25)
                frame()
                frame()
            win._osd_note = ("", 0.0)
        frame()
        t += step
    win._on_progress(3, 10, win._play_duration)
    frame()
    remaining = 58.0
    for _ in range(14):
        win._on_waiting(remaining, 4)
        frame()
        remaining -= 0.1
    win.state = "idle"
    close(win)
    out = os.path.join(DOCS, "preview-playback.gif")
    palette = os.path.join(TMP, "palette.png")
    scale = f"scale={GIF_WIDTH}:-2:flags=lanczos,setsar=1"
    src = ["-framerate", "10", "-i", os.path.join(frames, "f%04d.png")]
    subprocess.run([ffmpeg, "-v", "error", "-y", *src, "-vf", f"{scale},palettegen=stats_mode=diff", palette],
                   check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", *src, "-i", palette, "-lavfi",
                    f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
                    "-loop", "0", out], check=True)
    print("wrote", os.path.relpath(out, ROOT), f"({n} frames)")


ALL = {"editor": editor, "rerecord": rerecord, "compact": compact, "hotkeys": hotkeys, "alerts": alerts,
       "checkpoint": checkpoint, "screenshot": screenshot_point, "gif": gif}

if __name__ == "__main__":
    try:
        for name in sys.argv[1:] or ALL:
            ALL[name]()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
