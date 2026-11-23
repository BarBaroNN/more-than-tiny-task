"""Monitor layouts and mapping mouse positions between them.

Mouse positions are recorded in desktop pixels, which only make sense with
the monitor layout they were recorded on (on two screens, x = 3200 is on the
second one; on one screen it is off the edge). A macro therefore stores the
layout it was recorded with (``meta["monitors"]``, main screen first) and which
screen it was recorded on (``meta["record_on"]``: an index or ``"all"``).

Playback maps every position from the screen it was recorded on to the screen
it plays on (scaled to that screen's resolution), and never off-screen.

Rects are ``(left, top, width, height)`` in physical pixels.
"""
from __future__ import annotations

from dataclasses import replace
from typing import List, Optional, Sequence, Tuple

from .model import POSITIONAL, Event

Rect = Tuple[int, int, int, int]

RECORD_AUTO = "auto"    # the screen the mouse is on when recording starts
ALL = "all"             # all screens
PLAY_RECORDED = "recorded"  # the same screen(s) the macro was recorded on


def _rect(r) -> Rect:
    return int(r[0]), int(r[1]), int(r[2]), int(r[3])


def contains(r: Rect, x: int, y: int) -> bool:
    return r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]


def _distance2(r: Rect, x: int, y: int) -> int:
    dx = max(r[0] - x, 0, x - (r[0] + r[2] - 1))
    dy = max(r[1] - y, 0, y - (r[1] + r[3] - 1))
    return dx * dx + dy * dy


def monitor_at(monitors: Sequence[Rect], x: int, y: int) -> int:
    """Index of the monitor containing (x, y), else the nearest one."""
    for i, r in enumerate(monitors):
        if contains(r, x, y):
            return i
    return min(range(len(monitors)), key=lambda i: _distance2(monitors[i], x, y))


def clamp(r: Rect, x: int, y: int) -> Tuple[int, int]:
    return min(max(x, r[0]), r[0] + r[2] - 1), min(max(y, r[1]), r[1] + r[3] - 1)


def bounding_rect(monitors: Sequence[Rect]) -> Rect:
    left = min(r[0] for r in monitors)
    top = min(r[1] for r in monitors)
    right = max(r[0] + r[2] for r in monitors)
    bottom = max(r[1] + r[3] for r in monitors)
    return left, top, right - left, bottom - top


def describe(monitors: Sequence[Rect], index: int) -> str:
    r = monitors[index]
    return f"Screen {index + 1} · {r[2]}×{r[3]}" + (" (main)" if index == 0 else "")


def resolve_record_on(choice, monitors: Sequence[Rect], cursor: Tuple[int, int]):
    """What a recording made now is stored as: a screen index or ALL."""
    if choice == ALL:
        return ALL
    if isinstance(choice, int) and 0 <= choice < len(monitors):
        return choice
    return monitor_at(monitors, *cursor)


def layout_meta(monitors: Sequence[Rect], record_on) -> dict:
    """Macro metadata describing where a recording was made. ``screen`` is
    kept for older versions of the app (they rescale from it)."""
    mons = [list(_rect(r)) for r in monitors]
    ref = mons[record_on] if isinstance(record_on, int) else mons[0]
    return {"monitors": mons, "record_on": record_on, "screen": list(ref)}


def same_layout(a: Sequence[Rect], b: Sequence[Rect]) -> bool:
    return [_rect(r) for r in a] == [_rect(r) for r in b]


def remember_layout(known: list, layout: Sequence[Rect], limit: int = 12) -> list:
    """``known`` with ``layout`` moved / added to the front (most recent first)."""
    layout = [list(_rect(r)) for r in layout]
    rest = [k for k in (known or []) if not same_layout(k, layout)]
    return [layout] + rest[:limit - 1]


def _legacy_main(meta: dict) -> Optional[Rect]:
    s = meta.get("screen")
    return _rect(s) if s and len(s) == 4 else None


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def checkpoint_layout(meta: dict) -> Optional[List[Rect]]:
    """The screens an old macro's checkpoints were taken on, main screen
    first, or None if they show no screen besides the main one.

    Before layouts were stored, a checkpoint's picture was the whole monitor
    under the mouse and its ``image_rect`` was that monitor's rect in desktop
    pixels, so it tells the size and place of a screen that may be unplugged
    now. Rects that overlap the main screen or each other can't all be
    screens of one layout; the first one wins.
    """
    main = _legacy_main(meta)
    if main is None:
        return None
    found: List[Rect] = []
    for spec in (meta.get("checkpoints") or {}).values():
        r = spec.get("image_rect") if isinstance(spec, dict) else None
        if not r or len(r) != 4 or r[2] <= 0 or r[3] <= 0:
            continue
        r = _rect(r)
        if not _overlaps(r, main) and not any(_overlaps(r, f) for f in found):
            found.append(r)
    return [main] + sorted(found, key=lambda r: (r[0], r[1])) if found else None


