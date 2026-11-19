#!/usr/bin/env python3
"""
N64RA Bridge - a window around n64ra_bridge.py: RetroAchievements on a
real Nintendo 64 through a SummerCart64.

Plug the SC64's USB port into this Mac, press Start, and boot a game from
the SC64 menu with "RetroAchievements (USB)" enabled. The window runs the
bridge (and xeRAbora, the RetroAchievements client), finds the SC64, and
shows the game, the snapshots and the unlocks.

Only the standard library and pyserial are used. On a Mac this file
normally runs from inside "N64RA Bridge.app", which carries its own
Python. Settings are kept in ~/Library/Application Support/N64RA Bridge/.

Command line:
    n64ra_gui.pyw --autostart     start the bridge as soon as the window opens
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "N64RA Bridge"
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_LINES_MAX = 3000

MUTED = "#888888"
GOOD = "#2e9e44"
WARN = "#c27c0e"
BAD = "#d1242f"


# --------------------------------------------------------------------------
# Settings

def app_support(name=APP_TITLE):
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA") or HERE, name)
    if IS_MAC:
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", name)
    return os.path.join(os.path.expanduser("~"), ".config", name.lower().replace(" ", "-"))


def settings_path():
    return os.path.join(app_support(), "n64ra_gui.json")


DEFAULTS = {
    "sc64": "",            # serial port; "" = find it
    "pc": "",              # xeRAbora's address; "" = find it on the network
    "autostart": False,    # start the bridge when the window opens
    "xera_path": "",       # xeRAbora program; "" = look in the usual places
    "xera_auto": True,     # start xeRAbora with the bridge
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def find_bridge():
    for p in (os.path.join(HERE, "n64ra_bridge.py"), os.path.join(HERE, "..", "bridge", "n64ra_bridge.py")):
        if os.path.isfile(p):
            return os.path.abspath(p)
    return ""


def sc64_ports():
    """Serial ports that look like an SC64 (FTDI 0403:6014), SC64s first."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    out = []
    for p in list_ports.comports():
        if (p.vid, p.pid) == (0x0403, 0x6014):
            sc = "SC64" in ((p.serial_number or "") + (p.description or ""))
            out.append((0 if sc else 1, p.device))
    return [d for _, d in sorted(out)]


# --------------------------------------------------------------------------
# xeRAbora, the RetroAchievements client (github.com/hacan359/xerabora)

XERA_UI_PORTS = range(18280, 18285)   # it takes the first free one of these
XERA_RELEASE = "https://github.com/hacan359/xerabora/releases/latest/download/"
XERA_PAGE = "https://github.com/hacan359/xerabora/releases/latest"


def xera_file_name():
    if IS_WINDOWS:
        return "xerabora.exe"
    if IS_MAC:
        return "xerabora-macos"
    import platform
    return "xerabora-linux-aarch64" if platform.machine() in ("aarch64", "arm64") else "xerabora-linux-x86_64"


def xera_download_dir():
    return os.path.join(app_support(), "xerabora")


def find_xerabora(configured):
    """The xeRAbora program: the one picked in the window, else one in the
    usual places (this app's download folder, one another app keeps in
    Application Support, Downloads, Desktop, Applications)."""
    if configured and os.path.isfile(configured):
        return configured
    name = xera_file_name()
    home = os.path.expanduser("~")
    places = [HERE, xera_download_dir()]
    try:  # another app's download, ~/Library/Application Support/<app>/xerabora
        base = os.path.dirname(app_support())
        places += sorted(os.path.join(base, d, "xerabora") for d in os.listdir(base))
    except OSError:
        pass
    places += [os.path.join(home, "Downloads"), os.path.join(home, "Desktop"), home]
    if IS_MAC:
        places += ["/Applications", os.path.join(home, "Applications")]
    for d in places:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return ""


def xera_state(ports):
    """(port, state dict) of a running xeRAbora, or (0, None)."""
    for port in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=1.0) as r:
                st = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(st, dict) and "console" in st:
                return port, st
        except Exception:
            continue
    return 0, None


def xera_quit(port):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/quit", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=2.0).read()
        return True
    except Exception:
        return False


