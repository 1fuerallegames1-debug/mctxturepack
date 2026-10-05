"""Browser-Oberfläche: ein kleiner Webserver, der nur auf deinem PC erreichbar ist."""

from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .agent import DENIED_MESSAGE, Agent
from .config import save_setting
from .llm import LLMError
from .tools import truncate

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 2 * 1024 * 1024
APPROVAL_TIMEOUT = 15 * 60


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class WebApp:
    def __init__(self, agent: Agent, host: str = "127.0.0.1", port: int = 8765, token: str | None = None):
        self.agent = agent
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.busy = threading.Lock()
        self.pending: dict[str, dict] = {}
        self.pending_lock = threading.Lock()
        self.httpd: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------ Server

    def make_server(self) -> ThreadingHTTPServer:
        app = self

        class Handler(KaiHandler):
            pass

        Handler.app = app
        last_error = None
        for port in ([0] if self.port == 0 else range(self.port, self.port + 20)):
            try:
                httpd = ThreadingHTTPServer((self.host, port), Handler)
                httpd.daemon_threads = True
                self.port = httpd.server_address[1]
                self.httpd = httpd
                return httpd
            except OSError as e:
                last_error = e
        raise OSError(f"Kein freier Port ab {self.port} gefunden: {last_error}")

    def url(self) -> str:
        host = self.host
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}/#token={self.token}"

    # ------------------------------------------------------------ Nachfragen

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        with self.pending_lock:
            slot = self.pending.get(approval_id)
        if not slot:
            return False
        slot["decision"] = decision if decision in ("yes", "no", "always") else "no"
        slot["event"].set()
        return True

    def deny_all_pending(self):
        with self.pending_lock:
            slots = list(self.pending.values())
        for slot in slots:
            slot["decision"] = "no"
            slot["event"].set()

    def history_for_ui(self) -> list[dict]:
        """Bisheriges Gespräch für die Anzeige nach einem Neuladen der Seite."""
        items = []
        results = {m.get("tool_call_id"): m for m in self.agent.history if m["role"] == "tool"}
        for m in self.agent.history:
            if m["role"] == "user":
                items.append({"role": "user", "text": m["content"]})
            elif m["role"] == "assistant":
                if m.get("content"):
                    items.append({"role": "assistant", "text": m["content"]})
                for call in m.get("tool_calls") or []:
                    tool_obj = self.agent.registry.get(call["name"])
                    args = call.get("arguments") or {}
                    summary = tool_obj.describe(args) if tool_obj else json.dumps(args, ensure_ascii=False)
                    res = results.get(call["id"], {}).get("content", "")
                    status = ("denied" if res == DENIED_MESSAGE
                              else "error" if res.startswith("Error") else "ok")
                    items.append({"role": "tool", "name": call["name"], "summary": summary,
                                  "result": "Vom Benutzer abgelehnt." if status == "denied" else truncate(res, 4000),
                                  "status": status})
        return items


