"""Google-Werkzeuge für Angel (Gmail, Kalender, Drive, Kontakte, Tasks).

Einrichtung: siehe README, Abschnitt "Google". Ohne Anmeldung passiert nichts.
Sicherheit: Lesen läuft frei. E-Mail senden, Dateien/Termine/Aufgaben löschen und Dateien teilen
werden IMMER vorher bestätigt – auch im Automatik-Modus.
"""

from __future__ import annotations

import base64
import datetime as _dt
import urllib.parse
from email.message import EmailMessage

from angel.google_api import GoogleClient, GoogleError, NeedsLogin
from angel.tools import ToolError, tool

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary"
DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
PEOPLE = "https://people.googleapis.com/v1"
TASKS = "https://tasks.googleapis.com/tasks/v1"


def _client(ctx) -> GoogleClient:
    g = ctx.cfg.get("google") or {}
    if not g.get("aktiv"):
        raise ToolError("Google ist nicht eingeschaltet. In config.json: google → aktiv auf true setzen "
                        "und client_id/client_secret eintragen (siehe README, Abschnitt 'Google').")
    client = GoogleClient(g.get("client_id", ""), g.get("client_secret", ""),
                          ctx.data_dir / "google_token.json",
                          token_url=g.get("token_url") or "https://oauth2.googleapis.com/token",
                          api_base=g.get("api_base", ""))
    if not client.logged_in():
        raise ToolError("Angel ist noch nicht bei Google angemeldet. Starte Angel einmal mit dem Befehl "
                        "`python -m angel --google-anmelden` (siehe README) und erlaube den Zugriff.")
    return client


def _run(fn):
    try:
        return fn()
    except NeedsLogin as e:
        raise ToolError(str(e)) from None
    except GoogleError as e:
        raise ToolError(str(e)) from None


def _q(params: dict) -> str:
    return "?" + urllib.parse.urlencode(params)


# --------------------------------------------------------------------------- Gmail

