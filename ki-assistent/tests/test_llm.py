import json
import socket
import threading
import unittest

from kai.llm import (LLMError, OllamaClient, OpenAIClient, ThinkTagFilter, extract_text_tool_calls,
                     make_client)
from tests.helpers import TempDirTest, make_cfg
from tests.mock_llm import MockLLM, reply

TOOLS = [{"type": "function", "function": {"name": "list_directory", "description": "x",
                                           "parameters": {"type": "object", "properties": {}}}}]


def run_stream(client, messages, tools=TOOLS, cancel=None):
    events, final = [], None
    for kind, payload in client.chat_stream(messages, tools, cancel):
        if kind == "done":
            final = payload
        else:
            events.append((kind, payload))
    return events, final


class OllamaClientTest(TempDirTest):
    def test_text_thinking_and_tool_calls(self):
        mock = MockLLM([reply("Hallo Welt", thinking="Ich überlege", tool_calls=[("list_directory", {"path": "."})])])
        try:
            client = OllamaClient(make_cfg(self.tmp, server_url=mock.url + "/", kontext_laenge=8192))
            events, final = run_stream(client, [{"role": "user", "content": "hi"}])
        finally:
            mock.close()
        self.assertEqual("".join(t for k, t in events if k == "text"), "Hallo Welt")
        self.assertEqual("".join(t for k, t in events if k == "thinking"), "Ich überlege")
        self.assertEqual(final["content"], "Hallo Welt")
        self.assertEqual(final["tool_calls"][0]["name"], "list_directory")
        self.assertEqual(final["tool_calls"][0]["arguments"], {"path": "."})
        self.assertTrue(final["tool_calls"][0]["id"])
        sent = mock.requests[0]
        self.assertEqual(sent["options"]["num_ctx"], 8192)
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["tools"], TOOLS)

    def test_message_conversion(self):
        client = OllamaClient(make_cfg(self.tmp))
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "", "thinking": "alt", "tool_calls": []},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "", "thinking": "neu",
             "tool_calls": [{"id": "c1", "name": "x", "arguments": {"a": 1}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "x", "content": "ergebnis"},
        ]
        out = client._convert(msgs)
        self.assertNotIn("thinking", out[1])  # altes Nachdenken wird weggelassen
        self.assertEqual(out[3]["thinking"], "neu")
        self.assertEqual(out[3]["tool_calls"][0]["function"], {"name": "x", "arguments": {"a": 1}})
        self.assertEqual(out[4], {"role": "tool", "tool_name": "x", "content": "ergebnis"})

    def test_url_normalisation(self):
        for url in ("http://h:1", "http://h:1/", "http://h:1/api", "http://h:1/v1/"):
            self.assertEqual(OllamaClient(make_cfg(self.tmp, server_url=url)).base, "http://h:1")

    def test_http_errors_are_explained(self):
        mock = MockLLM([{"http_error": 404, "error": "model 'test' not found", **reply()},
                        {"http_error": 400, "error": "registry.ollama.ai/library/x does not support tools", **reply()}])
        try:
            client = OllamaClient(make_cfg(self.tmp, server_url=mock.url))
            with self.assertRaises(LLMError) as ctx:
                run_stream(client, [{"role": "user", "content": "hi"}])
            self.assertIn("ollama pull", str(ctx.exception))
            with self.assertRaises(LLMError) as ctx:
                run_stream(client, [{"role": "user", "content": "hi"}])
            self.assertIn("Werkzeug", str(ctx.exception))
        finally:
            mock.close()

    def test_error_inside_stream(self):
        mock = MockLLM([reply("teil", stream_error="CUDA out of memory")])
        try:
            client = OllamaClient(make_cfg(self.tmp, server_url=mock.url))
            with self.assertRaises(LLMError) as ctx:
                run_stream(client, [{"role": "user", "content": "hi"}])
            self.assertIn("Speicher", str(ctx.exception))
        finally:
            mock.close()

    def test_connection_refused(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        client = OllamaClient(make_cfg(self.tmp, server_url=f"http://127.0.0.1:{port}"))
        with self.assertRaises(LLMError) as ctx:
            run_stream(client, [{"role": "user", "content": "hi"}])
        self.assertIn("Ollama", str(ctx.exception))

    def test_check_detects_missing_model_and_tool_support(self):
        mock = MockLLM(models=["qwen3:8b", "llama3.2:latest"], capabilities=["completion"])
        try:
            info = OllamaClient(make_cfg(self.tmp, server_url=mock.url, modell="gemma4")).check()
            self.assertFalse(info["installed"])
            info = OllamaClient(make_cfg(self.tmp, server_url=mock.url, modell="llama3.2")).check()
            self.assertTrue(info["installed"])
            self.assertIn("keine Werkzeuge", info["warning"])
        finally:
            mock.close()

    def test_truncated_stream_is_an_error(self):
        mock = MockLLM([reply("halber Satz", truncate=True)])
        try:
            client = OllamaClient(make_cfg(self.tmp, server_url=mock.url))
            with self.assertRaises(LLMError) as ctx:
                run_stream(client, [{"role": "user", "content": "hi"}])
            self.assertIn("mittendrin", str(ctx.exception))
        finally:
            mock.close()

    def test_server_disconnect_is_an_error(self):
        mock = MockLLM([reply("x", disconnect=True)])
        try:
            client = OllamaClient(make_cfg(self.tmp, server_url=mock.url))
            with self.assertRaises(LLMError):
                run_stream(client, [{"role": "user", "content": "hi"}])
        finally:
            mock.close()

    def test_abort_while_model_is_loading(self):
        import time
        from kai.llm import Cancelled
        mock = MockLLM([reply("spät", header_delay=5)])
        cancel = threading.Event()
        try:
            client = OllamaClient(make_cfg(self.tmp, server_url=mock.url))
            threading.Timer(0.5, lambda: (cancel.set(), client.abort())).start()
            start = time.monotonic()
            with self.assertRaises(Cancelled):
                run_stream(client, [{"role": "user", "content": "hi"}], cancel=cancel)
            self.assertLess(time.monotonic() - start, 3)
        finally:
            mock.close()

    def test_tool_call_ids_are_unique(self):
        mock = MockLLM([reply(tool_calls=[("list_directory", {})]), reply(tool_calls=[("list_directory", {})])])
        try:
            client = OllamaClient(make_cfg(self.tmp, server_url=mock.url))
            ids = [run_stream(client, [{"role": "user", "content": "hi"}])[1]["tool_calls"][0]["id"] for _ in range(2)]
        finally:
            mock.close()
        self.assertNotEqual(ids[0], ids[1])

    def test_cancel(self):
        mock = MockLLM([reply("a" * 30, delay=0.2)])
        cancel = threading.Event()
        try:
            client = OllamaClient(make_cfg(self.tmp, server_url=mock.url))
            gen = client.chat_stream([{"role": "user", "content": "hi"}], TOOLS, cancel)
            next(gen)
            cancel.set()
            from kai.llm import Cancelled
            with self.assertRaises(Cancelled):
                for _ in gen:
                    pass
        finally:
            mock.close()


class OpenAIClientTest(TempDirTest):
    def test_streamed_tool_call_deltas(self):
        mock = MockLLM([reply("Okay", thinking="hmm", tool_calls=[("list_directory", {"path": "C:/x"}),
                                                                   ("list_directory", {"path": "D:/"})])])
        try:
            client = OpenAIClient(make_cfg(self.tmp, anbieter="openai", server_url=mock.url, api_schluessel="k"))
            self.assertTrue(client.base.endswith("/v1"))
            events, final = run_stream(client, [{"role": "user", "content": "hi"}])
        finally:
            mock.close()
        self.assertEqual(final["content"], "Okay")
        self.assertEqual(final["thinking"], "hmm")
        self.assertEqual([c["arguments"] for c in final["tool_calls"]], [{"path": "C:/x"}, {"path": "D:/"}])
        self.assertEqual([c["id"] for c in final["tool_calls"]], ["call_0", "call_1"])

    def test_message_conversion(self):
        client = OpenAIClient(make_cfg(self.tmp, anbieter="openai"))
        out = client._convert([
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "x", "arguments": {"a": "ä"}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "x", "content": "r"},
        ])
        self.assertEqual(json.loads(out[0]["tool_calls"][0]["function"]["arguments"]), {"a": "ä"})
        self.assertEqual(out[1], {"role": "tool", "tool_call_id": "c1", "content": "r"})
        bad = client._convert([{"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "name": "x", "arguments": None, "raw_arguments": '{"path": "C:\\x'}]}])
        self.assertEqual(json.loads(bad[0]["tool_calls"][0]["function"]["arguments"]), {})

    def test_make_client(self):
        self.assertIsInstance(make_client(make_cfg(self.tmp, anbieter="lmstudio")), OpenAIClient)
        with self.assertRaises(LLMError):
            make_client(make_cfg(self.tmp, anbieter="quatsch"))


class ThinkFilterTest(unittest.TestCase):
    def test_tags_split_over_chunks(self):
        f = ThinkTagFilter()
        out = []
        for chunk in ["Hallo <thi", "nk>geheim</th", "ink> Welt", "<"]:
            out += f.feed(chunk)
        out += f.flush()
        text = "".join(t for k, t in out if k == "text")
        thinking = "".join(t for k, t in out if k == "thinking")
        self.assertEqual(text, "Hallo  Welt<")
        self.assertEqual(thinking, "geheim")


class TextToolCallTest(unittest.TestCase):
    names = ["read_file", "run_command"]

    def test_plain_json(self):
        calls, rest = extract_text_tool_calls('{"name": "read_file", "arguments": {"path": "a.txt"}}', self.names)
        self.assertEqual(calls[0]["arguments"], {"path": "a.txt"})
        self.assertEqual(rest, "")

    def test_fenced_and_parameters_key(self):
        calls, _ = extract_text_tool_calls('```json\n{"name": "run_command", "parameters": {"command": "dir"}}\n```',
                                           self.names)
        self.assertEqual(calls[0]["name"], "run_command")

    def test_tool_call_tags_keep_text(self):
        calls, rest = extract_text_tool_calls(
            'Ich schaue nach.\n<tool_call>{"name": "read_file", "arguments": "{\\"path\\": \\"x\\"}"}</tool_call>',
            self.names)
        self.assertEqual(calls[0]["arguments"], {"path": "x"})
        self.assertEqual(rest, "Ich schaue nach.")

    def test_normal_text_untouched(self):
        for text in ["Hallo!", '{"name": "unbekannt", "arguments": {}}', "{kein json", ""]:
            calls, rest = extract_text_tool_calls(text, self.names)
            self.assertEqual(calls, [])
            self.assertEqual(rest, text)


if __name__ == "__main__":
    unittest.main()
