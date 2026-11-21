"""Macros saved before monitor layouts were stored (<= 0.0.3) keep working.

Modelled on a real one: meta.screen is the 1920x1080 main screen, but every
mouse position was recorded on a 2560x1440 second screen at x=1920
(x 3200-3978, y 301-1154).
"""
import json

from mttk.model import MOVE, Macro
from mttk.screens import ScreenMap, find_legacy_layout, remember_layout

MAIN = [0, 0, 1920, 1080]
SECOND = [1920, 0, 2560, 1440]
TWO = [MAIN, SECOND]
ONLY_MAIN = [MAIN]
ONLY_BIG = [[0, 0, 2560, 1440]]


def old_file(tmp_path):
    events = [{"t": 0.0, "kind": "move", "x": 3200, "y": 301},
              {"t": 0.5, "kind": "move", "x": 3589, "y": 720},
              {"t": 1.0, "kind": "key", "vk": 71, "char": "g"},
              {"t": 1.5, "kind": "move", "x": 3978, "y": 1154},
              {"t": 2.0, "kind": "key", "pressed": False, "vk": 71, "char": "g"}]
    path = tmp_path / "macro.mttk"
    path.write_text(json.dumps({"format": "more-than-tiny-task", "version": 1,
                                "meta": {"app_version": "0.0.2", "screen": MAIN},
                                "events": events}), encoding="utf-8")
    return Macro.load(str(path))


def moves(events):
    return [(e.x, e.y) for e in events if e.kind == MOVE]


def on_screen(points, layout):
    return all(any(r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3] for r in layout) for x, y in points)


def test_old_file_loads_without_layout(tmp_path):
    m = old_file(tmp_path)
    assert "monitors" not in m.meta and m.meta["screen"] == MAIN


def test_with_both_screens_connected_playback_is_unchanged(tmp_path):
    m = old_file(tmp_path)
    sm = ScreenMap(m.meta, TWO, events=m.events)
    assert moves(sm.map_events(m.events)) == moves(m.events)
    assert not sm.note


def test_layout_is_detected_from_the_screens_connected_now(tmp_path):
    m = old_file(tmp_path)
    found = find_legacy_layout(m.meta, m.events, [TWO])
    assert found["monitors"] == TWO and found["record_on"] == 1 and found["screen"] == SECOND


def test_remembered_layout_maps_exactly_with_only_one_screen(tmp_path):
    m = old_file(tmp_path)
    known = remember_layout([], TWO)              # the app saw both screens before
    sm = ScreenMap(m.meta, ONLY_MAIN, events=m.events, known_layouts=known)
    # second screen 2560x1440 -> main 1920x1080: x' = (x - 1920) * 0.75, y' = y * 0.75
    assert moves(sm.map_events(m.events)) == [(960, 226), (1252, 540), (1544, 866)]
    assert not sm.note


def test_remembered_layout_needs_the_same_main_screen(tmp_path):
    m = old_file(tmp_path)
    other_pc = [[0, 0, 3840, 2160], [3840, 0, 1920, 1080]]
    assert find_legacy_layout(m.meta, m.events, [ONLY_MAIN, other_pc]) is None


def test_without_any_known_layout_it_still_stays_on_screen(tmp_path):
    m = old_file(tmp_path)
    for layout in (ONLY_MAIN, ONLY_BIG):
        sm = ScreenMap(m.meta, layout, events=m.events)
        assert on_screen(moves(sm.map_events(m.events)), layout), layout
    # on the 2560x1440 screen (the one it was recorded on) it lands on the same spot
    sm = ScreenMap(m.meta, ONLY_BIG, events=m.events)
    assert moves(sm.map_events(m.events)) == [(1280, 301), (1669, 720), (2058, 1154)]


def test_detected_layout_is_written_to_the_file_on_save(tmp_path):
    m = old_file(tmp_path)
    meta = dict(m.meta)
    meta.update(find_legacy_layout(meta, m.events, [TWO]))
    out = tmp_path / "saved.mttk"
    Macro(m.events, meta=meta).save(str(out))
    again = Macro.load(str(out))
    assert again.meta["monitors"] == TWO and again.meta["record_on"] == 1
    # and from then on it maps exactly even with one screen, no memory needed
    sm = ScreenMap(again.meta, ONLY_MAIN, events=again.events)
    assert moves(sm.map_events(again.events)) == [(960, 226), (1252, 540), (1544, 866)]


def test_old_macro_on_the_main_screen_keeps_old_behaviour(tmp_path):
    m = old_file(tmp_path)
    meta = {"screen": [0, 0, 2560, 1440]}
    events = [e for e in m.events if e.kind != MOVE]
    from mttk.model import Event
    events = [Event(0.0, MOVE, x=1280, y=720)] + events
    found = find_legacy_layout(meta, events, [TWO])
    assert found["monitors"] == [[0, 0, 2560, 1440]] and found["record_on"] == 0
    sm = ScreenMap(meta, ONLY_MAIN, events=events)
    assert moves(sm.map_events(events)) == [(960, 540)]


def test_remember_layout_keeps_newest_first_without_duplicates():
    known = remember_layout([], TWO)
    known = remember_layout(known, ONLY_BIG)
    known = remember_layout(known, TWO)
    assert known == [TWO, ONLY_BIG]
