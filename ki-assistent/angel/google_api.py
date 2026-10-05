"""Zugriff auf dein Google-Konto über die offizielle Google-Anmeldung (OAuth 2.0), ohne Zusatzpakete.

Angel speichert NIE dein Passwort. Du meldest dich einmal bei Google im Browser an und erlaubst den
Zugriff; Google gibt Angel dann ein Token, das nur in daten/google_token.json auf deinem PC liegt.
Du kannst den Zugriff jederzeit widerrufen: https://myaccount.google.com/permissions

Hier stehen die Anmeldung (OAuth) und die REST-Aufrufe. Welche davon Angel als Werkzeuge nutzen darf,
steht in plugins/google.py.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# "Alles" – Lesen und Schreiben für Gmail, Kalender, Drive, Kontakte (lesen) und Tasks.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/userinfo.email",
]


class GoogleError(Exception):
    """Verständliche Meldung für den Benutzer."""


class NeedsLogin(GoogleError):
    """Es ist noch keine (gültige) Anmeldung vorhanden."""


def _explain(code: int, message: str) -> str:
    low = (message or "").lower()
    if code in (401, 403):
        if "insufficient" in low or "scope" in low:
            return ("Google verweigert den Zugriff: der Anmeldung fehlt eine Berechtigung (Scope). "
                    "Melde Angel neu an (daten/google_token.json löschen) und stimme allen Haken zu.")
        return f"Google verweigert den Zugriff ({code}: {message})."
    if code == 404:
        return f"Bei Google nicht gefunden (404: {message})."
    if code == 429:
        return "Google bremst gerade zu viele Anfragen (429). Bitte kurz warten."
    return f"Google-Fehler ({code}): {message or 'keine Details'}"


class GoogleClient:
    def __init__(self, client_id: str, client_secret: str, token_path: Path,
                 token_url: str = TOKEN_URL, api_base: str = "", timeout: float = 30):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.token_path = Path(token_path)
        self.token_url = token_url
        self.api_base = api_base.rstrip("/")  # für Tests: alle googleapis-URLs auf einen Mock umleiten
        self.timeout = timeout
        self._token = self._load()

    # ------------------------------------------------------------ Token

    def _load(self) -> dict:
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self):
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(self._token), encoding="utf-8")

    def logged_in(self) -> bool:
        return bool(self._token.get("refresh_token"))

    def _post_form(self, url: str, fields: dict) -> dict:
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            raise GoogleError(f"Anmeldung bei Google fehlgeschlagen ({e.code}): {raw}") from None
        except urllib.error.URLError as e:
            raise GoogleError(f"Google ist nicht erreichbar ({getattr(e, 'reason', e)}).") from None

    def _access_token(self, now=None) -> str:
        now = now if now is not None else time.time()
        if not self.logged_in():
            raise NeedsLogin("Angel ist noch nicht bei Google angemeldet (siehe README, Abschnitt 'Google').")
        if self._token.get("access_token") and self._token.get("expires_at", 0) > now + 60:
            return self._token["access_token"]
        # Mit dem Refresh-Token ein neues Access-Token holen
        resp = self._post_form(self.token_url, {
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self._token["refresh_token"], "grant_type": "refresh_token",
        })
        if "access_token" not in resp:
            raise NeedsLogin("Die Google-Anmeldung ist abgelaufen. Bitte neu anmelden "
                             "(daten/google_token.json löschen und Angel neu starten).")
        self._token["access_token"] = resp["access_token"]
        self._token["expires_at"] = now + int(resp.get("expires_in", 3600))
        self._save()
        return self._token["access_token"]

    def store_tokens(self, resp: dict, now=None):
        """Speichert die Tokens aus dem Code-Tausch (auch vom Anmelde-Ablauf genutzt)."""
        now = now if now is not None else time.time()
        self._token = {
            "access_token": resp.get("access_token", ""),
            "refresh_token": resp.get("refresh_token", self._token.get("refresh_token", "")),
            "expires_at": now + int(resp.get("expires_in", 3600)),
        }
        self._save()

    # ------------------------------------------------------------ REST

    def _url(self, url: str) -> str:
        if self.api_base:
            parts = urllib.parse.urlsplit(url)
            return self.api_base + parts.path + (("?" + parts.query) if parts.query else "")
        return url

    def request(self, method: str, url: str, body=None, raw_body: bytes | None = None,
                headers: dict | None = None, _retry: bool = True):
        token = self._access_token()
        hdrs = {"Authorization": f"Bearer {token}"}
        data = raw_body
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = urllib.request.Request(self._url(url), data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                if not raw:
                    return None
                ctype = r.headers.get("Content-Type", "")
                return json.loads(raw.decode("utf-8")) if "json" in ctype else raw
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            message = raw
            try:
                message = json.loads(raw).get("error", {}).get("message", raw)
            except Exception:
                pass
            if e.code == 401 and _retry:  # Token evtl. gerade abgelaufen -> einmal erneuern
                self._token["expires_at"] = 0
                return self.request(method, url, body, raw_body, headers, _retry=False)
            raise GoogleError(_explain(e.code, message)) from None
        except urllib.error.URLError as e:
            raise GoogleError(f"Google ist nicht erreichbar ({getattr(e, 'reason', e)}).") from None
        except (TimeoutError, OSError) as e:
            raise GoogleError(f"Google hat zu lange nicht geantwortet ({e}).") from None


# --------------------------------------------------------------------------- Anmeldung (interaktiv)

def login(client, cfg_google: dict, open_browser=True, port: int = 0) -> str:
    """Einmalige Anmeldung über den Browser (OAuth-Loopback). Gibt die E-Mail-Adresse zurück.

    Kann hier nicht automatisch getestet werden – braucht einen Browser und deine Zustimmung.
    """
    import http.server
    import secrets
    import threading
    import webbrowser

    if not cfg_google.get("client_id") or not cfg_google.get("client_secret"):
        raise GoogleError("Es fehlen client_id und client_secret in config.json (siehe README, Abschnitt 'Google').")
    state = secrets.token_urlsafe(16)
    code_box: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            code_box["code"] = (q.get("code") or [None])[0]
            code_box["state"] = (q.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Angel ist jetzt mit Google verbunden.</h2>"
                             "<p>Du kannst dieses Fenster schließen.</p>".encode("utf-8"))
            done.set()

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.handle_request, daemon=True).start()

    params = {"client_id": cfg_google["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
              "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state}
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print("Bitte im Browser bei Google anmelden und den Zugriff erlauben:")
    print(url)
    if open_browser:
        webbrowser.open(url)
    if not done.wait(timeout=300):
        raise GoogleError("Zeitüberschreitung bei der Google-Anmeldung.")
    if not code_box.get("code") or code_box.get("state") != state:
        raise GoogleError("Die Google-Anmeldung wurde abgebrochen oder war ungültig.")
    resp = client._post_form(client.token_url, {
        "client_id": cfg_google["client_id"], "client_secret": cfg_google["client_secret"],
        "code": code_box["code"], "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    })
    if "refresh_token" not in resp:
        raise GoogleError("Google hat kein Refresh-Token geschickt. Bitte den Zugriff unter "
                          "https://myaccount.google.com/permissions entfernen und neu anmelden.")
    client.store_tokens(resp)
    try:
        me = client.request("GET", "https://www.googleapis.com/oauth2/v2/userinfo")
        return (me or {}).get("email", "")
    except GoogleError:
        return ""
