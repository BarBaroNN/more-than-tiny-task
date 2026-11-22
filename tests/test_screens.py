"""Mapping mouse positions between monitor layouts."""
from mttk.model import KEY, MOVE, Event
from mttk.screens import (ALL, PLAY_RECORDED, ScreenMap, layout_meta, monitor_at,
                          resolve_record_on)

MAIN = [0, 0, 1920, 1080]
SECOND = [1920, 0, 2560, 1440]
TWO = [MAIN, SECOND]


def mv(x, y, t=0.0):
    return Event(t, MOVE, x=x, y=y)


def xy(events):
    return [(e.x, e.y) for e in events if e.kind == MOVE]


def test_same_layout_is_unchanged():
    meta = layout_meta(TWO, 1)
    m = ScreenMap(meta, TWO)
    assert xy(m.map_events([mv(3200, 720), mv(4479, 1439)])) == [(3200, 720), (4479, 1439)]
    assert m.is_identity()


def test_second_screen_recording_played_with_only_the_main_screen():
    # the reported bug: recorded on screen 2, now only one screen is connected
    m = ScreenMap(layout_meta(TWO, 1), [MAIN])
    assert xy(m.map_events([mv(1920, 0), mv(3200, 720), mv(4479, 1439)])) == [(0, 0), (960, 540), (1919, 1079)]


def test_play_on_a_chosen_screen():
    meta = layout_meta(TWO, 0)                          # recorded on the main screen
    m = ScreenMap(meta, TWO, play_on=1)                 # played on screen 2
    assert xy(m.map_events([mv(960, 540)])) == [(3200, 720)]


def test_all_screens_recording_maps_each_screen_separately():
    meta = layout_meta(TWO, ALL)
    events = [mv(100, 100), mv(3200, 720)]
    assert xy(ScreenMap(meta, TWO).map_events(events)) == [(100, 100), (3200, 720)]
    # screen 2 unplugged: its part goes to the main screen, rescaled
    assert xy(ScreenMap(meta, [MAIN]).map_events(events)) == [(100, 100), (960, 540)]


def test_positions_never_end_up_off_screen():
    # recorded on screen 1, but the mouse wandered far off it while recording
    m = ScreenMap(layout_meta(TWO, 0), [MAIN])
    assert xy(m.map_events([mv(3000, 2000), mv(-50, -50)])) == [(1919, 1079), (0, 0)]


def test_without_rescaling_offsets_are_kept_from_the_screen_corner():
    m = ScreenMap(layout_meta(TWO, 1), [MAIN], scale=False)
    assert xy(m.map_events([mv(2020, 100), mv(4000, 1400)])) == [(100, 100), (1919, 1079)]


def test_keys_are_left_alone():
    k = Event(1.0, KEY, vk=0x41)
    assert ScreenMap(layout_meta(TWO, 1), [MAIN]).map_events([k]) == [k]


def test_screen_matching_prefers_same_rect_then_same_number_then_main():
    swapped = [SECOND, MAIN]  # same screens, other one is main now
    m = ScreenMap(layout_meta(TWO, 1), swapped)
    assert xy(m.map_events([mv(3200, 720)])) == [(3200, 720)]
    bigger = [[0, 0, 3840, 2160], [3840, 0, 1920, 1080]]
    m = ScreenMap(layout_meta(TWO, 1), bigger)  # screen 2 now 1920x1080 at x=3840
    assert xy(m.map_events([mv(3200, 720)])) == [(3840 + 960, 540)]


def test_legacy_macro_recorded_on_a_second_screen_now_unplugged():
    # macros saved before this change only stored the main screen's rect
    meta = {"screen": MAIN}
    events = [mv(3200, 301), mv(3978, 1154)]
    m = ScreenMap(meta, [[0, 0, 2560, 1440]], events=events)
    # assumed to sit right of the main screen, top-aligned: lands where it was
    assert xy(m.map_events(events)) == [(1280, 301), (2058, 1154)]
    assert m.note


