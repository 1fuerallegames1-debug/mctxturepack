"""Discord-Werkzeuge für Angel (über einen eigenen Bot).

Einrichtung: siehe README, Abschnitt "Discord". Ohne Einträge in config.json → discord passiert nichts.
Dieses Plugin steuert einen Bot – niemals deinen persönlichen Discord-Account.

Sicherheit:
- Posten, Kanäle/Rollen ändern: Angel fragt vorher (im Automatik-Modus einmal pro Kanal).
- Menschen betreffende oder löschende Aktionen (Kanal löschen, kicken, bannen, Auszeit, Rolle vergeben):
  Angel fragt IMMER vorher, auch im Automatik-Modus.
- Angel arbeitet nur auf dem in config.json eingetragenen Server und nur in erlaubten Kanälen.
"""

from __future__ import annotations

import datetime as _dt

from angel.discord_api import CHANNEL_TYPES, DiscordClient, DiscordError
from angel.tools import ToolError, tool

NEW_CHANNEL_TYPES = {"text": 0, "sprache": 2, "voice": 2, "kategorie": 4, "category": 4,
                     "ankündigung": 5, "ankuendigung": 5, "announcement": 5, "forum": 15}


# --------------------------------------------------------------------------- Hilfsfunktionen

def _conf(ctx) -> dict:
    d = ctx.cfg.get("discord") or {}
    if not d.get("aktiv"):
        raise ToolError("Discord ist nicht eingeschaltet. In config.json: discord → aktiv auf true setzen "
                        "und bot_token sowie server_id eintragen (siehe README, Abschnitt 'Discord').")
    if not d.get("bot_token") or not d.get("server_id"):
        raise ToolError("Discord ist nicht fertig eingerichtet: bot_token und server_id fehlen in config.json "
                        "(siehe README, Abschnitt 'Discord').")
    return d


def _client(ctx) -> tuple[DiscordClient, str, dict]:
    d = _conf(ctx)
    client = DiscordClient(d["bot_token"], base=d["api_base"]) if d.get("api_base") else DiscordClient(d["bot_token"])
    return client, str(d["server_id"]), d


def _run(fn):
    try:
        return fn()
    except DiscordError as e:
        raise ToolError(str(e)) from None


def _channels(ctx):
    client, guild, _ = _client(ctx)
    return _run(lambda: client.channels(guild))


def _resolve_channel(ctx, query: str) -> dict:
    """Findet einen Kanal per Name (mit/ohne #) oder ID. Berücksichtigt die erlaubten Kanäle."""
    d = ctx.cfg.get("discord") or {}
    query = str(query or "").strip().lstrip("#")
    if not query:
        raise ToolError("Bitte einen Kanal angeben (Name oder ID).")
    channels = _channels(ctx)
    allow = [str(a).strip().lstrip("#").lower() for a in (d.get("erlaubte_kanaele") or [])]

    def allowed(ch) -> bool:
        return not allow or str(ch.get("id")) in allow or (ch.get("name") or "").lower() in allow

    match = None
    for ch in channels:
        if str(ch.get("id")) == query or (ch.get("name") or "").lower() == query.lower():
            match = ch
            break
    if not match:
        names = ", ".join(f"#{c['name']}" for c in channels if c.get("type") in (0, 5, 15) and allowed(c))
        raise ToolError(f"Kanal '{query}' nicht gefunden. Erlaubte Kanäle: {names or '(keine)'}")
    if not allowed(match):
        raise ToolError(f"Der Kanal #{match.get('name')} ist für Angel nicht freigegeben "
                        "(config.json → discord → erlaubte_kanaele).")
    return match


def _resolve_member(ctx, query: str) -> dict:
    client, guild, _ = _client(ctx)
    q = str(query or "").strip().lstrip("@")
    if not q:
        raise ToolError("Bitte ein Mitglied angeben (Name oder ID).")
    members = _run(lambda: client.members(guild, 1000))
    for m in members:
        u = m.get("user") or {}
        names = [str(u.get("id")), u.get("username"), u.get("global_name"), m.get("nick")]
        if any(n and n.lower() == q.lower() for n in names):
            return m
    if q.isdigit():
        return {"user": {"id": q, "username": q}}  # direkte ID auch ohne Trefferliste erlauben
    known = ", ".join(sorted({(m.get("user") or {}).get("username", "?") for m in members})[:20])
    raise ToolError(f"Mitglied '{query}' nicht gefunden. Bekannte Mitglieder: {known or '(keine – Server-Members-Intent aktiv?)'}")


def _resolve_role(ctx, query: str) -> dict:
    client, guild, _ = _client(ctx)
    q = str(query or "").strip().lstrip("@")
    for r in _run(lambda: client.roles(guild)):
        if str(r.get("id")) == q or (r.get("name") or "").lower() == q.lower():
            return r
    raise ToolError(f"Rolle '{query}' nicht gefunden.")


