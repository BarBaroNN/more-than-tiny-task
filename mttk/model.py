"""Macro data model and pure editing operations.

A macro is an ordered list of immutable ``Event`` objects, each stamped with
``t`` = seconds since the start of the recording. All editing operations are
pure functions returning a new list, which keeps undo/redo trivial.
"""
from __future__ import annotations

import bisect
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Iterable, List, Optional, Sequence

FILE_VERSION = 1
FILE_EXTENSION = ".mttk"

# Event kinds
MOVE = "move"
CLICK = "click"
SCROLL = "scroll"
KEY = "key"
CHECK = "check"   # screen checkpoint: compare the screen with a saved picture

KINDS = (MOVE, CLICK, SCROLL, KEY, CHECK)

MODIFIER_KEY_NAMES = {
    "ctrl", "ctrl_l", "ctrl_r", "shift", "shift_l", "shift_r",
    "alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r",
}


@dataclass(frozen=True)
class Event:
    t: float
    kind: str
    x: int = 0
    y: int = 0
    # click
    button: str = ""
    pressed: bool = True
    # scroll
    dx: int = 0
    dy: int = 0
    # key: name for special keys (pynput Key.<name>), vk for virtual key code,
    # char for printable characters (used for display / fallback)
    key_name: str = ""
    vk: int = 0
    char: str = ""
    # key: hardware scan code and E0 "extended" flag as recorded (0 / False if
    # unknown); played back exactly so scan-code readers see the same key
    scan: int = 0
    ext: bool = False
    # check: id of the checkpoint (picture, area, settings) in meta["checkpoints"]
    ref: str = ""

    def shifted(self, dt: float) -> "Event":
        return replace(self, t=max(0.0, self.t + dt))

    def at(self, t: float) -> "Event":
        return replace(self, t=max(0.0, t))

    @property
    def key_id(self) -> str:
        """Stable identity of a key, used to pair presses with releases."""
        if self.key_name:
            return "n:" + self.key_name
        if self.vk:
            return f"v:{self.vk}"
        return "c:" + self.char

    @property
    def key_label(self) -> str:
        if self.key_name:
            return self.key_name.replace("_", " ").title()
        if self.char and self.char.isprintable() and self.char.strip():
            return self.char.upper() if len(self.char) == 1 else self.char
        if 65 <= self.vk <= 90 or 48 <= self.vk <= 57:
            return chr(self.vk)
        if 96 <= self.vk <= 105:
            return f"Num {self.vk - 96}"
        return f"VK {self.vk}" if self.vk else "?"

    @property
    def is_modifier(self) -> bool:
        return self.kind == KEY and self.key_name in MODIFIER_KEY_NAMES

    def describe(self) -> str:
        if self.kind == MOVE:
            return f"Move to ({self.x}, {self.y})"
        if self.kind == CLICK:
            return f"{self.button.title()} {'down' if self.pressed else 'up'} at ({self.x}, {self.y})"
        if self.kind == SCROLL:
            return f"Scroll ({self.dx}, {self.dy}) at ({self.x}, {self.y})"
        if self.kind == KEY:
            return f"Key {self.key_label} {'down' if self.pressed else 'up'}"
        if self.kind == CHECK:
            return "Screen checkpoint"
        return self.kind

    def to_dict(self) -> dict:
        d = {"t": round(self.t, 6), "kind": self.kind}
        defaults = Event(0.0, self.kind)
        for k, v in asdict(self).items():
            if k in ("t", "kind"):
                continue
            if v != getattr(defaults, k):
                d[k] = v
        return d

    @staticmethod
    def from_dict(d: dict) -> "Event":
        known = {f for f in Event.__dataclass_fields__}
        return Event(**{k: v for k, v in d.items() if k in known})


def sort_events(events: Iterable[Event]) -> List[Event]:
    # stable sort keeps original order for identical timestamps
    return sorted(events, key=lambda e: e.t)


