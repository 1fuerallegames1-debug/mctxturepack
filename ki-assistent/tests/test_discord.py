import importlib.util
import unittest
from pathlib import Path

from angel.agent import Agent
from angel.discord_api import DiscordClient, DiscordError
from angel.tools import ToolContext, ToolError, ToolRegistry
from tests.helpers import Approver, TempDirTest, make_cfg
from tests.mock_discord import MockDiscord
from tests.mock_llm import MockLLM, reply

PROJECT = Path(__file__).resolve().parent.parent


def _load_plugin():
    spec = importlib.util.spec_from_file_location("angel_plugin_discord", PROJECT / "plugins" / "discord.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DiscordClientTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.mock = MockDiscord()
        self.addCleanup(self.mock.close)

    def test_calls_and_errors(self):
        c = DiscordClient("GUELTIG", base=self.mock.base)
        self.assertEqual(c.me()["username"], "Angel")
        self.assertEqual(len(c.channels("100")), 4)
        c.send_message("11", "Hallo")
        self.assertEqual(self.mock.messages["11"][0]["content"], "Hallo")
        bad = DiscordClient("FALSCH", base=self.mock.base)
        with self.assertRaises(DiscordError) as ctx:
            bad.me()
        self.assertIn("Token", str(ctx.exception))


class DiscordToolsTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.mock = MockDiscord()
        self.addCleanup(self.mock.close)
        _load_plugin()
        self.reg = ToolRegistry()
        self.cfg = make_cfg(self.tmp, discord={
            "aktiv": True, "bot_token": "GUELTIG", "server_id": "100",
            "api_base": self.mock.base, "erlaubte_kanaele": [], "nur_besitzer": True, "besitzer_discord_id": ""})
        self.ctx = ToolContext(cfg=self.cfg, workdir=self.work, data_dir=self.tmp / "daten")

    def run_tool(self, tool_name, **args):
        tool = self.reg.get(tool_name)
        self.assertIsNotNone(tool, tool_name)
        return tool, self.reg.run(tool, self.ctx, self.reg.prepare_args(tool, args))

    def test_overview_and_read(self):
        _, out = self.run_tool("discord_overview")
        self.assertIn("Mein Server", out)
        self.assertIn("#allgemein", out)
        self.assertIn("Admin", out)
        _, msgs = self.run_tool("discord_read_messages", channel="allgemein")
        self.assertIn("Hallo zusammen", msgs)

    def test_send_by_name_and_id(self):
        self.run_tool("discord_send_message", channel="allgemein", text="Hi")
        self.run_tool("discord_send_message", channel="12", text="Moin")
        self.assertEqual(self.mock.messages["11"][0]["content"], "Hi")
        self.assertEqual(self.mock.messages["12"][0]["content"], "Moin")

    def test_unknown_channel(self):
        with self.assertRaises(ToolError) as ctx:
            self.run_tool("discord_send_message", channel="gibtsnicht", text="x")
        self.assertIn("nicht gefunden", str(ctx.exception))

    def test_allowlist(self):
        self.cfg["discord"]["erlaubte_kanaele"] = ["allgemein"]
        self.run_tool("discord_send_message", channel="allgemein", text="ok")
        with self.assertRaises(ToolError) as ctx:
            self.run_tool("discord_send_message", channel="regeln", text="nein")
        self.assertIn("nicht freigegeben", str(ctx.exception))

    def test_create_and_delete_channel(self):
        _, out = self.run_tool("discord_create_channel", name="neuer-kanal", type="text")
        self.assertIn("neuer-kanal", out)
        self.assertEqual(self.mock.calls[-1][0], "POST")
        tool, out = self.run_tool("discord_delete_channel", channel="willkommen")
        self.assertIn("12", self.mock.deleted)
        self.assertEqual(tool.confirm, "always")  # Löschen fragt immer

    def test_roles_and_moderation_always_confirm(self):
        tool, _ = self.run_tool("discord_set_role", member="max", role="Admin", action="add")
        self.assertEqual(self.mock.actions[-1], ("role", "PUT", "900", "3"))
        self.assertEqual(tool.confirm, "always")
        tool, _ = self.run_tool("discord_moderate_member", member="nervig", action="kick")
        self.assertEqual(self.mock.actions[-1][:2], ("kick", "901"))
        self.assertEqual(tool.confirm, "always")
        self.run_tool("discord_moderate_member", member="901", action="ban")
        self.assertEqual(self.mock.actions[-1][0], "ban")
        self.run_tool("discord_moderate_member", member="max", action="timeout", minutes=5)
        self.assertEqual(self.mock.actions[-1][0], "timeout")
        self.assertIn("communication_disabled_until", self.mock.actions[-1][2])


class DiscordAgentTest(TempDirTest):
    def make(self, script, discord_active=True, **cfg):
        self.mock_llm = MockLLM(list(script))
        self.addCleanup(self.mock_llm.close)
        self.mock_dc = MockDiscord()
        self.addCleanup(self.mock_dc.close)
        discord = {"aktiv": discord_active, "bot_token": "GUELTIG", "server_id": "100",
                   "api_base": self.mock_dc.base, "erlaubte_kanaele": []}
        return Agent(make_cfg(self.tmp, server_url=self.mock_llm.url, discord=discord, **cfg))

    def test_tools_hidden_when_inactive(self):
        agent = self.make([reply("hi")], discord_active=False)
        self.assertFalse(any(n.startswith("discord_") for n in agent.registry.names()))
        agent2 = self.make([reply("hi")], discord_active=True)
        self.assertTrue(any(n.startswith("discord_") for n in agent2.registry.names()))

    def test_kick_asks_even_in_auto_mode(self):
        agent = self.make([reply(tool_calls=[("discord_moderate_member", {"member": "nervig", "action": "kick"})]),
                           reply("ok")], bestaetigung="automatisch")
        approver = Approver("no")
        list(agent.run("wirf nervig raus", approver))
        self.assertEqual(len(approver.requests), 1)           # trotz Automatik gefragt
        self.assertTrue(approver.requests[0]["dangerous"])
        self.assertEqual(self.mock_dc.actions, [])            # abgelehnt -> nichts passiert

    def test_send_runs_without_asking_in_auto_mode(self):
        agent = self.make([reply(tool_calls=[("discord_send_message", {"channel": "allgemein", "text": "Hallo"})]),
                           reply("gesendet")], bestaetigung="automatisch")
        approver = Approver()
        list(agent.run("schreib Hallo in allgemein", approver))
        self.assertEqual(approver.requests, [])               # Posten ist nicht kritisch
        self.assertEqual(self.mock_dc.messages["11"][0]["content"], "Hallo")

    def test_discord_prompt_mentions_server(self):
        agent = self.make([reply("hi")], discord_active=True)
        self.assertIn("Discord", agent.system_prompt())


if __name__ == "__main__":
    unittest.main()
