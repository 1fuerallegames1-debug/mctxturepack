"""Ein eigenes, automatisiertes Browserfenster für Angel (getrennt von deinem Alltags-Chrome).

Nutzt Playwright. Das ist ein Zusatzpaket; fehlt es, erklärt Angel die Installation, und alles andere
läuft weiter. Logins bleiben in einem eigenen Profil (daten/browser-profil) erhalten – du meldest dich
im Angel-Browser bei Bedarf einmal an.
"""

from __future__ import annotations

import threading
from pathlib import Path

PIP_HINT = ("Für die Browsersteuerung fehlt Playwright. Installiere es einmalig in der Eingabeaufforderung:\n"
            "    pip install playwright\n"
            "    python -m playwright install chromium\n"
            "Danach Angel neu starten.")


class BrowserError(Exception):
    """Verständliche Meldung für den Benutzer."""


def available() -> tuple[bool, str]:
    try:
        import playwright  # noqa: F401
        return True, ""
    except Exception:
        return False, PIP_HINT


def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + " …"


class Browser:
    """Hält ein Browserfenster offen. Alle Aufrufe laufen über denselben Playwright-Thread,
    weil Playwright nicht threadübergreifend verwendet werden darf."""

    def __init__(self, profile_dir: Path, visible: bool = True):
        self.profile_dir = Path(profile_dir)
        self.visible = visible
        self._pw = None
        self._ctx = None
        self._page = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._page is not None:
            return
        ok, msg = available()
        if not ok:
            raise BrowserError(msg)
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            # Eigenes Profil -> Logins bleiben erhalten, getrennt vom normalen Chrome
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir), headless=not self.visible)
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        except Exception as e:
            raise BrowserError(
                f"Der Browser konnte nicht gestartet werden: {e}\n"
                "Ist Chromium installiert?  python -m playwright install chromium") from None

    # ------------------------------------------------------------ Aktionen

    def open(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        with self._lock:
            self._ensure()
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return self.read()

    def read(self, limit: int = 6000) -> str:
        with self._lock:
            self._ensure()
            title = self._page.title()
            body = self._page.inner_text("body")
        return f"Seite: {self._page.url}\nTitel: {title}\n\n{clip(body, limit)}"

    def links(self, limit: int = 40) -> list[tuple[str, str]]:
        with self._lock:
            self._ensure()
            items = self._page.eval_on_selector_all(
                "a[href]", "els => els.map(e => [e.innerText.trim(), e.href])")
        out, seen = [], set()
        for text, href in items:
            if href.startswith("http") and href not in seen and text:
                seen.add(href)
                out.append((text, href))
            if len(out) >= limit:
                break
        return out

    def click(self, what: str) -> str:
        with self._lock:
            self._ensure()
            try:
                self._page.get_by_text(what, exact=False).first.click(timeout=8000)
            except Exception:
                self._page.click(what, timeout=8000)  # als CSS-Selektor versuchen
            self._page.wait_for_load_state("domcontentloaded", timeout=15000)
        return self.read()

    def type(self, field: str, text: str, submit: bool = False) -> str:
        with self._lock:
            self._ensure()
            try:
                self._page.get_by_label(field).first.fill(text, timeout=8000)
            except Exception:
                try:
                    self._page.get_by_placeholder(field).first.fill(text, timeout=4000)
                except Exception:
                    self._page.fill(field, text, timeout=4000)  # CSS-Selektor
            if submit:
                self._page.keyboard.press("Enter")
                self._page.wait_for_load_state("domcontentloaded", timeout=15000)
        return self.read()

    def close(self):
        with self._lock:
            try:
                if self._ctx:
                    self._ctx.close()
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
            self._ctx = self._pw = self._page = None
