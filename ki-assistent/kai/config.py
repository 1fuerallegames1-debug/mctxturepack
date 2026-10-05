"""Einstellungen laden: eingebaute Standardwerte + config.json des Benutzers."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

# Ordner "ki-assistent" (eine Ebene über dem Paket "kai")
PROJECT_DIR = Path(__file__).resolve().parent.parent

DEFAULTS = {
    # Wie heißt der Assistent und wie heißt du?
    "name": "Angel",
    "dein_name": "",
    # "Deutsch und Englisch" = antwortet in der Sprache, in der du schreibst
    "sprache": "Deutsch und Englisch",
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
    # true = Browser-Oberfläche auch vom Handy im selben WLAN erreichbar (siehe README)
    "handy_zugriff": False,
    "web_host": "127.0.0.1",
    "web_port": 8765,
    "browser_oeffnen": True,
    # Eigene Zusatz-Anweisungen an die KI (Persönlichkeit, Regeln, Wissen über dich ...)
    "zusatz_anweisungen": "",
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
            if "escape" in e.msg.lower():
                hint = ("\nTipp: In Pfaden jeden \\ doppelt schreiben (\"D:\\\\Server\") "
                        "oder / verwenden (\"D:/Server\").")
            raise ConfigError(
                f"Die Datei {path.name} enthält einen Fehler (Zeile {e.lineno}, Spalte {e.colno}): {e.msg}{hint}"
            ) from e
        if not isinstance(user, dict):
            raise ConfigError(f"{path.name} muss ein JSON-Objekt {{ ... }} enthalten.")
    cfg = _merge(DEFAULTS, user)

    # Umgebungsvariablen haben Vorrang (praktisch zum schnellen Testen)
    if os.environ.get("KAI_MODELL"):
        cfg["modell"] = os.environ["KAI_MODELL"]
    if os.environ.get("KAI_SERVER_URL"):
        cfg["server_url"] = os.environ["KAI_SERVER_URL"]

    cfg["data_dir"] = str(data_dir())
    return cfg


def data_dir() -> Path:
    """Ordner für Gedächtnis, Sicherungskopien usw. (KAI_DATEN überschreibt den Ort)."""
    d = Path(os.environ.get("KAI_DATEN") or (PROJECT_DIR / "daten"))
    d.mkdir(parents=True, exist_ok=True)
    return d


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
