import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from angel.tools import ToolContext, ToolError, ToolRegistry
from tests.helpers import TempDirTest, make_cfg

PROJECT = Path(__file__).resolve().parent.parent
BILD = b"\x89PNG\r\n\x1a\n" + b"BILDDATEN" * 20


def _load_plugin():
    spec = importlib.util.spec_from_file_location("angel_plugin_medien", PROJECT / "plugins" / "medien.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _DL(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(BILD)))
        self.end_headers()
        self.wfile.write(BILD)


class MockDownload:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _DL)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class MedienTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.mod = _load_plugin()
        self.reg = ToolRegistry()
        self.medien = self.tmp / "medien"
        self.cfg = make_cfg(self.tmp, web_browser="opera gx", medien_ordner=str(self.medien))
        self.ctx = ToolContext(cfg=self.cfg, workdir=self.work, data_dir=self.tmp / "daten")
        self.geoeffnet = []
        self.mod._browser_oeffnen = lambda cfg, url: self.geoeffnet.append(url)

    def run_tool(self, name, **args):
        t = self.reg.get(name)
        self.assertIsNotNone(t, name)
        return t, self.reg.run(t, self.ctx, self.reg.prepare_args(t, args))

    # ---- Suche
    def test_bild_suchen_liefert_echte_urls(self):
        def fake_get(url, headers=None):
            if "i.js" in url:
                return json.dumps({"results": [
                    {"title": "Foto 1", "image": "https://x/1.jpg", "url": "https://seite/1"},
                    {"title": "Foto 2", "image": "https://x/2.jpg", "url": "https://seite/2"}]})
            return 'var x = {"vqd":"4-12345"};'
        self.mod._http_get = fake_get
        _, out = self.run_tool("bild_suchen", begriff="Madison Beer", anzahl=2)
        self.assertIn("https://x/1.jpg", out)
        self.assertIn("https://x/2.jpg", out)

    def test_bild_suchen_fallback_in_browser(self):
        def boom(url, headers=None):
            raise OSError("offline")
        self.mod._http_get = boom
        _, out = self.run_tool("bild_suchen", begriff="Katze")
        self.assertIn("Browser", out)
        self.assertTrue(self.geoeffnet and "duckduckgo" in self.geoeffnet[-1].lower())

    def test_bild_zeigen_oeffnet_bild(self):
        self.mod._http_get = lambda url, headers=None: (
            json.dumps({"results": [{"image": "https://x/a.jpg", "title": "t", "url": "u"}]})
            if "i.js" in url else '{"vqd":"4-9"}')
        self.run_tool("bild_zeigen", begriff="Hund")
        self.assertEqual(self.geoeffnet[-1], "https://x/a.jpg")

    # ---- Browser öffnen (URL vs. Suchtext)
    def test_im_browser_url_und_suche(self):
        self.run_tool("im_browser_oeffnen", adresse="https://example.org")
        self.assertEqual(self.geoeffnet[-1], "https://example.org")
        self.run_tool("im_browser_oeffnen", adresse="Madison Beer video")
        self.assertIn("duckduckgo.com/?q=", self.geoeffnet[-1])

    # ---- Download
    def test_download_speichert_datei(self):
        mock = MockDownload()
        self.addCleanup(mock.close)
        _, out = self.run_tool("medien_herunterladen", url=mock.base + "/bild.png")
        self.assertIn("Gespeichert", out)
        dateien = list(self.medien.glob("*.png"))
        self.assertEqual(len(dateien), 1)
        self.assertEqual(dateien[0].read_bytes(), BILD)

    def test_download_in_vollen_pfad(self):
        mock = MockDownload()
        self.addCleanup(mock.close)
        ziel = self.tmp / "ordnerX" / "mein bild.png"
        self.run_tool("medien_herunterladen", url=mock.base + "/x", ziel=str(ziel))
        self.assertTrue(ziel.exists())
        self.assertEqual(ziel.read_bytes(), BILD)

    def test_download_groessenlimit(self):
        mock = MockDownload()
        self.addCleanup(mock.close)
        self.mod._MAX_DOWNLOAD = 5  # künstlich klein
        try:
            with self.assertRaises(ToolError) as e:
                self.run_tool("medien_herunterladen", url=mock.base + "/gross.png")
        finally:
            self.mod._MAX_DOWNLOAD = 300 * 1024 * 1024
        self.assertIn("größer", str(e.exception).lower())

    def test_kein_http_link(self):
        with self.assertRaises(ToolError):
            self.run_tool("medien_herunterladen", url="ftp://x/y.png")


class BrowserWahlTest(unittest.TestCase):
    def test_schluessel_erkennung(self):
        mod = _load_plugin()
        self.assertEqual(mod._browser_schluessel("Opera GX"), "opera gx")
        self.assertEqual(mod._browser_schluessel("firefox"), "firefox")
        self.assertEqual(mod._browser_schluessel("Microsoft Edge"), "edge")
        self.assertEqual(mod._browser_schluessel("Google Chrome"), "chrome")

    def test_fallback_standardbrowser(self):
        import webbrowser
        mod = _load_plugin()
        mod._browser_exe = lambda name: None  # kein Browser gefunden
        aufgerufen = []
        orig = webbrowser.open
        webbrowser.open = lambda u, *a, **k: aufgerufen.append(u)
        try:
            mod._browser_oeffnen({"web_browser": "firefox"}, "https://a.b")
        finally:
            webbrowser.open = orig
        self.assertEqual(aufgerufen, ["https://a.b"])


if __name__ == "__main__":
    unittest.main()