def duration(events: Sequence[Event]) -> float:
    return events[-1].t if events else 0.0


def times(events: Sequence[Event]) -> List[float]:
    return [e.t for e in events]


def index_range(events: Sequence[Event], start: float, end: float) -> tuple:
    """Return (i, j) such that events[i:j] have start <= t < end."""
    ts = times(events)
    return bisect.bisect_left(ts, start), bisect.bisect_left(ts, end)


def _norm(start: float, end: float) -> tuple:
    a, b = sorted((max(0.0, start), max(0.0, end)))
    return a, b


# ---------------------------------------------------------------- range ops

def slice_range(events: Sequence[Event], start: float, end: float) -> List[Event]:
    """Events inside [start, end), re-based so the slice starts at t=0."""
    start, end = _norm(start, end)
    i, j = index_range(events, start, end)
    return [e.shifted(-start) for e in events[i:j]]


def delete_range(events: Sequence[Event], start: float, end: float, ripple: bool = True) -> List[Event]:
    """Remove events in [start, end). With ripple, later events move left."""
    start, end = _norm(start, end)
    i, j = index_range(events, start, end)
    gap = (end - start) if ripple else 0.0
    return list(events[:i]) + [e.shifted(-gap) for e in events[j:]]


def insert_at(events: Sequence[Event], t: float, clip: Sequence[Event], clip_length: Optional[float] = None) -> List[Event]:
    """Insert ``clip`` (t relative to 0) at time ``t`` and push later events right."""
    t = max(0.0, t)
    if clip_length is None:
        clip_length = duration(clip)
    ts = times(events)
    i = bisect.bisect_left(ts, t)
    return (list(events[:i])
            + [e.shifted(t) for e in clip]
            + [e.shifted(clip_length) for e in events[i:]])


def replace_range(events: Sequence[Event], start: float, end: float,
                  clip: Sequence[Event], clip_length: Optional[float] = None) -> List[Event]:
    """Replace [start, end) with ``clip``; later events shift by the length difference."""
    start, end = _norm(start, end)
    removed = delete_range(events, start, end, ripple=True)
    return insert_at(removed, start, clip, clip_length)


def insert_gap(events: Sequence[Event], t: float, length: float) -> List[Event]:
    return insert_at(events, t, [], max(0.0, length))


def trim_to(events: Sequence[Event], start: float, end: float) -> List[Event]:
    return slice_range(events, start, end)


def scale_range(events: Sequence[Event], start: float, end: float, factor: float) -> List[Event]:
    """Stretch (factor > 1) or compress (factor < 1) the timing inside a range."""
    start, end = _norm(start, end)
    factor = max(0.01, factor)
    i, j = index_range(events, start, end)
    new_len = (end - start) * factor
    delta = new_len - (end - start)
    middle = [e.at(start + (e.t - start) * factor) for e in events[i:j]]
    return list(events[:i]) + middle + [e.shifted(delta) for e in events[j:]]


def remove_kind_in_range(events: Sequence[Event], start: float, end: float, kind: str) -> List[Event]:
    start, end = _norm(start, end)
    return [e for e in events if not (start <= e.t < end and e.kind == kind)]


def thin_moves(events: Sequence[Event], min_interval: float) -> List[Event]:
    """Drop mouse moves closer than ``min_interval`` seconds to the previous kept move."""
    out: List[Event] = []
    last_move_t = -1e9
    for idx, e in enumerate(events):
        if e.kind == MOVE:
            nxt = events[idx + 1] if idx + 1 < len(events) else None
            # always keep the last move before a non-move event (exact click position)
            keep_anchor = nxt is None or nxt.kind != MOVE
            if e.t - last_move_t < min_interval and not keep_anchor:
                continue
            last_move_t = e.t
        out.append(e)
    return out


