"""Small Windows helpers (with harmless fallbacks on other platforms)."""
from __future__ import annotations

import contextlib
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


@contextlib.contextmanager
def high_resolution_timer():
    """Ask Windows for 1 ms timer resolution so playback timing is accurate."""
    if IS_WINDOWS:
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
    try:
        yield
    finally:
        if IS_WINDOWS:
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass


def cursor_pos():
    if IS_WINDOWS:
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    try:
        from pynput.mouse import Controller
        x, y = Controller().position
        return int(x), int(y)
    except Exception:
        return 0, 0


def screen_center(target: str = "cursor"):
    """Centre of the primary monitor (target='primary') or of the monitor the
    cursor is on (target='cursor'), in physical pixels."""
    if IS_WINDOWS:
        try:
            if target == "primary":
                pt = wintypes.POINT(0, 0)
            else:
                pt = wintypes.POINT(*cursor_pos())
            MONITOR_DEFAULTTONEAREST = 2
            mon = _user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            _user32.GetMonitorInfoW(mon, ctypes.byref(info))
            r = info.rcMonitor
            return (r.left + r.right) // 2, (r.top + r.bottom) // 2
        except Exception:
            return _user32.GetSystemMetrics(0) // 2, _user32.GetSystemMetrics(1) // 2
    # Fallback via Qt (logical pixels scaled to physical)
    try:
        from PySide6.QtGui import QCursor, QGuiApplication
        if target == "primary":
            screen = QGuiApplication.primaryScreen()
        else:
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = screen.geometry()
        r = screen.devicePixelRatio()
        return int((g.x() + g.width() / 2) * r), int((g.y() + g.height() / 2) * r)
    except Exception:
        return 0, 0


def window_rect_physical(widget):
    """Window rectangle (left, top, right, bottom) in physical pixels."""
    if IS_WINDOWS:
        try:
            rect = wintypes.RECT()
            _user32.GetWindowRect(int(widget.winId()), ctypes.byref(rect))
            return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    g = widget.frameGeometry()
    r = widget.devicePixelRatioF()
    return int(g.left() * r), int(g.top() * r), int((g.right() + 1) * r), int((g.bottom() + 1) * r)


def primary_screen_rect():
    """(left, top, width, height) of the primary monitor in physical pixels.

    Mouse coordinates are recorded in the same physical-pixel space, so a
    macro's coordinates can be rescaled between resolutions with this rect.
    """
    if IS_WINDOWS:
        try:
            return [0, 0, int(_user32.GetSystemMetrics(0)), int(_user32.GetSystemMetrics(1))]
        except Exception:
            pass
    try:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        g = screen.geometry()
        r = screen.devicePixelRatio()
        return [int(g.x() * r), int(g.y() * r), int(round(g.width() * r)), int(round(g.height() * r))]
    except Exception:
        return None


# ------------------------------------------------------------- key injection
# The same calls TinyTask uses to play keys back: keybd_event with the key's
# virtual-key code *and* its hardware scan code (plus the extended flag for
# keys on the E0 prefix), so programs that read scan codes (games, DirectInput,
# raw input, remote desktops) see the key as well as normal Windows apps.

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

if IS_WINDOWS:
    _user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t]
    _user32.keybd_event.restype = None
    _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    _user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
    _user32.VkKeyScanW.restype = ctypes.c_short


def keybd_event(vk: int, scan: int, flags: int) -> None:
    if IS_WINDOWS:
        _user32.keybd_event(vk & 0xFF, scan & 0xFF, flags, 0)


def vk_to_scan(vk: int) -> int:
    """MapVirtualKey(vk, MAPVK_VK_TO_VSC): the scan code of a virtual key."""
    return int(_user32.MapVirtualKeyW(vk, 0)) if IS_WINDOWS else 0


def char_to_vk(char: str) -> int:
    """VkKeyScan: the virtual key that types ``char`` on this layout (0 if none)."""
    if not IS_WINDOWS or not char or len(char) != 1:
        return 0
    res = int(_user32.VkKeyScanW(char))
    return 0 if res == -1 or (res & 0xFF) == 0xFF else res & 0xFF
