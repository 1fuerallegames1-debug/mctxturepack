import unittest

from angel.gespraeche import Gespraeche, GespraechFehler, verschluesselung_verfuegbar
from tests.helpers import TempDirTest

MSGS = [{"role": "user", "content": "Hallo Angel, wie gehts?"},
        {"role": "assistant", "content": "Mir gehts gut!"}]


class GespraecheTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.g = Gespraeche(self.tmp)

    def test_speichern_laden_und_titel(self):
        cid = self.g.neu()
        self.g.speichern(cid, MSGS, self.g.lokaler_key())
        self.assertEqual(self.g.laden(cid, self.g.lokaler_key()), MSGS)
        liste = self.g.liste()
        self.assertEqual(len(liste), 1)
        self.assertIn("Hallo Angel", liste[0]["titel"])
        self.assertFalse(liste[0]["gesperrt"])

    @unittest.skipUnless(verschluesselung_verfuegbar(), "cryptography nicht installiert")
    def test_auf_der_platte_verschluesselt(self):
        cid = self.g.neu()
        self.g.speichern(cid, MSGS, self.g.lokaler_key())
        roh = (self.tmp / "gespraeche" / f"{cid}.chat").read_bytes()
        self.assertTrue(roh.startswith(b"E1\n"))
        self.assertNotIn(b"Hallo Angel", roh)  # Klartext steht NICHT in der Datei

    def test_loeschen(self):
        cid = self.g.neu()
        self.g.speichern(cid, MSGS, self.g.lokaler_key())
        self.g.loeschen(cid)
        self.assertEqual(self.g.liste(), [])
        self.assertFalse((self.tmp / "gespraeche" / f"{cid}.chat").exists())

    def test_mehrere_sortiert_neueste_zuerst(self):
        a = self.g.neu()
        self.g.speichern(a, [{"role": "user", "content": "erster"}], self.g.lokaler_key())
        b = self.g.neu()
        self.g.speichern(b, [{"role": "user", "content": "zweiter"}], self.g.lokaler_key())
        self.assertEqual(self.g.liste()[0]["id"], b)  # zuletzt gespeichert -> oben

    @unittest.skipUnless(verschluesselung_verfuegbar(), "cryptography nicht installiert")
    def test_sperren_und_entsperren_mit_passwort(self):
        cid = self.g.neu()
        self.g.speichern(cid, MSGS, self.g.lokaler_key())
        self.g.sperren(cid, "geheim123", self.g.lokaler_key())
        self.assertTrue(self.g.ist_gesperrt(cid))
        # mit lokalem Schlüssel NICHT mehr ladbar
        with self.assertRaises(GespraechFehler):
            self.g.laden(cid, self.g.lokaler_key())
        # falsches Passwort -> Fehler
        with self.assertRaises(GespraechFehler):
            self.g.entsperren_key(cid, "falsch")
        # richtiges Passwort -> Schlüssel, Chat ladbar
        key = self.g.entsperren_key(cid, "geheim123")
        self.assertEqual(self.g.laden(cid, key), MSGS)

    @unittest.skipUnless(verschluesselung_verfuegbar(), "cryptography nicht installiert")
    def test_entsperren_zurueck_auf_lokal(self):
        cid = self.g.neu()
        self.g.speichern(cid, MSGS, self.g.lokaler_key())
        key = self.g.sperren(cid, "pw", self.g.lokaler_key())
        self.g.entsperren(cid, key)
        self.assertFalse(self.g.ist_gesperrt(cid))
        self.assertEqual(self.g.laden(cid, self.g.lokaler_key()), MSGS)

    def test_neuer_index_ueberlebt_neustart(self):
        cid = self.g.neu()
        self.g.speichern(cid, MSGS, self.g.lokaler_key())
        g2 = Gespraeche(self.tmp)  # "Neustart"
        self.assertEqual(len(g2.liste()), 1)
        self.assertEqual(g2.laden(cid, g2.lokaler_key()), MSGS)


if __name__ == "__main__":
    unittest.main()
