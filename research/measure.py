"""Research lane (selective scanning): measurement and equivalence harness. Throwaway; not for merging.

Run inside a throwaway container on a COPY of the library database (MANGALIST_DATA_DIR), library mounted read-only:

    python measure.py timing          full all-root scan (GUI path and headless path), one-root scan, folder scans
    python measure.py equiv           full scan vs selective scan on two perturbed copies, table diff
    python measure.py pending         rename-pending count and duplicates query: all entries vs a few
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from mangalist import scanner, store
from mangalist.identity import moves
from mangalist.scanner import (LibraryScan, record_library_scan, scan_and_record_folders, scan_and_record_library,
                               scan_library, scan_one_root)

DATA = Path(os.environ["MANGALIST_DATA_DIR"])
TIMES: dict = {}


def timed(mod, name):
    orig = getattr(mod, name)

    def wrap(*a, **k):
        t = time.monotonic()
        try:
            return orig(*a, **k)
        finally:
            TIMES[name] = TIMES.get(name, 0.0) + time.monotonic() - t

    setattr(mod, name, wrap)


for _m, _n in ((scanner, "scan_one_root"), (scanner, "record_library_scan"), (scanner, "refresh_mangapixer_pairing"),
               (moves, "record_archives"), (moves, "pair_after_the_fact")):
    timed(_m, _n)


def reset():
    TIMES.clear()


def show(label, t0):
    tot = time.monotonic() - t0
    parts = ", ".join(f"{k}={v:.1f}s" for k, v in TIMES.items())
    print(f"{label}: {tot:.1f} s  ({parts})", flush=True)
    reset()
    return tot


def db_open():
    return store.get_store()


def largest_root(db):
    counts = {}
    for s in db.list_series():
        if s.status == "present":
            counts[s.root_id] = counts.get(s.root_id, 0) + 1
    rid = max(counts, key=counts.get)
    return next(r for r in db.list_roots() if r.id == rid), counts


def tops_of(db, root, n, seed=7):
    rows = [s.rel_path.split("/", 1)[0] for s in db.list_series(root.id) if s.status == "present"]
    rows = sorted(set(rows))
    random.Random(seed).shuffle(rows)
    return rows[:n]


def cmd_timing():
    db = db_open()
    roots = db.list_roots()
    big, counts = largest_root(db)
    print(f"roots={len(roots)} series={sum(counts.values())} largest root series={counts[big.id]}", flush=True)
    for rep in (1, 2):
        t = time.monotonic()
        res = scan_and_record_library(roots, db)
        show(f"[{rep}] GUI-style all-root scan+record ({len(res.entries)} series, "
             f"{sum(len(e.files) for e in res.entries)} archives)", t)
    for rep in (1, 2):
        t = time.monotonic()
        res = scan_library(roots, db=db)
        w = time.monotonic() - t
        record_library_scan(db, res)
        show(f"[{rep}] headless-style all-root (walk all, record once) walk={w:.1f}s", t)
    for rep in (1, 2):
        t = time.monotonic()
        scan_and_record_library([big], db)
        show(f"[{rep}] one-root (largest, {counts[big.id]} series) scan+record", t)
    tops = tops_of(db, big, 12)
    for top in tops[:4]:
        t = time.monotonic()
        rs = scan_and_record_folders(big, [top], db)
        n = sum(len(e.files) for e in rs.entries)
        show(f"folder scan 1 folder ({len(rs.entries)} entries, {n} archives)", t)
    for k in (5, 12):
        t = time.monotonic()
        rs = scan_and_record_folders(big, tops[:k], db)
        n = sum(len(e.files) for e in rs.entries)
        show(f"folder scan {k} folders ({len(rs.entries)} entries, {n} archives)", t)
    # walk only: one folder, no record
    t = time.monotonic()
    rs = scan_one_root(big, db=db, only=tops[:1])
    show("folder walk only (1 folder, no record)", t)
    # the root-level listing + stat of every top-level folder (what an external-change check would read)
    t = time.monotonic()
    n = 0
    for r in roots:
        for p in Path(r.path).iterdir():
            try:
                p.stat()
                n += 1
            except OSError:
                pass
    print(f"listing + stat of {n} children of {len(roots)} roots: {time.monotonic() - t:.2f} s", flush=True)
    t = time.monotonic()
    n = 0
    for r in roots:
        for p in Path(r.path).iterdir():
            if p.is_dir():
                for q in os.scandir(p):
                    n += 1
    print(f"one-level listing of every series folder ({n} entries): {time.monotonic() - t:.2f} s", flush=True)
    t = time.monotonic()
    n = d = 0
    for r in roots:
        for dp, dns, fns in os.walk(r.path):
            d += 1
            n += len(fns)
    print(f"os.walk of every root, names only ({d} dirs, {n} files): {time.monotonic() - t:.2f} s", flush=True)
    t = time.monotonic()
    n = 0
    for r in roots:
        for dp, dns, fns in os.walk(r.path):
            for dn in dns:
                os.stat(os.path.join(dp, dn))
                n += 1
    print(f"stat of every directory ({n} dirs): {time.monotonic() - t:.2f} s", flush=True)


def cmd_diffpass():
    """The cheap detection: walk + stat every archive, parse nothing, compare with the database's archive rows."""
    from mangalist.scanner import changed_top_folders, scan_changed
    db = db_open()
    roots = db.list_roots()
    scan_and_record_library(roots, db)             # settle
    for rep in (1, 2, 3):
        t = time.monotonic()
        tot = 0
        dirty = 0
        for r in roots:
            names, n = changed_top_folders(db, r)
            tot += n
            dirty += len(names)
        print(f"[{rep}] diff pass over all roots, settled database: {time.monotonic() - t:.1f} s "
              f"({tot} archives stat'ed, {dirty} changed folders)", flush=True)
    big, _c = largest_root(db)
    t = time.monotonic()
    names, n = changed_top_folders(db, big)
    print(f"diff pass over the largest root: {time.monotonic() - t:.1f} s ({n} archives, {len(names)} changed)", flush=True)


