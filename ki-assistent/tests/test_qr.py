import hashlib
import unittest

from angel import icons
from angel.qr import QrCode

# Referenzwerte, erzeugt mit der Bibliothek "qrcode" (Byte-Modus, Fehlerkorrektur M, feste Maske)
REFERENCE_A_MASK0 = [
    "111111100000101111111", "100000101110101000001", "101110100011001011101", "101110100100101011101",
    "101110101010101011101", "100000100110101000001", "111111101010101111111", "000000000001100000000",
    "101010100101000010010", "011001011110001000110", "000101101000100010001", "100000001100001000100",
    "100001101000101010101", "000000001001010101011", "111111100001011101111", "100000100101110111000",
    "101110101001011101101", "101110100000001000110", "101110101000100010001", "100000100110001000110",
    "111111101100101010111",
]
REFERENCE_HASHES = [
    ("http://192.168.178.23:8765/#token=AbCdEfGhIjKlMnOpQrStUvWxYz012345", 3, 5,
     "6644c812d0ff408d5e8fc75e84e675872a520a7f348a5e4852b5269ff234a8fd"),
    ("x" * 150, 6, 8, "75fb25cabde9039f5b030f40fb0ac2df799e59a76071ff2cb37405c16f1e34d0"),
]


def rows(qr):
    return ["".join("1" if v else "0" for v in row) for row in qr.modules]


class QrTest(unittest.TestCase):
    def test_structure(self):
        qr = QrCode("http://192.168.178.23:8765/#token=" + "x" * 32)
        self.assertEqual(qr.size, qr.version * 4 + 17)
        # Suchmuster oben links: 7x7 Rahmen, innen 3x3 dunkel
        top = [qr.modules[0][x] for x in range(7)]
        self.assertEqual(top, [True] * 7)
        self.assertEqual([qr.modules[1][x] for x in range(7)], [True, False, False, False, False, False, True])
        self.assertTrue(qr.modules[3][3])
        # Taktmuster in Zeile 6
        self.assertEqual([qr.modules[6][x] for x in range(8, qr.size - 8)],
                         [x % 2 == 0 for x in range(8, qr.size - 8)])
        # immer dunkles Modul
        self.assertTrue(qr.modules[qr.size - 8][8])

    def test_versions_and_limits(self):
        self.assertEqual(QrCode("a").version, 1)
        self.assertEqual(QrCode("x" * 120).version, 7)
        self.assertEqual(QrCode("z" * 213).version, 10)
        with self.assertRaises(ValueError):
            QrCode("z" * 214)

    def test_matches_reference_library(self):
        self.assertEqual(rows(QrCode("a", mask=0)), REFERENCE_A_MASK0)
        for text, mask, version, digest in REFERENCE_HASHES:
            qr = QrCode(text, mask=mask)
            self.assertEqual(qr.version, version)
            self.assertEqual(hashlib.sha256("\n".join(rows(qr)).encode()).hexdigest(), digest)

    def test_terminal_rendering_roundtrip(self):
        qr = QrCode("Hallo Angel")
        text = qr.to_terminal(border=2)
        lines = text.splitlines()
        n = qr.size + 4
        self.assertEqual(len(lines), (n + 1) // 2)
        light = {"█": (True, True), "▀": (True, False), "▄": (False, True), " ": (False, False)}
        for y in range(qr.size):
            ch = lines[(y + 2) // 2][2:2 + qr.size]
            for x in range(qr.size):
                top, bottom = light[ch[x]]
                is_light = top if (y + 2) % 2 == 0 else bottom
                self.assertEqual(is_light, not qr.modules[y][x])

    def test_svg(self):
        svg = QrCode("abc").to_svg()
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("<path", svg)


class IconTest(unittest.TestCase):
    def test_png(self):
        data = icons.png(32)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", data)
        self.assertIn(b"IEND", data)


if __name__ == "__main__":
    unittest.main()
