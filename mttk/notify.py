"""Phone alerts through ntfy, a Telegram bot or a Discord webhook.

Only the standard library is used. ``build_request`` is pure (and tested);
``send`` does the network call, ``send_async`` does it on a thread and
``Outbox`` sends a stream of messages (progress logs) in order, waiting out
rate limits.
"""
from __future__ import annotations

import base64
import collections
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Union

from . import __version__

SERVICES = {
    "none": "Off",
    "ntfy": "ntfy app (free, no account)",
    "telegram": "Telegram bot",
    "discord": "Discord webhook",
}

NTFY_DEFAULT_SERVER = "https://ntfy.sh"
USER_AGENT = f"MoreThanTinyTask/{__version__}"
TIMEOUT = 20


class NotifyError(Exception):
    def __init__(self, text: str = "", retry_after: float = 0.0):
        super().__init__(text)
        self.retry_after = retry_after   # > 0: rate limited, try again after this many seconds


@dataclass
class Request:
    url: str
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def random_topic() -> str:
    """A hard-to-guess ntfy topic (anyone who knows a topic name can read it)."""
    return "mttk-" + secrets.token_hex(8)


def config_from_settings(s) -> dict:
    return {
        "service": getattr(s, "notify_service", "none"),
        "ntfy_server": getattr(s, "ntfy_server", "") or NTFY_DEFAULT_SERVER,
        "ntfy_topic": getattr(s, "ntfy_topic", ""),
        "ntfy_token": getattr(s, "ntfy_token", ""),
        "telegram_token": getattr(s, "telegram_token", ""),
        "telegram_chat_id": getattr(s, "telegram_chat_id", ""),
        "discord_webhook": getattr(s, "discord_webhook", ""),
        "discord_mention": getattr(s, "notify_mention", ""),
    }


def problem(cfg: dict) -> str:
    """What's missing for ``cfg`` to work ('' when it's ready, or when off)."""
    svc = cfg.get("service", "none")
    if svc == "ntfy":
        topic = (cfg.get("ntfy_topic") or "").strip()
        if not topic:
            return "Enter an ntfy topic"
        if not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", topic):
            return "The ntfy topic may only use letters, digits, - and _ (up to 64)"
    elif svc == "telegram":
        if not (cfg.get("telegram_token") or "").strip():
            return "Enter the Telegram bot token"
        if not str(cfg.get("telegram_chat_id") or "").strip():
            return "Enter your Telegram chat ID"
    elif svc == "discord":
        if not (cfg.get("discord_webhook") or "").strip().startswith("https://"):
            return "Enter the Discord webhook URL (https://…)"
    return ""


def enabled(cfg: dict) -> bool:
    return cfg.get("service", "none") in SERVICES and cfg.get("service") != "none" and not problem(cfg)


# ---------------------------------------------------------------- mentions

def discord_mention(text: str) -> Tuple[str, dict]:
    """Who a Discord alert pings: ``(text to put in front, allowed_mentions)``.

    ``text`` lists any of ``@everyone``, ``@here``, a user ID (``123...`` or
    ``<@123...>``) and a role (``&456...`` or ``<@&456...>``), separated by spaces
    or commas. Anything else is ignored. Nothing is pinged unless it's listed.
    """
    parts: List[str] = []
    allowed: dict = {"parse": []}
    for tok in re.split(r"[\s,;]+", (text or "").strip()):
        low = tok.lower().lstrip("@")
        if low in ("everyone", "here"):
            mention, kind, ident = "@" + low, "everyone", ""
        else:
            m = re.fullmatch(r"<@(!|&)?(\d{5,25})>|(&)?(\d{5,25})", tok)
            if not m:
                continue
            role = m.group(1) == "&" or bool(m.group(3))
            ident = m.group(2) or m.group(4)
            kind = "roles" if role else "users"
            mention = f"<@&{ident}>" if role else f"<@{ident}>"
        if mention in parts:
            continue
        parts.append(mention)
        if kind == "everyone":
            if "everyone" not in allowed["parse"]:
                allowed["parse"].append("everyone")
        else:
            allowed.setdefault(kind, []).append(ident)
    return " ".join(parts), allowed


def mention_problem(text: str) -> str:
    """Words in a mention setting that wouldn't ping anyone ('' when fine)."""
    bad = [tok for tok in re.split(r"[\s,;]+", (text or "").strip())
           if tok and not discord_mention(tok)[0]]
    if bad:
        return (f"Not understood: {', '.join(bad)}. Use @everyone, @here, a user ID "
                "(digits) or a role ID starting with &")
    return ""


