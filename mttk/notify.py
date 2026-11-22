"""Phone alerts through ntfy, a Telegram bot or a Discord webhook.

Only the standard library is used. ``build_request`` is pure (and tested);
``send`` does the network call and ``send_async`` does it on a thread.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

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
    pass


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
                  priority: str = "high", tags: str = "warning") -> Request:
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
    payload = {"content": f"**{title}**\n{message}"[:2000], "allowed_mentions": {"parse": []}}
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
        try:
            raw = exc.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
                detail = data.get("description") or data.get("error") or data.get("message") or raw
            except ValueError:
                detail = raw
        except Exception:
            pass
        raise NotifyError(f"HTTP {exc.code}: {str(detail).strip()[:200] or exc.reason}") from None
    except urllib.error.URLError as exc:
        raise NotifyError(f"Couldn't connect: {exc.reason}") from None
    except OSError as exc:
        raise NotifyError(f"Couldn't connect: {exc}") from None


def send(cfg: dict, title: str, message: str, image: bytes = b"", priority: str = "high",
         tags: str = "warning", timeout: float = TIMEOUT) -> None:
    _open(build_request(cfg, title, message, image, priority, tags), timeout)


def send_async(cfg: dict, title: str, message: str, image: bytes = b"", priority: str = "high",
               tags: str = "warning", on_done: Optional[Callable[[bool, str], None]] = None) -> threading.Thread:
    """Send on a background thread; ``on_done(ok, error_text)`` runs on that thread."""
    def run():
        try:
            send(cfg, title, message, image, priority, tags)
        except Exception as exc:
            if on_done:
                on_done(False, str(exc))
            return
        if on_done:
            on_done(True, "")

    th = threading.Thread(target=run, name="mttk-notify", daemon=True)
    th.start()
    return th


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
