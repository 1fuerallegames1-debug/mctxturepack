"""Verbindung zum lokalen KI-Modell (Ollama oder ein OpenAI-kompatibler Server).

Intern wird überall dasselbe Nachrichtenformat verwendet:
  {"role": "system" | "user", "content": str}
  {"role": "assistant", "content": str, "thinking": str,
   "tool_calls": [{"id": str, "name": str, "arguments": dict | None, "raw_arguments": str}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Die Clients übersetzen das in das jeweilige API-Format.
"""

from __future__ import annotations

import http.client
import json
import re
import select
import socket
import urllib.error
import urllib.request
import uuid
from typing import Iterator
from urllib.parse import urlsplit

USER_AGENT = "Angel-Assistent/1.0"


class LLMError(Exception):
    """Fehler mit einer verständlichen (deutschen) Meldung für den Benutzer."""


class Cancelled(Exception):
    """Der Benutzer hat die laufende Antwort abgebrochen."""


# --------------------------------------------------------------------------
# HTTP-Hilfsfunktionen
# --------------------------------------------------------------------------

def _error_text(body: str) -> str:
    """Holt die eigentliche Fehlermeldung aus einer JSON-Fehlerantwort."""
    try:
        parsed = json.loads(body)
        err = parsed.get("error", parsed) if isinstance(parsed, dict) else parsed
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err, ensure_ascii=False)
        return str(err)
    except Exception:
        return body


def _no_connection(url: str, reason) -> LLMError:
    base = url.split("/api/")[0].split("/v1/")[0]
    return LLMError(
        f"Keine Verbindung zum KI-Server unter {base} ({reason}).\n"
        "Läuft Ollama? Starte die Ollama-App (oder im Terminal: ollama serve) und versuche es erneut."
    )


def _request(url: str, payload: dict | None = None, headers: dict | None = None,
             timeout: float = 600, method: str | None = None):
    data = None
    hdrs = {"User-Agent": USER_AGENT}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise LLMError(_explain_http_error(e.code, _error_text(body), url)) from None
    except urllib.error.URLError as e:
        raise _no_connection(url, getattr(e, "reason", e)) from None
    except (socket.timeout, TimeoutError):
        raise LLMError("Der KI-Server hat zu lange nicht geantwortet (Zeitüberschreitung).") from None
    except (http.client.HTTPException, ConnectionError) as e:
        raise LLMError(f"Der KI-Server hat die Verbindung unerwartet beendet ({e}). Läuft Ollama noch?") from None


def _explain_http_error(code: int, message: str, url: str) -> str:
    low = message.lower()
    if "does not support tools" in low:
        return (
            f"Das Modell unterstützt keine Werkzeuge (Tool-Calling): {message}\n"
            "Nötig ist ein Modell mit Werkzeug-Unterstützung, z. B. hermes3:8b, qwen3:8b oder gemma4.\n"
            "Liste: https://ollama.com/search?c=tools"
        )
    if code == 404 and ("not found" in low or "model" in low):
        return (
            f"Modell nicht gefunden: {message}\n"
            "Lade es herunter mit:  ollama pull <modellname>   (installierte Modelle zeigt /modelle)."
        )
    if code in (401, 403):
        return f"Zugriff verweigert ({code}): {message}. Prüfe 'api_schluessel' in config.json."
    if code == 500 and ("memory" in low or "cuda" in low or "alloc" in low):
        return (
            f"Der KI-Server hat nicht genug Speicher ({message}).\n"
            "Tipp: kleineres Modell wählen oder 'kontext_laenge' in config.json verringern."
        )
    return f"Fehler vom KI-Server (HTTP {code}): {message or 'keine Details'}"


