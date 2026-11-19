#!/usr/bin/env python3
"""n64ra bridge: RetroAchievements on a real N64, through a SummerCart64.

The N64 runs a small agent (installed by the n64ra build of
N64FlashcartMenu) that sends a snapshot of the watched memory out of
the SC64's USB port every frame. This program, on the PC or Pi the SC64
is plugged into, passes them on to xeRAbora over UDP:

    N64 --SC64 USB--> this bridge --UDP--> xeRAbora (rcheevos, your RA account)

  1. The agent says hello with the ROM's header and size. The bridge
     reads the ROM back from the SC64 and hashes it as rcheevos does
     (MD5 of the big-endian ROM).
  2. The bridge asks xeRAbora about the hash (RAQ1), fetches the watch
     list (RAG1/RAC1) and sends it down to the agent.
  3. The agent sends xeRAbora's snapshot each frame; the bridge forwards
     it as RA15 packets.

Usage:
    python3 n64ra_bridge.py                          # find the SC64 and xeRAbora
    python3 n64ra_bridge.py --sc64 /dev/ttyUSB0 --pc 192.168.1.20
    python3 n64ra_bridge.py --gopher64 --rom game.z64   # the gopher64 emulator

Needs pyserial for a real SC64 (pip install pyserial).
"""
import argparse
import hashlib
import json
import os
import select
import socket
import struct
import sys
import time

XERABORA_PORT = 18194
SNAP_CHUNK = 1472 - 191            # RA_SNAP_CHUNK_BYTES
FIELDS = [("seq", 6), ("sz", 4), ("us", 5), ("mx", 5), ("rxq", 3), ("fail", 6), ("err", 4),
          ("sk", 6), ("lk", 4), ("sq", 6), ("ds", 6), ("bad", 4), ("n", 4), ("vb", 4),
          ("pt", 1), ("np", 1), ("rc", 7), ("fc", 7), ("id", 15)]

USB_HELLO, USB_SNAP, USB_LIST_ACK, USB_DELTA = 0x50, 0x51, 0x52, 0x53
LIST_PIECE = 0x800 - 8              # the agent's 2 KB in buffer, less the piece header
USB_LIST, USB_HELLO_REQ, USB_NOLIST, USB_KEY_REQ = 0x58, 0x59, 0x5A, 0x5B
N64RA_MAGIC = 0x4E365241
SNAP_MAGIC = 0x534E5031
DELTA_MAGIC = 0x534E4431  # "SND1": a frame's changes against the snapshot with seq = offset


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def z64(data):
    """ROM bytes in big-endian (z64) order, as rcheevos hashes them."""
    if data[:1] == b"\x37":      # v64: 16-bit byte-swapped
        return b"".join(data[i + 1:i + 2] + data[i:i + 1] for i in range(0, len(data) - 1, 2))
    if data[:1] == b"\x40":      # n64: 32-bit little-endian
        return b"".join(data[i:i + 4][::-1] for i in range(0, len(data) - 3, 4))
    return data


# ---- transports: (type, payload) packets to and from the N64 -------------

