"""Werkzeuge für Befehle, Python-Code, Programme öffnen und Systeminfos."""

from __future__ import annotations

import datetime as _dt
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from . import ToolError, tool, truncate

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

# Bekannte gefährliche Befehle: fragen selbst im Modus "automatisch" immer nach.
# (Eine Liste bekannter Muster – keine Garantie, deshalb ist "nachfragen" der empfohlene Modus.)
DANGEROUS = re.compile(
    r"(\b(Remove-Item|rm|ri|del|erase|rd|rmdir)\b[^\n|;&]*?(\s-r(e(c(u(r(se?)?)?)?)?)?\b|\s/s\b)"  # rekursiv löschen
    r"|\brm\b[^\n|;&]*?\s-(-recursive\b|[a-z]*r)"                                       # bash: rm -r, -rf, -R
    r"|\bdel\s+/[sfq]|\bformat(\.com)?\s+[a-z]:|\bmkfs|\bdd\s+if=|\bdiskpart\b|\bshutdown\b"
    r"|\bbcdedit\b|\breg\s+delete\b|\bcipher\s+/w|\bStop-Computer\b|\bRestart-Computer\b"
    r"|\bClear-Disk\b|\bFormat-Volume\b|\bInitialize-Disk\b|\bvssadmin\s+delete|\bwbadmin\s+delete"
    r"|\bClear-RecycleBin\b|\bSet-MpPreference\b[^\n]*-Disable|:\(\)\s*\{)",
    re.I,
)
# Python-Code, der Dateien löscht oder andere Programme startet: immer nachfragen
PY_DANGEROUS = re.compile(
    r"\b(shutil\.rmtree|os\.(remove|unlink|rmdir|removedirs|system|popen|exec\w*|spawn\w*)|subprocess|"
    r"send2trash|ctypes|winreg)\b|\.(unlink|rmdir)\(",
)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def os_name() -> str:
    """Betriebssystem-Name – erkennt Windows 11 auch mit älteren Python-Versionen."""
    system, release = platform.system(), platform.release()
    if IS_WINDOWS and release == "10":
        try:
            if int(platform.version().split(".")[2]) >= 22000:
                release = "11"
        except (IndexError, ValueError):
            pass
    return f"{system} {release}"


def shell_name() -> str:
    if IS_WINDOWS:
        return "PowerShell"
    return "bash" if shutil.which("bash") else "sh"


def _powershell_exe() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def shell_description() -> str:
    """Beschreibung der Shell für die Anweisungen an das Modell."""
    if IS_WINDOWS:
        if shutil.which("pwsh"):
            return "PowerShell 7 (pwsh)"
        return "Windows PowerShell 5.1 (`&&` does not work there - use `;` or separate commands)"
    return "bash" if shutil.which("bash") else "sh"


def _decode(data: bytes) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        if IS_WINDOWS:
            try:
                import ctypes
                return data.decode(f"cp{ctypes.windll.kernel32.GetOEMCP()}", "replace")
            except Exception:
                pass
        return data.decode("latin-1", "replace")


_CLIXML_STRING = re.compile(r'<S S="Error">(.*?)</S>', re.S)