def find_legacy_layout(meta: dict, events: Sequence[Event], layouts: Sequence[Sequence[Rect]]) -> Optional[dict]:
    """Layout metadata for a macro saved without it (before 0.0.4: only the
    main screen's rect), or None if it can't be told.

    Positions on the stored main screen keep meaning "the main screen". If
    they lie elsewhere, the layout they were recorded on is, in this order:
    the one its checkpoint pictures were taken on (see ``checkpoint_layout``),
    or one in ``layouts``: the first is the layout connected now (any of its
    screens may hold them), the others are layouts seen before (their main
    screen must match the stored one).
    """
    if meta.get("monitors"):
        return None
    pts = [(e.x, e.y) for e in events if e.kind in POSITIONAL]
    main = _legacy_main(meta)
    if not pts:
        return None
    if main and sum(contains(main, *p) for p in pts) * 2 > len(pts):
        return layout_meta([main], 0)
    candidates = [(layout, n > 0) for n, layout in enumerate(layouts)]
    from_checkpoints = checkpoint_layout(meta)
    if from_checkpoints:
        candidates.insert(0, (from_checkpoints, False))
    for layout, needs_same_main in candidates:
        mons = [_rect(r) for r in layout or []]
        if not mons or (needs_same_main and main and mons[0] != main):
            continue
        inside = [p for p in pts if any(contains(r, *p) for r in mons)]
        if len(inside) < 0.98 * len(pts):
            continue
        used = {monitor_at(mons, *p) for p in inside}
        return layout_meta(mons, used.pop() if len(used) == 1 else ALL)
    return None


