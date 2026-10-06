import os
import unittest
from unittest import mock

from angel import sicherheit
from tests.helpers import TempDirTest

PW = "Joshua18#Onassis"


class PasswortTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.data = self.tmp / "daten"
        self.data.mkdir(parents=True, exist_ok=True)

    def test_passwort_korrekt(self):
        self.assertTrue(sicherheit.passwort_korrekt(PW))
        self.assertFalse(sicherheit.passwort_korrekt("falsch"))
        self.assertFalse(sicherheit.passwort_korrekt(""))
        self.assertFalse(sicherheit.passwort_korrekt(PW + " "))

    def test_richtiges_passwort_gibt_frei_und_setzt_zurueck(self):
        # erst zwei Fehlversuche, dann richtig -> Zähler wieder 0
        eingaben = iter(["x", "y", PW])
        self.assertEqual(sicherheit.pruefe_start(self.data, lambda rest: next(eingaben)),
                         sicherheit.FREIGEGEBEN)
        self.assertEqual(sicherheit.fehlversuche(self.data), 0)

    def test_abbruch_zaehlt_nicht(self):
        self.assertEqual(sicherheit.pruefe_start(self.data, lambda rest: None), sicherheit.ABGEBROCHEN)
        self.assertEqual(sicherheit.fehlversuche(self.data), 0)
        self.assertFalse(sicherheit.ist_gesperrt(self.data))

    def test_verbleibende_versuche_zaehlt_herunter(self):
        self.assertEqual(sicherheit.verbleibende_versuche(self.data), 5)
        sicherheit.registriere_fehlschlag(self.data)
        sicherheit.registriere_fehlschlag(self.data)
        self.assertEqual(sicherheit.verbleibende_versuche(self.data), 3)

    def test_fuenf_fehlversuche_zerstoeren_und_sperren(self):
        # Daten anlegen, auch in einem Unterordner
        (self.data / "gedaechtnis.json").write_text("geheim", encoding="utf-8")
        (self.data / "google_token.json").write_text("token", encoding="utf-8")
        profil = self.data / "browser-profil"
        profil.mkdir()
        (profil / "login.db").write_text("cookies", encoding="utf-8")

        # eine Datei AUSSERHALB von data_dir darf NICHT angetastet werden
        aussen = self.tmp / "wichtige_fotos.txt"
        aussen.write_text("meine Fotos", encoding="utf-8")

        rueck = sicherheit.pruefe_start(self.data, lambda rest: "falsch")
        self.assertEqual(rueck, sicherheit.ZERSTOERT)

        # Angels Daten sind weg
        self.assertFalse((self.data / "gedaechtnis.json").exists())
        self.assertFalse((self.data / "google_token.json").exists())
        self.assertFalse(profil.exists())
        # aber gesperrt-Marker ist da
        self.assertTrue(sicherheit.ist_gesperrt(self.data))
        # Datei außerhalb ist unberührt
        self.assertTrue(aussen.exists())
        self.assertEqual(aussen.read_text(encoding="utf-8"), "meine Fotos")

    def test_nach_sperre_kommt_man_nicht_mehr_rein(self):
        sicherheit.selbstzerstoerung(self.data)
        # auch mit richtigem Passwort bleibt Angel gesperrt
        self.assertEqual(sicherheit.pruefe_start(self.data, lambda rest: PW), sicherheit.GESPERRT)

    def test_genau_fuenf_versuche(self):
        # vier Fehlversuche -> noch nicht zerstört
        for _ in range(4):
            sicherheit.registriere_fehlschlag(self.data)
        self.assertFalse(sicherheit.ist_gesperrt(self.data))
        self.assertEqual(sicherheit.verbleibende_versuche(self.data), 1)
        # der fünfte Versuch (falsch) zerstört
        self.assertEqual(sicherheit.pruefe_start(self.data, lambda rest: "nein"), sicherheit.ZERSTOERT)


class HaertungTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.data = self.tmp / "daten"
        self.data.mkdir(parents=True, exist_ok=True)

    def test_manipulierter_zaehler_loest_zerstoerung_aus(self):
        # Jemand setzt den Zähler von Hand auf 0 (ohne gültige Signatur)
        import json
        (self.data / "sicherheit.json").write_text(json.dumps({"fehlversuche": 0, "sig": "fake"}),
                                                    encoding="utf-8")
        self.assertEqual(sicherheit.fehlversuche(self.data), sicherheit.MAX_VERSUCHE)  # fail-closed
        # sogar mit RICHTIGEM Passwort: manipulierter Zähler -> Selbstzerstörung
        self.assertEqual(sicherheit.pruefe_start(self.data, lambda rest: PW), sicherheit.ZERSTOERT)
        self.assertTrue(sicherheit.ist_gesperrt(self.data))

    def test_gueltiger_zaehler_hat_signatur(self):
        import json
        sicherheit.registriere_fehlschlag(self.data)
        gespeichert = json.loads((self.data / "sicherheit.json").read_text(encoding="utf-8"))
        self.assertEqual(gespeichert["fehlversuche"], 1)
        self.assertIn("sig", gespeichert)
        self.assertEqual(sicherheit.fehlversuche(self.data), 1)  # eigene Signatur wird akzeptiert

    def test_nicht_speicherbarer_zaehler_gilt_als_limit(self):
        # Wenn der Zähler nicht dauerhaft geschrieben werden kann, gibt es keine Gnade
        with mock.patch.object(sicherheit, "_schreibe_fehlversuche", return_value=False):
            self.assertEqual(sicherheit.registriere_fehlschlag(self.data), sicherheit.MAX_VERSUCHE)

    def test_sicherer_datenordner_erkennung(self):
        from pathlib import Path
        self.assertTrue(sicherheit._ist_sicherer_datenordner(self.data))
        # Home, Projektordner, dessen Elternordner und die Wurzel sind TABU
        self.assertFalse(sicherheit._ist_sicherer_datenordner(Path.home()))
        self.assertFalse(sicherheit._ist_sicherer_datenordner(sicherheit._PROJECT_DIR))
        self.assertFalse(sicherheit._ist_sicherer_datenordner(sicherheit._PROJECT_DIR.parent))
        self.assertFalse(sicherheit._ist_sicherer_datenordner(Path(self.data.anchor)))

    def test_selbstzerstoerung_verweigert_unsicheren_ordner(self):
        schatz = self.data / "wichtig.txt"
        schatz.write_text("nicht loeschen", encoding="utf-8")
        # Tut so, als wäre der Ordner unsicher -> es wird NICHTS gelöscht, nur gesperrt
        with mock.patch.object(sicherheit, "_ist_sicherer_datenordner", return_value=False):
            sicherheit.selbstzerstoerung(self.data)
        self.assertTrue(schatz.exists())                 # Datei blieb erhalten
        self.assertTrue(sicherheit.ist_gesperrt(self.data))  # aber gesperrt ist es trotzdem


class SchutzTest(TempDirTest):
    def test_sicherheit_ist_geschuetzter_kern(self):
        # Angel darf die Passwort-Datei nicht selbst ändern (Regel 3 / Programmkern)
        from pathlib import Path

        from angel import regeln
        ziel = Path(regeln.PROTECTED_DIR) / "sicherheit.py"
        self.assertTrue(regeln.touches_core(ziel))


class StartGateTest(TempDirTest):
    """Das Schloss sitzt zentral in __main__.main() – vor Google-Anmeldung und Agent-Aufbau."""

    def setUp(self):
        super().setUp()
        self.data = self.tmp / "daten"
        self.data.mkdir(parents=True, exist_ok=True)

    def test_gate_konsole_richtig_und_falsch(self):
        from angel import __main__ as m
        with mock.patch("getpass.getpass", lambda *a, **k: PW):
            self.assertTrue(m._gate_konsole(self.data, sicherheit))
        data2 = self.tmp / "daten2"
        data2.mkdir()
        with mock.patch("getpass.getpass", lambda *a, **k: "falsch"):
            self.assertFalse(m._gate_konsole(data2, sicherheit))

    def test_google_anmelden_ohne_passwort_blockiert(self):
        from angel import __main__ as m
        aufgerufen = {"login": False}

        def fake_login(cfg):
            aufgerufen["login"] = True
            return 0

        with mock.patch.dict(os.environ, {"ANGEL_DATEN": str(self.data)}, clear=False), \
             mock.patch.object(m, "_google_login", fake_login), \
             mock.patch("getpass.getpass", lambda *a, **k: "falsch"):
            rc = m.main(["--google-anmelden"])
        self.assertEqual(rc, 0)
        self.assertFalse(aufgerufen["login"])  # ohne richtiges Passwort KEINE Google-Anmeldung

    def test_google_anmelden_mit_passwort_laeuft(self):
        from angel import __main__ as m
        aufgerufen = {"login": False}

        def fake_login(cfg):
            aufgerufen["login"] = True
            return 0

        with mock.patch.dict(os.environ, {"ANGEL_DATEN": str(self.data)}, clear=False), \
             mock.patch.object(m, "_google_login", fake_login), \
             mock.patch("getpass.getpass", lambda *a, **k: PW):
            rc = m.main(["--google-anmelden"])
        self.assertEqual(rc, 0)
        self.assertTrue(aufgerufen["login"])  # richtiges Passwort -> Anmeldung läuft

    def test_autostart_laeuft_ohne_passwort(self):
        # --autostart darf NICHT nach Passwort fragen (sonst blockiert der Installer)
        from angel import __main__ as m

        def platzen(*a, **k):
            raise AssertionError("getpass darf bei --autostart nicht aufgerufen werden")

        with mock.patch.dict(os.environ, {"ANGEL_DATEN": str(self.data)}, clear=False), \
             mock.patch("getpass.getpass", platzen):
            rc = m.main(["--autostart", "status"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
