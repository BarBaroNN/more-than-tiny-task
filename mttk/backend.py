"""Global input: recording, playback, global hotkeys and mouse centring.

Everything here talks to the OS through ``pynput`` and runs on background
threads. Results are reported through plain callbacks; the UI layer turns
them into Qt signals.
"""
from __future__ import annotations

import collections
import math
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

from . import keys as K
from . import winutil
from .model import CLICK, KEY, MOVE, SCROLL, Event, sanitize_recording


def _injected(args) -> bool:
    """pynput >= 1.8 passes ``injected`` as the last callback argument."""
    return bool(args) and args[-1] is True


# ------------------------------------------------------------------ hotkeys

class HotkeyManager:
    """Listens globally for configurable hotkeys.

    On Windows, hotkeys that do something are swallowed system-wide so the
    focused app never sees them (F10 would otherwise open its menu bar, F8/F9
    would reach the app being recorded, ...). ``should_consume(action)`` decides
    per press whether to swallow; e.g. Esc is only swallowed while playing.
    """

    def __init__(self, on_trigger: Callable[[str], None],
                 should_consume: Optional[Callable[[str], bool]] = None):
        self._on_trigger = on_trigger
        self.should_consume = should_consume or (lambda action: True)
        self._bindings: Dict[tuple, str] = {}
        self._vk_bindings: Dict[tuple, str] = {}
        self._mods = set()
        self._vk_mods: Dict[int, str] = {}
        self._down_vks = set()
        self._swallowed = set()
        self._listener = None
        self.enabled = True

    def set_bindings(self, bindings: Dict[str, str]) -> None:
        """``bindings`` maps an action name to a hotkey string."""
        table, vk_table = {}, {}
        for action, text in bindings.items():
            mods, key = K.parse_hotkey(text)
            if key:
                table[(mods, key)] = action
                vk = K.name_to_vk(key)
                if vk:
                    vk_table[(mods, vk)] = action
        self._bindings = table
        self._vk_bindings = vk_table

    def keys_for(self, actions) -> set:
        """Key names bound to any of ``actions``."""
        return {key for (_m, key), a in self._bindings.items() if a in actions}

    def start(self) -> None:
        from pynput import keyboard
        kwargs = {}
        if winutil.IS_WINDOWS:
            kwargs["win32_event_filter"] = self._win32_filter
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release, **kwargs)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    # -- Windows: runs synchronously inside the low-level keyboard hook
    def _win32_filter(self, msg, data):
        if data.flags & 0x12:  # LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED
            return True
        vk = int(data.vkCode)
        down = msg in (0x0100, 0x0104)   # WM_KEYDOWN, WM_SYSKEYDOWN
        up = msg in (0x0101, 0x0105)     # WM_KEYUP, WM_SYSKEYUP
        mod = K.VK_MODIFIERS.get(vk)
        if mod:
            if down:
                self._vk_mods[vk] = mod
            elif up:
                self._vk_mods.pop(vk, None)
            return True
        if up:
            self._down_vks.discard(vk)
            if vk in self._swallowed:
                self._swallowed.discard(vk)
                self._listener.suppress_event()
            return True
        if not down or not self.enabled:
            return True
        action = self._vk_bindings.get((frozenset(self._vk_mods.values()), vk))
        if not action:
            return True
        first = vk not in self._down_vks   # ignore key auto-repeat
        self._down_vks.add(vk)
        consume = self.should_consume(action)
        if first:
            self._on_trigger(action)
        if consume:
            self._swallowed.add(vk)
            self._listener.suppress_event()
        return False  # handled here; don't run _press for it

    # -- other platforms (and modifier tracking)
    def _press(self, key, *args):
        if _injected(args):
            return
        name = K.pynput_key_name(key)
        mod = K.modifier_of(getattr(key, "name", "") or "")
        if mod:
            self._mods.add(mod)
            return
        if not self.enabled or winutil.IS_WINDOWS:
            return
        action = self._bindings.get((frozenset(self._mods), name))
        if action:
            self._on_trigger(action)

    def _release(self, key, *args):
        if _injected(args):
            return
        mod = K.modifier_of(getattr(key, "name", "") or "")
        if mod:
            self._mods.discard(mod)