def _header(msg, name):
    for h in (msg.get("payload", {}).get("headers") or []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _body_text(payload) -> str:
    """Holt den Text aus einer Gmail-Nachricht (bevorzugt text/plain)."""
    stack = [payload]
    html = ""
    while stack:
        part = stack.pop()
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
            if mime == "text/plain":
                return text
            if mime == "text/html" and not html:
                html = text
        stack.extend(part.get("parts") or [])
    import re
    return re.sub(r"<[^>]+>", " ", html) if html else ""


@tool(
    "gmail_search",
    "Search the user's Gmail. Returns a list of matching emails (id, from, subject, date, snippet). "
    "Use Gmail search syntax, e.g. 'from:amazon', 'is:unread', 'subject:Rechnung newer_than:7d'.",
    {"query": {"type": "string", "description": "Gmail search query (empty = most recent)."},
     "max_results": {"type": "integer", "description": "How many (default 8, max 20)."}},
)
def gmail_search(ctx, query: str = "", max_results: int = 8):
    client = _client(ctx)
    n = max(1, min(int(max_results or 8), 20))
    data = _run(lambda: client.request("GET", f"{GMAIL}/messages" + _q({"q": query, "maxResults": n})))
    ids = [m["id"] for m in (data or {}).get("messages", [])]
    if not ids:
        return f"Keine E-Mails gefunden für '{query}'."
    out = [f"{len(ids)} E-Mail(s) für '{query or '(neueste)'}':"]
    for mid in ids:
        m = _run(lambda mid=mid: client.request(
            "GET", f"{GMAIL}/messages/{mid}" + _q({"format": "metadata",
                                                   "metadataHeaders": ["From", "Subject", "Date"]})))
        out.append(f"[{mid}] {_header(m, 'Date')} · {_header(m, 'From')}\n    {_header(m, 'Subject')}"
                   f"\n    {(m.get('snippet') or '').strip()}")
    return "\n".join(out)


@tool(
    "gmail_read",
    "Read one full email by its id (from gmail_search).",
    {"message_id": {"type": "string", "description": "The email id."}},
    required=["message_id"],
)
def gmail_read(ctx, message_id: str):
    client = _client(ctx)
    m = _run(lambda: client.request("GET", f"{GMAIL}/messages/{message_id}" + _q({"format": "full"})))
    body = _body_text(m.get("payload", {}))
    from angel.tools import truncate
    return (f"Von: {_header(m, 'From')}\nAn: {_header(m, 'To')}\nDatum: {_header(m, 'Date')}\n"
            f"Betreff: {_header(m, 'Subject')}\n\n{truncate(body.strip(), int(ctx.cfg.get('max_ausgabe_zeichen', 8000)) * 2)}")


@tool(
    "gmail_send",
    "Send an email from the user's Gmail account.",
    {"to": {"type": "string", "description": "Recipient address(es), comma separated."},
     "subject": {"type": "string", "description": "Subject line."},
     "body": {"type": "string", "description": "The message text."}},
    required=["to", "subject", "body"],
    confirm="always",
    summary=lambda a: f"E-Mail senden an {a.get('to')}\nBetreff: {a.get('subject')}\n\n{a.get('body')}",
)
def gmail_send(ctx, to: str, subject: str, body: str):
    client = _client(ctx)
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _run(lambda: client.request("POST", f"{GMAIL}/messages/send", {"raw": raw}))
    return f"E-Mail an {to} wurde gesendet."


@tool(
    "gmail_trash",
    "Move an email to the Gmail trash.",
    {"message_id": {"type": "string", "description": "The email id."}},
    required=["message_id"],
    confirm="always",
    summary=lambda a: f"E-Mail {a.get('message_id')} in den Papierkorb verschieben",
)
def gmail_trash(ctx, message_id: str):
    client = _client(ctx)
    _run(lambda: client.request("POST", f"{GMAIL}/messages/{message_id}/trash"))
    return "E-Mail in den Papierkorb verschoben."


# --------------------------------------------------------------------------- Kalender

@tool(
    "calendar_list",
    "List upcoming Google Calendar events.",
    {"days": {"type": "integer", "description": "How many days ahead (default 7)."},
     "max_results": {"type": "integer", "description": "Maximum events (default 20)."}},
)
def calendar_list(ctx, days: int = 7, max_results: int = 20):
    client = _client(ctx)
    now = _dt.datetime.now(_dt.timezone.utc)
    params = {"timeMin": now.isoformat(), "timeMax": (now + _dt.timedelta(days=max(1, int(days or 7)))).isoformat(),
              "singleEvents": "true", "orderBy": "startTime", "maxResults": max(1, min(int(max_results or 20), 50))}
    data = _run(lambda: client.request("GET", f"{CAL}/events" + _q(params)))
    items = (data or {}).get("items", [])
    if not items:
        return f"Keine Termine in den nächsten {int(days or 7)} Tagen."
    out = [f"Termine (nächste {int(days or 7)} Tage):"]
    for e in items:
        start = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date", "")
        out.append(f"[{e.get('id')}] {start} · {e.get('summary', '(ohne Titel)')}")
    return "\n".join(out)


@tool(
    "calendar_create",
    "Create a Google Calendar event. Times are ISO like '2026-10-06T14:00:00' (local time).",
    {"title": {"type": "string", "description": "Event title."},
     "start": {"type": "string", "description": "Start, e.g. 2026-10-06T14:00:00."},
     "end": {"type": "string", "description": "End, e.g. 2026-10-06T15:00:00."},
     "description": {"type": "string", "description": "Optional details."}},
    required=["title", "start", "end"],
    confirm=True,
    summary=lambda a: f"Termin anlegen: {a.get('title')} ({a.get('start')} – {a.get('end')})",
)
def calendar_create(ctx, title: str, start: str, end: str, description: str = ""):
    client = _client(ctx)
    tz = _dt.datetime.now().astimezone().tzname() or "UTC"
    body = {"summary": title, "description": description,
            "start": {"dateTime": start, "timeZone": tz}, "end": {"dateTime": end, "timeZone": tz}}
    e = _run(lambda: client.request("POST", f"{CAL}/events", body))
    return f"Termin '{title}' angelegt ({e.get('htmlLink', '')})."


@tool(
    "calendar_delete",
    "Delete a Google Calendar event by id.",
    {"event_id": {"type": "string", "description": "Event id (from calendar_list)."}},
    required=["event_id"],
    confirm="always",
    summary=lambda a: f"Termin {a.get('event_id')} löschen",
)
def calendar_delete(ctx, event_id: str):
    client = _client(ctx)
    _run(lambda: client.request("DELETE", f"{CAL}/events/{event_id}"))
    return "Termin gelöscht."


# --------------------------------------------------------------------------- Drive

@tool(
    "drive_search",
    "Search files in the user's Google Drive. Returns id, name, type and date.",
    {"query": {"type": "string", "description": "Part of the file name, or empty for recent files."},
     "max_results": {"type": "integer", "description": "Maximum files (default 15)."}},
)
def drive_search(ctx, query: str = "", max_results: int = 15):
    client = _client(ctx)
    q = "trashed = false"
    if query:
        q += f" and name contains '{query.replace(chr(39), '')}'"
    params = {"q": q, "fields": "files(id,name,mimeType,modifiedTime)", "orderBy": "modifiedTime desc",
              "pageSize": max(1, min(int(max_results or 15), 50))}
    data = _run(lambda: client.request("GET", DRIVE + _q(params)))
    files = (data or {}).get("files", [])
    if not files:
        return f"Keine Dateien gefunden für '{query}'."
    return "Dateien im Drive:\n" + "\n".join(
        f"[{f['id']}] {f['name']}  ({f.get('mimeType', '').split('.')[-1]}, {f.get('modifiedTime', '')[:10]})"
        for f in files)


@tool(
    "drive_read",
    "Read the text content of a Google Drive file (Google Docs are exported as text).",
    {"file_id": {"type": "string", "description": "File id (from drive_search)."}},
    required=["file_id"],
)
def drive_read(ctx, file_id: str):
    client = _client(ctx)
    meta = _run(lambda: client.request("GET", f"{DRIVE}/{file_id}" + _q({"fields": "name,mimeType"})))
    mime = meta.get("mimeType", "")
    from angel.tools import truncate
    if mime.startswith("application/vnd.google-apps."):
        if mime.endswith(("document", "presentation")):
            data = _run(lambda: client.request("GET", f"{DRIVE}/{file_id}/export" + _q({"mimeType": "text/plain"})))
            text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
            return f"{meta.get('name')}:\n\n" + truncate(text, int(ctx.cfg.get("max_ausgabe_zeichen", 8000)) * 2)
        return f"{meta.get('name')} ist ein Google-{mime.split('.')[-1]} und kann nicht als Text gelesen werden."
    data = _run(lambda: client.request("GET", f"{DRIVE}/{file_id}" + _q({"alt": "media"})))
    if isinstance(data, (bytes, bytearray)):
        if b"\x00" in data[:4096]:
            return f"{meta.get('name')} ist keine Textdatei ({mime})."
        return f"{meta.get('name')}:\n\n" + truncate(data.decode("utf-8", "replace"),
                                                     int(ctx.cfg.get("max_ausgabe_zeichen", 8000)) * 2)
    return str(data)


@tool(
    "drive_upload",
    "Upload a local file from this PC to the user's Google Drive.",
    {"path": {"type": "string", "description": "Local file path on this PC."},
     "name": {"type": "string", "description": "Optional name in Drive (default: the file name)."}},
    required=["path"],
    confirm=True,
    summary=lambda a: f"Datei zu Google Drive hochladen: {a.get('path')}",
)
def drive_upload(ctx, path: str, name: str = ""):
    client = _client(ctx)
    p = ctx.resolve(path)
    if not p.is_file():
        raise ToolError(f"Datei nicht gefunden: {p}")
    meta = _run(lambda: client.request("POST", DRIVE, {"name": name or p.name}))
    fid = meta["id"]
    _run(lambda: client.request("PATCH", f"{UPLOAD}/{fid}" + _q({"uploadType": "media"}),
                                raw_body=p.read_bytes(),
                                headers={"Content-Type": "application/octet-stream"}))
    return f"'{p.name}' wurde zu Google Drive hochgeladen (id {fid})."


@tool(
    "drive_delete",
    "Move a Google Drive file to the trash.",
    {"file_id": {"type": "string", "description": "File id."}},
    required=["file_id"],
    confirm="always",
    summary=lambda a: f"Google-Drive-Datei {a.get('file_id')} in den Papierkorb",
)
def drive_delete(ctx, file_id: str):
    client = _client(ctx)
    _run(lambda: client.request("PATCH", f"{DRIVE}/{file_id}", {"trashed": True}))
    return "Datei in den Papierkorb verschoben."


@tool(
    "drive_share",
    "Share a Google Drive file with someone by email.",
    {"file_id": {"type": "string", "description": "File id."},
     "email": {"type": "string", "description": "Who to share with."},
     "role": {"type": "string", "description": "'reader' (view) or 'writer' (edit). Default reader."}},
    required=["file_id", "email"],
    confirm="always",
    summary=lambda a: f"Drive-Datei {a.get('file_id')} teilen mit {a.get('email')} ({a.get('role', 'reader')})",
)
def drive_share(ctx, file_id: str, email: str, role: str = "reader"):
    client = _client(ctx)
    role = "writer" if str(role).lower().startswith("w") else "reader"
    _run(lambda: client.request("POST", f"{DRIVE}/{file_id}/permissions",
                                {"type": "user", "role": role, "emailAddress": email}))
    return f"Datei mit {email} geteilt ({role})."


# --------------------------------------------------------------------------- Kontakte

@tool(
    "contacts_search",
    "Search the user's Google contacts by name. Returns names, emails and phone numbers.",
    {"query": {"type": "string", "description": "Name or part of it."}},
    required=["query"],
)
def contacts_search(ctx, query: str):
    client = _client(ctx)
    params = {"query": query, "readMask": "names,emailAddresses,phoneNumbers", "pageSize": 15}
    data = _run(lambda: client.request("GET", f"{PEOPLE}/people:searchContacts" + _q(params)))
    results = (data or {}).get("results", [])
    if not results:
        return f"Keine Kontakte gefunden für '{query}'."
    out = [f"Kontakte für '{query}':"]
    for r in results:
        person = r.get("person", {})
        name = (person.get("names") or [{}])[0].get("displayName", "?")
        mails = ", ".join(e.get("value", "") for e in person.get("emailAddresses") or [])
        phones = ", ".join(p.get("value", "") for p in person.get("phoneNumbers") or [])
        out.append(f"- {name}" + (f" · {mails}" if mails else "") + (f" · {phones}" if phones else ""))
    return "\n".join(out)


# --------------------------------------------------------------------------- Tasks

@tool(
    "tasks_list",
    "List the user's open Google Tasks (to-dos).",
    {},
)
def tasks_list(ctx):
    client = _client(ctx)
    data = _run(lambda: client.request("GET", f"{TASKS}/lists/@default/tasks" + _q({"showCompleted": "false"})))
    items = (data or {}).get("items", [])
    if not items:
        return "Keine offenen Aufgaben."
    out = ["Offene Aufgaben:"]
    for t in items:
        due = (t.get("due") or "")[:10]
        out.append(f"[{t.get('id')}] {t.get('title')}" + (f" (bis {due})" if due else ""))
    return "\n".join(out)


@tool(
    "tasks_add",
    "Add a new Google Task (to-do).",
    {"title": {"type": "string", "description": "What to do."},
     "notes": {"type": "string", "description": "Optional details."},
     "due": {"type": "string", "description": "Optional due date, e.g. 2026-10-10."}},
    required=["title"],
    confirm=True,
    summary=lambda a: f"Aufgabe hinzufügen: {a.get('title')}" + (f" (bis {a['due']})" if a.get('due') else ""),
)
def tasks_add(ctx, title: str, notes: str = "", due: str = ""):
    client = _client(ctx)
    body = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = (due if "T" in due else due + "T00:00:00.000Z")
    _run(lambda: client.request("POST", f"{TASKS}/lists/@default/tasks", body))
    return f"Aufgabe '{title}' hinzugefügt."


@tool(
    "tasks_complete",
    "Mark a Google Task as done.",
    {"task_id": {"type": "string", "description": "Task id (from tasks_list)."}},
    required=["task_id"],
)
def tasks_complete(ctx, task_id: str):
    client = _client(ctx)
    _run(lambda: client.request("PATCH", f"{TASKS}/lists/@default/tasks/{task_id}", {"status": "completed"}))
    return "Aufgabe als erledigt markiert."
