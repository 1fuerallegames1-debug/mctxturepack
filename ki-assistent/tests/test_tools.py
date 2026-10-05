import json
import os
import sys
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from angel.config import ConfigError, load_config, save_setting
from angel.tools import ToolContext, ToolError, ToolRegistry, truncate
from angel.tools import files, system, web
from angel.tools.memory import Memory
from tests.helpers import TempDirTest, make_cfg


class ToolTestBase(TempDirTest):
    def setUp(self):
        super().setUp()
        self.cfg = make_cfg(self.tmp)
        self.memory = Memory(self.tmp / "daten" / "gedaechtnis.json")
        self.ctx = ToolContext(cfg=self.cfg, workdir=self.work, data_dir=self.tmp / "daten", memory=self.memory)


class FileToolsTest(ToolTestBase):
    def test_read_file_in_parts(self):
        (self.work / "a.txt").write_text("\n".join(f"Zeile {i}" for i in range(1, 11)), encoding="utf-8")
        out = files.read_file(self.ctx, "a.txt", start_line=3, max_lines=2)
        self.assertIn("Zeilen 3-4 von 10", out)
        self.assertIn("start_line=5", out)
        self.assertIn("Zeile 3\nZeile 4", out)

    def test_read_file_encodings_and_binary(self):
        (self.work / "win.txt").write_bytes("Größe".encode("cp1252"))
        self.assertIn("Größe", files.read_file(self.ctx, "win.txt"))
        (self.work / "bin.dat").write_bytes(b"\x00\x01\x02" * 10)
        with self.assertRaises(ToolError):
            files.read_file(self.ctx, "bin.dat")
        with self.assertRaises(ToolError):
            files.read_file(self.ctx, "fehlt.txt")

    def test_read_docx(self):
        path = self.work / "brief.docx"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>Hallo &amp; Tschüss</w:t></w:r></w:p>'
                                            '<w:p><w:r><w:t>Zweiter Absatz</w:t></w:r></w:p></w:body></w:document>')
        out = files.read_file(self.ctx, str(path))
        self.assertIn("Hallo & Tschüss\nZweiter Absatz", out)

    def test_write_file_creates_backup(self):
        files.write_file(self.ctx, "sub/neu.txt", "eins")
        self.assertEqual((self.work / "sub" / "neu.txt").read_text(encoding="utf-8"), "eins")
        out = files.write_file(self.ctx, "sub/neu.txt", "zwei")
        self.assertIn("gesichert", out)
        backups = list((self.tmp / "daten" / "sicherungen").iterdir())
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "eins")
        files.write_file(self.ctx, "sub/neu.txt", "drei", append=True)
        self.assertEqual((self.work / "sub" / "neu.txt").read_text(encoding="utf-8"), "zweidrei")

    def test_read_file_paging_never_skips_lines(self):
        self.cfg["max_ausgabe_zeichen"] = 1000
        (self.work / "log.txt").write_text("\n".join(f"{i:04d} " + "x" * 95 for i in range(1, 101)), encoding="utf-8")
        seen, start = [], 1
        for _ in range(50):
            out = files.read_file(self.ctx, "log.txt", start_line=start)
            seen += [int(line[:4]) for line in out.splitlines()[1:]]
            if "start_line=" not in out:
                break
            start = int(out.split("start_line=")[1].split()[0])
        self.assertEqual(seen, list(range(1, 101)))

    def test_backups_do_not_overwrite_each_other(self):
        for text in ("eins", "zwei", "drei"):
            files.write_file(self.ctx, "config.yml", text)
        backups = sorted(p.read_text(encoding="utf-8") for p in (self.tmp / "daten" / "sicherungen").iterdir())
        self.assertEqual(backups, ["eins", "zwei"])

    def test_append_keeps_existing_encoding(self):
        (self.work / "u16.txt").write_bytes("Größe\r\n".encode("utf-16"))
        files.write_file(self.ctx, "u16.txt", "Übel\r\n", append=True)
        self.assertEqual((self.work / "u16.txt").read_bytes().decode("utf-16"), "Größe\r\nÜbel\r\n")
        (self.work / "ansi.txt").write_bytes("Größe\n".encode("cp1252"))
        files.write_file(self.ctx, "ansi.txt", "Übel\n", append=True)
        self.assertEqual((self.work / "ansi.txt").read_bytes().decode("cp1252"), "Größe\nÜbel\n")

    def test_batch_files_get_crlf_and_utf8(self):
        files.write_file(self.ctx, "sicherung.bat", "@echo off\nxcopy Welten D:\\Übersicht\n")
        raw = (self.work / "sicherung.bat").read_bytes()
        self.assertTrue(raw.startswith(b"@chcp 65001 >nul\r\n@echo off\r\n"))
        self.assertIn("Übersicht".encode("utf-8"), raw)

    def test_utf16_files(self):
        (self.work / "ps.txt").write_bytes("Größe\r\nZeile 2".encode("utf-16"))  # mit BOM
        self.assertIn("Größe", files.read_file(self.ctx, "ps.txt"))
        (self.work / "le.txt").write_bytes(("Hallo Welt " * 20).encode("utf-16-le"))  # ohne BOM
        self.assertIn("Hallo Welt", files.read_file(self.ctx, "le.txt"))

    def test_write_file_encodings(self):
        files.write_file(self.ctx, "skript.ps1", "Write-Host 'Grüße – fertig'")
        self.assertTrue((self.work / "skript.ps1").read_bytes().startswith(b"\xef\xbb\xbf"))
        files.write_file(self.ctx, "liste.csv", "Äpfel;2\n")
        files.write_file(self.ctx, "liste.csv", "Birnen;3\n", append=True)
        raw = (self.work / "liste.csv").read_bytes()
        self.assertEqual(raw.count(b"\xef\xbb\xbf"), 1)
        self.assertEqual(raw.decode("utf-8-sig"), "Äpfel;2\nBirnen;3\n")
        files.write_file(self.ctx, "normal.txt", "ü")
        self.assertEqual((self.work / "normal.txt").read_bytes(), "ü".encode("utf-8"))

    def test_find_matches_hidden_and_skipped_folders_by_name(self):
        (self.work / "AppData" / "Roaming" / ".minecraft" / "saves").mkdir(parents=True)
        (self.work / "projekt" / "node_modules").mkdir(parents=True)
        found = files.find_files(self.ctx, "minecraft", directory=str(self.work))
        self.assertIn(".minecraft", found)
        self.assertIn("node_modules", files.find_files(self.ctx, "node_modules", directory=str(self.work)))

    def test_list_and_find(self):
        (self.work / "Urlaub").mkdir()
        (self.work / "Urlaub" / "Strand.JPG").write_text("x")
        (self.work / ".versteckt").mkdir()
        (self.work / ".versteckt" / "strand2.jpg").write_text("x")
        (self.work / "node_modules").mkdir()
        (self.work / "node_modules" / "strand3.jpg").write_text("x")
        listing = files.list_directory(self.ctx, ".")
        self.assertIn("[Ordner] Urlaub/", listing)
        self.assertNotIn(".versteckt", listing)
        found = files.find_files(self.ctx, "*.jpg", directory=str(self.work))
        self.assertIn("Strand.JPG", found)
        self.assertLess(found.index("Strand.JPG"), found.index("strand2"))  # versteckte Ordner zuletzt
        self.assertNotIn("strand3", found)
        self.assertIn(".versteckt", files.find_files(self.ctx, ".versteckt", directory=str(self.work)))
        self.assertIn("Nichts gefunden", files.find_files(self.ctx, "gibtsnicht", directory=str(self.work)))


