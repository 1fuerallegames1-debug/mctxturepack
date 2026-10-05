"""Baut eine fertige Angel.zip zum Herunterladen: entpacken -> installieren.bat ausführen.

Aufruf:  python ki-assistent/werkzeuge/baue_release.py --out Angel.zip
Die ZIP enthält einen Ordner "Angel/" mit allem Nötigen (ohne deine privaten Daten).
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # Ordner "ki-assistent"
TOP = "Angel"  # oberster Ordner in der ZIP

INCLUDE_DIRS = ["angel", "plugins"]
INCLUDE_FILES = ["start.bat", "start-terminal.bat", "start.sh", "installieren.bat",
                 "autostart-ein.bat", "autostart-aus.bat", "config.beispiel.json", "README.md"]


def _keep(path: Path) -> bool:
    parts = set(path.parts)
    if "__pycache__" in parts:
        return False
    return path.suffix not in (".pyc", ".pyo")


def build(out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in INCLUDE_FILES:
            f = ROOT / name
            if f.exists():
                zf.write(f, f"{TOP}/{name}")
                count += 1
        for d in INCLUDE_DIRS:
            for f in sorted((ROOT / d).rglob("*")):
                if f.is_file() and _keep(f):
                    zf.write(f, f"{TOP}/{f.relative_to(ROOT).as_posix()}")
                    count += 1
    print(f"{out} erstellt ({count} Dateien, {out.stat().st_size // 1024} KB).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "Angel.zip"))
    build(Path(ap.parse_args().out))