def cmd_pending():
    from mangalist.duplicates import find_duplicate_files
    from mangalist.renamer import Renamer

    db = db_open()
    roots = db.list_roots()
    res = scan_library(roots, db=db)
    entries = res.entries
    rn = Renamer(db)
    for rep in (1, 2):
        t = time.monotonic()
        out = rn.pending_counts(entries)
        print(f"[{rep}] pending_counts over {len(entries)} entries: {time.monotonic() - t:.1f} s ({len(out)} pending)", flush=True)
    sub = entries[:5]
    t = time.monotonic()
    rn.pending_counts(sub)
    print(f"pending_counts over 5 entries: {time.monotonic() - t:.2f} s", flush=True)
    for rep in (1, 2):
        t = time.monotonic()
        g = find_duplicate_files(db)
        print(f"[{rep}] find_duplicate_files(all roots): {time.monotonic() - t:.1f} s ({len(g)} groups)", flush=True)


# --- equivalence -------------------------------------------------------------------------------------------------

def dump(path: Path) -> dict:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    sid = {r["id"]: (r["root_id"], r["rel_path"]) for r in con.execute("SELECT id, root_id, rel_path FROM series")}
    out = {}
    out["series"] = sorted(json.dumps([r["root_id"], r["rel_path"], r["fingerprint"], r["n_archives"], r["mu_id"],
                                       r["mu_confirmed"], r["status"], r["kind_hint"]])
                           for r in con.execute("SELECT * FROM series"))
    out["archives"] = sorted(json.dumps([r["root_id"], r["rel_path"], sid.get(r["series_id"]), r["size"], r["mtime_ns"],
                                         r["signature"], r["status"]]) for r in con.execute("SELECT * FROM archives"))
    out["units"] = sorted(json.dumps([sid.get(r["series_id"]), r["rel_path"], r["seq"], r["kind"], r["vol_from"],
                                      r["vol_to"], r["ch_from"], r["ch_to"], r["group_name"], r["title"], r["idx"],
                                      r["parser"], r["file_size"]]) for r in con.execute("SELECT * FROM units"))
    out["archive_moves"] = sorted(json.dumps([r["from_root_id"], r["from_path"], r["to_root_id"], r["to_path"], r["how"]])
                                  for r in con.execute("SELECT * FROM archive_moves"))
    out["series_carries"] = sorted(json.dumps([r["from_path"], r["to_path"], r["how"], r["moved"], r["kept"]])
                                   for r in con.execute("SELECT * FROM series_carries"))
    out["links_cache"] = sorted(json.dumps(list(tuple(r))) for r in con.execute("SELECT * FROM links_cache"))
    con.close()
    return out


