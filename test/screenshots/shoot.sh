#!/bin/bash
# shoot.sh MENU.n64 OUTPREFIX [GOPHER64]: boots the menu in gopher64 with
# the demo SD card (make_sd.py) and screenshots the file browser, the demo
# game's screen and its options. Run from this folder after
#   python3 make_sd.py ..    (needs Pillow, mtools, dosfstools, Xvfb,
#                              xdotool and ImageMagick's import)
set -e
MENU=$1; OUT=$2; GOPHER=${3:-gopher64}
H=$(sha256sum "$MENU" | cut -c1-64 | tr a-f A-F)
IMG=~/.local/share/gopher64/saves/N64FlashcartMenu-$H.img
rm -f sd.img && mkfs.fat -C -F 32 -n NO_NAME sd.img $((0x4000000 / 1024)) >/dev/null
MTOOLS_SKIP_CHECK=1 mcopy -s -i sd.img sd/* ::/
cp sd.img "$IMG"
export DISPLAY=:89 SDL_AUDIODRIVER=dummy XDG_RUNTIME_DIR=/tmp/xdg VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.json
Xvfb :89 -screen 0 1024x768x24 >/dev/null 2>&1 & XV=$!
sleep 1
"$GOPHER" "$MENU" >gopher64.log 2>&1 & G=$!
sleep 10
WIN=$(xdotool search --name gopher64 | head -1 || true)
[ -n "$WIN" ] && xdotool windowfocus "$WIN" 2>/dev/null || true
press() { xdotool keydown "$1"; sleep 0.15; xdotool keyup "$1"; sleep 0.5; }
shot() { import -display :89 -window root -crop 625x480+200+144 +repage "$1"; }
shot ${OUT}-0.png
for i in 1 2 3 4 5; do press s; done
sleep 1; shot ${OUT}-browser.png
press Shift_L; sleep 4; shot ${OUT}-info.png
press c; sleep 2; press s; press s; press s; sleep 1; shot ${OUT}-options.png
kill $G; sleep 1; kill $XV
