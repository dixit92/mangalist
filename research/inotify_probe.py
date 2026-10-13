"""Research lane: does inotify see changes on this filesystem? Modes:
  watch <dir> <seconds>   watch <dir> and its subfolders; print each event kind with its relative name, then a summary
  write <dir> <delay>     (in another process / container) make a few changes in <dir>/Series after <delay> seconds
Uses ctypes only (no packages)."""
import ctypes
import ctypes.util
import os
import select
import struct
import sys
import time
from pathlib import Path

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
FLAGS = {0x100: "CREATE", 0x200: "DELETE", 0x40: "MOVED_FROM", 0x80: "MOVED_TO", 0x2: "MODIFY", 0x8: "CLOSE_WRITE",
         0x4: "ATTRIB"}
MASK = 0x100 | 0x200 | 0x40 | 0x80 | 0x2 | 0x8 | 0x4 | 0x400 | 0x800


def watch(root: Path, seconds: float):
    fd = libc.inotify_init1(os.O_NONBLOCK)
    wd = {}
    t = time.monotonic()
    n = 0
    for dp, dns, fns in os.walk(root):
        w = libc.inotify_add_watch(fd, dp.encode(), MASK)
        if w < 0:
            print("add_watch failed", os.strerror(ctypes.get_errno()), dp)
            continue
        wd[w] = dp
        n += 1
    print(f"watching {n} folders (set up in {time.monotonic() - t:.3f} s)", flush=True)
    end = time.monotonic() + seconds
    seen = []
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        buf = os.read(fd, 65536)
        i = 0
        while i < len(buf):
            w, mask, cookie, ln = struct.unpack_from("iIII", buf, i)
            name = buf[i + 16:i + 16 + ln].rstrip(b"\0").decode()
            kinds = [v for k, v in FLAGS.items() if mask & k]
            rel = os.path.relpath(os.path.join(wd.get(w, "?"), name), root)
            seen.append((kinds, rel))
            print("  event", "+".join(kinds), rel, flush=True)
            i += 16 + ln
    print(f"{len(seen)} events", flush=True)


def write(root: Path, delay: float):
    time.sleep(delay)
    ser = root / "Series"
    (ser / "w1.cbz").write_bytes(b"1")
    os.rename(ser / "w1.cbz", ser / "w2.cbz")
    (ser / "Season 2" / "w3.cbz").write_bytes(b"1")
    os.remove(ser / "w2.cbz")


if __name__ == "__main__":
    if sys.argv[1] == "watch":
        watch(Path(sys.argv[2]), float(sys.argv[3]))
    else:
        write(Path(sys.argv[2]), float(sys.argv[3]))