def _member_name(m: dict) -> str:
    u = m.get("user") or {}
    return m.get("nick") or u.get("global_name") or u.get("username") or str(u.get("id"))


# --------------------------------------------------------------------------- Lesen (keine Nachfrage)

@tool(
    "discord_overview",
    "Show the connected Discord server: its name, the text/voice channels and the roles. "
    "Use this first to learn which channels and roles exist.",
    {},
)
def discord_overview(ctx):
    client, guild, d = _client(ctx)
    g = _run(lambda: client.guild(guild))
    me = _run(lambda: client.me())
    channels = _run(lambda: client.channels(guild))
    roles = _run(lambda: client.roles(guild))
    allow = d.get("erlaubte_kanaele") or []
    by_type = {}
    for c in channels:
        by_type.setdefault(CHANNEL_TYPES.get(c.get("type"), "?"), []).append("#" + (c.get("name") or "?"))
    lines = [f"Server: {g.get('name')} (Bot: {(me or {}).get('username')})"]
    for typ, names in by_type.items():
        lines.append(f"{typ}: " + ", ".join(names))
    lines.append("Rollen: " + ", ".join(r.get("name") for r in roles if r.get("name") != "@everyone"))
    if allow:
        lines.append(f"Für Angel freigegebene Kanäle: {', '.join('#' + str(a).lstrip('#') for a in allow)}")
    return "\n".join(lines)


@tool(
    "discord_read_messages",
    "Read the most recent messages of a Discord channel (newest last).",
    {"channel": {"type": "string", "description": "Channel name (e.g. 'general') or id."},
     "limit": {"type": "integer", "description": "How many messages (default 20, max 100)."}},
    required=["channel"],
)
def discord_read_messages(ctx, channel: str, limit: int = 20):
    client, guild, _ = _client(ctx)
    ch = _resolve_channel(ctx, channel)
    msgs = _run(lambda: client.messages(ch["id"], limit))
    if not msgs:
        return f"In #{ch['name']} gibt es keine Nachrichten."
    out = [f"Nachrichten in #{ch['name']} (neueste zuletzt):"]
    for m in reversed(msgs):
        who = (m.get("author") or {}).get("username", "?")
        text = (m.get("content") or "").replace("\n", " ")
        if not text and m.get("attachments"):
            text = "[Anhang]"
        out.append(f"{who}: {text}")
    return "\n".join(out)


# --------------------------------------------------------------------------- Posten / ändern (Nachfrage)

@tool(
    "discord_send_message",
    "Send a message to a Discord channel (as the bot).",
    {"channel": {"type": "string", "description": "Channel name or id."},
     "text": {"type": "string", "description": "The message to send (max 2000 characters)."}},
    required=["channel", "text"],
    confirm=True,
    summary=lambda a: f"In Discord-Kanal #{str(a.get('channel','')).lstrip('#')} schreiben:\n{a.get('text','')}",
    scope=lambda a: (f"discord-send:{str(a.get('channel','')).lower().lstrip('#')}",
                     f"Kanal #{str(a.get('channel','')).lstrip('#')}"),
)
def discord_send_message(ctx, channel: str, text: str):
    client, guild, _ = _client(ctx)
    ch = _resolve_channel(ctx, channel)
    if not str(text).strip():
        raise ToolError("Die Nachricht ist leer.")
    _run(lambda: client.send_message(ch["id"], str(text)))
    return f"Gesendet in #{ch['name']}."


@tool(
    "discord_create_channel",
    "Create a new channel on the Discord server.",
    {"name": {"type": "string", "description": "Name of the new channel."},
     "type": {"type": "string", "description": "'text' (default), 'sprache'/'voice', 'kategorie', 'ankündigung' or 'forum'."},
     "topic": {"type": "string", "description": "Optional channel topic (text channels only)."}},
    required=["name"],
    confirm=True,
    summary=lambda a: f"Neuen Discord-Kanal anlegen: {a.get('name')} ({a.get('type','text')})",
)
def discord_create_channel(ctx, name: str, type: str = "text", topic: str = ""):
    client, guild, _ = _client(ctx)
    ctype = NEW_CHANNEL_TYPES.get(str(type).strip().lower(), 0)
    ch = _run(lambda: client.create_channel(guild, str(name).strip(), ctype, topic=topic or None))
    return f"Kanal #{ch.get('name')} angelegt."


