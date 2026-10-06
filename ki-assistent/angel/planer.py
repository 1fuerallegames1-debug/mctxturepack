"""Termine, To-Dos und Erinnerungen – einmalig oder wiederkehrend ('jeden Montag').

Angel merkt sich geplante Aufgaben in daten/aufgaben.json. Ein Hintergrund-Thread
(Scheduler) prüft regelmäßig, was fällig ist, und meldet es:
  - typ "erinnerung": Angel erinnert dich nur.
  - typ "auftrag":    Angel führt die Aufgabe selbst aus (z. B. Nachricht schicken).

Das Umrechnen der Wörter ('jeden Montag morgen um 8') in die Felder (Uhrzeit,
Wiederholung, Wochentag) macht das KI-Modell beim Anlegen; hier liegt nur die
saubere, prüfbare Logik. Geplante Aufgaben laufen nur, solange Angel läuft –
Angel ist dafür im Autostart.
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path

WIEDERHOLUNGEN = ("", "einmalig", "taeglich", "werktags", "wochenende", "woechentlich", "monatlich")


def _parse_uhrzeit(uhrzeit, fallback=(9, 0)) -> tuple:
    try:
        h, _, m = str(uhrzeit or "").strip().partition(":")
        hh, mm = int(h), int(m or 0)
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh, mm
    except (ValueError, AttributeError):
        pass
    return fallback


def _parse_datum(datum):
    if not datum:
        return None
    try:
        return date.fromisoformat(str(datum)[:10])
    except ValueError:
        return None


def _iso_datum(datum):
    d = _parse_datum(datum)
    return d.isoformat() if d else None


def _tag_passt(d: date, wiederholung: str, wochentag, ziel_tag) -> bool:
    if wiederholung == "taeglich":
        return True
    if wiederholung == "werktags":
        return d.weekday() < 5
    if wiederholung == "wochenende":
        return d.weekday() >= 5
    if wiederholung == "woechentlich":
        return wochentag is not None and d.weekday() == int(wochentag)
    if wiederholung == "monatlich":
        return ziel_tag is not None and d.day == ziel_tag
    return False


def naechste_ausfuehrung(now: datetime, uhrzeit="", wiederholung="", wochentag=None, datum=None):
    """Nächster Zeitpunkt als datetime – oder None, wenn einmalig & in der Vergangenheit."""
    h, m = _parse_uhrzeit(uhrzeit)
    w = (wiederholung or "").strip().lower()
    if w in ("", "einmalig"):
        tag = _parse_datum(datum)
        if tag is None:  # 'heute' – falls die Uhrzeit schon vorbei ist, morgen
            kand = datetime.combine(now.date(), time(h, m))
            return kand if kand > now else kand + timedelta(days=1)
        kand = datetime.combine(tag, time(h, m))  # festes Datum: in der Vergangenheit -> None
        return kand if kand > now else None
    ziel_tag = None
    if w == "monatlich":
        tag = _parse_datum(datum)
        ziel_tag = tag.day if tag else now.day
    for offset in range(0, 400):
        d = now.date() + timedelta(days=offset)
        if _tag_passt(d, w, wochentag, ziel_tag):
            kand = datetime.combine(d, time(h, m))
            if kand > now:
                return kand
    return None


# --------------------------------------------------------------------------- Speicher

_STORES: dict = {}
_STORES_LOCK = threading.Lock()


def hole_aufgaben(path) -> "Aufgaben":
    """Gibt den (geteilten) Aufgaben-Speicher für diesen Pfad zurück."""
    key = str(Path(path).resolve())
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = Aufgaben(path)
        return _STORES[key]


class Aufgaben:
    """Liest und schreibt die Aufgabenliste (daten/aufgaben.json)."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _laden(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _speichern(self, tasks: list) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def liste(self, nur_aktiv=True) -> list:
        with self._lock:
            tasks = self._laden()
        return [t for t in tasks if t.get("aktiv", True)] if nur_aktiv else tasks

    def hinzufuegen(self, text, typ="erinnerung", uhrzeit="", wiederholung="",
                    wochentag=None, datum=None, now=None) -> dict:
        now = now or datetime.now()
        naechste = naechste_ausfuehrung(now, uhrzeit, wiederholung, wochentag, datum)
        if naechste is None:
            raise ValueError("Der Zeitpunkt liegt in der Vergangenheit – bitte einen späteren wählen.")
        wt = None
        if wochentag is not None and str(wochentag).lstrip("-").isdigit() and 0 <= int(wochentag) <= 6:
            wt = int(wochentag)
        task = {
            "id": secrets.token_hex(4),
            "text": (text or "").strip(),
            "typ": "auftrag" if typ == "auftrag" else "erinnerung",
            "uhrzeit": "%02d:%02d" % _parse_uhrzeit(uhrzeit),
            "wiederholung": (wiederholung or "").strip().lower(),
            "wochentag": wt,
            "datum": _iso_datum(datum),
            "naechste": naechste.isoformat(timespec="minutes"),
            "aktiv": True,
            "erstellt": now.isoformat(timespec="minutes"),
        }
        with self._lock:
            tasks = self._laden()
            tasks.append(task)
            self._speichern(tasks)
        return task

    def entfernen(self, task_id) -> bool:
        with self._lock:
            tasks = self._laden()
            neu = [t for t in tasks if t.get("id") != task_id]
            self._speichern(neu)
            return len(neu) != len(tasks)

    def faellige(self, now=None) -> list:
        now = now or datetime.now()
        res = []
        for t in self.liste(nur_aktiv=True):
            try:
                if datetime.fromisoformat(t["naechste"]) <= now:
                    res.append(t)
            except (KeyError, ValueError):
                pass
        return res

    def nach_ausfuehrung(self, task_id, now=None) -> None:
        """Nach dem Auslösen: wiederkehrende Aufgaben neu terminieren, einmalige abschalten."""
        now = now or datetime.now()
        with self._lock:
            tasks = self._laden()
            for t in tasks:
                if t.get("id") != task_id:
                    continue
                w = (t.get("wiederholung") or "").strip().lower()
                if w in ("", "einmalig"):
                    t["aktiv"] = False
                else:
                    nxt = naechste_ausfuehrung(now, t.get("uhrzeit", ""), w, t.get("wochentag"), t.get("datum"))
                    if nxt is None:
                        t["aktiv"] = False
                    else:
                        t["naechste"] = nxt.isoformat(timespec="minutes")
                break
            self._speichern(tasks)


