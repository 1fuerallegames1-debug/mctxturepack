import importlib.util
import unittest
from pathlib import Path

from angel.agent import Agent
from angel.tools import ToolContext, ToolError, ToolRegistry
from tests.helpers import Approver, TempDirTest, make_cfg
from tests.mock_llm import MockLLM, reply

PROJECT = Path(__file__).resolve().parent.parent


def _load_plugin():
    spec = importlib.util.spec_from_file_location("angel_plugin_browser", PROJECT / "plugins" / "browser.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


class FakeBrowser:
    """Tut so, als wäre es das Browserfenster (ohne echten Browser)."""

    def __init__(self):
        self.url = ""
        self.clicked = []
        self.typed = []

    def open(self, url):
        self.url = url
        return f"Seite: {url}\nTitel: Test\n\nInhalt von {url}"

    def read(self, limit=6000):
        return f"Seite: {self.url}\nTitel: Test\n\nInhalt"

    def links(self, limit=40):
        return [("Startseite", "https://example.org/"), ("Hilfe", "https://example.org/hilfe")]

    def click(self, what):
        self.clicked.append(what)
        return f"Geklickt: {what}"

    def type(self, field, text, submit=False):
        self.typed.append((field, text, submit))
        return f"Getippt in {field}: {text}"


class BrowserToolsTest(TempDirTest):
    def setUp(self):
        super().setUp()
        _load_plugin()
        self.reg = ToolRegistry()
        self.cfg = make_cfg(self.tmp, browser={"aktiv": True, "sichtbar": False})
        self.ctx = ToolContext(cfg=self.cfg, workdir=self.work, data_dir=self.tmp / "daten")
        self.ctx._browser = FakeBrowser()

    def run_tool(self, name, **args):
        t = self.reg.get(name)
        self.assertIsNotNone(t, name)
        return t, self.reg.run(t, self.ctx, self.reg.prepare_args(t, args))

    def test_open_read_links(self):
        _, out = self.run_tool("browser_open", url="example.org")
        self.assertIn("example.org", out)
        self.assertEqual(self.ctx._browser.url, "example.org")
        _, links = self.run_tool("browser_links")
        self.assertIn("https://example.org/hilfe", links)

    def test_click_and_type_confirm(self):
        tool, out = self.run_tool("browser_click", what="Anmelden")
        self.assertEqual(tool.confirm, True)
        self.assertIn("Anmelden", self.ctx._browser.clicked)
        tool, _ = self.run_tool("browser_type", field="Suche", text="Katzen", submit=True)
        self.assertEqual(tool.confirm, True)
        self.assertEqual(self.ctx._browser.typed[-1], ("Suche", "Katzen", True))

    def test_inactive_message(self):
        self.cfg["browser"]["aktiv"] = False
        with self.assertRaises(ToolError) as e:
            self.run_tool("browser_open", url="x")
        self.assertIn("nicht eingeschaltet", str(e.exception))


class BrowserAgentTest(TempDirTest):
    def make(self, script, active=True):
        self.llm = MockLLM(list(script))
        self.addCleanup(self.llm.close)
        return Agent(make_cfg(self.tmp, server_url=self.llm.url, browser={"aktiv": active, "sichtbar": False}))

    def test_tools_gated(self):
        self.assertFalse(any(n.startswith("browser_") for n in self.make([reply("hi")], active=False).registry.names()))
        agent = self.make([reply("hi")], active=True)
        self.assertTrue(any(n.startswith("browser_") for n in agent.registry.names()))
        self.assertIn("browser_", agent.system_prompt().replace("browser_* tools", "browser_"))

    def test_click_runs_in_auto_mode_with_injected_browser(self):
        agent = self.make([reply(tool_calls=[("browser_open", {"url": "example.org"})]),
                           reply(tool_calls=[("browser_click", {"what": "Mehr"})]), reply("fertig")])
        agent.auto_mode = True
        agent.ctx._browser = FakeBrowser()
        approver = Approver()
        list(agent.run("geh auf example.org und klick Mehr", approver))
        self.assertEqual(approver.requests, [])              # im Automatik-Modus ohne Nachfrage
        self.assertEqual(agent.ctx._browser.clicked, ["Mehr"])


if __name__ == "__main__":
    unittest.main()
