"""Browser-Oberfläche: ein kleiner Webserver für PC und Handy.

Eine Aufgabe läuft im Hintergrund weiter, auch wenn der Browser kurz die Verbindung verliert
(z. B. wenn das Handy-Display ausgeht). Die Seite holt sich neue Ereignisse per "Long-Polling"
ab und macht nach einem Verbindungsabbruch einfach dort weiter, wo sie war.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import icons
from .agent import DENIED_MESSAGE, FAILED_EXIT, INTERRUPTED_NOTE, NOT_RUN_NOTE, Agent
from .config import save_setting
from .llm import LLMError
from .qr import QrCode
from .regeln import REGELN
from .tools import truncate

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 2 * 1024 * 1024
APPROVAL_TIMEOUT = 15 * 60
POLL_TIMEOUT = 25


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def load_token(data_dir: Path, renew: bool = False) -> str:
    """Zugangsschlüssel laden (bleibt über Neustarts gleich, damit Lesezeichen am Handy funktionieren)."""
    path = Path(data_dir) / "zugang.json"
    if not renew:
        try:
            token = json.loads(path.read_text(encoding="utf-8")).get("token", "")
            if len(token) >= 20:
                return token
        except (OSError, ValueError, AttributeError):
            pass
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")
    return token


def local_ips() -> list[str]:
    """IPv4-Adressen dieses PCs im Netzwerk (die wahrscheinlichste zuerst)."""
    ips = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    result = []
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not addr.is_loopback and not addr.is_link_local and ip not in result:
            result.append(ip)
    return result


def _is_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


class Run:
    """Eine laufende (oder gerade beendete) Aufgabe mit allen Ereignissen für die Oberfläche."""

    def __init__(self, run_id: int, history_start: int):
        self.id = run_id
        self.history_start = history_start
        self.events: list[dict] = []
        self.done = False
        self.cond = threading.Condition()

    def emit(self, event: dict):
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def finish(self):
        with self.cond:
            self.done = True
            self.cond.notify_all()

    def wait(self, since: int, timeout: float) -> tuple[list[dict], int, bool]:
        with self.cond:
            if since >= len(self.events) and not self.done:
                self.cond.wait(timeout)
            since = max(0, min(since, len(self.events)))
            return self.events[since:], len(self.events), self.done


class WebApp:
    def __init__(self, agent: Agent, host: str = "127.0.0.1", port: int = 8765, token: str | None = None,
                 phone: bool = False):
        self.agent = agent
        self.host = host
        self.port = port
        self.phone = phone
        self.token = token or secrets.token_urlsafe(24)
        self.busy = threading.Lock()
        self.pending: dict[str, dict] = {}
        self.pending_lock = threading.Lock()
        self.run: Run | None = None
        self._run_counter = 0
        self.httpd: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------ Server

    def make_server(self) -> ThreadingHTTPServer:
        handler = type("Handler", (AngelHandler,), {"app": self})
        last_error = None
        for port in ([0] if self.port == 0 else range(self.port, self.port + 20)):
            try:
                httpd = ThreadingHTTPServer((self.host, port), handler)
                httpd.daemon_threads = True
                self.port = httpd.server_address[1]
                self.httpd = httpd
                return httpd
            except OSError as e:
                last_error = e
        raise OSError(f"Kein freier Port ab {self.port} gefunden: {last_error}")

    def local_url(self) -> str:
        host = self.host
        if host in ("0.0.0.0", "::", "") or _is_loopback(host):
            host = "127.0.0.1"
        elif ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.port}/#token={self.token}"

    def phone_urls(self) -> list[str]:
        if not self.phone:
            return []
        return [f"http://{ip}:{self.port}/#token={self.token}" for ip in local_ips()]

    # ------------------------------------------------------------ Aufgaben

    def start_run(self, text: str) -> Run | None:
        if not self.busy.acquire(blocking=False):
            return None
        self._run_counter += 1
        run = Run(self._run_counter, len(self.agent.history))
        self.run = run
        threading.Thread(target=self._worker, args=(run, text), daemon=True, name=f"run-{run.id}").start()
        return run

    def _worker(self, run: Run, text: str):
        agent = self.agent

        def approve(req: dict) -> str:
            approval_id = secrets.token_hex(8)
            slot = {"event": threading.Event(), "decision": "no"}
            with self.pending_lock:
                self.pending[approval_id] = slot
            run.emit({"type": "approval", "approval_id": approval_id, "tool": req["tool"],
                      "summary": req["summary"], "dangerous": req["dangerous"], "warning": req.get("warning"),
                      "always_label": req.get("always_label")})
            waited = 0.0
            while not slot["event"].wait(0.5):
                waited += 0.5
                if agent.cancel_event.is_set() or waited > APPROVAL_TIMEOUT:
                    break
            with self.pending_lock:
                self.pending.pop(approval_id, None)
            decision = slot["decision"] if slot["event"].is_set() else "no"
            run.emit({"type": "approval_done", "approval_id": approval_id, "decision": decision})
            return decision

        run.emit({"type": "user", "text": text})
        try:
            for event in agent.run(text, approve):
                run.emit(event)
        except Exception as e:  # unerwartete Fehler trotzdem anzeigen
            run.emit({"type": "error", "message": f"Interner Fehler: {type(e).__name__}: {e}"})
        finally:
            run.finish()
            self.busy.release()

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
        """Bisheriges Gespräch für die Anzeige (ohne die gerade laufende Aufgabe)."""
        hist = self.agent.history
        run = self.run
        if run is not None and not run.done:
            hist = hist[:run.history_start]
        items = []
        for idx, m in enumerate(hist):
            if m["role"] == "user":
                items.append({"role": "user", "text": m["content"]})
            elif m["role"] == "assistant":
                if m.get("content"):
                    items.append({"role": "assistant", "text": m["content"]})
                # Ergebnisse stehen direkt hinter der Assistenten-Nachricht
                results, j = {}, idx + 1
                while j < len(hist) and hist[j]["role"] == "tool":
                    results[hist[j].get("tool_call_id")] = hist[j]
                    j += 1
                for call in m.get("tool_calls") or []:
                    tool_obj = self.agent.registry.get(call["name"])
                    args = call.get("arguments") or {}
                    summary = tool_obj.describe(args) if tool_obj else json.dumps(args, ensure_ascii=False)
                    res = results.get(call["id"], {}).get("content", "")
                    status = ("denied" if res == DENIED_MESSAGE
                              else "cancelled" if res in (NOT_RUN_NOTE, INTERRUPTED_NOTE)
                              else "error" if res.startswith(("Error", "Refused")) or FAILED_EXIT.search(res)
                              else "ok")
                    items.append({"role": "tool", "name": call["name"], "summary": summary,
                                  "result": {"denied": "Vom Benutzer abgelehnt.", "cancelled": "Abgebrochen."}.get(
                                      status, truncate(res, 4000)),
                                  "status": status})
        return items

    def pairing(self) -> dict:
        urls = self.phone_urls()
        if not urls:
            return {"enabled": False}
        main = urls[0]
        svg = QrCode(main).to_svg(border=3, scale=6)
        return {"enabled": True, "url": main, "urls": urls,
                "qr": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode(),
                "tailscale": [u for u in urls if _is_tailscale(urlsplit(u).hostname or "")]}


class AngelHandler(BaseHTTPRequestHandler):
    app: WebApp = None  # wird in make_server gesetzt
    server_version = "Angel"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):  # keine Zugriffsprotokolle in der Konsole
        pass

    # ------------------------------------------------------------ Hilfsfunktionen

    def _host_ok(self) -> bool:
        """Schutz vor DNS-Rebinding: im lokalen Modus nur Anfragen an localhost/127.0.0.1 annehmen."""
        if not _is_loopback(self.app.host):
            return True  # bewusst im Netzwerk freigegeben -> Schutz über den Zugangsschlüssel
        host = self.headers.get("Host", "")
        hostname = urlsplit("//" + host).hostname or ""
        return _is_loopback(hostname)

    def _authorized(self) -> bool:
        token = self.headers.get("X-Angel-Token", "")
        return bool(token) and secrets.compare_digest(token.encode(), self.app.token.encode())

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, obj):
        self._send_bytes(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
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
            self._send_json(401, {"error": "Kein gültiger Zugangsschlüssel."})
            return False
        return True

    # ------------------------------------------------------------ GET

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            if self._guard(need_auth=False):
                body = (STATIC / "index.html").read_bytes()
                self._send_bytes(200, body, "text/html; charset=utf-8", {
                    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                               "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                               "form-action 'none'"})
            return
        if path in ("/manifest.webmanifest", "/icon.svg", "/icon-180.png", "/icon-192.png", "/icon-512.png"):
            if self._guard(need_auth=False):
                self._static_asset(path, parse_qs(url.query))
            return
        if not self._guard():
            return
        app, agent = self.app, self.app.agent
        if path == "/api/status":
            run = app.run
            self._send_json(200, {
                "name": agent.cfg.get("name") or "Angel", "model": agent.client.model, "auto": agent.auto_mode,
                "busy": app.busy.locked(), "plugin_errors": agent.plugin_errors, "phone": app.phone,
                "rules": [r.format(name=agent.cfg.get("name") or "Angel") for r in REGELN],
                "run": {"id": run.id, "done": run.done} if run else None,
            })
        elif path == "/api/history":
            self._send_json(200, {"items": app.history_for_ui()})
        elif path == "/api/events":
            self._events(parse_qs(url.query))
        elif path == "/api/models":
            try:
                self._send_json(200, {"models": agent.client.list_models(), "current": agent.client.model})
            except LLMError as e:
                self._send_json(502, {"error": str(e)})
        elif path == "/api/memory":
            self._send_json(200, {"facts": agent.memory.facts})
        elif path == "/api/pairing":
            self._send_json(200, app.pairing())
        else:
            self._send_json(404, {"error": "Nicht gefunden."})

    def _static_asset(self, path: str, query: dict):
        name = self.app.agent.cfg.get("name") or "Angel"
        cache = {"Cache-Control": "max-age=86400"}
        if path == "/manifest.webmanifest":
            # Wer den Schlüssel schon kennt, bekommt ihn als Startadresse – so funktioniert die App auf dem
            # Startbildschirm (z. B. iPhone, das dafür einen eigenen Speicher nutzt) ohne erneutes Scannen.
            given = (query.get("token") or [""])[0]
            start = "/"
            if given and secrets.compare_digest(given.encode(), self.app.token.encode()):
                start = f"/#token={self.app.token}"
            manifest = {
                "id": "/", "name": name, "short_name": name, "start_url": start, "display": "standalone",
                "background_color": "#15181c", "theme_color": "#4f46e5", "lang": "de",
                "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                          {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
            }
            self._send_bytes(200, json.dumps(manifest).encode(), "application/manifest+json")
        elif path == "/icon.svg":
            self._send_bytes(200, icons.SVG.encode(), "image/svg+xml", cache)
        else:
            size = int(path.split("-")[1].split(".")[0])
            self._send_bytes(200, icons.png(size), "image/png", cache)

    def _events(self, query: dict):
        run = self.app.run
        try:
            run_id = int((query.get("run") or ["0"])[0])
            since = int((query.get("since") or ["0"])[0])
        except ValueError:
            self._send_json(400, {"error": "Ungültige Anfrage."})
            return
        if run is None or run.id != run_id:
            self._send_json(404, {"error": "Diese Aufgabe gibt es nicht mehr."})
            return
        events, nxt, done = run.wait(since, POLL_TIMEOUT)
        self._send_json(200, {"events": events, "next": nxt, "done": done})

    # ------------------------------------------------------------ POST

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self._guard():
            return
        data = self._read_json()
        app, agent = self.app, self.app.agent
        if path == "/api/chat":
            text = str(data.get("text") or "").strip()
            if not text:
                self._send_json(400, {"error": "Leere Nachricht."})
                return
            run = app.start_run(text)
            if run is None:
                self._send_json(409, {"error": "Ich arbeite noch an der letzten Aufgabe."})
            else:
                self._send_json(200, {"run": run.id})
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
                app.run = None
                self._send_json(200, {"ok": True})
        elif path == "/api/settings":
            self._settings(data)
        elif path == "/api/forget":
            fact_id = data.get("id")
            removed = agent.memory.remove(int(fact_id)) if str(fact_id).isdigit() else None
            self._send_json(200 if removed else 404, {"ok": bool(removed)})
        else:
            self._send_json(404, {"error": "Nicht gefunden."})

    def _settings(self, data: dict):
        agent = self.app.agent
        if data.get("auto") is True and not _is_loopback(self.client_address[0]):
            # Im Handy-Modus lässt sich die Automatik nur direkt am PC einschalten
            self._send_json(403, {"error": "Die Automatik kann aus Sicherheitsgründen nur direkt am PC "
                                           "eingeschaltet werden."})
            return
        if "auto" in data:
            agent.auto_mode = data["auto"] is True
            if not agent.auto_mode:
                agent.session_allowed.clear()
        if data.get("model"):
            if self.app.busy.locked():
                self._send_json(409, {"error": "Modell erst nach der laufenden Aufgabe wechseln."})
                return
            model = str(data["model"])
            try:
                models = agent.client.list_models()
            except LLMError as e:
                self._send_json(502, {"error": str(e)})
                return
            if models and model not in models and f"{model}:latest" not in models:
                self._send_json(400, {"error": f"Modell '{model}' ist nicht installiert (ollama pull {model})."})
                return
            agent.set_model(model)
            try:
                save_setting("modell", model)
            except Exception:
                pass
        self._send_json(200, {"ok": True, "auto": agent.auto_mode, "model": agent.client.model})


def _print_qr(text: str):
    from .cli import _enable_ansi
    qr = QrCode(text).to_terminal()
    color = _enable_ansi()
    for line in qr.splitlines():
        print(f"   \033[97;40m{line}\033[0m" if color else f"   {line}")


def run_web(agent: Agent, open_browser: bool = True, port: int | None = None, phone: bool = False,
            renew_token: bool = False) -> int:
    cfg = agent.cfg
    host = cfg.get("web_host") or "127.0.0.1"
    if phone and _is_loopback(host):
        host = "0.0.0.0"
    token = load_token(Path(cfg["data_dir"]), renew=renew_token)
    app = WebApp(agent, host=host, port=int(port or cfg.get("web_port") or 8765), token=token,
                 phone=not _is_loopback(host))
    httpd = app.make_server()
    name = cfg.get("name") or "Angel"
    print(f"\n{name} läuft im Browser: {app.local_url()}")
    if app.phone:
        urls = app.phone_urls()
        if urls:
            print("\nHandy verbinden: Handy ins selbe WLAN, dann diesen QR-Code mit der Kamera scannen:\n")
            _print_qr(urls[0])
            print(f"\n   oder Adresse eintippen: {urls[0]}")
            for u in urls[1:]:
                print(f"   andere Netzwerk-Adresse: {u}" + ("  (Tailscale – auch unterwegs)" if _is_tailscale(urlsplit(u).hostname or "") else ""))
        else:
            print("Keine Netzwerk-Adresse gefunden – ist der PC mit dem WLAN/LAN verbunden?")
        print(f"\nWICHTIG: Wer diesen Link kennt, kann {name} Aufträge geben. Nur im eigenen WLAN nutzen und nicht weitergeben.\n"
              "Falls Windows nach der Firewall fragt: Zugriff für 'Private Netzwerke' erlauben.\n"
              "Neuen Schlüssel erzeugen (alte Links werden ungültig): mit --neuer-schluessel starten.")
    print("\nZum Beenden dieses Fenster schließen oder Strg+C drücken.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(app.local_url())).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print(f"\n{name} wurde beendet.")
    finally:
        agent.cancel()
        app.deny_all_pending()
        # kurz warten, damit ein laufender Befehl noch sauber beendet wird
        if app.busy.acquire(timeout=3):
            app.busy.release()
        httpd.server_close()
    return 0
