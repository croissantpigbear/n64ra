#!/usr/bin/env python3
"""End-to-end test of n64ra in the gopher64 emulator (which emulates the
SummerCart64, USB included, on TCP port 48646).

    test ROM (launcher + agent + test game) in gopher64
      --SC64 USB--> bridge/n64ra_bridge.py --UDP--> mock xeRAbora (here)

Checks: the agent hooks the game's exception handler and says hello;
the bridge hashes the ROM as rcheevos does; the watch list reaches the
agent; one snapshot per frame (the game's counter goes up by one, none
missed or doubled); every value and pointer chain right, read the way
rcheevos sees N64 memory.

usage: run_test.py GOPHER64 [SECONDS]
"""
import os, socket, struct, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
gopher = sys.argv[1]
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 40
rom = os.path.join(HERE, "launcher", "n64ra_test.z64")
work = os.path.join(HERE, "out")
os.makedirs(work, exist_ok=True)

# The watch list, as xeRAbora would send it (rcheevos addresses).
entries = [(0x100000, 4), (0x100000, 1), (0x100020, 4), (0x100024, 4), (0x100028, 4), (0x10002C, 4),
           (0x100010, 4), (0x100041, 2), (0x500000, 4), (0x900000, 4), (0x100002, 2),
           (0x100050, 4), (0x100054, 4), (0x100058, 4)]   # the game's DMAs: done, wrong, PI interrupts
# A big set: the list comes down in several pieces, each snapshot goes up
# in more than one packet. These read the table: value frames + (i & 3).
EXTRA = int(os.environ.get("N64RA_TEST_EXTRA", 1600))
entries += [(0x100020 + 4 * (i & 3), 4) for i in range(EXTRA)]
npack = lambda parent, from_node, size, shift, width: (width << 21) | (shift << 16) | (size << 13) | (from_node << 12) | parent
nodes = [(npack(6, 0, 4, 0, 23), 4, 1),          # *(ptr & 7FFFFF) + 4    -> frames * 3
         (npack(1, 0, 4, 0, 2), 0x100020, 4),    # table[frames & 3]      -> frames + (frames & 3)
         (npack(0, 1, 2, 0, 23), 0, 1)]          # node 0's value as a pointer
wfile = struct.pack("<4I", 0x4C574152, 1, len(entries), sum(s for _, s in entries))
wfile += b"".join(struct.pack("<I", (s << 28) | a) for a, s in entries)
wfile += struct.pack("<2I", 0x324E4152, len(nodes)) + b"".join(struct.pack("<3I", *n) for n in nodes)
layout = [("frames", 4), ("f8", 1), ("r0", 4), ("r1", 4), ("r2", 4), ("r3", 4), ("ptr", 4), ("u16", 2),
          ("exp", 4), ("oob", 4), ("hi16", 2), ("dmas", 4), ("baddma", 4), ("piirq", 4), ("extra", 4 * EXTRA), ("n0a", 4), ("n0v", 4), ("n1a", 4), ("n1v", 4), ("n2a", 4), ("n2v", 4)]

# ---- mock xeRAbora --------------------------------------------------------
asked, snaps, stop, cur = [], [], threading.Event(), []


def xerabora():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 18194))
    s.settimeout(0.5)
    CH = 896
    while not stop.is_set():
        try:
            d, a = s.recvfrom(4096)
        except socket.timeout:
            continue
        if d.startswith(b"RAP1"):
            s.sendto(b"RAO1 OK mock/1", a)
        elif d.startswith(b"RAQ1"):
            asked.append(d.split()[1].decode())
            s.sendto(f"RAA1 OK {len(wfile)} {(len(wfile) + CH - 1) // CH} 5 0 0 n64ra test".encode(), a)
        elif d.startswith(b"RAG1"):
            i = int(d.split()[2])
            part = wfile[i * CH:(i + 1) * CH]
            s.sendto(f"RAC1 {i} {len(part)} ".encode() + part, a)
        elif d.startswith(b"RA15"):
            # a snapshot bigger than one packet comes as parts pt of np, same sq
            i = d.index(b" id=") + 4 + 15 + 1
            field = lambda name: int(d.split(b" " + name + b"=")[1].split(b" ")[0])
            sq, pt, np_ = field(b"sq"), field(b"pt"), field(b"np")
            if pt == 0:
                cur[:] = [sq, {}]
            if cur and cur[0] == sq:
                cur[1][pt] = d[i:]
                if len(cur[1]) == np_:
                    snaps.append(b"".join(cur[1][k] for k in range(np_)))
                    cur[:] = []


t = threading.Thread(target=xerabora, daemon=True)
t.start()

# ---- emulator and bridge --------------------------------------------------
env = dict(os.environ, SDL_AUDIODRIVER="dummy", XDG_RUNTIME_DIR="/tmp/xdg",
           VK_ICD_FILENAMES="/usr/share/vulkan/icd.d/lvp_icd.json")
os.makedirs("/tmp/xdg", mode=0o700, exist_ok=True)
emu = subprocess.Popen(["xvfb-run", "-a", gopher, rom], env=env, stdout=open(os.path.join(work, "gopher64.log"), "w"),
                       stderr=subprocess.STDOUT, start_new_session=True)