@unittest.skipIf(os.name == "nt", "Shell-Tests sind für bash geschrieben")
class CommandToolsTest(ToolTestBase):
    def test_run_command(self):
        out = system.run_command(self.ctx, "echo hallo; echo fehler >&2; exit 3")
        self.assertIn("Exit-Code: 3", out)
        self.assertIn("hallo", out)
        self.assertIn("fehler", out)

    def test_run_command_working_directory_and_umlauts(self):
        (self.work / "unter").mkdir()
        out = system.run_command(self.ctx, "pwd; echo Größe", working_directory="unter")
        self.assertIn(str(self.work / "unter"), out)
        self.assertIn("Größe", out)

    def test_run_command_timeout_kills_process(self):
        start = time.monotonic()
        out = system.run_command(self.ctx, "sleep 30", timeout=1)
        self.assertLess(time.monotonic() - start, 10)
        self.assertIn("Zeitlimit", out)

    def test_run_command_background(self):
        out = system.run_command(self.ctx, "echo gestartet; sleep 2", background=True)
        self.assertIn("PID", out)
        self.assertIn("gestartet", out)

    def test_run_command_cancel(self):
        threading.Timer(0.5, self.ctx.cancel_event.set).start()
        start = time.monotonic()
        out = system.run_command(self.ctx, "sleep 30")
        self.assertLess(time.monotonic() - start, 10)
        self.assertIn("abgebrochen", out)

    def test_program_that_keeps_running_does_not_block(self):
        start = time.monotonic()
        out = system.run_command(self.ctx, "echo vorher; sleep 30 & echo nachher")
        self.assertLess(time.monotonic() - start, 8)
        self.assertIn("nachher", out)
        self.assertIn("läuft weiter", out)
        self.assertIn("Exit-Code: 0", out)

    def test_ansi_codes_are_removed(self):
        out = system.run_command(self.ctx, "printf '\\033[31mrot\\033[0m'")
        self.assertIn("rot", out)
        self.assertNotIn("\x1b", out)

    def test_run_python(self):
        out = system.run_python(self.ctx, "print(6*7)\nprint('Ä')")
        self.assertIn("42", out)
        self.assertIn("Ä", out)
        self.assertEqual(list((self.tmp / "daten" / "tmp").iterdir()), [])  # Temp-Datei aufgeräumt

    def test_system_info(self):
        out = system.system_info(self.ctx)
        self.assertIn("Datum/Uhrzeit", out)
        self.assertIn("Arbeitsordner", out)