def shift_events(events: Sequence[Event], indices: Iterable[int], dt: float) -> List[Event]:
    idx = set(indices)
    out = [e.shifted(dt) if n in idx else e for n, e in enumerate(events)]
    return sort_events(out)


def set_event(events: Sequence[Event], index: int, new: Event) -> List[Event]:
    out = list(events)
    out[index] = new
    return sort_events(out)


def set_delay(events: Sequence[Event], index: int, delay: float) -> List[Event]:
    """Set the gap before event ``index``; all following events move with it."""
    if not 0 <= index < len(events):
        return list(events)
    prev_t = events[index - 1].t if index > 0 else 0.0
    dt = (prev_t + max(0.0, delay)) - events[index].t
    return list(events[:index]) + [e.shifted(dt) for e in events[index:]]


def add_event(events: Sequence[Event], new: Event) -> List[Event]:
    """Insert one event in time order, after any events at the same time.
    Nothing else moves (unlike ``insert_at``)."""
    i = bisect.bisect_right(times(events), new.t)
    return list(events[:i]) + [new] + list(events[i:])


def delete_indices(events: Sequence[Event], indices: Iterable[int]) -> List[Event]:
    idx = set(indices)
    return [e for n, e in enumerate(events) if n not in idx]


def cursor_position_at(events: Sequence[Event], t: float) -> Optional[tuple]:
    """Last known mouse position at or before time ``t``."""
    ts = times(events)
    i = bisect.bisect_right(ts, t)
    for e in reversed(events[:i]):
        if e.kind in (MOVE, CLICK, SCROLL):
            return e.x, e.y
    for e in events[i:]:
        if e.kind in (MOVE, CLICK, SCROLL):
            return e.x, e.y
    return None


def resume_from(events: Sequence[Event], t: float) -> List[Event]:
    """Events from ``t`` to the end, re-based to start at 0, ready to play.

    Playing only the tail would lose the state the macro is in at ``t``, so
    the result starts with a move to where the cursor was at that moment and
    re-presses any keys / mouse buttons still held down at ``t`` (pressed
    before it, released after it). Events exactly at ``t`` belong to the tail.
    """
    t = max(0.0, t)
    i = bisect.bisect_left(times(events), t)
    pos = None
    held = {}
    for e in events[:i]:
        if e.kind in POSITIONAL:
            pos = (e.x, e.y)
        if e.kind == KEY:
            if e.pressed:
                held[("k", e.key_id)] = e
            else:
                held.pop(("k", e.key_id), None)
        elif e.kind == CLICK:
            if e.pressed:
                held[("b", e.button)] = e
            else:
                held.pop(("b", e.button), None)
    head: List[Event] = []
    if pos:
        head.append(Event(0.0, MOVE, x=pos[0], y=pos[1]))
    for e in held.values():
        if e.kind == CLICK and pos:
            e = replace(e, x=pos[0], y=pos[1])
        head.append(e.at(0.0))
    return head + [e.shifted(-t) for e in events[i:]]


def sanitize_recording(events: Sequence[Event]) -> List[Event]:
    """Clean up the edges of a fresh recording.

    * drops key / button releases that have no matching press (e.g. the
      release of the hotkey or button that started the recording)
    * drops trailing modifier presses that were never released (e.g. the
      Ctrl of a Ctrl+F8 stop hotkey)
    """
    held_keys = set()
    held_buttons = set()
    out: List[Event] = []
    for e in events:
        if e.kind == KEY:
            if e.pressed:
                held_keys.add(e.key_id)
            elif e.key_id in held_keys:
                held_keys.discard(e.key_id)
            else:
                continue
        elif e.kind == CLICK:
            if e.pressed:
                held_buttons.add(e.button)
            elif e.button in held_buttons:
                held_buttons.discard(e.button)
            else:
                continue
        out.append(e)
    # drop dangling modifier presses at the end
    while held_keys:
        for n in range(len(out) - 1, -1, -1):
            e = out[n]
            if e.kind == KEY and e.pressed and e.key_id in held_keys and e.is_modifier:
                held_keys.discard(e.key_id)
                del out[n]
                break
        else:
            break
    return out


