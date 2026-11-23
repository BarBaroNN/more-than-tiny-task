"""Screen checkpoint comparison, with synthetic pictures instead of the screen."""
import random

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from mttk import vision as V
from mttk.vision import Gray


def noise(w, h, seed=1):
    rnd = random.Random(seed)
    return Gray(bytes(rnd.randrange(256) for _ in range(w * h)), w, h)


def test_similarity_identical_and_partly_covered():
    g = noise(40, 30)
    assert V.similarity(g.data, g.data) == 1.0
    covered = bytearray(g.data)
    covered[:120] = bytes(120)          # first 3 rows (10%) blacked out, like a popup
    s = V.similarity(g.data, bytes(covered))
    assert 0.89 <= s <= 0.92


def test_similarity_tolerates_small_differences():
    g = noise(20, 20)
    shifted = bytes(min(255, v + 10) for v in g.data)
    assert V.similarity(g.data, shifted) == 1.0
    assert V.similarity(g.data, bytes(min(255, v + 60) for v in g.data)) < 0.5


def area_around(ref: Gray, margin: int, dx=0, dy=0, seed=7) -> Gray:
    """ref placed in a noisy area with ``margin`` around it, moved by (dx, dy)."""
    w, h = ref.w + 2 * margin, ref.h + 2 * margin
    data = bytearray(noise(w, h, seed).data)
    for r in range(ref.h):
        y = margin + dy + r
        data[y * w + margin + dx:y * w + margin + dx + ref.w] = ref.data[r * ref.w:(r + 1) * ref.w]
    return Gray(bytes(data), w, h)


def test_best_match_in_place_and_within_allowed_shift():
    ref = noise(30, 20)
    assert V.best_match(ref, area_around(ref, 5), 5) == (1.0, 0, 0)
    moved = area_around(ref, 5, dx=-3, dy=2)
    score, _, _ = V.best_match(ref, moved, 5, max_shift=0)
    assert score < 0.5                                   # no shift allowed: fails
    assert V.best_match(ref, moved, 5, max_shift=3) == (1.0, -3, 2)


def test_find_shift_reports_drift():
    ref = noise(40, 24)
    area = area_around(ref, 8, dx=7, dy=0)
    score, dx, dy = V.find_shift(ref, area, 8)
    assert score == 1.0 and (dx, dy) == (7, 0)


def test_work_size_and_rects():
    assert V.work_size(1920, 1080) == (128, 72)
    assert V.work_size(50, 20) == (50, 20)
    assert V.clamp_region([-10, 5, 5000, 50], [0, 0, 1920, 1080]) == [0, 5, 1920, 50]


def screen_image(w=800, h=600, box=QRect(300, 200, 160, 90), color="#e04040"):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor("#203040"))
    p = QPainter(img)
    for i in range(0, w, 40):            # some texture
        p.fillRect(i, 0, 20, h, QColor("#2a4058"))
    p.fillRect(box, QColor(color))
    p.end()
    return img


def make_runner(screen, spec, **kw):
    results, statuses = [], []
    r = V.CheckpointRunner({"a": spec}, capture=lambda rect: screen().copy(*rect),
                           on_result=results.append, on_status=lambda *a: statuses.append(a),
                           interval=0.01, **kw)
    assert r.prepare(["a"]) == []
    return r, results, statuses


def spec_for(img, region, **kw):
    full = [0, 0, img.width(), img.height()]
    spec = V.new_checkpoint(img, full, full, "Shop", **kw)
    spec["region"] = region
    return spec


def test_runner_passes_when_screen_matches():
    img = screen_image()
    r, results, _ = make_runner(lambda: img, spec_for(img, [250, 150, 300, 200]))
    assert r.check("a", run=1, t=2.0) is True
    assert results[0].passed and results[0].score > 0.95


def translated(img, dx, dy):
    out = QImage(img.size(), QImage.Format_RGB32)
    out.fill(QColor("black"))
    p = QPainter(out)
    p.drawImage(dx, dy, img)
    p.end()
    return out