class Gopher64:
    """gopher64's emulated SC64 USB, on TCP: type and size (big-endian
    words) then the data, both ways (UNFLoader's framing)."""

    def __init__(self, host="127.0.0.1", port=48646):
        self.addr = (host, port)
        self.sock = None
        self.buf = b""

    def connect(self):
        while self.sock is None:
            try:
                self.sock = socket.create_connection(self.addr, timeout=2)
                self.sock.settimeout(None)
                log(f"gopher64 USB at {self.addr[0]}:{self.addr[1]}")
            except OSError:
                time.sleep(1)

    def fileno(self):
        return self.sock.fileno()

    def send(self, kind, data):
        data = data or b"\0" * 4  # the agent sees a packet only by its length: never send an empty one
        self.sock.sendall(struct.pack(">II", kind, len(data)) + data)

    def recv(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("gopher64 closed the USB socket")
        self.buf += chunk
        out = []
        while len(self.buf) >= 8:
            kind, size = struct.unpack_from(">II", self.buf)
            if len(self.buf) < 8 + size:
                break
            out.append((kind & 0xFF, self.buf[8:8 + size]))
            self.buf = self.buf[8 + size:]
        return out

    def read_rom(self, size):
        return None  # gopher64 has no SC64 memory commands: use --rom


class SC64:
    """The real SummerCart64: a USB serial device (docs/03_usb_interface.md).
    PC -> SC64: "CMD" id arg0 arg1 [data]. SC64 -> PC: "CMP"/"ERR" id len data,
    and "PKT" 'U' len (type, 24-bit length, data) for the N64's USB_WRITE."""

    def __init__(self, port=None):
        import serial  # pyserial
        from serial.tools import list_ports
        if not port:
            found = [p.device for p in list_ports.comports() if (p.vid, p.pid) == (0x0403, 0x6014)
                     and "SC64" in ((p.serial_number or "") + (p.description or ""))]
            found = found or [p.device for p in list_ports.comports() if (p.vid, p.pid) == (0x0403, 0x6014)]
            if not found:
                raise SystemExit("no SummerCart64 found on USB (give --sc64 PORT)")
            port = found[0]
        self.port = port
        self.ser = serial.Serial(port, timeout=0)
        self.buf = b""
        self.pending = []
        self.reset()
        log(f"SC64 at {port}")

    def connect(self):
        pass

    def reopen(self):
        """Close and reopen the port, then reset the SC64's USB side (DTR):
        the SC64 drops a packet it was stuck on, and the agent goes on."""
        import serial
        try:
            self.ser.close()
        except Exception:
            pass
        time.sleep(0.2)
        self.ser = serial.Serial(self.port, timeout=0)
        self.pending = []
        self.reset()

    def reset(self):
        """DTR/DSR reset, as the docs describe."""
        s = self.ser
        s.dtr = True
        t = time.time()
        while not s.dsr and time.time() - t < 1:
            time.sleep(0.001)
        s.reset_input_buffer()
        s.reset_output_buffer()
        s.dtr = False
        t = time.time()
        while s.dsr and time.time() - t < 1:
            time.sleep(0.001)
        self.buf = b""

    def fileno(self):
        return self.ser.fileno()

    def _cmd(self, cid, arg0, arg1, data=b"", want_reply=True, timeout=5):
        self.ser.write(b"CMD" + cid.encode() + struct.pack(">II", arg0, arg1) + data)
        if not want_reply:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            for kind, body in self._parse(self.ser.read(65536)):
                if kind == ("CMP", cid):
                    return body
                if kind == ("ERR", cid):
                    raise IOError(f"SC64 command {cid} failed")
                self.pending.append((kind, body))
            time.sleep(0.0005)
        raise IOError(f"SC64 command {cid} timed out")

    def _parse(self, chunk):
        self.buf += chunk
        out = []
        while len(self.buf) >= 8:
            ident, cid = self.buf[:3].decode(errors="replace"), chr(self.buf[3])
            if ident not in ("CMP", "ERR", "PKT"):
                self.buf = self.buf[1:]  # out of step: resync
                continue
            size = struct.unpack_from(">I", self.buf, 4)[0]
            if len(self.buf) < 8 + size:
                break
            out.append(((ident, cid), self.buf[8:8 + size]))
            self.buf = self.buf[8 + size:]
        return out

    def send(self, kind, data):
        data = data or b"\0" * 4  # the agent sees a packet only by its length: never send an empty one
        self._cmd("U", kind, len(data), data, want_reply=False)

    bytes_in = 0
    kinds_in = {}

    def recv(self):
        items, self.pending = self.pending, []
        chunk = self.ser.read(65536)
        self.bytes_in += len(chunk)
        items += self._parse(chunk)
        for (ident, cid), _ in items:
            self.kinds_in[ident + cid] = self.kinds_in.get(ident + cid, 0) + 1
        out = []
        for (ident, cid), body in items:
            if ident == "PKT" and cid == "U" and len(body) >= 4:
                kind, n = body[0], int.from_bytes(body[1:4], "big")
                out.append((kind, body[4:4 + n]))
            elif ident == "PKT" and cid == "G":
                log("the agent did not take a packet in time (watch list resent on its next hello)")
        return out

    unstable = 0  # chunks that read differently twice in a row

    def _read_once(self, addr, n):
        part = self._cmd("m", addr, n)
        if len(part) != n:
            raise IOError(f"SC64 read at {addr:#x}: {len(part)} bytes, not {n}")
        return part

    def _read(self, addr, size):
        """Each 1 MB is read until two reads agree: the game is using the
        cartridge at the same time, and one bad read would spoil the hash."""
        out = bytearray()
        for off in range(0, size, 1 << 20):
            n = min(1 << 20, size - off)
            seen = [self._read_once(addr + off, n)]
            while True:
                part = self._read_once(addr + off, n)
                if part in seen:
                    break
                self.unstable += 1
                log(f"the SC64 read {addr + off:#010x} differently twice; reading it again")
                seen.append(part)
                if len(seen) > 6:
                    raise IOError(f"SC64 read at {addr + off:#x} never the same twice")
            out += part
        return out

    def _config(self, cfg_id):
        return struct.unpack(">I", self._cmd("c", cfg_id, 0)[:4])[0]

    def agent_status(self):
        """The agent's status block, straight from the SC64's data buffer
        (BlockRAM 05000000h; the agent's out half ends with it)."""
        raw = self._cmd("m", 0x05000000 + 0x1800 - 64, 64, timeout=1)
        names = ("magic exceptions watch_hits frame seq sent dropped pi_busy usb_busy usb_busy_run "
                 "cmd_fail list_state pending count mem_limit spare").split()
        return dict(zip(names, struct.unpack(">16I", raw)))

    def read_rom(self, size):
        """The ROM as the N64 sees it. The menu puts a ROM over 64 MB - 128 KB
        in SDRAM up to there, its next 128 KB in flash (ROM shadow, seen by
        the N64 at the end of the 64 MB) and anything over 64 MB in flash
        too (ROM extended): docs/01_memory_map.md."""
        SDRAM, SHADOW, EXTENDED, TOP = 0x00000000, 0x04FE0000, 0x04000000, 64 << 20
        shadow = size > TOP - (128 << 10) and self._config(2)     # ROM_SHADOW_ENABLE
        extended = size > TOP and self._config(14)                 # ROM_EXTENDED_ENABLE
        main = TOP - (128 << 10) if shadow else min(size, TOP)
        out = self._read(SDRAM, main)
        if shadow:
            out += self._read(SHADOW, min(size, TOP) - main)
        elif size > main:
            out += self._read(SDRAM + main, min(size, TOP) - main)
        if size > TOP:
            out += self._read(EXTENDED, size - TOP) if extended else bytes(size - TOP)
        return bytes(out)


# ---- the bridge -----------------------------------------------------------

class Bridge:
    def __init__(self, args, link):
        self.args = args
        self.link = link
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp.bind(("0.0.0.0", args.port))
        self.pc = (args.pc, XERABORA_PORT) if args.pc else None
        self.my_ip = args.ip or self.guess_ip()
        self.rom_cache = {}
        self.hash = self.serial = None
        self.state = "idle"         # idle, asking, fetching, ready, unknown
        self.list_len = self.chunks = 0
        self.parts = {}
        self.wfile = b""
        self.count = 0
        self.last_ask = self.last_hb = 0
        self.last_stats = time.time()
        self.snap_seq = self.udp_seq = 0
        self.stats = {"snaps": 0, "dropped": 0, "udp": 0}
        self.ref, self.ref_seq, self.key_asked = b"", 0, 0.0
        self.last_snap = 0.0
        self.last_diag = 0.0
        self.prev_status = None
        self.list_sent_at = 0
        self.list_next = 0          # offset of the piece in flight
        self.list_done = False
        self.parts_in = {}          # seq -> (meta, bytearray, received)

    def guess_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    # ---- xeRAbora ----
    def to_pc(self, data):
        self.udp.sendto(data, self.pc or ("255.255.255.255", XERABORA_PORT))

    def ask(self):
        self.last_ask = time.time()
        if self.state == "asking":
            self.to_pc(f"RAQ1 {self.hash} {self.serial} {self.my_ip} {self.args.port}".encode())
        elif self.state == "fetching":
            for i in range(self.chunks):
                if i not in self.parts:
                    self.to_pc(f"RAG1 {self.hash} {i} {self.my_ip} {self.args.port}".encode())
                    break

    def ra15(self, values):
        """One snapshot as RA15 packets (xeRAbora's console protocol)."""
        sq = self.snap_seq % 1000000
        parts = max(1, (len(values) + SNAP_CHUNK - 1) // SNAP_CHUNK)
        for pt in range(parts):
            chunk = values[pt * SNAP_CHUNK:(pt + 1) * SNAP_CHUNK]
            self.udp_seq = (self.udp_seq + 1) % 1000000
            vals = {"seq": self.udp_seq, "sz": 1472, "sq": sq, "n": self.count, "vb": len(chunk),
                    "pt": pt, "np": parts}
            head = "RA15"
            for name, width in FIELDS:
                if name == "id":
                    v = (self.serial or "").ljust(15, "~")[:15]
                elif name == "lk":
                    v = "SER1"
                elif name == "err":
                    v = "+000"
                else:
                    v = str(vals.get(name, 0)).rjust(width, "0")[-width:]
                head += f" {name}={v}"
            self.to_pc(head.encode() + b" " + chunk)
            self.stats["udp"] += 1

    def on_pc(self, data, addr):
        if self.pc is None and data.startswith(b"RA"):
            self.pc = (addr[0], XERABORA_PORT)
            log(f"xeRAbora at {addr[0]}")
        if data.startswith(b"RAA1 OK"):
            f = data.split()
            self.list_len, self.chunks = int(f[2]), int(f[3])
            title = b" ".join(f[7:]).decode(errors="replace") if len(f) > 7 else ""
            log(f"xeRAbora knows the game{': ' + title if title else ''} ({self.list_len}-byte watch list)")
            if self.state == "asking":
                self.state = "fetching"
                self.parts.clear()
                self.ask()
        elif data.startswith(b"RAA1 NO"):
            if self.state != "unknown":
                log("xeRAbora: " + data[8:].decode(errors="replace").strip() +
                    " (the hash must be one RetroAchievements lists for the game: check the ROM"
                    " with --rom FILE, see the README)")
            self.state = "unknown"
            self.link.send(USB_NOLIST, b"")
        elif data.startswith(b"RAC1 "):
            sp = data.split(b" ", 3)
            idx, n = int(sp[1]), int(sp[2])
            self.parts[idx] = sp[3][:n]
            if self.state == "fetching" and len(self.parts) == self.chunks:
                self.wfile = b"".join(self.parts[i] for i in range(self.chunks))[:self.list_len]
                self.count = struct.unpack_from("<I", self.wfile, 8)[0]
                self.state = "ready"
                log(f"watch list ready: {self.count} addresses; sending it to the N64")
                self.send_list()
            elif self.state == "fetching":
                self.ask()
        elif data.startswith(b"RAO1"):
            # one reply per broadcast: say it once
            msg = data.decode(errors="replace").strip()
            if msg != getattr(self, "last_rao", None):
                self.last_rao = msg
                log("xeRAbora: " + msg)
        elif data.startswith(b"RAU1"):
            what = data[5:].decode(errors="replace").strip()
            seen = self.__dict__.setdefault("unlocks_seen", set())
            if what not in seen:  # xeRAbora repeats the notice until it is sure we heard
                seen.add(what)
                log("achievement unlocked: " + what)
                self.stats["unlocks"] = self.stats.get("unlocks", 0) + 1
            self.udp.sendto(b"RAK1 0 n64ra", addr)

    def send_list(self, offset=0):
        """One piece of the watch list; the agent acknowledges each with the
        next offset it wants, so only one is ever in flight."""
        self.list_sent_at = time.time()
        self.list_next = offset
        self.list_done = False
        piece = self.wfile[offset:offset + LIST_PIECE]
        self.link.send(USB_LIST, struct.pack(">II", len(self.wfile), offset) + piece)

    def list_nodes(self):
        """Pointer chain nodes in the watch list (its RAN2 section)."""
        try:
            p = 16 + 4 * self.count
            if self.wfile[p:p + 4] == b"RAN2":
                return struct.unpack_from("<I", self.wfile, p + 4)[0]
        except Exception:
            pass
        return 0

    # ---- the N64 ----
    def rom_hash(self, size, header):
        key = (size, header)
        if key in self.rom_cache:
            return self.rom_cache[key]
        data = None
        if self.args.rom:
            data = open(self.args.rom, "rb").read()
        elif size:
            log(f"reading the {size // 1024} KB ROM back from the SC64 to hash it (read twice to be sure: about"
                f" {max(1, size >> 20) * 3 // 2} s)")
            data = self.link.read_rom(size)
        if data is None:
            return None
        h = hashlib.md5(z64(data)[:64 << 20]).hexdigest()
        self.rom_cache[key] = h
        return h

    def on_n64(self, kind, body):
        if kind == USB_HELLO and len(body) >= 24 + 64:
            magic, ver, rom_size, mem_size, frames, list_state = struct.unpack_from(">6I", body)
            if magic != N64RA_MAGIC:
                return
            header = body[24:88]
            title = header[0x20:0x34].decode("ascii", "replace").strip(" \0")
            code = header[0x3B:0x3F].decode("ascii", "replace")
            serial = f"{code}-{title}"[:15].replace(" ", "_")
            h = self.rom_hash(rom_size, header)
            if h is None:
                log(f"N64 says hello ({title}), but there is no ROM to hash: give --rom")
                return
            if h != self.hash:
                self.hash, self.serial = h, serial
                log(f"game: {title} ({code}), {mem_size // (1 << 20)} MB RAM, hash {h}")
                self.state = "asking"
                self.ask()
            elif self.state == "ready" and list_state == 0 and time.time() - self.list_sent_at > 2:
                self.send_list()  # the agent missed it (or rebooted)
            elif self.state == "unknown":
                self.link.send(USB_NOLIST, b"")
        elif kind == USB_LIST_ACK and len(body) >= 12:
            got, _, list_state = struct.unpack_from(">3I", body)
            if self.state != "ready" or self.list_done:
                return
            if list_state == 1:
                self.list_done = True
                log("the agent has the watch list")
            elif list_state == 2:
                self.list_done = True
                log(f"the agent refused the watch list ({self.count} addresses, {self.list_nodes()} pointer chain nodes; "
                    "it takes up to 4096 and 1024: update sc64menu.n64 on the SD card)")
            elif got < len(self.wfile):
                self.send_list(got)
        elif kind == USB_DELTA and len(body) >= 40:
            magic, seq, frame, count, nodes, nbytes, dropped, covers, base, part = struct.unpack_from(">10I", body)
            if magic != DELTA_MAGIC:
                return
            if base != self.ref_seq or len(self.ref) != nbytes:
                now = time.time()
                if now - self.key_asked > 0.5:  # missed one: ask for a whole snapshot
                    self.key_asked = now
                    self.link.send(USB_KEY_REQ, b"")
                    self.stats["keyreq"] = self.stats.get("keyreq", 0) + 1
                return
            cur, p, d = bytearray(self.ref), 0, body[40:40 + part]
            while p + 4 <= len(d):
                off, n = struct.unpack_from(">HH", d, p)
                cur[off:off + n] = d[p + 4:p + 4 + n]
                p += 4 + n
            self.ref, self.ref_seq = bytes(cur), seq
            self.stats["deltas"] = self.stats.get("deltas", 0) + 1
            self.forward(seq, frame, dropped, covers, self.ref)
        elif kind == USB_SNAP and len(body) >= 40:
            magic, seq, frame, count, nodes, nbytes, dropped, covers, offset, part = struct.unpack_from(">10I", body)
            if magic != SNAP_MAGIC:
                return
            if nbytes > len(body) - 40 or offset or part != nbytes:
                # a snapshot in several packets: put it back together
                meta, buf, have = self.parts_in.get(seq, (None, bytearray(nbytes), 0))
                buf[offset:offset + part] = body[40:40 + part]
                have += part
                if have < nbytes:
                    self.parts_in = {seq: (None, buf, have)}  # older unfinished ones are gone
                    return
                self.parts_in = {}
                values = bytes(buf)
            else:
                values = body[40:40 + nbytes]
            self.ref, self.ref_seq = bytes(values), seq
            self.forward(seq, frame, dropped, covers, values)

    def forward(self, seq, frame, dropped, covers, values):
        """One frame's snapshot, whole, to xeRAbora."""
        if self.state != "ready":
            if not self.hash:
                self.link.send(USB_HELLO_REQ, b"")  # we restarted: who are you?
            return
        self.stats["dropped"] = dropped
        # a snapshot can stand for frames the agent saw go by without an
        # exception in between: xeRAbora counts one per RA15 snapshot
        late = covers >> 31
        covers &= 0x7FFFFFFF
        self.stats["late"] = self.stats.get("late", 0) + late
        self.last_snap = time.time()
        if getattr(self, "stalled_at", 0):
            log(f"  snapshots flowing again after {self.last_snap - self.stalled_at:.1f} s"
                f" (USB stall {self.recoveries})")
            self.stalled_at = 0
        for _ in range(max(1, min(covers, 4))):
            self.snap_seq += 1
            self.ra15(values)
            self.stats["snaps"] += 1
        if self.args.trace:
            self.args.trace.write(f"{frame} {covers} {late} {values.hex()}\n")
            self.args.trace.flush()

    def run(self):
        self.link.connect()
        log(f"UDP {self.my_ip}:{self.args.port}, xeRAbora {self.pc[0] if self.pc else 'by broadcast'}")
        self.to_pc(f"RAP1 {self.my_ip} {self.args.port}".encode())
        while True:
            r, _, _ = select.select([self.udp, self.link], [], [], 0.2)
            if self.link in r:
                for kind, body in self.link.recv():
                    self.on_n64(kind, body)
            elif isinstance(self.link, SC64):
                for kind, body in self.link.recv():
                    self.on_n64(kind, body)
            if self.udp in r:
                data, addr = self.udp.recvfrom(4096)
                self.on_pc(data, addr)
            now = time.time()
            if self.state in ("asking", "fetching") and now - self.last_ask > 1.0:
                self.ask()
            if self.state == "ready" and not self.list_done and now - self.list_sent_at > 1.5:
                self.send_list(self.list_next)  # no acknowledgement: the SC64 flushes after 1 s
            if now - self.last_hb > 10:
                self.last_hb = now
                self.to_pc(f"RAH1 {self.stats['snaps']} 0 0 0 0".encode())
            if (isinstance(self.link, SC64) and self.state == "ready" and self.list_done and self.last_snap
                    and now - self.last_snap > 1.5 and now - self.last_diag > 3):
                self.last_diag = now
                self.diagnose()
            if self.args.gui and now - getattr(self, "last_gui", 0) >= 1:
                self.last_gui = now
                print("@status " + json.dumps({
                    "snaps": self.stats["snaps"], "dropped": self.stats["dropped"],
                    "late": self.stats.get("late", 0), "deltas": self.stats.get("deltas", 0),
                    "resyncs": self.stats.get("keyreq", 0), "stalls": getattr(self, "recoveries", 0),
                    "unlocks": self.stats.get("unlocks", 0), "state": self.state, "list": self.list_done,
                    "since": round(now - self.last_snap, 1) if self.last_snap else None}), flush=True)
            if now - self.last_stats > 10 and self.stats["snaps"]:
                log(f"{self.stats['snaps']} snapshots forwarded ({self.stats['dropped']} skipped on the N64 "
                    f"while the cartridge bus was busy, {self.stats.get('late', 0)} taken late; "
                    f"{self.stats.get('deltas', 0)} sent as changes only, {self.stats.get('keyreq', 0)} resyncs)")
                self.last_stats = now


    def diagnose(self):
        """Snapshots have stopped: ask the SC64 what the agent is doing."""
        link = self.link
        log(f"no snapshots for {time.time() - self.last_snap:.0f} s; serial in: {link.bytes_in} bytes, "
            f"{len(link.buf)} unparsed, packets {link.kinds_in}")
        if link.buf:
            log(f"  unparsed bytes start {link.buf[:16].hex()}")
        try:
            s = link.agent_status()
        except Exception as e:
            log(f"  the SC64 does not answer over USB ({e}): reopening the port and resetting its USB side")
            self.recoveries = getattr(self, "recoveries", 0) + 1
            try:
                link.reopen()
            except Exception as e2:
                log(f"  could not reopen the port ({e2})")
                return
            self.parts_in = {}
            self.stalled_at = self.last_snap
            self.last_snap = time.time()
            return
        if s["magic"] != 0x4E365354:
            log("  no agent status in the SC64's buffer (agent too old: update sc64menu.n64)")
            return
        p = self.prev_status
        log("  agent: " + ", ".join(f"{k} {v}" for k, v in s.items() if k not in ("magic", "spare")))
        if p:
            if s["exceptions"] == p["exceptions"]:
                log("  the agent has not run since last time: the game no longer goes through it"
                    f" (it wrote its exception handler {s['watch_hits']} times)")
            elif s["seq"] == p["seq"]:
                log("  the agent runs but takes no snapshots (no VI frames seen)")
            elif s["sent"] == p["sent"]:
                why = (f"the SC64 says it is still sending ({s['usb_busy_run']} times in a row)"
                       if s["usb_busy_run"] else "the cartridge bus was never free" if s["pi_busy"] > p["pi_busy"]
                       else "unknown")
                log(f"  the agent takes snapshots but sends none: {why}")
            else:
                log("  the agent is sending: the packets are lost between the SC64 and here")
        self.prev_status = s


def compare(link, path):
    """Where the SC64's copy of the ROM differs from the file."""
    rom = z64(open(path, "rb").read())
    size, top, kb = len(rom), 64 << 20, 128 << 10
    print(f"file: {size} bytes, RetroAchievements hash {hashlib.md5(rom[:top]).hexdigest()}")
    for cid, name in ((2, "ROM shadow"), (14, "ROM extended"), (6, "save type"), (7, "CIC seed")):
        try:
            print(f"SC64 {name}: {link._config(cid):#x}")
        except Exception as e:
            print(f"SC64 {name}: ? ({e})")
    print("reading the ROM back (about a minute)...")
    back = link.read_rom(size)
    print(f"read back: RetroAchievements hash {hashlib.md5(back[:top]).hexdigest()}"
          f" ({link.unstable} MB read differently on a second read)")
    bad, block = [], 4096
    for off in range(0, size, block):
        if back[off:off + block] != rom[off:off + block]:
            if bad and bad[-1][1] == off:
                bad[-1][1] = off + block
            else:
                bad.append([off, off + block])
    if not bad:
        print("identical")
    for a, b in bad[:20]:
        i = next(i for i in range(a, b) if back[i] != rom[i])
        print(f"differs {a:#010x}-{b:#010x} ({(b - a) // 1024} KB), first at {i:#x}: "
              f"file {rom[i:i + 8].hex()} SC64 {back[i:i + 8].hex()}")
    if len(bad) > 20:
        print(f"... {len(bad)} ranges in all")
    if size > top - kb:
        tail = rom[top - kb:min(size, top)]
        for name, addr in (("SDRAM end", top - kb), ("flash shadow", 0x04FE0000)):
            got = link._read(addr, len(tail))
            same = sum(1 for i in range(0, len(tail), block) if got[i:i + block] == tail[i:i + block])
            print(f"last 128 KB of the file vs {name} ({addr:#010x}): {same} of {len(tail) // block} blocks match")
    out = os.path.join(os.path.expanduser("~"), "n64ra-readback.z64")
    open(out, "wb").write(back)
    print(f"read-back saved as {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sc64", metavar="PORT", help="the SC64's serial port (default: find it)")
    ap.add_argument("--gopher64", nargs="?", const="127.0.0.1:48646", metavar="HOST:PORT",
                    help="talk to the gopher64 emulator's SC64 instead")
    ap.add_argument("--rom", help="hash this ROM file instead of reading the ROM back")
    ap.add_argument("--hash", metavar="FILE", help="print a ROM file's RetroAchievements hash (and its SHA-1, "
                    "to compare with No-Intro), then exit")
    ap.add_argument("--compare", metavar="FILE", help="read the loaded ROM back from the SC64, compare it "
                    "with FILE and say where they differ, then exit")
    ap.add_argument("--pc", help="xeRAbora's IP address (default: broadcast)")
    ap.add_argument("--port", type=int, default=18195, help="UDP port the bridge listens on")
    ap.add_argument("--ip", help="this machine's IP address as xeRAbora should see it")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--trace", type=argparse.FileType("w"), help=argparse.SUPPRESS)
    ap.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)  # "@status {json}" once a second
    args = ap.parse_args()
    if args.hash:
        data = z64(open(args.hash, "rb").read())[:64 << 20]
        print(f"RetroAchievements hash  {hashlib.md5(data).hexdigest()}")
        print(f"SHA-1 (big-endian ROM)  {hashlib.sha1(data).hexdigest()}")
        print(f"header                  {data[0x20:0x34].decode(errors='replace').strip()} "
              f"({data[0x3B:0x3F].decode(errors='replace')}, revision {data[0x3F]})")
        return
    if args.compare:
        compare(SC64(args.sc64), args.compare)
        return
    if args.gopher64:
        host, _, port = args.gopher64.partition(":")
        link = Gopher64(host, int(port or 48646))
    else:
        link = SC64(args.sc64)
    Bridge(args, link).run()


if __name__ == "__main__":
    main()
