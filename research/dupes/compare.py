"""Research lane D (throwaway): compare a whole chapter with its parts from the archive features.

For each overlap: page counts (whole vs sum of parts), the strip height in page widths (sum of h/w), exact page copies
(zip CRC32 + size), and MangaPixer's volume-contents run rule on the 64-bit DCT pHash (pairs within T bits, the longest
chain increasing on both sides, cut at jumps > 5, proven when matched >= 5 and unmatched <= max(3, pages / 4)) in both
directions: the parts inside the whole, and the whole inside the parts. Controls: the whole N against N-1 / N+1.

python3 compare.py jobs.json features.jsonl > compare.json
"""
import bisect
import json
import sys

T, MIN_RUN, MAX_JUMP = 16, 5, 5
jobs = json.load(open(sys.argv[1]))
F = {}
for line in open(sys.argv[2]):
    f = json.loads(line)
    F[f["path"]] = f


def hashes(paths):
    return [p.get("ph") for path in paths for p in F[path]["pages"]]


def dist(a, b):
    return bin(a ^ b).count("1")


def best_run(inner, outer):
    """Matched pages of *inner* (a page-hash list) found in order inside *outer*: MangaPixer's BestRun, simplified."""
    pairs = [(i, j) for i, a in enumerate(inner) if a is not None for j, b in enumerate(outer)
             if b is not None and dist(a, b) <= T]
    if not pairs:
        return 0, []
    pairs.sort(key=lambda p: (p[0], -p[1]))                 # LIS on j with i strictly increasing
    tails, tails_idx, prev = [], [], [None] * len(pairs)
    for k, (i, j) in enumerate(pairs):
        pos = bisect.bisect_left(tails, j)
        if pos == len(tails):
            tails.append(j); tails_idx.append(k)
        else:
            tails[pos] = j; tails_idx[pos] = k
        prev[k] = tails_idx[pos - 1] if pos else None
    chain, k = [], tails_idx[-1] if tails_idx else None
    while k is not None:
        chain.append(pairs[k]); k = prev[k]
    chain.reverse()
    pieces, cur = [], [chain[0]]
    for a, b in zip(chain, chain[1:]):
        if b[0] - a[0] > MAX_JUMP or b[1] - a[1] > MAX_JUMP:
            pieces.append(cur); cur = []
        cur.append(b)
    pieces.append(cur)
    best = max(pieces, key=len)
    return len(best), best


def proves(matched, pages):
    return matched >= MIN_RUN and pages - matched <= max(3, pages / 4.0)


def strip(paths):
    return round(sum(p["h"] / p["w"] for path in paths for p in F[path]["pages"] if p.get("w")), 2)


def any_match(inner, outer):
    return sum(1 for a in inner if a is not None and any(b is not None and dist(a, b) <= T for b in outer))


out = []
for job in jobs:
    whole, parts = job["files"][0], job["files"][1:]
    hw, hp = hashes([whole]), hashes(parts)
    crc_w = {(p["crc"], p["size"]) for p in F[whole]["pages"]}
    crc_p = {(p["crc"], p["size"]) for path in parts for p in F[path]["pages"]}
    m_pw, _ = best_run(hp, hw)            # parts inside the whole
    m_wp, _ = best_run(hw, hp)            # whole inside the parts
    per_part = []
    for path in parts:
        h = hashes([path])
        m, _ = best_run(h, hw)
        per_part.append({"pages": len(h), "in_whole": m, "proven": proves(m, len(h))})
    controls = []
    for c in job["controls"]:
        hc = hashes([c])
        m1, _ = best_run(hw, hc)
        m2, _ = best_run(hp, hc)
        controls.append({"pages": len(hc), "whole_in_ctrl": m1, "parts_in_ctrl": m2,
                         "single_page_hits_whole": any_match(hw, hc)})
    out.append(dict(series_id=job["series_id"], number=job["number"], overlap=job["overlap"],
                    whole_pages=len(hw), parts_pages=[len(hashes([p])) for p in parts], parts_sum=len(hp),
                    whole_strip=strip([whole]), parts_strip=strip(parts),
                    exact_shared=len(crc_w & crc_p),
                    parts_in_whole=m_pw, parts_in_whole_proven=proves(m_pw, len(hp)),
                    whole_in_parts=m_wp, whole_in_parts_proven=proves(m_wp, len(hw)),
                    any_whole_pages_matched=any_match(hw, hp), per_part=per_part, controls=controls,
                    comicinfo=[F[p].get("comicinfo", {}).get("Title") for p in job["files"]],
                    dims_whole=sorted({(p.get("w"), p.get("h")) for p in F[whole]["pages"]})[:3]))
print(json.dumps(out, indent=1))
