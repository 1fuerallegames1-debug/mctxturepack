"""Angels eigenes Fenster auf dem PC (kein Browser, kein Netzwerk).

Zeigt den Chat, führt Aktionen aus, fragt bei kritischen Dingen per Knopf nach und zeigt an, was per
Spracheingabe verstanden wurde.

Die Ablauf-Logik steckt in ChatController (ohne tkinter, dadurch testbar). Das Fenster selbst
(AngelWindow) baut darauf auf; tkinter wird erst in run_gui() geladen, damit der Rest auch ohne
grafische Oberfläche nutzbar bleibt.
"""

from __future__ import annotations

import queue
import secrets
import threading

APPROVAL_TIMEOUT = 15 * 60


class ChatController:
    """Treibt den Agenten in einem Hintergrund-Thread und liefert Ereignisse über eine Queue.

    So kann das Fenster flüssig bleiben, während Angel arbeitet, und Nachfragen werden per Knopf
    beantwortet. Diese Klasse kennt kein tkinter und lässt sich dadurch testen.
    """

    def __init__(self, agent):
        self.agent = agent
        self.events: queue.Queue = queue.Queue()
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.busy = threading.Lock()

    def send(self, text: str) -> bool:
        """Startet eine Aufgabe. Gibt False zurück, wenn Angel noch arbeitet."""
        if not self.busy.acquire(blocking=False):
            return False
        self.events.put({"type": "user", "text": text})
        threading.Thread(target=self._worker, args=(text,), daemon=True, name="aufgabe").start()
        return True

    def _worker(self, text: str):
        def approve(req: dict) -> str:
            aid = secrets.token_hex(8)
            slot = {"event": threading.Event(), "decision": "no"}
            with self._lock:
                self._pending[aid] = slot
            self.events.put({"type": "approval", "approval_id": aid, **req})
            waited = 0.0
            while not slot["event"].wait(0.5):
                waited += 0.5
                if self.agent.cancel_event.is_set() or waited > APPROVAL_TIMEOUT:
                    break
            with self._lock:
                self._pending.pop(aid, None)
            decision = slot["decision"] if slot["event"].is_set() else (
                "no" if self.agent.cancel_event.is_set() else "expired")
            self.events.put({"type": "approval_done", "approval_id": aid, "decision": decision})
            return decision

        try:
            for ev in self.agent.run(text, approve):
                self.events.put(ev)
        except Exception as e:  # darf das Fenster nie zum Absturz bringen
            self.events.put({"type": "error", "message": f"Interner Fehler: {type(e).__name__}: {e}"})
        finally:
            self.events.put({"type": "end"})
            self.busy.release()

    def resolve(self, approval_id: str, decision: str) -> bool:
        with self._lock:
            slot = self._pending.get(approval_id)
        if not slot:
            return False
        slot["decision"] = decision if decision in ("yes", "no", "always") else "no"
        slot["event"].set()
        return True

    def stop(self):
        self.agent.cancel()
        with self._lock:
            slots = list(self._pending.values())
        for slot in slots:
            slot["decision"] = "no"
            slot["event"].set()

    def running(self) -> bool:
        return self.busy.locked()


# --------------------------------------------------------------------------- Das Fenster

