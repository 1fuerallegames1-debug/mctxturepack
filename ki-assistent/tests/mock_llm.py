"""Ein simulierter KI-Server für die Tests (spricht Ollama- und OpenAI-Format)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def reply(content="", thinking="", tool_calls=None, **extra):
    """Eine geskriptete Antwort. tool_calls = [("name", {args}), ...]"""
    return dict(content=content, thinking=thinking, tool_calls=tool_calls or [], **extra)


class MockLLM:
    def __init__(self, script=None, models=("test:latest",), capabilities=("completion", "tools")):
        self.script = list(script or [])
        self.requests: list[dict] = []
        self.models = list(models)
        self.capabilities = list(capabilities)
        self.lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _json(self, status, obj):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._json(200, {"models": [{"name": m} for m in mock.models]})
                elif self.path == "/v1/models":
                    self._json(200, {"data": [{"id": m} for m in mock.models]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/show":
                    self._json(200, {"capabilities": mock.capabilities})
                    return
                if self.path == "/api/pull":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    for done in (0, 50, 100):
                        self._send(json.dumps({"status": "pulling abc", "total": 100, "completed": done}) + "\n", 0)
                    self._send(json.dumps({"status": "success"}) + "\n", 0)
                    mock.models.append(payload.get("model", ""))
                    return
                if self.path not in ("/api/chat", "/v1/chat/completions"):
                    self._json(404, {"error": "not found"})
                    return
                with mock.lock:
                    mock.requests.append(payload)
                    item = mock.script.pop(0) if mock.script else reply("(Skript leer)")
                if item.get("header_delay"):
                    time.sleep(item["header_delay"])  # z. B. Modell wird noch geladen
                if item.get("disconnect"):
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                if item.get("http_error"):
                    self._json(item["http_error"], {"error": item.get("error", "boom")})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson" if self.path == "/api/chat" else "text/event-stream")
                self.end_headers()
                try:
                    if self.path == "/api/chat":
                        self._ollama(item)
                    else:
                        self._openai(item)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _send(self, line: str, delay: float):
                self.wfile.write(line.encode())
                self.wfile.flush()
                if delay:
                    time.sleep(delay)

            def _ollama(self, item):
                delay = item.get("delay", 0)
                for chunk in _chunks(item["thinking"]):
                    self._send(json.dumps({"message": {"role": "assistant", "content": "", "thinking": chunk},
                                           "done": False}) + "\n", delay)
                for chunk in _chunks(item["content"]):
                    self._send(json.dumps({"message": {"role": "assistant", "content": chunk}, "done": False}) + "\n", delay)
                if item.get("truncate"):
                    return  # Server beendet die Verbindung ohne "done"
                if item.get("stream_error"):
                    self._send(json.dumps({"error": item["stream_error"]}) + "\n", 0)
                    return
                if item["tool_calls"]:
                    calls = [{"function": {"name": n, "arguments": a}} for n, a in item["tool_calls"]]
                    self._send(json.dumps({"message": {"role": "assistant", "content": "", "tool_calls": calls},
                                           "done": False}) + "\n", delay)
                self._send(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                                       "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 5}) + "\n", 0)

            def _openai(self, item):
                delay = item.get("delay", 0)

                def ev(delta, finish=None):
                    obj = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self._send("data: " + json.dumps(obj) + "\n\n", delay)

                for chunk in _chunks(item["thinking"]):
                    ev({"reasoning_content": chunk})
                for chunk in _chunks(item["content"]):
                    ev({"content": chunk})
                for idx, (name, args) in enumerate(item["tool_calls"]):
                    raw = json.dumps(args)
                    half = len(raw) // 2
                    ev({"tool_calls": [{"index": idx, "id": f"call_{idx}", "type": "function",
                                        "function": {"name": name, "arguments": raw[:half]}}]})
                    ev({"tool_calls": [{"index": idx, "function": {"arguments": raw[half:]}}]})
                ev({}, "tool_calls" if item["tool_calls"] else "stop")
                self._send("data: [DONE]\n\n", 0)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _chunks(text: str, n: int = 3):
    if not text:
        return []
    size = max(1, len(text) // n)
    return [text[i:i + size] for i in range(0, len(text), size)]
