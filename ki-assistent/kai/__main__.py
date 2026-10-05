"""Startpunkt:  python -m kai  (Terminal)  oder  python -m kai --web  (Browser)."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config


def main(argv=None) -> int:
    if sys.version_info < (3, 9):
        print("Kai braucht Python 3.9 oder neuer. Download: https://www.python.org/downloads/")
        return 1
    parser = argparse.ArgumentParser(prog="kai", description="Kai – dein lokaler KI-Assistent")
    parser.add_argument("--web", action="store_true", help="Oberfläche im Browser statt im Terminal")
    parser.add_argument("--modell", "--model", dest="modell", help="KI-Modell für diese Sitzung (z. B. qwen3:8b)")
    parser.add_argument("--port", type=int, help="Port für die Browser-Oberfläche")
    parser.add_argument("--kein-browser", action="store_true", help="Browser nicht automatisch öffnen")
    parser.add_argument("--version", action="version", version=f"Kai {__version__}")
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

    if args.web:
        from .web import run_web
        if not TerminalChat(agent).startup_check():
            return 1
        return run_web(agent, open_browser=cfg.get("browser_oeffnen", True) and not args.kein_browser,
                       port=args.port)
    return run_cli(agent)


if __name__ == "__main__":
    sys.exit(main())
