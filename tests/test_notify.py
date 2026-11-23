import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mttk import notify as N

NTFY = {"service": "ntfy", "ntfy_server": "https://ntfy.sh/", "ntfy_topic": "mttk-abc", "ntfy_token": ""}


def test_ntfy_text_and_picture():
    r = N.build_request(NTFY, "Macro stopped", "Shop: 54% match")
    assert (r.method, r.url) == ("POST", "https://ntfy.sh/mttk-abc")
    assert r.headers["X-Title"] == "Macro stopped" and r.headers["X-Priority"] == "4"
    assert r.body == b"Shop: 54% match"
    r = N.build_request({**NTFY, "ntfy_token": "tk"}, "T", "two\nlines", image=b"JPEG")
    assert r.method == "PUT" and r.body == b"JPEG"
    assert r.headers["Authorization"] == "Bearer tk"
    assert r.headers["X-Message"].startswith("=?UTF-8?B?")


def test_non_ascii_title_is_rfc2047():
    title = "נקודת בדיקה ✓"
    enc = N.build_request(NTFY, title, "x").headers["X-Title"]
    assert enc.startswith("=?UTF-8?B?") and enc.endswith("?=")
    assert base64.b64decode(enc[10:-2]).decode() == title
    enc.encode("ascii")


def test_telegram_and_discord():
    tg = {"service": "telegram", "telegram_token": "123:ABC", "telegram_chat_id": "42"}
    r = N.build_request(tg, "T", "M")
    assert r.url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert json.loads(r.body) == {"chat_id": "42", "text": "T\nM"}
    r = N.build_request(tg, "T", "M", image=b"IMG")
    assert r.url.endswith("/sendPhoto") and b'name="photo"' in r.body and b"IMG" in r.body
    dc = {"service": "discord", "discord_webhook": "https://discord.com/api/webhooks/1/x"}
    r = N.build_request(dc, "T", "M")
    assert json.loads(r.body)["content"] == "**T**\nM"
    r = N.build_request(dc, "T", "M", image=b"IMG")
    assert b'name="files[0]"' in r.body and b'name="payload_json"' in r.body


def test_problems_are_reported():
    assert N.problem({"service": "ntfy", "ntfy_topic": ""})
    assert N.problem({"service": "ntfy", "ntfy_topic": "has space"})
    assert N.problem({"service": "telegram", "telegram_token": "x"})
    assert N.problem({"service": "discord", "discord_webhook": "http://x"})
    assert not N.enabled({"service": "none"})
    assert N.enabled(NTFY)
    with pytest.raises(N.NotifyError):
        N.build_request({"service": "none"}, "T", "M")
    assert len(N.random_topic()) == 21


def serve_once(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    return srv


def test_send_to_local_ntfy_server():
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            got["path"] = self.path
            got["headers"] = dict(self.headers)
            got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = serve_once(Handler)
    cfg = {**NTFY, "ntfy_server": f"http://127.0.0.1:{srv.server_port}"}
    N.send(cfg, "Macro stopped", "Shop failed", image=b"\xff\xd8JPEG", timeout=5)
    srv.server_close()
    assert got["path"] == "/mttk-abc"
    assert got["body"] == b"\xff\xd8JPEG"
    assert got["headers"]["X-Title"] == "Macro stopped"
    assert got["headers"]["User-Agent"].startswith("MoreThanTinyTask/")


def test_send_reports_http_errors():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'{"error": "forbidden topic"}')

        def log_message(self, *a):
            pass

    srv = serve_once(Handler)
    cfg = {**NTFY, "ntfy_server": f"http://127.0.0.1:{srv.server_port}"}
    with pytest.raises(N.NotifyError, match="403.*forbidden topic"):
        N.send(cfg, "T", "M", timeout=5)
    srv.server_close()


DISCORD = {"service": "discord", "discord_webhook": "https://discord.com/api/webhooks/1/x",
           "discord_mention": "@everyone"}


