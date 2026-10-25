"""Research lane D (throwaway): a content verdict per overlap from the archive features (prototype of the proposed rule).

Signals: per-page pHash (MangaPixer's 64-bit DCT hash), matched within T bits; pages that also match a neighbouring
chapter (credit / recruitment pages) are left out; zip CRC32 + size for exact page copies.
Verdicts: copy (one part is the whole again), whole=parts, distinct (no shared content), partial (some), and
"neighbour" (the whole file is another chapter's pages).

python3 verdict.py jobs.json features.jsonl [T] > verdicts.json
"""
import json
import sys

jobs = json.load(open(sys.argv[1]))
T = int(sys.argv[3]) if len(sys.argv) > 3 else 12
F = {}
for line in open(sys.argv[2]):
    f = json.loads(line)
    F[f["path"]] = f


def H(path):
    return [p.get("ph") for p in F[path]["pages"]]


def near(a, hs):
    return a is not None and any(b is not None and bin(a ^ b).count("1") <= T for b in hs)


out = []
for j in jobs:
    whole, parts = j["files"][0], j["files"][1:]
    ctrl = [h for c in j["controls"] for h in H(c)]
    hw = H(whole)
    hp_all = [h for p in parts for h in H(p)]
    credit_w = [near(a, ctrl) for a in hw]
    w_idx = [i for i, a in enumerate(hw) if not credit_w[i]]
    cov_whole = sum(1 for i in w_idx if near(hw[i], hp_all)) / max(1, len(w_idx))
    per_part = []
    crc_w = {(p["crc"], p["size"]) for p in F[whole]["pages"]}
    for p in parts:
        hs = H(p)
        idx = [i for i, a in enumerate(hs) if not near(a, ctrl)]
        cov = sum(1 for i in idx if near(hs[i], hw)) / max(1, len(idx))
        back = sum(1 for i in w_idx if near(hw[i], hs)) / max(1, len(w_idx))
        exact = sum(1 for q in F[p]["pages"] if (q["crc"], q["size"]) in crc_w) / max(1, len(hs))
        per_part.append(dict(pages=len(hs), in_whole=round(cov, 2), whole_in_it=round(back, 2), exact=round(exact, 2)))
    neighbour = max([sum(1 for a in hw if near(a, H(c))) / max(1, len(hw)) for c in j["controls"]] + [0])
    if neighbour >= 0.7:
        verdict = "neighbour"
    elif any(pp["in_whole"] >= 0.85 and pp["whole_in_it"] >= 0.85 for pp in per_part):
        verdict = "copy"
    elif cov_whole >= 0.7 and all(pp["in_whole"] >= 0.6 for pp in per_part):
        verdict = "whole=parts"
    elif cov_whole <= 0.15 and all(pp["in_whole"] <= 0.15 for pp in per_part):
        verdict = "distinct"
    else:
        verdict = "partial"
    out.append(dict(series_id=j["series_id"], number=j["number"], overlap=j["overlap"], verdict=verdict,
                    cov_whole=round(cov_whole, 2), whole_pages=len(hw), parts_pages=[len(H(p)) for p in parts],
                    credit_pages=sum(credit_w), neighbour=round(neighbour, 2), per_part=per_part))
print(json.dumps(out, indent=1))
