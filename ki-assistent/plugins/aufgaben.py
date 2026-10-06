"""To-Dos, Termine und Erinnerungen – auch wiederkehrend ('jeden Montag morgen').

Diese Werkzeuge legen geplante Aufgaben an. Ein Hintergrund-Dienst (angel/planer.py)
löst sie zur richtigen Zeit aus: erinnert dich (typ 'erinnerung') oder führt sie
selbst aus (typ 'auftrag', z. B. jemandem eine Nachricht schicken).
"""

from datetime import datetime

from angel.planer import hole_aufgaben
from angel.tools import ToolError, tool


def _store(ctx):
    return hole_aufgaben(ctx.data_dir / "aufgaben.json")


def _wann(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return iso or "?"


@tool(
    "aufgabe_planen",
    "Schedule a reminder or an autonomous task for later, once or repeating. Use this whenever the owner "
    "wants something at a certain time or on a schedule ('at 3pm send Tom a message', 'remind me tomorrow at "
    "9', 'every Monday morning make my list'). You convert their words into the fields: give the local time "
    "as HH:MM in 24h; for a one-time task on a specific day set datum=YYYY-MM-DD (omit for today); for "
    "repeating set wiederholung to taeglich (daily), werktags (Mon-Fri), wochenende (Sat/Sun), woechentlich "
    "(then also set wochentag 0=Mon..6=Sun) or monatlich (monthly). typ='erinnerung' just reminds the owner; "
    "typ='auftrag' means YOU carry it out yourself at that time. Scheduled tasks only run while Angel is "
    "running (Angel is in autostart).",
    {
        "text": {"type": "string", "description": "What to remind about or do, written in the owner's language."},
        "uhrzeit": {"type": "string", "description": "Time of day, HH:MM in 24h, e.g. '15:00'."},
        "typ": {"type": "string", "enum": ["erinnerung", "auftrag"],
                "description": "erinnerung = just remind the owner; auftrag = you do it yourself."},
        "datum": {"type": "string", "description": "One-time only: date YYYY-MM-DD. Omit for today or for repeating."},
        "wiederholung": {"type": "string",
                         "enum": ["", "taeglich", "werktags", "wochenende", "woechentlich", "monatlich"],
                         "description": "Empty = one-time. Otherwise the repeat rule."},
        "wochentag": {"type": "integer", "description": "Only for woechentlich: 0=Monday .. 6=Sunday."},
    },
    required=["text", "uhrzeit"],
)
def aufgabe_planen(ctx, text, uhrzeit, typ="erinnerung", datum=None, wiederholung="", wochentag=None):
    if not (text or "").strip():
        raise ToolError("Bitte angeben, woran erinnert oder was getan werden soll.")
    try:
        task = _store(ctx).hinzufuegen(text, typ=typ, uhrzeit=uhrzeit, wiederholung=wiederholung,
                                       wochentag=wochentag, datum=datum)
    except ValueError as e:
        raise ToolError(str(e)) from None
    art = "Auftrag" if task["typ"] == "auftrag" else "Erinnerung"
    wdh = f", wiederholt: {task['wiederholung']}" if task["wiederholung"] else ""
    return f"{art} geplant für {_wann(task['naechste'])}{wdh} (Nr. {task['id']}): {task['text']}"


@tool("aufgaben_anzeigen", "List the owner's scheduled reminders and tasks (with their ids).", {})
def aufgaben_anzeigen(ctx):
    tasks = sorted(_store(ctx).liste(), key=lambda t: t.get("naechste", ""))
    if not tasks:
        return "Keine geplanten Aufgaben."
    zeilen = []
    for t in tasks:
        wdh = f" ({t['wiederholung']})" if t.get("wiederholung") else ""
        art = "Auftrag" if t.get("typ") == "auftrag" else "Erinnerung"
        zeilen.append(f"[{t.get('id')}] {_wann(t.get('naechste'))}{wdh} – {art}: {t.get('text', '')}")
    return "\n".join(zeilen)


@tool(
    "aufgabe_absagen",
    "Cancel and remove a scheduled reminder or task by its id (see aufgaben_anzeigen).",
    {"id": {"type": "string", "description": "The task id to cancel."}},
    required=["id"],
)
def aufgabe_absagen(ctx, id):
    if _store(ctx).entfernen(str(id)):
        return f"Aufgabe {id} abgesagt."
    raise ToolError(f"Keine Aufgabe mit der Nr. {id} gefunden.")
