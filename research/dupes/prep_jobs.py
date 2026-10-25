"""Research lane D (throwaway): from the listed overlaps (sections.json) and a database COPY, write the analysis jobs:
each overlap's files plus a control - the whole chapters N-1 and N+1 of the same folder, when held.

python3 prep_jobs.py sections.json db_copy jobs.json
"""
import json
import os
import sqlite3
import sys
from decimal import Decimal, InvalidOperation

sections, db_path, out = sys.argv[1:4]
d = json.load(open(sections))
con = sqlite3.connect(db_path)
jobs = []
for g in d["overlaps"]:
    n = Decimal(g["number"])
    folder = os.path.dirname(g["files"][0]["path"])
    rel_dir = os.path.relpath(folder, g["folder"])
    rel_dir = "" if rel_dir == "." else rel_dir + "/"
    controls = []
    for row in con.execute("SELECT rel_path, ch_from, ch_to FROM units WHERE series_id=? AND kind='chapter'", (g["series_id"],)):
        try:
            a = Decimal(row[1])
        except (InvalidOperation, TypeError):
            continue
        if row[2] not in (None, row[1]):
            continue
        if a in (n - 1, n + 1) and os.path.dirname(row[0]) + ("/" if os.path.dirname(row[0]) else "") == rel_dir:
            controls.append(os.path.join(g["folder"], row[0]))
    jobs.append(dict(series_id=g["series_id"], number=g["number"], overlap=g["overlap"],
                     files=[f["path"] for f in g["files"]], controls=sorted(set(controls))))
json.dump(jobs, open(out, "w"), indent=1)
print(len(jobs), "jobs", sum(len(j["controls"]) for j in jobs), "controls")
