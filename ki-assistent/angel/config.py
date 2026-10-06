"""Einstellungen laden: eingebaute Standardwerte + config.json des Benutzers."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

# Ordner "ki-assistent" (eine Ebene über dem Paket "angel")
PROJECT_DIR = Path(__file__).resolve().parent.parent

DEFAULTS = {
    # Wie heißt der Assistent und wie heißt du?
    "name": "Angel",
    "dein_name": "",
    # "Deutsch und Englisch" = antwortet in der Sprache, in der du schreibst
    "sprache": "Deutsch und Englisch",
    # "weiblich", "männlich" oder "neutral" – wie die KI im Deutschen von sich selbst spricht
    "geschlecht": "weiblich",
    # Woher kommt das KI-Modell? "ollama" (empfohlen) oder "openai"
    # (= jeder OpenAI-kompatible lokale Server, z. B. LM Studio oder llama.cpp)
    "anbieter": "ollama",
    "modell": "qwen3:8b",
    "server_url": "http://127.0.0.1:11434",
    "api_schluessel": "",
    # Wie viele Tokens das Modell auf einmal "im Kopf" behält.
    # Mehr = besseres Gedächtnis im Gespräch, braucht aber mehr (Grafik-)Speicher.
    "kontext_laenge": 16384,
    "temperatur": 0.6,
    # null = Standard des Modells, true/false = Nachdenken (Reasoning) an/aus
    "denken": None,
    "denken_anzeigen": False,
    # "nachfragen" = vor jeder Aktion am PC um Erlaubnis bitten (empfohlen)
    # "automatisch" = alles ohne Rückfrage ausführen (auf eigene Gefahr!)
    "bestaetigung": "nachfragen",
    # Werkzeuge, die nie nachfragen sollen, z. B. ["open_item"]
    "immer_erlauben": [],
    # Werkzeuge, die komplett abgeschaltet sein sollen, z. B. ["run_python"]
    "deaktivierte_werkzeuge": [],
    "max_schritte": 25,
    "befehl_timeout": 120,
    "max_ausgabe_zeichen": 8000,
    # Standard-Ordner für Befehle ("" = dein Benutzerordner)
    "arbeitsordner": "",
    "websuche": {"anbieter": "duckduckgo", "searxng_url": ""},
    # Bilder/Videos verstehen: lokales Seh-Modell über Ollama (einmalig: ollama pull llava).
    # Für Videos zusätzlich ffmpeg. Ein anderes Seh-Modell (z. B. "qwen2.5vl") hier eintragen.
    # "aktiv": false schaltet die Seh-Werkzeuge (und den 📎-Knopf) ab.
    "sehen": {"aktiv": True, "modell": "llava"},
    # Oberfläche: "fenster" = eigenes PC-Programm (empfohlen), "terminal" = schwarzes Textfenster
    "oberflaeche": "fenster",
    # Spracheingabe (du sprichst, Angel führt aus)
    "sprachsteuerung": {
        "aktiv": True,
        # Offline-Spracherkennung mit Vosk. Modell wird beim ersten Mal heruntergeladen.
        "modell_url": "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip",
        "automatisch_senden": False,  # true = nach dem Sprechen sofort abschicken
    },
    # Eigene Zusatz-Anweisungen an die KI (Persönlichkeit, Regeln, Wissen über dich ...)
    "zusatz_anweisungen": "",
    # Google-Konto (optional). Einrichtung siehe README, Abschnitt "Google".
    "google": {
        "aktiv": False,
        "client_id": "",        # aus deinem Google-Cloud-Projekt (OAuth, Desktop-App)
        "client_secret": "",
    },
    # Eigenes, automatisiertes Browserfenster (optional). Einrichtung siehe README, Abschnitt "Browser".
    "browser": {
        "aktiv": False,
        "sichtbar": True,       # true = Fenster sichtbar, false = im Hintergrund
    },
    # Discord-Bot (optional). Einrichtung siehe README, Abschnitt "Discord".
    "discord": {
        "aktiv": False,
        "bot_token": "",            # Token deines eigenen Bots (geheim halten!)
        "server_id": "",            # ID deines Servers (Guild), auf dem Angel arbeiten darf
        "erlaubte_kanaele": [],     # leer = alle Kanäle; sonst nur diese (Namen oder IDs)
        "nur_besitzer": True,       # nur du (besitzer_discord_id) darfst Befehle geben
        "besitzer_discord_id": "",
    },
}

CONFIG_FILE = PROJECT_DIR / "config.json"
EXAMPLE_FILE = PROJECT_DIR / "config.beispiel.json"


class ConfigError(Exception):
    pass


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key.startswith("_"):
            continue  # Kommentarfelder wie "_hinweis" ignorieren
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | None = None) -> dict:
    """Lädt die Einstellungen. Legt beim ersten Start config.json aus der Vorlage an."""
    path = Path(path) if path else CONFIG_FILE
    if not path.exists() and path == CONFIG_FILE and EXAMPLE_FILE.exists():
        shutil.copyfile(EXAMPLE_FILE, path)
    user = {}
    if path.exists():
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", "replace")  # im Editor als "ANSI" gespeichert
        try:
            user = json.loads(text)
        except json.JSONDecodeError as e:
            hint = ""
            msg = e.msg.lower()
            if "expecting ',' delimiter" in msg:
                hint = "\nTipp: Vermutlich fehlt am Ende der Zeile davor ein Komma."
            elif "property name" in msg:
                hint = "\nTipp: Überzähliges Komma vor } oder fehlende Anführungszeichen um einen Namen."
            elif "expecting value" in msg:
                hint = "\nTipp: Ein Wert fehlt (z. B. \"\" für leeren Text) oder es steht ein Komma zu viel."
            elif "escape" in msg:
                hint = ("\nTipp: In Pfaden jeden \\ doppelt schreiben (\"D:\\\\Server\") "
                        "oder / verwenden (\"D:/Server\").")
            raise ConfigError(
                f"Die Datei {path.name} enthält einen Fehler (Zeile {e.lineno}, Spalte {e.colno}): {e.msg}{hint}"
            ) from e
        if not isinstance(user, dict):
            raise ConfigError(f"{path.name} muss ein JSON-Objekt {{ ... }} enthalten.")
    cfg = _merge(DEFAULTS, user)

    # Umgebungsvariablen haben Vorrang (praktisch zum schnellen Testen)
    if os.environ.get("ANGEL_MODELL"):
        cfg["modell"] = os.environ["ANGEL_MODELL"]
    if os.environ.get("ANGEL_SERVER_URL"):
        cfg["server_url"] = os.environ["ANGEL_SERVER_URL"]

    cfg["data_dir"] = str(data_dir())
    return cfg


def data_dir() -> Path:
    """Ordner für Gedächtnis, Sicherungskopien usw. (ANGEL_DATEN überschreibt den Ort)."""
    d = Path(os.environ.get("ANGEL_DATEN") or (PROJECT_DIR / "daten"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sehen_aktiv(cfg: dict) -> bool:
    """Seh-Funktion verfügbar? Nur wenn eingeschaltet UND das Modell über Ollama läuft
    (das Seh-Plugin spricht das Ollama-Format; andere Anbieter werden hier nicht unterstützt)."""
    if not (cfg.get("sehen") or {}).get("aktiv", True):
        return False
    return (cfg.get("anbieter") or "ollama").strip().lower() == "ollama"


def working_dir(cfg: dict) -> Path:
    raw = (cfg.get("arbeitsordner") or "").strip()
    p = Path(os.path.expandvars(os.path.expanduser(raw))) if raw else Path.home()
    return p if p.is_dir() else Path.home()


def save_setting(key: str, value, path: Path | None = None):
    """Ändert einen einzelnen Wert in config.json (der Rest der Datei bleibt erhalten)."""
    path = Path(path) if path else CONFIG_FILE
    data = {}
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    data[key] = value
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)
