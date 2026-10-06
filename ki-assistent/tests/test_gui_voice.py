import json
import threading
import time
import unittest

from angel.agent import Agent
from angel.gui import ChatController
from angel import voice
from tests.helpers import TempDirTest, make_cfg
from tests.mock_llm import MockLLM, reply


def drain_until_end(controller, timeout=20):
    """Sammelt Ereignisse bis zum 'end'; beantwortet Nachfragen über den Callback."""
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ev = controller.events.get(timeout=0.2)
        except Exception:
            continue
        events.append(ev)
        if ev["type"] == "end":
            return events
    raise AssertionError("Aufgabe wurde nicht fertig")


class ChatControllerTest(TempDirTest):
    def make(self, script, **cfg):
        self.mock = MockLLM(list(script))
        self.addCleanup(self.mock.close)
        return ChatController(Agent(make_cfg(self.tmp, server_url=self.mock.url, **cfg)))

    def test_simple_answer(self):
        c = self.make([reply("Hallo!")])
        self.assertTrue(c.send("hi"))
        self.assertFalse(c.send("noch eins"))  # schon beschäftigt
        events = drain_until_end(c)
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "user")
        self.assertIn("text", types)
        self.assertEqual(types[-1], "end")
        self.assertFalse(c.running())

    def test_approval_flow(self):
        c = self.make([reply(tool_calls=[("write_file", {"path": "a.txt", "content": "x"})]), reply("fertig")])

        def answer():
            for _ in range(200):
                try:
                    ev = c.events.get(timeout=0.2)
                except Exception:
                    continue
                c.events.put(ev)  # zurücklegen, damit drain sie auch sieht
                if ev["type"] == "approval":
                    c.resolve(ev["approval_id"], "yes")
                    return
                if ev["type"] == "end":
                    return
        # Wir lesen selbst mit und beantworten direkt
        c.send("schreib a.txt")
        got_approval = False
        for _ in range(300):
            ev = c.events.get(timeout=0.2)
            if ev["type"] == "approval":
                got_approval = True
                c.resolve(ev["approval_id"], "yes")
            if ev["type"] == "end":
                break
        self.assertTrue(got_approval)
        self.assertEqual((self.work / "a.txt").read_text(), "x")

    def test_stop_cancels(self):
        c = self.make([reply(tool_calls=[("write_file", {"path": "nein.txt", "content": "x"})])])
        c.send("schreib")
        for _ in range(300):
            ev = c.events.get(timeout=0.2)
            if ev["type"] == "approval":
                c.stop()
            if ev["type"] == "end":
                break
        self.assertFalse((self.work / "nein.txt").exists())
        self.assertFalse(c.running())


class FakeRecognizer:
    """Tut so, als wäre es ein Vosk-Recognizer: jeder Block ist ein 'Wort'."""

    def __init__(self, words):
        self.words = list(words)
        self.spoken = []

    def AcceptWaveform(self, chunk):
        word = self.words.pop(0)
        if word is None:  # None = Satzende
            return True
        self.spoken.append(word)
        return False

    def Result(self):
        said = " ".join(self.spoken)
        self.spoken = []
        return json.dumps({"text": said})

    def PartialResult(self):
        return json.dumps({"partial": " ".join(self.spoken)})

    def FinalResult(self):
        said = " ".join(self.spoken)
        self.spoken = []
        return json.dumps({"text": said})


class VoiceTest(TempDirTest):
    def test_transcribe_stream(self):
        rec = FakeRecognizer(["öffne", "den", "rechner", None, "und", "schließe", "notepad"])
        chunks = [b"x"] * 7
        out = list(voice.transcribe_stream(rec, chunks))
        partials = [t for k, t in out if k == "partial"]
        finals = [t for k, t in out if k == "final"]
        self.assertIn("öffne den rechner", finals)
        self.assertEqual(finals[-1], "und schließe notepad")  # Rest am Ende
        self.assertTrue(any("öffne" in p for p in partials))

    def test_have_model_and_available(self):
        self.assertFalse(voice.have_model(self.tmp))       # noch nichts heruntergeladen
        ok, msg = voice.available()
        if not ok:
            self.assertIn("pip install", msg)              # klare Anleitung, wenn Pakete fehlen

    def test_download_model_from_fake_zip(self):
        # Mini-Zip bauen, das wie ein Vosk-Modell aussieht, und lokal ausliefern
        import io, zipfile
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("vosk-de/am/final.mdl", "x")
            z.writestr("vosk-de/conf/model.conf", "x")
        data = buf.getvalue()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        url = f"http://127.0.0.1:{httpd.server_address[1]}/m.zip"
        seen = []
        root = voice.download_model(self.tmp, url, progress=lambda p: seen.append(p))
        self.assertTrue(voice.have_model(self.tmp))
        self.assertTrue((root / "am" / "final.mdl").exists())


if __name__ == "__main__":
    unittest.main()
