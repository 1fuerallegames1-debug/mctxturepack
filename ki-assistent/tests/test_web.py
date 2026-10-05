import http.client
import json
import threading
import time
import unittest

from angel.agent import Agent
from angel.web import WebApp, load_token
from tests.helpers import TempDirTest, make_cfg
from tests.mock_llm import MockLLM, reply


class WebTest(TempDirTest):
    host = "127.0.0.1"
    phone = False

    def setUp(self):
        super().setUp()
        self.mock = MockLLM([])
        self.addCleanup(self.mock.close)
        self.agent = Agent(make_cfg(self.tmp, server_url=self.mock.url))
        self.app = WebApp(self.agent, host=self.host, port=0, token="geheim-schluessel-1234567890", phone=self.phone)
        self.httpd = self.app.make_server()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.agent.cancel)

    def request(self, method, path, body=None, token="geheim-schluessel-1234567890", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=40)
        headers = {}
        if token:
            headers["X-Angel-Token"] = token
        if host:
            headers["Host"] = host
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        return conn.getresponse()

    def json(self, method, path, body=None, **kw):
        resp = self.request(method, path, body, **kw)
        return resp.status, json.loads(resp.read() or b"{}")

    def follow(self, run_id, on_event=None, since=0):
        """Liest alle Ereignisse einer Aufgabe per Long-Polling (wie die Webseite)."""
        events = []
        for _ in range(200):
            status, data = self.json("GET", f"/api/events?run={run_id}&since={since}")
            self.assertEqual(status, 200, data)
            for ev in data["events"]:
                events.append(ev)
                if on_event:
                    on_event(ev)
            since = data["next"]
            if data["done"]:
                return events
        self.fail("Aufgabe wurde nicht fertig")

    def wait_for_pending(self):
        for _ in range(200):
            if self.app.pending:
                return next(iter(self.app.pending))
            time.sleep(0.05)
        self.fail("keine Nachfrage erschienen")


class LocalWebTest(WebTest):
    def test_page_assets_and_auth(self):
        resp = self.request("GET", "/", token=None)
        self.assertEqual(resp.status, 200)
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        self.assertIn(b"<title>", resp.read())
        for path, ctype in (("/manifest.webmanifest", "application/manifest+json"), ("/icon.svg", "image/svg+xml"),
                            ("/icon-180.png", "image/png"), ("/icon-512.png", "image/png")):
            resp = self.request("GET", path, token=None)
            self.assertEqual(resp.status, 200, path)
            self.assertEqual(resp.getheader("Content-Type"), ctype)
            body = resp.read()
            if ctype == "image/png":
                self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertEqual(self.request("GET", "/api/status", token=None).status, 401)
        self.assertEqual(self.request("GET", "/api/status", token="falsch").status, 401)
        status, data = self.json("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["rules"]), 3)
        self.assertIn("Gesetz", data["rules"][0])

    def test_dns_rebinding_blocked(self):
        self.assertEqual(self.request("GET", "/", host="evil.example:8765").status, 403)
        self.assertEqual(self.request("GET", "/api/status", host="evil.example").status, 403)
        self.assertEqual(self.request("GET", "/api/status", host=f"localhost:{self.app.port}").status, 200)

    def test_chat_with_approval_and_reconnect(self):
        self.mock.script += [reply(tool_calls=[("write_file", {"path": "web.txt", "content": "vom Browser"})]),
                             reply("Gespeichert!")]
        status, data = self.json("POST", "/api/chat", {"text": "Speichere was"})
        self.assertEqual(status, 200)
        run_id = data["run"]
        approval_id = self.wait_for_pending()
        # "Handy war kurz weg": eine neue Verbindung holt alles ab Ereignis 0 nach
        status, data = self.json("GET", f"/api/events?run={run_id}&since=0")
        self.assertIn("approval", [e["type"] for e in data["events"]])
        self.assertEqual(data["events"][0], {"type": "user", "text": "Speichere was"})
        self.assertEqual(self.json("POST", "/api/approve", {"approval_id": approval_id, "decision": "yes"})[0], 200)
        events = self.follow(run_id)
        types = [e["type"] for e in events]
        self.assertIn("approval_done", types)
        self.assertEqual(types[-1], "done")
        self.assertEqual((self.work / "web.txt").read_text(encoding="utf-8"), "vom Browser")
        hist = self.json("GET", "/api/history")[1]["items"]
        self.assertEqual([i["role"] for i in hist], ["user", "tool", "assistant"])
        self.assertEqual(hist[1]["status"], "ok")
        self.assertEqual(self.json("GET", "/api/events?run=999&since=0")[0], 404)

    def test_history_hides_running_task(self):
        self.mock.script += [reply("erste Antwort"), reply(tool_calls=[("write_file", {"path": "x.txt", "content": "x"})])]
        run_id = self.json("POST", "/api/chat", {"text": "eins"})[1]["run"]
        self.follow(run_id)
        run_id = self.json("POST", "/api/chat", {"text": "zwei"})[1]["run"]
        self.wait_for_pending()
        status = self.json("GET", "/api/status")[1]
        self.assertEqual(status["run"], {"id": run_id, "done": False})
        texts = [i.get("text") for i in self.json("GET", "/api/history")[1]["items"]]
        self.assertEqual(texts, ["eins", "erste Antwort"])  # "zwei" kommt über die Ereignisse
        self.json("POST", "/api/stop", {})
        self.follow(run_id)

    def test_stop_denies_pending_approval_and_busy_lock(self):
        self.mock.script += [reply(tool_calls=[("write_file", {"path": "nein.txt", "content": "x"})])]
        run_id = self.json("POST", "/api/chat", {"text": "los"})[1]["run"]
        self.wait_for_pending()
        self.assertEqual(self.json("POST", "/api/chat", {"text": "noch was"})[0], 409)
        self.assertEqual(self.json("POST", "/api/reset", {})[0], 409)
        self.assertEqual(self.json("POST", "/api/stop", {})[0], 200)
        events = self.follow(run_id)
        self.assertFalse((self.work / "nein.txt").exists())
        self.assertIn(events[-1]["type"], ("cancelled", "done"))
        self.assertEqual(self.json("POST", "/api/reset", {})[0], 200)
        self.assertEqual(self.agent.history, [])

    def test_task_survives_client_disconnect(self):
        self.mock.script += [reply("a" * 40, delay=0.05)]
        run_id = self.json("POST", "/api/chat", {"text": "hallo"})[1]["run"]
        # Niemand fragt die Ereignisse ab – die Aufgabe läuft trotzdem zu Ende
        for _ in range(200):
            if self.app.run.done:
                break
            time.sleep(0.05)
        self.assertTrue(self.app.run.done)
        events = self.follow(run_id)
        self.assertEqual(events[-1]["type"], "done")

    def test_settings(self):
        status, data = self.json("POST", "/api/settings", {"auto": True})
        self.assertTrue(data["auto"])
        self.assertTrue(self.agent.auto_mode)
        self.assertFalse(self.json("POST", "/api/settings", {"auto": "false"})[1]["auto"])
        self.assertEqual(self.json("POST", "/api/settings", {"model": "gibtsnicht"})[0], 400)
        self.assertEqual(self.json("GET", "/api/models")[1]["models"], ["test:latest"])

    def test_manifest_contains_token_only_for_token_holders(self):
        resp = self.request("GET", "/manifest.webmanifest?token=geheim-schluessel-1234567890", token=None)
        self.assertEqual(json.loads(resp.read())["start_url"], "/#token=geheim-schluessel-1234567890")
        for query in ("", "?token=falsch"):
            resp = self.request("GET", "/manifest.webmanifest" + query, token=None)
            self.assertEqual(json.loads(resp.read())["start_url"], "/")

    def test_history_shows_stopped_and_refused_tools_correctly(self):
        from angel.agent import NOT_RUN_NOTE
        from angel.regeln import PROTECTED_MESSAGE
        self.agent.history = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "name": "run_command", "arguments": {"command": "dir"}},
                {"id": "b", "name": "write_file", "arguments": {"path": "C:\\x", "content": "y"}},
                {"id": "c", "name": "run_command", "arguments": {"command": "false"}}]},
            {"role": "tool", "tool_call_id": "a", "name": "run_command", "content": NOT_RUN_NOTE},
            {"role": "tool", "tool_call_id": "b", "name": "write_file", "content": PROTECTED_MESSAGE},
            {"role": "tool", "tool_call_id": "c", "name": "run_command", "content": "Exit-Code: 1\n(keine Ausgabe)"},
        ]
        items = self.json("GET", "/api/history")[1]["items"]
        self.assertEqual([i.get("status") for i in items if i["role"] == "tool"], ["cancelled", "error", "error"])

    def test_pairing_disabled_locally(self):
        self.assertEqual(self.json("GET", "/api/pairing")[1], {"enabled": False})


