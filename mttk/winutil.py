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


def monitors():
    """Every monitor as [left, top, width, height] in physical pixels, the main
    (primary) one first, then left to right."""
    found = []
    if IS_WINDOWS:
        try:
            MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                                 ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

            def cb(hmon, _hdc, _rect, _data):
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(_MONITORINFO)
                if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                    r = info.rcMonitor
                    found.append((not (info.dwFlags & 1), r.left, r.top,
                                  [r.left, r.top, r.right - r.left, r.bottom - r.top]))
                return True

            _user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
        except Exception:
            found = []
    if not found:
        try:
            from PySide6.QtGui import QGuiApplication
            primary = QGuiApplication.primaryScreen()
            for scr in QGuiApplication.screens():
                g, r = scr.geometry(), scr.devicePixelRatio()
                rect = [int(g.x() * r), int(g.y() * r), int(round(g.width() * r)), int(round(g.height() * r))]
                found.append((scr is not primary, rect[0], rect[1], rect))
        except Exception:
            pass
    found.sort(key=lambda m: m[:3])
    return [m[3] for m in found] or [primary_screen_rect() or [0, 0, 1920, 1080]]


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


# ------------------------------------------------------------ mouse movement
# SetCursorPos alone only *places* the pointer: no mouse input event is
# generated, so programs that read the mouse as input (raw input / DirectInput,
# e.g. games with selection wheels, camera or drag controls) see no movement.
# Like TinyTask, a real mouse_event move is sent first and SetCursorPos then
# puts the pointer on the exact pixel.

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

MOUSE_RELATIVE = "relative"   # like a real mouse: movement deltas
MOUSE_ABSOLUTE = "absolute"   # like TinyTask: absolute screen position
MOUSE_SET_ONLY = "set"        # only SetCursorPos (no input event)

if IS_WINDOWS:
    _user32.mouse_event.argtypes = [wintypes.DWORD, ctypes.c_long, ctypes.c_long, wintypes.DWORD, ctypes.c_size_t]
    _user32.mouse_event.restype = None
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int


def virtual_screen_rect():
    """(left, top, width, height) of the whole desktop across all monitors."""
    if not IS_WINDOWS:
        return 0, 0, 0, 0
    gsm = _user32.GetSystemMetrics
    return gsm(76), gsm(77), gsm(78), gsm(79)  # SM_X/Y/CX/CYVIRTUALSCREEN


def normalize_absolute(x: int, y: int, vrect) -> tuple:
    """Pixel position -> 0..65535 coordinates over the virtual desktop
    (what MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK expects)."""
    left, top, w, h = vrect
    nx = round((x - left) * 65535 / max(1, w - 1))
    ny = round((y - top) * 65535 / max(1, h - 1))
    return min(65535, max(0, nx)), min(65535, max(0, ny))


def move_mouse(x: int, y: int, mode: str = MOUSE_RELATIVE) -> bool:
    """Move the pointer to (x, y) with a real mouse input event, then snap it
    to the exact pixel. False if not on Windows (caller falls back)."""
    if not IS_WINDOWS:
        return False
    x, y = int(x), int(y)
    if mode == MOUSE_RELATIVE:
        cx, cy = cursor_pos()
        if x != cx or y != cy:
            _user32.mouse_event(MOUSEEVENTF_MOVE, x - cx, y - cy, 0, 0)
    elif mode == MOUSE_ABSOLUTE:
        nx, ny = normalize_absolute(x, y, virtual_screen_rect())
        _user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny, 0, 0)
    _user32.SetCursorPos(x, y)
    return True


# ------------------------------------------------------------ screen capture
# Plain GDI, so it works from any thread (the player checks the screen from
# its own thread, where Qt's QScreen.grabWindow can't be used).

def monitor_rect_at(x: int, y: int):
    """(left, top, width, height) of the monitor containing (x, y)."""
    if IS_WINDOWS:
        try:
            mon = _user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if _user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                r = info.rcMonitor
                return [r.left, r.top, r.right - r.left, r.bottom - r.top]
        except Exception:
            pass
    return primary_screen_rect()


if IS_WINDOWS:
    _gdi32 = ctypes.windll.gdi32

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = wintypes.HDC
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    _gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    _gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.SetWindowDisplayAffinity.restype = wintypes.BOOL


def grab_screen(left: int, top: int, width: int, height: int):
    """Screenshot of a rectangle of the desktop (physical pixels, any monitor)
    as ``(bgra_bytes, width, height)``, or None. Parts outside every monitor
    come back black. The mouse pointer is not included."""
    if not IS_WINDOWS or width <= 0 or height <= 0:
        return None
    screen_dc = _user32.GetDC(None)
    if not screen_dc:
        return None
    mem_dc = bmp = old = None
    try:
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
        bmp = _gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not mem_dc or not bmp:
            return None
        old = _gdi32.SelectObject(mem_dc, bmp)
        if not _gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, int(left), int(top), 0x00CC0020):  # SRCCOPY
            return None
        _gdi32.SelectObject(mem_dc, old)
        old = None
        bi = _BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.biWidth = width
        bi.biHeight = -height  # top-down rows
        bi.biPlanes = 1
        bi.biBitCount = 32
        buf = ctypes.create_string_buffer(width * height * 4)
        if not _gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bi), 0):  # DIB_RGB_COLORS
            return None
        return buf.raw, width, height
    finally:
        if old:
            _gdi32.SelectObject(mem_dc, old)
        if bmp:
            _gdi32.DeleteObject(bmp)
        if mem_dc:
            _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(None, screen_dc)


def can_exclude_from_capture() -> bool:
    """WDA_EXCLUDEFROMCAPTURE needs Windows 10 2004 (build 19041) or newer."""
    if not IS_WINDOWS:
        return False
    try:
        return sys.getwindowsversion().build >= 19041
    except Exception:
        return False


def set_exclude_from_capture(hwnd: int, on: bool) -> bool:
    """Leave a window out of screenshots (it stays visible on screen), so our
    own window and indicator never end up in checkpoint pictures."""
    if not can_exclude_from_capture() or not hwnd:
        return False
    try:
        return bool(_user32.SetWindowDisplayAffinity(int(hwnd), 0x11 if on else 0))
    except Exception:
        return False