class ScreenMap:
    """Maps positions from the layout a macro was recorded on to this one.

    ``play_on`` is PLAY_RECORDED (each recorded screen goes to the same screen
    now: identical rect, else same number, else the main screen) or a screen
    index (everything goes to that screen). ``scale`` rescales between
    resolutions; without it positions keep their offset from the screen's
    top-left corner. Positions that would land off every screen are clamped
    onto their target screen.
    """

    def __init__(self, meta: dict, current: Sequence[Rect], play_on=PLAY_RECORDED,
                 scale: bool = True, events: Sequence[Event] = (), known_layouts=()):
        self.current: List[Rect] = [_rect(r) for r in current] or [(0, 0, 1920, 1080)]
        self.play_on = play_on if isinstance(play_on, int) and 0 <= play_on < len(self.current) else PLAY_RECORDED
        self.scale = scale
        self.note = ""
        self.known_layouts = [[_rect(r) for r in layout if len(r) == 4] for layout in (known_layouts or []) if layout]
        if not meta.get("monitors"):
            # old macro: work out the layout it was recorded on, if we can
            found = find_legacy_layout(meta, events, [self.current] + list(known_layouts or []))
            if found:
                meta = found
        mons = meta.get("monitors")
        if mons:
            self.recorded: List[Rect] = [_rect(r) for r in mons]
            ref = meta.get("record_on", 0)
            self.record_on = ref if ref == ALL or (isinstance(ref, int) and 0 <= ref < len(self.recorded)) else 0
        else:
            self._init_legacy(meta, events)

    # -- macros saved before layouts were stored: only the main screen's rect
    def _init_legacy(self, meta: dict, events: Sequence[Event]) -> None:
        main = _legacy_main(meta) or self.current[0]
        self.recorded, self.record_on = [main], 0
        pts = [(e.x, e.y) for e in events if e.kind in POSITIONAL]
        outside = [p for p in pts if not contains(main, *p)]
        if not pts or len(outside) * 2 <= len(pts):
            return
        # Most of it happened on another screen. If one of today's screens
        # holds it, that is the one; otherwise centre it on the target screen.
        xs, ys = [p[0] for p in outside], [p[1] for p in outside]
        box = (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        for r in self.current:
            if contains(r, box[0], box[1]) and contains(r, box[0] + box[2] - 1, box[1] + box[3] - 1):
                self.recorded.append(r)
                break
        else:
            self.recorded.append(self._guess_neighbour(main, box))
            self.note = "recorded on a screen that isn't connected now"
        self.record_on = ALL

    def _guess_neighbour(self, main: Rect, box: Rect) -> Rect:
        """The unknown screen a legacy macro was recorded on. Windows puts extra
        screens next to the main one, edges aligned, so assume that. Its size:
        the screen it will be played on if the recording fits on it, else the
        smallest screen this PC has had that fits it (the mouse rarely reaches
        a screen's far corners, so the recording's own extent is too small),
        else the screen it plays on grown to fit."""
        target = (self.current[self.play_on] if isinstance(self.play_on, int)
                  else self.current[1] if len(self.current) > 1 else self.current[0])
        ml, mt, mr, mb = main[0], main[1], main[0] + main[2], main[1] + main[3]
        bl, bt, br, bb = box[0], box[1], box[0] + box[2], box[1] + box[3]
        right, below = bl >= mr, bt >= mb
        left_of, above = not right and br <= ml, not below and bb <= mt
        # room the recording needs on that screen, measured from its near edge
        need_w = br - mr if right else ml - bl if left_of else br - ml
        need_h = bb - mb if below else mt - bt if above else bb - mt
        known = {(r[2], r[3]) for layout in [self.current] + self.known_layouts for r in layout}
        sizes = [(target[2], target[3])] + sorted(known, key=lambda s: (s[0] * s[1], s))
        w, h = next(((w, h) for w, h in sizes if w >= need_w and h >= need_h),
                    (max(target[2], need_w), max(target[3], need_h)))
        left = mr if right else ml - w if left_of else ml
        top = mb if below else mt - h if above else mt
        return left, top, w, h

    # -- mapping
    def _target_index(self, src_index: int) -> int:
        if isinstance(self.play_on, int):
            return self.play_on
        src = self.recorded[src_index]
        for i, r in enumerate(self.current):
            if r == src:
                return i
        return src_index if src_index < len(self.current) else 0

    def _target_rect(self, src_index: int) -> Rect:
        return self.current[self._target_index(src_index)]

    def source_index(self, x: int, y: int) -> int:
        return self.record_on if isinstance(self.record_on, int) else monitor_at(self.recorded, x, y)

    def _transform(self, s: Rect, d: Rect, x: int, y: int) -> Tuple[int, int]:
        if self.scale and s[2] > 0 and s[3] > 0:
            return (int(round(d[0] + (x - s[0]) * d[2] / s[2])),
                    int(round(d[1] + (y - s[1]) * d[3] / s[3])))
        return d[0] + (x - s[0]), d[1] + (y - s[1])

    def point(self, x: int, y: int) -> Tuple[int, int]:
        i = self.source_index(x, y)
        d = self._target_rect(i)
        nx, ny = self._transform(self.recorded[i], d, x, y)
        if not any(contains(r, nx, ny) for r in self.current):
            nx, ny = clamp(d, nx, ny)
        return nx, ny

    def map_events(self, events: Sequence[Event]) -> List[Event]:
        out = []
        for e in events:
            if e.kind in POSITIONAL:
                x, y = self.point(e.x, e.y)
                e = replace(e, x=x, y=y)
            out.append(e)
        return out

    def _inverse_screens(self, x: int, y: int) -> Tuple[Rect, Rect]:
        """(screen now, recorded screen) for undoing the mapping at (x, y) now."""
        j = monitor_at(self.current, x, y)
        sources = [i for i in range(len(self.recorded)) if self._target_index(i) == j]
        if isinstance(self.record_on, int):
            i = self.record_on
        elif sources:
            i = sources[0]
        else:
            i = min(j, len(self.recorded) - 1)
        return self.current[j], self.recorded[i]

    def inverse_events(self, events: Sequence[Event]) -> List[Event]:
        """Positions recorded now (e.g. a re-recorded part) -> the macro's
        own coordinates, so they line up with the rest of it."""
        out = []
        for e in events:
            if e.kind in POSITIONAL:
                s, d = self._inverse_screens(e.x, e.y)
                x, y = self._transform(s, d, e.x, e.y)
                e = replace(e, x=x, y=y)
            out.append(e)
        return out

    def rect(self, r) -> Rect:
        """An area in the macro's coordinates (a screen checkpoint) -> on the
        screens now, moved and rescaled like the positions inside it. The
        screen is decided by its centre; an area that would land off every
        screen is moved onto its target screen."""
        x, y, w, h = _rect(r)
        i = self.source_index(x + w // 2, y + h // 2)
        s, d = self.recorded[i], self._target_rect(i)
        x0, y0 = self._transform(s, d, x, y)
        x1, y1 = self._transform(s, d, x + w, y + h)
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        if not any(contains(c, x0 + w // 2, y0 + h // 2) for c in self.current):
            x0 = min(max(x0, d[0]), d[0] + max(0, d[2] - w))
            y0 = min(max(y0, d[1]), d[1] + max(0, d[3] - h))
        return x0, y0, w, h

    def inverse_rect(self, r) -> Rect:
        """An area on the screens now (a screenshot just taken) -> the macro's
        own coordinates, the way ``inverse_events`` treats positions there."""
        x, y, w, h = _rect(r)
        s, d = self._inverse_screens(x + w // 2, y + h // 2)
        x0, y0 = self._transform(s, d, x, y)
        x1, y1 = self._transform(s, d, x + w, y + h)
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0)

    def is_identity(self) -> bool:
        return all(self._target_rect(i) == r for i, r in enumerate(self.recorded)) and not self.note

    def summary(self) -> str:
        """Short "recorded on → plays on" text for the status bar."""
        if isinstance(self.record_on, int):
            src = f"{describe(self.recorded, self.record_on)}"
            dst_i = self._target_index(self.record_on)
            dst = describe(self.current, dst_i)
        else:
            src = f"all {len(self.recorded)} screens" if len(self.recorded) > 1 else describe(self.recorded, 0)
            dst = (describe(self.current, self.play_on) if isinstance(self.play_on, int)
                   else f"{len(self.current)} screen{'s' if len(self.current) != 1 else ''}")
        if self.note:
            src = "a screen that isn't connected now"
        return f"Recorded on {src}  →  plays on {dst}"
