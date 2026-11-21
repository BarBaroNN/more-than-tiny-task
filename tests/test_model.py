import os
import tempfile

from mttk import model as M
from mttk.model import CLICK, KEY, MOVE, Event


def ev(t, kind=MOVE, **kw):
    return Event(t, kind, **kw)


def ts(events):
    return [round(e.t, 6) for e in events]


BASE = [ev(0.0), ev(1.0), ev(2.0), ev(3.0), ev(4.0)]


def test_delete_range_ripple():
    out = M.delete_range(BASE, 1.0, 3.0)
    assert ts(out) == [0.0, 1.0, 2.0]


def test_delete_range_no_ripple():
    assert ts(M.delete_range(BASE, 1.0, 3.0, ripple=False)) == [0.0, 3.0, 4.0]


def test_slice_and_insert_roundtrip():
    clip = M.slice_range(BASE, 1.0, 3.0)
    assert ts(clip) == [0.0, 1.0]
    out = M.insert_at(BASE, 2.0, clip, 2.0)
    assert ts(out) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_replace_range_longer_clip_shifts_tail():
    clip = [ev(0.5), ev(2.5)]
    out = M.replace_range(BASE, 1.0, 2.0, clip, 3.0)
    # 0 | clip at 1.5, 3.5 | old 2,3,4 shifted by +2
    assert ts(out) == [0.0, 1.5, 3.5, 4.0, 5.0, 6.0]


def test_replace_range_reversed_bounds():
    assert ts(M.replace_range(BASE, 2.0, 1.0, [], 0.0)) == ts(M.delete_range(BASE, 1.0, 2.0))


def test_insert_gap_and_scale():
    assert ts(M.insert_gap(BASE, 2.0, 1.5)) == [0.0, 1.0, 3.5, 4.5, 5.5]
    assert ts(M.scale_range(BASE, 0.0, 2.0, 2.0)) == [0.0, 2.0, 4.0, 5.0, 6.0]


def test_set_delay():
    assert ts(M.set_delay(BASE, 2, 0.25)) == [0.0, 1.0, 1.25, 2.25, 3.25]


def test_sanitize_drops_orphans_and_trailing_modifiers():
    events = [
        ev(0.0, KEY, key_name="f8", pressed=False),       # orphan release
        ev(0.1, CLICK, button="left", pressed=False),     # orphan release
        ev(0.2, KEY, char="a", vk=65, pressed=True),
        ev(0.3, KEY, char="a", vk=65, pressed=False),
        ev(0.4, KEY, key_name="ctrl_l", pressed=True),    # dangling modifier
    ]
    out = M.sanitize_recording(events)
    assert ts(out) == [0.2, 0.3]


def test_thin_moves_keeps_anchor_before_click():
    events = [ev(i * 0.001) for i in range(100)] + [ev(0.1, CLICK, button="left")]
    out = M.thin_moves(events, 0.02)
    assert out[-1].kind == CLICK
    assert out[-2].t == 0.099
    assert len(out) < 20


def test_cursor_position_at():
    events = [ev(0.0, x=1, y=1), ev(1.0, KEY, char="a"), ev(2.0, x=5, y=6)]
    assert M.cursor_position_at(events, 1.5) == (1, 1)
    assert M.cursor_position_at(events, 2.0) == (5, 6)


def test_save_load_roundtrip():
    events = [ev(0.0, x=10, y=20), ev(0.5, CLICK, x=1, y=2, button="right", pressed=False),
              ev(1.0, KEY, key_name="enter", vk=13)]
    path = os.path.join(tempfile.mkdtemp(), "m.mttk")
    M.Macro(events).save(path)
    assert M.Macro.load(path).events == events


def test_scale_events_2k_to_1080p():
    events = [ev(0.0, x=2560 // 2, y=1440 // 2), ev(0.1, CLICK, x=2559, y=0, button="left"),
              ev(0.2, KEY, key_name="enter")]
    out = M.scale_events(events, (0, 0, 2560, 1440), (0, 0, 1920, 1080))
    assert (out[0].x, out[0].y) == (960, 540)
    assert (out[1].x, out[1].y) == (1919, 0)
    assert out[2] == events[2]


def test_scale_events_identity_and_offset_origin():
    events = [ev(0.0, x=100, y=100)]
    assert M.scale_events(events, (0, 0, 1920, 1080), (0, 0, 1920, 1080)) == events
    out = M.scale_events([ev(0.0, x=-1920, y=0)], (-1920, 0, 1920, 1080), (0, 0, 1280, 720))
    assert (out[0].x, out[0].y) == (0, 0)


def test_offset_events_relative_start():
    events = [ev(0.0, KEY, char="a"), ev(0.1, x=500, y=500), ev(0.2, CLICK, x=600, y=550, button="left")]
    assert M.first_position(events) == (500, 500)
    out = M.offset_events(events, 100 - 500, 200 - 500)
    assert [(e.x, e.y) for e in out[1:]] == [(100, 200), (200, 250)]
    assert out[0] == events[0]


def test_resume_from_starts_at_playhead_with_cursor_position():
    events = [ev(0.0, x=10, y=10), ev(1.0, x=20, y=20), ev(2.0, KEY, key_name="a", pressed=True),
              ev(2.5, KEY, key_name="a", pressed=False), ev(3.0, x=30, y=30)]
    out = M.resume_from(events, 1.5)
    # cursor placed where it was at 1.5s, then the tail re-based to start at 0
    assert out[0].kind == MOVE and (out[0].x, out[0].y) == (20, 20) and out[0].t == 0.0
    assert ts(out[1:]) == [0.5, 1.0, 1.5]


def test_resume_from_repress_held_keys_and_buttons():
    events = [ev(0.0, CLICK, x=5, y=5, button="left", pressed=True), ev(0.5, x=8, y=8),
              ev(1.0, KEY, key_name="shift", pressed=True), ev(1.5, KEY, key_name="a", pressed=True),
              ev(1.6, KEY, key_name="a", pressed=False),
              ev(3.0, KEY, key_name="shift", pressed=False),
              ev(4.0, CLICK, x=9, y=9, button="left", pressed=False)]
    out = M.resume_from(events, 2.0)
    head = [e for e in out if e.t == 0.0]
    assert head[0].kind == MOVE and (head[0].x, head[0].y) == (8, 8)
    # left button (pressed at the resume position) and shift are still down; "a" is not
    assert any(e.kind == CLICK and e.pressed and (e.x, e.y) == (8, 8) for e in head)
    assert any(e.kind == KEY and e.key_name == "shift" and e.pressed for e in head)
    assert not any(e.kind == KEY and e.key_name == "a" for e in head)
    assert ts(out[len(head):]) == [1.0, 2.0]


def test_resume_from_event_exactly_at_t_is_kept():
    out = M.resume_from(BASE, 2.0)
    # move to the position at 1.0 (last before 2.0), then events 2, 3, 4
    assert ts(out) == [0.0, 0.0, 1.0, 2.0]


def test_resume_from_zero_is_whole_macro():
    assert ts(M.resume_from(BASE, 0.0)) == ts(BASE)
