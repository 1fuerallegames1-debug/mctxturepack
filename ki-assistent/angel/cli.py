"""Chat im Terminal / in der Eingabeaufforderung."""

from __future__ import annotations

import os
import queue
import re
import shutil
import sys
import textwrap
import threading

from . import __version__
from .agent import Agent
from .config import save_setting
from .llm import LLMError, OllamaClient
from .regeln import as_text as rules_text

HELP = """\
Befehle:
  /hilfe              diese Hilfe
  /neu                neues Gespräch beginnen (Gedächtnis bleibt)
  /modelle            installierte Modelle anzeigen
  /modell <name>      anderes Modell verwenden (z. B. /modell qwen3:8b)
  /gedaechtnis        anzeigen, was sich {name} gemerkt hat
  /vergiss <nr>       einen gemerkten Eintrag löschen
  /auto an|aus        Aktionen ohne Nachfrage ausführen (Vorsicht!) / wieder nachfragen
  /regeln             {name}s drei Grundregeln anzeigen
  /beenden            {name} beenden (oder Strg+C)
Während {name} arbeitet, bricht Strg+C die aktuelle Aufgabe ab."""


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x9b]")


def safe(text: str) -> str:
    """Steuerzeichen sichtbar machen, damit Ausgaben das Terminal nicht manipulieren können."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")  # Windows-Zeilenenden
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


class Style:
    def __init__(self, enabled: bool):
        e = enabled
        self.reset = "\033[0m" if e else ""
        self.dim = "\033[2m" if e else ""
        self.bold = "\033[1m" if e else ""
        self.cyan = "\033[36m" if e else ""
        self.green = "\033[32m" if e else ""
        self.yellow = "\033[33m" if e else ""
        self.red = "\033[31m" if e else ""
        self.magenta = "\033[35m" if e else ""
        self.clear_line = "\r\033[K" if e else "\r"


def _enable_ansi() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING – schlägt in der alten Konsole fehl
            return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        pass
    return False


class TerminalChat:
    def __init__(self, agent: Agent):
        self.agent = agent
        self.name = agent.cfg.get("name") or "Angel"
        self.s = Style(_enable_ansi())
        self.width = max(40, min(shutil.get_terminal_size((100, 20)).columns, 120))

    # -------------------------------------------------------------- Ausgabe-Helfer

    def out(self, text: str = "", end: str = "\n"):
        sys.stdout.write(text + end)
        sys.stdout.flush()

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def _indent(self, text: str, prefix: str = "  │ ") -> str:
        return "\n".join(prefix + line for line in (text.splitlines() or [""]))

    # -------------------------------------------------------------- Start

    def startup_check(self, web: bool = False) -> bool:
        s, agent = self.s, self.agent
        self.out(f"{s.bold}{s.cyan}{self.name}{s.reset} – deine eigene KI  {s.dim}(v{__version__}){s.reset}")
        for err in agent.plugin_errors:
            self.out(f"{s.yellow}{err}{s.reset}")
        for note in getattr(agent, "notices", []):
            self.out(f"{s.dim}{note}{s.reset}")
        try:
            info = agent.client.check()
        except LLMError as e:
            self.out(f"\n{s.red}{e}{s.reset}")
            self.out(f"\nSo geht's: Ollama von https://ollama.com/download installieren, starten und {self.name} neu öffnen.")
            return False
        if not info["installed"]:
            if not self._handle_missing_model(info["models"]):
                return False
        elif info.get("warning"):
            self.out(f"{s.yellow}{info['warning']}{s.reset}")
        if agent.context_warning():
            self.out(f"{s.yellow}{agent.context_warning()}{s.reset}")
        mode = (f"{s.red}automatisch (ohne Nachfrage){s.reset}" if agent.auto_mode
                else f"{s.green}mit Nachfrage{s.reset}")
        self.out(f"Modell: {s.bold}{agent.client.model}{s.reset}  |  Aktionen: {mode}"
                 + ("" if web else "  |  /hilfe für Befehle"))
        self.out(f"{s.dim}Grundregeln aktiv: 1. Dein Wort ist Gesetz  2. Kein körperlicher Schaden für Menschen  "
                 f"3. Unveränderlich" + ("" if web else "  (/regeln)") + s.reset)
        if agent.auto_mode:
            self.out(f"{s.red}Achtung: {self.name} führt Befehle ohne Rückfrage aus"
                     + ("." if web else f". Mit /auto aus fragt {self.name} wieder nach.") + s.reset)
        return True

    def _handle_missing_model(self, models: list[str]) -> bool:
        s, agent = self.s, self.agent
        model = agent.client.model
        self.out(f"\n{s.yellow}Das Modell '{model}' ist noch nicht heruntergeladen.{s.reset}")
        if isinstance(agent.client, OllamaClient):
            answer = self.ask(f"Jetzt herunterladen? (einmalig, mehrere GB) [j/n] ").strip().lower()
            if answer in ("j", "ja", "y", "yes", ""):
                return self._pull(model)
        if models:
            self.out("Bereits installierte Modelle:")
            for i, m in enumerate(models, 1):
                self.out(f"  {i}. {m}")
            choice = self.ask("Nummer wählen (Enter = abbrechen): ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(models):
                self._switch_model(models[int(choice) - 1], check=False)
                return True
        self.out(f"Tipp: im Terminal 'ollama pull {model}' ausführen und {self.name} neu starten.")
        return False

    def _pull(self, model: str) -> bool:
        s = self.s
        try:
            last = ""
            for p in self.agent.client.pull(model):
                status = p.get("status", "")
                total, done = p.get("total"), p.get("completed")
                if total and done:
                    pct = done / total * 100
                    line = f"{status[:30]:30} {pct:5.1f} %  ({done / 1e9:.2f} / {total / 1e9:.2f} GB)"
                else:
                    line = status
                if line != last:
                    self.out(f"{s.clear_line}{line}", end="" if s.clear_line != "\r" else "\n")
                    last = line
            self.out(f"\n{s.green}Fertig!{s.reset}")
            return True
        except KeyboardInterrupt:
            self.out("\nDownload abgebrochen.")
            return False
        except LLMError as e:
            self.out(f"\n{s.red}{e}{s.reset}")
            return False

    def _switch_model(self, model: str, check: bool = True):
        if check:
            try:
                models = self.agent.client.list_models()
            except LLMError as e:
                self.out(f"{self.s.red}{e}{self.s.reset}")
                return
            if models and model not in models and f"{model}:latest" not in models:
                if isinstance(self.agent.client, OllamaClient):
                    if self.ask(f"'{model}' ist nicht installiert. Herunterladen? [j/n] ").strip().lower() in ("j", "ja", "y"):
                        if not self._pull(model):
                            return
                    else:
                        return
                else:
                    self.out(f"'{model}' ist auf dem Server nicht verfügbar.")
                    return
        self.agent.set_model(model)
        try:
            save_setting("modell", model)
        except Exception:
            pass
        self.out(f"Verwende jetzt: {self.s.bold}{model}{self.s.reset}")

    # -------------------------------------------------------------- Hauptschleife

    def loop(self):
        if not self.startup_check():
            return 1
        while True:
            try:
                self.out("")
                text = self.ask(f"{self.s.bold}{self.s.green}Du ›{self.s.reset} ").strip()
            except (EOFError, KeyboardInterrupt):
                self.out("\nTschüss!")
                return 0
            if not text:
                continue
            if text.startswith("/") or text.lower() in ("exit", "quit", "beenden", "tschüss"):
                if self.command(text) == "quit":
                    self.out("Tschüss!")
                    return 0
                continue
            self.handle(text)

    def command(self, text: str):
        s, agent = self.s, self.agent
        cmd, _, arg = text.partition(" ")
        cmd = cmd.lower().lstrip("/")
        arg = arg.strip()
        if cmd in ("beenden", "exit", "quit", "q", "tschüss"):
            return "quit"
        if cmd in ("hilfe", "help", "h", "?"):
            self.out(HELP.format(name=self.name))
        elif cmd in ("neu", "new", "reset"):
            agent.reset()
            self.out("Neues Gespräch gestartet.")
        elif cmd in ("modelle", "models"):
            try:
                models = agent.client.list_models()
            except LLMError as e:
                self.out(f"{s.red}{e}{s.reset}")
                return None
            for m in models:
                mark = "  ← aktiv" if m in (agent.client.model, f"{agent.client.model}:latest") else ""
                self.out(f"  {m}{mark}")
            if not models:
                self.out("Keine Modelle gefunden.")
        elif cmd in ("modell", "model"):
            if arg:
                self._switch_model(arg)
            else:
                self.out(f"Aktuelles Modell: {agent.client.model}  (wechseln: /modell <name>)")
        elif cmd in ("gedaechtnis", "gedächtnis", "memory"):
            text = agent.memory.prompt_text()
            self.out(text or "Noch nichts gemerkt. Sag z. B.: 'Merk dir, dass ich Max heiße.'")
        elif cmd in ("vergiss", "forget"):
            if arg.isdigit() and agent.memory.remove(int(arg)):
                self.out(f"Eintrag {arg} gelöscht.")
            else:
                self.out("Nutzung: /vergiss <nr>  (Nummern siehe /gedaechtnis)")
        elif cmd in ("regeln", "rules"):
            self.out(f"{s.bold}{self.name}s Grundregeln (fest eingebaut, nicht änderbar):{s.reset}")
            self.out(rules_text(self.name))
        elif cmd == "auto":
            if arg.lower() in ("an", "on", "ein"):
                agent.auto_mode = True
                self.out(f"{s.red}Aktionen werden jetzt OHNE Nachfrage ausgeführt (außer gefährliche Befehle).{s.reset}")
            elif arg.lower() in ("aus", "off"):
                agent.auto_mode = False
                agent.session_allowed.clear()
                self.out(f"{s.green}{self.name} fragt wieder vor jeder Aktion nach.{s.reset}")
            else:
                self.out(f"Automatik ist {'an' if agent.auto_mode else 'aus'}. Nutzung: /auto an  oder  /auto aus")
        else:
            self.out(f"Unbekannter Befehl. {HELP.format(name=self.name)}")
        return None

    def handle(self, text: str):
        s = self.s
        state = {"text_open": False, "thinking_shown": False}

        def open_text():
            if state["thinking_shown"] and not self.agent.cfg.get("denken_anzeigen"):
                self.out(s.clear_line, end="")
                state["thinking_shown"] = False
            if not state["text_open"]:
                self.out(f"\n{s.bold}{s.cyan}{self.name} ›{s.reset} ", end="")
                state["text_open"] = True

        def close_text():
            if state["text_open"]:
                self.out("")
                state["text_open"] = False
            if state["thinking_shown"]:
                if not self.agent.cfg.get("denken_anzeigen"):
                    self.out(s.clear_line, end="")
                else:
                    self.out(s.reset)
                state["thinking_shown"] = False

        def render(ev: dict):
            t = ev["type"]
            if t == "thinking":
                if self.agent.cfg.get("denken_anzeigen"):
                    if not state["thinking_shown"]:
                        close_text()
                        self.out(f"{s.dim}", end="")
                        state["thinking_shown"] = True
                    self.out(safe(ev["text"]), end="")
                elif not state["thinking_shown"]:
                    self.out(f"{s.dim}  … denkt nach …{s.reset}", end="")
                    state["thinking_shown"] = True
            elif t == "text":
                if state["thinking_shown"] and self.agent.cfg.get("denken_anzeigen"):
                    self.out(s.reset)
                    state["thinking_shown"] = False
                open_text()
                self.out(safe(ev["text"]), end="")
            elif t in ("assistant", "done"):
                close_text()
            elif t == "tool_start":
                close_text()
                # die ersten zwei Zeilen, damit z. B. der eigentliche Befehl sichtbar ist
                summary = " · ".join(safe(ev["summary"]).splitlines()[:2]) if ev["summary"] else ""
                self.out(f"{s.magenta}  ⚙ {safe(ev['name'])}{s.reset} {s.dim}{summary[:self.width - 10]}{s.reset}")
            elif t == "tool_result":
                self._show_result(ev)
            elif t == "info":
                close_text()
                self.out(f"{s.dim}{ev['message']}{s.reset}")
            elif t == "error":
                close_text()
                self.out(f"{s.red}{ev['message']}{s.reset}")
            elif t == "cancelled":
                close_text()
                self.out(f"{s.yellow}Abgebrochen.{s.reset}")

        # Die Aufgabe läuft in einem eigenen Thread. So reagiert Strg+C sofort – auch unter Windows,
        # während das Modell noch rechnet. Nachfragen werden hier im Haupt-Thread beantwortet.
        events: queue.Queue = queue.Queue()

        def approve_in_main_thread(req: dict) -> str:
            reply: queue.Queue = queue.Queue(maxsize=1)
            events.put(("approve", req, reply))
            return reply.get()

        def worker():
            try:
                for ev in self.agent.run(text, approve_in_main_thread):
                    events.put(("event", ev, None))
            except BaseException as e:  # sollte nicht passieren – trotzdem nicht stumm abstürzen
                events.put(("event", {"type": "error", "message": f"Interner Fehler: {e}"}, None))
            finally:
                events.put(("end", None, None))

        threading.Thread(target=worker, daemon=True, name="aufgabe").start()
        approve = self.approve_hook(close_text)
        pending_reply = None
        try:
            while True:
                try:
                    kind, payload, reply = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if kind == "end":
                    break
                if kind == "approve":
                    pending_reply = reply
                    reply.put(approve(payload))
                    pending_reply = None
                else:
                    render(payload)
        except KeyboardInterrupt:
            self.agent.cancel()
            if pending_reply is not None and pending_reply.empty():
                pending_reply.put("no")
            close_text()
            self.out(f"\n{s.yellow}Wird abgebrochen …{s.reset}")
            try:  # auf das saubere Ende der Aufgabe warten (offene Nachfragen ablehnen)
                while True:
                    kind, payload, reply = events.get(timeout=30)
                    if kind == "end":
                        break
                    if kind == "approve":
                        reply.put("no")
            except (queue.Empty, KeyboardInterrupt):
                pass
            self.out(f"{s.yellow}Abgebrochen.{s.reset}")

    def _show_result(self, ev: dict):
        s = self.s
        status = ev["status"]
        if status == "denied":
            self.out(f"  {s.yellow}✗ abgelehnt{s.reset}")
            return
        color = s.green if status == "ok" else s.red
        mark = "✓" if status == "ok" else "✗"
        lines = safe(ev["result"] or "").splitlines()
        preview = lines[:4]
        more = f" … (+{len(lines) - 4} Zeilen)" if len(lines) > 4 else ""
        for i, line in enumerate(preview):
            prefix = f"  {color}{mark}{s.reset} " if i == 0 else "    "
            self.out(f"{prefix}{s.dim}{line[:self.width - 6]}{s.reset}")
        if more:
            self.out(f"    {s.dim}{more.strip()}{s.reset}")

    def approve_hook(self, before):
        def approve(req: dict) -> str:
            before()
            s = self.s
            self.out(f"\n  {s.yellow}┌ {self.name} möchte Folgendes tun:{s.reset}")
            # Immer den VOLLSTÄNDIGEN Text zeigen – nichts darf unsichtbar ausgeführt werden
            body = "\n".join(textwrap.fill(line, self.width - 6, replace_whitespace=False) if len(line) > self.width - 6
                             else line for line in safe(req["summary"]).splitlines())
            self.out(self._indent(body, prefix=f"  {s.yellow}│{s.reset} "))
            label = req.get("always_label")
            label = safe(label) if label else label
            if req.get("warning"):
                self.out(f"  {s.yellow}│{s.reset} {s.red}{s.bold}{req['warning']}{s.reset}")
            if label is None:
                options = "[j] ja  [n] nein"
            elif label:
                options = f"[j] ja  [n] nein  [i] immer für {label} (diese Sitzung)"
            else:
                options = "[j] ja  [n] nein  [i] immer erlauben (für diese Sitzung)"
            while True:
                try:
                    answer = self.ask(f"  {s.yellow}└ Erlauben? {options}: {s.reset}").strip().lower()
                except EOFError:
                    return "no"
                if answer in ("j", "ja", "y", "yes"):
                    return "yes"
                if answer in ("n", "nein", "no", ""):
                    return "no"
                if answer in ("i", "immer", "a", "always") and label is not None:
                    return "always"
        return approve


def run_cli(agent: Agent) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    # Passwortschloss: ohne richtiges Passwort tut Angel nichts.
    import getpass

    from . import sicherheit

    def _frage_passwort(rest):
        try:
            return getpass.getpass(f"Passwort (noch {rest} Versuch(e), danach loescht sich Angel): ")
        except (EOFError, KeyboardInterrupt):
            return None

    def _melde_sperre(code):
        if code == sicherheit.GESPERRT:
            print("Angel ist gesperrt und hat seine Daten geloescht. Bitte Angel neu installieren.")
        elif code == sicherheit.ZERSTOERT:
            print("Fuenf falsche Passwoerter. Angel hat seine eigenen Daten geloescht und sich gesperrt.")

    if sicherheit.pruefe_start(agent.ctx.data_dir, _frage_passwort, _melde_sperre) != sicherheit.FREIGEGEBEN:
        return 0

    return TerminalChat(agent).loop() or 0