# ----------------------------------------------------------------- recorder

class Recorder:
    def __init__(self, record_moves: bool = True, move_interval: float = 0.008,
                 record_keyboard: bool = True, record_mouse: bool = True,
                 ignore_key: Optional[Callable[[str], bool]] = None):
        self.record_moves = record_moves
        self.record_keyboard = record_keyboard
        self.record_mouse = record_mouse
        self.move_interval = move_interval
        self.ignore_key = ignore_key or (lambda name: False)
        self._events: List[Event] = []
        self._lock = threading.Lock()
        self._t0 = 0.0
        self._last_move = -1.0
        self._listeners = []
        self._hw_keys = collections.deque()  # (vk, scan, extended) from the hook, in order
        self.running = False

    def elapsed(self) -> float:
        return time.perf_counter() - self._t0 if self.running else 0.0

    def event_count(self) -> int:
        return len(self._events)

    def start(self) -> None:
        from pynput import keyboard, mouse
        self._events = []
        self._t0 = time.perf_counter()
        self._last_move = -1.0
        self.running = True
        if self.record_mouse:
            ml = mouse.Listener(on_move=self._move, on_click=self._click, on_scroll=self._scroll)
            ml.daemon = True
            ml.start()
            self._listeners.append(ml)
        if self.record_keyboard:
            self._hw_keys.clear()
            kwargs = {"win32_event_filter": self._win32_key_filter} if winutil.IS_WINDOWS else {}
            kl = keyboard.Listener(on_press=self._press, on_release=self._release, **kwargs)
            kl.daemon = True
            kl.start()
            self._listeners.append(kl)

    def stop(self, trim_rect=None) -> tuple:
        """Stop and return ``(events, length)``.

        ``trim_rect`` (left, top, right, bottom) removes trailing mouse
        activity inside our own window, i.e. the click on the Stop button.
        """
        length = self.elapsed()
        self.running = False
        for listener in self._listeners:
            listener.stop()
        self._listeners = []
        with self._lock:
            events = list(self._events)
        if trim_rect:
            left, top, right, bottom = trim_rect
            while events and events[-1].kind in (MOVE, CLICK, SCROLL) and \
                    left <= events[-1].x < right and top <= events[-1].y < bottom:
                events.pop()
            if events:
                length = min(length, events[-1].t + 0.05)
            else:
                length = 0.0
        events = sanitize_recording(events)
        return events, length

    def add_synthetic_move(self, x: int, y: int) -> None:
        """Used for the animated centring so it ends up in the recording."""
        if self.running and self.record_mouse:
            self._add(Event(self._now(), MOVE, x=int(x), y=int(y)))

    # -- internals
    def _now(self) -> float:
        return time.perf_counter() - self._t0

    def _add(self, e: Event) -> None:
        with self._lock:
            self._events.append(e)

    def _move(self, x, y, *args):
        if not self.running or _injected(args) or not self.record_moves:
            return
        t = self._now()
        if t - self._last_move < self.move_interval:
            return
        self._last_move = t
        self._add(Event(t, MOVE, x=int(x), y=int(y)))

    def _click(self, x, y, button, pressed, *args):
        if not self.running or _injected(args):
            return
        self._add(Event(self._now(), CLICK, x=int(x), y=int(y),
                        button=getattr(button, "name", str(button)), pressed=bool(pressed)))

    def _scroll(self, x, y, dx, dy, *args):
        if not self.running or _injected(args):
            return
        self._add(Event(self._now(), SCROLL, x=int(x), y=int(y), dx=int(dx), dy=int(dy)))

    def _win32_key_filter(self, msg, data):
        """Runs in the hook for every key; pynput calls ``_press`` / ``_release``
        for each one afterwards, in the same order. Remember what the keyboard
        actually sent (scan code, E0 flag) for real, non-injected keys."""
        if not data.flags & 0x12:  # LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED
            vk = int(data.vkCode)
            if vk == 0xE7:  # VK_PACKET: "scanCode" is a unicode character, not a key
                self._hw_keys.append((vk, 0, False))
            else:
                self._hw_keys.append((vk, int(data.scanCode) & 0xFF, bool(data.flags & 0x01)))
        return True

    def _press(self, key, *args):
        self._key(key, True, args)

    def _release(self, key, *args):
        self._key(key, False, args)

    def _hardware_info(self, key) -> tuple:
        """Scan code and extended flag of this (non-injected) key event."""
        if not winutil.IS_WINDOWS:
            return 0, False
        try:
            vk, scan, ext = self._hw_keys.popleft()
        except IndexError:
            return 0, False
        key_vk = getattr(key, "vk", None) or getattr(getattr(key, "value", None), "vk", None)
        if key_vk and key_vk != vk and K.GENERIC_MODIFIER_VK.get(key_vk) != K.GENERIC_MODIFIER_VK.get(vk, -1):
            self._hw_keys.clear()  # out of step: don't attach the wrong scan code
            return 0, False
        return scan, ext

    def _key(self, key, pressed, args):
        if _injected(args):
            return
        scan, ext = self._hardware_info(key)
        if not self.running or self.ignore_key(K.pynput_key_name(key)):
            return
        self._add(K.event_from_pynput(key, pressed, self._now(), scan, ext))


