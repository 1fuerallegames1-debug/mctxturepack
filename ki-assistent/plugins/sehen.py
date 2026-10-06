"""Bilder und Videos verstehen.

Angel schaut sich eine Bild- oder Videodatei an und beschreibt, was darauf/darin
passiert. Dafür wird gezielt ein lokales Seh-Modell über Ollama gefragt (Standard
'llava'); das Hauptmodell bleibt fürs Denken und die Werkzeuge zuständig.

Einmalig installieren (falls noch nicht geschehen):  ollama pull llava
Für Videos wird zusätzlich ffmpeg benötigt (winget install Gyan.FFmpeg).
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from angel.tools import ToolError, tool

_BILD_ENDUNGEN = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_VIDEO_ENDUNGEN = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
_MAX_BYTES = 20 * 1024 * 1024


def _ollama_base(cfg) -> str:
    base = (cfg.get("server_url") or "http://127.0.0.1:11434").rstrip("/")
    for suffix in ("/api/chat", "/api/generate", "/v1", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base.rstrip("/")


def _seh_modell(cfg) -> str:
    return ((cfg.get("sehen") or {}).get("modell") or "llava").strip()


def _frag_seh_modell(cfg, prompt: str, bilder_b64: list) -> str:
    base = _ollama_base(cfg)
    modell = _seh_modell(cfg)
    payload = {
        "model": modell,
        "messages": [{"role": "user", "content": prompt, "images": bilder_b64}],
        "stream": False,
    }
    req = urllib.request.Request(base + "/api/chat", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 404 or "not found" in detail.lower():
            raise ToolError(f"Das Seh-Modell '{modell}' ist nicht installiert. Einmalig im Textfenster/CMD: "
                            f"ollama pull {modell}") from None
        raise ToolError(f"Seh-Modell nicht erreichbar ({e.code}): {detail}") from None
    except Exception as e:
        raise ToolError(f"Seh-Modell nicht erreichbar: {e}. Läuft Ollama? Ist '{modell}' installiert "
                        f"(ollama pull {modell})?") from None
    text = ((data.get("message") or {}).get("content") or data.get("response") or "").strip()
    if not text:
        raise ToolError("Das Seh-Modell hat keine Beschreibung geliefert.")
    return text


def _lade_b64(pfad: Path) -> str:
    daten = pfad.read_bytes()
    if len(daten) > _MAX_BYTES:
        raise ToolError("Die Datei ist größer als 20 MB – bitte ein kleineres Bild verwenden.")
    return base64.b64encode(daten).decode("ascii")


def _video_dauer(pfad: Path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nk=1:nw=1", str(pfad)],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _frames_extrahieren(pfad: Path, n: int) -> list:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    dauer = _video_dauer(pfad)
    bilder = []
    with tempfile.TemporaryDirectory() as tmp:
        if dauer and dauer > 0:  # gleichmäßig über das Video verteilt
            for i in range(n):
                ts = dauer * (i + 0.5) / n
                out = os.path.join(tmp, f"f{i:03d}.jpg")
                try:
                    subprocess.run([ffmpeg, "-y", "-ss", f"{ts:.2f}", "-i", str(pfad),
                                    "-frames:v", "1", "-q:v", "3", out],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
                except (OSError, subprocess.SubprocessError):
                    continue
                if os.path.exists(out):
                    bilder.append(base64.b64encode(Path(out).read_bytes()).decode("ascii"))
        else:  # Dauer unbekannt: Vorschaubilder sammeln
            muster = os.path.join(tmp, "f%03d.jpg")
            try:
                subprocess.run([ffmpeg, "-y", "-i", str(pfad), "-vf", "thumbnail,fps=1",
                                "-frames:v", str(n), "-q:v", "3", muster],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=150)
            except (OSError, subprocess.SubprocessError):
                pass
            for f in sorted(Path(tmp).glob("f*.jpg"))[:n]:
                bilder.append(base64.b64encode(f.read_bytes()).decode("ascii"))
    return bilder


@tool(
    "bild_ansehen",
    "Look at an image file on this computer and describe what is in it, or answer a question about it. Use "
    "this whenever the owner sends or points to a picture, photo or screenshot and wants to know what it "
    "shows. Give the path to the image file.",
    {
        "pfad": {"type": "string", "description": "Path to the image file (.png/.jpg/.jpeg/.webp/.gif/.bmp)."},
        "frage": {"type": "string", "description": "Optional: what exactly to answer about the image."},
    },
    required=["pfad"],
)
def bild_ansehen(ctx, pfad, frage=""):
    p = ctx.resolve(pfad)
    if not p.exists() or not p.is_file():
        raise ToolError(f"Datei nicht gefunden: {p}")
    if p.suffix.lower() not in _BILD_ENDUNGEN:
        raise ToolError(f"Das sieht nicht nach einem Bild aus ({p.suffix}). Für Videos nutze video_ansehen.")
    prompt = (frage or "").strip() or "Beschreibe genau und auf Deutsch, was auf diesem Bild zu sehen ist."
    return _frag_seh_modell(ctx.cfg, prompt, [_lade_b64(p)])


@tool(
    "video_ansehen",
    "Look at a video file: sample a few frames and describe what happens in it (needs ffmpeg installed). Use "
    "this when the owner sends or points to a video and wants to know what happens in it.",
    {
        "pfad": {"type": "string", "description": "Path to the video file (.mp4/.mov/.mkv/.avi/.webm/.m4v)."},
        "frage": {"type": "string", "description": "Optional: what exactly to answer about the video."},
        "bilder": {"type": "integer", "description": "How many frames to sample (default 4, max 8)."},
    },
    required=["pfad"],
)
def video_ansehen(ctx, pfad, frage="", bilder=4):
    p = ctx.resolve(pfad)
    if not p.exists() or not p.is_file():
        raise ToolError(f"Datei nicht gefunden: {p}")
    if p.suffix.lower() not in _VIDEO_ENDUNGEN:
        raise ToolError(f"Das sieht nicht nach einem Video aus ({p.suffix}). Für Bilder nutze bild_ansehen.")
    if not shutil.which("ffmpeg"):
        raise ToolError("Für Videos brauche ich ffmpeg. Einmalig installieren: winget install -e --id Gyan.FFmpeg "
                        "(danach das Fenster neu starten).")
    try:
        n = max(1, min(int(bilder or 4), 8))
    except (TypeError, ValueError):
        n = 4
    frames = _frames_extrahieren(p, n)
    if not frames:
        raise ToolError("Konnte keine Bilder aus dem Video lesen (ist die Datei in Ordnung?).")
    prompt = ((frage or "").strip()
              or f"Das sind {len(frames)} zeitlich geordnete Standbilder aus einem Video. "
                 "Beschreibe auf Deutsch, was im Video passiert.")
    return _frag_seh_modell(ctx.cfg, prompt, frames)
