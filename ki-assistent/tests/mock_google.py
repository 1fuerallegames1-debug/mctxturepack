"""Ein simulierter Google-Server für die Tests (Teile von Gmail, Kalender, Drive, People, Tasks)."""

from __future__ import annotations

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


class MockGoogle:
    def __init__(self):
        self.sent: list[dict] = []
        self.actions: list[tuple] = []
        self.token_calls = 0
        mock = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _send(self, status, obj, ctype="application/json"):
                body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                self._route("GET", b"")

            def do_POST(self):
                self._route("POST", self._read())

            def do_PATCH(self):
                self._route("PATCH", self._read())

            def do_DELETE(self):
                self._route("DELETE", b"")

            def _route(self, method, raw):
                u = urlsplit(self.path)
                path, q = u.path, parse_qs(u.query)
                if path == "/token":
                    mock.token_calls += 1
                    return self._send(200, {"access_token": "frisch", "expires_in": 3600})
                if not self.headers.get("Authorization", "").startswith("Bearer "):
                    return self._send(401, {"error": {"message": "missing token"}})
                body = {}
                if raw:
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        body = {"_raw": raw}
                # --- Gmail
                if path == "/gmail/v1/users/me/messages":
                    return self._send(200, {"messages": [{"id": "m1"}, {"id": "m2"}]})
                if path == "/gmail/v1/users/me/messages/send":
                    mock.sent.append(body)
                    return self._send(200, {"id": "sent1"})
                m = re.match(r"/gmail/v1/users/me/messages/(\w+)/trash", path)
                if m:
                    mock.actions.append(("trash", m.group(1)))
                    return self._send(200, {"id": m.group(1)})
                m = re.match(r"/gmail/v1/users/me/messages/(\w+)", path)
                if m:
                    if q.get("format", [""])[0] == "full":
                        payload = {"headers": [{"name": "From", "value": "chef@firma.de"},
                                               {"name": "To", "value": "ich@gmail.com"},
                                               {"name": "Subject", "value": "Meeting"},
                                               {"name": "Date", "value": "Mon, 6 Oct 2026"}],
                                   "mimeType": "text/plain", "body": {"data": _b64("Bitte um 15 Uhr.")}}
                        return self._send(200, {"id": m.group(1), "snippet": "Bitte um 15 Uhr.", "payload": payload})
                    return self._send(200, {"id": m.group(1), "snippet": "Hallo …", "payload": {"headers": [
                        {"name": "From", "value": "chef@firma.de"}, {"name": "Subject", "value": "Meeting"},
                        {"name": "Date", "value": "Mon, 6 Oct 2026"}]}})
                # --- Kalender
                if path == "/calendar/v3/calendars/primary/events":
                    if method == "POST":
                        mock.actions.append(("event_create", body.get("summary")))
                        return self._send(200, {"id": "ev1", "htmlLink": "http://cal/ev1"})
                    return self._send(200, {"items": [{"id": "ev9", "summary": "Zahnarzt",
                                                       "start": {"dateTime": "2026-10-07T09:00:00Z"}}]})
                m = re.match(r"/calendar/v3/calendars/primary/events/(\w+)", path)
                if m and method == "DELETE":
                    mock.actions.append(("event_delete", m.group(1)))
                    return self._send(204, b"")
                # --- Drive
                if path == "/drive/v3/files":
                    if method == "POST":
                        return self._send(200, {"id": "f_new", "name": body.get("name")})
                    return self._send(200, {"files": [{"id": "f1", "name": "Urlaub.txt",
                                                       "mimeType": "text/plain", "modifiedTime": "2026-10-01T00:00:00Z"}]})
                m = re.match(r"/upload/drive/v3/files/(\w+)", path)
                if m:
                    mock.actions.append(("upload", m.group(1), len(raw)))
                    return self._send(200, {"id": m.group(1)})
                m = re.match(r"/drive/v3/files/(\w+)/permissions", path)
                if m:
                    mock.actions.append(("share", m.group(1), body.get("emailAddress"), body.get("role")))
                    return self._send(200, {"id": "perm1"})
                m = re.match(r"/drive/v3/files/(\w+)/export", path)
                if m:
                    return self._send(200, "Exportierter Text".encode(), ctype="text/plain")
                m = re.match(r"/drive/v3/files/(\w+)", path)
                if m:
                    if method == "PATCH":
                        mock.actions.append(("drive_patch", m.group(1), body))
                        return self._send(200, {"id": m.group(1)})
                    if q.get("alt", [""])[0] == "media":
                        return self._send(200, "Dateiinhalt".encode(), ctype="text/plain")
                    return self._send(200, {"id": m.group(1), "name": "Urlaub.txt", "mimeType": "text/plain"})
                # --- People
                if path == "/v1/people:searchContacts":
                    return self._send(200, {"results": [{"person": {
                        "names": [{"displayName": "Max Mustermann"}],
                        "emailAddresses": [{"value": "max@example.com"}],
                        "phoneNumbers": [{"value": "0123"}]}}]})
                # --- Tasks
                if path == "/tasks/v1/lists/@default/tasks":
                    if method == "POST":
                        mock.actions.append(("task_add", body.get("title"), body.get("due")))
                        return self._send(200, {"id": "t_new"})
                    return self._send(200, {"items": [{"id": "t1", "title": "Mülltonne rausstellen",
                                                       "due": "2026-10-08T00:00:00Z"}]})
                m = re.match(r"/tasks/v1/lists/@default/tasks/(\w+)", path)
                if m:
                    mock.actions.append(("task_patch", m.group(1), body))
                    return self._send(200, {"id": m.group(1)})
                if path == "/oauth2/v2/userinfo":
                    return self._send(200, {"email": "ich@gmail.com"})
                self._send(404, {"error": {"message": "404: " + path}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
