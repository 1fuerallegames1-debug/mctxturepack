"""Bilder & Videos aus dem Web: finden, im Browser zeigen, speichern.

- Echte Bildsuche (DuckDuckGo) – Angel erfindet KEINE Links mehr.
- Öffnen im bevorzugten Browser des Besitzers (z. B. Opera GX).
- Speichern in den Medien-Ordner aus der Konfiguration (oder einen angegebenen Pfad).
"""

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from angel.tools import ToolError, tool

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AngelMedia/1.0"}
_MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
                   ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_MAX_DOWNLOAD = 300 * 1024 * 1024  # 300 MB


# --------------------------------------------------------------------------- HTTP (für Tests ersetzbar)

def _http_get(url: str, headers=None) -> str:
    req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _bildsuche_url(begriff: str) -> str:
    return "https://duckduckgo.com/?q=" + urllib.parse.quote(begriff) + "&iax=images&ia=images"


def _ddg_bilder(begriff: str, anzahl: int) -> list:
    q = urllib.parse.quote(begriff)
    html = _http_get("https://duckduckgo.com/?q=" + q + "&iax=images&ia=images")
    m = (re.search(r'vqd=["\']([\d-]+)["\']', html) or re.search(r'vqd=([\d-]+)&', html)
         or re.search(r'"vqd":"([\d-]+)"', html))
    if not m:
        raise ToolError("Bildsuche gerade nicht erreichbar.")
    api = f"https://duckduckgo.com/i.js?l=de-de&o=json&q={q}&vqd={m.group(1)}&f=,,,&p=1"
    data = json.loads(_http_get(api, headers={"Referer": "https://duckduckgo.com/"}))
    treffer = []
    for r in (data.get("results") or [])[:anzahl]:
        if r.get("image"):
            treffer.append({"titel": r.get("title", ""), "bild": r["image"], "quelle": r.get("url", "")})
    return treffer


# --------------------------------------------------------------------------- Browser (Opera GX)

# Bekannte Browser und ihre üblichen Installationsorte (Windows)
_BROWSER_PFADE = {
    "opera gx": ("Programs\\Opera GX\\launcher.exe", "Opera GX\\launcher.exe",
                 "Programs\\Opera GX\\opera.exe", "Opera GX\\opera.exe"),
    "opera": ("Programs\\Opera\\launcher.exe", "Opera\\launcher.exe", "Programs\\Opera\\opera.exe"),
    "firefox": ("Mozilla Firefox\\firefox.exe",),
    "edge": ("Microsoft\\Edge\\Application\\msedge.exe",),
    "chrome": ("Google\\Chrome\\Application\\chrome.exe",),
}


def _browser_schluessel(name: str) -> str:
    n = (name or "").strip().lower()
    if "opera" in n and "gx" in n:
        return "opera gx"
    if "edge" in n:
        return "edge"
    if "fire" in n:
        return "firefox"
    if "chrome" in n:
        return "chrome"
    if "opera" in n:
        return "opera"
    return n


def _browser_exe(name: str) -> str | None:
    if os.name != "nt":
        return None
    rels = _BROWSER_PFADE.get(_browser_schluessel(name))
    if not rels:
        return None
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        for rel in rels:
            p = Path(base) / rel
            if p.exists():
                return str(p)
    return None


def _browser_oeffnen(cfg: dict, url: str) -> None:
    pref = (cfg.get("web_browser") or "").strip()
    # 1) voller Pfad zu einer .exe
    if pref and (os.sep in pref or "/" in pref) and Path(pref).exists():
        try:
            subprocess.Popen([pref, url])
            return
        except OSError:
            pass
    # 2) bekannter Browsername (Opera GX / Firefox / Edge / Chrome / Opera)
    if pref and pref.lower() not in ("standard", "default", "system"):
        exe = _browser_exe(pref)
        if exe:
            try:
                subprocess.Popen([exe, url])
                return
            except OSError:
                pass
    # 3) Standardbrowser des Systems
    import webbrowser
    webbrowser.open(url)


# --------------------------------------------------------------------------- Speichern

def _medien_ordner(ctx) -> Path:
    raw = (ctx.cfg.get("medien_ordner") or "").strip()
    if raw:
        return Path(os.path.expandvars(os.path.expanduser(raw)))
    return Path.home() / "Pictures" / "Angel-Medien"


def _sicherer_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip()) or "medien"
    return name[:120]


def _name_aus_url(url: str) -> str:
    pfad = urllib.parse.urlparse(url).path
    name = os.path.basename(pfad) or "bild"
    if not os.path.splitext(name)[1]:
        name += ".jpg"
    return name


