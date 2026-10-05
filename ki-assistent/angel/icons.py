"""App-Symbol (Engel mit Heiligenschein) – als SVG und als PNG, ohne Zusatzpakete erzeugt."""

from __future__ import annotations

import math
import struct
import zlib
from functools import lru_cache

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#4f46e5"/><stop offset="1" stop-color="#a855f7"/></linearGradient></defs>
<rect width="100" height="100" rx="22" fill="url(#g)"/>
<ellipse cx="30" cy="66" rx="19" ry="9" transform="rotate(-28 30 66)" fill="#fff" fill-opacity=".75"/>
<ellipse cx="70" cy="66" rx="19" ry="9" transform="rotate(28 70 66)" fill="#fff" fill-opacity=".75"/>
<ellipse cx="50" cy="92" rx="24" ry="20" fill="#fff"/>
<circle cx="50" cy="52" r="13" fill="#fff"/>
<ellipse cx="50" cy="28" rx="19" ry="6.5" fill="none" stroke="#fcd34d" stroke-width="3.6"/>
</svg>"""


def _ellipse_dist(px, py, cx, cy, rx, ry, angle=0.0):
    """Ungefährer Abstand zum Ellipsenrand (negativ = innen), in denselben Einheiten wie die Radien."""
    dx, dy = px - cx, py - cy
    if angle:
        c, s = math.cos(-angle), math.sin(-angle)
        dx, dy = dx * c - dy * s, dx * s + dy * c
    k = math.hypot(dx / rx, dy / ry)
    grad = math.hypot(dx / (rx * rx), dy / (ry * ry))
    if grad == 0:
        return -min(rx, ry)
    return (k - 1) * k / grad  # Näherung erster Ordnung


@lru_cache(maxsize=4)
def png(size: int) -> bytes:
    """Erzeugt das Symbol als PNG (z. B. 180, 192 oder 512 Pixel)."""
    px = 100.0 / size  # Größe eines Pixels in Symbol-Einheiten (0..100)
    top, bottom = (0x4f, 0x46, 0xe5), (0xa8, 0x55, 0xf7)
    gold = (0xfc, 0xd3, 0x4d)
    rows = []
    for j in range(size):
        y = (j + 0.5) * px
        t = j / max(1, size - 1)
        bg = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        row = bytearray()
        for i in range(size):
            x = (i + 0.5) * px
            r, g, b = bg
            layers = (
                (_ellipse_dist(x, y, 30, 66, 19, 9, math.radians(-28)), (255, 255, 255), 0.75),
                (_ellipse_dist(x, y, 70, 66, 19, 9, math.radians(28)), (255, 255, 255), 0.75),
                (_ellipse_dist(x, y, 50, 92, 24, 20), (255, 255, 255), 1.0),
                (math.hypot(x - 50, y - 52) - 13, (255, 255, 255), 1.0),
                (abs(_ellipse_dist(x, y, 50, 28, 19, 6.5)) - 1.8, gold, 1.0),
            )
            for dist, color, alpha in layers:
                cover = min(1.0, max(0.0, 0.5 - dist / px)) * alpha
                if cover:
                    r = r + (color[0] - r) * cover
                    g = g + (color[1] - g) * cover
                    b = b + (color[2] - b) * cover
            row += bytes((round(r), round(g), round(b)))
        rows.append(bytes(row))
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
