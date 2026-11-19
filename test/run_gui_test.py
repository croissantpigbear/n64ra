#!/usr/bin/env python3
"""Runs the N64RA Bridge window (app/n64ra_gui.pyw) against gopher64's
emulated SC64 and a mock xeRAbora, then screenshots it.

usage: run_gui_test.py GOPHER64 PYTHON_WITH_TK_AND_PYSERIAL [SECONDS] [OUT.png]
needs xvfb-run, Xvfb and ImageMagick's import"""
import os, socket, struct, subprocess, sys, threading, time
T = os.path.dirname(os.path.abspath(__file__))
GOPHER, PY = sys.argv[1], sys.argv[2]
SECS = float(sys.argv[3]) if len(sys.argv) > 3 else 25
OUT = sys.argv[4] if len(sys.argv) > 4 else os.path.join(T, "out", "gui.png")
rom = os.path.join(T, "launcher", "n64ra_test.z64")
entries = [(0x100000, 4), (0x100000, 1), (0x100020, 4)]
wfile = struct.pack("<4I", 0x4C574152, 1, len(entries), sum(s for _, s in entries))
wfile += b"".join(struct.pack("<I", (s << 28) | a) for a, s in entries)
def xera():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 18194)); n = 0
    while True:
        d, a = s.recvfrom(4096)
        if d.startswith(b"RAP1"): s.sendto(b"RAO1 OK mock/1", a)
        elif d.startswith(b"RAQ1"): s.sendto(f"RAA1 OK {len(wfile)} 1 5 0 0 N64RA Test Game".encode(), a)
        elif d.startswith(b"RAG1"): s.sendto(f"RAC1 0 {len(wfile)} ".encode() + wfile, a)
        elif d.startswith(b"RA15"):
            n += 1
            if n == 400: s.sendto(b"RAU1 12345 1", a); s.sendto(b"RAU1 12345 1", a)
threading.Thread(target=xera, daemon=True).start()
env = dict(os.environ, SDL_AUDIODRIVER="dummy", XDG_RUNTIME_DIR="/tmp/xdg", VK_ICD_FILENAMES="/usr/share/vulkan/icd.d/lvp_icd.json")
emu = subprocess.Popen(["xvfb-run", "-a", GOPHER, rom], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
time.sleep(3)
disp = subprocess.Popen(["Xvfb", ":77", "-screen", "0", "900x900x24"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
genv = dict(os.environ, DISPLAY=":77", HOME=os.path.join(T, "out", "guihome"),
            N64RA_BRIDGE_ARGS=f"--gopher64 --rom {rom} --pc 127.0.0.1 --ip 127.0.0.1")
os.makedirs(genv["HOME"], exist_ok=True)
gui = subprocess.Popen([PY, os.path.join(T, "..", "app", "n64ra_gui.pyw"), "--autostart"], env=genv)
time.sleep(SECS)
subprocess.run(["import", "-display", ":77", "-window", "root", OUT])
print(OUT)
gui.terminate(); disp.terminate(); os.killpg(emu.pid, 15)
