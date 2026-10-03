"""Layer 3 - release names (synthetic names)."""

from __future__ import annotations

import pytest

from manga_list.parsing import Kind, Layer, parse_release


def _u(r):
    return None if r is None else str(r)


@pytest.mark.parametrize("name, kind, vol, ch, num", [
    ("Some Series v01 (2019) (Digital) (Grp).cbz", Kind.VOLUME, "1", None, None),
    ("Some Series v01-03 (2019) (Digital) (Grp).cbz", Kind.VOLUME, "1-3", None, None),
    ("Some Series v01-v03 (2019) (Digital) (Grp).cbz", Kind.VOLUME, "1-3", None, None),
    ("Some Series v10.5 (2020) (Digital) (Grp).cbz", Kind.VOLUME, "10.5", None, None),
    ("Some Series v10 + 085-086 (2021) (Digital) (Grp).cbz", Kind.BOTH, "10", "85-86", None),
    ("Some Series v05 (+ c041-045) (Digital-Compilation) (Grp).cbz", Kind.BOTH, "5", "41-45", None),
    ("Some Series v05 (+ Ch. 41.5) (Digital) (Grp).cbz", Kind.BOTH, "5", "41.5", None),
    ("Some Series v07.cbz", Kind.VOLUME, "7", None, None),
    ("Some Series vol 07.cbz", Kind.VOLUME, "7", None, None),
    ("Some Series Vol. 07.cbz", Kind.VOLUME, "7", None, None),
    ("Some Series Volume 7.cbz", Kind.VOLUME, "7", None, None),
    ("Some Series c012 (2020) (Grp).cbz", Kind.CHAPTER, None, "12", None),
    ("Some Series c012-014 (2020) (Grp).cbz", Kind.CHAPTER, None, "12-14", None),
    ("Some Series c291.999 (Grp).cbz", Kind.CHAPTER, None, "291.999", None),
    ("Some Series 001 (2019) (Digital) (Grp).cbz", Kind.UNKNOWN, None, None, "1"),
    ("Some Series 2 v03 (2020) (Digital).cbz", Kind.VOLUME, "3", None, None),
])
def test_units(name, kind, vol, ch, num):
    r = parse_release(name)
    assert r is not None and r.layer is Layer.RELEASE
    assert (r.kind, _u(r.volume), _u(r.chapter), _u(r.number)) == (kind, vol, ch, num)


def test_tags():
    r = parse_release("Some Series v01 (2019) (Digital) (Grp-Name) (f2).cbz")
    assert (r.series, r.year, r.edition, r.group, r.fix) == ("Some Series", 2019, "Digital", "Grp-Name", "f2")
    assert r.tags == ("2019", "Digital", "Grp-Name", "f2")
    r = parse_release("Some Series v01 (Digital-Compilation) (Grp) (f).cbz")
    assert (r.year, r.edition, r.group, r.fix) == (None, "Digital-Compilation", "Grp", "f")
    r = parse_release("Some Series v02 (2019) (Digital) [Grp].cbz")
    assert r.group == "Grp"
    r = parse_release("Some Series v02 (Colored) (2019) (Digital) (Grp).cbz")
    assert r.group == "Grp"          # the tag after the edition is the group
    r = parse_release("Some Series v02 (2019-2020) (Grp).cbz")
    assert (r.year, r.group) == (2019, "Grp")


def test_volume_tag_after_a_chapter():
    r = parse_release("Some Series - c001 (v01) [Grp].cbz")
    assert (r.kind, str(r.chapter), str(r.volume), r.group, r.series) == (Kind.CHAPTER, "1", "1", "Grp", "Some Series")


def test_series_and_volume_title():
    r = parse_release("Some Series v03 - The Subtitle (2019) (Digital) (Grp).cbz")
    assert (r.series, str(r.volume), r.title) == ("Some Series", "3", "The Subtitle")
    r = parse_release("Some Series - v03 (2019).cbz")
    assert r.series == "Some Series"


@pytest.mark.parametrize("name", [
    "Some Series 001.cbz",                 # bare number without a year / edition tag: layer 5
    "Some Series 001 (Grp).cbz",
    "Vol. 01.cbz",                         # no series part: generic
    "v05.cbz",
    "Some Series Vol. 1 Ch 3.cbz",         # the series part would end in a unit token: generic
    "Some Series.cbz",
    "Some Series c012 + 013 (Grp).cbz",
])
def test_not_release(name):
    assert parse_release(name) is None
