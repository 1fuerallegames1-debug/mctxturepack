"""Langzeitgedächtnis: Dinge, die sich Angel über dich merken soll (bleibt nach dem Neustart erhalten)."""

from __future__ import annotations

import datetime as _dt
import json
import threading
from pathlib import Path

from . import ToolError, tool

MAX_FACTS_IN_PROMPT = 60


class Memory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.facts: list[dict] = []
        self.load()

    def load(self):
        self.facts = []
        self.warning = ""
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("keine Liste")
        except (OSError, ValueError) as e:
            # Kaputte Datei nicht überschreiben, sondern beiseitelegen
            backup = self.path.with_name(f"gedaechtnis.defekt-{_dt.datetime.now():%Y%m%d_%H%M%S}.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            self.warning = f"Das Gedächtnis konnte nicht gelesen werden ({e}). Die Datei wurde nach {backup.name} verschoben."
            return
        used = set()
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
                continue
            fact_id = item.get("id")
            if not isinstance(fact_id, int) or isinstance(fact_id, bool) or fact_id in used:
                fact_id = max(used, default=0) + 1
            used.add(fact_id)
            self.facts.append({**item, "id": fact_id})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.facts, f, ensure_ascii=False, indent=1)
        tmp.replace(self.path)

    def add(self, text: str) -> dict:
        text = " ".join(text.split())
        with self._lock:
            for fact in self.facts:
                if fact["text"].lower() == text.lower():
                    return fact
            new_id = max((f.get("id", 0) for f in self.facts), default=0) + 1
            fact = {"id": new_id, "text": text, "datum": _dt.date.today().isoformat()}
            self.facts.append(fact)
            self.save()
            return fact

    def remove(self, fact_id: int) -> dict | None:
        with self._lock:
            for i, fact in enumerate(self.facts):
                if fact.get("id") == fact_id:
                    removed = self.facts.pop(i)
                    self.save()
                    return removed
        return None

    def prompt_text(self) -> str:
        facts = self.facts[-MAX_FACTS_IN_PROMPT:]
        return "\n".join(f"[{f.get('id')}] {f.get('text', '')}" for f in facts)


@tool(
    "remember",
    "Save an important fact about the user or their preferences to long-term memory, so you still "
    "know it in future conversations (e.g. names, preferences, where things are, recurring tasks). "
    "Use it when the user tells you something worth remembering or explicitly asks you to remember.",
    {"fact": {"type": "string", "description": "The fact, written as a short complete sentence."}},
    required=["fact"],
    # Nach dem Lesen fremder Inhalte (Webseiten, Dateien ...) erst fragen – sonst könnten diese
    # dauerhaft falsche "Fakten" in Angels Gedächtnis schreiben.
    confirm=lambda ctx, a: bool(ctx.untrusted_seen),
    summary=lambda a: f"Dauerhaft merken: {a.get('fact', '')}",
)
def remember(ctx, fact: str):
    if ctx.memory is None:
        raise ToolError("Gedächtnis nicht verfügbar.")
    entry = ctx.memory.add(fact)
    return f"Gemerkt (Nr. {entry['id']}): {entry['text']}"


@tool(
    "forget",
    "Delete a fact from long-term memory by its number (shown in brackets in the memory list).",
    {"fact_id": {"type": "integer", "description": "Number of the fact to delete."}},
    required=["fact_id"],
    confirm=lambda ctx, a: bool(ctx.untrusted_seen),  # wie bei remember
    summary=lambda a: f"Aus dem Gedächtnis löschen: Nr. {a.get('fact_id')}",
)
def forget(ctx, fact_id: int):
    if ctx.memory is None:
        raise ToolError("Gedächtnis nicht verfügbar.")
    removed = ctx.memory.remove(int(fact_id))
    if not removed:
        raise ToolError(f"Es gibt keinen Eintrag mit Nr. {fact_id}.")
    return f"Vergessen: {removed['text']}"
