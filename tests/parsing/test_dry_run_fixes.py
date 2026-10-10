"""Fixes from the renamer's read-only dry run over the owner's library (integrator, 2026-10-10): six name shapes the
scheme got wrong. Synthetic names only (the shapes, not the titles)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mangalist.naming import (
    chapter_file_name,
    restates_number,
    sanitize_group,
    sanitize_qualifier,
    target_name,
    volume_file_name,
)
from mangalist.parsing import Kind, Layer, ParseContext, parse_fmd2, parse_name

D = Decimal


def _t(name, series="Some Series", **kw):
    return target_name(parse_name(name, kw.pop("ctx", None)), ext=".cbz", series_title=series, **kw)


def _u(r):
    return None if r is None else str(r)


# --- 1. "<Series> 001 Vol 01 <chapter title>" is chapter 1 of volume 1 -------------------------------------------

@pytest.mark.parametrize("name, vol, ch, title, group", [
    ("Some Series 001 Vol 01 A Chapter Title.cbz", "1", "1", "A Chapter Title", None),
    ("Some Series 002 Vol 01 Another Title.cbz", "1", "2", "Another Title", None),
    ("Some Series 001 Vol 01.cbz", "1", "1", None, None),
    ("Some Series - 001 Vol. 01 - A Title [G].cbz", "1", "1", "A Title", "G"),
    ("Some Series 012.5 Vol 02.cbz", "2", "12.5", None, None),
    ("Some Series 001 v01 (2020) (Digital) (G).cbz", "1", "1", None, "G"),
    ("Some Series 2 001 Volume 3 Title.cbz", "3", "1", "Title", None),
])
def test_a_chapter_number_before_its_volume(name, vol, ch, title, group):
    r = parse_name(name)
    assert (r.kind, _u(r.volume), _u(r.chapter), r.title, r.group) == (Kind.CHAPTER, vol, ch, title, group)
    assert r.series.startswith("Some Series") and not r.guessed


@pytest.mark.parametrize("name, vol", [
    ("Some Series 2 Vol 3 Subtitle.cbz", "3"),                       # a plain number belongs to the title
    ("Some Series 2049 v01 (2020) (Digital) (G).cbz", "1"),
    ("Some Series v01 (2020) (Digital) (G).cbz", "1"),
])
def test_plain_numbers_before_a_volume_stay_in_the_title(name, vol):
    r = parse_name(name)
    assert (r.kind, str(r.volume), r.chapter) == (Kind.VOLUME, vol, None)


def test_a_series_of_numbered_chapters_no_longer_collides():
    names = [f"Some Series {n:03d} Vol 01 Title {n}.cbz" for n in range(1, 6)]
    targets = [_t(n) for n in names]
    assert targets[0] == "Ch. 0001.00 Vol. 001 (Title 1).cbz"
    assert len(set(targets)) == len(names)


# --- 2. a season / part of the series stays in the volume name ---------------------------------------------------

@pytest.mark.parametrize("name, qualifier, vol", [
    ("Some Series Season 1 v01.cbz", "Season 1", "1"),
    ("Some Series Season 2 v01 (2020) (Digital) (G).cbz", "Season 2", "1"),
    ("Some Series season 02 v03.cbz", "Season 2", "3"),
    ("Some Series Part 2 v03.cbz", "Part 2", "3"),
    ("Some Series - Part 5 - A Subtitle v05 (2022) (Digital) (G).cbz", "Part 5", "5"),
    ("Some Series Season 2 - Vol. 001 [G].cbz", "Season 2", "1"),                  # the scheme's own form
    ("Some Series v01 (2020) (Digital) (G).cbz", None, "1"),
    ("Some Partition v01.cbz", None, "1"),
    ("Some Series - Part 5 Is Long v01.cbz", None, "1"),                            # not at the end of the title
])
def test_the_qualifier_is_read(name, qualifier, vol):
    r = parse_name(name)
    assert (r.kind, r.qualifier, str(r.volume)) == (Kind.VOLUME, qualifier, vol)


def test_two_seasons_no_longer_collide():
    one, two = _t("Some Series Season 1 v01.cbz"), _t("Some Series Season 2 v01 (2020) (Digital) (G).cbz")
    assert (one, two) == ("Some Series Season 1 - Vol. 001.cbz", "Some Series Season 2 - Vol. 001 [G].cbz")
    assert _t("Other Title - Part 5 - A Subtitle v05 (2022) (Digital) (G).cbz") == "Some Series Part 5 - Vol. 005 [G].cbz"


def test_a_title_that_already_says_it_is_not_repeated():
    assert _t("Some Series Season 2 v01.cbz", series="Some Series Season 2") == "Some Series Season 2 - Vol. 001.cbz"
    assert _t("Some Series Season 2 v01.cbz", series="Some Series: Season 02") == "Some Series - Season 02 - Vol. 001.cbz"
    assert _t("Some Series Season 2 v01.cbz", series="Some Series Season 3") == \
        "Some Series Season 3 Season 2 - Vol. 001.cbz"


@pytest.mark.parametrize("series, qualifier", [
    ("Some Series", "Season 2"), ("Some Series", "Part 5"), ("Some Series", "Season 10.5"), ("Some Series", None),
    ("Some Series - Part 5 - Sub", "Season 1"), ("A [B]", "Part 1"),
])
def test_qualifier_round_trip(series, qualifier):
    name = volume_file_name(series, 3, volume_end=4, group="G", qualifier=qualifier)
    r = parse_name(name)
    assert (r.layer, r.kind, r.series, r.qualifier, str(r.volume), r.group) == \
        (Layer.SCHEME, Kind.VOLUME, series, qualifier, "3-4", "G")
    assert target_name(r, ext=".cbz", series_title=series) == name                  # renaming again changes nothing


def test_a_title_ending_in_a_season_reads_back_the_same_name():
    # By construction the parse-back takes a trailing "Season N" as the qualifier; the name is still stable.
    name = volume_file_name("Some Series Season 2", 1)
    r = parse_name(name)
    assert (r.series, r.qualifier) == ("Some Series", "Season 2")
    assert target_name(r, ext=".cbz", series_title="Some Series Season 2") == name
    assert target_name(r, ext=".cbz", series_title="Some Series") == name


@pytest.mark.parametrize("text, expected", [
    ("Season 2", "Season 2"), ("season 02", "Season 2"), ("PART  5", "Part 5"), ("Season 1.50", "Season 1.5"),
    (None, None), ("  ", None),
])
def test_sanitize_qualifier(text, expected):
    assert sanitize_qualifier(text) == expected


@pytest.mark.parametrize("bad", ["Book 2", "Season", "2nd Season", "Season 2 extra", "Part two"])
def test_a_bad_qualifier_raises(bad):
    with pytest.raises(ValueError):
        volume_file_name("Some Series", 1, qualifier=bad)


def test_chapters_carry_no_qualifier():
    assert _t("Some Series Season 2 - Episode 3.cbz") == "Ch. 0003.00.cbz"


# --- 3. a name cut off after a chapter word is left alone --------------------------------------------------------

@pytest.mark.parametrize("name, vol", [
    ("0001 [Vol. 0001 Ch.cbz", "1"),
    ("0002 [Vol. 0001 Chapter.cbz", "1"),
    ("0003 [Vol. 2 Ch].cbz", "2"),
    ("Some Series Vol. 3 Ch.cbz", "3"),
    ("Some Series v04 Chap.cbz", "4"),
])
def test_cut_off_names(name, vol):
    r = parse_name(name)
    assert (r.kind, str(r.volume), r.chapter, r.ambiguous) == (Kind.CHAPTER, vol, None, True)
    assert _t(name) is None


@pytest.mark.parametrize("name", ["0001 [Vol. 1 Ch. 1].cbz", "Some Rich v01.cbz", "Some Series v01 Chapters.cbz"])
def test_complete_names_are_not_cut_off(name):
    assert not parse_name(name).ambiguous


# --- 4. placeholder groups are no group --------------------------------------------------------------------------

@pytest.mark.parametrize("placeholder", ["no group", "No Group", "NO GROUP", "no  group", "Unknown", "none", "N/A",
                                         "n/a", "null", "-", "?"])
def test_placeholder_groups(placeholder):
    assert sanitize_group(placeholder) is None
    r = parse_name(f"0003 [Ch. 3 - A Title [{placeholder}]].cbz")
    assert (str(r.chapter), r.title, r.group) == ("3", "A Title", None)
    assert _t(f"0003 [Ch. 3 - A Title [{placeholder}]].cbz") == "Ch. 0003.00 (A Title).cbz"
    assert chapter_file_name("3", group=placeholder) == "Ch. 0003.00.cbz"


def test_placeholders_in_other_layers():
    assert parse_name("Some Series v01 (2019) (Digital) (Unknown).cbz").group is None
    assert parse_name("Ch. 0003.00 [no group].cbz").group is None
    assert _t("Ch. 0003.00 [no group].cbz") == "Ch. 0003.00.cbz"


@pytest.mark.parametrize("group", ["No Group Scans", "Nobody", "Unknown Scans", "The None Team", "NA Team"])
def test_real_groups_with_those_words_stay(group):
    assert sanitize_group(group) == group
    assert parse_name(f"0003 [Ch. 3 [{group}]].cbz").group == group


# --- 5. a title that only restates the number is left out ---------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("0144 [Ch. 0144 - Chapter 144].cbz", "Ch. 0144.00.cbz"),
    ("0144 [Ch. 144 - Ch. 0144].cbz", "Ch. 0144.00.cbz"),
    ("0144 [Ch. 144 - Episode 144 [G]].cbz", "Ch. 0144.00 [G].cbz"),
    ("0144 [Ch. 144 - Ep. 144].cbz", "Ch. 0144.00.cbz"),
    ("0144 [Ch. 144 - #144].cbz", "Ch. 0144.00.cbz"),
    ("0144 [Ch. 144 - No. 144].cbz", "Ch. 0144.00.cbz"),
    ("0144 [Ch. 144.5 - Chapter 144.50].cbz", "Ch. 0144.50.cbz"),
    ("0144 [Ch. 10-12 - Chapter 10-12].cbz", "Ch. 0010.00-0012.00.cbz"),
    ("0145 [Ch. 145 - Chapter 144].cbz", "Ch. 0145.00 (Chapter 144).cbz"),           # another number: kept
    ("0144 [Ch. 144 - Chapter 144 - The End].cbz", "Ch. 0144.00 (Chapter 144 - The End).cbz"),
    ("0144 [Ch. 144 - Chapter 144.5].cbz", "Ch. 0144.00 (Chapter 144.5).cbz"),
])
def test_restated_titles(name, expected):
    assert _t(name) == expected


@pytest.mark.parametrize("title, chapter, end, restates", [
    ("Chapter 144", "144", None, True), ("chapter 0144", "144", None, True), ("CH.144", "144", None, True),
    ("144", "144", None, True), ("Chapter 144.0", "144", None, True), ("Chapter 12", "144", None, False),
    ("Chapter 10", "10", "12", False), ("Chapter 10-12", "10", "12", True), ("Chapter 144 Part 2", "144", None, False),
    ("", "1", None, False), (None, "1", None, False),
])
def test_restates_number(title, chapter, end, restates):
    assert restates_number(title, D(chapter), D(end) if end else None) is restates


# --- 6. any single-word label opens an FMD2 bracket ---------------------------------------------------------------

@pytest.mark.parametrize("name, ch, title", [
    ("Some Series - 0001 [No. 0001].cbz", "1", None),
    ("0001 [Hug 0001].cbz", "1", None),
    ("0002 [Hug 2].cbz", "2", None),
    ("0003 [Hug 3 - A Title].cbz", "3", "A Title"),
    ("0004 [Dish 0004 Soup Night].cbz", "4", "Soup Night"),
    ("0005 [Lap.5: A Title].cbz", "5", "A Title"),
    ("0006 [Vol. 2 Hug 0006].cbz", "6", None),
    ("0001 [Contact. 0001 - Hello].cbz", "1", "Hello"),
])
def test_any_word_label(name, ch, title):
    r = parse_fmd2(name)
    assert (r.kind, str(r.chapter), r.title) == (Kind.CHAPTER, ch, title)
    assert _t(name).startswith(f"Ch. {int(D(ch)):04d}.00")


@pytest.mark.parametrize("name, title", [
    ("0002 [Season 2].cbz", "Season 2"),
    ("0003 [Part 3].cbz", "Part 3"),
    ("0004 [Extra 4].cbz", "Extra 4"),
    ("0005 [Tome 5].cbz", "Tome 5"),
    ("0006 [Book 6 - A Title].cbz", "Book 6 - A Title"),
    ("0007 [Arc 7].cbz", "Arc 7"),
    ("0008 [Love 2 Hate].cbz", "Love 2 Hate"),                # an unknown word + a plain number + more title
    ("0001 [3 Days Later].cbz", "3 Days Later"),
])
def test_words_that_are_not_chapter_labels(name, title):
    r = parse_fmd2(name)
    assert r.chapter is None and r.title == title
    assert _t(name) is None


def test_a_volume_word_still_reads_as_the_volume():
    r = parse_fmd2("0001 [Vol. 3].cbz")
    assert (r.kind, str(r.volume), r.chapter) == (Kind.VOLUME, "3", None)


# --- 3.1 and 3.10 ----------------------------------------------------------------------------------------------

def test_three_point_one_and_three_point_ten_share_a_name():
    # The owner's two-decimal rule writes both as "Ch. 0003.10" (the same number); two such files in one folder are
    # a collision the renamer refuses - it keeps both names as they are.
    assert chapter_file_name("3.1") == chapter_file_name("3.10") == "Ch. 0003.10.cbz"
    assert _t("0003 [Ch. 3.1].cbz") == _t("0004 [Ch. 3.10].cbz")


def test_the_series_answer_still_decides_a_guess_with_a_qualifier():
    r = parse_name("Some Series Season 2 07.cbz", ParseContext(kind_hint="volumes"))
    assert (r.kind, str(r.volume), r.qualifier) == (Kind.VOLUME, "7", "Season 2")
    assert target_name(r, ext=".cbz", series_title="Some Series") == "Some Series Season 2 - Vol. 007.cbz"
