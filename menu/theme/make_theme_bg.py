#!/usr/bin/env python3
"""Backgrounds for the n64ra menu themes (640x480): Trophy Night and Gold
Rush. The menu draws its own panels over
them, so they stay quiet: a colour field, rings or rays, and a faint
trophy. Run: python3 make_theme_bg.py OUT_DIR"""
import math
import os
import sys
from PIL import Image, ImageDraw, ImageFilter

SS = 4
W, H = 640, 480


def s(*v):
    return [x * SS for x in v]


def trophy(d, cx, cy, size, c):
    """A generic cup: bowl, handles, stem, base. Not the RA logo."""
    k = size / 32.0
    P = lambda x, y: (SS * (cx + (x - 16) * k), SS * (cy + (y - 16) * k))
    d.polygon([P(8, 4), P(24, 4), P(23, 13), P(19, 18), P(13, 18), P(9, 13)], fill=c)
    w = max(2, int(2.6 * k)) * SS
    d.arc([*P(2, 5), *P(11, 14)], 90, 270, fill=c, width=w)
    d.arc([*P(21, 5), *P(30, 14)], 270, 90, fill=c, width=w)
    d.rectangle([*P(14.5, 18), *P(17.5, 23)], fill=c)
    d.rectangle([*P(10, 23), *P(22, 26)], fill=c)
    d.rectangle([*P(8, 26), *P(24, 29)], fill=c)


def gradient(top, bottom):
    im = Image.new("RGB", (W * SS, H * SS))
    d = ImageDraw.Draw(im)
    for y in range(H * SS):
        t = y / (H * SS - 1)
        d.line([(0, y), (W * SS, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return im


def trophy_night():
    BG_TOP, BG_BOT, RING, GOLD = (17, 26, 50), (9, 13, 26), (34, 48, 86), (242, 179, 61)
    im = gradient(BG_TOP, BG_BOT).convert("RGBA")
    d = ImageDraw.Draw(im)
    for r in (420, 330, 250):                       # rings from the top right corner
        d.ellipse(s(660 - r, -80 - r, 660 + r, -80 + r), outline=RING + (255,), width=2 * SS)
    d = ImageDraw.Draw(im)
    trophy(d, 520, 350, 190, (60, 54, 52, 255))      # watermark: dark bronze on navy
    for i in range(26):                              # a few gold sparkles
        x = (i * 97 + 31) % 600 + 20
        y = (i * 61 + 17) % 440 + 20
        r = 1.2 if i % 3 else 2.0
        d.ellipse(s(x - r, y - r, x + r, y + r), fill=GOLD + (110 if i % 3 else 170,))
    return im.resize((W, H), Image.LANCZOS).convert("RGB")


def gold_rush():
    BG, RAY, GOLD, SOFT = (247, 241, 228), (240, 228, 200), (214, 150, 28), (232, 214, 172)
    im = Image.new("RGBA", (W * SS, H * SS), BG + (255,))
    d = ImageDraw.Draw(im)
    cx, cy = 640, 480                                # sunburst from the bottom right corner
    for i in range(13):
        a0 = 180 + i * 7
        d.pieslice(s(cx - 900, cy - 900, cx + 900, cy + 900), a0, a0 + 3.4, fill=RAY + (255,))
    mask = Image.new("L", im.size, 0)                # fade the rays out at the centre
    ImageDraw.Draw(mask).ellipse(s(cx - 190, cy - 190, cx + 190, cy + 190), fill=255)
    im.paste(BG + (255,), (0, 0), mask.filter(ImageFilter.GaussianBlur(30 * SS)))
    d = ImageDraw.Draw(im)
    trophy(d, 540, 360, 170, SOFT + (255,))          # watermark: pale gold on cream
    d.rectangle(s(0, 476, 640, 480), fill=GOLD + (255,))
    for i in range(18):
        x = (i * 113 + 47) % 600 + 20
        y = (i * 71 + 29) % 300 + 20
        d.ellipse(s(x - 1.4, y - 1.4, x + 1.4, y + 1.4), fill=GOLD + (120,))
    return im.resize((W, H), Image.LANCZOS).convert("RGB")


def main(out):
    os.makedirs(out, exist_ok=True)
    for name, fn in (("trophynight", trophy_night), ("goldrush", gold_rush)):
        fn().save(os.path.join(out, f"theme_{name}.png"))
        print("wrote", f"theme_{name}.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
