"""Page hashes of a chapter archive: MangaPixer's 64-bit perceptual hash of every page, read-only (content verdicts for
the Duplicates view's "Whole chapters with parts", :mod:`mangalist.duplicate_verdicts`; owner, 2026-10-24: "Content
verdicts").

The hash is MangaPixer's ``CoverHash`` as its media worker's ``page_hash`` op computes it (MangaPixer 1.44.0, the
Volume Contents Scan), ported the way research lane D measured it on the owner's library (2026-10-24):

- a **reduced JPEG decode** (``draft("L", (128, 128))``: the decoder scales by 1/2 ... 1/8 while it decodes - about 4x
  cheaper; other formats decode in full);
- the page in luma, resized to 32 x 32 (Lanczos, the aspect ignored);
- a 2-D DCT-II, keeping the 8 x 8 low-frequency block;
- one bit per coefficient, set when it is above the median of the 64 (row-major, the first coefficient the top bit).

Two pages are the same page when their hashes differ in at most :data:`MATCH_BITS` bits (lane D: true matches sat at
0-10 bits, unrelated pages mostly at 18-24; MangaPixer itself uses 16). Besides the hash, each page's zip CRC32 and size
(central directory only) are kept: they prove byte-identical copies.

Pages are the archive's image entries in natural name order (``__MACOSX`` and ``._`` resource files left out). Reading
is guarded like MangaPixer's: at most :data:`MAX_PAGES` pages, :data:`MAX_PAGE_BYTES` per page and
:data:`MAX_ARCHIVE_BYTES` per archive; the file's size and modification time are checked before and after (a file
being written or replaced gives no hashes). Only zip archives (``.cbz`` / ``.zip``, or a ``.cbr`` that is really a zip)
are read; RAR and 7z are "unsupported". Nothing is ever written to the library.

Pillow is needed for the decode. Without it (:func:`pillow_available`) nothing is hashed and the verdicts stay
"undecided" - the section then works from names alone, as before. Pillow is imported on first use only, so this module
is Qt-free and Pillow-free to import (the no-Qt CI job). The DCT is pure Python (about a millisecond a page); the decode
is Pillow's C code.
"""

from __future__ import annotations

import math
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

#: The cache key's algorithm part: a change of the hash (size, filter, decode) must change it, so old rows are not used.
ALGORITHM = "mp-coverhash64-draft128-v1"

MATCH_BITS = 12                         # two pages are one page within this many differing bits (of 64)
MAX_PAGES = 400                         # more image entries than this: not hashed ("too many pages")
MAX_PAGE_BYTES = 64 * 1024 * 1024       # an entry larger than this (uncompressed) is not decoded
MAX_ARCHIVE_BYTES = 4 * 1024 ** 3       # MangaPixer's limit per archive
MAX_UNREAD_SHARE = 0.25                 # more than this share of undecodable pages: the archive is "unreadable"

STATUS_OK = "ok"
STATUS_UNREADABLE = "unreadable"        # content-dependent (a broken zip, pages Pillow cannot decode): cached
STATUS_UNSUPPORTED = "unsupported"      # not a zip, too large, too many pages, no Pillow: never cached

IMAGE = re.compile(r"\.(jpe?g|jfif|png|webp|gif|bmp|tiff?|avif|jxl|heic)$", re.I)
_DRAFT = (128, 128)
_SIDE = 32
_LOW = 8
_COS = [[math.cos((2 * x + 1) * u * math.pi / (2 * _SIDE)) for x in range(_SIDE)] for u in range(_LOW)]


@dataclass(frozen=True)
class ArchivePages:
    """What :func:`hash_archive` read from one archive."""

    status: str                                     # STATUS_*
    hashes: Tuple[Optional[int], ...] = ()          # one per page, natural order; None: that page could not be decoded
    crcs: Tuple[Tuple[int, int], ...] = ()          # one (CRC32, size) per page
    reason: str = ""                                # why not ok, in words
    seconds: float = 0.0

    @property
    def pages(self) -> int:
        return len(self.hashes)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


@dataclass
class _Timer:
    started: float = field(default_factory=time.perf_counter)

    def __call__(self) -> float:
        return round(time.perf_counter() - self.started, 4)


def pillow_available() -> bool:
    """Whether Pillow can be imported here (the page decode needs it)."""
    try:
        import PIL.Image  # noqa: F401
    except Exception:  # noqa: BLE001 - missing, or a broken install: no hashing either way
        return False
    return True


def distance(a: int, b: int) -> int:
    """Differing bits of two 64-bit hashes."""
    return bin((a ^ b) & 0xFFFF_FFFF_FFFF_FFFF).count("1")


def same_page(a: Optional[int], b: Optional[int], bits: int = MATCH_BITS) -> bool:
    return a is not None and b is not None and distance(a, b) <= bits