def run_gui(agent) -> int:
    import tkinter as tk
    from tkinter import font as tkfont

    from . import sicherheit, voice
    from .regeln import REGELN

    # Passwortschloss: bevor irgendetwas passiert, muss das richtige Passwort kommen.
    _gate_root = tk.Tk()
    _gate_root.withdraw()
    try:
        from tkinter import messagebox, simpledialog

        def _frage_passwort(rest):
            return simpledialog.askstring(
                "Angel – Passwort",
                "Bitte Passwort eingeben.\n"
                f"Noch {rest} Versuch(e) – danach löscht Angel seine eigenen Daten und sperrt sich.",
                show="*", parent=_gate_root)

        def _melde_sperre(code):
            if code == sicherheit.GESPERRT:
                messagebox.showerror(
                    "Angel ist gesperrt",
                    "Angel wurde nach zu vielen falschen Passwörtern gesperrt und hat seine "
                    "eigenen Daten gelöscht. Bitte Angel neu installieren.", parent=_gate_root)
            elif code == sicherheit.ZERSTOERT:
                messagebox.showerror(
                    "Angel hat sich gesperrt",
                    "Fünf falsche Passwörter. Angel hat seine eigenen Daten (Gedächtnis, "
                    "Konto-Zugänge, Browser-Logins) gelöscht und sich dauerhaft gesperrt.",
                    parent=_gate_root)

        _ergebnis = sicherheit.pruefe_start(agent.ctx.data_dir, _frage_passwort, _melde_sperre)
    finally:
        _gate_root.destroy()
    if _ergebnis != sicherheit.FREIGEGEBEN:
        return 0

    cfg = agent.cfg
    name = cfg.get("name") or "Angel"
    controller = ChatController(agent)
    sv = cfg.get("sprachsteuerung") or {}
    mic = voice.Microphone(agent.ctx.data_dir, sv.get("modell_url")
                           or "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip")

    # Farben (heller/dunkler Modus einfach gehalten)
    BG, PANEL, TEXT, MUTED, ACCENT = "#15181c", "#1d2126", "#e6e9ed", "#98a2ad", "#8b85ff"
    USER, OKC, ERRC, WARN = "#2a2750", "#4cc27c", "#f07070", "#f0c75e"

    root = tk.Tk()
    root.title(name)
    root.configure(bg=BG)
    root.geometry("760x640")
    root.minsize(420, 400)
    try:
        from . import icons
        icon_path = agent.ctx.data_dir / "angel-icon.png"
        icon_path.write_bytes(icons.png(128))
        root.iconphoto(True, tk.PhotoImage(file=str(icon_path)))
    except Exception:
        pass

    base = tkfont.nametofont("TkDefaultFont")
    base.configure(size=11)
    mono = tkfont.Font(family="Consolas", size=10)
    bold = base.copy()
    bold.configure(weight="bold")

    # Kopfzeile
    head = tk.Frame(root, bg=PANEL)
    head.pack(fill="x")
    tk.Label(head, text=f"😇 {name}", bg=PANEL, fg=TEXT, font=bold).pack(side="left", padx=10, pady=8)
    mode = "automatisch" if agent.auto_mode else "mit Nachfrage"
    tk.Label(head, text=f"{agent.client.model}  ·  {mode}", bg=PANEL, fg=MUTED).pack(side="left")
    tk.Button(head, text="Neu", command=lambda: new_chat(), bg=PANEL, fg=TEXT,
              relief="flat", activebackground=USER).pack(side="right", padx=8)

    # Chatbereich
    wrap = tk.Frame(root, bg=BG)
    wrap.pack(fill="both", expand=True)
    chat = tk.Text(wrap, bg=BG, fg=TEXT, wrap="word", relief="flat", padx=12, pady=10,
                   insertbackground=TEXT, font=base, state="disabled", cursor="arrow")
    scroll = tk.Scrollbar(wrap, command=chat.yview)
    chat.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    chat.pack(side="left", fill="both", expand=True)
    chat.tag_configure("user", foreground="#c9c6ff", justify="right", spacing1=8, spacing3=2, lmargin1=80)
    chat.tag_configure("assistant", foreground=TEXT, spacing1=6, spacing3=2)
    chat.tag_configure("name", foreground=ACCENT, font=bold, spacing1=8)
    chat.tag_configure("tool", foreground=MUTED, font=mono, spacing1=2)
    chat.tag_configure("ok", foreground=OKC, font=mono)
    chat.tag_configure("err", foreground=ERRC, font=mono)
    chat.tag_configure("info", foreground=MUTED, spacing1=4)

    # Transkript-Zeile (zeigt, was per Sprache verstanden wurde)
    heard = tk.Label(root, text="", bg=BG, fg=WARN, anchor="w")
    heard.pack(fill="x", padx=12)

    # Eingabezeile
    foot = tk.Frame(root, bg=PANEL)
    foot.pack(fill="x")
    entry = tk.Text(foot, height=2, bg="#262b31", fg=TEXT, insertbackground=TEXT, relief="flat",
                    wrap="word", font=base, padx=8, pady=6)
    entry.pack(side="left", fill="both", expand=True, padx=(10, 6), pady=10)
    mic_btn = tk.Button(foot, text="🎤", width=3, relief="flat", bg=PANEL, fg=TEXT, activebackground=USER)
    mic_btn.pack(side="left", padx=2, pady=10)
    send_btn = tk.Button(foot, text="Senden", bg=ACCENT, fg="#0d1117", relief="flat", width=9,
                         activebackground="#a39dff")
    send_btn.pack(side="right", padx=(6, 10), pady=10)

    hint = tk.Label(root, text="Enter = senden · Umschalt+Enter = neue Zeile · kritische Aktionen "
                               "werden per Knopf bestätigt", bg=PANEL, fg=MUTED, anchor="w")
    hint.pack(fill="x")

    state = {"busy": False, "stream": False, "approvals": {}}

    # ---- Chat-Ausgabe
    def write(text, tag=None, newline=True):
        chat.configure(state="normal")
        chat.insert("end", text + ("\n" if newline else ""), (tag,) if tag else ())
        chat.configure(state="disabled")
        chat.see("end")

    def add_approval(ev):
        chat.configure(state="normal")
        box = tk.Frame(chat, bg=("#3d1f1f" if ev.get("dangerous") else "#3a3218"),
                       highlightbackground=(ERRC if ev.get("dangerous") else WARN), highlightthickness=1)
        title = f"⚠ {name} möchte etwas Kritisches tun:" if ev.get("dangerous") else f"{name} möchte Folgendes tun:"
        tk.Label(box, text=title, bg=box["bg"], fg=TEXT, font=bold, anchor="w", justify="left",
                 wraplength=560).pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(box, text=ev.get("summary", ""), bg=box["bg"], fg=TEXT, font=mono, anchor="w",
                 justify="left", wraplength=560).pack(fill="x", padx=10)
        if ev.get("warning"):
            tk.Label(box, text=ev["warning"], bg=box["bg"], fg=ERRC, anchor="w", justify="left",
                     wraplength=560).pack(fill="x", padx=10, pady=(4, 0))
        btns = tk.Frame(box, bg=box["bg"])
        btns.pack(fill="x", padx=10, pady=8)
        aid = ev["approval_id"]
        done = tk.Label(box, text="", bg=box["bg"], fg=MUTED, anchor="w")

        def decide(decision, label):
            for b in btns.winfo_children():
                b.configure(state="disabled")
            controller.resolve(aid, decision)
            done.configure(text=label)
            done.pack(fill="x", padx=10, pady=(0, 6))

        tk.Button(btns, text="Erlauben", bg=ACCENT, fg="#0d1117", relief="flat",
                  command=lambda: decide("yes", "✓ erlaubt")).pack(side="left")
        label = ev.get("always_label")
        if label is not None and not ev.get("dangerous") and label:
            tk.Button(btns, text=f"Immer für {label}", bg=PANEL, fg=TEXT, relief="flat",
                      command=lambda: decide("always", "✓ für diese Sitzung erlaubt")).pack(side="left", padx=6)
        tk.Button(btns, text="Ablehnen", bg=ERRC, fg="#0d1117", relief="flat",
                  command=lambda: decide("no", "✗ abgelehnt")).pack(side="left", padx=6)
        chat.window_create("end", window=box, padx=4, pady=6)
        chat.insert("end", "\n")
        chat.configure(state="disabled")
        chat.see("end")
        state["approvals"][aid] = done

    # ---- Ereignisse aus dem Controller holen (alle 60 ms)
    def pump():
        try:
            while True:
                ev = controller.events.get_nowait()
                handle(ev)
        except queue.Empty:
            pass
        root.after(60, pump)

    def handle(ev):
        t = ev["type"]
        if t == "user":
            write("Du: " + ev["text"], "user")
        elif t == "thinking":
            pass  # Nachdenken wird im Fenster nicht angezeigt
        elif t == "text":
            if not state["stream"]:
                write(f"{name}:", "name")
                state["stream"] = True
            write(ev["text"], "assistant", newline=False)
        elif t == "assistant":
            if state["stream"]:
                write("", "assistant")
            state["stream"] = False
        elif t == "tool_start":
            summary = (ev.get("summary") or "").splitlines()
            write(f"  ⚙ {ev['name']}  {summary[0] if summary else ''}", "tool")
        elif t == "tool_result":
            icon = {"ok": "✓", "denied": "✗", "cancelled": "✗", "error": "✗"}.get(ev["status"], "✓")
            first = (ev.get("result") or "").splitlines()
            write(f"  {icon} {first[0] if first else ''}", "ok" if ev["status"] == "ok" else "err")
        elif t == "approval":
            add_approval(ev)
        elif t == "approval_done":
            box = state["approvals"].pop(ev["approval_id"], None)
            if box and not box.winfo_viewable():
                pass
        elif t == "info":
            write(ev["message"], "info")
        elif t == "error":
            write(ev["message"], "err")
        elif t == "cancelled":
            state["stream"] = False
            write("Abgebrochen.", "info")
        elif t == "end":
            state["stream"] = False
            set_busy(False)

    def set_busy(b):
        state["busy"] = b
        send_btn.configure(text="Stopp" if b else "Senden", bg=ERRC if b else ACCENT)

    # ---- Senden / Stoppen
    def do_send(event=None):
        if state["busy"]:
            controller.stop()
            return "break"
        text = entry.get("1.0", "end").strip()
        if not text:
            return "break"
        entry.delete("1.0", "end")
        heard.configure(text="")
        if controller.send(text):
            set_busy(True)
        return "break"

    def new_chat():
        if state["busy"]:
            write("Bitte zuerst die laufende Aufgabe stoppen.", "info")
            return
        agent.reset()
        chat.configure(state="normal")
        chat.delete("1.0", "end")
        chat.configure(state="disabled")
        state["approvals"].clear()
        show_welcome()

    send_btn.configure(command=do_send)
    entry.bind("<Return>", do_send)
    entry.bind("<Shift-Return>", lambda e: None)

    # ---- Spracheingabe
    def on_partial(text):
        root.after(0, lambda: heard.configure(text="🎤 " + text))

    def on_final(text):
        def apply():
            heard.configure(text="🎤 verstanden: " + text)
            entry.delete("1.0", "end")
            entry.insert("1.0", text)
            stop_mic()
            if (cfg.get("sprachsteuerung") or {}).get("automatisch_senden"):
                do_send()
        root.after(0, apply)

    def on_voice_error(msg):
        root.after(0, lambda: (write(msg, "info"), stop_mic()))

    def stop_mic():
        mic.stop()
        mic_btn.configure(text="🎤", bg=PANEL)
        if not heard.cget("text").startswith("🎤 verstanden"):
            heard.configure(text="")

    def toggle_mic():
        if mic.listening():
            stop_mic()
            return
        ok, msg = voice.available()
        if not ok:
            write(msg, "info")
            return
        mic_btn.configure(text="● Höre…", bg=ERRC)
        heard.configure(text="🎤 …")

        def prepare():
            try:
                if not voice.have_model(agent.ctx.data_dir):
                    root.after(0, lambda: heard.configure(text="Lade einmalig das Sprachmodell herunter …"))
                    mic.ensure_ready(progress=lambda p: root.after(
                        0, lambda: heard.configure(text=f"Sprachmodell wird geladen … {int(p * 100)} %")))
                mic.start(on_partial, on_final, on_voice_error)
                root.after(0, lambda: heard.configure(text="🎤 …"))
            except voice.VoiceError as e:
                root.after(0, lambda: (write(str(e), "info"), stop_mic()))

        threading.Thread(target=prepare, daemon=True).start()

    mic_btn.configure(command=toggle_mic)

    def show_welcome():
        write(f"Hallo! Ich bin {name}. Ich laufe auf deinem PC und erledige Dinge für dich – "
              "auf Deutsch oder Englisch.", "info")
        write("Meine Grundregeln: " + "  ".join(f"{i}. {r.format(name=name)}"
                                                 for i, r in enumerate(REGELN, 1)), "info")
        for err in getattr(agent, "plugin_errors", []):
            write(err, "err")
        for note in getattr(agent, "notices", []):
            write(note, "info")
        if agent.auto_mode:
            write(f"Automatik ist an: {name} handelt ohne Nachfrage, fragt aber bei kritischen Dingen "
                  "(z. B. PC-Einstellungen ändern, Discord-Ban/Kick) immer.", "info")

    show_welcome()
    entry.focus_set()
    root.after(60, pump)
    root.protocol("WM_DELETE_WINDOW", lambda: (controller.stop(), root.destroy()))
    root.mainloop()
    controller.stop()
    return 0
