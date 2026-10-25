"""Synthetic manga pages and chapter archives for the page-hash and content-verdict tests: grey shapes and lines on a
white page, one seed per page, so a page can be made again (the same page), re-encoded or resized (another group's
release of it) or made different (another seed). Needs Pillow (tests skip without it). No real pages."""

from __future__ import annotations

import io
import random
import zipfile
from pathlib import Path
from typing import Iterable, Optional, Sequence

from PIL import Image, ImageDraw

SIZE = (720, 1040)


def page(seed: int, size=SIZE) -> Image.Image:
    """A page drawn from *seed*: panels, blobs and speech-bubble-like ellipses in greys."""
    rnd = random.Random(seed)
    w, h = size
    im = Image.new("L", size, 255)
    d = ImageDraw.Draw(im)
    for _ in range(rnd.randrange(10, 16)):
        x0, y0 = rnd.randrange(0, w - 40), rnd.randrange(0, h - 40)
        x1, y1 = min(w, x0 + rnd.randrange(40, w // 2)), min(h, y0 + rnd.randrange(40, h // 2))
        shade = rnd.randrange(0, 235)
        (d.rectangle if rnd.random() < 0.5 else d.ellipse)((x0, y0, x1, y1), fill=shade)
    for _ in range(rnd.randrange(20, 40)):
        d.line((rnd.randrange(w), rnd.randrange(h), rnd.randrange(w), rnd.randrange(h)), fill=rnd.randrange(0, 120),
               width=rnd.randrange(1, 6))
    return im.convert("RGB")


def encode(im: Image.Image, fmt: str = "JPEG", quality: int = 90, scale: float = 1.0) -> bytes:
    if scale != 1.0:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
    out = io.BytesIO()
    if fmt == "JPEG":
        im.save(out, "JPEG", quality=quality)
    else:
        im.save(out, fmt)
    return out.getvalue()


def _entry(name: str) -> zipfile.ZipInfo:
    """A fixed entry time: the same pages give the same bytes (a byte-identical copy), whenever they are written."""
    return zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))


def write_cbz(path: Path, seeds: Iterable[int], *, fmt: str = "JPEG", quality: int = 90, scale: float = 1.0,
              extra: Sequence[tuple] = (), names: Optional[Sequence[str]] = None) -> Path:
    """A chapter archive at *path*: one page per seed (``001.jpg`` ...), plus *extra* ``(name, bytes)`` entries."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[fmt]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for i, seed in enumerate(seeds):
            name = names[i] if names else f"{i + 1:03d}.{ext}"
            z.writestr(_entry(name), encode(page(seed), fmt, quality, scale))
        for name, data in extra:
            z.writestr(_entry(name), data)
    return path


def add_cbz(lib, series: str, rel: str, seeds: Iterable[int], mtime_ns: Optional[int] = None, **kw) -> Path:
    """An archive in the test library (:class:`tests.duplicates.conftest.Library`), before its scan."""
    import os

    path = lib.add(series, rel)
    write_cbz(path, seeds, **kw)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path