def hash_luma(pixels: Sequence[float]) -> int:
    """The 64-bit hash of a 32 x 32 luma image given row-major (the DCT and median step - no Pillow needed)."""
    if len(pixels) != _SIDE * _SIDE:
        raise ValueError(f"expected {_SIDE * _SIDE} pixels, got {len(pixels)}")
    rows = []
    for y in range(_SIDE):
        line = pixels[y * _SIDE:(y + 1) * _SIDE]
        rows.append([sum(p * c for p, c in zip(line, cu)) for cu in _COS])
    coefficients = [sum(rows[y][u] * _COS[v][y] for y in range(_SIDE)) for v in range(_LOW) for u in range(_LOW)]
    ordered = sorted(coefficients)
    median = (ordered[31] + ordered[32]) / 2
    value = 0
    for i, c in enumerate(coefficients):
        if c > median:
            value |= 1 << (63 - i)
    return value


def hash_image(image) -> int:
    """The hash of an opened Pillow image (the reduced decode is asked for here, before the pixels are loaded)."""
    from PIL import Image

    try:
        image.draft("L", _DRAFT)                    # JPEG: scale while decoding (MangaPixer's page_hash)
    except Exception:  # noqa: BLE001 - formats without a draft mode decode in full
        pass
    small = image.convert("L").resize((_SIDE, _SIDE), Image.LANCZOS)
    data = small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata()
    return hash_luma(list(data))


def _natural(name: str):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]


def page_entries(archive: zipfile.ZipFile) -> List[zipfile.ZipInfo]:
    """The image entries of *archive* in natural name order (MangaPixer's page order)."""
    out = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        base = name.rsplit("/", 1)[-1]
        if info.is_dir() or not IMAGE.search(base) or base.startswith("._") or "__MACOSX/" in name:
            continue
        out.append(info)
    return sorted(out, key=lambda i: _natural(i.filename))


def _stamp(path) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return int(st.st_size), int(st.st_mtime_ns)


def hash_archive(path, expect: Optional[Tuple[int, int]] = None) -> ArchivePages:
    """Every page's hash and (CRC32, size) of the archive at *path* (module docstring). *expect*: its ``(size,
    mtime_ns)`` as listed; a file that differs, or changes while it is read, gives STATUS_UNSUPPORTED ("changed").
    Never raises for a bad file."""
    timer = _Timer()
    before = _stamp(path)
    if before is None:
        return ArchivePages(STATUS_UNSUPPORTED, reason="the file cannot be read")
    if expect is not None and tuple(expect) != before:
        return ArchivePages(STATUS_UNSUPPORTED, reason="the file changed since it was listed")
    if before[0] > MAX_ARCHIVE_BYTES:
        return ArchivePages(STATUS_UNSUPPORTED, reason="the archive is larger than 4 GiB")
    try:
        is_zip = zipfile.is_zipfile(path)
    except OSError:
        return ArchivePages(STATUS_UNSUPPORTED, reason="the file cannot be read")
    if not is_zip:
        kind = os.path.splitext(str(path))[1].lower().lstrip(".") or "this kind of"
        return ArchivePages(STATUS_UNSUPPORTED, reason=f"{kind} archives are not read (zip / cbz only)")
    if not pillow_available():
        return ArchivePages(STATUS_UNSUPPORTED, reason="Pillow is not installed, so pages cannot be decoded")
    from PIL import Image

    try:
        with zipfile.ZipFile(path) as archive:
            entries = page_entries(archive)
            if not entries:
                return ArchivePages(STATUS_UNREADABLE, reason="the archive holds no image", seconds=timer())
            if len(entries) > MAX_PAGES:
                return ArchivePages(STATUS_UNSUPPORTED, reason=f"more than {MAX_PAGES} pages", seconds=timer())
            hashes: List[Optional[int]] = []
            crcs = []
            for info in entries:
                crcs.append((int(info.CRC) & 0xFFFF_FFFF, int(info.file_size) & 0xFFFF_FFFF))
                value = None
                if info.file_size <= MAX_PAGE_BYTES:
                    try:
                        with archive.open(info) as fh, Image.open(fh) as image:
                            value = hash_image(image)
                    except Exception:  # noqa: BLE001 - an undecodable page (AVIF, JXL, a broken file): counted below
                        value = None
                hashes.append(value)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, RuntimeError, ValueError, NotImplementedError) as exc:
        return ArchivePages(STATUS_UNREADABLE, reason=f"the archive cannot be read ({type(exc).__name__})",
                            seconds=timer())
    if _stamp(path) != before:
        return ArchivePages(STATUS_UNSUPPORTED, reason="the file changed while it was read", seconds=timer())
    unread = sum(1 for h in hashes if h is None)
    if unread == len(hashes) or unread > MAX_UNREAD_SHARE * len(hashes):
        return ArchivePages(STATUS_UNREADABLE, tuple(hashes), tuple(crcs),
                            reason=f"{unread} of {len(hashes)} pages cannot be decoded", seconds=timer())
    return ArchivePages(STATUS_OK, tuple(hashes), tuple(crcs), seconds=timer())


__all__ = ["ALGORITHM", "MATCH_BITS", "MAX_PAGES", "MAX_PAGE_BYTES", "MAX_ARCHIVE_BYTES", "STATUS_OK",
           "STATUS_UNREADABLE", "STATUS_UNSUPPORTED", "ArchivePages", "distance", "hash_archive", "hash_image",
           "hash_luma", "page_entries", "pillow_available", "same_page"]
