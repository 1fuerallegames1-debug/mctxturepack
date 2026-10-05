"""Kleiner QR-Code-Generator (ohne Zusatzpakete) – für die Handy-Kopplung.

Unterstützt Byte-Modus, Fehlerkorrektur-Stufe M und die Versionen 1–10 (bis 213 Bytes),
das reicht locker für eine Adresse wie http://192.168.178.20:8765/#token=...
Aufbau nach ISO/IEC 18004, angelehnt an Project Nayukis "QR Code generator library" (MIT).
"""

from __future__ import annotations

# Fehlerkorrektur-Stufe M, Index = Version
_ECC_PER_BLOCK = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]
_NUM_BLOCKS = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]
_FORMAT_BITS_M = 0
MAX_VERSION = 10


def _raw_data_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_data_modules(ver) // 8 - _ECC_PER_BLOCK[ver] * _NUM_BLOCKS[ver]


def _rs_multiply(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _rs_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _rs_multiply(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _rs_multiply(coef, factor)
    return result


def _add_ecc_and_interleave(data: list[int], ver: int) -> list[int]:
    numblocks = _NUM_BLOCKS[ver]
    blockecclen = _ECC_PER_BLOCK[ver]
    rawcodewords = _raw_data_modules(ver) // 8
    numshort = numblocks - rawcodewords % numblocks
    shortlen = rawcodewords // numblocks
    divisor = _rs_divisor(blockecclen)
    blocks, k = [], 0
    for i in range(numblocks):
        dat = data[k: k + shortlen - blockecclen + (0 if i < numshort else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, divisor)
        if i < numshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    result = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != shortlen - blockecclen or j >= numshort:
                result.append(blk[i])
    return result


class QrCode:
    def __init__(self, text: str, mask: int | None = None):
        data = text.encode("utf-8")
        for ver in range(1, MAX_VERSION + 1):
            count_bits = 8 if ver <= 9 else 16
            if 4 + count_bits + 8 * len(data) <= _data_codewords(ver) * 8:
                break
        else:
            raise ValueError("Text ist zu lang für den QR-Code.")
        self.version = ver
        self.size = ver * 4 + 17

        # Bitstrom: Modus (Byte), Länge, Daten, Abschluss, Füllbytes
        bits: list[int] = []

        def append(val: int, n: int):
            bits.extend((val >> i) & 1 for i in reversed(range(n)))

        append(0x4, 4)
        append(len(data), count_bits)
        for b in data:
            append(b, 8)
        capacity = _data_codewords(ver) * 8
        append(0, min(4, capacity - len(bits)))
        append(0, -len(bits) % 8)
        pad = 0xEC
        while len(bits) < capacity:
            append(pad, 8)
            pad ^= 0xEC ^ 0x11
        codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

        self.modules = [[False] * self.size for _ in range(self.size)]
        self._function = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()
        self._draw_codewords(_add_ecc_and_interleave(codewords, ver))

        if mask is None:
            best = None
            for m in range(8):
                self._apply_mask(m)
                self._draw_format_bits(m)
                score = self._penalty()
                if best is None or score < best[0]:
                    best = (score, m)
                self._apply_mask(m)  # XOR macht die Maske wieder rückgängig
            mask = best[1]
        self.mask = mask
        self._apply_mask(mask)
        self._draw_format_bits(mask)

    # ------------------------------------------------------------ Zeichnen

    def _set(self, x: int, y: int, dark: bool):
        self.modules[y][x] = dark
        self._function[y][x] = True

    def _draw_function_patterns(self):
        size = self.size
        for i in range(size):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = self._alignment_positions()
        n = len(pos)
        for i in range(n):
            for j in range(n):
                if (i == 0 and j == 0) or (i == 0 and j == n - 1) or (i == n - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(pos[i] + dx, pos[j] + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format_bits(0)  # Platz reservieren, wird später überschrieben
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.version << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self._set(a, b, bit)
                self._set(b, a, bit)

    def _alignment_positions(self) -> list[int]:
        ver = self.version
        if ver == 1:
            return []
        numalign = ver // 7 + 2
        step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
        result = [self.size - 7 - i * step for i in range(numalign - 1)] + [6]
        return list(reversed(result))

    def _draw_format_bits(self, mask: int):
        data = _FORMAT_BITS_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i):
            return (bits >> i) & 1 == 1

        for i in range(6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(8):
            self._set(self.size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, self.size - 15 + i, bit(i))
        self._set(8, self.size - 8, True)

    def _draw_codewords(self, data: list[int]):
        i, size = 0, self.size
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self._function[y][x] and i < len(data) * 8:
                        self.modules[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int):
        tests = (
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )
        test = tests[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self._function[y][x] and test(x, y):
                    self.modules[y][x] = not self.modules[y][x]

    def _penalty(self) -> int:
        """Vereinfachte Bewertung (Regeln 1, 2 und 4) – jede Maske ergibt einen gültigen Code."""
        size, m = self.size, self.modules
        score = 0
        for line in list(m) + [list(col) for col in zip(*m)]:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, prev = 1, v
            if run >= 5:
                score += run - 2
        for y in range(size - 1):
            for x in range(size - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(row.count(True) for row in m)
        total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        return score + max(0, k) * 10

    # ------------------------------------------------------------ Ausgabe

    def to_svg(self, border: int = 4, scale: int = 8) -> str:
        dim = (self.size + border * 2) * scale
        parts = []
        for y in range(self.size):
            for x in range(self.size):
                if self.modules[y][x]:
                    parts.append(f"M{x + border},{y + border}h1v1h-1z")
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.size + border * 2} {self.size + border * 2}" '
                f'width="{dim}" height="{dim}" shape-rendering="crispEdges">'
                f'<rect width="100%" height="100%" fill="#fff"/><path d="{"".join(parts)}" fill="#000"/></svg>')

    def to_terminal(self, border: int = 2) -> str:
        """QR-Code aus Halbblock-Zeichen. Helle Module werden gezeichnet, dunkle bleiben leer."""
        n = self.size + border * 2

        def light(x, y):
            x -= border
            y -= border
            if 0 <= x < self.size and 0 <= y < self.size:
                return not self.modules[y][x]
            return True

        lines = []
        for y in range(0, n, 2):
            row = []
            for x in range(n):
                top, bottom = light(x, y), (light(x, y + 1) if y + 1 < n else False)
                row.append("█" if top and bottom else "▀" if top else "▄" if bottom else " ")
            lines.append("".join(row))
        return "\n".join(lines)
