"""Releases that are not volumes of a comic are refused once their file list is known (owner, 2026-10-24): a pack of
e-books / audio files (a light novel with no "novel" in its title) and a bare-number pack of chapter-sized files with
no volume in their names ("001-158": a chapter pack). Made-up names only."""

from __future__ import annotations

from mangalist.downloads.partial import CHAPTERS_ONLY, NOT_COMIC, choose_files

MB = 1024 * 1024


def test_a_pack_of_epub_volumes_is_not_a_comic():
    sel = choose_files([(f"Made Up Novel v{n:02d} [Pub] [Grp].epub", 7 * MB) for n in (1, 2, 3)], ["1", "2"], "volumes")
    assert sel.not_comic and sel.refused and not sel.chapters_only and sel.problem == NOT_COMIC


def test_an_audiobook_with_its_cover_and_metadata_is_not_a_comic():
    files = [("Made Up Novel v01 [Grp].m4b", 280 * MB), ("cover.jpg", MB), ("metadata.opf", 1000)]
    assert choose_files(files, ["1"], "volumes").not_comic


def test_a_comic_pack_with_an_epub_extra_is_still_a_comic():
    files = [("Made Up Series v01.cbz", 120 * MB), ("Made Up Series v02.cbz", 120 * MB), ("Bonus novel.epub", 2 * MB)]
    sel = choose_files(files, ["2"], "volumes")
    assert not sel.refused and [f.keep for f in sel.files] == [False, True, False]


def test_a_bare_number_pack_of_many_small_unnumbered_files_is_chapters():
    """The owner's case: "Title 001-158 (2021-2022)", 158 episode files of ~10 MB, sent for missing volume 5."""
    files = [(f"Made Up Webtoon {n:03d} (2021) (Digital) (grp).cbz", 10 * MB) for n in range(1, 159)]
    sel = choose_files(files, ["5"], None)              # hint None: the title's numbers carry no volume mark
    assert sel.chapters_only and sel.refused and sel.problem == CHAPTERS_ONLY


def test_a_few_small_bare_number_files_are_chapters_too():
    files = [(f"Made Up Webtoon {n:03d} (Digital).cbz", 12 * MB) for n in range(1, 13)]
    assert choose_files(files, ["3"], None).chapters_only


def test_a_bare_number_pack_of_volume_sized_files_is_still_offered():
    files = [(f"Made Up Series {n:02d} (Digital).cbz", 150 * MB) for n in range(1, 13)]
    sel = choose_files(files, ["3"], None)
    assert not sel.refused


def test_the_rule_needs_the_bare_number_hint():
    """A title that states volumes ("v01-12") keeps its unnumbered small files: the title already said volumes."""
    files = [(f"Made Up Webtoon {n:03d} (Digital).cbz", 10 * MB) for n in range(1, 13)]
    assert not choose_files(files, ["3"], "volumes").refused
