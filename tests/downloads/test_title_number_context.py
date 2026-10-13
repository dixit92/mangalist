"""Names read with their folder's title outside the scan too (MangaPixer 1.42.0 parity, integrator 2026-10-13): a number
the series title ends with is never the unit - in a held chapter's look-up, an image-volume folder and the duplicate
finder's "other numbers". Temporary folders and made-up names only."""

from __future__ import annotations

from decimal import Decimal

from mangalist.downloads.chapter_arrivals import _ChapterPass
from mangalist.downloads.placement import _volume_named
from mangalist.duplicates import other_numbers

TITLE = "Example Squad 100"


def test_a_held_chapter_is_not_the_number_the_title_ends_with(tmp_path):
    folder = tmp_path / TITLE
    folder.mkdir()
    for name in (f"{TITLE} v01.cbz", f"{TITLE} c007.cbz"):
        (folder / name).write_bytes(b"x")
    assert _ChapterPass._held_copy(str(folder), "100") is None             # v01 is volume 1, not chapter 100
    assert _ChapterPass._held_copy(str(folder), "7") == f"{TITLE} c007.cbz"


def test_an_image_folder_named_only_by_the_series_title_is_not_a_volume():
    assert not _volume_named(TITLE, TITLE)
    assert _volume_named(f"{TITLE} Vol. 02", TITLE)


def test_other_numbers_with_the_series_title():
    assert other_numbers(f"{TITLE} v01.cbz", Decimal(1), TITLE) == other_numbers(f"{TITLE} v01 (copy).cbz", Decimal(1),
                                                                                    TITLE)