@tool(
    "discord_rename_channel",
    "Rename a Discord channel or change its topic.",
    {"channel": {"type": "string", "description": "Current channel name or id."},
     "new_name": {"type": "string", "description": "New name (optional)."},
     "topic": {"type": "string", "description": "New topic (optional)."}},
    required=["channel"],
    confirm=True,
    summary=lambda a: f"Discord-Kanal #{str(a.get('channel','')).lstrip('#')} ändern"
                      + (f" → Name: {a['new_name']}" if a.get('new_name') else "")
                      + (f", Thema: {a['topic']}" if a.get('topic') else ""),
    scope=lambda a: (f"discord-edit:{str(a.get('channel','')).lower().lstrip('#')}",
                     f"Kanal #{str(a.get('channel','')).lstrip('#')}"),
)
def discord_rename_channel(ctx, channel: str, new_name: str = "", topic: str = ""):
    client, guild, _ = _client(ctx)
    ch = _resolve_channel(ctx, channel)
    if not new_name and not topic:
        raise ToolError("Bitte new_name oder topic angeben.")
    _run(lambda: client.modify_channel(ch["id"], name=new_name or None, topic=topic or None))
    return f"#{ch['name']} geändert."


# --------------------------------------------------------------------------- Immer nachfragen (kritisch)

@tool(
    "discord_delete_channel",
    "Permanently delete a Discord channel.",
    {"channel": {"type": "string", "description": "Channel name or id to delete."}},
    required=["channel"],
    confirm="always",
    summary=lambda a: f"Discord-Kanal #{str(a.get('channel','')).lstrip('#')} UNWIDERRUFLICH löschen",
)
def discord_delete_channel(ctx, channel: str):
    client, guild, _ = _client(ctx)
    ch = _resolve_channel(ctx, channel)
    _run(lambda: client.delete_channel(ch["id"]))
    return f"Kanal #{ch['name']} wurde gelöscht."


@tool(
    "discord_set_role",
    "Give a role to a member or take it away.",
    {"member": {"type": "string", "description": "Member name or id."},
     "role": {"type": "string", "description": "Role name or id."},
     "action": {"type": "string", "description": "'add' (give) or 'remove' (take away)."}},
    required=["member", "role", "action"],
    confirm="always",
    summary=lambda a: f"Rolle '{a.get('role')}' bei {a.get('member')} {'vergeben' if a.get('action')=='add' else 'entfernen'}",
)
def discord_set_role(ctx, member: str, role: str, action: str):
    client, guild, _ = _client(ctx)
    m = _resolve_member(ctx, member)
    r = _resolve_role(ctx, role)
    uid = (m.get("user") or {}).get("id")
    act = str(action).strip().lower()
    if act in ("add", "give", "vergeben", "hinzufügen"):
        _run(lambda: client.add_role(guild, uid, r["id"]))
        return f"Rolle '{r['name']}' an {_member_name(m)} vergeben."
    if act in ("remove", "take", "entfernen", "wegnehmen"):
        _run(lambda: client.remove_role(guild, uid, r["id"]))
        return f"Rolle '{r['name']}' von {_member_name(m)} entfernt."
    raise ToolError("action muss 'add' oder 'remove' sein.")


@tool(
    "discord_moderate_member",
    "Moderate a member: kick, ban or time out (temporary mute). Affects a real person.",
    {"member": {"type": "string", "description": "Member name or id."},
     "action": {"type": "string", "description": "'kick', 'ban' or 'timeout'."},
     "minutes": {"type": "integer", "description": "For 'timeout': how many minutes (default 10)."},
     "reason": {"type": "string", "description": "Optional reason."}},
    required=["member", "action"],
    confirm="always",
    summary=lambda a: f"Discord-Moderation: {a.get('member')} "
                      + {"kick": "vom Server entfernen (kick)", "ban": "dauerhaft bannen",
                         "timeout": f"stummschalten ({a.get('minutes', 10)} Min.)"}.get(
                          str(a.get('action')).lower(), str(a.get('action')))
                      + (f" – Grund: {a['reason']}" if a.get('reason') else ""),
)
def discord_moderate_member(ctx, member: str, action: str, minutes: int = 10, reason: str = ""):
    client, guild, _ = _client(ctx)
    m = _resolve_member(ctx, member)
    uid = (m.get("user") or {}).get("id")
    act = str(action).strip().lower()
    if act in ("kick", "entfernen"):
        _run(lambda: client.kick(guild, uid))
        return f"{_member_name(m)} wurde vom Server entfernt (kick)."
    if act in ("ban", "bann", "bannen"):
        _run(lambda: client.ban(guild, uid))
        return f"{_member_name(m)} wurde gebannt."
    if act in ("timeout", "mute", "auszeit", "stumm"):
        until = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=max(1, int(minutes or 10))))
        _run(lambda: client.timeout_member(guild, uid, until.isoformat()))
        return f"{_member_name(m)} wurde für {int(minutes or 10)} Minuten stummgeschaltet."
    raise ToolError("action muss 'kick', 'ban' oder 'timeout' sein.")