time.sleep(3)
blog = open(os.path.join(work, "bridge.log"), "w")
bridge = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "bridge", "n64ra_bridge.py"), "--gopher64",
                           "--rom", rom, "--pc", "127.0.0.1", "--ip", "127.0.0.1", "-v",
                           "--trace", os.path.join(work, "trace.log")], stdout=blog, stderr=subprocess.STDOUT)
time.sleep(secs)
bridge.terminate()
os.killpg(emu.pid, 15)
stop.set()
time.sleep(1)

# ---- checks ---------------------------------------------------------------
fails = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


blog_text = open(os.path.join(work, "bridge.log")).read()
rh = subprocess.run([os.path.join(HERE, "rchash"), rom], capture_output=True, text=True).stdout.strip()
check(bool(asked) and asked[0] == rh, f"hash from the bridge {asked[:1]} = rcheevos {rh}")
check("sending it to the N64" in blog_text, "watch list fetched and sent to the N64")
check(len(snaps) > 100, f"{len(snaps)} snapshots reached xeRAbora")

size = sum(n for _, n in layout)
prev, bad, jumps = None, 0, 0
# snapshots standing for several frames arrive as copies (trace: frame covers values)
copies, late, k = set(), set(), 0
for line in open(os.path.join(work, "trace.log")):
    c, lt = int(line.split()[1]), int(line.split()[2])
    if lt:
        late.add(k)
    k += 1
    for j in range(1, min(c, 4)):
        copies.add(k)
        k += 1
covered = sum(1 for _ in copies)
for k, b in enumerate(snaps):
    if len(b) != size:
        bad += 1
        continue
    v, pos = {}, 0
    for name, n in layout:
        v[name] = int.from_bytes(b[pos:pos + n], "little")
        pos += n
    f = v["frames"]
    ok = v["f8"] == f & 0xFF and v["oob"] == 0 and v["hi16"] == (f >> 16) & 0xFFFF
    if f:
        ok = ok and [v["r0"], v["r1"], v["r2"], v["r3"]] == [f, f + 1, f + 2, f + 3]
        ok = ok and v["ptr"] == (0x80100200 if (f >> 6) & 1 else 0x80100100) and v["u16"] == f & 0xFFFF
        ok = ok and v["exp"] == f ^ 0xA5A5A5A5
        ok = ok and v["n0a"] == (v["ptr"] & 0x7FFFFF) + 4 and v["n0v"] == f * 3
        ok = ok and v["n1a"] == 0x100020 + 4 * (f & 3) and v["n1v"] == f + (f & 3)
        ok = ok and v["n2a"] == (f * 3) & 0x7FFFFF
        xb = v["extra"].to_bytes(4 * EXTRA, "little")
        ok = ok and all(int.from_bytes(xb[4 * i:4 * i + 4], "little") == f + (i & 3) for i in range(0, EXTRA, 97))
    # a late snapshot (taken after the game handled its VI) is one ahead;
    # the next one then repeats it
    ok_step = f == prev + 1 if prev is not None else True
    if prev is not None and k in late and f == prev + 2:
        ok_step = True
    if prev is not None and (k - 1) in late and f == prev:
        ok_step = True
    if prev is not None and not ok_step and not (f == prev == 0) and not (f == prev and k in copies):
        jumps += 1
        if jumps <= 5:
            print(f"  counter {prev} -> {f} at snapshot {k}")
    if not ok:
        if bad < 5:
            print("  bad snapshot", k, {n: hex(x) for n, x in v.items()})
        bad += 1
    prev = f
check(bool(snaps) and bad == 0, f"every snapshot right ({bad} bad)")
check(bool(snaps) and jumps == 0, f"one snapshot per frame ({jumps} missed or doubled; {len(late)} taken late, {covered} sent as copies)")
if snaps and len(snaps[-1]) == size:
    last = dict(zip([n for n, _ in layout], [0] * len(layout)))
    pos = 0
    for name, n in layout:
        last[name] = int.from_bytes(snaps[-1][pos:pos + n], "little")
        pos += n
    worst = max(int.from_bytes(b[37 + 4:37 + 8], "little") for b in snaps if len(b) == size)
    print(f"  watch list {len(wfile)} bytes, snapshot {size} bytes a frame")
    check(last["dmas"] > 100, f"the game streamed {last['dmas']} x 32 KB from the ROM meanwhile")
    check(worst == 0, f"none of the game's DMAs corrupted ({worst} bad)")
    check(abs(last["piirq"] - last["dmas"]) <= 1, f"every game DMA got its PI interrupt ({last['piirq']} for {last['dmas']})")
m = [l for l in blog_text.splitlines() if "skipped on the N64" in l]
if m:
    print("  " + m[-1].split(" ", 1)[1])
if snaps:
    print("  frame counter", int.from_bytes(snaps[0][:4], "little"), "..", int.from_bytes(snaps[-1][:4], "little"))
print("\n%d failed" % len(fails) if fails else "\nall passed")
if fails:
    print("bridge log tail:\n" + "\n".join(blog_text.splitlines()[-15:]))
sys.exit(1 if fails else 0)
