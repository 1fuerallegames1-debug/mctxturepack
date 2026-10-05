"""Startpunkt:  python -m angel  (eigenes Fenster)  oder  python -m angel --terminal  (Textfenster)."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config


def main(argv=None) -> int:
    if sys.version_info < (3, 9):
        print("Angel braucht Python 3.9 oder neuer. Download: https://www.python.org/downloads/")
        return 1
    parser = argparse.ArgumentParser(prog="angel", description="Angel – deine eigene KI auf deinem PC")
    parser.add_argument("--terminal", "--text", dest="terminal", action="store_true",
                        help="im schwarzen Textfenster statt im eigenen Fenster starten")
    parser.add_argument("--modell", "--model", dest="modell", help="KI-Modell für diese Sitzung (z. B. qwen3:8b)")
    parser.add_argument("--google-anmelden", dest="google_login", action="store_true",
                        help="einmalig bei Google anmelden (öffnet den Browser)")
    parser.add_argument("--autostart", choices=["ein", "aus", "status"],
                        help="Angel beim Anmelden automatisch starten (ein/aus/status)")
    parser.add_argument("--version", action="version", version=f"Angel {__version__}")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"Fehler in den Einstellungen: {e}")
        return 1
    if args.modell:
        cfg["modell"] = args.modell

    if args.autostart:
        from . import autostart
        try:
            if args.autostart == "status":
                print("Autostart ist " + ("EIN" if autostart.is_enabled() else "AUS") + ".")
            else:
                print(autostart.enable() if args.autostart == "ein" else autostart.disable())
            return 0
        except Exception as e:
            print(f"Autostart konnte nicht geändert werden: {e}")
            return 1

    if args.google_login:
        return _google_login(cfg)

    from .agent import Agent
    from .cli import TerminalChat, run_cli
    from .llm import LLMError

    try:
        agent = Agent(cfg)
    except LLMError as e:
        print(e)
        return 1

    use_window = not args.terminal and (cfg.get("oberflaeche") or "fenster").lower() == "fenster"
    if use_window:
        try:
            # Kurz den Status im Konsolenfenster zeigen (beim Autostart ohne Konsole einfach überspringen)
            TerminalChat(agent).startup_check(web=True)
        except Exception:
            pass
        try:
            from .gui import run_gui
        except Exception as e:
            print(f"Das Fenster konnte nicht geladen werden ({e}).")
            print("Starte stattdessen im Textfenster. (Tkinter fehlt? Es gehört zur Python-Installation "
                  "von python.org – dort beim Installieren 'tcl/tk and IDLE' aktiviert lassen.)")
            return run_cli(agent)
        return run_gui(agent)
    return run_cli(agent)


def _google_login(cfg) -> int:
    from pathlib import Path

    from .config import data_dir
    from .google_api import GoogleClient, GoogleError, login
    g = cfg.get("google") or {}
    if not g.get("client_id") or not g.get("client_secret"):
        print("Es fehlen client_id und client_secret in config.json (siehe README, Abschnitt 'Google').")
        return 1
    client = GoogleClient(g["client_id"], g["client_secret"], Path(data_dir()) / "google_token.json")
    try:
        email = login(client, g)
    except GoogleError as e:
        print(f"\nAnmeldung fehlgeschlagen: {e}")
        return 1
    print(f"\nFertig! Angel ist jetzt mit Google verbunden{(' (' + email + ')') if email else ''}.")
    print("Denk daran, in config.json  google → aktiv  auf true zu setzen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