def touch_scope(rows, scope):
    return [r for r in rows if json.loads(r)[1].split("/", 1)[0] in scope or
            (isinstance(json.loads(r)[1], str) is False)]


def perturb(path: Path, root_id: int, tops: list) -> dict:
    """Make the database stale against disk for six folders; returns {kind: folder or names}."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    done = {}

    def arch(top):
        return con.execute("SELECT id FROM archives WHERE root_id=? AND (rel_path LIKE ? ESCAPE '\\')",
                           (root_id, top.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%")).fetchall()

    t1, t2, t3, t4, t5, t6 = tops[:6]
    ids = [r["id"] for r in arch(t1)][:2]                       # two archives unknown to the database: "new files"
    con.executemany("DELETE FROM archives WHERE id=?", [(i,) for i in ids])
    done["new_files"] = t1
    ids = [r["id"] for r in arch(t2)][:1]                       # one archive of another size: "changed in place"
    con.executemany("UPDATE archives SET size=size+1 WHERE id=?", [(i,) for i in ids])
    done["changed"] = t2
    sid = con.execute("SELECT id FROM series WHERE root_id=? AND rel_path=?", (root_id, t3)).fetchone()
    if sid is not None:                                          # a file the database holds but disk does not: "removed"
        con.execute("INSERT INTO archives (root_id, series_id, rel_path, size, mtime_ns, signature, status, first_seen_at,"
                    " last_seen_at, first_seen_scan, last_seen_scan) VALUES (?,?,?,?,?,?, 'present', 'x', 'x', 1, 1)",
                    (root_id, sid["id"], f"{t3}/zz removed file.cbz", 1234567, 1, None))
    done["removed_file"] = t3
    row = con.execute("SELECT id FROM series WHERE root_id=? AND rel_path=?", (root_id, t4)).fetchone()
    if row is not None:                                          # a series folder unknown to the database: "new folder"
        con.execute("DELETE FROM archives WHERE series_id=?", (row["id"],))
        con.execute("DELETE FROM series WHERE id=?", (row["id"],))
    done["new_folder"] = t4
    old5 = t5 + " (old)"                                         # the database knows the folder under an older name
    con.execute("UPDATE series SET rel_path=? WHERE root_id=? AND rel_path=?", (old5, root_id, t5))
    con.execute("UPDATE archives SET rel_path=? || substr(rel_path, ?) WHERE root_id=? AND substr(rel_path,1,?)=?",
                (old5, len(t5) + 1, root_id, len(t5) + 1, t5 + "/"))
    done["renamed_folder"] = [t5, old5]
    gone = "Zz Vanished Folder"                                  # a folder the database knows and disk does not
    con.execute("INSERT INTO series (root_id, rel_path, fingerprint, n_archives, status, first_seen_at, last_seen_at)"
                " VALUES (?,?,NULL,1,'present','x','x')", (root_id, gone))
    s = con.execute("SELECT id FROM series WHERE root_id=? AND rel_path=?", (root_id, gone)).fetchone()["id"]
    con.execute("INSERT INTO archives (root_id, series_id, rel_path, size, mtime_ns, signature, status, first_seen_at,"
                " last_seen_at, first_seen_scan, last_seen_scan) VALUES (?,?,?,?,?,NULL,'present','x','x',1,1)",
                (root_id, s, f"{gone}/v01.cbz", 999, 1))
    done["vanished_folder"] = gone
    con.commit()
    con.close()
    return done


def run_with_data(path: Path, fn):
    os.environ["MANGALIST_DATA_DIR"] = str(path)
    from mangalist import paths as _p
    store._STORES.clear() if hasattr(store, "_STORES") else None
    return fn()


def cmd_equiv(second_scope="both"):
    base = DATA
    db = db_open()
    big, _c = largest_root(db)
    roots = db.list_roots()
    print("settling: one full scan so the database matches the disk now", flush=True)
    scan_and_record_library(roots, db)
    tops = tops_of(db, big, 8, seed=11)
    base_db = base / "mangalist.db"
    results = {}
    for tag in ("A", "B", "C"):
        d = base.parent / f"copy{tag}"
        d.mkdir(exist_ok=True)
        shutil.copy(base_db, d / "mangalist.db")
    done = None
    for tag in ("A", "B", "C"):
        done = perturb(base.parent / f"copy{tag}" / "mangalist.db", big.id, tops)
    print("perturbed:", {k: ("<names>" if isinstance(v, str) else "<names>") for k, v in done.items()}, flush=True)
    scope_names = {done["new_files"], done["changed"], done["removed_file"], done["new_folder"], *done["renamed_folder"],
                   done["vanished_folder"]}
    # A: full scan of every root
    t = time.monotonic()
    code = f"""
