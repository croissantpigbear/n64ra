"""Draws the N64RA Bridge icon (Trophy Night colours): icon.png and AppIcon.icns."""
import os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 1024
here = os.path.dirname(os.path.abspath(__file__))
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# macOS icon grid: 824 px squircle-ish rounded square centred, with a soft shadow
m, r = 100, 185
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(shadow).rounded_rectangle((m, m + 14, S - m, S - m + 14), r, fill=(0, 0, 0, 110))
img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
g = ImageDraw.Draw(bg)
for y in range(m, S - m):  # navy vertical gradient
    t = (y - m) / (S - 2 * m)
    g.line((m, y, S - m, y), fill=(int(18 + 10 * t), int(28 + 6 * t), int(64 - 18 * t), 255))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((m, m, S - m, S - m), r, fill=255)
img.paste(bg, (0, 0), mask)
d = ImageDraw.Draw(img)
d.rounded_rectangle((m + 6, m + 6, S - m - 6, S - m - 6), r - 6, outline=(242, 204, 96, 90), width=6)

gold, deep = (242, 196, 72, 255), (196, 142, 34, 255)
cx = S // 2
# trophy cup
top, cup_w, cup_h = 230, 330, 270
d.chord((cx - cup_w // 2, top - cup_h, cx + cup_w // 2, top + cup_h), 0, 180, fill=gold)
d.rectangle((cx - cup_w // 2, top - 10, cx + cup_w // 2, top + 4), fill=gold)
# handles
for sgn in (-1, 1):
    x0 = cx + sgn * (cup_w // 2) - (70 if sgn > 0 else -70)
    box = (min(x0, x0 + sgn * 140), top + 10, max(x0, x0 + sgn * 140), top + 170)
    d.arc(box, 270 if sgn > 0 else 90, 90 if sgn > 0 else 270, fill=gold, width=30)
# shine
d.chord((cx - cup_w // 2 + 40, top + 20, cx - 40, top + cup_h - 60), 100, 200, fill=(255, 236, 170, 255))
# stem and base
d.rectangle((cx - 32, top + cup_h - 10, cx + 32, top + cup_h + 70), fill=deep)
d.rounded_rectangle((cx - 120, top + cup_h + 60, cx + 120, top + cup_h + 110), 14, fill=gold)
# "64" on a plate
font = ImageFont.truetype("/usr/share/fonts/opentype/inter/Inter-Black.otf", 200)
txt = "64"
w = d.textlength(txt, font=font)
d.text((cx - w / 2 + 6, 652 + 6), txt, font=font, fill=(0, 0, 0, 120))
d.text((cx - w / 2, 652), txt, font=font, fill=(236, 241, 255, 255))

img.save(os.path.join(here, "icon.png"))
small = img.resize((256, 256), Image.LANCZOS)
small.save(os.path.join(here, "icon-256.png"))
img.save(os.path.join(here, "macos", "AppIcon.icns"), sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
print("ok")
