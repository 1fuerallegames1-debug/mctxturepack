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
    parser.add_argument("--version", action="version", version=f"Angel {__version__}")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"Fehler in den Einstellungen: {e}")
        return 1
    if args.modell:
        cfg["modell"] = args.modell

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
        # Vor dem Fenster kurz prüfen, ob der KI-Server läuft (verständliche Meldung im Terminal)
        if not TerminalChat(agent).startup_check(web=True):
            return 1
        try:
            from .gui import run_gui
        except Exception as e:
            print(f"Das Fenster konnte nicht geladen werden ({e}).")
            print("Starte stattdessen im Textfenster. (Tkinter fehlt? Es gehört zur Python-Installation "
                  "von python.org – dort beim Installieren 'tcl/tk and IDLE' aktiviert lassen.)")
            return run_cli(agent)
        return run_gui(agent)
    return run_cli(agent)


if __name__ == "__main__":
    sys.exit(main())
