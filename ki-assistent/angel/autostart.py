"""Angel beim Anmelden automatisch starten (und wieder abschalten).

Windows: legt eine Verknüpfung im Autostart-Ordner an, die Angel ohne schwarzes Fenster startet.
Linux: legt eine .desktop-Datei unter ~/.config/autostart an. macOS: kurzer Hinweis.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent  # Ordner "ki-assistent"
APP_NAME = "Angel"


def _pythonw() -> str:
    """Python ohne Konsolenfenster (pythonw) wenn vorhanden, sonst normales Python."""
    exe = Path(sys.executable)
    if os.name == "nt":
        cand = exe.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


# --------------------------------------------------------------------------- Windows

def _win_startup_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _win_shortcut() -> Path:
    return _win_startup_dir() / f"{APP_NAME}.lnk"


def _win_enable() -> str:
    link = _win_shortcut()
    link.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        f"$s.TargetPath = '{_pythonw()}';"
        "$s.Arguments = '-m angel';"
        f"$s.WorkingDirectory = '{PROJECT_DIR}';"
        f"$s.Description = 'Angel – deine KI';"
        "$s.WindowStyle = 7;"
        "$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    return f"Autostart eingerichtet: {link}"


def _win_disable() -> str:
    link = _win_shortcut()
    if link.exists():
        link.unlink()
        return "Autostart entfernt."
    return "Autostart war nicht eingerichtet."


# --------------------------------------------------------------------------- Linux

def _linux_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "autostart" / "angel.desktop"


def _linux_enable() -> str:
    f = _linux_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    f.write_text(
        "[Desktop Entry]\nType=Application\nName=Angel\n"
        f"Exec={py} -m angel\nPath={PROJECT_DIR}\nX-GNOME-Autostart-enabled=true\nTerminal=false\n",
        encoding="utf-8")
    return f"Autostart eingerichtet: {f}"


def _linux_disable() -> str:
    f = _linux_file()
    if f.exists():
        f.unlink()
        return "Autostart entfernt."
    return "Autostart war nicht eingerichtet."


# --------------------------------------------------------------------------- öffentlich

def enable() -> str:
    if os.name == "nt":
        return _win_enable()
    if sys.platform == "darwin":
        return ("Automatischer Start unter macOS: Systemeinstellungen → Allgemein → Anmeldeobjekte → "
                "start.sh (bzw. 'python3 -m angel') hinzufügen.")
    return _linux_enable()


def disable() -> str:
    if os.name == "nt":
        return _win_disable()
    if sys.platform == "darwin":
        return "Bitte den Eintrag in den Anmeldeobjekten der macOS-Systemeinstellungen entfernen."
    return _linux_disable()


def is_enabled() -> bool:
    if os.name == "nt":
        return _win_shortcut().exists()
    if sys.platform == "darwin":
        return False
    return _linux_file().exists()
