import unittest

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


class SchutzTest(TempDirTest):
    def test_sicherheit_ist_geschuetzter_kern(self):
        # Angel darf die Passwort-Datei nicht selbst ändern (Regel 3 / Programmkern)
        from pathlib import Path

        from angel import regeln
        ziel = Path(regeln.PROTECTED_DIR) / "sicherheit.py"
        self.assertTrue(regeln.touches_core(ziel))


if __name__ == "__main__":
    unittest.main()
