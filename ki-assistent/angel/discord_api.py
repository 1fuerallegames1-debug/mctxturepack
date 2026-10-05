"""Verbindung zu Discord über einen eigenen Bot (ganz ohne Zusatzpakete).

Angel steuert einen Discord-Bot, nicht deinen persönlichen Account. Ein Bot ist ein eigenes
Mitglied auf deinem Server. Das Steuern eines normalen Benutzer-Accounts ("Self-Bot") ist bei
Discord verboten und wird gesperrt – deshalb geht Angel bewusst nur den erlaubten Bot-Weg.

Hier werden nur die Discord-REST-Aufrufe gemacht (https). Welche davon Angel als Werkzeuge
nutzen darf, steht in plugins/discord.py.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/angel-assistant, 1.0)"

# Discord-Kanaltypen (nur die, die Angel anlegen/anzeigen kann)
CHANNEL_TYPES = {0: "Text", 2: "Sprache", 4: "Kategorie", 5: "Ankündigung", 13: "Bühne", 15: "Forum"}


class DiscordError(Exception):
    """Fehler mit einer verständlichen (deutschen) Meldung für den Benutzer."""


def _explain(code: int, message: str) -> str:
    low = (message or "").lower()
    if code == 401:
        return ("Discord hat den Bot-Token abgelehnt (401). Prüfe 'bot_token' in config.json – "
                "am besten im Developer Portal neu erzeugen (Bot → Reset Token).")
    if code == 403:
        return (f"Dem Bot fehlen die Rechte für diese Aktion (403: {message}). Gib ihm auf dem Server die nötige "
                "Rolle/Berechtigung – und achte darauf, dass seine Rolle über der Rolle des Ziels steht.")
    if code == 404:
        return f"Nicht gefunden (404: {message}). Stimmen Server-ID, Kanal und Mitglied?"
    if code == 429:
        return "Discord bremst gerade zu viele Anfragen (429). Bitte kurz warten und erneut versuchen."
    if "disallowed intents" in low or "privileged intent" in low:
        return ("Dem Bot fehlt eine Berechtigung (Intent). Im Developer Portal unter Bot die 'Server Members "
                "Intent' (und ggf. 'Message Content Intent') einschalten.")
    return f"Discord-Fehler ({code}): {message or 'keine Details'}"


class DiscordClient:
    def __init__(self, token: str, timeout: float = 20, base: str = API):
        self.token = (token or "").strip()
        self.timeout = timeout
        self.base = base

    # ------------------------------------------------------------ HTTP

    def _request(self, method: str, path: str, body: dict | None = None, _retry: bool = True):
        if not self.token:
            raise DiscordError("Kein Bot-Token eingetragen (config.json → discord → bot_token).")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bot {self.token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return json.loads(raw.decode("utf-8")) if raw.strip() else None
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            message = raw
            retry_after = None
            try:
                parsed = json.loads(raw)
                message = parsed.get("message", raw)
                retry_after = parsed.get("retry_after")
            except Exception:
                pass
            if e.code == 429 and _retry:
                time.sleep(min(float(retry_after or 1.0), 5.0) + 0.1)
                return self._request(method, path, body, _retry=False)
            raise DiscordError(_explain(e.code, message)) from None
        except urllib.error.URLError as e:
            raise DiscordError(f"Discord ist nicht erreichbar ({getattr(e, 'reason', e)}). Besteht Internet?") from None
        except (TimeoutError, OSError) as e:
            raise DiscordError(f"Discord hat zu lange nicht geantwortet ({e}).") from None

    # ------------------------------------------------------------ Bot / Server

    def me(self) -> dict:
        return self._request("GET", "/users/@me")

    def guild(self, guild_id: str) -> dict:
        return self._request("GET", f"/guilds/{guild_id}")

    def channels(self, guild_id: str) -> list[dict]:
        return self._request("GET", f"/guilds/{guild_id}/channels") or []

    def roles(self, guild_id: str) -> list[dict]:
        return self._request("GET", f"/guilds/{guild_id}/roles") or []

    def members(self, guild_id: str, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        return self._request("GET", f"/guilds/{guild_id}/members?limit={limit}") or []

    # ------------------------------------------------------------ Nachrichten

    def messages(self, channel_id: str, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        return self._request("GET", f"/channels/{channel_id}/messages?limit={limit}") or []

    def send_message(self, channel_id: str, content: str) -> dict:
        return self._request("POST", f"/channels/{channel_id}/messages", {"content": content[:2000]})

    # ------------------------------------------------------------ Kanäle

    def create_channel(self, guild_id: str, name: str, type: int = 0, parent_id: str | None = None,
                       topic: str | None = None) -> dict:
        body = {"name": name, "type": type}
        if parent_id:
            body["parent_id"] = parent_id
        if topic:
            body["topic"] = topic
        return self._request("POST", f"/guilds/{guild_id}/channels", body)

    def modify_channel(self, channel_id: str, **fields) -> dict:
        return self._request("PATCH", f"/channels/{channel_id}", {k: v for k, v in fields.items() if v is not None})

    def delete_channel(self, channel_id: str) -> dict:
        return self._request("DELETE", f"/channels/{channel_id}")

    # ------------------------------------------------------------ Rollen

    def add_role(self, guild_id: str, user_id: str, role_id: str):
        return self._request("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")

    def remove_role(self, guild_id: str, user_id: str, role_id: str):
        return self._request("DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")

    # ------------------------------------------------------------ Moderation

    def kick(self, guild_id: str, user_id: str):
        return self._request("DELETE", f"/guilds/{guild_id}/members/{user_id}")

    def ban(self, guild_id: str, user_id: str, delete_message_seconds: int = 0):
        return self._request("PUT", f"/guilds/{guild_id}/bans/{user_id}",
                             {"delete_message_seconds": max(0, min(int(delete_message_seconds), 604800))})

    def timeout_member(self, guild_id: str, user_id: str, until_iso: str | None):
        return self._request("PATCH", f"/guilds/{guild_id}/members/{user_id}",
                             {"communication_disabled_until": until_iso})