def test_runner_fails_after_timeout_and_reports_drift():
    ref = screen_image()
    drifted = translated(ref, 40, 0)                    # the whole view drifted 40px to the right
    r, results, statuses = make_runner(lambda: drifted, spec_for(ref, [280, 180, 200, 130], timeout=0.05))
    assert r.check("a") is False
    res = results[-1]
    assert not res.passed and res.score < res.threshold
    assert statuses                                              # it kept trying until the timeout
    assert res.looks_shifted and abs(res.looks_shifted[0] - 40) <= 3 and res.looks_shifted[1] == 0
    assert "right" in res.summary
    assert res.expected is not None and res.actual is not None


def test_runner_allowed_shift_passes():
    ref = screen_image()
    drifted = screen_image(box=QRect(310, 200, 160, 90))
    r, results, _ = make_runner(lambda: drifted, spec_for(ref, [280, 180, 200, 130], timeout=0.0, shift=20))
    assert r.check("a") is True


def test_runner_waits_for_laggy_screen():
    ref = screen_image()
    wrong = screen_image(color="#40e040")
    frames = iter([wrong, wrong, wrong])
    r, results, _ = make_runner(lambda: next(frames, ref), spec_for(ref, [280, 180, 200, 130], timeout=2.0))
    assert r.check("a") is True
    assert results[-1].passed


def test_runner_cancel_and_missing_data_carry_on():
    ref = screen_image()
    r, results, _ = make_runner(lambda: screen_image(color="#40e040"), spec_for(ref, [280, 180, 200, 130], timeout=5))
    r.cancel.set()
    assert r.check("a") is True              # stopped by the user: not a failure
    assert r.check("unknown") is True
    assert results == []


def test_runner_maps_area_to_this_screen():
    ref = screen_image(800, 600)
    big = screen_image(1600, 1200, box=QRect(600, 400, 320, 180))     # same picture, twice the resolution
    spec = spec_for(ref, [280, 180, 200, 130])
    r, results, _ = make_runner(lambda: big, spec,
                                to_screen=lambda rect, scr: [v * 2 for v in rect])
    assert r.check("a") is True and results[-1].score > 0.9


def test_encode_decode_and_crop():
    img = screen_image(2000, 1000)
    spec = spec_for(img, [500, 250, 1000, 500])
    stored = V.decode_image(spec["image"])
    assert max(stored.width(), stored.height()) == V.STORE_MAX_SIDE
    crop = V.reference_crop(spec)
    assert (crop.width(), crop.height()) == (640, 320)


def test_photo_checkpoint_sends_its_area_and_never_stops():
    img = screen_image()
    other = screen_image(color="#40e040")              # doesn't match, but it isn't compared
    spec = dict(spec_for(img, [100, 50, 400, 300]), compare=False, send=True)
    r, results, statuses = make_runner(lambda: other, spec)
    assert r.check("a", run=2, t=94.0) is True
    res = results[-1]
    assert res.passed and not res.compared and not res.error and not statuses
    assert (res.photo.width(), res.photo.height()) == (400, 300)
    assert (res.run, res.t) == (2, 94.0)


def test_photo_checkpoint_reports_a_failed_screenshot():
    img = screen_image()
    spec = dict(spec_for(img, [100, 50, 400, 300]), compare=False, send=True)
    r = V.CheckpointRunner({"a": spec}, capture=lambda rect: img.copy(*rect))
    results = []
    r.on_result = results.append
    assert r.prepare(["a"]) == []
    r.capture = lambda rect: None
    assert r.check("a") is True
    assert results[-1].photo is None and "screenshot" in results[-1].error


def test_matching_checkpoint_can_send_a_picture_too():
    img = screen_image()
    r, results, _ = make_runner(lambda: img, dict(spec_for(img, [250, 150, 300, 200]), send=True))
    assert r.check("a") is True
    assert results[-1].passed and results[-1].compared and results[-1].photo is not None
    r, results, _ = make_runner(lambda: img, spec_for(img, [250, 150, 300, 200]))
    assert r.check("a") is True and results[-1].photo is None   # "send" off: nothing to send


def test_checkpoint_kinds_described():
    img = screen_image()
    spec = spec_for(img, [0, 0, 10, 10])
    assert V.compares(spec) and not V.sends(spec)
    assert "sends" not in V.describe(spec)
    assert V.describe(dict(spec, send=True)).endswith("sends a picture")
    assert V.describe(dict(spec, compare=False, send=True)) == "sends a picture of the screen"
    assert "does nothing" in V.describe(dict(spec, compare=False))
    assert not V.compares(None) and V.describe(None) == "missing picture"
