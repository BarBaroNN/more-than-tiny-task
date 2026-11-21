"""Conversion between pynput keys, macro events and hotkey strings.

Hotkey strings look like ``"f8"``, ``"ctrl+shift+r"`` or ``"alt+num5"``.
"""
from __future__ import annotations

from typing import FrozenSet, Optional, Tuple

from .model import KEY, Event

MODIFIERS = ("ctrl", "shift", "alt", "win")

_MOD_ALIASES = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "cmd": "win", "cmd_l": "win", "cmd_r": "win",
}

# pynput Key names that we expose in hotkeys as-is
_SPECIAL_NAMES = {
    "esc", "pause", "insert", "delete", "home", "end", "page_up", "page_down",
    "scroll_lock", "num_lock", "caps_lock", "space", "tab", "up", "down",
    "left", "right", "print_screen", "backspace", "enter", "menu",
}

DISPLAY = {
    "esc": "Esc", "page_up": "PgUp", "page_down": "PgDn", "scroll_lock": "ScrLk",
    "num_lock": "NumLk", "caps_lock": "Caps", "print_screen": "PrtSc",
    "backspace": "Backspace", "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win",
}


def modifier_of(name: str) -> Optional[str]:
    return _MOD_ALIASES.get(name)


def pynput_key_name(key) -> str:
    """Normalised name of a pynput key (``'f8'``, ``'a'``, ``'ctrl'``, ``'num5'`` ...)."""
    name = getattr(key, "name", None)
    if name:  # pynput.keyboard.Key member
        return _MOD_ALIASES.get(name, name)
    vk = getattr(key, "vk", None)
    if vk:
        if 65 <= vk <= 90:
            return chr(vk).lower()
        if 48 <= vk <= 57:
            return chr(vk)
        if 96 <= vk <= 105:
            return f"num{vk - 96}"
        if 112 <= vk <= 135:
            return f"f{vk - 111}"
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    return f"vk{vk}" if vk else ""


def parse_hotkey(text: str) -> Tuple[FrozenSet[str], str]:
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    mods = frozenset(p for p in parts if p in MODIFIERS)
    keys = [p for p in parts if p not in MODIFIERS]
    return mods, (keys[-1] if keys else "")


def format_hotkey(text: str) -> str:
    mods, key = parse_hotkey(text)
    if not key:
        return "None"
    ordered = [m for m in MODIFIERS if m in mods]
    pretty = [DISPLAY.get(m, m.title()) for m in ordered]
    pretty.append(DISPLAY.get(key, key.upper() if len(key) <= 3 else key.replace("_", " ").title()))
    return " + ".join(pretty)


def qt_key_to_name(qt_key: int, text: str = "") -> Optional[str]:
    """Map a Qt key code to our hotkey key name (None for unsupported / modifiers)."""
    from PySide6.QtCore import Qt
    K = Qt.Key
    try:
        qt_key = K(qt_key)
    except Exception:
        return None
    if K.Key_F1.value <= qt_key.value <= K.Key_F24.value:
        return f"f{qt_key.value - K.Key_F1.value + 1}"
    if K.Key_A.value <= qt_key.value <= K.Key_Z.value:
        return chr(qt_key.value).lower()
    if K.Key_0.value <= qt_key.value <= K.Key_9.value:
        return chr(qt_key.value)
    table = {
        K.Key_Escape: "esc", K.Key_Pause: "pause", K.Key_Insert: "insert",
        K.Key_Delete: "delete", K.Key_Home: "home", K.Key_End: "end",
        K.Key_PageUp: "page_up", K.Key_PageDown: "page_down",
        K.Key_ScrollLock: "scroll_lock", K.Key_NumLock: "num_lock",
        K.Key_CapsLock: "caps_lock", K.Key_Space: "space", K.Key_Tab: "tab",
        K.Key_Up: "up", K.Key_Down: "down", K.Key_Left: "left", K.Key_Right: "right",
        K.Key_Print: "print_screen", K.Key_Backspace: "backspace",
        K.Key_Return: "enter", K.Key_Enter: "enter", K.Key_Menu: "menu",
    }
    if qt_key in table:
        return table[qt_key]
    if text and text.isprintable() and len(text) == 1 and not text.isspace():
        return text.lower()
    return None


def event_from_pynput(key, pressed: bool, t: float) -> Event:
    name = getattr(key, "name", None)
    if name:
        vk = getattr(getattr(key, "value", None), "vk", 0) or 0
        return Event(t, KEY, pressed=pressed, key_name=name, vk=vk)
    return Event(t, KEY, pressed=pressed, vk=getattr(key, "vk", 0) or 0,
                 char=getattr(key, "char", "") or "")


def pynput_from_event(e: Event):
    from pynput.keyboard import Key, KeyCode
    if e.key_name:
        try:
            return Key[e.key_name]
        except KeyError:
            pass
    if e.vk:
        return KeyCode.from_vk(e.vk)
    return KeyCode.from_char(e.char or " ")


_NAME_TO_VK = {
    "esc": 0x1B, "pause": 0x13, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "page_down": 0x22, "scroll_lock": 0x91, "num_lock": 0x90,
    "caps_lock": 0x14, "space": 0x20, "tab": 0x09, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "print_screen": 0x2C, "backspace": 0x08,
    "enter": 0x0D, "menu": 0x5D,
}

# Windows virtual-key codes of the modifier keys
VK_MODIFIERS = {
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x12: "alt", 0xA4: "alt", 0xA5: "alt",
    0x5B: "win", 0x5C: "win",
}


def name_to_vk(name: str) -> int:
    """Windows virtual-key code for a hotkey key name (0 if unknown)."""
    if not name:
        return 0
    if name in _NAME_TO_VK:
        return _NAME_TO_VK[name]
    if len(name) == 1 and ("a" <= name <= "z" or "0" <= name <= "9"):
        return ord(name.upper())
    if name.startswith("num") and name[3:].isdigit():
        return 0x60 + int(name[3:])
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x6F + int(name[1:])
    if name.startswith("vk") and name[2:].isdigit():
        return int(name[2:])
    return 0