class KaiHandler(BaseHTTPRequestHandler):
    app: WebApp = None  # wird in make_server gesetzt
    server_version = "Kai"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):  # keine Zugriffsprotokolle in der Konsole
        pass

    # ------------------------------------------------------------ Hilfsfunktionen

    def _host_ok(self) -> bool:
        """Schutz vor DNS-Rebinding: nur Anfragen an localhost/127.0.0.1 annehmen."""
        if not _is_loopback(self.app.host):
            return True  # bewusst im Netzwerk freigegeben -> nur Token-Schutz
        host = self.headers.get("Host", "")
        hostname = urlsplit("//" + host).hostname or ""
        return _is_loopback(hostname)

    def _authorized(self) -> bool:
        token = self.headers.get("X-Kai-Token", "")
        return bool(token) and secrets.compare_digest(token, self.app.token)

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, status: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _guard(self, need_auth: bool = True) -> bool:
        if not self._host_ok():
            self._send_json(403, {"error": "Ungültiger Host."})
            return False
        if need_auth and not self._authorized():
            self._send_json(401, {"error": "Kein gültiger Zugangsschlüssel. Bitte Kai über start-web neu öffnen."})
            return False
        return True

    # ------------------------------------------------------------ GET

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            if not self._guard(need_auth=False):
                return
            body = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._guard():
            return
        agent = self.app.agent
        if path == "/api/status":
            self._send_json(200, {"name": agent.cfg.get("name") or "Kai", "model": agent.client.model,
                                  "auto": agent.auto_mode, "busy": self.app.busy.locked(),
                                  "plugin_errors": agent.plugin_errors})
        elif path == "/api/history":
            self._send_json(200, {"items": self.app.history_for_ui()})
        elif path == "/api/models":
            try:
                self._send_json(200, {"models": agent.client.list_models(), "current": agent.client.model})
            except LLMError as e:
                self._send_json(502, {"error": str(e)})
        elif path == "/api/memory":
            self._send_json(200, {"facts": agent.memory.facts})
        else:
            self._send_json(404, {"error": "Nicht gefunden."})

    # ------------------------------------------------------------ POST

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self._guard():
            return
        data = self._read_json()
        app, agent = self.app, self.app.agent
        if path == "/api/chat":
            self._chat(str(data.get("text") or "").strip())
        elif path == "/api/approve":
            ok = app.resolve_approval(str(data.get("approval_id") or ""), str(data.get("decision") or "no"))
            self._send_json(200 if ok else 404, {"ok": ok})
        elif path == "/api/stop":
            agent.cancel()
            app.deny_all_pending()
            self._send_json(200, {"ok": True})
        elif path == "/api/reset":
            if app.busy.locked():
                self._send_json(409, {"error": "Bitte erst die laufende Aufgabe stoppen."})
            else:
                agent.reset()
                self._send_json(200, {"ok": True})
        elif path == "/api/settings":
            self._settings(data)
        elif path == "/api/forget":
            removed = agent.memory.remove(int(data.get("id") or 0)) if str(data.get("id", "")).isdigit() else None
            self._send_json(200 if removed else 404, {"ok": bool(removed)})
        else:
            self._send_json(404, {"error": "Nicht gefunden."})

    def _settings(self, data: dict):
        agent = self.app.agent
        if "auto" in data:
            agent.auto_mode = bool(data["auto"])
            if not agent.auto_mode:
                agent.session_allowed.clear()
        if data.get("model"):
            model = str(data["model"])
            try:
                models = agent.client.list_models()
            except LLMError as e:
                self._send_json(502, {"error": str(e)})
                return
            if models and model not in models:
                self._send_json(400, {"error": f"Modell '{model}' ist nicht installiert (ollama pull {model})."})
                return
            agent.set_model(model)
            try:
                save_setting("modell", model)
            except Exception:
                pass
        self._send_json(200, {"ok": True, "auto": agent.auto_mode, "model": agent.client.model})

    def _chat(self, text: str):
        app, agent = self.app, self.app.agent
        if not text:
            self._send_json(400, {"error": "Leere Nachricht."})
            return
        if not app.busy.acquire(blocking=False):
            self._send_json(409, {"error": "Ich arbeite noch an der letzten Aufgabe."})
            return
        gen = None
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("X-Accel-Buffering", "no")
            self._security_headers()
            self.end_headers()
            write_lock = threading.Lock()

            def emit(obj: dict):
                line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
                with write_lock:
                    self.wfile.write(line)
                    self.wfile.flush()

            def approve(req: dict) -> str:
                approval_id = secrets.token_hex(8)
                slot = {"event": threading.Event(), "decision": "no"}
                with app.pending_lock:
                    app.pending[approval_id] = slot
                try:
                    emit({"type": "approval", "approval_id": approval_id, "tool": req["tool"],
                          "summary": req["summary"], "dangerous": req["dangerous"]})
                    waited = 0.0
                    while not slot["event"].wait(0.5):
                        waited += 0.5
                        if agent.cancel_event.is_set() or waited > APPROVAL_TIMEOUT:
                            break
                        if waited % 10 == 0:
                            emit({"type": "ping"})  # merkt, wenn der Browser geschlossen wurde
                    return slot["decision"]
                finally:
                    with app.pending_lock:
                        app.pending.pop(approval_id, None)

            gen = agent.run(text, approve)
            for ev in gen:
                emit(ev)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            agent.cancel()  # Browser-Tab wurde geschlossen
        except OSError:
            agent.cancel()
        finally:
            if gen is not None:
                gen.close()
            app.busy.release()


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def run_web(agent: Agent, open_browser: bool = True, port: int | None = None) -> int:
    cfg = agent.cfg
    app = WebApp(agent, host=cfg.get("web_host") or "127.0.0.1", port=int(port or cfg.get("web_port") or 8765))
    httpd = app.make_server()
    url = app.url()
    print(f"\nKai läuft im Browser: {url}")
    if not _is_loopback(app.host):
        print(f"ACHTUNG: Kai ist im Netzwerk erreichbar (http://{_lan_ip()}:{app.port}/#token={app.token}).\n"
              "Jeder mit diesem Link kann Befehle auf diesem PC auslösen. Nur in vertrauenswürdigen Netzen nutzen!")
    print("Zum Beenden dieses Fenster schließen oder Strg+C drücken.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nKai wurde beendet.")
    finally:
        agent.cancel()
        app.deny_all_pending()
        httpd.server_close()
    return 0
