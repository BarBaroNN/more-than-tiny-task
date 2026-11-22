"""Screen checkpoints: does part of the screen still look like a saved picture?

Both pictures are shrunk to a small grey thumbnail (at most ``WORK_SIZE``
pixels on the long side). Shrinking averages blocks of pixels, which smooths
away noise and tiny differences. The thumbnails are then compared pixel by
pixel, and the score is the share of the area that still looks the same: a
popup covering 10% of the area scores about 90%, a view that drifted off to
one side scores far lower.

The comparison works on plain bytes so it can be tested without a screen;
Qt's ``QImage`` (safe to use from any thread) does decoding and scaling.
"""
from __future__ import annotations

import base64
import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

WORK_SIZE = 128          # long side of the comparison thumbnail, in pixels
PIXEL_TOLERANCE = 24     # 0-255: how far a thumbnail pixel may be off and still count as the same
STORE_MAX_SIDE = 1280    # reference screenshots are stored at most this big (keeps .mttk files small)
CHECK_INTERVAL = 0.25    # seconds between tries while waiting for the screen to match

DEFAULT_THRESHOLD = 80   # % of the area that must look the same
DEFAULT_TIMEOUT = 5.0    # seconds to keep trying (lag) before giving up
DEFAULT_SHIFT = 0        # pixels the picture may be off by and still pass


# ------------------------------------------------------------- comparison

@dataclass(frozen=True)
class Gray:
    """An 8-bit grey image: ``w * h`` bytes, row by row."""
    data: bytes
    w: int
    h: int


def similarity(a: bytes, b: bytes, tol: int = PIXEL_TOLERANCE) -> float:
    """Share (0..1) of pixels of two same-size grey images that differ by at most ``tol``."""
    if not a or len(a) != len(b):
        return 0.0
    same = sum(1 for x, y in zip(a, b) if -tol <= x - y <= tol)
    return same / len(a)


def window(area: Gray, ox: int, oy: int, w: int, h: int) -> bytes:
    """The ``w`` x ``h`` block of ``area`` whose top-left corner is (ox, oy)."""
    if ox == 0 and w == area.w:
        return area.data[oy * w:(oy + h) * w]
    aw, d = area.w, area.data
    return b"".join(d[(oy + r) * aw + ox:(oy + r) * aw + ox + w] for r in range(h))


def _axis(limit: int, step: int = 1) -> List[int]:
    out = [0]
    for k in range(step, limit + 1, step):
        out += [k, -k]
    return out


def best_match(ref: Gray, area: Gray, margin: int, max_shift: int = 0, tol: int = PIXEL_TOLERANCE,
               good_enough: float = 2.0, step: int = 1) -> Tuple[float, int, int]:
    """Where does ``ref`` fit best inside ``area``?

    ``area`` is the spot where ``ref`` is expected plus ``margin`` pixels on
    every side. Shifts up to ``max_shift`` pixels are tried, smallest first,
    stopping early at a score of ``good_enough``. Returns ``(score, dx, dy)``,
    the shift being relative to the expected spot.
    """
    max_shift = max(0, min(max_shift, margin))
    axis = _axis(max_shift, step)
    offsets = sorted(((dx, dy) for dy in axis for dx in axis), key=lambda d: (d[0] ** 2 + d[1] ** 2, d))
    best = (-1.0, 0, 0)
    for dx, dy in offsets:
        s = similarity(ref.data, window(area, margin + dx, margin + dy, ref.w, ref.h), tol)
        if s > best[0]:
            best = (s, dx, dy)
            if s >= good_enough:
                break
    return best


def find_shift(ref: Gray, area: Gray, margin: int, tol: int = PIXEL_TOLERANCE,
               every: int = 4, keep: int = 6) -> Tuple[float, int, int]:
    """Wide search over every shift within the margin for where the picture
    went (e.g. the view drifted). Each shift is first scored on every
    ``every``-th row only, then the ``keep`` best are scored in full.
    Returns ``(score, dx, dy)``."""
    rows = range(0, ref.h, every)
    ref_rows = b"".join(ref.data[r * ref.w:(r + 1) * ref.w] for r in rows)
    aw, d = area.w, area.data
    ranked = []
    for dy in range(-margin, margin + 1):
        for dx in range(-margin, margin + 1):
            ox, oy = margin + dx, margin + dy
            sample = b"".join(d[(oy + r) * aw + ox:(oy + r) * aw + ox + ref.w] for r in rows)
            ranked.append((similarity(ref_rows, sample, tol), -(dx * dx + dy * dy), dx, dy))
    ranked.sort(reverse=True)
    best = (-1.0, 0, 0)
    for _s, _d, dx, dy in ranked[:keep]:
        s = similarity(ref.data, window(area, margin + dx, margin + dy, ref.w, ref.h), tol)
        if s > best[0]:
            best = (s, dx, dy)
    return best


