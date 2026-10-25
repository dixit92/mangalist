"""Research lane D (throwaway): read CBZ archives READ-ONLY and print their content features as JSON lines.

Per archive: the image entries (natural order) with their zip CRC32 and size (central directory only), ComicInfo.xml
fields, each page's dimensions (image header only), and a 64-bit dHash (9x8) and MangaPixer's 64-bit DCT pHash per page (reduced JPEG decode) - with the
time each stage took. Run with -I in a throwaway container, the library mounted read-only, Pillow on sys.path.

python -I archive_features.py jobs.json pillow_dir > features.jsonl
"""
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

jobs_path, pillow_dir = sys.argv[1:3]
sys.path.insert(0, pillow_dir)
from PIL import Image  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
IMG = re.compile(r"\.(jpe?g|png|webp|gif|bmp|avif|jxl)$", re.I)
FIELDS = ("Title", "Series", "Number", "Volume", "Count", "PageCount", "Web", "Translator", "ScanInformation",
          "Summary", "Notes", "LanguageISO")


def natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def dhash(im):
    g = im.convert("L").resize((9, 8), Image.BILINEAR)
    px = list(g.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
    return bits


import math
_COS = [[math.cos((2 * x + 1) * u * math.pi / 64) for x in range(32)] for u in range(8)]


def phash(im):
    """MangaPixer's CoverHash: 32x32 luma (aspect ignored), DCT-II, the 8x8 low block, bit = above the median."""
    px = list(im.convert("L").resize((32, 32), Image.LANCZOS).getdata())
    rows = [[sum(px[y * 32 + x] * _COS[u][x] for x in range(32)) for u in range(8)] for y in range(32)]
    co = [sum(rows[y][u] * _COS[v][y] for y in range(32)) for v in range(8) for u in range(8)]
    srt = sorted(co)
    med = (srt[31] + srt[32]) / 2
    h = 0
    for i, c in enumerate(co):
        if c > med:
            h |= 1 << (63 - i)
    return h


def features(path):
    out = {"path": path}
    t0 = time.perf_counter()
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    with z:
        infos = sorted((i for i in z.infolist() if not i.is_dir() and IMG.search(i.filename)), key=lambda i: natural(i.filename))
        out["entries"] = len(z.infolist())
        out["pages"] = [{"name": i.filename, "crc": i.CRC, "size": i.file_size} for i in infos]
        out["t_list"] = time.perf_counter() - t0
        t1 = time.perf_counter()
        ci = {}
        for i in z.infolist():
            if i.filename.lower().endswith("comicinfo.xml"):
                try:
                    root = ET.fromstring(z.read(i))
                    for f in FIELDS:
                        el = root.find(f)
                        if el is not None and el.text:
                            ci[f] = el.text.strip()[:300]
                except Exception as e:  # noqa: BLE001
                    ci["error"] = str(e)
        out["comicinfo"] = ci
        out["t_comicinfo"] = time.perf_counter() - t1
        t2 = time.perf_counter()
        for p, i in zip(out["pages"], infos):
            try:
                with z.open(i) as fh:
                    im = Image.open(fh)
                    p["w"], p["h"] = im.size
            except Exception as e:  # noqa: BLE001
                p["err"] = type(e).__name__
        out["t_dims"] = time.perf_counter() - t2
        t3 = time.perf_counter()
        for p, i in zip(out["pages"], infos):
            if "err" in p:
                continue
            try:
                with z.open(i) as fh:
                    im = Image.open(fh)
                    im.draft("L", (128, 128))        # JPEG: reduced decode, as MangaPixer's page_hash
                    im.load()
                    p["dh"] = dhash(im)
                    p["ph"] = phash(im)
            except Exception as e:  # noqa: BLE001
                p["err"] = type(e).__name__
        out["t_hash"] = time.perf_counter() - t3
    return out


seen = set()
for job in json.load(open(jobs_path)):
    for path in job["files"] + job["controls"]:
        if path in seen:
            continue
        seen.add(path)
        print(json.dumps(features(path)), flush=True)