# --------------------------------------------------------------------------- Werkzeuge

@tool(
    "bild_zeigen",
    "Show the owner a picture of something: find a REAL image for the query and open it in the owner's "
    "browser. Use this for 'zeig/such mir ein Bild von X'. Do NOT invent links or write Markdown images.",
    {"begriff": {"type": "string", "description": "What to show a picture of, e.g. 'Madison Beer'."}},
    required=["begriff"],
)
def bild_zeigen(ctx, begriff):
    url = None
    try:
        treffer = _ddg_bilder(begriff, 1)
        url = treffer[0]["bild"] if treffer else None
    except Exception:
        url = None
    _browser_oeffnen(ctx.cfg, url or _bildsuche_url(begriff))
    if url:
        return f"Ich habe ein Bild zu '{begriff}' im Browser geöffnet: {url}"
    return f"Ich habe die Bildersuche zu '{begriff}' im Browser geöffnet (kein Einzelbild gefunden)."


@tool(
    "bild_suchen",
    "Search the web for real images matching a query and return a list of real image URLs (title, image "
    "URL, source page). Use when the owner wants to pick from several, or needs a URL to save.",
    {"begriff": {"type": "string", "description": "Search text."},
     "anzahl": {"type": "integer", "description": "How many results (default 6, max 10)."}},
    required=["begriff"],
)
def bild_suchen(ctx, begriff, anzahl=6):
    try:
        treffer = _ddg_bilder(begriff, max(1, min(int(anzahl or 6), 10)))
    except Exception:
        _browser_oeffnen(ctx.cfg, _bildsuche_url(begriff))
        return "Die direkte Bildsuche war nicht erreichbar – ich habe die Bildersuche im Browser geöffnet."
    if not treffer:
        _browser_oeffnen(ctx.cfg, _bildsuche_url(begriff))
        return "Keine direkten Treffer – ich habe die Bildersuche im Browser geöffnet."
    return "\n".join(f"[{i + 1}] {t['titel']}\n    Bild: {t['bild']}\n    Quelle: {t['quelle']}"
                     for i, t in enumerate(treffer))


@tool(
    "im_browser_oeffnen",
    "Open a URL in the owner's browser - or, if given plain text, open a web search for it. Also good for "
    "videos (e.g. open a search for a music video).",
    {"adresse": {"type": "string", "description": "A URL, or plain text to search for."}},
    required=["adresse"],
)
def im_browser_oeffnen(ctx, adresse):
    a = (adresse or "").strip()
    if not a:
        raise ToolError("Keine Adresse angegeben.")
    if not re.match(r"^https?://", a, re.I):
        a = ("https://" + a) if ("." in a and " " not in a) else \
            ("https://duckduckgo.com/?q=" + urllib.parse.quote(a))
    _browser_oeffnen(ctx.cfg, a)
    return f"Im Browser geöffnet: {a}"


@tool(
    "medien_herunterladen",
    "Download an image or video from a URL and save it. Use when the owner says to save a picture/video. "
    "If no folder/path is given, it goes to the owner's media folder from the settings.",
    {"url": {"type": "string", "description": "Direct http(s) link to the image/video."},
     "ziel": {"type": "string", "description": "Optional target folder or full file path."},
     "dateiname": {"type": "string", "description": "Optional file name."}},
    required=["url"],
)
def medien_herunterladen(ctx, url, ziel="", dateiname=""):
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ToolError("Bitte einen http(s)-Link zum Bild/Video angeben.")
    ziel = (ziel or "").strip()
    zp = Path(os.path.expandvars(os.path.expanduser(ziel))) if ziel else _medien_ordner(ctx)
    if zp.suffix.lower() in _MEDIA_SUFFIXES:  # voller Dateipfad angegeben
        ordner, name = zp.parent, zp.name
    else:
        ordner, name = zp, (dateiname.strip() or _name_aus_url(url))
    try:
        ordner.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ToolError(f"Ordner konnte nicht angelegt werden: {e}") from None
    zielpfad = ordner / _sicherer_name(name)
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(zielpfad, "wb") as f:
            gesamt = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                gesamt += len(chunk)
                if gesamt > _MAX_DOWNLOAD:
                    f.close()
                    zielpfad.unlink(missing_ok=True)
                    raise ToolError("Datei ist größer als 300 MB – abgebrochen.")
                f.write(chunk)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Download fehlgeschlagen: {e}") from None
    return f"Gespeichert: {zielpfad}  ({gesamt // 1024} KB)"
