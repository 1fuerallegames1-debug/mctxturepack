"""Ein simulierter Discord-Server für die Tests (spricht die Teile der REST-API, die Angel nutzt)."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class MockDiscord:
    def __init__(self):
        self.calls: list[tuple] = []        # (method, path, body)
        self.messages: dict[str, list] = {}  # channel_id -> gesendete Nachrichten
        self.deleted: list[str] = []
        self.actions: list[tuple] = []       # (art, ...)
        self.guild = "100"
        self.channels = [
            {"id": "11", "name": "allgemein", "type": 0},
            {"id": "12", "name": "willkommen", "type": 0},
            {"id": "13", "name": "sprache", "type": 2},
            {"id": "14", "name": "regeln", "type": 0},
        ]
        self.roles = [{"id": "1", "name": "@everyone"}, {"id": "2", "name": "Mitglied"}, {"id": "3", "name": "Admin"}]
        self.members = [
            {"nick": None, "user": {"id": "900", "username": "max", "global_name": "Max"}},
            {"nick": "Trollkind", "user": {"id": "901", "username": "nervig", "global_name": None}},
        ]
        mock = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _send(self, status, obj):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth_ok(self):
                return self.headers.get("Authorization") == "Bot GUELTIG"

            def _read(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def do_GET(self):
                self._route("GET", None)

            def do_POST(self):
                self._route("POST", self._read())

            def do_PATCH(self):
                self._route("PATCH", self._read())

            def do_PUT(self):
                self._route("PUT", self._read())

            def do_DELETE(self):
                self._route("DELETE", None)

            def _route(self, method, body):
                path = urlparse(self.path).path.replace("/api/v10", "")
                mock.calls.append((method, path, body))
                if not self._auth_ok():
                    return self._send(401, {"message": "401: Unauthorized", "code": 0})
                if path == "/users/@me":
                    return self._send(200, {"id": "bot1", "username": "Angel"})
                if path == f"/guilds/{mock.guild}":
                    return self._send(200, {"id": mock.guild, "name": "Mein Server"})
                if path == f"/guilds/{mock.guild}/channels":
                    if method == "POST":
                        ch = {"id": "99", "name": body.get("name"), "type": body.get("type", 0)}
                        mock.channels.append(ch)
                        return self._send(201, ch)
                    return self._send(200, mock.channels)
                if path == f"/guilds/{mock.guild}/roles":
                    return self._send(200, mock.roles)
                if re.match(rf"/guilds/{mock.guild}/members$", path):
                    return self._send(200, mock.members)
                m = re.match(r"/channels/(\d+)/messages", path)
                if m:
                    cid = m.group(1)
                    if method == "POST":
                        msg = {"id": "m1", "content": body.get("content"), "author": {"username": "Angel"}}
                        mock.messages.setdefault(cid, []).append(msg)
                        return self._send(200, msg)
                    return self._send(200, [{"id": "x", "content": "Hallo zusammen", "author": {"username": "max"}}])
                m = re.match(r"/channels/(\d+)$", path)
                if m:
                    if method == "DELETE":
                        mock.deleted.append(m.group(1))
                        return self._send(200, {"id": m.group(1)})
                    if method == "PATCH":
                        return self._send(200, {"id": m.group(1), **body})
                m = re.match(r"/guilds/\d+/members/(\d+)/roles/(\d+)", path)
                if m:
                    mock.actions.append(("role", method, m.group(1), m.group(2)))
                    return self._send(204, {})
                m = re.match(r"/guilds/\d+/members/(\d+)$", path)
                if m:
                    mock.actions.append(("kick" if method == "DELETE" else "timeout", m.group(1), body))
                    return self._send(200 if method == "PATCH" else 204, {})
                m = re.match(r"/guilds/\d+/bans/(\d+)", path)
                if m:
                    mock.actions.append(("ban", m.group(1), body))
                    return self._send(204, {})
                self._send(404, {"message": "404: Not Found", "code": 0})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/api/v10"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
