"""Research lane: does a change inside a series folder move the folder's mtime on this filesystem, and does inotify
see it? Works only inside the scratch folder it is given (never the library). Usage: fsprobe.py <scratch> [<direct>]
<scratch> is the path through the shfs mount; <direct> (optional) is the same folder through the disk's own mount."""
import ctypes
import ctypes.util
import os
import struct
import sys
import time
from pathlib import Path

S = Path(sys.argv[1]) / "probe"
D = Path(sys.argv[2]) / "probe" if len(sys.argv) > 2 else None


def ns(p):
    return os.stat(p).st_mtime_ns


def snap(*ps):
    return {str(p.relative_to(S)) or ".": ns(p) for p in ps}


def changed(label, before, after):
    ch = [k for k in before if before[k] != after[k]]
    print(f"  {label:<46} dir mtime changed: {', '.join(ch) if ch else '(none)'}", flush=True)


def reset():
    import shutil
    if S.exists():
        shutil.rmtree(S)
    (S / "Series" / "Season 2" / "deep").mkdir(parents=True)
    for n in ("a.cbz", "b.cbz"):
        (S / "Series" / n).write_bytes(b"x" * 100)
    (S / "Series" / "Season 2" / "c.cbz").write_bytes(b"y" * 100)
    (S / "Series" / "Season 2" / "deep" / "d.cbz").write_bytes(b"z" * 100)


def watched():
    return [S, S / "Series", S / "Series" / "Season 2", S / "Series" / "Season 2" / "deep"]


def step(label, fn):
    time.sleep(1.1)       # keep the steps apart
    b = snap(*watched())
    fn()
    a = snap(*watched())
    changed(label, b, a)


def main():
    print("filesystem:", os.popen(f"stat -f -c %T {S.parent}").read().strip(), flush=True)
    reset()
    ser = S / "Series"
    print("mtime resolution of the series folder (ns):", ns(ser), flush=True)
    step("create a file in the series folder", lambda: (ser / "new.cbz").write_bytes(b"1" * 10))
    step("modify a file's bytes in place", lambda: open(ser / "a.cbz", "r+b").write(b"Q"))
    step("append to a file", lambda: open(ser / "a.cbz", "ab").write(b"Q"))
    step("touch (utime) a file", lambda: os.utime(ser / "a.cbz", None))
    step("rename a file inside the series folder", lambda: os.rename(ser / "a.cbz", ser / "a renamed.cbz"))
    step("delete a file in the series folder", lambda: os.remove(ser / "b.cbz"))
    step("create a file in Season 2", lambda: (ser / "Season 2" / "e.cbz").write_bytes(b"1"))
    step("create a file 2 levels down (deep)", lambda: (ser / "Season 2" / "deep" / "f.cbz").write_bytes(b"1"))
    step("delete a file 2 levels down (deep)", lambda: os.remove(ser / "Season 2" / "deep" / "f.cbz"))
    step("move a file from series folder to Season 2", lambda: os.rename(ser / "new.cbz", ser / "Season 2" / "new.cbz"))
    step("rename the Season 2 folder", lambda: os.rename(ser / "Season 2", ser / "Season 2x"))
    step("rename it back", lambda: os.rename(ser / "Season 2x", ser / "Season 2"))
    step("create a new series folder in the root", lambda: (S / "Another").mkdir())
    step("create a file in the new series folder", lambda: (S / "Another" / "x.cbz").write_bytes(b"1"))
    step("rename the series folder", lambda: os.rename(S / "Another", S / "Another renamed"))
    step("delete the (non-empty) series folder", lambda: __import__("shutil").rmtree(S / "Another renamed"))

    # two changes inside one second
    reset()
    time.sleep(1.1)
    ser = S / "Series"
    m0 = ns(ser)
    (ser / "n1.cbz").write_bytes(b"1")
    m1 = ns(ser)
    (ser / "n2.cbz").write_bytes(b"1")
    m2 = ns(ser)
    print(f"  two creates back to back: mtime moved after the first: {m1 != m0}, after the second: {m2 != m1}", flush=True)

    if D is not None:
        print("changes made through the disk's own path (bypassing shfs), seen through the shfs path:", flush=True)
        reset()
        time.sleep(1.1)
        ser, dser = S / "Series", D / "Series"
        before = ns(ser)
        (dser / "direct.cbz").write_bytes(b"1")
        seen = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            if ns(ser) != before:
                seen = time.monotonic() - t0
                break
            time.sleep(0.05)
        print(f"  new file via the disk path: series folder mtime via shfs changed after "
              f"{'%.2f s' % seen if seen is not None else '> 30 s (never)'}; listed via shfs at once: "
              f"{(ser / 'direct.cbz').exists()}", flush=True)
        before = ns(ser)
        time.sleep(1.1)
        os.rename(dser / "direct.cbz", dser / "direct2.cbz")
        t0 = time.monotonic()
        seen = None
        while time.monotonic() - t0 < 30:
            if ns(ser) != before:
                seen = time.monotonic() - t0
                break
            time.sleep(0.05)
        print(f"  rename via the disk path: mtime via shfs changed after {'%.2f s' % seen if seen is not None else '> 30 s (never)'}; "
              f"new name listed at once: {(ser / 'direct2.cbz').exists()}", flush=True)


if __name__ == "__main__":
    main()
