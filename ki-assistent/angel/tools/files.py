"""Werkzeuge für Dateien und Ordner."""

from __future__ import annotations

import datetime as _dt
import fnmatch
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path

from . import ToolError, tool, truncate

# Ordner, die bei der Suche übersprungen werden (riesig und für Benutzer uninteressant)
ALWAYS_SKIP = {"$recycle.bin", "system volume information", "node_modules", ".git", "__pycache__",
               "site-packages", "winsxs", ".cache", ".venv", "venv"}
# ... nur direkt unter einem Laufwerk / dem Wurzelverzeichnis
ROOT_SKIP = {"proc", "sys", "dev", "run", "snap", "windows"}
# ... werden zuletzt durchsucht (groß, aber manchmal wichtig, z. B. AppData/.minecraft)
SEARCH_LAST = {"appdata", "library", "programdata", "program files", "program files (x86)"}


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _read_docx(path: Path) -> str:
    """Text aus einer Word-Datei (.docx) lesen – ganz ohne Zusatzpakete."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ToolError(f"Kein gültiges Word-Dokument: {e}") from None
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"<w:br/>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(a, b)
    return text


def _looks_like_utf16(raw: bytes) -> str | None:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    sample = raw[:1024]
    if len(sample) >= 16:
        odd_nuls = sample[1::2].count(0) / (len(sample) // 2)
        even_nuls = sample[0::2].count(0) / ((len(sample) + 1) // 2)
        if odd_nuls > 0.9 and even_nuls < 0.1:
            return "utf-16-le"
        if even_nuls > 0.9 and odd_nuls < 0.1:
            return "utf-16-be"
    return None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    utf16 = _looks_like_utf16(raw)  # z. B. Ausgabe von "> datei.txt" in Windows PowerShell 5.1
    if utf16:
        return raw.decode(utf16, "replace")
    if b"\x00" in raw[:8192]:
        raise ToolError(
            f"{path.name} ist keine Textdatei (Binärdatei, {_human_size(len(raw))}). "
            "Zum Öffnen open_item verwenden oder mit run_python/run_command verarbeiten."
        )
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


@tool(
    "read_file",
    "Read a text file (also .docx Word documents) and return its content. "
    "Long files are returned in parts: use start_line to continue reading.",
    {
        "path": {"type": "string", "description": "Path to the file."},
        "start_line": {"type": "integer", "description": "First line to read (1 = beginning)."},
        "max_lines": {"type": "integer", "description": "Maximum number of lines (default 400)."},
    },
    required=["path"],
)
def read_file(ctx, path: str, start_line: int = 1, max_lines: int = 400):
    p = ctx.resolve(path)
    if not p.exists():
        raise ToolError(f"Datei nicht gefunden: {p}")
    if p.is_dir():
        raise ToolError(f"{p} ist ein Ordner – list_directory verwenden.")
    if p.stat().st_size > 50 * 1024 * 1024:
        raise ToolError(f"Datei ist zu groß zum Lesen ({_human_size(p.stat().st_size)}).")
    text = _read_docx(p) if p.suffix.lower() == ".docx" else _read_text(p)
    lines = text.splitlines()
    total = len(lines)
    start = max(1, int(start_line or 1))
    count = max(1, min(int(max_lines or 400), 2000))
    budget = int(ctx.cfg.get("max_ausgabe_zeichen", 8000)) * 2
    # So viele ganze Zeilen wie in das Zeichenbudget passen – dann stimmt "weiterlesen mit" genau
    chunk, used = [], 0
    for line in lines[start - 1: start - 1 + count]:
        if chunk and used + len(line) + 1 > budget:
            break
        if not chunk and len(line) > budget:
            line = truncate(line, budget)  # einzelne riesige Zeile
        chunk.append(line)
        used += len(line) + 1
    end = start + len(chunk) - 1
    header = f"Datei: {p}  (Zeilen {start}-{end} von {total})"
    if end < total:
        header += f"  – weiterlesen mit start_line={end + 1}"
    return header + "\n" + "\n".join(chunk)


# Dateitypen, die ausgeführt oder automatisch geladen werden können: bei der Nachfrage komplett zeigen
SCRIPT_SUFFIXES = {".ps1", ".psm1", ".psd1", ".ps1xml", ".bat", ".cmd", ".py", ".pyw", ".pth", ".sh", ".bash",
                   ".zsh", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".wsc", ".sct", ".msc", ".reg", ".lnk",
                   ".url", ".hta", ".command", ".desktop", ".inf", ".scr", ".cpl", ""}


def _write_summary(args) -> str:
    content = args.get("content") or ""
    lines = content.splitlines()
    # Skripte komplett zeigen (sie werden später evtl. ausgeführt), sonst eine Vorschau
    limit = 10_000 if Path(str(args.get("path", ""))).suffix.lower() in SCRIPT_SUFFIXES else 20
    preview = "\n".join(lines[:limit])
    if len(lines) > limit:
        preview += f"\n... ({len(lines) - limit} weitere Zeilen, insgesamt {len(lines)})"
    mode = "anhängen an" if args.get("append") else "schreiben nach"
    return f"Datei {mode}: {args.get('path', '')}\n--- Inhalt ---\n{preview}"


def _unique(path: Path) -> Path:
    """Hängt _2, _3 … an, falls es die Datei schon gibt (z. B. zwei Sicherungen in derselben Sekunde)."""
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
    return candidate


def _existing_encoding(p: Path) -> str:
    """Kodierung einer vorhandenen Datei erraten, damit Anhängen sie nicht beschädigt."""
    raw = p.read_bytes()[:65536]
    utf16 = _looks_like_utf16(raw)
    if utf16:
        return "utf-16-le" if utf16 in ("utf-16", "utf-16-le") and not raw.startswith(b"\xfe\xff") else "utf-16-be"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        if e.start > len(raw) - 4:  # nur am Ende abgeschnittenes Zeichen
            return "utf-8"
        return "cp1252"


def _file_encoding(p: Path, content: str, append: bool) -> tuple[str, str]:
    """Passende Kodierung je Dateityp, damit Windows-Programme Umlaute richtig lesen."""
    suffix = p.suffix.lower()
    if suffix in (".bat", ".cmd"):
        content = content.replace("\r\n", "\n").replace("\n", "\r\n")  # cmd braucht CRLF
    if append and p.exists() and p.stat().st_size > 0:
        return _existing_encoding(p), content  # beim Anhängen die vorhandene Kodierung beibehalten
    if suffix in (".ps1", ".psm1", ".psd1", ".csv"):
        # Windows PowerShell 5.1 und Excel erkennen UTF-8 nur mit BOM
        return "utf-8-sig", content
    if suffix in (".bat", ".cmd") and not content.isascii() and "chcp" not in content.lower():
        # Umlaute in Batch-Dateien: als UTF-8 speichern und cmd das vorher sagen
        content = "@chcp 65001 >nul\r\n" + content
    return "utf-8", content


@tool(
    "write_file",
    "Create or overwrite a text file with the given content (folders are created automatically). "
    "Set append=true to add to the end instead. Before overwriting, a backup copy is kept.",
    {
        "path": {"type": "string", "description": "Path to the file."},
        "content": {"type": "string", "description": "The complete text to write."},
        "append": {"type": "boolean", "description": "Append instead of overwrite."},
    },
    required=["path", "content"],
    confirm=True,
    summary=_write_summary,
    scope=lambda a: (str(a.get("path", "")).strip().lower(), f"die Datei {a.get('path', '')}"),
)
def write_file(ctx, path: str, content: str, append: bool = False):
    p = ctx.resolve(path)
    if p.is_dir():
        raise ToolError(f"{p} ist ein Ordner, keine Datei.")
    p.parent.mkdir(parents=True, exist_ok=True)
    backup = ""
    if p.exists() and not append:
        backup_dir = Path(ctx.data_dir) / "sicherungen"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = _unique(backup_dir / f"{_dt.datetime.now():%Y%m%d_%H%M%S}_{p.name}")
        shutil.copy2(p, target)
        backup = f" (alte Version gesichert unter {target})"
    encoding, content = _file_encoding(p, content, append)
    with open(p, "a" if append else "w", encoding=encoding, errors="replace", newline="") as f:
        f.write(content)
    return f"{'Angehängt an' if append else 'Gespeichert:'} {p} ({len(content)} Zeichen){backup}"


def _is_hidden(entry) -> bool:
    """Versteckt? Unter Windows zählt das Datei-Attribut (".minecraft" ist dort sichtbar), sonst der Punkt."""
    if os.name == "nt":
        try:
            return bool(entry.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
        except (OSError, AttributeError):
            return False
    return entry.name.startswith(".")


@tool(
    "list_directory",
    "List the files and sub-folders of a folder with size and modification date.",
    {
        "path": {"type": "string", "description": "Folder path (default: working folder)."},
        "show_hidden": {"type": "boolean", "description": "Also show hidden files."},
    },
)
def list_directory(ctx, path: str = ".", show_hidden: bool = False):
    p = ctx.resolve(path)
    if not p.exists():
        raise ToolError(f"Ordner nicht gefunden: {p}")
    if not p.is_dir():
        raise ToolError(f"{p} ist eine Datei, kein Ordner.")
    entries, hidden = [], 0
    try:
        items = list(os.scandir(p))
    except PermissionError:
        raise ToolError(f"Kein Zugriff auf {p}") from None
    for e in items:
        if not show_hidden and _is_hidden(e):
            hidden += 1
            continue
        try:
            st = e.stat()
            is_dir = e.is_dir()
        except OSError:
            continue
        mtime = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M")
        entries.append((not is_dir, e.name.lower(), e.name, is_dir, st.st_size, mtime))
    entries.sort()
    lines = [f"Inhalt von {p} ({len(entries)} Einträge"
             + (f", {hidden} versteckte nicht angezeigt – show_hidden=true zeigt sie" if hidden else "") + "):"]
    for _, _, name, is_dir, size, mtime in entries[:300]:
        lines.append(f"[Ordner] {name}/" if is_dir else f"{name}  ({_human_size(size)}, {mtime})")
    if len(entries) > 300:
        lines.append(f"... und {len(entries) - 300} weitere")
    return "\n".join(lines)


@tool(
    "find_files",
    "Search for files or folders by name inside a folder and all its sub-folders. "
    "Pattern examples: '*.pdf', 'rechnung*', 'urlaub' (part of the name).",
    {
        "pattern": {"type": "string", "description": "Name or wildcard pattern (case-insensitive)."},
        "directory": {"type": "string", "description": "Where to search (default: user's home folder)."},
        "max_results": {"type": "integer", "description": "Maximum number of results (default 50)."},
    },
    required=["pattern"],
)
def find_files(ctx, pattern: str, directory: str = "", max_results: int = 50):
    root = ctx.resolve(directory) if directory else Path.home()
    if not root.is_dir():
        raise ToolError(f"Ordner nicht gefunden: {root}")
    pat = pattern.strip().lower()
    if not any(c in pat for c in "*?["):
        pat = f"*{pat}*"
    limit = max(1, min(int(max_results or 50), 500))
    deadline = time.monotonic() + 25
    results, scanned, timed_out = [], 0, False
    for dirpath, dirnames, filenames in os.walk(root):
        if ctx.cancel_event.is_set():
            break
        at_root = Path(dirpath).parent == Path(dirpath)
        all_dirs = list(dirnames)  # Treffer auch bei Ordnern, in die nicht hineingesucht wird
        dirnames[:] = sorted(
            (d for d in dirnames
             if d.lower() not in ALWAYS_SKIP
             and not (at_root and d.lower() in ROOT_SKIP)
             ),
            # große bzw. versteckte Ordner (z. B. AppData, .minecraft) zuletzt durchsuchen
            key=lambda d: (d.lower() in SEARCH_LAST or d.startswith("."), d.lower()),
        )
        for name, is_dir in [(d, True) for d in all_dirs] + [(f, False) for f in filenames]:
            if fnmatch.fnmatch(name.lower(), pat) or (is_dir and fnmatch.fnmatch(name.lower().lstrip("."), pat)):
                full = Path(dirpath) / name
                results.append(str(full) + (os.sep if is_dir else ""))
                if len(results) >= limit:
                    break
        scanned += 1
        if len(results) >= limit:
            break
        if time.monotonic() > deadline:
            timed_out = True
            break
    if not results:
        msg = f"Nichts gefunden für '{pattern}' in {root}."
    else:
        msg = f"{len(results)} Treffer für '{pattern}' in {root}:\n" + "\n".join(results)
    if len(results) >= limit:
        msg += f"\n(Ergebnisliste nach {limit} Treffern gekürzt)"
    if timed_out:
        msg += "\n(Suche nach 25 Sekunden abgebrochen – evtl. einen genaueren Ordner angeben)"
    return msg
