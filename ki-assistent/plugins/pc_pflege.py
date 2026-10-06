"""PC säubern und Viren prüfen.

- Aufräumen: löscht ausschließlich temporäre Dateien (TEMP-Ordner) und leert den
  Papierkorb. Deine eigenen Dokumente/Bilder werden NIE angefasst.
- Viren: Angel steuert den echten, in Windows eingebauten Virenschutz
  (Microsoft Defender) über die offiziellen PowerShell-Befehle – es wird kein
  eigener, unzuverlässiger Scanner gebaut.
"""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from angel.tools import ToolError, tool


def _mb(bytes_: int) -> str:
    mb = bytes_ / (1024 * 1024)
    return f"{mb/1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _temp_ordner() -> list:
    kandidaten = [tempfile.gettempdir()]
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA")
        if la:
            kandidaten.append(str(Path(la) / "Temp"))
        win = os.environ.get("WINDIR")
        if win:
            kandidaten.append(str(Path(win) / "Temp"))
    ordner, gesehen = [], set()
    for k in kandidaten:
        try:
            p = Path(k).resolve()
        except OSError:
            continue
        if p.is_dir() and str(p) not in gesehen:
            gesehen.add(str(p))
            ordner.append(p)
    return ordner


def _ordnergroesse(pfad: Path) -> int:
    gesamt = 0
    for root, _dirs, files in os.walk(pfad):
        for f in files:
            try:
                gesamt += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return gesamt


def _aufraeumen_ordner(ordner: Path, max_alter_tage: float = 1) -> tuple:
    """Löscht Dateien/Unterordner, die älter als max_alter_tage sind. Gibt (anzahl, bytes) zurück.

    Zu frische Einträge werden übersprungen (könnten gerade benutzt werden); gesperrte Dateien
    werden einfach ausgelassen.
    """
    if not ordner.exists():
        return 0, 0
    grenze = time.time() - max_alter_tage * 86400
    anzahl = frei = 0
    for kind in list(ordner.iterdir()):
        try:
            st = kind.stat()
        except OSError:
            continue
        if st.st_mtime > grenze:
            continue
        try:
            if kind.is_dir() and not kind.is_symlink():
                groesse = _ordnergroesse(kind)
                shutil.rmtree(kind, ignore_errors=True)
                if not kind.exists():
                    anzahl += 1
                    frei += groesse
            else:
                groesse = st.st_size
                kind.unlink()
                anzahl += 1
                frei += groesse
        except OSError:
            continue
    return anzahl, frei


def _powershell(ps: str, timeout: int) -> str:
    if os.name != "nt":
        raise ToolError("Das geht nur unter Windows (hier läuft der Microsoft Defender).")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ToolError("PowerShell wurde nicht gefunden.") from None
    except subprocess.TimeoutExpired:
        raise ToolError("Zeitüberschreitung – der Vorgang hat zu lange gedauert.") from None
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        raise ToolError(err or out or f"Defender-Befehl fehlgeschlagen (Code {r.returncode}).")
    return out


# --------------------------------------------------------------------------- Aufräumen