class SafetyTest(ToolTestBase):
    def test_dangerous_patterns(self):
        dangerous = ["rm -rf /", "rm -fr ~/x", "Remove-Item C:\\Daten -Recurse -Force", "format C:",
                     "rd /s /q C:\\x", "del /s *.*", "shutdown /s /t 0", "Stop-Computer", "diskpart",
                     "reg delete HKLM\\x", "Format-Volume -DriveLetter D", "rm -r -fo C:\\Users\\Max\\alt",
                     'rmdir "$HOME\\Documents" -Recurse', "Remove-Item X -r -Force", "del D:\\Server -Recurse",
                     "rm -r -f ~/x", "rm -R ordner", "rm --recursive x", "vssadmin delete shadows /all /quiet",
                     "Clear-RecycleBin -Force", "Set-MpPreference -DisableRealtimeMonitoring $true",
                     "gci $HOME\\Documents -Recurse -File | Remove-Item -Force"]
        harmless = ["ls -la", "Get-ChildItem", "echo format", "git status", "Remove-Item a.txt", "dir",
                    "rm datei.txt", "Get-ChildItem -Recurse", "del alt.log", "gci -Recurse | Select-Object Name"]
        for cmd in dangerous:
            self.assertEqual(system._command_confirm(self.ctx, {"command": cmd}), "always", cmd)
        for cmd in harmless:
            self.assertIs(system._command_confirm(self.ctx, {"command": cmd}), True, cmd)

    def test_open_item_confirmation(self):
        self.ctx.remember_urls("https://example.org", from_user=True)
        self.ctx.remember_urls("https://from-search.example")
        self.assertFalse(system._open_confirm(self.ctx, {"target": "https://example.org"}))
        self.assertTrue(system._open_confirm(self.ctx, {"target": "https://from-search.example"}))
        self.assertTrue(system._open_confirm(self.ctx, {"target": "https://evil.example/?x=1"}))
        self.assertTrue(system._open_confirm(self.ctx, {"target": "C:\\Downloads\\setup.exe"}))

    def test_unusual_urls_always_ask(self):
        self.assertEqual(system.clean_web_url("https://www.youtube.com/watch?v=1"), "https://www.youtube.com/watch?v=1")
        for url in ("https://www.youtube.com\\@evil.example/", "https://user@evil.example/", "https://a b.de/"):
            self.assertIsNone(system.clean_web_url(url), url)
            self.assertEqual(system._open_confirm(self.ctx, {"target": url}), "always")
            self.assertIsNone(system._open_scope(url))

    def test_risky_file_types_are_shown_completely(self):
        content = "\n".join(f"zeile {i}" for i in range(60))
        self.assertIn("zeile 59", files._write_summary({"path": "C:/x/autorun.pth", "content": content}))
        self.assertIn("zeile 59", files._write_summary({"path": "/home/x/.bashrc", "content": content}))
        self.assertIn("insgesamt 60", files._write_summary({"path": "notiz.txt", "content": content}))

    def test_clixml_cleanup(self):
        raw = ('#< CLIXML\r\n<Objs Version="1.1.0.1"><S S="Error">Fehler: Datei fehlt_x000D__x000A_</S>'
               '<S S="Error">zweite Zeile</S></Objs>')
        self.assertEqual(system._clean_clixml(raw), "Fehler: Datei fehlt\r\nzweite Zeile")
        self.assertEqual(system._clean_clixml("normaler Text"), "normaler Text")


