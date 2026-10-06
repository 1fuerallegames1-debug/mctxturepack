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

    # --autostart richtet nur die Startverknüpfung ein (kein Kontozugriff, keine Nutzung von
    # Angel) und bleibt daher ohne Passwort, damit der Ein-Klick-Installer nicht blockiert.
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

    # Passwortschloss: ab hier wird Angel wirklich benutzt oder greift auf Konten zu. Es läuft
    # VOR der Google-Anmeldung und VOR dem Aufbau des Agents – vorher tut Angel nichts.
    gate_fenster = (not args.terminal and not args.google_login
                    and (cfg.get("oberflaeche") or "fenster").lower() == "fenster")
    if not _passwort_gate(gate_fenster):
        return 0

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
            return run_gui(agent)
        except Exception as e:
            print(f"Das Fenster konnte nicht geladen werden ({e}).")
            print("Starte stattdessen im Textfenster. (Tkinter fehlt? Es gehört zur Python-Installation "
                  "von python.org – dort beim Installieren 'tcl/tk and IDLE' aktiviert lassen.)")
            return run_cli(agent)
    return run_cli(agent)


def _passwort_gate(fenster: bool) -> bool:
    """Fragt das Startpasswort ab. Gibt nur True zurück, wenn es stimmt."""
    from . import sicherheit
    from .config import data_dir
    dd = data_dir()
    if fenster:
        try:
            return _gate_fenster(dd, sicherheit)
        except Exception:
            pass  # kein Tkinter/keine Anzeige -> auf die Konsole ausweichen
    return _gate_konsole(dd, sicherheit)


def _gate_konsole(dd, sicherheit) -> bool:
    import getpass

    def frage(rest):
        try:
            return getpass.getpass(f"Passwort (noch {rest} Versuch(e), danach loescht sich Angel): ")
        except Exception:
            return None  # keine Eingabe moeglich (kein Terminal) -> sicherheitshalber nicht starten

    def melde(code):
        if code == sicherheit.GESPERRT:
            print("Angel ist gesperrt und hat seine Daten geloescht. Bitte Angel neu installieren.")
        elif code == sicherheit.ZERSTOERT:
            print("Fuenf falsche Passwoerter. Angel hat seine eigenen Daten geloescht und sich gesperrt.")

    return sicherheit.pruefe_start(dd, frage, melde) == sicherheit.FREIGEGEBEN


def _gate_fenster(dd, sicherheit) -> bool:
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    root = tk.Tk()
    root.withdraw()
    try:
        def frage(rest):
            return simpledialog.askstring(
                "Angel – Passwort",
                "Bitte Passwort eingeben.\n"
                f"Noch {rest} Versuch(e) – danach löscht Angel seine eigenen Daten und sperrt sich.",
                show="*", parent=root)

        def melde(code):
            if code == sicherheit.GESPERRT:
                messagebox.showerror(
                    "Angel ist gesperrt",
                    "Angel wurde nach zu vielen falschen Passwörtern gesperrt und hat seine eigenen "
                    "Daten gelöscht. Bitte Angel neu installieren.", parent=root)
            elif code == sicherheit.ZERSTOERT:
                messagebox.showerror(
                    "Angel hat sich gesperrt",
                    "Fünf falsche Passwörter. Angel hat seine eigenen Daten (Gedächtnis, Konto-Zugänge, "
                    "Browser-Logins) gelöscht und sich dauerhaft gesperrt.", parent=root)

        return sicherheit.pruefe_start(dd, frage, melde) == sicherheit.FREIGEGEBEN
    finally:
        root.destroy()


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