def _clean_clixml(text: str) -> str:
    """PowerShell schreibt Fehler manchmal als CLIXML – in lesbaren Text umwandeln."""
    if not text.lstrip().startswith("#< CLIXML"):
        return text
    parts = _CLIXML_STRING.findall(text)
    if not parts:
        return ""
    joined = "".join(parts)
    joined = re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), joined)
    return joined.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def _kill_tree(proc: subprocess.Popen):
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_process(args: list[str], cwd: Path, timeout: float, cancel_event=None, env=None) -> dict:
    """Startet einen Prozess und wartet (mit Zeitlimit). Die Ausgabe wird in eigenen Threads gelesen,
    damit ein gestartetes Programm, das die Ausgabe-Leitung offen hält (z. B. Notepad), nicht blockiert."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, **kwargs)
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []

    def reader(stream, sink):
        try:
            while True:
                chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
                if not chunk:
                    break
                sink.append(chunk)
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=reader, args=(proc.stdout, out_chunks), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, err_chunks), daemon=True)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    timed_out = cancelled = False
    try:
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if time.monotonic() > deadline:
                timed_out = True
                break
            time.sleep(0.1)
        if cancelled or timed_out:
            _kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for t in threads:
            t.join(timeout=2)
    except BaseException:
        _kill_tree(proc)  # z. B. Strg+C: Prozess nicht verwaist weiterlaufen lassen
        raise
    return {
        "code": proc.returncode,
        "out": _decode(b"".join(out_chunks)),
        "err": _decode(b"".join(err_chunks)),
        "timed_out": timed_out,
        "cancelled": cancelled,
        # Shell ist fertig, aber ein von ihr gestartetes Programm hält die Ausgabe noch offen
        "lingering": not (timed_out or cancelled) and any(t.is_alive() for t in threads),
    }


def _format_result(res: dict, timeout: float, limit: int) -> str:
    out = ANSI_ESCAPE.sub("", res["out"])
    err = ANSI_ESCAPE.sub("", res["err"])
    parts = []
    if res["timed_out"]:
        parts.append(f"[Zeitlimit von {int(timeout)} s erreicht – Prozess wurde beendet. "
                     "Für dauerhaft laufende Programme background=true verwenden.]")
    if res["cancelled"]:
        parts.append("[Vom Benutzer abgebrochen – Prozess wurde beendet.]")
    if res.get("lingering"):
        parts.append("[Ein gestartetes Programm läuft weiter; seine weitere Ausgabe wird nicht angezeigt.]")
    parts.append(f"Exit-Code: {res['code'] if res['code'] is not None else '?'}")
    if out.strip():
        parts.append("Ausgabe:\n" + out.rstrip())
    if err.strip():
        parts.append("Fehlerausgabe:\n" + err.rstrip())
    if not out.strip() and not err.strip():
        parts.append("(keine Ausgabe)")
    return truncate("\n".join(parts), limit)


def _command_confirm(ctx, args):
    # "always" = auch im Modus "automatisch" nachfragen
    return "always" if DANGEROUS.search(str(args.get("command") or "")) else True


def _cmd_summary(args) -> str:
    extra = []
    if args.get("working_directory"):
        extra.append(f"im Ordner {args['working_directory']}")
    if args.get("background"):
        extra.append("im Hintergrund")
    suffix = f"  ({', '.join(extra)})" if extra else ""
    return f"{shell_name()}-Befehl{suffix}:\n{args.get('command', '')}"


@tool(
    "run_command",
    f"Run a shell command on the user's computer ({'Windows PowerShell' if IS_WINDOWS else 'bash/sh'} syntax) "
    "and return its output. Use it for anything the other tools cannot do: managing files and folders, "
    "installing software, git, network checks, system settings, clipboard, processes, etc. "
    "Commands are non-interactive (no input possible). For GUI programs use open_item instead. "
    "For servers or other long-running programs set background=true.",
    {
        "command": {"type": "string", "description": "The command to run."},
        "working_directory": {"type": "string", "description": "Optional folder to run the command in."},
        "timeout": {"type": "integer", "description": "Optional time limit in seconds (default 120, max 1800)."},
        "background": {"type": "boolean", "description": "Start detached and return immediately (output goes to a log file)."},
    },
    required=["command"],
    confirm=_command_confirm,
    summary=_cmd_summary,
    scope=lambda a: (" ".join(str(a.get("command", "")).split()) + f"|{a.get('working_directory', '')}",
                     "genau diesen Befehl"),
)
def run_command(ctx, command: str, working_directory: str = "", timeout: int = 0, background: bool = False):
    cwd = ctx.resolve(working_directory) if working_directory else ctx.workdir
    if not cwd.is_dir():
        raise ToolError(f"Ordner existiert nicht: {cwd}")
    limit = int(ctx.cfg.get("max_ausgabe_zeichen", 8000))
    timeout = max(1, min(int(timeout or ctx.cfg.get("befehl_timeout", 120)), 1800))

    script_file = None
    if IS_WINDOWS:
        # Als .ps1-Datei mit BOM ausführen: so gibt es keine Probleme mit Anführungszeichen und Umlauten
        tmp_dir = Path(ctx.data_dir) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, script_file = tempfile.mkstemp(suffix=".ps1", dir=str(tmp_dir))
        os.close(fd)
        # Vorspann in einer Zeile (UTF-8-Ausgabe, keine Fortschrittsbalken, Fehlerzähler zurücksetzen).
        # Im Hintergrund-Modus löscht sich das Skript gleich selbst, PowerShell hat es dann schon gelesen.
        prelude = ("$ProgressPreference='SilentlyContinue'; "
                   "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}; "
                   "$OutputEncoding=[System.Text.Encoding]::UTF8; "
                   + ("Remove-Item -LiteralPath $PSCommandPath -ErrorAction SilentlyContinue; " if background else "")
                   + "$Error.Clear(); $global:LASTEXITCODE=0\n")
        # Nachspann: PowerShell meldet sonst auch bei Fehlern Exit-Code 0
        epilogue = "\nif ($LASTEXITCODE) { exit $LASTEXITCODE } elseif ($Error.Count) { exit 1 }\n"
        Path(script_file).write_text(prelude + command + epilogue, encoding="utf-8-sig")
        args = [_powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", script_file]
    else:
        args = [shutil.which("bash") or "/bin/sh", "-c", command]

    try:
        if background:
            return _start_background(ctx, args, cwd, command)
        res = _run_process(args, cwd, timeout, ctx.cancel_event)
        if IS_WINDOWS:
            res["err"] = _clean_clixml(res["err"])
        return _format_result(res, timeout, limit)
    except FileNotFoundError as e:
        raise ToolError(f"Shell nicht gefunden: {e}") from None
    finally:
        if script_file and not background:
            try:
                os.remove(script_file)
            except OSError:
                pass


def _start_background(ctx, args, cwd: Path, command: str) -> str:
    log_dir = Path(ctx.data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"hintergrund_{_dt.datetime.now():%Y%m%d_%H%M%S}.log"
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    with open(log_file, "wb") as log:
        proc = subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, **kwargs)
    time.sleep(1.5)
    status = "läuft" if proc.poll() is None else f"bereits beendet (Exit-Code {proc.returncode})"
    preview = ""
    try:
        preview = ANSI_ESCAPE.sub("", _decode(log_file.read_bytes()[-2000:]))
    except OSError:
        pass
    stop = (f"taskkill /T /F /PID {proc.pid}" if IS_WINDOWS else f"kill -- -{proc.pid}")
    return (f"Im Hintergrund gestartet (PID {proc.pid}, Status: {status}).\n"
            f"Ausgabe wird geschrieben nach: {log_file}\n"
            f"Zum Beenden (inklusive aller gestarteten Unterprogramme): {stop}\n"
            + (f"Bisherige Ausgabe:\n{preview}" if preview.strip() else ""))


@tool(
    "run_python",
    "Run a Python 3 script and return what it prints. Good for calculations, data processing, "
    "converting files, creating charts or documents. Only the Python standard library is guaranteed "
    "to be installed. Use print() to output results.",
    {
        "code": {"type": "string", "description": "Complete Python source code."},
        "timeout": {"type": "integer", "description": "Optional time limit in seconds (default 120)."},
    },
    required=["code"],
    confirm=lambda ctx, a: "always" if PY_DANGEROUS.search(str(a.get("code") or "")) else True,
    summary=lambda a: "Python-Code:\n" + a.get("code", ""),
    scope=lambda a: (str(a.get("code", "")), "genau diesen Code"),
)
def run_python(ctx, code: str, timeout: int = 0):
    tmp_dir = Path(ctx.data_dir) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".py", dir=str(tmp_dir))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(code)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    timeout = max(1, min(int(timeout or ctx.cfg.get("befehl_timeout", 120)), 1800))
    try:
        res = _run_process([sys.executable, path], ctx.workdir, timeout, ctx.cancel_event, env=env)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return _format_result(res, timeout, int(ctx.cfg.get("max_ausgabe_zeichen", 8000)))


def _is_web_url(target: str) -> bool:
    return bool(re.match(r"^https?://", (target or "").strip(), re.I))


def _open_scope(target: str):
    """ "Immer erlauben" gilt nur für diese eine Webseite bzw. dieses eine Programm/diese Datei."""
    target = target.strip().strip('"')
    if _is_web_url(target):
        from urllib.parse import urlsplit
        host = urlsplit(target).hostname or target
        return f"web:{host}", host
    return f"open:{target.lower()}", target


def _open_confirm(ctx, args) -> bool:
    target = str(args.get("target") or "").strip()
    # Nur Webseiten, die du selbst im Chat genannt hast, öffnen sich ohne Nachfrage
    return not (_is_web_url(target) and target in ctx.user_urls)


@tool(
    "open_item",
    "Open something on the user's computer the way a double-click would: a website URL in the browser, "
    "a file with its default program, a folder in the file explorer, or start a program by name "
    "(e.g. 'notepad', 'calc', 'chrome', 'spotify', 'explorer'). Returns immediately.",
    {"target": {"type": "string", "description": "URL, file/folder path or program name."}},
    required=["target"],
    confirm=_open_confirm,
    summary=lambda a: f"Öffnen: {a.get('target', '')}",
    scope=lambda a: _open_scope(a.get("target") or ""),
)
def open_item(ctx, target: str):
    target = target.strip().strip('"')
    if _is_web_url(target):
        webbrowser.open(target)
        return f"Im Browser geöffnet: {target}"
    path = ctx.resolve(target)
    looks_like_path = any(sep in target for sep in ("/", "\\")) or path.exists()
    try:
        if path.exists():
            _open_path(str(path))
            return f"Geöffnet: {path}"
        if looks_like_path and not re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            raise ToolError(f"Datei oder Ordner nicht gefunden: {path}")
        # Programmname oder URI wie "ms-settings:" / "spotify:"
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
        elif IS_MAC:
            if ":" in target:
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["open", "-a", target])
        else:
            exe = shutil.which(target)
            if exe:
                subprocess.Popen([exe], start_new_session=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", target], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Gestartet: {target}"
    except ToolError:
        raise
    except OSError as e:
        raise ToolError(f"Konnte '{target}' nicht öffnen: {e}. Tipp: vollständigen Pfad zur .exe angeben "
                        "oder mit find_files danach suchen.") from None


def _open_path(p: str):
    if IS_WINDOWS:
        os.startfile(p)  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ram_gb() -> tuple[float | None, float | None]:
    """(gesamt, frei) in GB."""
    try:
        if IS_WINDOWS:
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMSTAT()
            st.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return st.ullTotalPhys / 1024 ** 3, st.ullAvailPhys / 1024 ** 3
        if IS_MAC:
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=5))
            return total / 1024 ** 3, None
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
        return info["MemTotal"] / 1024 ** 3, info.get("MemAvailable", 0) / 1024 ** 3
    except Exception:
        return None, None


def now_text() -> str:
    now = _dt.datetime.now()
    return f"{WOCHENTAGE[now.weekday()]}, {now:%d.%m.%Y %H:%M} Uhr"


@tool(
    "system_info",
    "Get the current date and time plus information about the computer: operating system, "
    "CPU, RAM, free disk space, user name and important folders.",
    {},
)
def system_info(ctx):
    total, free = _ram_gb()
    lines = [
        f"Datum/Uhrzeit: {now_text()}",
        f"Betriebssystem: {os_name()} ({platform.version()})",
        f"Rechnername: {platform.node()}",
        f"Benutzer: {os.environ.get('USERNAME') or os.environ.get('USER') or '?'}",
        f"Prozessor: {platform.processor() or platform.machine()} ({os.cpu_count()} Threads)",
    ]
    if total:
        lines.append(f"Arbeitsspeicher: {total:.1f} GB" + (f" (davon frei: {free:.1f} GB)" if free else ""))
    try:
        anchor = Path.home().anchor or "/"
        du = shutil.disk_usage(anchor)
        lines.append(f"Laufwerk {anchor}: {du.free / 1024 ** 3:.0f} GB frei von {du.total / 1024 ** 3:.0f} GB")
    except OSError:
        pass
    lines.append(f"Python: {platform.python_version()}  |  Shell für Befehle: {shell_name()}")
    for name, path in known_folders().items():
        lines.append(f"{name}: {path}")
    lines.append(f"Arbeitsordner: {ctx.workdir}")
    return "\n".join(lines)


_FOLDER_IDS = {
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Dokumente": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Bilder": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Musik": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
_FALLBACK_NAMES = {"Desktop": "Desktop", "Dokumente": "Documents", "Downloads": "Downloads",
                   "Bilder": "Pictures", "Musik": "Music", "Videos": "Videos"}
_folders_cache: dict | None = None


def known_folders() -> dict:
    """Echte Pfade von Desktop, Dokumente usw. (auch wenn OneDrive sie verschoben hat)."""
    global _folders_cache
    if _folders_cache is not None:
        return _folders_cache
    result = {}
    home = Path.home()
    for name, guid in _FOLDER_IDS.items():
        path = _windows_known_folder(guid) if IS_WINDOWS else None
        if not path:
            path = _xdg_folder(name) if not IS_WINDOWS and not IS_MAC else None
        if not path:
            candidate = home / _FALLBACK_NAMES[name]
            if not candidate.exists() and IS_WINDOWS:
                onedrive = home / "OneDrive" / _FALLBACK_NAMES[name]
                candidate = onedrive if onedrive.exists() else candidate
            path = str(candidate) if candidate.exists() else None
        if path:
            result[name] = path
    _folders_cache = result
    return result


def _windows_known_folder(guid_text: str) -> str | None:
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(guid_text)
        g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        ptr = ctypes.c_void_p()
        if fn(ctypes.byref(g), 0, None, ctypes.byref(ptr)) != 0:
            return None
        value = ctypes.wstring_at(ptr.value)
        ctypes.windll.ole32.CoTaskMemFree(ptr)
        return value
    except Exception:
        return None


def _xdg_folder(name: str) -> str | None:
    key = {"Desktop": "DESKTOP", "Dokumente": "DOCUMENTS", "Downloads": "DOWNLOAD", "Bilder": "PICTURES",
           "Musik": "MUSIC", "Videos": "VIDEOS"}[name]
    exe = shutil.which("xdg-user-dir")
    if not exe:
        return None
    try:
        out = subprocess.check_output([exe, key], timeout=3, text=True).strip()
    except Exception:
        return None
    return out if out and out != str(Path.home()) and Path(out).exists() else None
