import base64
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from angel.tools import ToolContext, ToolError, ToolRegistry
from tests.helpers import TempDirTest, make_cfg

PROJECT = Path(__file__).resolve().parent.parent
BILD_BYTES = b"FAKE_BILD_DATEN_1234"  # Inhalt egal – das Plugin prueft nur die Endung


def _load_plugin():
    spec = importlib.util.spec_from_file_location("angel_plugin_sehen", PROJECT / "plugins" / "sehen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        self.server.letzte = json.loads(self.rfile.read(n) or b"{}")
        out = json.dumps({"message": {"content": "Ein kleines Testbild mit Text."}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class MockVision:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.letzte = None
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class SehenTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.mod = _load_plugin()
        self.mock = MockVision()
        self.addCleanup(self.mock.close)
        self.reg = ToolRegistry()
        self.cfg = make_cfg(self.tmp, server_url=self.mock.base, sehen={"modell": "llava"})
        self.ctx = ToolContext(cfg=self.cfg, workdir=self.work, data_dir=self.tmp / "daten")

    def run_tool(self, name, **args):
        t = self.reg.get(name)
        self.assertIsNotNone(t, name)
        return t, self.reg.run(t, self.ctx, self.reg.prepare_args(t, args))

    def test_bild_ansehen_sendet_bild_und_gibt_beschreibung(self):
        (self.work / "katze.png").write_bytes(BILD_BYTES)
        _, out = self.run_tool("bild_ansehen", pfad="katze.png")
        self.assertIn("Testbild", out)
        body = self.mock.server.letzte
        self.assertEqual(body["model"], "llava")
        self.assertFalse(body["stream"])
        gesendet = base64.b64decode(body["messages"][0]["images"][0])
        self.assertEqual(gesendet, BILD_BYTES)

    def test_eigene_frage_wird_weitergereicht(self):
        (self.work / "foto.jpg").write_bytes(BILD_BYTES)
        self.run_tool("bild_ansehen", pfad="foto.jpg", frage="Wie viele Personen?")
        self.assertIn("Wie viele Personen?", self.mock.server.letzte["messages"][0]["content"])

    def test_fehlende_datei(self):
        with self.assertRaises(ToolError):
            self.run_tool("bild_ansehen", pfad="gibtsnicht.png")

    def test_keine_bilddatei(self):
        (self.work / "notiz.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ToolError):
            self.run_tool("bild_ansehen", pfad="notiz.txt")

    def test_server_nicht_erreichbar(self):
        (self.work / "a.png").write_bytes(BILD_BYTES)
        self.cfg["server_url"] = "http://127.0.0.1:1"  # dort lauscht nichts
        with self.assertRaises(ToolError) as e:
            self.run_tool("bild_ansehen", pfad="a.png")
        self.assertIn("erreichbar", str(e.exception).lower())

    def test_video_ohne_ffmpeg(self):
        (self.work / "clip.mp4").write_bytes(b"\x00\x00")
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ToolError) as e:
                self.run_tool("video_ansehen", pfad="clip.mp4")
        self.assertIn("ffmpeg", str(e.exception).lower())

    def test_zu_grosses_bild_wird_vor_dem_einlesen_abgelehnt(self):
        (self.work / "gross.png").write_bytes(b"x" * 500)
        self.mod._MAX_BYTES = 100  # Grenze künstlich klein setzen
        try:
            with self.assertRaises(ToolError) as e:
                self.run_tool("bild_ansehen", pfad="gross.png")
        finally:
            self.mod._MAX_BYTES = 20 * 1024 * 1024
        self.assertIn("MB", str(e.exception))
        self.assertIsNone(self.mock.server.letzte)  # nichts gesendet -> vor dem Lesen abgelehnt

    def test_pfad_mit_anfuehrungszeichen_und_leerzeichen(self):
        (self.work / "mein bild.png").write_bytes(BILD_BYTES)
        # So übergibt der 📎-Knopf den Pfad (in Anführungszeichen) – ctx.resolve entfernt sie wieder
        _, out = self.run_tool("bild_ansehen", pfad='"mein bild.png"')
        self.assertIn("Testbild", out)


class SehenAktivTest(unittest.TestCase):
    def test_gatterung(self):
        from angel.config import sehen_aktiv
        self.assertTrue(sehen_aktiv({"sehen": {"aktiv": True}, "anbieter": "ollama"}))
        self.assertTrue(sehen_aktiv({}))  # Standard: aktiv + ollama
        self.assertFalse(sehen_aktiv({"sehen": {"aktiv": False}, "anbieter": "ollama"}))
        self.assertFalse(sehen_aktiv({"sehen": {"aktiv": True}, "anbieter": "openai"}))


if __name__ == "__main__":
    unittest.main()
