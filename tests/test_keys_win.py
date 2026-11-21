"""Key playback parameters (the TinyTask way) and scan-code capture while recording."""
from types import SimpleNamespace

from mttk import backend, winutil
from mttk import keys as K
from mttk.model import KEY, Event

SCANS = {0x41: 0x1E, 0xA0: 0x2A, 0xA1: 0x36, 0xA2: 0x1D, 0xA3: 0x1D, 0x26: 0x48, 0x5B: 0x5B, 0x0D: 0x1C}


def vk_to_scan(vk):
    return SCANS.get(vk, 0x10)


def char_to_vk(ch):
    return ord(ch.upper()) if ch and ch.isalpha() else 0


def params(**kw):
    return K.win_key_params(Event(0.0, KEY, **kw), vk_to_scan, char_to_vk)


def test_letter_uses_vk_and_scan_like_tinytask():
    assert params(vk=0x41, char="a") == (0x41, 0x1E, 0)
    assert params(vk=0x41, char="a", pressed=False) == (0x41, 0x1E, 0x2)


def test_navigation_and_win_keys_are_extended_without_hardware_info():
    # TinyTask marks 0x21..0x2E and the Win / Menu keys as extended
    assert params(key_name="up", vk=0x26) == (0x26, 0x48, 0x1)
    assert params(key_name="cmd", vk=0x5B) == (0x5B, 0x5B, 0x1)
    assert params(key_name="enter", vk=0x0D) == (0x0D, 0x1C, 0)


def test_modifiers_are_sent_as_generic_keys():
    assert params(key_name="shift", vk=0xA0) == (0x10, 0x2A, 0)
    assert params(key_name="shift_r", vk=0xA1) == (0x10, 0x36, 0)
    assert params(key_name="ctrl_l", vk=0xA2) == (0x11, 0x1D, 0)
    assert params(key_name="ctrl_r", vk=0xA3) == (0x11, 0x1D, 0x1)  # right side = E0


def test_recorded_hardware_scan_and_flag_win():
    # numpad Enter: same vk as Enter but E0-prefixed on the keyboard
    assert params(key_name="enter", vk=0x0D, scan=0x1C, ext=True) == (0x0D, 0x1C, 0x1)
    # a hardware scan code overrides MapVirtualKey
    assert params(vk=0x41, char="a", scan=0x10, ext=False) == (0x41, 0x10, 0)


def test_events_without_vk_fall_back_to_name_or_char():
    assert params(key_name="esc") == (0x1B, vk_to_scan(0x1B), 0)
    assert params(char="b") == (0x42, vk_to_scan(0x42), 0)
    assert params(char="€") is None  # not a key on this layout: caller falls back


def test_scan_and_ext_survive_save_load_and_old_files_still_load():
    e = Event(1.0, KEY, key_name="enter", vk=0x0D, scan=0x1C, ext=True)
    assert Event.from_dict(e.to_dict()) == e
    old = Event.from_dict({"t": 1.0, "kind": KEY, "key_name": "enter", "vk": 13})
    assert old.scan == 0 and old.ext is False


def _hook_data(vk, scan, flags=0):
    return SimpleNamespace(vkCode=vk, scanCode=scan, flags=flags)


def test_recorder_attaches_hardware_scan_codes(monkeypatch):
    monkeypatch.setattr(winutil, "IS_WINDOWS", True)
    r = backend.Recorder()
    r.running = True
    r._t0 = 0.0
    key_a = SimpleNamespace(vk=0x41, char="a")
    enter = SimpleNamespace(name="enter", value=SimpleNamespace(vk=0x0D))
    r._win32_key_filter(0x100, _hook_data(0x41, 0x1E))
    r._win32_key_filter(0x100, _hook_data(0x0D, 0x1C, flags=0x01))  # numpad Enter
    r._win32_key_filter(0x100, _hook_data(0x42, 0x30, flags=0x10))  # injected: not queued
    r._press(key_a, False)
    r._press(enter, False)
    a, ent = r._events
    assert (a.vk, a.scan, a.ext) == (0x41, 0x1E, False)
    assert (ent.key_name, ent.scan, ent.ext) == ("enter", 0x1C, True)


def test_recorder_ignored_hotkeys_still_consume_their_hook_entry(monkeypatch):
    monkeypatch.setattr(winutil, "IS_WINDOWS", True)
    r = backend.Recorder(ignore_key=lambda name: name == "f8")
    r.running = True
    f8 = SimpleNamespace(name="f8", value=SimpleNamespace(vk=0x77))
    key_a = SimpleNamespace(vk=0x41, char="a")
    r._win32_key_filter(0x100, _hook_data(0x77, 0x42))
    r._win32_key_filter(0x100, _hook_data(0x41, 0x1E))
    r._press(f8, False)
    r._press(key_a, False)
    (a,) = r._events
    assert (a.vk, a.scan) == (0x41, 0x1E)


def test_recorder_drops_scan_codes_when_out_of_step(monkeypatch):
    monkeypatch.setattr(winutil, "IS_WINDOWS", True)
    r = backend.Recorder()
    r.running = True
    r._win32_key_filter(0x100, _hook_data(0x42, 0x30))
    r._press(SimpleNamespace(vk=0x41, char="a"), False)
    (a,) = r._events
    assert a.scan == 0  # better MapVirtualKey later than the wrong key's scan code
