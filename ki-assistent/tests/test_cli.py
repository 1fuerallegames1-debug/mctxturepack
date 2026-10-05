import unittest
from unittest import mock

from kai.agent import Agent
from kai.cli import TerminalChat
from tests.helpers import TempDirTest, make_cfg
from tests.mock_llm import MockLLM, reply


class ScriptedChat(TerminalChat):
    """Terminal-Chat mit vorgegebenen Eingaben; die Ausgabe wird mitgeschrieben."""

    def __init__(self, agent, answers):
        super().__init__(agent)
        self.answers = list(answers)
        self.output = []

    def out(self, text: str = "", end: str = "\n"):
        self.output.append(text + end)

    def ask(self, prompt: str) -> str:
        self.output.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    @property
    def text(self) -> str:
        return "".join(self.output)


class CliTest(TempDirTest):
    def make(self, script=(), models=("test:latest",), **cfg):
        self.mock = MockLLM(list(script), models=list(models))
        self.addCleanup(self.mock.close)
        return Agent(make_cfg(self.tmp, server_url=self.mock.url, **cfg))

    def test_chat_approval_and_commands(self):
        agent = self.make([reply(tool_calls=[("write_file", {"path": "a.txt", "content": "hi"})]), reply("Erledigt.")])
        chat = ScriptedChat(agent, ["Schreib a.txt", "j", "/regeln", "/gedaechtnis", "/auto an", "/auto aus", "/beenden"])
        self.assertEqual(chat.loop(), 0)
        self.assertTrue((self.work / "a.txt").exists())
        out = chat.text
        self.assertIn("möchte Folgendes tun", out)
        self.assertIn("Erledigt.", out)
        self.assertIn("Dein Wort ist Gesetz", out)
        self.assertIn("körperlichen Schaden", out)
        self.assertIn("OHNE Nachfrage", out)
        self.assertIn("fragt wieder", out)
        self.assertFalse(agent.auto_mode)

    def test_denied_and_dangerous_has_no_always(self):
        agent = self.make([reply(tool_calls=[("run_command", {"command": "rm -rf ./x"})]), reply("Okay.")])
        chat = ScriptedChat(agent, ["lösch alles", "i", "n", "/beenden"])
        chat.loop()
        self.assertIn("ACHTUNG", chat.text)
        self.assertNotIn("[i] immer", chat.text.split("ACHTUNG")[1].split("\n")[1])
        self.assertIn("abgelehnt", chat.text)

    def test_missing_model_can_be_downloaded(self):
        agent = self.make(models=["anderes:latest"], modell="qwen3:8b")
        chat = ScriptedChat(agent, ["j"])
        with mock.patch("kai.cli.save_setting"):
            self.assertTrue(chat.startup_check())
        self.assertIn("Fertig", chat.text)

    def test_missing_model_pick_installed_one(self):
        agent = self.make(models=["anderes:latest"], modell="qwen3:8b")
        chat = ScriptedChat(agent, ["n", "1"])
        with mock.patch("kai.cli.save_setting") as saved:
            self.assertTrue(chat.startup_check())
        self.assertEqual(agent.client.model, "anderes:latest")
        saved.assert_called_once_with("modell", "anderes:latest")

    def test_no_server(self):
        agent = Agent(make_cfg(self.tmp, server_url="http://127.0.0.1:9"))
        chat = ScriptedChat(agent, [])
        self.assertFalse(chat.startup_check())
        self.assertIn("ollama.com/download", chat.text)


if __name__ == "__main__":
    unittest.main()
