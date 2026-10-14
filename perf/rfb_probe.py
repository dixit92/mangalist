"""A minimal RFB (VNC) client for measuring what the browser view costs: connects to the container's Xvnc unix socket
like noVNC does (Tight + JPEG quality / compression pseudo-encodings), drives the app with key / pointer events and
records, per action, the time until the screen settles, the bytes the server sent and Xvnc's CPU time.
Research only; runs inside a throwaway container of the release image (python3.11, no extra packages).

    python rfb_probe.py --quality 6 --compress 2 --actions actions.json
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import threading
import time


def xvnc_cpu() -> float:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as fh:
                if fh.read().strip() != "Xvnc":
                    continue
            with open(f"/proc/{pid}/stat") as fh:
                parts = fh.read().rsplit(")", 1)[1].split()
            return (int(parts[11]) + int(parts[12])) / os.sysconf("SC_CLK_TCK")
        except OSError:
            continue
    return 0.0


class Client:
    def __init__(self, path, quality, compress):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.buf = b""
        self.lock = threading.Lock()
        self.updates = []       # (t_done, bytes, rects, area, jpeg_rects)
        self.total = 0
        ver = self.read(12)
        self.s.sendall(b"RFB 003.008\n")
        n = self.read(1)[0]
        types = self.read(n)
        assert 1 in types, types
        self.s.sendall(b"\x01")
        assert struct.unpack(">I", self.read(4))[0] == 0
        self.s.sendall(b"\x01")             # shared
        self.w, self.h = struct.unpack(">HH", self.read(4))
        pf = self.read(16)
        self.bpp, self.depth = pf[0], pf[1]
        name_len = struct.unpack(">I", self.read(4))[0]
        self.name = self.read(name_len)
        encs = [7, 1, 0, -224, -223]
        if quality is not None:
            encs.append(-32 + quality)
        encs.append(-256 + compress)
        self.s.sendall(struct.pack(">BxH", 2, len(encs)) + b"".join(struct.pack(">i", e) for e in encs))
        self.tpixel = 3 if (self.bpp == 32 and self.depth == 24) else self.bpp // 8
        self.ver = ver

    def read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise EOFError
            self.buf += chunk
            self.total += len(chunk)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def compact(self):
        b = self.read(1)[0]
        n = b & 0x7F
        if b & 0x80:
            b = self.read(1)[0]
            n |= (b & 0x7F) << 7
            if b & 0x80:
                n |= self.read(1)[0] << 14
        return n

    def request(self, incremental=True):
        self.s.sendall(struct.pack(">BBHHHH", 3, 1 if incremental else 0, 0, 0, self.w, self.h))

    def key(self, keysym, down):
        self.s.sendall(struct.pack(">BBxxI", 4, 1 if down else 0, keysym))

    def pointer(self, mask, x, y):
        self.s.sendall(struct.pack(">BBHH", 5, mask, x, y))

    def read_message(self):
        t = self.read(1)[0]
        if t == 0:
            start = self.total - len(self.buf) - 1
            _, n = struct.unpack(">BH", self.read(3))
            rects = area = jpeg = 0
            for _ in range(n):
                x, y, w, h, enc = struct.unpack(">HHHHi", self.read(12))
                if enc == -224:
                    break
                rects += 1
                area += w * h
                if enc == 0:
                    self.read(w * h * self.bpp // 8)
                elif enc == 1:
                    self.read(4)
                elif enc == -223:
                    self.w, self.h = w, h
                elif enc == 7:
                    ctrl = self.read(1)[0]
                    comp = ctrl >> 4
                    if comp == 8:
                        self.read(self.tpixel)
                    elif comp == 9:
                        jpeg += 1
                        self.read(self.compact())
                    elif comp > 9:
                        raise ValueError(f"tight comp {comp}")
                    else:
                        filt = self.read(1)[0] if ctrl & 0x40 else 0
                        if filt == 1:
                            ncol = self.read(1)[0] + 1
                            self.read(ncol * self.tpixel)
                            size = ((w + 7) // 8) * h if ncol == 2 else w * h
                        else:
                            size = w * h * self.tpixel
                        if size < 12:
                            self.read(size)
                        else:
                            self.read(self.compact())
                else:
                    raise ValueError(f"encoding {enc}")
            end = self.total - len(self.buf)
            with self.lock:
                self.updates.append((time.perf_counter(), end - start, rects, area, jpeg))
            return True
        if t == 2:
            return False
        if t == 3:
            self.read(3)
            self.read(struct.unpack(">I", self.read(4))[0])
            return False
        if t == 1:
            _, _, n = struct.unpack(">BHH", self.read(5))
            self.read(n * 6)
            return False
        raise ValueError(f"message {t}")

    def loop(self):
        """Like noVNC: ask for the next incremental update as soon as one arrives."""
        self.request(False)
        while True:
            try:
                if self.read_message():
                    self.request(True)
            except (EOFError, OSError):
                return

    def take(self):
        with self.lock:
            u, self.updates = self.updates, []
        return u


KEYS = {"ctrl": 0xFFE3, "backspace": 0xFF08, "down": 0xFF54, "up": 0xFF52, "pagedown": 0xFF56, "escape": 0xFF1B,
        "return": 0xFF0D, "tab": 0xFF09}


def run_action(c, a):
    kind = a["do"]
    if kind == "type":
        for ch in a["text"]:
            yield f"key {ch!r}", lambda ch=ch: (c.key(ord(ch), True), c.key(ord(ch), False))
    elif kind == "key":
        for _ in range(a.get("n", 1)):
            k = KEYS[a["key"]]
            yield f"key {a['key']}", lambda k=k: (c.key(k, True), c.key(k, False))
    elif kind == "chord":
        mods = [KEYS[m] for m in a["mods"]]

        def chord():
            for m in mods:
                c.key(m, True)
            c.key(ord(a["key"]), True)
            c.key(ord(a["key"]), False)
            for m in reversed(mods):
                c.key(m, False)
        yield f"chord {'+'.join(a['mods'])}+{a['key']}", chord
    elif kind == "click":
        yield f"click {a.get('label', '')}", lambda: (c.pointer(0, a["x"], a["y"]), c.pointer(1, a["x"], a["y"]),
                                                       c.pointer(0, a["x"], a["y"]))
    elif kind == "move":
        x0, y0, x1, y1, n = a["x0"], a["y0"], a["x1"], a["y1"], a["n"]
        for i in range(n):
            x = x0 + (x1 - x0) * i // max(1, n - 1)
            y = y0 + (y1 - y0) * i // max(1, n - 1)
            yield f"hover {a.get('label', '')}", lambda x=x, y=y: c.pointer(0, x, y)
    elif kind == "wheel":
        btn = 16 if a.get("dir", "down") == "down" else 8
        for _ in range(a.get("n", 1)):
            yield f"wheel {a.get('label', '')}", lambda: (c.pointer(btn, a["x"], a["y"]), c.pointer(0, a["x"], a["y"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", default="/tmp/vnc.sock")
    ap.add_argument("--quality", type=int, default=6)
    ap.add_argument("--lossless", action="store_true")
    ap.add_argument("--compress", type=int, default=2)
    ap.add_argument("--actions", required=True)
    ap.add_argument("--quiet-ms", type=int, default=1200)
    ap.add_argument("--min-area", type=int, default=2500, help="updates smaller than this (the caret blink) do not "
                    "count as the action's response")
    args = ap.parse_args()
    c = Client(args.sock, None if args.lossless else args.quality, args.compress)
    th = threading.Thread(target=c.loop, daemon=True)
    th.start()
    t = time.perf_counter()
    while not c.updates and time.perf_counter() - t < 10:
        time.sleep(0.01)
    time.sleep(1.0)
    first = c.take()
    print(json.dumps({"server": [c.w, c.h, c.bpp, c.depth], "full_frame_bytes": sum(u[1] for u in first),
                      "full_frame_ms": round((first[-1][0] - t) * 1000, 1) if first else None,
                      "quality": None if args.lossless else args.quality, "compress": args.compress}), flush=True)
    t = time.perf_counter()
    time.sleep(3)
    idle = c.take()
    print(json.dumps({"idle_3s_updates": len(idle), "idle_3s_bytes": sum(u[1] for u in idle)}), flush=True)
    with open(args.actions) as fh:
        actions = json.load(fh)
    for a in actions:
        if a["do"] == "sleep":
            time.sleep(a["s"])
            c.take()
            continue
        for label, fire in run_action(c, a):
            c.take()
            cpu0 = xvnc_cpu()
            t0 = time.perf_counter()
            fire()
            last = t0
            got = []
            small = 0
            while True:
                time.sleep(0.005)
                new = c.take()
                got_all = new
                new = [u for u in new if u[3] >= args.min_area]
                if got_all:
                    small += len(got_all) - len(new)
                if new:
                    got += new
                    last = new[-1][0]
                elif time.perf_counter() - max(last, t0) > args.quiet_ms / 1000:
                    break
                if time.perf_counter() - t0 > 30:
                    break
            rec = {"action": label, "updates": len(got), "bytes": sum(u[1] for u in got),
                   "area_px": sum(u[3] for u in got), "jpeg_rects": sum(u[4] for u in got),
                   "first_ms": round((got[0][0] - t0) * 1000, 1) if got else None,
                   "settled_ms": round((last - t0) * 1000, 1) if got else None,
                   "xvnc_cpu_ms": round((xvnc_cpu() - cpu0) * 1000, 1), "small_updates": small}
            print(json.dumps(rec), flush=True)
            time.sleep(a.get("gap", 0.2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
