"""Page hashes (MangaPixer's 64-bit pHash of every page of an archive, :mod:`mangalist.page_hash`): the same page
re-encoded or resized stays within the match threshold, a different page does not; the archive reader's guards and
its graceful refusals. Synthetic pages only (tests/duplicates/pages.py); the decode tests need Pillow."""

from __future__ import annotations

import io
import os
import random
import zipfile

import pytest

from mangalist import page_hash
from mangalist.page_hash import (MATCH_BITS, STATUS_UNREADABLE, STATUS_UNSUPPORTED, distance, hash_archive,
                                 hash_luma, same_page)


@pytest.fixture
def pil():
    pytest.importorskip("PIL")
    from tests.duplicates import pages

    return pages


def test_the_hash_of_a_luma_grid_is_the_dct_median_rule():
    rnd = random.Random(7)
    noisy = [rnd.randrange(256) for _ in range(1024)]
    a = hash_luma(noisy)
    assert a == hash_luma(list(noisy)) and bin(a).count("1") == 32         # 64 coefficients split at their median
    brighter = [min(255, p + 10) for p in noisy]                          # brightness moves the DC term only
    assert distance(a, hash_luma(brighter)) <= 2
    with pytest.raises(ValueError):
        hash_luma([0] * 10)


def test_distance_and_the_match_threshold():
    assert distance(0, 0) == 0 and distance(0, (1 << 64) - 1) == 64
    assert same_page(0b1011, 0b0000) and same_page(0, (1 << MATCH_BITS) - 1)
    assert not same_page(0, (1 << (MATCH_BITS + 1)) - 1)
    assert not same_page(None, 0) and not same_page(0, None)


def test_the_same_page_re_encoded_or_resized_matches_and_a_different_page_does_not(pil, tmp_path):
    seeds = list(range(100, 112))
    original = hash_archive(pil.write_cbz(tmp_path / "a.cbz", seeds))
    assert original.ok and original.pages == 12 and all(h is not None for h in original.hashes)
    for kw in ({"quality": 55}, {"scale": 0.7}, {"scale": 1.4, "quality": 75}, {"fmt": "PNG"}, {"fmt": "WEBP"}):
        other = hash_archive(pil.write_cbz(tmp_path / f"b{len(kw)}{kw.get('fmt', '')}{kw.get('scale', '')}.cbz", seeds,
                                           **kw))
        assert other.ok and max(distance(a, b) for a, b in zip(original.hashes, other.hashes)) <= MATCH_BITS, kw
    different = hash_archive(pil.write_cbz(tmp_path / "c.cbz", range(200, 212)))
    assert min(distance(a, b) for a in original.hashes for b in different.hashes) > MATCH_BITS
    # the same bytes: the same hashes and the same CRC32 + sizes (byte-identical pages)
    again = hash_archive(pil.write_cbz(tmp_path / "d.cbz", seeds))
    assert again.hashes == original.hashes and again.crcs == original.crcs


def test_pages_come_in_natural_order_and_non_pages_are_left_out(pil, tmp_path):
    names = ["p10.jpg", "p2.jpg", "p1.jpg"]
    path = pil.write_cbz(tmp_path / "a.cbz", [1, 2, 3], names=names,
                         extra=[("ComicInfo.xml", b"<ComicInfo/>"), ("__MACOSX/._p1.jpg", b"junk"), ("._p3.jpg", b"x"),
                                ("notes.txt", b"hello")])
    read = hash_archive(path)
    by_seed = {s: hash_archive(pil.write_cbz(tmp_path / f"s{s}.cbz", [s])).hashes[0] for s in (1, 2, 3)}
    assert read.ok and read.hashes == (by_seed[3], by_seed[2], by_seed[1])   # p1 (seed 3), p2 (seed 2), p10 (seed 1)