def _iter_lines(resp, cancel_event=None) -> Iterator[str]:
    """Liest eine gestreamte Antwort Zeile für Zeile (bricht bei Abbruch ab)."""
    try:
        for raw in resp:
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            line = raw.decode("utf-8", "replace").strip()
            if line:
                yield line
    except Cancelled:
        raise
    except (OSError, ValueError, AttributeError, http.client.HTTPException) as e:
        # Wird auch ausgelöst, wenn die Verbindung beim Abbrechen geschlossen wurde
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled() from None
        raise LLMError(f"Verbindung zum KI-Server unterbrochen: {e}") from None
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()


# --------------------------------------------------------------------------
# <think>...</think>-Filter (manche Modelle schreiben ihr Nachdenken in den Text)
# --------------------------------------------------------------------------

class ThinkTagFilter:
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf = ""
        self.in_think = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self.buf += text
        out: list[tuple[str, str]] = []
        while self.buf:
            tag = self.CLOSE if self.in_think else self.OPEN
            kind = "thinking" if self.in_think else "text"
            i = self.buf.find(tag)
            if i >= 0:
                if i:
                    out.append((kind, self.buf[:i]))
                self.buf = self.buf[i + len(tag):]
                self.in_think = not self.in_think
                continue
            keep = 0  # ein evtl. angeschnittenes Tag am Ende zurückhalten
            for k in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-k:]):
                    keep = k
                    break
            emit = self.buf[: len(self.buf) - keep]
            if emit:
                out.append((kind, emit))
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self.buf:
            return []
        out = [("thinking" if self.in_think else "text", self.buf)]
        self.buf = ""
        return out


# --------------------------------------------------------------------------
# Notfall-Erkennung: Werkzeugaufrufe, die als Text statt als tool_call kommen
# --------------------------------------------------------------------------

_TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_FENCED = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def extract_text_tool_calls(content: str, tool_names) -> tuple[list[dict], str]:
    """Erkennt Werkzeugaufrufe, die ein Modell als JSON-Text ausgegeben hat.

    Gibt (aufrufe, resttext) zurück. Ist nichts eindeutig erkennbar, bleibt der Text unverändert.
    """
    text = (content or "").strip()
    if not text:
        return [], content
    names = set(tool_names)
    candidates: list[str] = []
    rest = text
    tagged = _TOOL_CALL_TAG.findall(text)
    if tagged:
        candidates = tagged
        rest = _TOOL_CALL_TAG.sub("", text).strip()
    else:
        m = _FENCED.fullmatch(text)
        body = m.group(1) if m else text
        if body[:1] in "{[":
            candidates = [body]
            rest = ""
    calls: list[dict] = []
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        for item in obj if isinstance(obj, list) else [obj]:
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = item.get("name") or fn.get("name")
            args = item.get("arguments", item.get("parameters", fn.get("arguments", {})))
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except ValueError:
                    continue
            if name in names and isinstance(args, dict):
                calls.append({"id": f"call_{uuid.uuid4().hex[:12]}", "name": name,
                              "arguments": args, "raw_arguments": json.dumps(args, ensure_ascii=False)})
    if not calls:
        return [], content
    return calls, rest


def _parse_arguments(raw) -> tuple[dict | None, str]:
    """Wandelt Argumente (dict oder JSON-String) in ein dict um. None = ungültig."""
    if isinstance(raw, dict):
        return raw, json.dumps(raw, ensure_ascii=False)
    if raw is None or raw == "":
        return {}, "{}"
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except ValueError:
            return None, raw
        return (val, raw) if isinstance(val, dict) else (None, raw)
    return None, str(raw)


# --------------------------------------------------------------------------
# Basis-Client
# --------------------------------------------------------------------------

