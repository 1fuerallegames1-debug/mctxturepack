"""Werkzeuge für das Internet: Websuche und Webseiten lesen (ohne API-Schlüssel)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import ToolError, tool, truncate

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
MAX_DOWNLOAD = 3 * 1024 * 1024


def _fetch(url: str, data: bytes | None = None, timeout: float = 20) -> tuple[bytes, str, str]:
    """Lädt eine URL. Gibt (inhalt, content_type, end_url) zurück."""
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_DOWNLOAD + 1)
            return body[:MAX_DOWNLOAD], r.headers.get("Content-Type", ""), r.geturl()
    except urllib.error.HTTPError as e:
        raise ToolError(f"Die Seite antwortete mit Fehler {e.code} ({e.reason}).") from None
    except urllib.error.URLError as e:
        raise ToolError(f"Seite nicht erreichbar: {e.reason}. Besteht eine Internetverbindung?") from None
    except (TimeoutError, OSError) as e:
        raise ToolError(f"Seite nicht erreichbar: {e}") from None


def _charset(content_type: str, body: bytes) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
        if m:
            return m.group(1).decode("ascii", "ignore") or "utf-8"
        return "utf-8"
    return m.group(1)


def _decode(body: bytes, content_type: str) -> str:
    enc = _charset(content_type, body)
    try:
        return body.decode(enc, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head", "nav", "footer", "form"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header",
             "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dd", "dt", "hr", "main"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.parts.append("## ")
        elif tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.title.strip(), parser.text()


# --------------------------------------------------------------------------
# Websuche
# --------------------------------------------------------------------------

class _DDGParser(HTMLParser):
    """Liest Treffer aus der HTML-Version von DuckDuckGo (html. und lite.)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._mode = None  # "title" | "snippet"

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "a" and ("result__a" in cls or "result-link" in cls):
            self.results.append({"title": "", "url": _ddg_target(a.get("href", "")), "snippet": ""})
            self._mode = "title"
        elif ("result__snippet" in cls or "result-snippet" in cls) and self.results:
            self._mode = "snippet"

    def handle_endtag(self, tag):
        if tag in ("a", "td", "div") and self._mode:
            self._mode = None

    def handle_data(self, data):
        if self._mode and self.results:
            key = "title" if self._mode == "title" else "snippet"
            self.results[-1][key] += data


def _ddg_target(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        q = urllib.parse.parse_qs(parsed.query)
        if q.get("uddg"):
            return q["uddg"][0]
    return href


def parse_ddg(html: str) -> list[dict]:
    p = _DDGParser()
    p.feed(html)
    out = []
    for r in p.results:
        url = r["url"]
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:
            continue  # Werbung / interne Links
        out.append({"title": " ".join(r["title"].split()), "url": url, "snippet": " ".join(r["snippet"].split())})
    return out


def _search_duckduckgo(query: str) -> list[dict]:
    data = urllib.parse.urlencode({"q": query, "kl": "de-de"}).encode()
    errors = []
    for endpoint in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            body, ctype, _ = _fetch(endpoint, data=data)
        except ToolError as e:
            errors.append(str(e))
            continue
        results = parse_ddg(_decode(body, ctype))
        if results:
            return results
    if errors:
        raise ToolError("Websuche fehlgeschlagen: " + " / ".join(errors))
    return []


def _search_searxng(base: str, query: str) -> list[dict]:
    url = base.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "format": "json", "language": "de"})
    body, _, _ = _fetch(url)
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise ToolError("SearXNG lieferte kein JSON – ist 'json' in den SearXNG-Einstellungen unter formats aktiviert?") from None
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in data.get("results", [])]


@tool(
    "web_search",
    "Search the internet and return a list of results (title, URL, snippet). "
    "Use it for current events, facts you are unsure about, prices, how-tos, downloads etc. "
    "Then use fetch_webpage to read a promising result.",
    {
        "query": {"type": "string", "description": "Search terms."},
        "max_results": {"type": "integer", "description": "Number of results (default 6)."},
    },
    required=["query"],
)
def web_search(ctx, query: str, max_results: int = 6):
    settings = ctx.cfg.get("websuche") or {}
    provider = (settings.get("anbieter") or "duckduckgo").lower()
    if provider == "searxng" and settings.get("searxng_url"):
        results = _search_searxng(settings["searxng_url"], query)
    else:
        results = _search_duckduckgo(query)
    if not results:
        return (f"Keine Ergebnisse für '{query}'. (Falls das öfter passiert, blockiert die Suchmaschine evtl. "
                "automatische Anfragen – dann in config.json eine SearXNG-Instanz eintragen.)")
    n = max(1, min(int(max_results or 6), 15))
    lines = [f"Suchergebnisse für '{query}':"]
    for i, r in enumerate(results[:n], 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
    return "\n".join(lines)


def _fetch_confirm(ctx, args) -> bool:
    # Unbekannte Adressen nur nach Rückfrage abrufen. Grund: Eine manipulierte Webseite oder Datei
    # könnte die KI sonst dazu bringen, private Daten über eine präparierte URL hinauszuschicken.
    url = (args.get("url") or "").strip()
    return url not in ctx.seen_urls


@tool(
    "fetch_webpage",
    "Download a web page and return its readable text. URLs from search results or from the user "
    "can be fetched directly.",
    {
        "url": {"type": "string", "description": "Full URL starting with http:// or https://"},
        "max_chars": {"type": "integer", "description": "Maximum characters of text to return (default 12000)."},
    },
    required=["url"],
    confirm=_fetch_confirm,
    summary=lambda a: f"Webseite abrufen: {a.get('url', '')}",
)
def fetch_webpage(ctx, url: str, max_chars: int = 12000):
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        raise ToolError("Nur http:// und https:// Adressen sind erlaubt.")
    body, ctype, final_url = _fetch(url)
    ctype_low = ctype.lower()
    limit = max(500, min(int(max_chars or 12000), 60000))
    if "html" in ctype_low or (not ctype_low and body.lstrip()[:1] == b"<"):
        title, text = html_to_text(_decode(body, ctype))
    elif ctype_low.startswith("text/") or "json" in ctype_low or "xml" in ctype_low:
        title, text = "", _decode(body, ctype)
    else:
        raise ToolError(f"Die Adresse liefert keine Webseite, sondern '{ctype or 'unbekannt'}' "
                        f"({len(body)} Bytes). Zum Herunterladen run_command verwenden.")
    head = f"Seite: {final_url}" + (f"\nTitel: {title}" if title else "")
    return head + "\n\n" + truncate(text, limit)
