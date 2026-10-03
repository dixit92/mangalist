"""Layer 5 - bare numbers: unknown kind unless the per-series hint decides."""

from __future__ import annotations

import pytest

from manga_list.parsing import Kind, Layer, ParseContext, parse_bare, parse_name


@pytest.mark.parametrize("name, series, num", [
    ("01.cbz", None, "1"),
    ("001.5.cbz", None, "1.5"),
    ("Some Series 01.cbz", "Some Series", "1"),
    ("Some Series - 01.cbz", "Some Series", "1"),
    ("Some Series_01.cbz", "Some Series", "1"),
    ("Some Series #3.cbz", "Some Series", "3"),
    ("Some Series 01-03.cbz", "Some Series", "1-3"),
    ("Some Series 07 (Grp).cbz", "Some Series", "7"),
])
def test_bare(name, series, num):
    r = parse_bare(name)
    assert r.layer is Layer.BARE and r.kind is Kind.UNKNOWN
    assert (r.series, str(r.number)) == (series, num)
    assert r.volume is None and r.chapter is None


def test_series_title_context():
    assert parse_bare("42.cbz", series_title="42") is None
    r = parse_bare("Series 2 03.cbz", series_title="Series 2")
    assert (r.series, str(r.number)) == ("Series 2", "3")
    r = parse_bare("Series 2.cbz", series_title="Series")
    assert str(r.number) == "2"


@pytest.mark.parametrize("hint, kind", [
    ("volumes", Kind.VOLUME), ("chapters", Kind.CHAPTER), ("Volume", Kind.VOLUME), (Kind.CHAPTER, Kind.CHAPTER),
])
def test_kind_hint_decides(hint, kind):
    r = parse_name("Some Series 05.cbz", ParseContext(kind_hint=hint))
    assert r.kind is kind and str(r.number) == "5"
    assert str(r.volume if kind is Kind.VOLUME else r.chapter) == "5"
    assert (r.chapter if kind is Kind.VOLUME else r.volume) is None


def test_no_hint_means_unknown():
    r = parse_name("01.cbz")
    assert r.kind is Kind.UNKNOWN and str(r.number) == "1" and r.legacy_kind == "ambiguous"


def test_hint_also_decides_a_bare_release_number():
    r = parse_name("Some Series 012 (2019) (Digital) (Grp).cbz", ParseContext(kind_hint="chapters"))
    assert r.layer is Layer.RELEASE and r.kind is Kind.CHAPTER and str(r.chapter) == "12"


def test_hint_never_overrides_a_stated_kind():
    ctx = ParseContext(kind_hint="volumes")
    assert parse_name("0001 [Ch. 1].cbz", ctx).kind is Kind.CHAPTER
    assert parse_name("Ch. 003.cbz", ctx).kind is Kind.CHAPTER
    assert parse_name("Some Series c012 (Grp).cbz", ctx).kind is Kind.CHAPTER


@pytest.mark.parametrize("bad", ["both", "x", Kind.BOTH, Kind.UNKNOWN])
def test_bad_hint(bad):
    with pytest.raises(ValueError):
        ParseContext(kind_hint=bad)


@pytest.mark.parametrize("name", ["Some Series.cbz", "Some Series-01.cbz", "Title01.cbz"])
def test_not_bare(name):
    assert parse_bare(name) is None
