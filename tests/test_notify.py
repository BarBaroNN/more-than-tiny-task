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