def test_legacy_macro_on_a_second_screen_that_is_still_connected():
    events = [mv(3200, 301), mv(3978, 1154)]
    m = ScreenMap({"screen": MAIN}, TWO, events=events)
    assert xy(m.map_events(events)) == [(3200, 301), (3978, 1154)]
    assert not m.note


def test_legacy_macro_on_the_main_screen_still_rescales():
    m = ScreenMap({"screen": [0, 0, 2560, 1440]}, [MAIN], events=[mv(1280, 720)])
    assert xy(m.map_events([mv(1280, 720)])) == [(960, 540)]


def test_rerecorded_part_is_converted_back_to_the_macros_coordinates():
    meta = layout_meta(TWO, 1)
    m = ScreenMap(meta, [MAIN])                       # recorded on 2, re-recording on 1 screen
    played = m.map_events([mv(3200, 720)])
    assert xy(m.inverse_events(played)) == [(3200, 720)]


def test_record_on_choices():
    assert resolve_record_on("auto", TWO, (3000, 500)) == 1
    assert resolve_record_on("auto", TWO, (10, 10)) == 0
    assert resolve_record_on(ALL, TWO, (10, 10)) == ALL
    assert resolve_record_on(1, TWO, (10, 10)) == 1
    assert resolve_record_on(5, TWO, (10, 10)) == 0       # screen gone: the one under the mouse


def test_monitor_at_falls_back_to_nearest():
    assert monitor_at(TWO, 5000, 100) == 1
    assert monitor_at(TWO, -10, 500) == 0


def test_unknown_play_on_screen_falls_back_to_recorded_layout():
    m = ScreenMap(layout_meta(TWO, 1), TWO, play_on=7)
    assert m.play_on == PLAY_RECORDED


# ------------------------------------------------- screen checkpoint areas
def test_checkpoint_area_follows_the_macro_to_another_screen():
    area = [2240, 180, 640, 360]                        # on screen 2 (2560x1440), recorded there
    assert ScreenMap(layout_meta(TWO, 1), TWO).rect(area) == tuple(area)
    # only the main screen now: moved and rescaled like the clicks inside it
    assert ScreenMap(layout_meta(TWO, 1), [MAIN]).rect(area) == (240, 135, 480, 270)
    # recorded on the main screen, played on screen 2
    assert ScreenMap(layout_meta(TWO, 0), TWO, play_on=1).rect([480, 270, 960, 540]) == (2560, 360, 1280, 720)


def test_checkpoint_area_uses_the_same_screen_as_the_positions_in_it():
    m = ScreenMap(layout_meta(TWO, 0), TWO, play_on=1)
    x, y, w, h = m.rect([100, 100, 200, 100])
    (px, py), = xy(m.map_events([mv(150, 150)]))       # a click inside the area
    assert x <= px < x + w and y <= py < y + h


def test_checkpoint_area_is_never_off_every_screen():
    # recorded on screen 1, area on screen 2 (taken there), played with screen 1 only
    x, y, w, h = ScreenMap(layout_meta(TWO, 0), [MAIN]).rect([2500, 500, 400, 300])
    assert 0 <= x and x + w <= 1920 and 0 <= y and y + h <= 1080


def test_checkpoint_taken_now_is_stored_in_the_macros_coordinates():
    # macro recorded on screen 2 of a 2-screen PC, now played on a single screen
    m = ScreenMap(layout_meta(TWO, 1), [MAIN])
    stored = m.inverse_rect([240, 135, 480, 270])       # picture area on the screen now
    assert stored == (2240, 180, 640, 360)
    assert m.rect(stored) == (240, 135, 480, 270)       # and back again
    # play on screen 2 of a macro recorded on screen 1
    m = ScreenMap(layout_meta(TWO, 0), TWO, play_on=1)
    assert m.rect(m.inverse_rect([2560, 360, 1280, 720])) == (2560, 360, 1280, 720)