def test_an_archive_that_cannot_be_read_says_why_and_never_raises(pil, tmp_path):
    rar = tmp_path / "a.cbr"
    rar.write_bytes(b"Rar!\x1a\x07\x00" + b"\0" * 100)
    assert hash_archive(rar).status == STATUS_UNSUPPORTED and "cbr archives are not read" in hash_archive(rar).reason
    junk = tmp_path / "b.cbz"
    junk.write_bytes(b"not a zip at all")
    assert hash_archive(junk).status == STATUS_UNSUPPORTED
    empty = tmp_path / "c.cbz"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "no pages")
    assert (hash_archive(empty).status, hash_archive(empty).reason) == (STATUS_UNREADABLE, "the archive holds no image")
    broken = pil.write_cbz(tmp_path / "d.cbz", [1], extra=[("002.jpg", b"\xff\xd8 broken"), ("003.jpg", b"also bad")])
    out = hash_archive(broken)
    assert out.status == STATUS_UNREADABLE and out.reason == "2 of 3 pages cannot be decoded"
    one_bad = pil.write_cbz(tmp_path / "e.cbz", range(1, 9), extra=[("009.jpg", b"bad")])
    out = hash_archive(one_bad)
    assert out.ok and out.pages == 9 and out.hashes[-1] is None and all(h is not None for h in out.hashes[:-1])
    assert hash_archive(tmp_path / "gone.cbz").status == STATUS_UNSUPPORTED


def test_a_file_that_changed_since_it_was_listed_is_not_read(pil, tmp_path):
    path = pil.write_cbz(tmp_path / "a.cbz", [1, 2])
    st = os.stat(path)
    assert hash_archive(path, expect=(st.st_size, st.st_mtime_ns)).ok
    out = hash_archive(path, expect=(st.st_size + 1, st.st_mtime_ns))
    assert out.status == STATUS_UNSUPPORTED and out.reason == "the file changed since it was listed"


def test_without_pillow_nothing_is_decoded(pil, tmp_path, monkeypatch):
    path = pil.write_cbz(tmp_path / "a.cbz", [1, 2])
    monkeypatch.setattr(page_hash, "pillow_available", lambda: False)
    out = hash_archive(path)
    assert out.status == STATUS_UNSUPPORTED and "Pillow is not installed" in out.reason


def test_too_many_pages_are_not_read(pil, tmp_path, monkeypatch):
    monkeypatch.setattr(page_hash, "MAX_PAGES", 3)
    out = hash_archive(pil.write_cbz(tmp_path / "a.cbz", [1, 2, 3, 4]))
    assert out.status == STATUS_UNSUPPORTED and out.reason == "more than 3 pages"


def test_the_reduced_decode_is_asked_for_jpeg_pages(pil, monkeypatch):
    from PIL import Image, JpegImagePlugin

    asked = []
    real = JpegImagePlugin.JpegImageFile.draft

    def draft(self, mode, size):
        asked.append((mode, size))
        return real(self, mode, size)

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", draft)
    image = Image.open(io.BytesIO(pil.encode(pil.page(5))))
    page_hash.hash_image(image)
    assert asked == [("L", (128, 128))] and 128 <= image.size[0] < pil.SIZE[0]     # decoded at a reduced size


def test_pillow_is_a_runtime_dependency_of_every_build():
    """The Docker image installs requirements.txt; the desktop builds bundle what PyInstaller is allowed to see."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    reqs = [line.split("#")[0].strip().lower() for line in (root / "requirements.txt").read_text().splitlines()]
    assert any(r.startswith("pillow") for r in reqs)
    spec = (root / "MangaList.spec").read_text()
    excludes = spec[spec.index("excludes=["):spec.index("]", spec.index("excludes=["))]
    assert '"PIL"' not in excludes and '"mangalist.page_hash"' in spec and '"PIL.Image"' in spec
    dockerfile = (root / "packaging" / "docker" / "Dockerfile").read_text()
    assert "-r /tmp/requirements-image.txt" in dockerfile