def work_size(w: int, h: int, limit: int = WORK_SIZE) -> Tuple[int, int]:
    """Thumbnail size for a ``w`` x ``h`` picture (never enlarged)."""
    f = min(1.0, limit / max(1, w, h))
    return max(1, round(w * f)), max(1, round(h * f))


# --------------------------------------------------------------- geometry

def scale_rect(rect: Sequence[int], src: Optional[Sequence[int]], dst: Optional[Sequence[int]]) -> List[int]:
    """Map ``(x, y, w, h)`` from screen rect ``src`` to ``dst`` (like ``model.scale_events``)."""
    x, y, w, h = (int(v) for v in rect)
    if not src or not dst or tuple(src) == tuple(dst) or src[2] <= 0 or src[3] <= 0:
        return [x, y, w, h]
    sl, st, sw, sh = src
    dl, dt, dw, dh = dst
    fx, fy = dw / sw, dh / sh
    return [round(dl + (x - sl) * fx), round(dt + (y - st) * fy), max(1, round(w * fx)), max(1, round(h * fy))]


def clamp_region(region: Sequence[int], bounds: Sequence[int]) -> List[int]:
    """Keep ``region`` inside ``bounds`` (both ``x, y, w, h``), at least 4x4."""
    bx, by, bw, bh = bounds
    x, y, w, h = region
    x = min(max(x, bx), bx + bw - 4)
    y = min(max(y, by), by + bh - 4)
    w = max(4, min(w, bx + bw - x))
    h = max(4, min(h, by + bh - y))
    return [int(x), int(y), int(w), int(h)]


# ------------------------------------------------------------- Qt images