def test_discord_ping_only_when_asked():
    plain = json.loads(N.build_request(DISCORD, "Run 2 of 5", "log").body)
    assert plain["content"] == "**Run 2 of 5**\nlog"
    assert plain["allowed_mentions"] == {"parse": []}           # logs never ping
    err = json.loads(N.build_request(DISCORD, "Macro stopped", "54% match", ping=True).body)
    assert err["content"] == "@everyone **Macro stopped**\n54% match"
    assert err["allowed_mentions"] == {"parse": ["everyone"]}
    # with a picture the same payload goes in payload_json
    r = N.build_request(DISCORD, "Macro stopped", "x", image=b"IMG", ping=True)
    assert b"@everyone **Macro stopped**" in r.body and b'"everyone"' in r.body


def test_discord_mention_parsing():
    assert N.discord_mention("") == ("", {"parse": []})
    who, allowed = N.discord_mention("here, <@!123456789012345678> &987654321 junk 123456789012345678")
    assert who == "@here <@123456789012345678> <@&987654321>"
    assert allowed == {"parse": ["everyone"], "users": ["123456789012345678"], "roles": ["987654321"]}
    assert N.mention_problem("@everyone <@&55555>") == ""
    assert "junk" in N.mention_problem("@everyone junk")
    # a ping with nothing configured is just the message
    cfg = {**DISCORD, "discord_mention": ""}
    assert json.loads(N.build_request(cfg, "T", "M", ping=True).body)["content"] == "**T**\nM"


def test_outbox_sends_in_order_and_waits_out_rate_limits():
    sent, calls = [], []

    def sender(cfg, title, message, image, priority, tags, ping=False):
        calls.append(title)
        if title == "two" and calls.count("two") == 1:
            raise N.NotifyError("HTTP 429: slow down", retry_after=0.05)
        sent.append((title, image, ping))

    done = []
    box = N.Outbox(sender)
    box.put(DISCORD, "one", "m")
    box.put(DISCORD, "two", "m", image=lambda: b"JPEG", on_done=lambda ok, err: done.append(ok))
    box.put(DISCORD, "three", "m", ping=True)
    assert box.flush(5)
    assert sent == [("one", b"", False), ("two", b"JPEG", False), ("three", b"", True)]
    assert calls.count("two") == 2 and done == [True]


def test_outbox_drops_oldest_logs_when_behind_but_never_alerts():
    gate = threading.Event()
    sent = []

    def sender(cfg, title, message, image, priority, tags, ping=False):
        gate.wait(5)
        sent.append(title)

    box = N.Outbox(sender, max_backlog=2)
    box.put(DISCORD, "start", "m")                 # being sent (blocked on the gate)
    for i in range(1, 6):
        box.put(DISCORD, f"run {i}", "m", droppable=True)
    box.put(DISCORD, "alert", "m", ping=True)
    gate.set()
    assert box.flush(5)
    assert sent[0] == "start" and sent[-1] == "alert"
    assert [t for t in sent if t.startswith("run")] == ["run 4", "run 5"]
    assert box.dropped == 3


def test_outbox_reports_failures():
    def sender(*a, **kw):
        raise N.NotifyError("HTTP 404: Unknown Webhook")

    done = []
    box = N.Outbox(sender)
    box.put(DISCORD, "x", "m", on_done=lambda ok, err: done.append((ok, err)))
    assert box.flush(5)
    assert done == [(False, "HTTP 404: Unknown Webhook")]


def test_rate_limit_answer_becomes_retry_after():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b'{"message": "You are being rate limited.", "retry_after": 1.25, "global": false}')

        def log_message(self, *a):
            pass

    srv = serve_once(Handler)
    cfg = {**NTFY, "ntfy_server": f"http://127.0.0.1:{srv.server_port}"}
    with pytest.raises(N.NotifyError) as info:
        N.send(cfg, "T", "M", timeout=5)
    srv.server_close()
    assert info.value.retry_after == 1.25
