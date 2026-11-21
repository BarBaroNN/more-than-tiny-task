"""Mouse moves are sent as real input events (then snapped to the pixel)."""
from mttk import backend, winutil
from mttk.model import MOVE, Event


def test_normalize_absolute_covers_the_whole_virtual_desktop():
    vrect = (0, 0, 4480, 1440)  # 1920x1080 + 2560x1440 side by side
    assert winutil.normalize_absolute(0, 0, vrect) == (0, 0)
    assert winutil.normalize_absolute(4479, 1439, vrect) == (65535, 65535)
    nx, ny = winutil.normalize_absolute(3200, 720, vrect)
    assert abs(nx - 3200 * 65535 / 4479) < 1 and abs(ny - 720 * 65535 / 1439) < 1


def test_normalize_absolute_handles_monitors_left_of_primary_and_clamps():
    vrect = (-1920, 0, 3840, 1080)
    assert winutil.normalize_absolute(-1920, 0, vrect) == (0, 0)
    assert winutil.normalize_absolute(5000, -50, vrect) == (65535, 0)


def test_player_moves_through_set_mouse_position(monkeypatch):
    calls = []
    monkeypatch.setattr(winutil, "move_mouse", lambda x, y, mode: calls.append((x, y, mode)) or True)
    p = backend.Player([], mouse_mode=winutil.MOUSE_ABSOLUTE)
    p._mouse = None  # must not be used when move_mouse succeeds
    p._do(Event(0.0, MOVE, x=3200, y=720))
    assert calls == [(3200, 720, "absolute")]


def test_set_mouse_position_falls_back_to_placing_the_pointer(monkeypatch):
    monkeypatch.setattr(winutil, "move_mouse", lambda x, y, mode: False)

    class FakeController:
        position = None
    c = FakeController()
    backend.set_mouse_position(10, 20, controller=c)
    assert c.position == (10, 20)