class PhoneWebTest(WebTest):
    host = "0.0.0.0"
    phone = True

    def test_pairing_and_foreign_host_allowed_with_token(self):
        status, data = self.json("GET", "/api/pairing")
        self.assertEqual(status, 200)
        if data["enabled"]:  # in Testumgebungen ohne Netzwerk evtl. keine Adresse
            self.assertIn(f":{self.app.port}/#token=geheim-schluessel-1234567890", data["url"])
            self.assertTrue(data["qr"].startswith("data:image/svg+xml;base64,"))
        # Im Handy-Modus kommt der Host-Header z. B. als 192.168.x.x – erlaubt, aber nur mit Schlüssel
        self.assertEqual(self.request("GET", "/api/status", host="192.168.178.20:8765").status, 200)
        self.assertEqual(self.request("GET", "/api/status", host="192.168.178.20:8765", token=None).status, 401)


class PhoneAutoModeTest(PhoneWebTest):
    def test_auto_mode_cannot_be_enabled_from_the_network(self):
        from angel.web import local_ips
        ips = local_ips()
        if not ips:
            self.skipTest("keine Netzwerk-Adresse")
        conn = http.client.HTTPConnection(ips[0], self.app.port, timeout=10)
        conn.request("POST", "/api/settings", body=json.dumps({"auto": True}),
                     headers={"X-Angel-Token": "geheim-schluessel-1234567890", "Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 403)
        self.assertFalse(self.agent.auto_mode)
        self.assertEqual(self.json("POST", "/api/settings", {"auto": True})[0], 200)  # am PC selbst geht es


class TokenTest(TempDirTest):
    def test_token_is_persistent_and_renewable(self):
        a = load_token(self.tmp)
        self.assertGreaterEqual(len(a), 20)
        self.assertEqual(load_token(self.tmp), a)
        b = load_token(self.tmp, renew=True)
        self.assertNotEqual(a, b)
        self.assertEqual(load_token(self.tmp), b)


if __name__ == "__main__":
    unittest.main()
