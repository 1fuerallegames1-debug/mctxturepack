"""Browser-Werkzeuge für Angel: ein eigenes, automatisiertes Browserfenster.

Einrichtung: siehe README, Abschnitt "Browser". Lesen/Öffnen läuft frei; Klicken und Tippen werden
im Modus "nachfragen" bestätigt. Angel soll im Netz nichts kaufen, bezahlen oder absenden, ohne dass
du es verlangt hast.
"""

from __future__ import annotations

from angel.browser import Browser, BrowserError, available
from angel.tools import ToolError, tool


def _browser(ctx) -> Browser:
    b = ctx.cfg.get("browser") or {}
    if not b.get("aktiv"):
        raise ToolError("Der Browser ist nicht eingeschaltet. In config.json: browser → aktiv auf true setzen "
                        "(siehe README, Abschnitt 'Browser').")
    existing = getattr(ctx, "_browser", None)
    if existing is not None:
        return existing
    ok, msg = available()
    if not ok:
        raise ToolError(msg)
    browser = Browser(ctx.data_dir / "browser-profil", visible=b.get("sichtbar", True))
    ctx._browser = browser
    return browser


def _run(fn):
    try:
        return fn()
    except BrowserError as e:
        raise ToolError(str(e)) from None


@tool(
    "browser_open",
    "Open a web page in Angel's own browser window and return its text. Use this for interactive sites "
    "or sites where the owner is logged in (in Angel's browser), when just reading (fetch_webpage) is not enough.",
    {"url": {"type": "string", "description": "The address to open."}},
    required=["url"],
)
def browser_open(ctx, url: str):
    return _run(lambda: _browser(ctx).open(url))


@tool(
    "browser_read",
    "Read the text of the page currently open in Angel's browser.",
    {},
)
def browser_read(ctx):
    return _run(lambda: _browser(ctx).read())


@tool(
    "browser_links",
    "List the links on the page currently open in Angel's browser (text and address).",
    {},
)
def browser_links(ctx):
    links = _run(lambda: _browser(ctx).links())
    if not links:
        return "Keine Links auf dieser Seite gefunden."
    return "Links:\n" + "\n".join(f"- {t}: {u}" for t, u in links)


@tool(
    "browser_click",
    "Click something on the current page, found by its visible text or a CSS selector.",
    {"what": {"type": "string", "description": "The visible button/link text, or a CSS selector."}},
    required=["what"],
    confirm=True,
    summary=lambda a: f"Im Browser klicken: {a.get('what')}",
)
def browser_click(ctx, what: str):
    return _run(lambda: _browser(ctx).click(what))


@tool(
    "browser_type",
    "Type text into a field on the current page (found by its label, placeholder or a CSS selector). "
    "Set submit=true to press Enter afterwards (e.g. to run a search).",
    {"field": {"type": "string", "description": "The field's label, placeholder or a CSS selector."},
     "text": {"type": "string", "description": "What to type."},
     "submit": {"type": "boolean", "description": "Press Enter after typing (default false)."}},
    required=["field", "text"],
    confirm=True,
    summary=lambda a: f"Im Browser in '{a.get('field')}' eingeben: {a.get('text')}"
                      + ("  und Enter" if a.get("submit") else ""),
)
def browser_type(ctx, field: str, text: str, submit: bool = False):
    return _run(lambda: _browser(ctx).type(field, text, submit))