# --------------------------------------------------------------------------- Hintergrund

class Scheduler(threading.Thread):
    """Prüft regelmäßig, welche Aufgaben fällig sind, und meldet sie über Rückrufe.

    on_erinnerung(task): zeigt eine Erinnerung.
    on_auftrag(task) -> bool: führt die Aufgabe aus; False = gerade beschäftigt,
        dann bleibt die Aufgabe fällig und wird beim nächsten Durchlauf erneut versucht.
    """

    def __init__(self, store: "Aufgaben", on_erinnerung, on_auftrag, intervall=30, jetzt=None):
        super().__init__(daemon=True, name="planer")
        self.store = store
        self.on_erinnerung = on_erinnerung
        self.on_auftrag = on_auftrag
        self.intervall = max(5, int(intervall))
        self._stop = threading.Event()
        self._jetzt = jetzt or datetime.now

    def tick(self, now=None) -> None:
        now = now or self._jetzt()
        for task in self.store.faellige(now):
            erledigt = True
            if task.get("typ") == "auftrag":
                try:
                    erledigt = bool(self.on_auftrag(task))
                except Exception:
                    erledigt = True  # nicht endlos wiederholen, wenn der Rückruf selbst scheitert
            else:
                try:
                    self.on_erinnerung(task)
                except Exception:
                    pass
            if erledigt:
                self.store.nach_ausfuehrung(task["id"], now)

    def run(self) -> None:
        while not self._stop.wait(self.intervall):
            try:
                self.tick()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
