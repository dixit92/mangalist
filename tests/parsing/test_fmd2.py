"""Layer 2 - FMD2 exact: units only from the bracket head (synthetic names)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from manga_list.parsing import Kind, Layer, parse_fmd2


def _u(r):
    return None if r is None else str(r)


@pytest.mark.parametrize("name, index, vol, ch, title, group", [
    ("0012 [Vol. 3 Ch. 12.5 - Some Title [Group]].cbz", 12, "3", "12.5", "Some Title", "Group"),
    ("0001 [Ch. 1].cbz", 1, None, "1", None, None),
    ("0001 [Ch. 0001].cbz", 1, None, "1", None, None),
    ("0300 [Ch. 291.999].cbz", 300, None, "291.999", None, None),
    ("0040 [Vol. 0004 Ch. 0040.5 - Title [G]].cbz", 40, "4", "40.5", "Title", "G"),
    ("0007 [Ch. 3.10 [G]].cbz", 7, None, "3.10", None, "G"),
    ("0020 [Ch. 10-12 - Triple].cbz", 20, None, "10-12", "Triple", None),
    ("0003 [Vol. 1-2].cbz", 3, "1-2", None, None, None),
    ("0050 [Vol. 2 Ch. 9 [Team [X]]].cbz", 50, "2", "9", None, "Team [X]"),
    ("0051 [Chapter 9 - Title].cbz", 51, None, "9", "Title", None),
    ("0052 [ch.9].cbz", 52, None, "9", None, None),
    ("0012 [Vol. 3 Ch. 12.5 - Some Title [Group]]", 12, "3", "12.5", "Some Title", "Group"),   # no extension
])
def test_bracket_head(name, index, vol, ch, title, group):
    r = parse_fmd2(name)
    assert r is not None and r.layer is Layer.FMD2
    assert (r.index, _u(r.volume), _u(r.chapter), r.title, r.group) == (index, vol, ch, title, group)


def test_index_is_never_the_chapter_number():
    r = parse_fmd2("0099 [Ch. 10].cbz")
    assert r.index == 99 and r.chapter.start == Decimal("10")


def test_title_prefixed_form():
    r = parse_fmd2("Some Series - 0045 [Ch. 0045].cbz")
    assert (r.series, r.index, str(r.chapter)) == ("Some Series", 45, "45")
    r = parse_fmd2("A - B - 0002 [Vol. 1 Ch. 2 - C - D [G]].cbz")
    assert (r.series, r.index, str(r.volume), str(r.chapter), r.title, r.group) == ("A - B", 2, "1", "2", "C - D", "G")


@pytest.mark.parametrize("name, ch, title", [
    # The misread classes: numbers in the title or group are never read.
    ("0045 [Vol. 5 Ch. 45 - Episode 3 [Group]].cbz", "45", "Episode 3"),
    ("0046 [Vol. 5 Ch. 45.5 - Extra Chapter 2 [Group]].cbz", "45.5", "Extra Chapter 2"),
    ("0002 [Ch. 0 - 4th Year Anniversary [Group]].cbz", "0", "4th Year Anniversary"),
    ("0060 [Ch. 60 - Back to Vol. 2 [Group]].cbz", "60", "Back to Vol. 2"),
    ("0061 [Ch. 61 - Chapter 100 Special].cbz", "61", "Chapter 100 Special"),
    ("0062 [Ch. 62 - v3 c4 Ch. 5].cbz", "62", "v3 c4 Ch. 5"),
    ("0063 [Ch. 63 - 3 Days Later].cbz", "63", "3 Days Later"),
    ("0064 [Ch. 64 - 10-20 Years [Group 2]].cbz", "64", "10-20 Years"),
])
def test_title_and_group_are_never_read_for_numbers(name, ch, title):
    r = parse_fmd2(name)
    assert str(r.chapter) == ch and r.title == title
    assert r.volume is None or str(r.volume) == "5"


@pytest.mark.parametrize("group", ["c4 Scans", "Team v2", "Ch. 99 Group", "[Inner] Outer", "G [2]"])
def test_groups_with_numbers_or_brackets(group):
    r = parse_fmd2(f"0010 [Ch. 10 - Title [{group}]].cbz")
    assert str(r.chapter) == "10" and r.group == group and r.title == "Title"


@pytest.mark.parametrize("name, title", [
    # Windows-unsafe characters as they appear on a Linux share, and their usual replacements.
    ("0008 [Ch. 8 - Who? Me! [G]].cbz", "Who? Me!"),
    ("0008 [Ch. 8 - Part 1: Start [G]].cbz", "Part 1: Start"),
    ("0008 [Ch. 8 - A/B \\ C [G]].cbz", "A/B \\ C"),
    ('0008 [Ch. 8 - "Quoted" <x> | y* [G]].cbz', '"Quoted" <x> | y*'),
    ("0008 [Ch. 8 - Who_ Me! [G]].cbz", "Who_ Me!"),
    ("0008 [Ch. 8 - Part 1\uff1a Start\uff1f [G]].cbz", "Part 1\uff1a Start\uff1f"),
])
def test_windows_unsafe_characters_in_titles(name, title):
    r = parse_fmd2(name)
    assert str(r.chapter) == "8" and r.title == title and r.group == "G"


@pytest.mark.parametrize("name, title", [
    ("0008 [Ch. 8: Title].cbz", "Title"),
    ("0008 [Ch. 8_ Title].cbz", "Title"),
    ("0008 [Ch. 8 Title].cbz", "Title"),
])
def test_other_title_separators(name, title):
    r = parse_fmd2(name)
    assert str(r.chapter) == "8" and r.title == title


def test_kinds():
    assert parse_fmd2("0001 [Vol. 1 Ch. 1].cbz").kind is Kind.CHAPTER
    assert parse_fmd2("0001 [Ch. 1].cbz").kind is Kind.CHAPTER
    assert parse_fmd2("0006 [Vol. 2 - Volume Title].cbz").kind is Kind.VOLUME


@pytest.mark.parametrize("name, extra, title", [
    ("0100 [Oneshot].cbz", False, "Oneshot"),
    ("0101 [Omake [G]].cbz", True, "Omake"),
    ("0102 [Side Story - The Sea].cbz", True, "Side Story - The Sea"),
    ("0005 [Vol. 2 Ch. Extra].cbz", True, "Ch. Extra"),
    ("0103 [Ch. Special - Title [G]].cbz", True, "Ch. Special - Title"),
])
def test_extras_without_numbers(name, extra, title):
    r = parse_fmd2(name)
    assert r.chapter is None and r.kind is Kind.CHAPTER
    assert r.is_extra is extra and r.title == title


def test_numbered_extra_title_is_not_an_extra():
    r = parse_fmd2("0046 [Ch. 45.5 - Extra Chapter 2].cbz")
    assert not r.is_extra and r.is_fraction


@pytest.mark.parametrize("name", [
    "Title 12 [Group].cbz",          # no index position
    "0012 [Ch. 1] [Group].cbz",      # the bracket closes early
    "12 [Group].cbz",                # unit-less body with a short index
    "Title - 012 [Group].cbz",       # unit-less body with a title prefix
    "0012 [].cbz",
    "0012[Ch. 1].cbz",
    "Vol. 01.cbz",
    "01.cbz",
])
def test_not_fmd2(name):
    assert parse_fmd2(name) is None
