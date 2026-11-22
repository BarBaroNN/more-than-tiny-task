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


# ------------------------------------------------------- screen checkpoints
import time  # noqa: E402

from mttk.backend import Player  # noqa: E402
from mttk.model import CHECK, Event  # noqa: E402


def run_player(events, on_check, **kw):
    done, progress = [], []
    p = Player(events, on_check=on_check, on_finished=done.append,
               on_progress=lambda run, total, t: progress.append(t), **kw)
    p.start()
    p._thread.join(5)
    return p, done, progress


def test_checkpoint_pause_delays_the_rest():
    calls = []

    def check(e, run):
        calls.append(time.perf_counter())
        if e.ref == "slow":
            time.sleep(0.3)
        return True

    events = [Event(0.0, CHECK, ref="slow"), Event(0.05, CHECK, ref="next")]
    p, done, _ = run_player(events, check)
    assert done == [True] and not p.check_failed
    # "next" still comes 0.05s after "slow" finished, not 0.05s after the start
    assert calls[1] - calls[0] >= 0.33


def test_failed_checkpoint_stops_playback():
    calls = []

    def check(e, run):
        calls.append(e.ref)
        return e.ref != "bad"

    events = [Event(0.0, CHECK, ref="ok"), Event(0.01, CHECK, ref="bad"), Event(0.02, CHECK, ref="never")]
    p, done, progress = run_player(events, check, repeats=3)
    assert done == [False] and p.check_failed
    assert calls == ["ok", "bad"]
    assert progress[-1] == 0.01          # stopped at the failed checkpoint (resuming re-checks it)


def test_checkpoint_that_raises_counts_as_failed():
    def check(e, run):
        raise RuntimeError("boom")

    p, done, _ = run_player([Event(0.0, CHECK, ref="x")], check)
    assert done == [False] and p.check_failed


def test_checkpoints_skipped_without_checker():
    p, done, _ = run_player([Event(0.0, CHECK, ref="x")], None)
    assert done == [True]