_ENERGIE = {
    "neustart": (["shutdown", "/r", "/t", "0"], "PC neu starten"),
    "herunterfahren": (["shutdown", "/s", "/t", "0"], "PC herunterfahren"),
    "abmelden": (["shutdown", "/l"], "Benutzer abmelden"),
    "sperren": (["rundll32.exe", "user32.dll,LockWorkStation"], "Bildschirm sperren"),
    "schlaf": (["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "PC in den Ruhezustand"),
}


def _energie_confirm(ctx, args):
    # Neustart/Herunterfahren/Abmelden schliessen Programme -> immer kurz bestaetigen.
    a = str(args.get("aktion", "")).lower()
    return "always" if a in ("neustart", "herunterfahren", "abmelden") else False


@tool(
    "pc_energie",
    "Restart, shut down, log off, lock or put the Windows PC to sleep. When the owner tells you to do any of "
    "these ('starte meinen PC neu', 'fahr runter', 'sperr den Bildschirm'), use THIS tool and just do it - "
    "do NOT explain Start-menu steps.",
    {"aktion": {"type": "string", "enum": ["neustart", "herunterfahren", "abmelden", "sperren", "schlaf"],
                "description": "neustart=restart, herunterfahren=shutdown, abmelden=log off, sperren=lock, "
                               "schlaf=sleep."}},
    required=["aktion"],
    confirm=_energie_confirm,
    summary=lambda a: _ENERGIE.get(str(a.get("aktion", "")).lower(), (None, "PC-Energieaktion"))[1],
)
def pc_energie(ctx, aktion):
    a = str(aktion or "").lower()
    if a not in _ENERGIE:
        raise ToolError("Unbekannte Aktion. Möglich: neustart, herunterfahren, abmelden, sperren, schlaf.")
    if os.name != "nt":
        raise ToolError("Das geht nur unter Windows.")
    cmd, beschreibung = _ENERGIE[a]
    try:
        subprocess.Popen(cmd)
    except OSError as e:
        raise ToolError(f"Konnte die Aktion nicht ausführen: {e}") from None
    return f"{beschreibung} wurde ausgelöst."


@tool("speicherplatz", "Show free and used disk space on the main drive.", {})
def speicherplatz(ctx):
    pfad = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
    try:
        gesamt, benutzt, frei = shutil.disk_usage(pfad)
    except OSError as e:
        raise ToolError(f"Speicherplatz nicht lesbar: {e}") from None
    return (f"Laufwerk {pfad}: {_mb(frei)} frei von {_mb(gesamt)} "
            f"({benutzt * 100 // gesamt}% belegt).")


@tool(
    "pc_aufraeumen",
    "Clean up the PC by deleting TEMPORARY files (older than a day) and optionally emptying the Recycle "
    "Bin. Only temp folders and the Recycle Bin are touched - never the owner's own documents, pictures or "
    "other files. Use this when the owner wants to free up space or 'clean the PC'.",
    {"auch_papierkorb": {"type": "boolean", "description": "Also empty the Recycle Bin (default true)."}},
    confirm="always",
    summary=lambda a: "Temporäre Dateien löschen" + (" und Papierkorb leeren" if a.get("auch_papierkorb", True)
                                                      else ""),
)
def pc_aufraeumen(ctx, auch_papierkorb=True):
    anzahl = frei = 0
    for o in _temp_ordner():
        a, f = _aufraeumen_ordner(o, 1)
        anzahl += a
        frei += f
    teile = [f"{anzahl} temporäre Dateien/Ordner gelöscht ({_mb(frei)} freigegeben)."]
    if auch_papierkorb and os.name == "nt":
        try:
            _powershell("Clear-RecycleBin -Force -ErrorAction SilentlyContinue", 180)
            teile.append("Papierkorb geleert.")
        except ToolError as e:
            teile.append(f"Papierkorb konnte nicht geleert werden: {e}")
    teile.append(speicherplatz(ctx))
    return " ".join(teile)


# --------------------------------------------------------------------------- Virenschutz (Defender)

@tool(
    "virenscan",
    "Run a Microsoft Defender virus scan (Windows only). art='schnell' is a quick scan (fast), 'voll' is a "
    "full scan (can take a long time). Afterwards reports whether threats were found.",
    {"art": {"type": "string", "enum": ["schnell", "voll"],
             "description": "schnell = quick scan, voll = full scan."}},
)
def virenscan(ctx, art="schnell"):
    voll = str(art).lower() in ("voll", "full", "komplett", "gross", "groß")
    typ, timeout = ("FullScan", 3600) if voll else ("QuickScan", 1200)
    _powershell(f"Start-MpScan -ScanType {typ}", timeout)
    return f"{'Vollständiger' if voll else 'Schneller'} Scan abgeschlossen.\n" + _funde_text()


@tool("viren_status", "Show Microsoft Defender status and any threats it has detected (Windows only).", {})
def viren_status(ctx):
    status = _powershell(
        "Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,"
        "AntivirusSignatureLastUpdated,QuickScanAge,FullScanAge | Format-List | Out-String", 60)
    return (status.strip() + "\n\n" + _funde_text()).strip()


@tool(
    "viren_entfernen",
    "Remove/quarantine the threats Microsoft Defender has found (Windows only). Use after a scan found "
    "something. This changes the system, so it is always confirmed.",
    {},
    confirm="always",
    summary=lambda a: "Von Defender gefundene Bedrohungen entfernen/in Quarantäne verschieben",
)
def viren_entfernen(ctx):
    _powershell("Remove-MpThreat", 600)
    return "Defender hat die gefundenen Bedrohungen entfernt bzw. in Quarantäne verschoben.\n" + _funde_text()


@tool("virenschutz_aktualisieren", "Update Microsoft Defender's virus definitions (Windows only).", {})
def virenschutz_aktualisieren(ctx):
    _powershell("Update-MpSignature", 600)
    return "Die Virendefinitionen von Defender wurden aktualisiert."


def _funde_text() -> str:
    ps = ("$t = Get-MpThreatDetection 2>$null | Sort-Object InitialDetectionTime; "
          "if ($t) { ($t | Select-Object -Last 20 | ForEach-Object { $_.ThreatName }) -join \"`n\" } "
          "else { 'Keine aktiven Bedrohungen gefunden.' }")
    try:
        out = _powershell(ps, 120)
    except ToolError as e:
        return f"(Bedrohungen konnten nicht abgefragt werden: {e})"
    return "Bedrohungen:\n" + out if out and "Keine" not in out else (out or "Keine aktiven Bedrohungen gefunden.")
