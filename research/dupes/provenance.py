"""Research lane D (throwaway): where each file of the listed overlaps came from - from a database COPY.

For each file: the archives row (first seen; the first full scans are the library as it was), the ledger record that
filed it (tool, backend, source, scanlator, chapter name/url, date), and the journal plans that moved or renamed it
(followed back through renames). python3 provenance.py sections.json db_copy > provenance.json
"""
import json
import os
import sqlite3
import sys

sections, db_path = sys.argv[1:3]
d = json.load(open(sections))
c = sqlite3.connect(db_path)
c.row_factory = sqlite3.Row
roots = [dict(r) for r in c.execute("SELECT id, path FROM roots")]
filed = {}
for r in c.execute("SELECT * FROM ledger WHERE status IN ('filed','removed','sent')"):
    for name in json.loads(r["filed_files"] or "[]"):
        filed.setdefault(os.path.join(r["destination"] or "", name), []).append(r)
steps_by_dst = {}
for s in c.execute("SELECT s.src, s.dst, s.state, p.reason, p.created_at, p.id AS plan FROM journal_steps s "
                   "JOIN journal_plans p ON p.id = s.plan_id WHERE s.is_dir = 0"):
    steps_by_dst.setdefault(s["dst"], []).append(dict(s))


def archive(path):
    for r in roots:
        if path.startswith(r["path"].rstrip("/") + "/"):
            row = c.execute("SELECT first_seen_at, size, signature FROM archives WHERE root_id=? AND rel_path=?",
                            (r["id"], path[len(r["path"].rstrip("/")) + 1:])).fetchone()
            return dict(row) if row else None


def history(path, depth=0):
    out = []
    for s in steps_by_dst.get(path, []):
        out.append({k: s[k] for k in ("reason", "created_at", "state", "plan")} | {"src": s["src"]})
        if depth < 6 and s["src"] != path:
            out += history(s["src"], depth + 1)
    return out


def ledger_for(path, hist):
    for p in [path] + [h["src"] for h in hist]:
        for r in filed.get(p, []):
            q = json.loads(r["request"])
            return dict(id=r["id"], created_at=r["created_at"], status=r["status"], backend=r["backend"],
                        source=q.get("source_name"), scanlator=q.get("scanlator"), chapter_name=q.get("chapter_name"),
                        chapter_url=q.get("chapter_url"), wanted=q.get("wanted_chapters"), batch=q.get("batch"))
    return None


out = []
for g in d["overlaps"] + d["duplicates"]:
    files = []
    for f in g["files"]:
        h = history(f["path"])
        files.append(dict(path=f["path"], size=f["size"], modified=f["modified"], archive=archive(f["path"]),
                          history=h, ledger=ledger_for(f["path"], h)))
    out.append(dict(section="overlap" if g.get("overlap") else "duplicate", series_id=g["series_id"],
                    number=g["number"], kind=g["kind"], overlap=g.get("overlap"), files=files))
print(json.dumps(out, indent=1))
