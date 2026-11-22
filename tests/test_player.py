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


# ------------------------------------------- position (checkpoint key while playing)
def test_position_follows_playback_and_speed():
    seen = {}

    def check(e, run):
        seen[e.ref] = p.position()
        return True

    p = Player([Event(0.0, CHECK, ref="a"), Event(2.0, CHECK, ref="b")], on_check=check, speed=2.0)
    assert p.position() is None          # not started
    p.start()
    time.sleep(0.15)
    run, t = p.position()                # 0.15s in at double speed: about 0.3s of the macro
    p._thread.join(5)
    assert run == 1 and 0.2 <= t < 2.0
    assert seen == {"a": (1, 0.0), "b": (1, 2.0)}   # held at the checkpoint while it's checked


def test_position_holds_during_a_check_and_continues_after():
    def check(e, run):
        if e.ref == "slow":
            time.sleep(0.4)
        return True

    p = Player([Event(0.0, CHECK, ref="slow"), Event(1.0, CHECK, ref="end")], on_check=check)
    p.start()
    time.sleep(0.2)
    during = p.position()
    time.sleep(0.4)                      # 0.6s in, 0.4s of it spent checking
    after = p.position()
    p.stop()
    p._thread.join(5)
    assert during == (1, 0.0)
    assert 0.1 < after[1] < 0.5


def test_position_stays_at_the_end_between_runs():
    p = Player([Event(0.0, CHECK, ref="a"), Event(0.1, CHECK, ref="b")], on_check=lambda e, run: True,
               repeats=2, delay=0.6)
    p.start()
    time.sleep(0.35)                     # run 1 is done, waiting for run 2
    pos = p.position()
    p.stop()
    p._thread.join(5)
    assert pos == (1, 0.1)
