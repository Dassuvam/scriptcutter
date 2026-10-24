#!/usr/bin/env python3
"""
Draw the app icon (the logo in web/static/img/logo.svg) and save it as
assets/ScriptCutter.icns (macOS app) and assets/ScriptCutter.ico (Windows shortcuts).
Standard library only; run it again after changing the logo:

    python3 tools/make_icons.py
"""
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

# the logo on its 64-unit canvas (same as logo.svg)
CREAM, RED, INK = (0xF4, 0xEF, 0xE4), (0xD0, 0x40, 0x2C), (0x24, 0x22, 0x1F)
CORNER, CIRCLE, LINE, LINE_WIDTH = 14.0, (32.0, 32.0, 18.0), ((12.0, 46.0), (52.0, 18.0)), 4.0


def _coverage(distance: float, pixel: float) -> float:
    """Anti-aliased coverage from a signed distance (negative = inside), in logo units."""
    return min(1.0, max(0.0, 0.5 - distance / pixel))


def _rounded_square(x: float, y: float) -> float:
    qx, qy = abs(x - 32) - (32 - CORNER), abs(y - 32) - (32 - CORNER)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - CORNER


def _segment(x: float, y: float) -> float:
    (ax, ay), (bx, by) = LINE
    t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
    return math.hypot(x - ax - t * (bx - ax), y - ay - t * (by - ay)) - LINE_WIDTH / 2


def render(size: int, margin: float) -> bytes:
    """RGBA rows of the icon at size x size pixels; margin = empty share on each side."""
    inner = size * (1 - 2 * margin)
    unit = 64.0 / inner  # logo units per pixel
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            x = (px + 0.5 - size * margin) * unit
            y = (py + 0.5 - size * margin) * unit
            alpha = _coverage(_rounded_square(x, y), unit)
            color = CREAM
            for paint, distance in ((RED, math.hypot(x - CIRCLE[0], y - CIRCLE[1]) - CIRCLE[2]),
                                    (INK, _segment(x, y))):
                c = _coverage(distance, unit)
                color = tuple(round(a * (1 - c) + b * c) for a, b in zip(color, paint))
            row += bytes(color) + bytes([round(alpha * 255)])
        rows.append(bytes(row))
    return b"".join(rows)


def png(size: int, margin: float) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(render(size, margin), 9))
            + chunk(b"IEND", b""))


def icns(pngs: dict) -> bytes:
    """macOS icon: PNG entries by type (16 to 1024 pixels, plain and @2x)."""
    types = [(b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
             (b"ic10", 1024), (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512)]
    body = b"".join(kind + struct.pack(">I", 8 + len(pngs[size])) + pngs[size] for kind, size in types)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def ico(pngs: dict) -> bytes:
    """Windows icon: PNG entries (Windows Vista and newer read these)."""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    offset = 6 + 16 * len(sizes)
    head, data = struct.pack("<HHH", 0, 1, len(sizes)), b""
    for s in sizes:
        head += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(pngs[s]), offset + len(data))
        data += pngs[s]
    return head + data


def main():
    os.makedirs(ASSETS, exist_ok=True)
    mac = {s: png(s, 0.09) for s in (16, 32, 64, 128, 256, 512, 1024)}  # macOS icons leave a margin
    win = {s: png(s, 0.02) for s in (16, 24, 32, 48, 64, 128, 256)}
    with open(os.path.join(ASSETS, "ScriptCutter.icns"), "wb") as f:
        f.write(icns(mac))
    with open(os.path.join(ASSETS, "ScriptCutter.ico"), "wb") as f:
        f.write(ico(win))
    with open(os.path.join(ASSETS, "ScriptCutter.png"), "wb") as f:
        f.write(mac[256])
    print("Wrote assets/ScriptCutter.icns, .ico and .png")


if __name__ == "__main__":
    main()
