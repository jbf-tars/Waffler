#!/usr/bin/env python3
"""Generate the Waffler DMG drag-to-install background.

Produces installer/mac/dmg-background.png at 2x (1320x880) for a Retina-crisp
Finder window whose logical content area is 660x440 points. The macOS release
workflow copies this committed PNG into the DMG's hidden .background folder and
sets it as the volume background via AppleScript.

Layout (logical points, origin top-left — matches Finder icon coords):
  - App icon sits at  (165, 250)
  - Applications sits at (495, 250)
  - Title text centred near the top
  - A hand-drawn-style curved arrow sweeps from above the app toward
    Applications

Run:  python installer/mac/make_dmg_background.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCALE = 2
W, H = 660 * SCALE, 440 * SCALE

# Warm cream palette that fits the waffle/syrup brand without being loud.
BG_TOP = (245, 241, 232)      # #F5F1E8
BG_BOTTOM = (236, 228, 213)   # #ECE4D5
LINEART = (214, 203, 182)     # subtle decorative curves
TITLE_CLR = (74, 55, 30)      # dark syrup brown
ARROW_CLR = (150, 120, 48)    # waffle-gold-brown


def _font(candidates: list[tuple[str, int]]):
    for path, size in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _vertical_gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    base = Image.new("RGB", (w, h), top)
    px = base.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return base


def _quad_bezier(p0, p1, p2, n=120):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
        y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def main():
    out = Path(__file__).parent / "dmg-background.png"
    img = _vertical_gradient(W, H, BG_TOP, BG_BOTTOM)
    d = ImageDraw.Draw(img, "RGBA")

    s = SCALE

    # ── Decorative line-art sweeps (very subtle, Granola-style) ──
    for cfg in [
        ((-40, 120), (330, 40), (720, 150)),
        ((-40, 360), (300, 470), (720, 330)),
        ((120, -30), (260, 220), (120, 470)),
    ]:
        p0 = (cfg[0][0] * s, cfg[0][1] * s)
        p1 = (cfg[1][0] * s, cfg[1][1] * s)
        p2 = (cfg[2][0] * s, cfg[2][1] * s)
        pts = _quad_bezier(p0, p1, p2)
        d.line(pts, fill=LINEART + (90,), width=max(1, s), joint="curve")

    # ── Title text ──
    title_font = _font([
        ("/System/Library/Fonts/Supplemental/Georgia.ttf", 30 * s),
        ("/Library/Fonts/Georgia.ttf", 30 * s),
        ("/System/Library/Fonts/SFNS.ttf", 30 * s),
    ])
    line1 = "To install, drag Waffler"
    line2 = "to Applications"
    for i, line in enumerate((line1, line2)):
        bbox = d.textbbox((0, 0), line, font=title_font)
        tw = bbox[2] - bbox[0]
        x = (W - tw) / 2
        y = 44 * s + i * 40 * s
        d.text((x, y), line, font=title_font, fill=TITLE_CLR)

    # ── Curved arrow from above the app icon toward Applications ──
    # App icon centre (165,250); Applications (495,250). Arc the arrow
    # through the gap, ending just left of the Applications folder.
    start = (250 * s, 200 * s)
    ctrl = (360 * s, 150 * s)
    end = (430 * s, 205 * s)
    arc = _quad_bezier(start, ctrl, end, n=80)
    d.line(arc, fill=ARROW_CLR + (235,), width=4 * s, joint="curve")

    # Arrowhead — orient along the final segment of the arc.
    ax, ay = arc[-1]
    bx, by = arc[-6]
    ang = math.atan2(ay - by, ax - bx)
    head = 16 * s
    spread = math.radians(26)
    p_a = (ax - head * math.cos(ang - spread), ay - head * math.sin(ang - spread))
    p_b = (ax - head * math.cos(ang + spread), ay - head * math.sin(ang + spread))
    d.polygon([(ax, ay), p_a, p_b], fill=ARROW_CLR + (235,))

    img.save(out, "PNG")
    print(f"wrote {out} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
