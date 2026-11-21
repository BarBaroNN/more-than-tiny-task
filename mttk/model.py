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

KINDS = (MOVE, CLICK, SCROLL, KEY)

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


# ---------------------------------------------------------------- file io

@dataclass
class Macro:
    events: List[Event] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def save(self, path: str) -> None:
        data = {
            "format": "more-than-tiny-task",
            "version": FILE_VERSION,
            "meta": self.meta,
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