def to_gray(img, w: int, h: int) -> Gray:
    """Shrink a QImage to ``w`` x ``h`` (averaging) and make it grey."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    small = img.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_Grayscale8)
    bpl = small.bytesPerLine()
    raw = bytes(small.constBits())
    if bpl == w:
        return Gray(raw[:w * h], w, h)
    return Gray(b"".join(raw[r * bpl:r * bpl + w] for r in range(h)), w, h)


def qimage_from_bgra(data: bytes, w: int, h: int):
    from PySide6.QtGui import QImage
    return QImage(data, w, h, w * 4, QImage.Format_RGB32).copy()


def grab(rect: Sequence[int]):
    """Screenshot of ``(x, y, w, h)`` as a QImage, or None if unavailable."""
    from . import winutil
    res = winutil.grab_screen(*(int(v) for v in rect))
    return qimage_from_bgra(*res) if res else None


def encode_image(img, max_side: int = STORE_MAX_SIDE) -> str:
    """QImage -> base64 JPEG (PNG if JPEG isn't available), shrunk to ``max_side``."""
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    if max(img.width(), img.height()) > max_side:
        img = img.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    for fmt, quality in (("JPG", 90), ("PNG", -1)):
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        if img.save(buf, fmt, quality):
            return base64.b64encode(bytes(buf.data().data())).decode("ascii")
    raise RuntimeError("could not encode the screenshot")


def image_bytes(img, max_side: int = STORE_MAX_SIDE) -> bytes:
    return base64.b64decode(encode_image(img, max_side)) if img is not None else b""


def decode_image(text: str):
    from PySide6.QtGui import QImage
    img = QImage()
    try:
        img.loadFromData(base64.b64decode(text or ""))
    except Exception:
        pass
    return img


# ------------------------------------------------------- checkpoint data
# Stored in macro meta["checkpoints"][id] (see model.py):
#   name         label shown on the timeline and in alerts
#   image        base64 JPEG of the whole monitor at the moment it was taken
#   image_rect   [x, y, w, h] of the desktop that picture shows (physical px)
#   screen       primary screen rect when it was taken (for other resolutions)
#   region       [x, y, w, h] of the area to compare, same coordinates as image_rect
#   threshold    % of the area that must look the same
#   timeout      seconds to keep retrying before it counts as failed
#   shift        pixels the picture may be off by and still pass

def new_id() -> str:
    return secrets.token_hex(4)


def new_checkpoint(img, image_rect, screen, name: str, threshold: int = DEFAULT_THRESHOLD,
                   timeout: float = DEFAULT_TIMEOUT, shift: int = DEFAULT_SHIFT) -> dict:
    return {
        "name": name,
        "image": encode_image(img),
        "image_rect": [int(v) for v in image_rect],
        "screen": [int(v) for v in screen] if screen else list(image_rect),
        "region": [int(v) for v in image_rect],
        "threshold": int(threshold),
        "timeout": float(timeout),
        "shift": int(shift),
    }


def region_in_image(spec: dict, img_w: int, img_h: int) -> Tuple[int, int, int, int]:
    """The checkpoint area in pixels of its stored picture."""
    ix, iy, iw, ih = spec["image_rect"]
    rx, ry, rw, rh = spec.get("region") or spec["image_rect"]
    fx, fy = img_w / max(1, iw), img_h / max(1, ih)
    x0 = max(0, min(img_w - 1, round((rx - ix) * fx)))
    y0 = max(0, min(img_h - 1, round((ry - iy) * fy)))
    x1 = max(x0 + 1, min(img_w, round((rx - ix + rw) * fx)))
    y1 = max(y0 + 1, min(img_h, round((ry - iy + rh) * fy)))
    return x0, y0, x1 - x0, y1 - y0


def reference_crop(spec: dict):
    """The part of the stored picture that is compared (a QImage)."""
    img = decode_image(spec.get("image", ""))
    if img.isNull():
        return img
    return img.copy(*region_in_image(spec, img.width(), img.height()))


def describe(spec: Optional[dict]) -> str:
    if not spec:
        return "missing picture"
    txt = f"≥{spec.get('threshold', DEFAULT_THRESHOLD)}% match, wait up to {spec.get('timeout', DEFAULT_TIMEOUT):g}s"
    if spec.get("shift"):
        txt += f", ±{spec['shift']}px"
    return txt


# ------------------------------------------------------------ the runner

@dataclass
class CheckResult:
    ref: str
    name: str
    passed: bool
    score: float                 # best score seen, 0..1
    threshold: float             # needed, 0..1
    shift: Tuple[int, int] = (0, 0)   # where it matched (screen px), within the allowed shift
    run: int = 0
    t: float = 0.0               # time of the checkpoint in the macro
    waited: float = 0.0          # seconds spent checking
    error: str = ""
    looks_shifted: Optional[Tuple[int, int, float]] = None  # failed: (dx, dy, score) found further away
    expected: object = None      # QImage: the reference area
    actual: object = None        # QImage: the same area on screen now
    screenshot: object = None    # QImage: the whole monitor with the area marked (failures)

    @property
    def summary(self) -> str:
        if self.error:
            return self.error
        txt = f"{self.score * 100:.0f}% match (needs {self.threshold * 100:.0f}%)"
        if self.looks_shifted:
            dx, dy, _s = self.looks_shifted
            txt += f", the screen looks shifted by {_shift_words(dx, dy)}"
        return txt


def _shift_words(dx: int, dy: int) -> str:
    parts = []
    if dx:
        parts.append(f"{abs(dx)}px {'right' if dx > 0 else 'left'}")
    if dy:
        parts.append(f"{abs(dy)}px {'down' if dy > 0 else 'up'}")
    return " and ".join(parts) or "0px"


@dataclass
class _Prepared:
    spec: dict
    gray: Gray
    rect: List[int]          # area on this screen (physical px)
    fx: float                # screen px -> thumbnail px
    fy: float
    allowed: int             # allowed shift, thumbnail px
    margin: int              # extra thumbnail px captured around the area (wide search)
    crop: object             # QImage of the reference area


class CheckpointRunner:
    """Checks checkpoints during playback (called from the player thread).

    ``to_screen(rect, spec_screen)`` maps a checkpoint area to where it is on
    this screen (resolution change, "start from current cursor"), the same
    way mouse positions are mapped. ``capture(rect)`` returns a QImage.
    """

    def __init__(self, checkpoints: Dict[str, dict], to_screen: Callable = None, capture: Callable = None,
                 on_status: Callable = None, on_result: Callable = None, interval: float = CHECK_INTERVAL):
        self.specs = dict(checkpoints or {})
        self.to_screen = to_screen or (lambda rect, screen: list(rect))
        self.capture = capture or grab
        self.on_status = on_status or (lambda *a: None)
        self.on_result = on_result or (lambda r: None)
        self.interval = interval
        self.cancel = threading.Event()   # set to stop waiting (the player's stop event)
        self._prepared: Dict[str, _Prepared] = {}

    def prepare(self, refs: Iterable[str]) -> List[str]:
        """Build the reference thumbnails up front. Returns refs with problems."""
        bad = []
        for ref in set(refs):
            spec = self.specs.get(ref)
            p = self._prepare_one(spec) if spec else None
            if p:
                self._prepared[ref] = p
            else:
                bad.append(ref)
        return bad

    def _prepare_one(self, spec: dict) -> Optional[_Prepared]:
        try:
            crop = reference_crop(spec)
        except Exception:
            return None
        if crop.isNull():
            return None
        region = spec.get("region") or spec["image_rect"]
        rect = self.to_screen(region, spec.get("screen"))
        gw, gh = work_size(crop.width(), crop.height())
        gray = to_gray(crop, gw, gh)
        fx, fy = gw / max(1, rect[2]), gh / max(1, rect[3])
        allowed = math.ceil(max(0, spec.get("shift", 0)) * gw / max(1, region[2]))
        margin = max(allowed, 2, round(0.25 * max(gw, gh)))
        return _Prepared(spec, gray, list(rect), fx, fy, allowed, margin, crop)

    def _grab_area(self, p: _Prepared):
        mx, my = round(p.margin / p.fx), round(p.margin / p.fy)
        x, y, w, h = p.rect
        return self.capture([x - mx, y - my, w + 2 * mx, h + 2 * my]), mx, my

    def check(self, ref: str, run: int = 0, t: float = 0.0) -> bool:
        """True to carry on (matched, cancelled, or no picture), False to stop."""
        p = self._prepared.get(ref)
        if p is None:
            return True
        spec = p.spec
        name = spec.get("name") or "Checkpoint"
        threshold = max(0.0, min(1.0, spec.get("threshold", DEFAULT_THRESHOLD) / 100.0))
        start = time.perf_counter()
        deadline = start + max(0.0, float(spec.get("timeout", DEFAULT_TIMEOUT)))
        best = (-1.0, 0, 0)
        area_img = area = None
        mx = my = 0
        error = ""
        while True:
            area_img, mx, my = self._grab_area(p)
            if area_img is None or area_img.isNull():
                error = "Couldn't take a screenshot to check"
                break
            area = to_gray(area_img, p.gray.w + 2 * p.margin, p.gray.h + 2 * p.margin)
            score, dx, dy = best_match(p.gray, area, p.margin, p.allowed, good_enough=threshold)
            if score > best[0]:
                best = (score, dx, dy)
            if score >= threshold:
                self.on_result(CheckResult(ref, name, True, score, threshold, self._to_px(p, dx, dy), run, t,
                                           time.perf_counter() - start))
                return True
            if self.cancel.is_set():
                return True
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            self.on_status(name, score, threshold, remaining)
            if self.cancel.wait(min(self.interval, remaining)):
                return True

        res = CheckResult(ref, name, False, max(0.0, best[0]), threshold, self._to_px(p, best[1], best[2]),
                          run, t, time.perf_counter() - start, error=error, expected=p.crop)
        if area is not None:
            try:
                s, dx, dy = find_shift(p.gray, area, p.margin)
                if s >= threshold and (dx or dy):
                    sx, sy = self._to_px(p, dx, dy)
                    res.looks_shifted = (sx, sy, s)
            except Exception:
                pass
            res.actual = area_img.copy(mx, my, p.rect[2], p.rect[3])
        res.screenshot = self._marked_screenshot(p)
        self.on_result(res)
        return False

    @staticmethod
    def _to_px(p: _Prepared, dx: int, dy: int) -> Tuple[int, int]:
        return round(dx / p.fx), round(dy / p.fy)

    def _marked_screenshot(self, p: _Prepared):
        """The whole monitor right now, with the checked area outlined in red."""
        try:
            from PySide6.QtGui import QColor, QPainter, QPen
            from . import winutil
            x, y, w, h = p.rect
            mon = winutil.monitor_rect_at(x + w // 2, y + h // 2) or p.rect
            img = self.capture(mon)
            if img is None or img.isNull():
                return None
            painter = QPainter(img)
            pen = QPen(QColor(255, 60, 80), max(3, mon[2] // 400))
            painter.setPen(pen)
            painter.drawRect(x - mon[0], y - mon[1], w, h)
            painter.end()
            return img
        except Exception:
            return None