class BaseClient:
    provider = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model = cfg["modell"]
        self.timeout = float(cfg.get("antwort_timeout", 600))
        self._active_conn = None

    def abort(self):
        """Für den Stopp-Knopf: laufende Verbindung von außen sofort beenden."""
        conn = self._active_conn
        sock = getattr(conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # weckt auch blockierte Lesevorgänge auf
            except OSError:
                pass

    def _close_conn(self):
        conn, self._active_conn = self._active_conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _open_stream(self, url: str, payload: dict, headers: dict, cancel_event=None):
        """Startet eine gestreamte Anfrage. Ein Abbruch wirkt auch, während das Modell noch lädt."""
        parts = urlsplit(url)
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(parts.hostname, parts.port, timeout=self.timeout)
        self._active_conn = conn
        hdrs = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        hdrs.update(headers or {})
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            conn.request("POST", path, body=json.dumps(payload).encode("utf-8"), headers=hdrs)
            waited = 0.0
            while True:  # auf das erste Byte warten, dabei regelmäßig auf "Stopp" prüfen
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled()
                sock = conn.sock
                if sock is None or getattr(sock, "pending", lambda: 0)() or select.select([sock], [], [], 0.5)[0]:
                    break
                waited += 0.5
                if waited > self.timeout:
                    raise socket.timeout("keine Antwort")
            resp = conn.getresponse()
        except Cancelled:
            self._close_conn()
            raise
        except (OSError, ValueError, http.client.HTTPException) as e:
            self._close_conn()
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled() from None
            if isinstance(e, (socket.timeout, TimeoutError)):
                raise LLMError("Der KI-Server hat zu lange nicht geantwortet (Zeitüberschreitung).") from None
            if isinstance(e, (ConnectionRefusedError, socket.gaierror)) or not isinstance(e, http.client.HTTPException):
                raise _no_connection(url, e) from None
            raise LLMError(f"Der KI-Server hat die Verbindung unerwartet beendet ({e}). Läuft Ollama noch?") from None
        if resp.status >= 400:
            try:
                body = resp.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            self._close_conn()
            raise LLMError(_explain_http_error(resp.status, _error_text(body), url))
        return resp

    def chat_stream(self, messages, tools, cancel_event=None) -> Iterator[tuple[str, object]]:
        """Liefert ("thinking", text), ("text", text) und zum Schluss ("done", nachricht)."""
        raise NotImplementedError

    def _finish(self, content: str, thinking: str, tool_calls: list[dict], stats: dict, tool_names):
        content = content or ""
        if not tool_calls and tool_names:
            found, rest = extract_text_tool_calls(content, tool_names)
            if found:
                tool_calls, content = found, rest
        for call in tool_calls:
            if not call.get("id"):
                call["id"] = f"call_{uuid.uuid4().hex[:12]}"  # eindeutig über das ganze Gespräch
        return {"role": "assistant", "content": content.strip(), "thinking": thinking.strip(),
                "tool_calls": tool_calls, "stats": stats}

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def check(self) -> dict:
        """Prüft Verbindung und Modell. Gibt {"ok", "models", "installed", "warning"} zurück."""
        models = self.list_models()
        installed = any(_same_model(self.model, m) for m in models) if models else True
        return {"ok": True, "models": models, "installed": installed, "warning": ""}


def _same_model(wanted: str, have: str) -> bool:
    if wanted == have:
        return True
    if ":" not in wanted:
        return have == wanted + ":latest"
    return False


# --------------------------------------------------------------------------
# Ollama (empfohlen) – nutzt die native API, damit die Kontextlänge stimmt
# --------------------------------------------------------------------------

class OllamaClient(BaseClient):
    provider = "ollama"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        base = cfg.get("server_url") or "http://127.0.0.1:11434"
        base = base.rstrip("/")
        for suffix in ("/api", "/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        self.base = base

    def list_models(self) -> list[str]:
        with _request(self.base + "/api/tags", timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        return sorted(m.get("name") or m.get("model", "") for m in data.get("models", []))

    def show(self, model: str | None = None) -> dict:
        with _request(self.base + "/api/show", {"model": model or self.model}, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def check(self) -> dict:
        info = super().check()
        if info["installed"]:
            try:
                caps = self.show().get("capabilities")
                if isinstance(caps, list) and "tools" not in caps:
                    info["warning"] = (
                        f"Achtung: '{self.model}' unterstützt laut Ollama keine Werkzeuge. "
                        "Damit ist nur Chatten möglich, aber keine Aktionen am PC. Empfohlen: hermes3:8b, qwen3:8b oder gemma4."
                    )
            except LLMError:
                pass
        return info

    def pull(self, model: str | None = None) -> Iterator[dict]:
        """Lädt ein Modell herunter und liefert Fortschrittsmeldungen."""
        resp = _request(self.base + "/api/pull", {"model": model or self.model, "stream": True}, timeout=3600)
        with resp:
            for line in _iter_lines(resp):
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("error"):
                    raise LLMError(f"Download fehlgeschlagen: {obj['error']}")
                yield obj

    def _convert(self, messages: list[dict]) -> list[dict]:
        last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
        out = []
        for i, m in enumerate(messages):
            role = m["role"]
            if role == "assistant":
                d = {"role": "assistant", "content": m.get("content") or ""}
                # Nachdenken nur innerhalb der aktuellen Aufgabe zurückgeben (spart Kontext)
                if m.get("thinking") and i > last_user:
                    d["thinking"] = m["thinking"]
                if m.get("tool_calls"):
                    d["tool_calls"] = [
                        {"type": "function",
                         "function": {"name": c["name"], "arguments": c.get("arguments") or {}}}
                        for c in m["tool_calls"]
                    ]
                out.append(d)
            elif role == "tool":
                out.append({"role": "tool", "tool_name": m.get("name", ""), "content": m.get("content", "")})
            else:
                out.append({"role": role, "content": m.get("content", "")})
        return out

    def chat_stream(self, messages, tools, cancel_event=None):
        options = {"num_ctx": int(self.cfg.get("kontext_laenge") or 16384)}
        if self.cfg.get("temperatur") is not None:
            options["temperature"] = float(self.cfg["temperatur"])
        payload = {
            "model": self.model,
            "messages": self._convert(messages),
            "stream": True,
            "options": options,
            "keep_alive": self.cfg.get("im_speicher_halten", "30m"),
        }
        if tools:
            payload["tools"] = tools
        if self.cfg.get("denken") is not None:
            payload["think"] = self.cfg["denken"]

        tool_names = [t["function"]["name"] for t in tools or []]
        content, thinking, calls, stats = [], [], [], {}
        filt = ThinkTagFilter()
        resp = self._open_stream(self.base + "/api/chat", payload, {}, cancel_event)
        saw_done = False
        try:
            for line in _iter_lines(resp, cancel_event):
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("error"):
                    raise LLMError(_explain_http_error(500, str(obj["error"]), self.base))
                msg = obj.get("message") or {}
                if msg.get("thinking"):
                    thinking.append(msg["thinking"])
                    yield ("thinking", msg["thinking"])
                if msg.get("content"):
                    for kind, part in filt.feed(msg["content"]):
                        (thinking if kind == "thinking" else content).append(part)
                        yield (kind, part)
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    args, raw = _parse_arguments(fn.get("arguments"))
                    calls.append({"id": tc.get("id") or "", "name": fn.get("name", ""),
                                  "arguments": args, "raw_arguments": raw})
                if obj.get("done"):
                    saw_done = True
                    stats = {k: obj.get(k) for k in ("prompt_eval_count", "eval_count", "done_reason")}
                    break
        finally:
            self._close_conn()
        if not saw_done:
            raise LLMError("Die Antwort brach mittendrin ab – die Verbindung zum KI-Server wurde unterbrochen. "
                           "Läuft Ollama noch?")
        for kind, part in filt.flush():
            (thinking if kind == "thinking" else content).append(part)
            yield (kind, part)
        yield ("done", self._finish("".join(content), "".join(thinking), calls, stats, tool_names))


# --------------------------------------------------------------------------
# OpenAI-kompatible Server (LM Studio, llama.cpp-Server, vLLM, Jan, ...)
# --------------------------------------------------------------------------

class OpenAIClient(BaseClient):
    provider = "openai"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        base = (cfg.get("server_url") or "http://127.0.0.1:1234/v1").rstrip("/")
        if not re.search(r"/v\d+$", base) and base.count("/") <= 2:
            base += "/v1"  # nur Host angegeben -> Standardpfad ergänzen
        self.base = base
        self.headers = {}
        if cfg.get("api_schluessel"):
            self.headers["Authorization"] = f"Bearer {cfg['api_schluessel']}"

    def list_models(self) -> list[str]:
        try:
            with _request(self.base + "/models", headers=self.headers, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
        except LLMError as e:
            if "HTTP 404" in str(e):
                return []  # manche Server haben keine Modell-Liste
            raise
        return sorted(m.get("id", "") for m in data.get("data", []))

    def _convert(self, messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            role = m["role"]
            if role == "assistant":
                d = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    d["tool_calls"] = [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"],
                                      # immer gültiges JSON zurückschicken (manche Server prüfen das streng)
                                      "arguments": json.dumps(c["arguments"] if c.get("arguments") is not None
                                                              else {}, ensure_ascii=False)}}
                        for c in m["tool_calls"]
                    ]
                out.append(d)
            elif role == "tool":
                out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""),
                            "content": m.get("content", "")})
            else:
                out.append({"role": role, "content": m.get("content", "")})
        return out

    def chat_stream(self, messages, tools, cancel_event=None):
        payload = {"model": self.model, "messages": self._convert(messages), "stream": True}
        if tools:
            payload["tools"] = tools
        if self.cfg.get("temperatur") is not None:
            payload["temperature"] = float(self.cfg["temperatur"])
        if self.cfg.get("denken") is False:
            payload["reasoning_effort"] = "none"

        tool_names = [t["function"]["name"] for t in tools or []]
        content, thinking, slots, stats = [], [], {}, {}
        filt = ThinkTagFilter()
        resp = self._open_stream(self.base + "/chat/completions", payload, self.headers, cancel_event)
        saw_end = False
        try:
            for line in _iter_lines(resp, cancel_event):
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    saw_end = True
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get("error"):
                    err = obj["error"]
                    raise LLMError(f"Fehler vom KI-Server: {err.get('message') if isinstance(err, dict) else err}")
                if obj.get("usage"):
                    stats["usage"] = obj["usage"]
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or choices[0].get("message") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    thinking.append(reasoning)
                    yield ("thinking", reasoning)
                if isinstance(delta.get("content"), str) and delta["content"]:
                    for kind, part in filt.feed(delta["content"]):
                        (thinking if kind == "thinking" else content).append(part)
                        yield (kind, part)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", len(slots))
                    slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name") and fn["name"] != slot["name"]:
                        slot["name"] += fn["name"]
                    args = fn.get("arguments")
                    if isinstance(args, dict):
                        slot["arguments"] = json.dumps(args)
                    elif isinstance(args, str):
                        slot["arguments"] += args
                if choices[0].get("finish_reason"):
                    saw_end = True
                    stats["done_reason"] = choices[0]["finish_reason"]
        finally:
            self._close_conn()
        if not saw_end:
            raise LLMError("Die Antwort brach mittendrin ab – die Verbindung zum KI-Server wurde unterbrochen.")
        for kind, part in filt.flush():
            (thinking if kind == "thinking" else content).append(part)
            yield (kind, part)
        calls = []
        for idx in sorted(slots):
            s = slots[idx]
            args, raw = _parse_arguments(s["arguments"])
            calls.append({"id": s["id"], "name": s["name"], "arguments": args, "raw_arguments": raw})
        yield ("done", self._finish("".join(content), "".join(thinking), calls, stats, tool_names))


def make_client(cfg: dict) -> BaseClient:
    provider = (cfg.get("anbieter") or "ollama").lower()
    if provider == "ollama":
        return OllamaClient(cfg)
    if provider in ("openai", "lmstudio", "llamacpp", "openai-kompatibel"):
        return OpenAIClient(cfg)
    raise LLMError(f"Unbekannter Anbieter '{provider}' in config.json (erlaubt: ollama, openai).")
