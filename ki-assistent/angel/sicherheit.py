"""Passwortschutz beim Start.

Beim Start verlangt Angel ein Passwort. Solange es nicht stimmt, tut Angel NICHTS
(weder Chat noch Werkzeuge). Es gibt fünf Versuche. Spätestens der fünfte muss
richtig sein – sonst zerstört Angel sich selbst: es löscht seine EIGENEN Daten
(Gedächtnis, gespeicherte Konto-Zugänge, Browser-Logins) und sperrt sich
dauerhaft. So kann niemand Fremdes Angel oder die verbundenen Konten benutzen.

Wichtig: Angel fasst dabei NUR den eigenen Datenordner an. Dateien, Programme
oder Einstellungen des restlichen Geräts werden nie verändert oder gelöscht –
das schützt dich davor, dass ein versehentlicher Fehlversuch deine eigenen
Sachen vernichtet.

Das Passwort steht nirgends im Klartext. Gespeichert ist nur ein gesalzener
PBKDF2-Hash; aus ihm lässt sich das Passwort praktisch nicht zurückrechnen.
Diese Datei gehört zum geschützten Programmkern – Angel selbst darf sie nicht
verändern (siehe regeln.py).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from pathlib import Path

# Höchstens so viele Fehlversuche, dann Selbstzerstörung.
MAX_VERSUCHE = 5

# Gesalzener PBKDF2-HMAC-SHA256-Hash des vom Besitzer gewählten Passworts.
# (Der Klartext taucht hier bewusst nicht auf.)
_SALT = bytes.fromhex("098b56be4f1a0c92ac4b6be9bab71bc6")
_ITERATIONEN = 240000
_ERWARTETER_HASH = "b090d3e7c69a6a2951f7b9701567556bae34592e99dba2d804a1b644225386be"

_STATUS_DATEI = "sicherheit.json"
_SPERR_DATEI = "gesperrt.marker"


def _hash(passwort: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", (passwort or "").encode("utf-8"), _SALT, _ITERATIONEN).hex()


def passwort_korrekt(passwort: str) -> bool:
    """True, wenn das eingegebene Passwort stimmt (zeitkonstanter Vergleich)."""
    return hmac.compare_digest(_hash(passwort), _ERWARTETER_HASH)


def _status_datei(data_dir: Path) -> Path:
    return Path(data_dir) / _STATUS_DATEI


def _sperr_datei(data_dir: Path) -> Path:
    return Path(data_dir) / _SPERR_DATEI


def ist_gesperrt(data_dir: Path) -> bool:
    """True, wenn Angel sich nach zu vielen Fehlversuchen selbst gesperrt hat."""
    return _sperr_datei(data_dir).exists()


def fehlversuche(data_dir: Path) -> int:
    try:
        n = int(json.loads(_status_datei(data_dir).read_text(encoding="utf-8")).get("fehlversuche", 0))
        return max(0, n)
    except (OSError, ValueError, TypeError):
        return 0


def verbleibende_versuche(data_dir: Path) -> int:
    return max(0, MAX_VERSUCHE - fehlversuche(data_dir))


def _schreibe_fehlversuche(data_dir: Path, n: int) -> None:
    p = _status_datei(data_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fehlversuche": int(n)}), encoding="utf-8")
    except OSError:
        pass


def registriere_fehlschlag(data_dir: Path) -> int:
    """Zählt einen Fehlversuch hoch und gibt die neue Gesamtzahl zurück."""
    n = fehlversuche(data_dir) + 1
    _schreibe_fehlversuche(data_dir, n)
    return n


def zuruecksetzen(data_dir: Path) -> None:
    """Nach erfolgreicher Anmeldung: Zähler auf 0."""
    _schreibe_fehlversuche(data_dir, 0)


def selbstzerstoerung(data_dir: Path) -> None:
    """Löscht AUSSCHLIESSLICH Angels eigenen Datenordner und sperrt den Start dauerhaft.

    Es wird nur der Inhalt von data_dir entfernt (Gedächtnis, Konto-Zugänge,
    Browser-Profil, Statusdateien). Nichts außerhalb dieses Ordners wird berührt.
    """
    data_dir = Path(data_dir)
    try:
        if data_dir.exists():
            for kind in list(data_dir.iterdir()):
                try:
                    if kind.is_dir() and not kind.is_symlink():
                        shutil.rmtree(kind, ignore_errors=True)
                    else:
                        kind.unlink()
                except OSError:
                    pass
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        _sperr_datei(data_dir).write_text("gesperrt", encoding="utf-8")
    except OSError:
        pass


# Rückgabewerte von pruefe_start / Codes für die melde-Rückmeldung
FREIGEGEBEN = "freigegeben"
ABGEBROCHEN = "abgebrochen"
GESPERRT = "gesperrt"
ZERSTOERT = "zerstoert"


def pruefe_start(data_dir: Path, frage_passwort, melde=None) -> str:
    """Führt die Passwortabfrage beim Start aus.

    frage_passwort(verbleibend:int) -> str | None
        Holt eine Passworteingabe. None bedeutet Abbruch (Fenster geschlossen o. Ä.).
    melde(code:str) -> None   (optional)
        Wird mit GESPERRT bzw. ZERSTOERT aufgerufen, um dem Benutzer Bescheid zu geben.

    Rückgabe: FREIGEGEBEN, ABGEBROCHEN, GESPERRT oder ZERSTOERT.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if ist_gesperrt(data_dir):
        if melde:
            melde(GESPERRT)
        return GESPERRT

    while True:
        rest = verbleibende_versuche(data_dir)
        if rest <= 0:
            selbstzerstoerung(data_dir)
            if melde:
                melde(ZERSTOERT)
            return ZERSTOERT

        eingabe = frage_passwort(rest)
        if eingabe is None:
            return ABGEBROCHEN  # Abbruch zählt NICHT als Fehlversuch

        if passwort_korrekt(eingabe):
            zuruecksetzen(data_dir)
            return FREIGEGEBEN

        n = registriere_fehlschlag(data_dir)
        if n >= MAX_VERSUCHE:
            selbstzerstoerung(data_dir)
            if melde:
                melde(ZERSTOERT)
            return ZERSTOERT
