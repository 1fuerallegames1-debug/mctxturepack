"""Werkzeug-System: alles, was Kai auf deinem PC tun kann, ist hier als "Werkzeug" registriert.

Eigene Werkzeuge kannst du im Ordner "plugins" anlegen (siehe plugins/beispiel_wetter.py):

    from kai.tools import tool

    @tool("mein_werkzeug", "Was das Werkzeug tut (auf Englisch versteht das Modell es am besten).",
          {"text": {"type": "string", "description": "..."}}, required=["text"])
    def mein_werkzeug(ctx, text):
        return "Ergebnis"
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_REGISTERED: dict[str, "Tool"] = {}

URL_RE = re.compile(r"https?://[^\s<>\"'`)\]}]+", re.I)


class ToolError(Exception):
    """Erwarteter Fehler in einem Werkzeug – die Meldung geht an das Modell zurück."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    required: list
    func: Callable
    # False = ohne Nachfrage, True = nachfragen (außer im Modus "automatisch"),
    # "always" = immer nachfragen – oder eine Funktion(ctx, args), die einen dieser Werte liefert
    confirm: object = False
    # Funktion(args) -> kurze, menschenlesbare Beschreibung für die Nachfrage
    summary: Callable | None = None

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.parameters, "required": list(self.required)},
            },
        }

    def needs_confirmation(self, ctx, args: dict):
        level = self.confirm(ctx, args) if callable(self.confirm) else self.confirm
        return level if level == "always" else bool(level)

    def describe(self, args: dict) -> str:
        if self.summary:
            try:
                return self.summary(args)
            except Exception:
                pass
        return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())


def tool(name: str, description: str, parameters: dict | None = None, required=(),
         confirm=False, summary: Callable | None = None):
    """Decorator zum Registrieren eines Werkzeugs."""
    def deco(func):
        _REGISTERED[name] = Tool(name, description.strip(), parameters or {}, list(required),
                                 func, confirm, summary)
        return func
    return deco


@dataclass
class ToolContext:
    """Wird jedem Werkzeug als erstes Argument übergeben."""
    cfg: dict
    workdir: Path
    data_dir: Path
    memory: object = None
    seen_urls: set = field(default_factory=set)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def resolve(self, path: str) -> Path:
        """Wandelt eine (evtl. relative) Pfadangabe in einen absoluten Pfad um."""
        import os
        p = (path or ".").strip().strip('"').strip("'")
        p = os.path.expandvars(os.path.expanduser(p))
        q = Path(p)
        if not q.is_absolute():
            q = self.workdir / q
        return q

    def remember_urls(self, text: str):
        for url in URL_RE.findall(text or ""):
            self.seen_urls.add(url.rstrip(".,;:!?"))


def truncate(text: str, limit: int) -> str:
    """Kürzt lange Ausgaben in der Mitte (Anfang und Ende bleiben sichtbar)."""
    if text is None:
        return ""
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n\n[... {cut} Zeichen ausgelassen ...]\n\n{text[-tail:]}"


def _coerce(value, spec: dict):
    """Kleine Modelle schicken Zahlen gerne als Text – hier wird das korrigiert."""
    typ = spec.get("type")
    try:
        if typ == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        if typ == "integer" and isinstance(value, float) and value.is_integer():
            return int(value)
        if typ == "number" and isinstance(value, str):
            return float(value.strip())
        if typ == "boolean" and isinstance(value, str):
            if value.strip().lower() in ("true", "1", "yes", "ja"):
                return True
            if value.strip().lower() in ("false", "0", "no", "nein"):
                return False
        if typ == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    except ValueError:
        pass
    return value


class ToolRegistry:
    def __init__(self, disabled=()):
        load_builtin_tools()
        self.tools = {n: t for n, t in _REGISTERED.items() if n not in set(disabled)}

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def names(self) -> list[str]:
        return list(self.tools)

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def prepare_args(self, tool_obj: Tool, args: dict | None) -> dict:
        """Prüft und bereinigt Argumente. Wirft ToolError bei fehlenden Pflichtangaben."""
        if args is None:
            raise ToolError("Die Argumente waren kein gültiges JSON-Objekt. Bitte erneut mit gültigem JSON aufrufen.")
        clean = {}
        for key, value in args.items():
            if key in tool_obj.parameters:
                clean[key] = _coerce(value, tool_obj.parameters[key])
        missing = [r for r in tool_obj.required if clean.get(r) in (None, "")]
        if missing:
            raise ToolError(f"Fehlende Pflichtangabe(n): {', '.join(missing)}")
        # Nur Parameter übergeben, die die Funktion auch kennt
        accepted = inspect.signature(tool_obj.func).parameters
        if not any(p.kind == p.VAR_KEYWORD for p in accepted.values()):
            clean = {k: v for k, v in clean.items() if k in accepted}
        return clean

    def run(self, tool_obj: Tool, ctx: ToolContext, args: dict) -> str:
        result = tool_obj.func(ctx, **args)
        if result is None:
            return "OK"
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, indent=1, default=str)


_builtin_loaded = False
_plugin_errors: list[str] = []


def load_builtin_tools():
    global _builtin_loaded
    if _builtin_loaded:
        return
    _builtin_loaded = True
    from . import files, memory, system, web  # noqa: F401  (registrieren sich selbst)


def load_plugins(folder: Path) -> list[str]:
    """Lädt alle .py-Dateien aus dem Plugin-Ordner. Gibt Fehlermeldungen zurück."""
    errors = []
    if not folder.is_dir():
        return errors
    for file in sorted(folder.glob("*.py")):
        if file.name.startswith("_"):
            continue
        mod_name = f"kai_plugin_{file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception as e:  # ein kaputtes Plugin soll Kai nicht lahmlegen
            sys.modules.pop(mod_name, None)
            errors.append(f"Plugin {file.name} konnte nicht geladen werden: {e}")
    return errors
