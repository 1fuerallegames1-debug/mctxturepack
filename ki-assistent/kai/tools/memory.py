"""Langzeitgedächtnis: Dinge, die sich Kai über dich merken soll (bleibt nach dem Neustart erhalten)."""

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
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.facts = [x for x in data if isinstance(x, dict) and x.get("text")]
        except (OSError, ValueError):
            self.facts = []

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
        return "\n".join(f"[{f['id']}] {f['text']}" for f in facts)


@tool(
    "remember",
    "Save an important fact about the user or their preferences to long-term memory, so you still "
    "know it in future conversations (e.g. names, preferences, where things are, recurring tasks). "
    "Use it when the user tells you something worth remembering or explicitly asks you to remember.",
    {"fact": {"type": "string", "description": "The fact, written as a short complete sentence."}},
    required=["fact"],
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
)
def forget(ctx, fact_id: int):
    if ctx.memory is None:
        raise ToolError("Gedächtnis nicht verfügbar.")
    removed = ctx.memory.remove(int(fact_id))
    if not removed:
        raise ToolError(f"Es gibt keinen Eintrag mit Nr. {fact_id}.")
    return f"Vergessen: {removed['text']}"
