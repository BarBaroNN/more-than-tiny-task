"""HotkeyManager's Windows keyboard-hook filter, driven with fake hook data."""
from types import SimpleNamespace

from mttk.backend import HotkeyManager

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN = 0x0100, 0x0101, 0x0104
VK = {"f8": 0x77, "f9": 0x78, "f10": 0x79, "esc": 0x1B, "a": 0x41, "ctrl": 0xA2, "shift": 0xA0, "r": 0x52}


class Suppressed(Exception):
    pass


class FakeListener:
    def suppress_event(self):
        raise Suppressed()


def make(playing=False):
    fired = []
    state = {"playing": playing}
    hk = HotkeyManager(fired.append,
                       should_consume=lambda a: state["playing"] if a == "stop" else True)
    hk.set_bindings({"record": "f8", "play": "f9", "center": "f10", "stop": "esc",
                     "extra": "ctrl+shift+r"})
    hk._listener = FakeListener()
    return hk, fired, state


def send(hk, key, msg=WM_KEYDOWN, injected=False):
    """Returns 'suppressed', 'handled' (False) or 'pass' (True)."""
    data = SimpleNamespace(vkCode=VK[key], flags=0x10 if injected else 0)
    try:
        return "pass" if hk._win32_filter(msg, data) else "handled"
    except Suppressed:
        return "suppressed"


def test_f10_fires_once_and_never_reaches_apps():
    hk, fired, _ = make()
    assert send(hk, "f10") == "suppressed"
    assert send(hk, "f10") == "suppressed"          # auto-repeat: swallowed, not re-fired
    assert send(hk, "f10", WM_KEYUP) == "suppressed"
    assert fired == ["center"]


def test_f10_as_syskeydown_is_handled():
    hk, fired, _ = make()
    assert send(hk, "f10", WM_SYSKEYDOWN) == "suppressed"
    assert fired == ["center"]


def test_esc_passes_through_unless_playing():
    hk, fired, state = make(playing=False)
    assert send(hk, "esc") == "handled"   # not swallowed: the app still gets Esc
    assert send(hk, "esc", WM_KEYUP) == "pass"
    state["playing"] = True
    assert send(hk, "esc") == "suppressed"
    assert send(hk, "esc", WM_KEYUP) == "suppressed"
    assert fired == ["stop", "stop"]


def test_normal_keys_and_injected_keys_untouched():
    hk, fired, _ = make()
    assert send(hk, "a") == "pass"
    assert send(hk, "f8", injected=True) == "pass"   # macro playback pressing F8
    assert fired == []


def test_modifier_combo():
    hk, fired, _ = make()
    assert send(hk, "r") == "pass"
    send(hk, "ctrl")
    send(hk, "shift")
    assert send(hk, "r") == "suppressed"
    send(hk, "shift", WM_KEYUP)
    send(hk, "ctrl", WM_KEYUP)
    assert send(hk, "f8") == "suppressed"
    assert fired == ["extra", "record"]
