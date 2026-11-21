from mttk.backend import stop_position


def test_stop_position_between_events():
    # stopped 0.3s after an event at 1.0, next event at 2.0: keep the real time
    assert stop_position(1.3, 1.0, 2.0) == 1.3


def test_stop_position_never_before_played_event():
    # progress lagging behind: still after the event that already ran
    pos = stop_position(0.9, 1.0, 2.0)
    assert 1.0 < pos < 2.0


def test_stop_position_never_after_next_event():
    pos = stop_position(2.4, 1.0, 2.0)
    assert 1.0 < pos < 2.0


def test_stop_position_before_first_event():
    assert stop_position(0.0, None, 0.0) == 0.0
    assert stop_position(0.5, None, 1.0) == 0.5