# ------------------------------------------------------------------- player

_EPS = 1e-6


def stop_position(elapsed: float, done_t: Optional[float], next_t: float) -> float:
    """Where in the clip playback stopped: the real elapsed time, kept strictly
    after the last played event (``done_t``) and strictly before the next one,
    so resuming from it neither repeats nor skips an event."""
    pos = elapsed
    if done_t is not None:
        pos = max(pos, done_t + _EPS)
    pos = min(pos, next_t - _EPS)
    return max(0.0, pos)


class Player:
    """Plays a list of events on a background thread.

    Callbacks (called from the worker thread):
      on_progress(run_index, total_runs, t_seconds)
      on_wait(seconds_remaining, next_run_index)
      on_finished(completed: bool)

    ``first_run`` (optional) is played instead of ``events`` on the first run
    only, e.g. to continue from the middle and then loop the whole macro.
    When stopped mid-run, a last ``on_progress`` reports the exact stop
    position: after every event already played and before the next one.
    """

    def __init__(self, events: Sequence[Event], repeats: int = 1, delay: float = 0.0,
                 speed: float = 1.0, release_on_stop: bool = True,
                 on_progress=None, on_wait=None, on_finished=None,
                 first_run: Optional[Sequence[Event]] = None):
        self.events = list(events)
        self.first_run = list(first_run) if first_run is not None else None
        self.repeats = max(0, int(repeats))  # 0 = forever
        self.delay = max(0.0, delay)
        self.speed = max(0.05, speed)
        self.release_on_stop = release_on_stop
        self.on_progress = on_progress or (lambda *a: None)
        self.on_wait = on_wait or (lambda *a: None)
        self.on_finished = on_finished or (lambda *a: None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._held_keys = {}
        self._held_buttons = set()
        self.win_keys = winutil.IS_WINDOWS  # send keys with keybd_event like TinyTask

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mttk-player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _wait_until(self, target: float) -> bool:
        while True:
            if self._stop.is_set():
                return False
            remaining = target - time.perf_counter()
            if remaining <= 0:
                return True
            if remaining > 0.003:
                self._stop.wait(min(remaining - 0.002, 0.05))
            # else: spin for sub-millisecond accuracy

    def _run(self) -> None:
        from pynput import keyboard, mouse
        self._mouse = mouse.Controller()
        self._kb = keyboard.Controller()
        completed = False
        try:
            with winutil.high_resolution_timer():
                run = 0
                while self.repeats == 0 or run < self.repeats:
                    run += 1
                    if not self._play_once(run):
                        break
                    self._release_all()
                    more = self.repeats == 0 or run < self.repeats
                    if more and self.delay > 0:
                        if not self._delay_countdown(run + 1):
                            break
                else:
                    completed = True
        finally:
            self._release_all()
            self.on_finished(completed)

    def _play_once(self, run: int) -> bool:
        events = self.first_run if (run == 1 and self.first_run is not None) else self.events
        start = time.perf_counter()
        last_report = 0.0
        self.on_progress(run, self.repeats, 0.0)
        done_t = None
        for e in events:
            target = start + e.t / self.speed
            if not self._wait_until(target):
                elapsed = (time.perf_counter() - start) * self.speed
                self.on_progress(run, self.repeats, stop_position(elapsed, done_t, e.t))
                return False
            try:
                self._do(e)
            except Exception:
                pass
            done_t = e.t
            now = time.perf_counter()
            if now - last_report > 0.04:
                last_report = now
                self.on_progress(run, self.repeats, e.t)
        if events:
            self.on_progress(run, self.repeats, events[-1].t)
        return not self._stop.is_set()

    def _delay_countdown(self, next_run: int) -> bool:
        end = time.perf_counter() + self.delay
        while True:
            remaining = end - time.perf_counter()
            if remaining <= 0:
                return True
            self.on_wait(remaining, next_run)
            if self._stop.wait(min(0.1, remaining)):
                return False

    def _do(self, e: Event) -> None:
        from pynput import mouse
        if e.kind == MOVE:
            self._mouse.position = (e.x, e.y)
        elif e.kind == CLICK:
            self._mouse.position = (e.x, e.y)
            button = getattr(mouse.Button, e.button, mouse.Button.left)
            if e.pressed:
                self._mouse.press(button)
                self._held_buttons.add(button)
            else:
                self._mouse.release(button)
                self._held_buttons.discard(button)
        elif e.kind == SCROLL:
            self._mouse.position = (e.x, e.y)
            self._mouse.scroll(e.dx, e.dy)
        elif e.kind == KEY:
            params = K.win_key_params(e, winutil.vk_to_scan, winutil.char_to_vk) if self.win_keys else None
            if params:
                winutil.keybd_event(*params)
                key = ("win", params[0], params[1], params[2] & ~winutil.KEYEVENTF_KEYUP)
            else:
                key = K.pynput_from_event(e)
                (self._kb.press if e.pressed else self._kb.release)(key)
            if e.pressed:
                self._held_keys[e.key_id] = key
            else:
                self._held_keys.pop(e.key_id, None)

    def _release_all(self) -> None:
        if not self.release_on_stop:
            return
        for key in list(self._held_keys.values()):
            try:
                if isinstance(key, tuple) and key[0] == "win":
                    _, vk, scan, flags = key
                    winutil.keybd_event(vk, scan, flags | winutil.KEYEVENTF_KEYUP)
                else:
                    self._kb.release(key)
            except Exception:
                pass
        self._held_keys.clear()
        for button in list(self._held_buttons):
            try:
                self._mouse.release(button)
            except Exception:
                pass
        self._held_buttons.clear()


# ------------------------------------------------------------ mouse centring

EASINGS = {
    "Smooth (ease in-out)": lambda p: 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2,
    "Ease out": lambda p: 1 - (1 - p) ** 3,
    "Linear": lambda p: p,
    "Instant": None,
}


def animate_mouse_to_center(target: str = "cursor", duration_ms: int = 350,
                            easing: str = "Smooth (ease in-out)",
                            on_step: Optional[Callable[[int, int], None]] = None) -> threading.Thread:
    """Glide the cursor to the screen centre on a background thread."""

    def run():
        from pynput.mouse import Controller
        m = Controller()
        x0, y0 = winutil.cursor_pos()
        x1, y1 = winutil.screen_center(target)
        ease = EASINGS.get(easing, EASINGS["Smooth (ease in-out)"])
        dur = max(0.0, duration_ms / 1000.0)
        dist = math.hypot(x1 - x0, y1 - y0)
        if ease is None or dur <= 0 or dist < 2:
            m.position = (x1, y1)
            if on_step:
                on_step(x1, y1)
            return
        with winutil.high_resolution_timer():
            start = time.perf_counter()
            while True:
                p = min(1.0, (time.perf_counter() - start) / dur)
                k = ease(p)
                x = round(x0 + (x1 - x0) * k)
                y = round(y0 + (y1 - y0) * k)
                m.position = (x, y)
                if on_step:
                    on_step(x, y)
                if p >= 1.0:
                    break
                time.sleep(0.008)

    th = threading.Thread(target=run, name="mttk-center", daemon=True)
    th.start()
    return th