def rebase(events: Sequence[Event]) -> List[Event]:
    """Shift everything so the first event happens at t=0."""
    if not events:
        return []
    t0 = events[0].t
    return [e.shifted(-t0) for e in events]


# ------------------------------------------------- coordinate transforms

POSITIONAL = (MOVE, CLICK, SCROLL)


def first_position(events: Sequence[Event]) -> Optional[tuple]:
    for e in events:
        if e.kind in POSITIONAL:
            return e.x, e.y
    return None


def offset_events(events: Sequence[Event], dx: int, dy: int) -> List[Event]:
    """Move every mouse position by (dx, dy)."""
    if not dx and not dy:
        return list(events)
    return [replace(e, x=e.x + dx, y=e.y + dy) if e.kind in POSITIONAL else e for e in events]


def scale_events(events: Sequence[Event], src: Sequence[int], dst: Sequence[int]) -> List[Event]:
    """Map mouse positions from screen rect ``src`` to ``dst``.

    Rects are ``(left, top, width, height)`` in physical pixels, e.g. a macro
    recorded on 2560x1440 replayed on 1920x1080 is scaled by 0.75 on both axes.
    """
    if not src or not dst or tuple(src) == tuple(dst):
        return list(events)
    sl, st, sw, sh = src
    dl, dt_, dw, dh = dst
    if sw <= 0 or sh <= 0:
        return list(events)
    fx, fy = dw / sw, dh / sh

    def tx(x):
        return int(round(dl + (x - sl) * fx))

    def ty(y):
        return int(round(dt_ + (y - st) * fy))

    return [replace(e, x=tx(e.x), y=ty(e.y)) if e.kind in POSITIONAL else e for e in events]


# ------------------------------------------------------------ checkpoints
# A checkpoint is a CHECK event (so it moves with cut / insert / re-record
# like any other event) whose ``ref`` points at its data in
# meta["checkpoints"]: the reference picture, the area to compare and how
# strict to be. The data dicts are treated as immutable (undo keeps old meta).

def checkpoint_refs(events: Iterable[Event]) -> set:
    return {e.ref for e in events if e.kind == CHECK and e.ref}


def checkpoints_of(meta: dict) -> dict:
    cps = (meta or {}).get("checkpoints")
    return cps if isinstance(cps, dict) else {}


def with_checkpoints(meta: dict, updates: dict) -> dict:
    """New meta with ``updates`` (id -> data) merged into its checkpoints."""
    out = dict(meta or {})
    out["checkpoints"] = {**checkpoints_of(meta), **updates}
    return out


def prune_checkpoints(meta: dict, events: Sequence[Event]) -> dict:
    """New meta keeping only checkpoint data that some event still uses."""
    cps = checkpoints_of(meta)
    if not cps:
        out = dict(meta or {})
        out.pop("checkpoints", None)
        return out
    used = checkpoint_refs(events)
    out = dict(meta)
    kept = {k: v for k, v in cps.items() if k in used}
    if kept:
        out["checkpoints"] = kept
    else:
        out.pop("checkpoints", None)
    return out


# ---------------------------------------------------------------- file io

@dataclass
class Macro:
    events: List[Event] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def save(self, path: str) -> None:
        data = {
            "format": "more-than-tiny-task",
            "version": FILE_VERSION,
            "meta": prune_checkpoints(self.meta, self.events),
            "events": [e.to_dict() for e in self.events],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))

    @staticmethod
    def load(path: str) -> "Macro":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("format") != "more-than-tiny-task":
            raise ValueError("Not a More Than Tiny Task macro file")
        events = sort_events(Event.from_dict(d) for d in data.get("events", []))
        return Macro(events=events, meta=data.get("meta", {}))
