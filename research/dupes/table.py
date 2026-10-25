"""Research lane D (throwaway): one row per overlap - names' chapter titles, content verdict, provenance - as TSV.

python3 table.py sections.json verdicts.json db_copy downloads.json > table.tsv
(downloads.json: [[series_id, number], ...] of the ledger records whose number sits next to the other form.)
"""
import json
import re
import sqlite3
import sys

sections, verdicts, db_path, dl = sys.argv[1:5]
S = json.load(open(sections))["overlaps"]
V = {(v["series_id"], v["number"]): v for v in json.load(open(verdicts))}
D = {tuple(x) for x in json.load(open(dl))}
c = sqlite3.connect(db_path)
root = {r[0]: r[1] for r in c.execute("SELECT s.id, r.name FROM series s JOIN roots r ON r.id = s.root_id")}
PART = re.compile(r"(?i)\b(part|pt\.?)\s*\d|\[revised\]|revised")


def title(sid, path):
    rel = path.split("/")[-1]
    row = c.execute("SELECT title FROM units WHERE series_id=? AND rel_path LIKE ?", (sid, "%" + rel)).fetchone()
    return (row[0] or "") if row else ""


print("\t".join(["root", "series", "ch", "rule", "titles", "names_say", "pages", "cov_whole", "verdict", "downloaded"]))
for g in S:
    v = V[(g["series_id"], g["number"])]
    ts = [title(g["series_id"], f["path"]) for f in g["files"]]
    base = [re.sub(r"(?i)\s*[\[(]?\b(part|pt\.?)\s*\d+[\])]?|\s*\[revised\]|\s*\d+$|\.\d$", "", t).strip().casefold() for t in ts]
    if not any(ts):
        says = "no titles"
    elif all(ts) and len(set(base)) == 1:
        says = "same title"
    elif all(ts) and len(set(base)) == len(base):
        says = "all titles differ"
    else:
        says = "mixed"
    if any(PART.search(t) for t in ts):
        says += " +part/revised marker"
    pages = f"{v['whole_pages']} vs {'+'.join(map(str, v['parts_pages']))}"
    print("\t".join([root[g["series_id"]], g["title"][:60], g["number"], g["overlap"], " | ".join(ts)[:160], says, pages,
                     str(v["cov_whole"]), v["verdict"], "yes" if (g["series_id"], g["number"]) in D else ""]))