import os, sys, time
os.environ['MANGALIST_DATA_DIR']='{base.parent}/copyA'
from mangalist import store, scanner
db=store.get_store(); t=time.monotonic()
scanner.scan_and_record_library(db.list_roots(), db)
print('A full scan', round(time.monotonic()-t,1),'s')
"""
    import subprocess
    env = dict(os.environ)
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
    code = f"""
import os, sys, time, json
os.environ['MANGALIST_DATA_DIR']='{base.parent}/copyB'
from mangalist import store, scanner
db=store.get_store(); t=time.monotonic()
root=next(r for r in db.list_roots() if r.id=={big.id})
names={sorted(scope_names)!r}
rs=scanner.scan_and_record_folders(root, names, db)
print('B selective scan of', len(names), 'folders', round(time.monotonic()-t,1),'s;', len(rs.entries),'entries')
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
    renamed_new = done["renamed_folder"][0]
    code = f"""
import os, sys, time, json
os.environ['MANGALIST_DATA_DIR']='{base.parent}/copyC'
from mangalist import store, scanner
db=store.get_store()
root=next(r for r in db.list_roots() if r.id=={big.id})
names={sorted(scope_names - {done['renamed_folder'][1]})!r}
rs=scanner.scan_and_record_folders(root, names, db)
print('C selective scan, rename source NOT named:', len(names), 'folders')
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
    A, B, C = (dump(base.parent / f"copy{t}" / "mangalist.db") for t in "ABC")
    P = dump(base_db)

    def compare(x, y, label):
        print(f"--- {label}")
        for table in x:
            sx, sy = set(x[table]), set(y[table])
            only_x, only_y = sorted(sx - sy), sorted(sy - sx)
            print(f"  {table}: {len(sx)} vs {len(sy)} rows; only-left {len(only_x)}, only-right {len(only_y)}")
            for r in only_x[:3]:
                print("     L:", r[:150])
            for r in only_y[:3]:
                print("     R:", r[:150])

    compare(A, B, "full scan (A) vs selective scan naming both folder names (B)")
    compare(A, C, "full scan (A) vs selective scan missing the rename source (C)")


def cmd_equiv_plain():
    cmd_equiv()


if __name__ == "__main__":
    {"timing": cmd_timing, "pending": cmd_pending, "equiv": cmd_equiv, "diffpass": cmd_diffpass}[sys.argv[1]]()
