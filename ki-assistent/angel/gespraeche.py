"""Gespeicherte Gespräche (Chat-Verlauf) – verschlüsselt auf der Festplatte.

Jeder Chat liegt als eigene Datei in daten/gespraeche/. Normale Chats sind mit einem
lokalen Schlüssel (daten/gespraeche/.schluessel) verschlüsselt. Einzelne Chats kann man
zusätzlich mit einem selbst gewählten Passwort SPERREN – dann wird der Chat mit einem aus
dem Passwort abgeleiteten Schlüssel verschlüsselt, und man braucht das Passwort zum Öffnen.
Das Passwort selbst wird nie gespeichert.

Verschlüsselt wird mit der Bibliothek 'cryptography' (Fernet = AES). Fehlt sie, speichert
Angel die Chats als Klartext und meldet das – der Installer installiert sie aber mit.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path

try:  # echte Verschlüsselung, falls vorhanden
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _CRYPTO = True
except Exception:  # pragma: no cover - nur ohne installierte Bibliothek
    _CRYPTO = False
    InvalidToken = Exception  # type: ignore

_ITER = 240000


class GespraechFehler(Exception):
    """Chat kann nicht geladen werden (gesperrt oder falsches Passwort)."""


def verschluesselung_verfuegbar() -> bool:
    return _CRYPTO


# --------------------------------------------------------------------------- Krypto-Helfer

def key_aus_passwort(passwort: str, salt: bytes) -> bytes:
    """Leitet aus einem Passwort einen Fernet-Schlüssel ab (PBKDF2-SHA256)."""
    if not _CRYPTO:
        return b""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ITER)
    return base64.urlsafe_b64encode(kdf.derive(passwort.encode("utf-8")))


def _schreibe_blob(pfad: Path, obj, key: bytes | None) -> None:
    roh = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if key and _CRYPTO:
        pfad.write_bytes(b"E1\n" + Fernet(key).encrypt(roh))
    else:
        pfad.write_bytes(b"P1\n" + roh)


def _lese_blob(pfad: Path, key: bytes | None):
    b = pfad.read_bytes()
    if b.startswith(b"E1\n"):
        if not (_CRYPTO and key):
            raise GespraechFehler("Chat ist verschlüsselt – Schlüssel/Passwort nötig.")
        try:
            roh = Fernet(key).decrypt(b[3:])
        except InvalidToken:
            raise GespraechFehler("Falsches Passwort oder beschädigter Chat.") from None
        return json.loads(roh.decode("utf-8"))
    if b.startswith(b"P1\n"):
        return json.loads(b[3:].decode("utf-8"))
    return json.loads(b.decode("utf-8"))  # ganz altes Klartext-Format


# --------------------------------------------------------------------------- Verwaltung

class Gespraeche:
    def __init__(self, data_dir):
        self.ordner = Path(data_dir) / "gespraeche"
        self.ordner.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._key = self._lokaler_schluessel()

    # -- lokaler Schlüssel (für normale, nicht gesperrte Chats) --
    def _lokaler_schluessel(self) -> bytes | None:
        if not _CRYPTO:
            return None
        p = self.ordner / ".schluessel"
        try:
            if p.exists():
                return p.read_bytes()
            key = Fernet.generate_key()
            p.write_bytes(key)
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            return key
        except OSError:
            return None

    def lokaler_key(self) -> bytes | None:
        return self._key

    # -- Index (Liste aller Chats) --
    def _index_pfad(self) -> Path:
        return self.ordner / "index"

    def _lade_index(self) -> list:
        p = self._index_pfad()
        if not p.exists():
            return []
        try:
            data = _lese_blob(p, self._key)
            return data if isinstance(data, list) else []
        except (OSError, ValueError, GespraechFehler):
            return []

    def _speichere_index(self, index: list) -> None:
        try:
            _schreibe_blob(self._index_pfad(), index, self._key)
        except OSError:
            pass

    def _chat_pfad(self, chat_id: str) -> Path:
        return self.ordner / f"{chat_id}.chat"

    # -- öffentliche API --
    def liste(self) -> list:
        """Alle Chats (neueste zuerst): [{id, titel, geaendert, gesperrt}]."""
        with self._lock:
            index = self._lade_index()
        return sorted(index, key=lambda e: e.get("geaendert", ""), reverse=True)

    def ist_gesperrt(self, chat_id: str) -> bool:
        with self._lock:
            for e in self._lade_index():
                if e.get("id") == chat_id:
                    return bool(e.get("gesperrt"))
        return False

    def neu(self) -> str:
        return secrets.token_hex(6)

    def _titel_aus(self, nachrichten: list, fallback: str = "Neuer Chat") -> str:
        for m in nachrichten:
            if m.get("role") == "user" and (m.get("content") or "").strip():
                t = " ".join(m["content"].split())
                return (t[:40] + "…") if len(t) > 41 else t
        return fallback

    def speichern(self, chat_id: str, nachrichten: list, key: bytes | None, titel: str | None = None) -> None:
        """Schreibt den Chat (mit 'key' verschlüsselt) und aktualisiert den Index."""
        with self._lock:
            index = self._lade_index()
            eintrag = next((e for e in index if e.get("id") == chat_id), None)
            jetzt = datetime.now().isoformat()  # mit Mikrosekunden -> eindeutige Reihenfolge
            gesperrt = bool(eintrag and eintrag.get("gesperrt"))
            try:
                _schreibe_blob(self._chat_pfad(chat_id), {"nachrichten": nachrichten}, key)
            except OSError:
                return
            titel = titel or (eintrag.get("titel") if eintrag else None) or self._titel_aus(nachrichten)
            if eintrag is None:
                index.append({"id": chat_id, "titel": titel, "erstellt": jetzt, "geaendert": jetzt,
                              "gesperrt": gesperrt})
            else:
                eintrag["titel"] = titel
                eintrag["geaendert"] = jetzt
            self._speichere_index(index)

    def laden(self, chat_id: str, key: bytes | None) -> list:
        """Lädt die Nachrichten eines Chats. Wirft GespraechFehler bei falschem Passwort."""
        p = self._chat_pfad(chat_id)
        if not p.exists():
            return []
        data = _lese_blob(p, key)
        return (data or {}).get("nachrichten", []) if isinstance(data, dict) else []

    def loeschen(self, chat_id: str) -> None:
        with self._lock:
            index = [e for e in self._lade_index() if e.get("id") != chat_id]
            self._speichere_index(index)
        try:
            self._chat_pfad(chat_id).unlink(missing_ok=True)
        except OSError:
            pass

    # -- Sperren / Entsperren --
    def sperren(self, chat_id: str, passwort: str, aktueller_key: bytes | None) -> bytes:
        """Verschlüsselt den Chat mit einem Passwort. Gibt den neuen Schlüssel zurück."""
        if not _CRYPTO:
            raise GespraechFehler("Verschlüsselung ist nicht verfügbar (Bibliothek fehlt).")
        nachrichten = self.laden(chat_id, aktueller_key)
        salt = secrets.token_bytes(16)
        key = key_aus_passwort(passwort, salt)
        with self._lock:
            index = self._lade_index()
            eintrag = next((e for e in index if e.get("id") == chat_id), None)
            if eintrag is None:
                raise GespraechFehler("Chat nicht gefunden.")
            _schreibe_blob(self._chat_pfad(chat_id), {"nachrichten": nachrichten}, key)
            eintrag["gesperrt"] = True
            eintrag["salt"] = base64.b64encode(salt).decode("ascii")
            eintrag["pw_check"] = Fernet(key).encrypt(b"angel").decode("ascii")
            self._speichere_index(index)
        return key

    def entsperren_key(self, chat_id: str, passwort: str) -> bytes:
        """Prüft das Passwort und gibt den Schlüssel zurück (GespraechFehler bei falschem)."""
        if not _CRYPTO:
            raise GespraechFehler("Verschlüsselung ist nicht verfügbar.")
        with self._lock:
            eintrag = next((e for e in self._lade_index() if e.get("id") == chat_id), None)
        if not eintrag or not eintrag.get("gesperrt"):
            raise GespraechFehler("Chat ist nicht gesperrt.")
        salt = base64.b64decode(eintrag.get("salt", ""))
        key = key_aus_passwort(passwort, salt)
        try:
            if Fernet(key).decrypt(eintrag.get("pw_check", "").encode("ascii")) != b"angel":
                raise GespraechFehler("Falsches Passwort.")
        except InvalidToken:
            raise GespraechFehler("Falsches Passwort.") from None
        return key

    def entsperren(self, chat_id: str, aktueller_key: bytes | None) -> None:
        """Hebt die Sperre auf (zurück auf lokale Verschlüsselung)."""
        nachrichten = self.laden(chat_id, aktueller_key)
        with self._lock:
            index = self._lade_index()
            eintrag = next((e for e in index if e.get("id") == chat_id), None)
            if eintrag is None:
                return
            _schreibe_blob(self._chat_pfad(chat_id), {"nachrichten": nachrichten}, self._key)
            eintrag["gesperrt"] = False
            eintrag.pop("salt", None)
            eintrag.pop("pw_check", None)
            self._speichere_index(index)