# ---------------------------------------------------------------- requests

def _header_value(text: str) -> str:
    """HTTP headers are ASCII-only: anything else is sent RFC 2047 encoded
    (ntfy decodes it), which also covers line breaks in the message."""
    if text.isascii() and text.isprintable():
        return text
    return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def _multipart(fields: Dict[str, str], files) -> Tuple[bytes, str]:
    boundary = "mttk" + secrets.token_hex(16)
    out = bytearray()
    for name, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n").encode()
        out += str(value).encode("utf-8") + b"\r\n"
    for name, filename, ctype, data in files:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


NTFY_PRIORITY = {"low": "2", "default": "3", "high": "4", "urgent": "5"}


def build_request(cfg: dict, title: str, message: str, image: bytes = b"",
                  priority: str = "high", tags: str = "warning", ping: bool = False) -> Request:
    """``ping`` makes a Discord message mention ``cfg["discord_mention"]`` (used
    for errors, so they stand out from the progress log)."""
    svc = cfg.get("service", "none")
    err = problem(cfg)
    if svc == "none" or svc not in SERVICES:
        raise NotifyError("Phone alerts are off")
    if err:
        raise NotifyError(err)

    if svc == "ntfy":
        server = (cfg.get("ntfy_server") or NTFY_DEFAULT_SERVER).strip().rstrip("/")
        url = f"{server}/{cfg['ntfy_topic'].strip()}"
        headers = {"X-Title": _header_value(title), "X-Priority": NTFY_PRIORITY.get(priority, "3")}
        if tags:
            headers["X-Tags"] = tags
        token = (cfg.get("ntfy_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if image:
            # the picture is the body, so the text goes in a header
            headers["X-Message"] = _header_value(message)
            headers["X-Filename"] = "screen.jpg"
            return Request(url, "PUT", headers, image)
        headers["Content-Type"] = "text/plain; charset=utf-8"
        return Request(url, "POST", headers, message.encode("utf-8"))

    if svc == "telegram":
        base = f"https://api.telegram.org/bot{cfg['telegram_token'].strip()}"
        chat = str(cfg["telegram_chat_id"]).strip()
        text = f"{title}\n{message}"
        if image:
            body, ctype = _multipart({"chat_id": chat, "caption": text[:1024]},
                                     [("photo", "screen.jpg", "image/jpeg", image)])
            return Request(base + "/sendPhoto", "POST", {"Content-Type": ctype}, body)
        body = json.dumps({"chat_id": chat, "text": text[:4096]}).encode("utf-8")
        return Request(base + "/sendMessage", "POST", {"Content-Type": "application/json"}, body)

    # discord
    url = cfg["discord_webhook"].strip()
    prefix, allowed = discord_mention(cfg.get("discord_mention", "")) if ping else ("", {"parse": []})
    content = f"**{title}**\n{message}"
    if prefix:
        content = f"{prefix} {content}"
    payload = {"content": content[:2000], "allowed_mentions": allowed}
    if image:
        body, ctype = _multipart({"payload_json": json.dumps(payload)},
                                 [("files[0]", "screen.jpg", "image/jpeg", image)])
        return Request(url, "POST", {"Content-Type": ctype}, body)
    return Request(url, "POST", {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8"))


def _open(req: Request, timeout: float = TIMEOUT) -> bytes:
    r = urllib.request.Request(req.url, data=req.body, method=req.method,
                               headers={"User-Agent": USER_AGENT, **req.headers})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        retry = 0.0
        try:
            raw = exc.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
                detail = data.get("description") or data.get("error") or data.get("message") or raw
                if exc.code == 429:  # Discord: retry_after; Telegram: parameters.retry_after
                    retry = float(data.get("retry_after") or (data.get("parameters") or {}).get("retry_after") or 0)
            except (ValueError, TypeError, AttributeError):
                detail = raw
        except Exception:
            pass
        if exc.code == 429:
            if not retry:
                try:
                    retry = float(exc.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    retry = 0.0
            retry = max(retry, 0.5)
        raise NotifyError(f"HTTP {exc.code}: {str(detail).strip()[:200] or exc.reason}", retry) from None
    except urllib.error.URLError as exc:
        raise NotifyError(f"Couldn't connect: {exc.reason}") from None
    except OSError as exc:
        raise NotifyError(f"Couldn't connect: {exc}") from None


def send(cfg: dict, title: str, message: str, image: bytes = b"", priority: str = "high",
         tags: str = "warning", timeout: float = TIMEOUT, ping: bool = False) -> None:
    _open(build_request(cfg, title, message, image, priority, tags, ping), timeout)


def send_async(cfg: dict, title: str, message: str, image: bytes = b"", priority: str = "high",
               tags: str = "warning", on_done: Optional[Callable[[bool, str], None]] = None,
               ping: bool = False) -> threading.Thread:
    """Send on a background thread; ``on_done(ok, error_text)`` runs on that thread."""
    def run():
        try:
            send(cfg, title, message, image, priority, tags, ping=ping)
        except Exception as exc:
            if on_done:
                on_done(False, str(exc))
            return
        if on_done:
            on_done(True, "")

    th = threading.Thread(target=run, name="mttk-notify", daemon=True)
    th.start()
    return th


ImageSource = Union[bytes, Callable[[], bytes]]


@dataclass
class _Item:
    cfg: dict
    title: str
    message: str
    image: ImageSource = b""
    priority: str = "default"
    tags: str = ""
    ping: bool = False
    droppable: bool = False
    on_done: Optional[Callable[[bool, str], None]] = None


class Outbox:
    """Sends messages one at a time on a background thread, in the order they
    were queued, so a playback's log reads in order on the phone.

    Rate limits (HTTP 429; Discord allows about 5 messages per 2 seconds) are
    waited out and the message is sent again. If ``droppable`` messages
    (progress logs) pile up, e.g. with runs only a second long, the oldest of
    them are dropped so the log doesn't fall further and further behind;
    other messages (errors, pictures) are always sent. ``image`` may be a
    function returning the bytes, so a screenshot is encoded on the sending
    thread too. ``on_done(ok, error_text)`` runs on the sending thread.
    """

    def __init__(self, sender: Optional[Callable] = None, max_backlog: int = 8, max_retries: int = 4,
                 max_wait: float = 60.0):
        self._send = sender or send
        self.max_backlog = max_backlog
        self.max_retries = max_retries
        self.max_wait = max_wait
        self.dropped = 0
        self._queue: "collections.deque[_Item]" = collections.deque()
        self._cv = threading.Condition()
        self._busy = False
        self._thread: Optional[threading.Thread] = None

    def put(self, cfg: dict, title: str, message: str, image: ImageSource = b"", priority: str = "default",
            tags: str = "", ping: bool = False, droppable: bool = False,
            on_done: Optional[Callable[[bool, str], None]] = None) -> None:
        with self._cv:
            self._queue.append(_Item(dict(cfg), title, message, image, priority, tags, ping, droppable, on_done))
            waiting = [it for it in self._queue if it.droppable]
            for it in waiting[:max(0, len(waiting) - self.max_backlog)]:
                self._queue.remove(it)
                self.dropped += 1
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="mttk-outbox", daemon=True)
                self._thread.start()
            self._cv.notify_all()

    def pending(self) -> int:
        with self._cv:
            return len(self._queue) + (1 if self._busy else 0)

    def flush(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for everything queued to be sent."""
        end = time.monotonic() + timeout
        with self._cv:
            while self._queue or self._busy:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                item = self._queue.popleft()
                self._busy = True
            try:
                ok, err = self._deliver(item)
                if item.on_done:
                    try:
                        item.on_done(ok, err)
                    except Exception:
                        pass
            finally:
                with self._cv:
                    self._busy = False
                    self._cv.notify_all()

    def _deliver(self, item: _Item) -> Tuple[bool, str]:
        try:
            image = item.image() if callable(item.image) else item.image
        except Exception:
            image = b""
        for attempt in range(self.max_retries + 1):
            try:
                self._send(item.cfg, item.title, item.message, image or b"", item.priority, item.tags,
                           ping=item.ping)
                return True, ""
            except NotifyError as exc:
                if exc.retry_after and attempt < self.max_retries:
                    time.sleep(min(exc.retry_after, self.max_wait))
                    continue
                return False, str(exc)
            except Exception as exc:
                return False, str(exc)
        return False, "rate limited"


def telegram_chat_id(token: str, timeout: float = TIMEOUT) -> Tuple[str, str]:
    """The chat that most recently messaged the bot: ``(chat_id, name)``."""
    token = (token or "").strip()
    if not token:
        raise NotifyError("Enter the bot token first")
    raw = _open(Request(f"https://api.telegram.org/bot{token}/getUpdates", "GET"), timeout)
    try:
        updates = json.loads(raw.decode("utf-8")).get("result", [])
    except ValueError:
        raise NotifyError("Unexpected answer from Telegram") from None
    for upd in reversed(updates):
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
            return str(chat["id"]), name or chat.get("username", "")
    raise NotifyError("No messages yet: open your bot in Telegram, send it any message, then try again")
