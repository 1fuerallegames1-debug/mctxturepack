import threading
import unittest

from kai.agent import DENIED_MESSAGE, Agent
from tests.helpers import Approver, TempDirTest, make_cfg
from tests.mock_llm import MockLLM, reply


class AgentTest(TempDirTest):
    def make(self, script, **cfg):
        self.mock = MockLLM(script)
        self.addCleanup(self.mock.close)
        return Agent(make_cfg(self.tmp, server_url=self.mock.url, **cfg))

    def types(self, events):
        return [e["type"] for e in events]

    def test_safe_tool_then_answer(self):
        (self.work / "notiz.txt").write_text("x")
        agent = self.make([reply("Ich schaue nach.", tool_calls=[("list_directory", {"path": "."})]),
                           reply("Da liegt notiz.txt.")])
        approver = Approver()
        events = list(agent.run("Was liegt im Ordner?", approver))
        self.assertEqual(approver.requests, [])  # Lesen braucht keine Erlaubnis
        self.assertIn("tool_result", self.types(events))
        result = next(e for e in events if e["type"] == "tool_result")
        self.assertEqual(result["status"], "ok")
        self.assertIn("notiz.txt", result["result"])
        self.assertEqual(events[-1]["type"], "done")
        roles = [m["role"] for m in agent.history]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        # Zweite Anfrage an das Modell enthält das Werkzeug-Ergebnis
        second = self.mock.requests[1]["messages"]
        self.assertEqual(second[0]["role"], "system")
        self.assertEqual(second[-1]["role"], "tool")
        self.assertIn("notiz.txt", second[-1]["content"])

    def test_write_needs_approval_and_can_be_denied(self):
        target = self.work / "neu.txt"
        agent = self.make([reply(tool_calls=[("write_file", {"path": "neu.txt", "content": "hallo"})]),
                           reply("Okay, nicht gespeichert.")])
        approver = Approver("no")
        events = list(agent.run("Speichere hallo", approver))
        self.assertEqual(len(approver.requests), 1)
        self.assertIn("neu.txt", approver.requests[0]["summary"])
        self.assertFalse(target.exists())
        self.assertEqual(next(e for e in events if e["type"] == "tool_result")["status"], "denied")
        self.assertEqual(agent.history[2]["content"], DENIED_MESSAGE)

    def test_always_allows_rest_of_session(self):
        agent = self.make([reply(tool_calls=[("write_file", {"path": "a.txt", "content": "1"})]),
                           reply(tool_calls=[("write_file", {"path": "b.txt", "content": "2"})]),
                           reply("fertig")])
        approver = Approver("always")
        list(agent.run("zwei Dateien", approver))
        self.assertEqual(len(approver.requests), 1)
        self.assertEqual((self.work / "a.txt").read_text(), "1")
        self.assertEqual((self.work / "b.txt").read_text(), "2")

    def test_auto_mode_still_asks_for_dangerous_commands(self):
        agent = self.make([reply(tool_calls=[("write_file", {"path": "a.txt", "content": "1"})]),
                           reply(tool_calls=[("run_command", {"command": "rm -rf ./ordner"})]),
                           reply("ok")], bestaetigung="automatisch")
        approver = Approver("always")
        list(agent.run("los", approver))
        self.assertEqual(len(approver.requests), 1)
        self.assertTrue(approver.requests[0]["dangerous"])
        self.assertNotIn("run_command", agent.session_allowed)  # "immer" gilt nicht für Gefährliches

    def test_configured_always_allow(self):
        agent = self.make([reply(tool_calls=[("write_file", {"path": "a.txt", "content": "1"})]), reply("ok")],
                          immer_erlauben=["write_file"])
        approver = Approver()
        list(agent.run("los", approver))
        self.assertEqual(approver.requests, [])
        self.assertTrue((self.work / "a.txt").exists())

    def test_unknown_tool_and_bad_arguments(self):
        agent = self.make([reply(tool_calls=[("fliegen", {}), ("read_file", {})]), reply("sorry")])
        events = list(agent.run("x", Approver()))
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual([r["status"] for r in results], ["error", "error"])
        self.assertIn("unknown tool", results[0]["result"])
        self.assertIn("path", results[1]["result"])
        self.assertEqual(len([m for m in agent.history if m["role"] == "tool"]), 2)

    def test_disabled_tools_are_not_offered(self):
        agent = self.make([reply("hi")], deaktivierte_werkzeuge=["run_python"])
        list(agent.run("x", Approver()))
        names = [t["function"]["name"] for t in self.mock.requests[0]["tools"]]
        self.assertNotIn("run_python", names)
        self.assertIn("run_command", names)

    def test_llm_error_removes_unsent_message(self):
        agent = self.make([{"http_error": 500, "error": "kaputt", **reply()}])
        events = list(agent.run("hallo", Approver()))
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(agent.history, [])

    def test_cancel_during_approval_repairs_history(self):
        agent = self.make([reply(tool_calls=[("write_file", {"path": "a.txt", "content": "1"}),
                                             ("write_file", {"path": "b.txt", "content": "2"})])])

        def approve(req):
            agent.cancel()
            return "yes"

        events = list(agent.run("x", approve))
        self.assertEqual(events[-1]["type"], "cancelled")
        self.assertFalse((self.work / "a.txt").exists())
        tool_msgs = [m for m in agent.history if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 2)  # jeder Aufruf hat ein Ergebnis
        # Danach geht es normal weiter
        self.mock.script.append(reply("wieder da"))
        events = list(agent.run("weiter", Approver()))
        self.assertEqual(events[-1]["type"], "done")

    def test_closing_generator_repairs_history(self):
        agent = self.make([reply(tool_calls=[("list_directory", {}), ("list_directory", {})]), reply("x")])
        gen = agent.run("x", Approver())
        for ev in gen:
            if ev["type"] == "tool_start":
                break
        gen.close()  # z. B. Browser-Tab geschlossen
        self.assertEqual(len([m for m in agent.history if m["role"] == "tool"]), 2)

    def test_max_steps(self):
        agent = self.make([reply(tool_calls=[("system_info", {})])] * 3, max_schritte=3)
        events = list(agent.run("x", Approver()))
        self.assertIn("3 Schritten", next(e for e in events if e["type"] == "error")["message"])

    def test_context_trimming_keeps_recent_turns(self):
        agent = self.make([reply("ok")], kontext_laenge=6000)
        for i in range(30):
            agent.history.append({"role": "user", "content": f"Frage {i} " + "x" * 400})
            agent.history.append({"role": "assistant", "content": f"Antwort {i} " + "y" * 400, "tool_calls": []})
        list(agent.run("neueste Frage", Approver()))
        sent = self.mock.requests[0]["messages"]
        self.assertEqual(sent[1]["role"], "user")  # Schnitt immer an einer Benutzer-Nachricht
        self.assertEqual(sent[-1]["content"], "neueste Frage")
        self.assertLess(len(sent), 61)
        self.assertIn("removed to save space", sent[0]["content"])

    def test_huge_tool_result_in_current_turn_is_shortened(self):
        big = self.work / "gross.txt"
        big.write_text("\n".join("Zeile %d %s" % (i, "z" * 80) for i in range(1500)))
        agent = self.make([reply(tool_calls=[("read_file", {"path": "gross.txt", "max_lines": 2000})]),
                           reply(tool_calls=[("read_file", {"path": "gross.txt", "max_lines": 2000})]),
                           reply("fertig")], kontext_laenge=8192, max_ausgabe_zeichen=8000)
        list(agent.run("lies", Approver()))
        last = self.mock.requests[-1]["messages"]
        total = sum(len(str(m.get("content", ""))) for m in last)
        self.assertLess(total, 8192 * 3)

    def test_memory_is_in_system_prompt(self):
        agent = self.make([reply(tool_calls=[("remember", {"fact": "Der Benutzer heißt Alex."})]), reply("Gemerkt!"),
                           reply("Hallo Alex")])
        list(agent.run("Ich heiße Alex, merk dir das", Approver()))
        agent.reset()
        list(agent.run("Wie heiße ich?", Approver()))
        self.assertIn("Der Benutzer heißt Alex.", self.mock.requests[-1]["messages"][0]["content"])

    def test_fetch_requires_approval_only_for_unknown_urls(self):
        agent = self.make([reply("ok")])
        tool = agent.registry.get("fetch_webpage")
        agent.ctx.remember_urls("Schau hier: https://example.org/seite.")
        self.assertFalse(tool.needs_confirmation(agent.ctx, {"url": "https://example.org/seite"}))
        self.assertTrue(tool.needs_confirmation(agent.ctx, {"url": "https://evil.example/?d=geheim"}))


if __name__ == "__main__":
    unittest.main()
