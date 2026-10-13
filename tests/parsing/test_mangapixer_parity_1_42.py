"""Parity with MangaPixer 1.42.0 (its lane T hand-over to MangaList, 2026-10-12): a number the series title ends with is
no chapter (the series title, and the series head of the folder's names - Mihon's rule), a single-digit ``[v2]`` is a
release revision, the comics / BD grammar, and the site watermark as a class. Made-up titles only."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangalist.naming import target_name
from mangalist.parsing import (
    Kind,
    Layer,
    ParseContext,
    SeriesHead,
    parse_comics_extra,
    parse_name,
    parse_names,
    series_head_of,
    with_series_head,
)


def _u(r):
    return None if r is None else str(r)


def _read(name, ctx=None):
    r = parse_name(name, ctx)
    return r.kind, _u(r.volume), _u(r.chapter)


# --- 1. a series title that ends in a number ------------------------------------------------------------------------

@pytest.mark.parametrize("name, title, vol", [
    ("Some Title 100 v01.cbz", "Some Title 100", "1"),
    ("Some Title 100 v01 (2018) (Digital) (Grp).cbz", "Some Title 100", "1"),
    ("Some Title 100 Vol. 02.cbz", "Some Title 100 (2012)", "2"),        # the folder's year tag is not the title
    ("Robo 009 v01.cbz", "Robo 009", "1"),
    ("Robo 009 v03.cbz", "Robo 9", "3"),                                # zero padding aside
    ("Space Fleet 2199 v01.cbz", "Space Fleet 2199", "1"),
    ("Mecha Suit 0079 Vol 1.cbz", "Mecha Suit 0079", "1"),
    ("[Grp] Some Title 100 v04.cbz", "Some Title 100", "4"),             # a leading group tag aside
    ("some_title_100_v05.cbz", "Some Title 100", "5"),
])
def test_a_number_the_series_title_ends_with_is_never_read(name, title, vol):
    assert parse_name(name).kind is Kind.CHAPTER                        # the name alone: chapter <N> of the volume
    r = parse_name(name, ParseContext(series_title=title))
    assert (r.kind, _u(r.volume), _u(r.chapter), r.guessed) == (Kind.VOLUME, vol, None, False)


@pytest.mark.parametrize("name, title, vol, ch", [
    ("Some Title 100 001 Vol 1 A Chapter.cbz", "Some Title 100", "1", "1"),   # the number AFTER the title is read
    ("Some Series 100 Vol 11 A Title.cbz", "Some Series", "11", "100"),      # the title does not end in it
    ("Some Series 099 Vol 10.cbz", "Other Folder Name", "10", "99"),         # another folder name: the name alone
    ("Some Series 100 Vol 11.cbz", "Some Series 1", "11", "100"),            # "1" is not "100"
])
def test_a_chapter_number_after_the_series_title_is_still_read(name, title, vol, ch):
    assert _read(name, ParseContext(series_title=title)) == (Kind.CHAPTER, vol, ch)


def test_the_series_title_keeps_a_bare_number_after_it():
    assert _read("Some Title 100 07.cbz", ParseContext(series_title="Some Title 100")) == (Kind.CHAPTER, None, "7")
    assert parse_name("Some Title 100.cbz", ParseContext(series_title="Some Title 100")).kind is Kind.UNKNOWN


# --- 1b. the series head of a folder's names --------------------------------------------------------------------------

def test_the_series_head_is_the_text_before_a_number_the_siblings_vary():
    assert series_head_of(["Some Series 099 Vol 10 A", "Some Series 100 Vol 11 B", "Some Series 101 Vol 11"]) \
        == SeriesHead("some series", True)
    assert series_head_of(["Some Title 100 001 A Title", "Some Title 100 002 B Title"]) \
        == SeriesHead("some title 100", True)
    assert series_head_of(["[Grp] Some Series 001 Read Online.cbz", "Some Series 002 Read Online.cbz"]) \
        == SeriesHead("some series", True)


def test_a_number_every_sibling_shares_before_a_changing_volume_is_part_of_the_title():
    assert series_head_of(["Robo 009 v01.cbz", "Robo 009 v02.cbz", "Robo 009 Vol 3.cbz"]) == SeriesHead("robo", False)
    assert series_head_of(["Some Title 100 Vol 1", "Some Title 100 Vol 2"]) == SeriesHead("some title", False)
    # sequels: the number repeats while the volumes restart
    assert series_head_of(["Some Series 02 v01", "Some Series 02 v02", "Some Series 03 v01", "Some Series 03 v02"]) \
        == SeriesHead("some series", False)


@pytest.mark.parametrize("names", [
    ["Some Series 100 Vol 11 A.cbz", "Some Series 100 Vol 11 A (1).cbz"],   # two copies of one chapter
    ["Some Series 100 Vol 11 A.cbz"],                                         # one file
    [],
    ["Some Series 05.cbz", "Some Series 05 (1).cbz"],                         # no volume after the shared number
    ["Some Series Vol 01.cbz", "Some Series Vol 02.cbz"],                     # a unit word owns the number
])
def test_no_series_head(names):
    assert series_head_of(names) is None


def test_parse_names_reads_title_numbers_as_titles_and_varying_numbers_as_chapters():
    title = parse_names(["Robo 009 v01.cbz", "Robo 009 v02.cbz", "Robo 009 Vol 3.cbz"])
    assert [(r.kind, _u(r.volume), _u(r.chapter)) for r in title] == [
        (Kind.VOLUME, "1", None), (Kind.VOLUME, "2", None), (Kind.VOLUME, "3", None)]
    chapters = parse_names(["Some Series 099 Vol 10 A.cbz", "Some Series 100 Vol 11 B.cbz", "Some Series 101 Vol 11.cbz"])
    assert [(r.kind, _u(r.volume), _u(r.chapter)) for r in chapters] == [
        (Kind.CHAPTER, "10", "99"), (Kind.CHAPTER, "11", "100"), (Kind.CHAPTER, "11", "101")]
    copies = parse_names(["Some Series 100 Vol 11 A.cbz", "Some Series 100 Vol 11 A (1).cbz"])
    assert [(r.kind, _u(r.chapter)) for r in copies] == [(Kind.CHAPTER, "100"), (Kind.CHAPTER, "100")]


def test_with_series_head_keeps_a_given_head():
    given = ParseContext(series_head=SeriesHead("other", True))
    assert with_series_head(given, ["Robo 009 v01.cbz", "Robo 009 v02.cbz"]) is given
    assert with_series_head(None, ["Robo 009 v01.cbz", "Robo 009 v02.cbz"]).series_head == SeriesHead("robo", False)
    assert with_series_head(None, ["Robo 009 v01.cbz"]) is None


def test_the_scan_reads_each_folder_with_its_series_head(tmp_path):
    from mangalist.scanner import _parse_context, _walk_manga_folder

    folder = tmp_path / "A Different Folder Name"
    folder.mkdir()
    for n in ("Robo 009 v01.cbz", "Robo 009 v02.cbz", "Robo 009 v03.cbz"):
        (folder / n).write_bytes(b"x")
    hits = _walk_manga_folder(folder, context=_parse_context(folder.name))
    assert sorted((_u(h.parsed.volume), _u(h.parsed.chapter)) for h in hits) == [("1", None), ("2", None), ("3", None)]


# --- 2. "[v2]" is a release revision ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, vol, ch, group", [
    ("[Grp] Some Title - c007 [v2].cbz", None, "7", None),
    ("Some Title c007 [v2] (Grp).cbz", None, "7", "Grp"),       # the revision is not the group either
    ("Some Title c007 (v3).cbz", None, "7", None),
    ("Some Title c007 (v02).cbz", "2", "7", None),              # zero-padded: the volume
    ("Some Title c107 (v10).cbz", "10", "107", None),           # two digits: the volume
    ("Some Title c007 (Vol. 3).cbz", "3", "7", None),           # a volume word: the volume
])
def test_a_single_digit_v_tag_is_a_revision(name, vol, ch, group):
    r = parse_name(name)
    assert (r.kind, _u(r.volume), _u(r.chapter), r.group) == (Kind.CHAPTER, vol, ch, group)


# --- 3. comics / BD grammar ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, vol", [
    ("Some Comic Tome 3.cbz", "3"),
    ("Some Comic - Tome 03 (2019) (Digital) (Grp).cbz", "3"),
    ("Some Comic Tomo 3.cbz", "3"),
    ("Some Comic Band 3.cbz", "3"),
    ("Some Comic Deel 12.cbz", "12"),
    ("Some Comic Album 2.cbz", "2"),
    ("Some Comic Livre 1.cbz", "1"),
    ("Some Comic T03.cbz", "3"),
    ("Some Comic T1-T3.cbz", "1-3"),
    ("Some Comic Tome 1-3.cbz", "1-3"),
])
def test_album_tokens_are_volumes(name, vol):
    r = parse_name(name)
    assert (r.kind, r.layer, _u(r.volume), r.chapter, r.series) == (Kind.VOLUME, Layer.RELEASE, vol, None, "Some Comic")


@pytest.mark.parametrize("name", ["Some Comic t03.cbz", "Some Comic T0003.cbz", "Brass Band.cbz", "Some Album.cbz"])
def test_no_album_token(name):
    assert parse_name(name).volume is None


@pytest.mark.parametrize("name, title, group", [
    ("Some Comic Annual 2.cbz", "Annual 2", None),
    ("Some Comic Annual.cbz", "Annual", None),
    ("Some Comic Annual #2 (2019) (Digital) (Grp).cbz", "Annual #2", "Grp"),
    ("Some Comic Special #1.cbz", "Special #1", None),
    ("Some Comic - One-Shot 1 - A Story.cbz", "One-Shot 1 - A Story", None),
    ("Some Comic Oneshot 2.cbz", "Oneshot 2", None),
    ("Some Comic FCBD 2019.cbz", "FCBD 2019", None),
    ("Some Comic Free Comic Book Day 2020.cbz", "Free Comic Book Day 2020", None),
])
def test_comics_extras_are_extras_without_a_chapter_number(name, title, group):
    r = parse_name(name)
    assert (r.kind, r.chapter, r.is_extra, r.title, r.group, r.series) == (
        Kind.CHAPTER, None, True, title, group, "Some Comic")
    assert target_name(r, ext=".cbz", series_title="Some Comic") is None      # the renamer leaves extras alone


@pytest.mark.parametrize("name", [
    "Annual Report Girl 05.cbz",          # the marker must end the name's units
    "Some Comic Special Edition.cbz",     # an edition
    "Some Comic Special.cbz",             # a title word without its own number
    "Some Comic Annual v02.cbz",          # a volume token: the release layer's
    "Some Comic Annual c003.cbz",         # a chapter token
])
def test_not_a_comics_extra(name):
    assert parse_comics_extra(name) is None


# --- 4. the site watermark as a class -----------------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Some Series 001 Read Online.cbz",
    "Some Series 001 - Read Manga Online Free.cbz",
    "Some Series 001 Read Manhwa Online.cbz",
    "Some Series 001 Read Webtoon Online Free.cbz",
    "Some Series 001 Read Online for Free.cbz",
    "Some Series 001 Read for Free.cbz",
    "Some Series 001 Free Online.cbz",
    "Some Series 001 Manga Online Free.cbz",
    "Some Series 001 | Read Comic Online.cbz",
    "Some_Series_001_Read_Online.cbz",
])
def test_a_site_watermark_is_left_out(name):
    r = parse_name(name, ParseContext(series_title="Some Series"))
    assert (r.kind, _u(r.chapter)) == (Kind.CHAPTER, "1")
    assert "a site's watermark left out of the name" in r.notes and r.name == name


@pytest.mark.parametrize("name", [
    "Some Art Online 05.cbz", "Some Series Online.cbz", "Some Series Manga Online.cbz", "Some Series Free.cbz",
    "Read or Die 03.cbz", "Online Free.cbz", "Free Online Stories 02.cbz",
])
def test_one_watermark_word_never_eats_a_title(name):
    assert "a site's watermark left out of the name" not in parse_name(name).notes
