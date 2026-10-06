import importlib.util
import os
import time
import unittest
from pathlib import Path

from angel.tools import ToolContext, ToolError, ToolRegistry
from tests.helpers import TempDirTest, make_cfg

PROJECT = Path(__file__).resolve().parent.parent


def _load_plugin():
    spec = importlib.util.spec_from_file_location("angel_plugin_pcpflege", PROJECT / "plugins" / "pc_pflege.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PcPflegeTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.mod = _load_plugin()
        self.reg = ToolRegistry()
        self.cfg = make_cfg(self.tmp)
        self.ctx = ToolContext(cfg=self.cfg, workdir=self.work, data_dir=self.tmp / "daten")

    def run_tool(self, name, **args):
        t = self.reg.get(name)
        self.assertIsNotNone(t, name)
        return self.reg.run(t, self.ctx, self.reg.prepare_args(t, args))

    def _alt_machen(self, pfad, tage=3):
        past = time.time() - tage * 86400
        os.utime(pfad, (past, past))

    def test_aufraeumen_alte_weg_neue_bleiben(self):
        d = self.tmp / "temp"
        d.mkdir()
        alt = d / "alt.tmp"
        alt.write_bytes(b"x" * 1000)
        self._alt_machen(alt)
        neu = d / "neu.tmp"
        neu.write_bytes(b"y" * 10)  # frisch -> bleibt
        sub = d / "altsub"
        sub.mkdir()
        (sub / "f.dat").write_bytes(b"z" * 500)
        self._alt_machen(sub)
        anzahl, frei = self.mod._aufraeumen_ordner(d, 1)
        self.assertFalse(alt.exists())
        self.assertFalse(sub.exists())
        self.assertTrue(neu.exists())
        self.assertEqual(anzahl, 2)
        self.assertGreaterEqual(frei, 1000)

    def test_pc_aufraeumen_faesst_nur_tempordner_an(self):
        fake = self.tmp / "faketemp"
        fake.mkdir()
        muell = fake / "a.tmp"
        muell.write_bytes(b"x" * 2000)
        self._alt_machen(muell)
        schatz = self.work / "wichtig.txt"
        schatz.write_text("behalten", encoding="utf-8")
        self.mod._temp_ordner = lambda: [fake]  # nur unseren Testordner säubern
        out = self.run_tool("pc_aufraeumen", auch_papierkorb=False)
        self.assertFalse(muell.exists())
        self.assertTrue(schatz.exists())  # außerhalb der Temp-Ordner -> unberührt
        self.assertIn("gelöscht", out)

    def test_speicherplatz_lesbar(self):
        out = self.run_tool("speicherplatz")
        self.assertIn("frei", out)

    def test_bestaetigungsstufen(self):
        self.assertEqual(self.reg.get("pc_aufraeumen").confirm, "always")
        self.assertEqual(self.reg.get("viren_entfernen").confirm, "always")
        self.assertFalse(self.reg.get("speicherplatz").confirm)
        self.assertFalse(self.reg.get("viren_status").confirm)

    @unittest.skipIf(os.name == "nt", "Defender-Fehlerpfad nur auf Nicht-Windows prüfbar")
    def test_defender_nur_windows(self):
        for name in ("virenscan", "viren_status", "viren_entfernen", "virenschutz_aktualisieren"):
            with self.assertRaises(ToolError) as e:
                self.run_tool(name)
            self.assertIn("Windows", str(e.exception))


if __name__ == "__main__":
    unittest.main()
