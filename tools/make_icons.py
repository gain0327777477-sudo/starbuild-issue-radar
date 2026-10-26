"""홈 화면 아이콘(PNG) 만들기 — 별도 프로그램 없이 파이썬만으로 그림.

실행:  python tools/make_icons.py   →  web/icons/icon-192.png, icon-512.png, maskable-512.png, favicon.svg
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
BG = (20, 33, 61)          # 남색
RING = (64, 96, 150)
ACCENT = (255, 183, 3)     # 호박색
WHITE = (240, 244, 250)


def png(path: Path, size: int, pixels) -> None:
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def draw(size: int, safe: float) -> list:
    """safe: 그림이 들어갈 비율 (maskable 은 가장자리가 잘려도 되게 0.7)"""
    c = size / 2
    r_max = size * safe / 2
    rows = []
    sweep = math.radians(-50)
    for y in range(size):
        row = []
        for x in range(size):
            dx, dy = x + 0.5 - c, y + 0.5 - c
            d = math.hypot(dx, dy)
            col = BG
            # 고리 3개
            for k in (0.38, 0.66, 0.94):
                if abs(d - r_max * k) < size * 0.012:
                    col = RING
            # 레이더 부채꼴(최근 30도)
            ang = math.atan2(dy, dx)
            diff = (ang - sweep) % (2 * math.pi)
            if d < r_max * 0.94 and diff > 2 * math.pi - math.radians(35):
                t = 1 - (2 * math.pi - diff) / math.radians(35)
                col = mix(col, ACCENT, 0.15 + 0.45 * t)
            # 바늘
            px, py = math.cos(sweep), math.sin(sweep)
            along = dx * px + dy * py
            perp = abs(-dx * py + dy * px)
            if 0 < along < r_max * 0.94 and perp < size * 0.012:
                col = ACCENT
            # 점(이슈)
            for (ax, ay, ar) in ((0.42, -0.30, 0.055), (-0.35, 0.22, 0.04), (0.12, 0.48, 0.035)):
                if math.hypot(dx - ax * r_max, dy - ay * r_max) < size * ar:
                    col = WHITE if ar < 0.05 else ACCENT
            if d < size * 0.03:
                col = WHITE
            row.append(col)
        rows.append(row)
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    png(OUT / "icon-192.png", 192, draw(192, 0.9))
    png(OUT / "icon-512.png", 512, draw(512, 0.9))
    png(OUT / "maskable-512.png", 512, draw(512, 0.68))
    (OUT / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#14213d"/>'
        '<circle cx="32" cy="32" r="22" fill="none" stroke="#406096" stroke-width="2.5"/>'
        '<circle cx="32" cy="32" r="12" fill="none" stroke="#406096" stroke-width="2.5"/>'
        '<line x1="32" y1="32" x2="48" y2="16" stroke="#ffb703" stroke-width="3.5" stroke-linecap="round"/>'
        '<circle cx="44" cy="24" r="3.5" fill="#f0f4fa"/></svg>', encoding="utf-8")
    print("아이콘을 만들었습니다:", ", ".join(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