def download_file(url, dest):
    """urllib first; the system curl when Python's TLS has no certificate
    store to check GitHub with."""
    tmp = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception:
        curl = shutil.which("curl")
        if not curl:
            raise
        res = subprocess.run([curl, "-fsSL", "--retry", "2", "-o", tmp, url], capture_output=True, text=True)
        if res.returncode != 0:
            raise OSError(res.stderr.strip() or "download failed")
    if os.path.getsize(tmp) < 100000:
        os.remove(tmp)
        raise OSError("the download is too small to be xeRAbora")
    os.replace(tmp, dest)
    if not IS_WINDOWS:
        os.chmod(dest, 0o755)


# --------------------------------------------------------------------------
# The window

class App:
    def __init__(self, root, args):
        self.root = root
        self.s = load_settings()
        self.q = queue.Queue()
        self.proc = None             # the bridge
        self.want_running = False    # Start pressed: keep a bridge going
        self.stopping = False
        self.retry_at = 0.0
        self.game = ""
        self.hash = ""
        self.phase = ""              # what the bridge is doing, for the status line
        self.last_status = {}
        self.xproc = None            # xeRAbora started by this window
        self.xport = 0
        self.xstate = None
        self.xstopping = False
        self.xpoll_busy = False

        root.title(APP_TITLE)
        root.minsize(640, 520)
        self._build()
        self._load_into_widgets()
        self.root.after(100, self._pump)
        self.root.after(1000, self._tick)
        self.root.after(1500, self._xera_poll)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if IS_MAC:
            try:
                root.createcommand("tk::mac::Quit", self.on_close)
                root.createcommand("tk::mac::ReopenApplication", root.deiconify)
            except Exception:
                pass
        if "--autostart" in args or self.s["autostart"]:
            self.root.after(300, self.start)

    # ---- layout -----------------------------------------------------------

    def _dot(self, parent):
        c = tk.Canvas(parent, width=14, height=14, highlightthickness=0, borderwidth=0)
        try:
            c.configure(background=ttk.Style().lookup("TFrame", "background") or self.root.cget("background"))
        except Exception:
            pass
        return c, c.create_oval(2, 2, 12, 12, fill=MUTED, outline="")

    def _build(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        # SummerCart64
        cf = ttk.LabelFrame(outer, text="SummerCart64 (USB)", padding=8)
        cf.pack(fill="x")
        ttk.Label(cf, text="Port:").grid(row=0, column=0, sticky="w")
        self.port = tk.StringVar()
        self.port_box = ttk.Combobox(cf, textvariable=self.port, width=34, postcommand=self._fill_ports)
        self.port_box.grid(row=0, column=1, sticky="w", padx=8)
        self.port_note = ttk.Label(cf, text="", foreground=MUTED)
        self.port_note.grid(row=0, column=2, sticky="w")
        ttk.Label(cf, text="xeRAbora at:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.pc = tk.StringVar()
        ttk.Entry(cf, textvariable=self.pc, width=20).grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(cf, text="empty: find it on the network", foreground=MUTED).grid(
            row=1, column=2, sticky="w", pady=(6, 0))
        self.autostart = tk.BooleanVar()
        ttk.Checkbutton(cf, text="Start the bridge when this window opens",
                        variable=self.autostart).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # xeRAbora
        xf = ttk.LabelFrame(outer, text="RetroAchievements (xeRAbora)", padding=8)
        xf.pack(fill="x", pady=(8, 0))
        self.xera_path = tk.StringVar()
        ttk.Entry(xf, textvariable=self.xera_path).grid(row=0, column=0, columnspan=3, sticky="ew", padx=(0, 8))
        ttk.Button(xf, text="Browse...", command=self.xera_browse).grid(row=0, column=3)
        self.xera_dl_btn = ttk.Button(xf, text="Download", command=self.xera_download)
        self.xera_dl_btn.grid(row=0, column=4, padx=(8, 0))
        self.xera_btn = ttk.Button(xf, text="Start xeRAbora", command=self.xera_toggle, width=16)
        self.xera_btn.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.xera_dot, self.xera_dot_id = self._dot(xf)
        self.xera_dot.grid(row=1, column=1, padx=(10, 4), pady=(6, 0))
        self.xera_status = ttk.Label(xf, text="Not running")
        self.xera_status.grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Button(xf, text="Open page", command=self.xera_open).grid(row=1, column=3, columnspan=2, sticky="e",
                                                                     pady=(6, 0))
        self.xera_auto = tk.BooleanVar()
        ttk.Checkbutton(xf, text="Start xeRAbora with the bridge (its page opens in your browser)",
                        variable=self.xera_auto).grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))
        xf.columnconfigure(2, weight=1)
        self.xera_path.trace_add("write", lambda *_: self.xera_refresh_label())

        # Start / stop and status
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(10, 4))
        self.start_btn = ttk.Button(bar, text="Start", width=12, command=self.toggle)
        self.start_btn.pack(side="left")
        self.dot, self.dot_id = self._dot(bar)
        self.dot.pack(side="left", padx=(12, 4))
        self.status = ttk.Label(bar, text="Stopped", font=("TkDefaultFont", 13 if IS_MAC else 10, "bold"))
        self.status.pack(side="left")
        ttk.Button(bar, text="Clear log", command=self.clear_log).pack(side="right")

        # The game
        gf = ttk.LabelFrame(outer, text="Game", padding=8)
        gf.pack(fill="x", pady=(4, 0))
        self.fields = {}
        for i, (key, label) in enumerate((("game", "Game"), ("hash", "RetroAchievements hash"),
                                          ("set", "Achievements"), ("snaps", "Snapshots"),
                                          ("link", "USB link"), ("unlocks", "Unlocked this session"))):
            ttk.Label(gf, text=label + ":", foreground=MUTED).grid(row=i, column=0, sticky="w")
            v = ttk.Label(gf, text="-")
            v.grid(row=i, column=1, sticky="w", padx=(8, 0))
            self.fields[key] = v
        gf.columnconfigure(1, weight=1)

        # Log
        logf = ttk.Frame(outer)
        logf.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(logf, height=10, wrap="none", state="disabled",
                           font=("Menlo", 11) if IS_MAC else ("TkFixedFont", 9),
                           background="#111", foreground="#ddd", insertbackground="#ddd")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log.tag_configure("err", foreground="#ff7b72")
        self.log.tag_configure("ok", foreground="#7ee787")
        self.log.tag_configure("me", foreground="#79c0ff")
        self.log.tag_configure("ra", foreground="#d2a8ff")
        self.log.tag_configure("gold", foreground="#f2cc60")

    def _fill_ports(self):
        self.port_box.configure(values=["Find it"] + sc64_ports())

    def _load_into_widgets(self):
        s = self.s
        self.port.set(s["sc64"] or "Find it")
        self.pc.set(s["pc"])
        self.autostart.set(s["autostart"])
        self.xera_path.set(find_xerabora(s["xera_path"]))
        self.xera_auto.set(s["xera_auto"])
        self._fill_ports()
        found = sc64_ports()
        self.port_note.configure(text=f"SC64 found: {found[0]}" if found else "no SC64 plugged in yet")

    def _store(self):
        s = self.s
        p = self.port.get().strip()
        s["sc64"] = "" if p in ("", "Find it") else p
        s["pc"] = self.pc.get().strip()
        s["autostart"] = self.autostart.get()
        s["xera_path"] = self.xera_path.get().strip()
        s["xera_auto"] = self.xera_auto.get()
        save_settings(s)

    def _set_status(self, text, color):
        self.status.configure(text=text)
        self.dot.itemconfigure(self.dot_id, fill=color)

    def _field(self, key, text, color=None):
        self.fields[key].configure(text=text, foreground=color or "")

    # ---- the bridge ---------------------------------------------------------

    def toggle(self):
        if self.want_running:
            self.stop()
        else:
            self.start()

    def start(self):
        self._store()
        if not find_bridge():
            messagebox.showerror(APP_TITLE, "n64ra_bridge.py is missing next to this program.")
            return
        self.want_running = True
        self.start_btn.configure(text="Stop")
        self.port_box.configure(state="disabled")
        if self.xera_auto.get() and self.xproc is None and not self.xport:
            self.xera_start(quiet=True)
        self._launch()

    def _launch(self):
        args = [sys.executable or "python3", "-u", find_bridge(), "--gui"]
        if self.s["sc64"]:
            args += ["--sc64", self.s["sc64"]]
        if self.s["pc"]:
            args += ["--pc", self.s["pc"]]
        args += os.environ.get("N64RA_BRIDGE_ARGS", "").split()  # for testing (e.g. --gopher64 --rom ...)
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, env=env, cwd=HERE)
        except Exception as e:
            self.proc = None
            self.write_log(f"Could not start the bridge: {e}", "err")
            self.retry_at = time.monotonic() + 5
            return
        self.phase = "Looking for the SC64..."
        self._set_status(self.phase, WARN)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def stop(self):
        self.want_running = False
        self.start_btn.configure(text="Start")
        self.port_box.configure(state="normal")
        if self.proc is not None:
            self.stopping = True
            try:
                self.proc.terminate()
            except Exception:
                pass
        else:
            self._set_status("Stopped", MUTED)

    def _read(self, proc):
        for raw in iter(proc.stdout.readline, b""):
            self.q.put(("line", raw.decode("utf-8", "replace").rstrip()))
        proc.wait()
        self.q.put(("exit", proc.returncode))

    def _on_line(self, line):
        if line.startswith("@status "):
            try:
                self._on_status(json.loads(line[8:]))
            except Exception:
                pass
            return
        text = re.sub(r"^\d\d:\d\d:\d\d ", "", line)
        tag = None
        m = re.match(r"SC64 at (\S+)", text)
        if m:
            self.port_note.configure(text=f"SC64 found: {m.group(1)}")
            self.phase = "SC64 found - boot a game with RetroAchievements on"
        if "no SummerCart64 found" in text:
            self.phase = "Waiting for the SC64 (plug its USB port into this Mac)"
            self.port_note.configure(text="no SC64 plugged in yet")
            tag = "err"
        m = re.match(r"reading the (\d+) KB ROM back", text)
        if m:
            self.phase = "Reading the game back to identify it (about %d s)" % max(1, int(m.group(1)) * 3 // 2048)
        m = re.match(r"game: (.*) \(([^)]*)\), .*hash ([0-9a-f]{32})", text)
        if m:
            self.game, self.hash = m.group(1).strip(), m.group(3)
            self._field("game", f"{self.game} ({m.group(2)})")
            self._field("hash", self.hash)
            self._field("set", "asking RetroAchievements...", WARN)
            self.phase = "Asking RetroAchievements about the game"
        m = re.match(r"xeRAbora knows the game: (.*) \((\d+)-byte", text)
        if m:
            self.game = m.group(1)
            self._field("game", self.game)
        m = re.match(r"watch list ready: (\d+) addresses", text)
        if m:
            self._field("set", f"{m.group(1)} addresses watched - sending to the N64", WARN)
        if text.startswith("the agent has the watch list"):
            self._field("set", "tracked", GOOD)
            self.phase = "Tracking"
            tag = "ok"
        if text.startswith("the agent refused"):
            self._field("set", "the N64 refused the watch list (see the log)", BAD)
            tag = "err"
        if "Unknown to RetroAchievements" in text:
            self._field("set", "not a dump RetroAchievements knows (check the ROM)", BAD)
            self.phase = "Game not recognised by RetroAchievements"
            tag = "err"
        if text.startswith("xeRAbora at"):
            tag = "ra"
        if text.startswith("achievement unlocked"):
            tag = "gold"
        if text.lstrip().startswith(("no snapshots for", "the SC64 does not answer", "unparsed", "agent:",
                                     "could not reopen")):
            tag = "err"
        if "snapshots forwarded" in text:
            return  # the counters show it
        if "Traceback" in text or "Error" in text or "Exception" in text:
            tag = "err"
        if text.strip():
            self.write_log(text, tag)

    def _on_status(self, st):
        self.last_status = st
        snaps = st.get("snaps", 0)
        if snaps:
            share = 100.0 * st.get("deltas", 0) / max(1, snaps)
            self._field("snaps", f"{snaps:,} frames sent ({share:.0f}% as changes only), "
                                 f"{st.get('dropped', 0)} skipped, {st.get('late', 0)} late")
        stalls, resyncs = st.get("stalls", 0), st.get("resyncs", 0)
        since = st.get("since")
        if since is not None and since > 2 and st.get("list"):
            self._field("link", f"no frames for {since:.0f} s", BAD)
        else:
            self._field("link", f"{stalls} stalls, {resyncs} resyncs" if (stalls or resyncs) else "steady",
                        WARN if stalls else GOOD if snaps else None)
        self._field("unlocks", str(st.get("unlocks", 0)), "#b8860b" if st.get("unlocks") else None)

    def _on_exit(self, code):
        self.proc = None
        if self.stopping or not self.want_running:
            self.stopping = False
            self.write_log("Bridge stopped.", "me")
            self._set_status("Stopped", MUTED)
            return
        # No SC64 yet, or it was unplugged: try again shortly.
        self.retry_at = time.monotonic() + 3
        if not self.phase.startswith("Waiting for the SC64"):
            self.write_log(f"The bridge stopped (code {code}); starting it again.", "me")

    def _tick(self):
        if self.want_running and self.proc is None and self.retry_at and time.monotonic() >= self.retry_at:
            self.retry_at = 0
            self._launch()
        if self.want_running:
            st = self.last_status
            since = st.get("since")
            if self.phase == "Tracking" and since is not None and since <= 2:
                self._set_status(f"Tracking {self.game}" if self.game else "Tracking", GOOD)
            elif self.phase == "Tracking" and since is not None:
                self._set_status("Tracking - waiting for frames from the N64", WARN)
            else:
                bad = self.phase.startswith("Game not recognised")
                self._set_status(self.phase or "Starting...", BAD if bad else WARN)
        self.root.after(1000, self._tick)

    def _pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "line":
                    self._on_line(val)
                elif kind == "exit":
                    self._on_exit(val)
                elif kind == "xline":
                    if val.strip():
                        self.write_log("[xeRAbora] " + val, "ra")
                elif kind == "xexit":
                    self._xera_exited(val)
                elif kind == "xstate":
                    self.xpoll_busy = False
                    self.xport, self.xstate = val
                    self.xera_refresh_label()
                elif kind == "xdl":
                    self._xera_downloaded(val)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    # ---- log ----------------------------------------------------------------

    def write_log(self, text, tag=None):
        self.log.configure(state="normal")
        at_end = self.log.yview()[1] > 0.999
        self.log.insert("end", time.strftime("%H:%M:%S ") + text + "\n", (tag,) if tag else ())
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > LOG_LINES_MAX:
            self.log.delete("1.0", f"{lines - LOG_LINES_MAX}.0")
        self.log.configure(state="disabled")
        if at_end:
            self.log.see("end")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def on_close(self):
        self._store()
        if self.proc is not None or self.xproc is not None:
            what = " and xeRAbora" if self.xproc is not None and self.proc is not None else ""
            msg = (f"Stop the bridge{what} and close?\n\nAchievements stop being tracked."
                   if self.proc is not None else "Stop xeRAbora and close?\n\nAchievements stop being tracked.")
            if not messagebox.askokcancel(APP_TITLE, msg):
                return
            self.want_running = False
            if self.proc is not None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=3)
                except Exception:
                    pass
            if self.xproc is not None:
                self.xera_stop(wait=True)
        self.root.destroy()

    # ---- xeRAbora -----------------------------------------------------------

    def xera_browse(self):
        p = filedialog.askopenfilename(title="xeRAbora program")
        if p:
            self.xera_path.set(os.path.normpath(p))
            self._store()

    def xera_download(self):
        dest = os.path.join(xera_download_dir(), xera_file_name())
        if not messagebox.askyesno(APP_TITLE, "Download the latest xeRAbora from its GitHub releases?\n\n"
                                   f"It is saved to {dest}"):
            return
        self.xera_dl_btn.configure(state="disabled")
        self.xera_status.configure(text="Downloading xeRAbora...")

        def work():
            try:
                download_file(XERA_RELEASE + xera_file_name(), dest)
                self.q.put(("xdl", (True, dest)))
            except Exception as e:
                self.q.put(("xdl", (False, str(e))))
        threading.Thread(target=work, daemon=True).start()

    def _xera_downloaded(self, res):
        ok, val = res
        self.xera_dl_btn.configure(state="normal")
        if ok:
            self.xera_path.set(val)
            self._store()
            self.write_log(f"xeRAbora downloaded to {val}", "me")
        elif messagebox.askyesno(APP_TITLE, f"The download did not work:\n{val}\n\n"
                                 "Open the releases page to download it yourself?"):
            webbrowser.open(XERA_PAGE)
        self.xera_refresh_label()

    def xera_toggle(self):
        if self.xproc is not None or self.xport:
            self.xera_stop()
        else:
            self.xera_start()

    def xera_start(self, quiet=False):
        if self.xproc is not None:
            return
        port, st = xera_state(XERA_UI_PORTS)
        if st is not None:
            self.xport, self.xstate = port, st
            self.write_log("xeRAbora is already running; using that one.", "ra")
            self.xera_refresh_label()
            return
        exe = self.xera_path.get().strip()
        if not exe or not os.path.isfile(exe):
            exe = find_xerabora("")
            if exe:
                self.xera_path.set(exe)
        if not exe:
            if not quiet:
                messagebox.showinfo(APP_TITLE, "xeRAbora was not found. Press Download, or Browse to "
                                    f"{xera_file_name()} if you already have it.")
            else:
                self.write_log("xeRAbora not found: press Download in the RetroAchievements box.", "ra")
            return
        self._store()
        try:
            # --server: it keeps running when its page is closed; this window
            # decides when it stops.
            self.xproc = subprocess.Popen([exe, "--server"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, cwd=os.path.dirname(exe))
        except Exception as e:
            self.xproc = None
            messagebox.showerror(APP_TITLE, f"Could not start xeRAbora:\n{e}")
            return
        self.xstopping = False
        self.write_log(f"> {os.path.basename(exe)} --server", "ra")
        threading.Thread(target=self._xera_read, args=(self.xproc,), daemon=True).start()
        self.xera_refresh_label()

    def _xera_read(self, proc):
        for raw in iter(proc.stdout.readline, b""):
            self.q.put(("xline", raw.decode("utf-8", "replace").rstrip()))
        proc.wait()
        self.q.put(("xexit", proc.returncode))

    def _xera_exited(self, code):
        self.xproc = None
        self.xport, self.xstate = 0, None
        self.write_log("xeRAbora stopped." if self.xstopping else f"xeRAbora exited (code {code}).",
                       "ra" if self.xstopping else "err")
        self.xstopping = False
        self.xera_refresh_label()

    def xera_stop(self, wait=False):
        """Ask it to quit (it finishes sending a pending unlock), then make sure."""
        self.xstopping = True
        port = self.xport or xera_state(XERA_UI_PORTS)[0]
        if port:
            xera_quit(port)
        proc = self.xproc
        if proc is None:
            self.xport, self.xstate = 0, None
            self.xera_refresh_label()
            return

        def finish():
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        if wait:
            finish()
        else:
            threading.Thread(target=finish, daemon=True).start()

    def xera_open(self):
        port = self.xport or xera_state(XERA_UI_PORTS)[0]
        if port:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        else:
            messagebox.showinfo(APP_TITLE, "xeRAbora is not running. Start it first.")

    def _xera_poll(self):
        if not self.xpoll_busy:
            self.xpoll_busy = True
            ports = [self.xport] if self.xport else list(XERA_UI_PORTS)

            def work():
                res = xera_state(ports)
                if res[1] is None and len(ports) == 1:
                    res = xera_state(XERA_UI_PORTS)
                self.q.put(("xstate", res))
            threading.Thread(target=work, daemon=True).start()
        self.root.after(2000, self._xera_poll)

    def xera_refresh_label(self):
        st = self.xstate
        running = self.xproc is not None or st is not None
        self.xera_btn.configure(text="Stop xeRAbora" if running else "Start xeRAbora")
        color, text = MUTED, "Not running"
        if not running:
            if not self.xera_path.get().strip():
                text, color = f"Not found - Download it, or Browse to {xera_file_name()}", WARN
        elif st is None:
            text, color = "Starting...", WARN
        else:
            con = st.get("console") or {}
            login = st.get("login") or {}
            game = st.get("game") or {}
            if not login.get("ok"):
                text, color = "Running - sign in on its page (Open page)", WARN
            elif not con.get("connected"):
                text, color = "Running - waiting for the bridge", WARN
            else:
                text, color = "Bridge connected", GOOD
                title = game.get("title") or ""
                if title and not game.get("checked_only"):
                    achs = st.get("achievements") or []
                    got = sum(1 for a in achs if a.get("mode"))
                    text += f" - {title}"
                    if achs:
                        text += f", {got}/{len(achs)} unlocked"
            if self.xproc is None:
                text += "  (started outside this window)"
        self.xera_status.configure(text=text)
        self.xera_dot.itemconfigure(self.xera_dot_id, fill=color)


def main():
    root = tk.Tk()
    icon = os.path.join(HERE, "icon.png")
    if os.path.isfile(icon):
        try:
            root._icon = tk.PhotoImage(file=icon)
            root.iconphoto(True, root._icon)
        except Exception:
            pass
    try:
        ttk.Style().theme_use("aqua" if IS_MAC else ("vista" if IS_WINDOWS else "clam"))
    except Exception:
        pass
    App(root, sys.argv[1:])
    root.mainloop()


if __name__ == "__main__":
    main()
