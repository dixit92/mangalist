"""Research lane D (throwaway): approximate the whole-and-parts count over time from the archives table of a database
COPY (first_seen_at / missing_since), with the split-model rule on scheme names ("Ch. NNNN.NN") only.

python3 history.py db_copy
"""
import re
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal

MARK = re.compile(r"(?i)(?<![a-z])(extras?|omake|specials?|side[\s_-]*stor(y|ies)|bonus|afterword)(?![a-z])(?!\s*edition)")
NUM = re.compile(r"Ch\. 0*(\d+)\.(\d\d)(?![\d-])")
c = sqlite3.connect(sys.argv[1])
rows = []
for root, rel, first, gone in c.execute("SELECT root_id, rel_path, first_seen_at, missing_since FROM archives"):
    m = NUM.search(rel.rsplit("/", 1)[-1])
    if m and not re.search(r"Ch\. \d+(\.\d+)?-", rel):
        rows.append((root, rel.rsplit("/", 1)[0], Decimal(m.group(1)) + Decimal(m.group(2)) / 100, first, gone,
                     bool(MARK.search(rel.rsplit("/", 1)[-1]))))


def overlaps(t):
    folders = defaultdict(lambda: defaultdict(list))
    for root, folder, n, first, gone, extra in rows:
        if first <= t and (gone is None or gone > t):
            folders[(root, folder)][n].append(extra)
    out = set()
    for key, nums in folders.items():
        for n in [x for x in nums if x == int(x)]:
            ts = sorted(int((p - n) * 10) for p in nums if int(p) == n and p != n and (p - n) * 10 == int((p - n) * 10))
            ts = [x for x in ts if x < 5 or 4 in ts]
            ts = [x for x in ts if not all(nums[n + Decimal(x) / 10])]
            if any(len(nums[q]) > 1 for q in [n] + [n + Decimal(x) / 10 for x in ts]):
                continue
            if ts and ((ts[0] == 2) or (ts[0] == 1 and len(ts) > 1)):
                out.add((key, n))
    return out


prev = set()
for day in ["2026-10-08", "2026-10-10", "2026-10-12", "2026-10-14", "2026-10-16", "2026-10-18", "2026-10-19", "2026-10-21",
            "2026-10-23", "2026-10-24T23:59", "2026-10-25T23:59"]:
    now = overlaps(day)
    print(day, len(now), "series", len({k for k, _ in now}), "+%d -%d" % (len(now - prev), len(prev - now)))
    prev = now
