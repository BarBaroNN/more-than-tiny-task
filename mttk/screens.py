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
                 scale: bool = True, events: Sequence[Event] = ()):
        self.current: List[Rect] = [_rect(r) for r in current] or [(0, 0, 1920, 1080)]
        self.play_on = play_on if isinstance(play_on, int) and 0 <= play_on < len(self.current) else PLAY_RECORDED
        self.scale = scale
        self.note = ""
        mons = meta.get("monitors")
        if mons:
            self.recorded: List[Rect] = [_rect(r) for r in mons]
            ref = meta.get("record_on", 0)
            self.record_on = ref if ref == ALL or (isinstance(ref, int) and 0 <= ref < len(self.recorded)) else 0
        else:
            self._init_legacy(meta, events)

    # -- macros saved before layouts were stored: only the main screen's rect
    def _init_legacy(self, meta: dict, events: Sequence[Event]) -> None:
        main = _rect(meta["screen"]) if meta.get("screen") and len(meta["screen"]) == 4 else self.current[0]
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
        """The unknown screen a legacy macro was recorded on: Windows puts extra
        screens next to the main one, edges aligned, so assume that, with the
        size of the screen it will be played on (grown to fit the recording)."""
        target = (self.current[self.play_on] if isinstance(self.play_on, int)
                  else self.current[1] if len(self.current) > 1 else self.current[0])
        w, h = target[2], target[3]
        ml, mt, mr, mb = main[0], main[1], main[0] + main[2], main[1] + main[3]
        bl, bt, br, bb = box[0], box[1], box[0] + box[2], box[1] + box[3]
        if bl >= mr:
            left = mr
        elif br <= ml:
            left = ml - max(w, ml - bl)
        else:
            left = ml
        if bt >= mb:
            top = mb
        elif bb <= mt:
            top = mt - max(h, mt - bt)
        else:
            top = mt
        w = max(w, br - left)
        h = max(h, bb - top)
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

    def inverse_events(self, events: Sequence[Event]) -> List[Event]:
        """Positions recorded now (e.g. a re-recorded part) -> the macro's
        own coordinates, so they line up with the rest of it."""
        out = []
        for e in events:
            if e.kind in POSITIONAL:
                j = monitor_at(self.current, e.x, e.y)
                sources = [i for i in range(len(self.recorded)) if self._target_index(i) == j]
                if isinstance(self.record_on, int):
                    i = self.record_on
                elif sources:
                    i = sources[0]
                else:
                    i = min(j, len(self.recorded) - 1)
                s, d = self.current[j], self.recorded[i]
                x, y = self._transform(s, d, e.x, e.y)
                e = replace(e, x=x, y=y)
            out.append(e)
        return out

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
