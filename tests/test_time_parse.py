from mttk.ui.timeline import fmt_time, parse_time


def test_parse_typed_times():
    assert parse_time("1:34") == 94.0
    assert parse_time(" 1:34.5 ") == 94.5
    assert parse_time("94") == 94.0
    assert parse_time("94.5s") == 94.5
    assert parse_time("1:02:03") == 3723.0
    for bad in ("", "abc", "1:", "-3", "1:2:3:4", "inf", "nan"):
        assert parse_time(bad) is None, bad


def test_parse_reads_what_fmt_time_writes():
    for t in (0.0, 5.25, 94.0, 754.5, 3723.75):
        assert abs(parse_time(fmt_time(t, 2)) - t) < 0.006