class RegistryTest(ToolTestBase):
    def test_argument_coercion_and_unknown_keys(self):
        reg = ToolRegistry()
        tool = reg.get("read_file")
        args = reg.prepare_args(tool, {"path": "a", "start_line": "5", "max_lines": 10.0, "erfunden": 1})
        self.assertEqual(args, {"path": "a", "start_line": 5, "max_lines": 10})
        with self.assertRaises(ToolError):
            reg.prepare_args(tool, {"start_line": 1})
        with self.assertRaises(ToolError):
            reg.prepare_args(tool, None)

    def test_each_tool_runs_its_own_function(self):
        for name, t in ToolRegistry().tools.items():
            self.assertEqual(t.func.__name__, name)

    def test_empty_content_is_allowed_for_files(self):
        reg = ToolRegistry()
        self.assertEqual(reg.prepare_args(reg.get("write_file"), {"path": "leer.txt", "content": ""}),
                         {"path": "leer.txt", "content": ""})
        with self.assertRaises(ToolError):
            reg.prepare_args(reg.get("write_file"), {"path": "", "content": "x"})

    def test_schemas_are_valid(self):
        for schema in ToolRegistry().schemas():
            fn = schema["function"]
            self.assertRegex(fn["name"], r"^[a-z_][a-z0-9_]*$")
            self.assertTrue(fn["description"])
            for req in fn["parameters"]["required"]:
                self.assertIn(req, fn["parameters"]["properties"])

    def test_describe_shows_paths_without_escaping(self):
        tool = ToolRegistry().get("list_directory")
        self.assertEqual(tool.describe({"path": "C:\\Users\\Alex", "show_hidden": True}),
                         "path=C:\\Users\\Alex, show_hidden=true")

    def test_truncate(self):
        text = "A" * 100 + "B" * 100
        out = truncate(text, 50)
        self.assertTrue(out.startswith("A" * 30))
        self.assertTrue(out.endswith("B" * 20))
        self.assertIn("ausgelassen", out)
        self.assertEqual(truncate("kurz", 50), "kurz")


class MemoryRobustnessTest(ToolTestBase):
    def test_broken_file_is_kept_aside(self):
        path = self.tmp / "daten" / "kaputt.json"
        path.write_text("[{'text': kaputt", encoding="utf-8")
        mem = Memory(path)
        self.assertEqual(mem.facts, [])
        self.assertIn("verschoben", mem.warning)
        self.assertEqual(len(list(path.parent.glob("gedaechtnis.defekt-*.json"))), 1)

    def test_bom_and_missing_ids(self):
        path = self.tmp / "daten" / "bom.json"
        path.write_text(json.dumps([{"text": "Ich spiele Minecraft"}, {"id": 1, "text": "Hund Bello"},
                                    {"id": "x", "text": "Katze"}, "müll", {"text": ""}]), encoding="utf-8-sig")
        mem = Memory(path)
        self.assertEqual(sorted(f["id"] for f in mem.facts), [1, 2, 3])
        self.assertIn("Minecraft", mem.prompt_text())


class MemoryTest(ToolTestBase):
    def test_add_dedupe_remove_persist(self):
        a = self.memory.add("Ich mag  Pizza.")
        b = self.memory.add("ich mag pizza.")
        self.assertEqual(a["id"], b["id"])
        self.memory.add("Mein Hund heißt Bello.")
        again = Memory(self.memory.path)
        self.assertEqual(len(again.facts), 2)
        self.assertIn("[2] Mein Hund heißt Bello.", again.prompt_text())
        self.assertTrue(again.remove(1))
        self.assertIsNone(again.remove(99))
        self.assertEqual(len(Memory(self.memory.path).facts), 1)


DDG_HTML = """
<div class="result results_links"><h2 class="result__title">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2F&amp;rut=abc">Download <b>Python</b></a></h2>
<a class="result__snippet" href="x">Die neueste Version von <b>Python</b> herunterladen.</a></div>
<div class="result result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Werbung</a></div>
<table><tr><td><a rel="nofollow" href="https://example.org/lite" class='result-link'>Lite Treffer</a></td></tr>
<tr><td class='result-snippet'>Text aus der Lite-Version</td></tr></table>
"""


