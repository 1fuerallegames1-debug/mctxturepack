import base64
import importlib.util
import json
import unittest
import email.policy
from email import message_from_bytes
from pathlib import Path

from angel.agent import Agent
from angel.google_api import GoogleClient, NeedsLogin
from angel.tools import ToolContext, ToolError, ToolRegistry
from tests.helpers import Approver, TempDirTest, make_cfg
from tests.mock_google import MockGoogle
from tests.mock_llm import MockLLM, reply

PROJECT = Path(__file__).resolve().parent.parent


def _load_plugin():
    spec = importlib.util.spec_from_file_location("angel_plugin_google", PROJECT / "plugins" / "google.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


class GoogleClientTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.mock = MockGoogle()
        self.addCleanup(self.mock.close)
        self.token = self.tmp / "google_token.json"

    def client(self, token=None):
        if token is None:
            token = {"refresh_token": "r1", "access_token": "alt", "expires_at": 0}
        self.token.write_text(json.dumps(token), encoding="utf-8")
        return GoogleClient("cid", "secret", self.token, token_url=self.mock.base + "/token", api_base=self.mock.base)

    def test_refresh_and_request(self):
        c = self.client()
        data = c.request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages")
        self.assertEqual(self.mock.token_calls, 1)          # abgelaufenes Token -> einmal erneuert
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(json.loads(self.token.read_text())["access_token"], "frisch")  # gespeichert

    def test_needs_login_without_refresh_token(self):
        c = self.client(token={})
        self.assertFalse(c.logged_in())
        with self.assertRaises(NeedsLogin):
            c.request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages")


class GoogleToolsTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.mock = MockGoogle()
        self.addCleanup(self.mock.close)
        _load_plugin()
        self.reg = ToolRegistry()
        (self.tmp / "daten").mkdir(parents=True, exist_ok=True)
        (self.tmp / "daten" / "google_token.json").write_text(
            json.dumps({"refresh_token": "r1", "expires_at": 0}), encoding="utf-8")
        self.cfg = make_cfg(self.tmp, google={"aktiv": True, "client_id": "cid", "client_secret": "s",
                                              "api_base": self.mock.base, "token_url": self.mock.base + "/token"})
        self.ctx = ToolContext(cfg=self.cfg, workdir=self.work, data_dir=self.tmp / "daten")

    def run_tool(self, name, **args):
        t = self.reg.get(name)
        self.assertIsNotNone(t, name)
        return t, self.reg.run(t, self.ctx, self.reg.prepare_args(t, args))

    def test_gmail_search_read_send(self):
        _, out = self.run_tool("gmail_search", query="is:unread")
        self.assertIn("[m1]", out)
        _, msg = self.run_tool("gmail_read", message_id="m1")
        self.assertIn("Meeting", msg)
        self.assertIn("Bitte um 15 Uhr.", msg)
        tool, _ = self.run_tool("gmail_send", to="freund@x.de", subject="Hi ä", body="Täst")
        self.assertEqual(tool.confirm, "always")            # Senden fragt immer
        raw = base64.urlsafe_b64decode(self.mock.sent[0]["raw"] + "===")
        em = message_from_bytes(raw, policy=email.policy.default)
        self.assertEqual(em["To"], "freund@x.de")
        self.assertIn("Hi", em["Subject"])                  # Betreff korrekt kodiert
        self.assertIn("Täst", em.get_content())

    def test_gmail_trash_always(self):
        tool, _ = self.run_tool("gmail_trash", message_id="m2")
        self.assertEqual(tool.confirm, "always")
        self.assertIn(("trash", "m2"), self.mock.actions)

    def test_calendar(self):
        _, out = self.run_tool("calendar_list", days=5)
        self.assertIn("Zahnarzt", out)
        tool, _ = self.run_tool("calendar_create", title="Kino", start="2026-10-07T20:00:00", end="2026-10-07T22:00:00")
        self.assertEqual(tool.confirm, True)
        self.assertIn(("event_create", "Kino"), self.mock.actions)
        tool, _ = self.run_tool("calendar_delete", event_id="ev9")
        self.assertEqual(tool.confirm, "always")

    def test_drive(self):
        _, out = self.run_tool("drive_search", query="Urlaub")
        self.assertIn("Urlaub.txt", out)
        _, content = self.run_tool("drive_read", file_id="f1")
        self.assertIn("Dateiinhalt", content)
        (self.work / "hoch.txt").write_text("x")
        tool, _ = self.run_tool("drive_upload", path="hoch.txt")
        self.assertEqual(tool.confirm, True)
        self.assertTrue(any(a[0] == "upload" for a in self.mock.actions))
        tool, _ = self.run_tool("drive_delete", file_id="f1")
        self.assertEqual(tool.confirm, "always")
        self.assertTrue(any(a[0] == "drive_patch" and a[2].get("trashed") for a in self.mock.actions))
        tool, _ = self.run_tool("drive_share", file_id="f1", email="a@b.de", role="writer")
        self.assertEqual(tool.confirm, "always")
        self.assertIn(("share", "f1", "a@b.de", "writer"), self.mock.actions)

    def test_contacts_and_tasks(self):
        _, out = self.run_tool("contacts_search", query="Max")
        self.assertIn("max@example.com", out)
        _, tl = self.run_tool("tasks_list")
        self.assertIn("Mülltonne", tl)
        self.run_tool("tasks_add", title="Einkaufen", due="2026-10-09")
        self.assertTrue(any(a[0] == "task_add" and a[1] == "Einkaufen" for a in self.mock.actions))
        self.run_tool("tasks_complete", task_id="t1")
        self.assertTrue(any(a[0] == "task_patch" for a in self.mock.actions))

    def test_not_logged_in(self):
        (self.tmp / "daten" / "google_token.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ToolError) as e:
            self.run_tool("gmail_search", query="x")
        self.assertIn("angemeldet", str(e.exception))


class GoogleAgentTest(TempDirTest):
    def make(self, script, active=True):
        self.llm = MockLLM(list(script))
        self.addCleanup(self.llm.close)
        self.mg = MockGoogle()
        self.addCleanup(self.mg.close)
        (self.tmp / "daten").mkdir(parents=True, exist_ok=True)
        (self.tmp / "daten" / "google_token.json").write_text(
            json.dumps({"refresh_token": "r1", "expires_at": 0}), encoding="utf-8")
        google = {"aktiv": active, "client_id": "c", "client_secret": "s",
                  "api_base": self.mg.base, "token_url": self.mg.base + "/token"}
        return Agent(make_cfg(self.tmp, server_url=self.llm.url, google=google))

    def test_tools_gated(self):
        self.assertFalse(any(n.startswith("gmail_") for n in self.make([reply("hi")], active=False).registry.names()))
        self.assertTrue(any(n.startswith("gmail_") for n in self.make([reply("hi")], active=True).registry.names()))

    def test_send_asks_even_in_auto_mode(self):
        agent = self.make([reply(tool_calls=[("gmail_send", {"to": "x@y.de", "subject": "Hi", "body": "Test"})]),
                           reply("ok")])
        agent.auto_mode = True
        approver = Approver("no")
        list(agent.run("schreib x@y.de", approver))
        self.assertEqual(len(approver.requests), 1)
        self.assertTrue(approver.requests[0]["dangerous"])
        self.assertEqual(self.mg.sent, [])

    def test_search_runs_without_asking(self):
        agent = self.make([reply(tool_calls=[("gmail_search", {"query": "is:unread"})]), reply("fertig")])
        agent.auto_mode = True
        approver = Approver()
        list(agent.run("neue mails?", approver))
        self.assertEqual(approver.requests, [])


if __name__ == "__main__":
    unittest.main()
