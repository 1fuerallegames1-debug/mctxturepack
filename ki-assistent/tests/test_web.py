import http.client
import json
import threading
import time
import unittest

from kai.agent import Agent
from kai.web import WebApp
from tests.helpers import TempDirTest, make_cfg
from tests.mock_llm import MockLLM, reply


class WebTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.mock = MockLLM([])
        self.addCleanup(self.mock.close)
        self.agent = Agent(make_cfg(self.tmp, server_url=self.mock.url))
        self.app = WebApp(self.agent, host="127.0.0.1", port=0, token="geheim")
        self.httpd = self.app.make_server()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, token="geheim", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        headers = {}
        if token:
            headers["X-Kai-Token"] = token
        if host:
            headers["Host"] = host
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        return conn.getresponse()

    def events(self, resp):
        out = []
        for raw in resp:
            line = raw.decode().strip()
            if line:
                out.append(json.loads(line))
        return out

    def test_page_and_auth(self):
        resp = self.request("GET", "/", token=None)
        self.assertEqual(resp.status, 200)
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        self.assertIn(b"<title>", resp.read())
        self.assertEqual(self.request("GET", "/api/status", token=None).status, 401)
        self.assertEqual(self.request("GET", "/api/status", token="falsch").status, 401)
        resp = self.request("GET", "/api/status")
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.read())["name"], "Kai")

    def test_dns_rebinding_blocked(self):
        self.assertEqual(self.request("GET", "/", host="evil.example:8765").status, 403)
        self.assertEqual(self.request("GET", "/api/status", host="evil.example").status, 403)
        self.assertEqual(self.request("GET", "/api/status", host=f"localhost:{self.app.port}").status, 200)

    def test_chat_with_approval(self):
        self.mock.script += [reply(tool_calls=[("write_file", {"path": "web.txt", "content": "vom Browser"})]),
                             reply("Gespeichert!")]
        approvals = []

        def approver():
            for _ in range(100):
                if self.app.pending:
                    approval_id = next(iter(self.app.pending))
                    approvals.append(approval_id)
                    resp = self.request("POST", "/api/approve", {"approval_id": approval_id, "decision": "yes"})
                    self.assertEqual(resp.status, 200)
                    return
                time.sleep(0.05)

        t = threading.Thread(target=approver)
        t.start()
        events = self.events(self.request("POST", "/api/chat", {"text": "Speichere was"}))
        t.join()
        types = [e["type"] for e in events]
        self.assertIn("approval", types)
        self.assertEqual(types[-1], "done")
        self.assertEqual((self.work / "web.txt").read_text(encoding="utf-8"), "vom Browser")
        hist = json.loads(self.request("GET", "/api/history").read())["items"]
        self.assertEqual([i["role"] for i in hist], ["user", "tool", "assistant"])
        self.assertEqual(hist[1]["status"], "ok")

    def test_stop_denies_pending_approval_and_busy_lock(self):
        self.mock.script += [reply(tool_calls=[("write_file", {"path": "nein.txt", "content": "x"})])]
        results = {}

        def chat():
            results["events"] = self.events(self.request("POST", "/api/chat", {"text": "los"}))

        t = threading.Thread(target=chat)
        t.start()
        for _ in range(100):
            if self.app.pending:
                break
            time.sleep(0.05)
        self.assertTrue(self.app.pending)
        self.assertEqual(self.request("POST", "/api/chat", {"text": "noch was"}).status, 409)
        self.assertEqual(self.request("POST", "/api/reset", {}).status, 409)
        self.assertEqual(self.request("POST", "/api/stop", {}).status, 200)
        t.join(10)
        self.assertFalse((self.work / "nein.txt").exists())
        self.assertIn(results["events"][-1]["type"], ("cancelled", "done"))
        self.assertEqual(self.request("POST", "/api/reset", {}).status, 200)
        self.assertEqual(self.agent.history, [])

    def test_settings(self):
        resp = self.request("POST", "/api/settings", {"auto": True})
        self.assertTrue(json.loads(resp.read())["auto"])
        self.assertTrue(self.agent.auto_mode)
        resp = self.request("POST", "/api/settings", {"model": "gibtsnicht"})
        self.assertEqual(resp.status, 400)
        models = json.loads(self.request("GET", "/api/models").read())
        self.assertEqual(models["models"], ["test:latest"])


if __name__ == "__main__":
    unittest.main()
