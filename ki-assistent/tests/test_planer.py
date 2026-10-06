import importlib.util
import unittest
from datetime import datetime
from pathlib import Path

from angel import planer
from angel.tools import ToolContext, ToolRegistry
from tests.helpers import TempDirTest, make_cfg

PROJECT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 5, 8, 0)  # fester Zeitpunkt für reproduzierbare Tests


def _load_plugin():
    spec = importlib.util.spec_from_file_location("angel_plugin_aufgaben", PROJECT / "plugins" / "aufgaben.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


class ZeitLogikTest(unittest.TestCase):
    def test_einmalig_heute_und_morgen(self):
        self.assertEqual(planer.naechste_ausfuehrung(NOW, "15:00"), datetime(2026, 10, 5, 15, 0))
        # Uhrzeit heute schon vorbei -> morgen
        self.assertEqual(planer.naechste_ausfuehrung(NOW, "07:00"), datetime(2026, 10, 6, 7, 0))

    def test_einmalig_mit_datum(self):
        self.assertEqual(planer.naechste_ausfuehrung(NOW, "10:00", datum="2026-12-24"),
                         datetime(2026, 12, 24, 10, 0))

    def test_taeglich(self):
        self.assertEqual(planer.naechste_ausfuehrung(NOW, "09:00", "taeglich"), datetime(2026, 10, 5, 9, 0))
        self.assertEqual(planer.naechste_ausfuehrung(NOW, "07:00", "taeglich"), datetime(2026, 10, 6, 7, 0))

    def test_woechentlich(self):
        wd = NOW.weekday()
        heute = planer.naechste_ausfuehrung(NOW, "09:00", "woechentlich", wochentag=wd)
        self.assertEqual(heute, datetime(2026, 10, 5, 9, 0))
        naechste_woche = planer.naechste_ausfuehrung(NOW, "07:00", "woechentlich", wochentag=wd)
        self.assertEqual(naechste_woche.weekday(), wd)
        self.assertEqual((naechste_woche.date() - NOW.date()).days, 7)

    def test_werktags_und_wochenende(self):
        wt = planer.naechste_ausfuehrung(NOW, "09:00", "werktags")
        self.assertLess(wt.weekday(), 5)
        self.assertGreater(wt, NOW)
        self.assertEqual((wt.hour, wt.minute), (9, 0))
        we = planer.naechste_ausfuehrung(NOW, "09:00", "wochenende")
        self.assertIn(we.weekday(), (5, 6))

    def test_einmalig_in_vergangenheit_ist_none(self):
        self.assertIsNone(planer.naechste_ausfuehrung(NOW, "10:00", datum="2000-01-01"))


class SpeicherTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.store = planer.Aufgaben(self.tmp / "aufgaben.json")

    def test_hinzufuegen_und_liste(self):
        t = self.store.hinzufuegen("Tom schreiben", typ="auftrag", uhrzeit="15:00", now=NOW)
        self.assertEqual(t["typ"], "auftrag")
        self.assertEqual(t["naechste"], "2026-10-05T15:00")
        self.assertEqual(len(self.store.liste()), 1)

    def test_vergangenheit_abgelehnt(self):
        with self.assertRaises(ValueError):
            self.store.hinzufuegen("zu spaet", uhrzeit="10:00", datum="2000-01-01", now=NOW)

    def test_faellige_und_einmalig_deaktiviert(self):
        t = self.store.hinzufuegen("erinnern", uhrzeit="09:00", now=NOW)  # heute 09:00
        self.assertEqual(self.store.faellige(NOW), [])  # um 08:00 noch nicht
        spaeter = datetime(2026, 10, 5, 9, 1)
        self.assertEqual(len(self.store.faellige(spaeter)), 1)
        self.store.nach_ausfuehrung(t["id"], spaeter)
        self.assertEqual(self.store.liste(), [])  # einmalig -> abgeschaltet

    def test_wiederkehrend_neu_terminiert(self):
        t = self.store.hinzufuegen("taeglich", uhrzeit="09:00", wiederholung="taeglich", now=NOW)
        self.store.nach_ausfuehrung(t["id"], datetime(2026, 10, 5, 9, 1))
        nur = self.store.liste()[0]
        self.assertEqual(nur["naechste"], "2026-10-06T09:00")  # morgen
        self.assertTrue(nur["aktiv"])

    def test_entfernen(self):
        t = self.store.hinzufuegen("x", uhrzeit="15:00", now=NOW)
        self.assertTrue(self.store.entfernen(t["id"]))
        self.assertFalse(self.store.entfernen("gibtsnicht"))


class SchedulerTest(TempDirTest):
    def test_tick_meldet_und_terminiert(self):
        store = planer.Aufgaben(self.tmp / "a.json")
        store.hinzufuegen("erinnern", uhrzeit="09:00", wiederholung="taeglich", now=NOW)
        store.hinzufuegen("tun", typ="auftrag", uhrzeit="09:00", now=NOW)  # einmalig
        erinnert, getan = [], []
        sched = planer.Scheduler(store, lambda t: erinnert.append(t), lambda t: (getan.append(t) or True))
        spaeter = datetime(2026, 10, 5, 9, 5)
        sched.tick(spaeter)
        self.assertEqual(len(erinnert), 1)
        self.assertEqual(len(getan), 1)
        self.assertEqual(store.faellige(spaeter), [])   # nichts bleibt fällig
        self.assertEqual(len(store.liste()), 1)         # nur die wiederkehrende bleibt

    def test_auftrag_busy_bleibt_faellig(self):
        store = planer.Aufgaben(self.tmp / "b.json")
        store.hinzufuegen("tun", typ="auftrag", uhrzeit="09:00", now=NOW)
        sched = planer.Scheduler(store, lambda t: None, lambda t: False)  # immer "beschäftigt"
        spaeter = datetime(2026, 10, 5, 9, 5)
        sched.tick(spaeter)
        self.assertEqual(len(store.faellige(spaeter)), 1)  # wird später erneut versucht


class WerkzeugTest(TempDirTest):
    def setUp(self):
        super().setUp()
        _load_plugin()
        self.reg = ToolRegistry()
        (self.tmp / "daten").mkdir(parents=True, exist_ok=True)
        self.cfg = make_cfg(self.tmp)
        self.ctx = ToolContext(cfg=self.cfg, workdir=self.work, data_dir=self.tmp / "daten")

    def run_tool(self, name, **args):
        t = self.reg.get(name)
        self.assertIsNotNone(t, name)
        return self.reg.run(t, self.ctx, self.reg.prepare_args(t, args))

    def test_planen_anzeigen_absagen(self):
        out = self.run_tool("aufgabe_planen", text="Tom schreiben", uhrzeit="15:00", typ="auftrag")
        self.assertIn("geplant", out.lower())
        self.assertIn("Tom schreiben", self.run_tool("aufgaben_anzeigen"))
        store = planer.hole_aufgaben(self.ctx.data_dir / "aufgaben.json")
        tid = store.liste()[0]["id"]
        self.assertIn("abgesagt", self.run_tool("aufgabe_absagen", id=tid))
        self.assertEqual(store.liste(), [])

    def test_wochentag_wird_uebernommen(self):
        self.run_tool("aufgabe_planen", text="Montagsliste", uhrzeit="08:00",
                      wiederholung="woechentlich", wochentag=0)
        store = planer.hole_aufgaben(self.ctx.data_dir / "aufgaben.json")
        self.assertEqual(store.liste()[0]["wochentag"], 0)


if __name__ == "__main__":
    unittest.main()
