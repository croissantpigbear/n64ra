# Builds the SD card contents for the README screenshots: a demo cartridge
# (the n64ra test ROM with its title changed) with RetroAchievements on, its
# own box art and description, and a few file names for the browser.
import os, shutil, sys
from PIL import Image, ImageDraw, ImageFont
T = sys.argv[1] if len(sys.argv) > 1 else ".."  # n64ra/test
sd = "sd"
os.makedirs(f"{sd}/menu", exist_ok=True)
rom = bytearray(open(os.path.join(T, "launcher", "n64ra_test.z64"), "rb").read())
rom[0x20:0x34] = b"N64RA ACHIEVEMENT 64"
open(f"{sd}/N64RA Demo.z64", "wb").write(rom)
open(f"{sd}/N64RA Demo.ini", "w").write("achievements_enabled=true\n")
open(f"{sd}/N64RA Demo.metadata.ini", "w").write("""[meta]
name = N64RA Demo
author = n64ra
release-date = 2026-11-19
num-players = 1
short-desc = A demo cartridge for n64ra. With Achievements On, the agent sends the game's memory to the bridge every frame.
""")
for n in ["1080 Snowboarding (USA).z64", "Banjo-Kazooie (USA).z64", "Conker's Bad Fur Day (USA).z64",
          "GoldenEye 007 (USA).z64", "Mario Kart 64 (USA).z64", "Paper Mario (USA).z64",
          "Super Mario 64 (USA).z64", "The Legend of Zelda - Ocarina of Time (USA).z64"]:
    open(f"{sd}/{n}", "wb").write(bytes(rom[:0x101000]))
open(f"{sd}/menu/config.ini", "w").write("[menu]\nfirst_run = false\nshow_rom_configuration_files = false\n")
# box art, 158x112: a navy box with a gold trophy and the name
W, H = 158, 112
im = Image.new("RGB", (W, H), (20, 30, 62))
d = ImageDraw.Draw(im)
for y in range(H):
    d.line((0, y, W, y), fill=(20 + y // 8, 30 + y // 12, 70 - y // 6))
d.rectangle((0, 0, W - 1, H - 1), outline=(242, 196, 72), width=2)
gold = (242, 196, 72)
cx, top = 40, 24
d.chord((cx - 18, top - 16, cx + 18, top + 26), 0, 180, fill=gold)
d.arc((cx - 28, top + 2, cx - 10, top + 18), 90, 270, fill=gold, width=3)
d.arc((cx + 10, top + 2, cx + 28, top + 18), 270, 90, fill=gold, width=3)
d.rectangle((cx - 3, top + 24, cx + 3, top + 34), fill=(196, 142, 34))
d.rectangle((cx - 12, top + 34, cx + 12, top + 39), fill=gold)
f1 = ImageFont.truetype("/usr/share/fonts/opentype/inter/Inter-Black.otf", 22)
f2 = ImageFont.truetype("/usr/share/fonts/opentype/inter/Inter-Bold.otf", 10)
d.text((66, 14), "N64RA", font=f1, fill=(236, 241, 255))
d.text((67, 40), "ACHIEVEMENT", font=f2, fill=gold)
d.text((67, 52), "DEMO 64", font=f2, fill=gold)
d.text((10, 80), "RetroAchievements on a", font=f2, fill=(200, 210, 235))
d.text((10, 93), "real Nintendo 64", font=f2, fill=(200, 210, 235))
os.makedirs(f"{sd}/menu/metadata/homebrew/N64RA ACHIEVEMENT 64", exist_ok=True)
im.save(f"{sd}/menu/metadata/homebrew/N64RA ACHIEVEMENT 64/boxart_front.png")
print("ok")
