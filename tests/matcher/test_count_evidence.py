"""Port of MangaPixer 1.31.1 ``CountEvidenceTests.cs`` (the count rule, 1.29.0). Synthetic names only.
``CountEvidence.Describe`` (Identify's wording) is not ported, so its assertions are left out."""

from __future__ import annotations

from dataclasses import replace

import pytest

from manga_list.matcher import count_evidence as ce
from manga_list.matcher.contracts import ChildFolderShape, MatchContext, WorkClass
from manga_list.matcher.count_evidence import CountSignal, LocalUnitCounts, PublishedUnitCounts


def published(volumes=None, english_volumes=None, status_chapters=None, english_chapters=None, latest=None):
    return PublishedUnitCounts(volumes, english_volumes, status_chapters, english_chapters, latest)


def sub(name: str, *archives: str) -> ChildFolderShape:
    return ChildFolderShape(name, len(archives), tuple(archives))


def test_local_of_reads_loose_names_as_highest_numbers():
    local = ce.local_of(["Some Series v01.cbz", "Some Series v02.cbz", "Some Series v02.5.cbz", "Some Series v06.cbz"], None)

    assert local == LocalUnitCounts(4, 0, 1, 6, None, None)
    assert not local.is_mixed


def test_local_of_season_subfolders_add_their_numbers_side_folders_do_not():
    local = ce.local_of(["000.cbz"], [
        sub("Season 1", "Some Series - Chapter 001.cbz", "Some Series - Chapter 002.cbz"),
        sub("Season 2", "Some Series - Chapter 003.cbz", "Some Series - Chapter 004.5.cbz"),
        sub("Extras", "Some Series - Chapter 900.cbz"),
        sub("Another Work", "Another Work - Chapter 700.cbz"),  # not a unit subfolder: a separate work
    ])

    assert local == LocalUnitCounts(0, 5, None, None, 0, 4)


def test_local_of_volumes_subfolder_reads_bare_numbers_as_volumes():
    local = ce.local_of([], [sub("Volumes", "01.cbz", "02.cbz", "03 [Final].cbz")])

    assert local == LocalUnitCounts(3, 0, 1, 3, None, None)


def test_local_of_volumes_next_to_chapters_is_mixed():
    local = ce.local_of(["Some Series Ch 015.cbz"], [sub("Volumes", "Some Series v01.cbz")])

    assert local.is_mixed
    assert ce.compare(local, published(volumes=1, status_chapters=2)) == ce.NO_COMPARISON


@pytest.mark.parametrize(("name", "expected"), [
    ("Extras", True),
    ("Side Stories", True),
    ("Colored", True),
    ("Oneshots", True),
    ("Season 2", False),
    ("Volumes", False),
    ("One Piece of Work", False),  # not a unit folder at all
])
def test_is_side_folder_name_knows_the_side_material_words(name, expected):
    assert ce.is_side_folder_name(name) == expected


def test_compare_volumes_with_volume_totals_only():
    volumes = LocalUnitCounts(12, 0, 1, 12, None, None)

    assert ce.compare(volumes, published(volumes=10)).volumes == CountSignal.AGREE
    assert ce.compare(volumes, published(volumes=6)).volumes == CountSignal.CONFLICT  # 12 > 1.5 x 6 + 2
    assert ce.compare(volumes, published(volumes=6, english_volumes=12)).volumes == CountSignal.AGREE
    # A chapter total says nothing about volumes.
    assert ce.compare(volumes, published(status_chapters=3)) == replace(ce.NO_COMPARISON, published_chapters=3)


def test_compare_chapters_with_chapter_totals_the_latest_chapter_alone_never_conflicts_with_a_volume_record():
    chapters = LocalUnitCounts(0, 58, None, None, 1, 58)

    assert ce.compare(chapters, published(volumes=10, latest=12)).chapters == CountSignal.NONE
    assert ce.compare(chapters, published(volumes=10)).chapters == CountSignal.NONE
    assert ce.compare(chapters, published(volumes=10, latest=60)).chapters == CountSignal.AGREE
    assert ce.compare(chapters, published(latest=12)).chapters == CountSignal.CONFLICT
    assert ce.compare(chapters, published(volumes=10, status_chapters=20)).chapters == CountSignal.CONFLICT
    assert ce.compare(chapters, published(status_chapters=20, latest=50)).chapters == CountSignal.AGREE
    assert ce.compare(chapters, published(english_chapters=60)).chapters == CountSignal.AGREE


def test_from_context_prefers_the_planners_units_else_the_older_fields():
    context = MatchContext(WorkClass.SERIES, 5, 5, 0, None, None, False, (), local_volumes=4)
    assert ce.from_context(context) == LocalUnitCounts(5, 0, None, 4, None, None)

    units = LocalUnitCounts(5, 0, 2, 9, None, None)
    assert ce.from_context(replace(context, units=units)) is units


def test_chapter_names_stating_their_volume_are_compared_with_the_volume_total():
    local = ce.local_of(["Some Title v01 c001.cbz", "Some Title v05 c030.cbz", "Some Title v09 c060.cbz"], None)
    assert (local.volume_archives, local.chapter_archives, local.highest_named_volume) == (0, 3, 9)
    assert not local.is_mixed
    assert ce.compare(local, PublishedUnitCounts(10, None, None, None, None)).volumes == CountSignal.AGREE
    assert ce.compare(local, PublishedUnitCounts(3, None, None, None, None)).volumes == CountSignal.CONFLICT

    plain = ce.local_of(["Some Title - Chapter 001.cbz", "Some Title - Chapter 060.cbz"], None)
    assert plain.highest_named_volume is None
    assert ce.compare(plain, PublishedUnitCounts(3, None, None, None, None)).volumes == CountSignal.NONE