class _Pages(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/seite":
            body = ("<html><head><title>Testseite</title><style>.x{}</style></head><body><nav>Menü</nav>"
                    "<h1>Überschrift</h1><p>Erster&nbsp;Absatz.</p><script>alert(1)</script><ul><li>Punkt</li></ul>"
                    "</body></html>").encode("utf-8")
            ctype = "text/html; charset=utf-8"
        elif self.path == "/bild":
            body, ctype = b"\x89PNG....", "image/png"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WebToolsTest(ToolTestBase):
    def test_parse_duckduckgo(self):
        results = web.parse_ddg(DDG_HTML)
        self.assertEqual(results[0]["url"], "https://www.python.org/downloads/")
        self.assertEqual(results[0]["title"], "Download Python")
        self.assertIn("neueste Version", results[0]["snippet"])
        self.assertEqual(results[1]["url"], "https://example.org/lite")
        self.assertEqual(results[1]["snippet"], "Text aus der Lite-Version")
        self.assertEqual(len(results), 2)  # Werbung entfernt

    def test_html_in_form_and_without_head_end(self):
        page = "<html><head><title>Amt</title><body><form id=f1><h1>Öffnungszeiten</h1><p>Mo 8-12</p></form></body>"
        title, text = web.html_to_text(page)
        self.assertEqual(title, "Amt")
        self.assertIn("Öffnungszeiten", text)
        self.assertIn("Mo 8-12", text)

    def test_html_to_text(self):
        title, text = web.html_to_text("<title>T</title><body><p>a</p><script>x()</script><p>b &amp; c</p></body>")
        self.assertEqual(title, "T")
        self.assertEqual(text, "a\n\nb & c")

    def test_fetch_webpage(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        out = web.fetch_webpage(self.ctx, base + "/seite")
        self.assertIn("Titel: Testseite", out)
        self.assertIn("## Überschrift", out)
        self.assertIn("Erster\xa0Absatz.", out)
        self.assertIn("- Punkt", out)
        self.assertNotIn("alert", out)
        self.assertNotIn("Menü", out)
        with self.assertRaises(ToolError):
            web.fetch_webpage(self.ctx, base + "/bild")
        with self.assertRaises(ToolError):
            web.fetch_webpage(self.ctx, base + "/fehlt")
        with self.assertRaises(ToolError):
            web.fetch_webpage(self.ctx, "file:///etc/passwd")


class ConfigTest(TempDirTest):
    def test_merge_and_comments(self):
        path = self.tmp / "config.json"
        path.write_text(json.dumps({"_hinweis": "x", "modell": "gemma4", "websuche": {"anbieter": "searxng"}}),
                        encoding="utf-8")
        cfg = load_config(path)
        self.assertEqual(cfg["modell"], "gemma4")
        self.assertEqual(cfg["websuche"], {"anbieter": "searxng", "searxng_url": ""})
        self.assertNotIn("_hinweis", cfg)
        self.assertEqual(cfg["kontext_laenge"], 16384)

    def test_invalid_json_is_explained(self):
        path = self.tmp / "config.json"
        path.write_text('{"modell": "x",}', encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("Zeile 1", str(ctx.exception))

    def test_save_setting_keeps_other_values(self):
        path = self.tmp / "config.json"
        path.write_text(json.dumps({"name": "Jarvis", "modell": "a"}), encoding="utf-8")
        save_setting("modell", "b", path)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"name": "Jarvis", "modell": "b"})

    def test_ansi_config_and_path_hint(self):
        path = self.tmp / "config.json"
        path.write_bytes('{"dein_name": "Jürgen"}'.encode("cp1252"))
        self.assertEqual(load_config(path)["dein_name"], "Jürgen")
        path.write_text('{"arbeitsordner": "D:\\Server"}', encoding="utf-8")  # einfacher Backslash = JSON-Fehler
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("doppelt", str(ctx.exception))

    def test_example_config_is_valid(self):
        example = Path(__file__).resolve().parent.parent / "config.beispiel.json"
        cfg = load_config(example)
        self.assertEqual(cfg["anbieter"], "ollama")


if __name__ == "__main__":
    unittest.main()
