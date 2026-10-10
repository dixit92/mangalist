"""mangalist.naming: the library naming scheme (owner, 2026-10-10) - format, round trips through the parser,
sanitising, the length rule, Windows paths, the volume lookup and target_name's decisions. Synthetic names only."""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from mangalist import naming
from mangalist.naming import (
    CHAPTER_SCHEME,
    DEFAULT_SCHEMES,
    ELLIPSIS,
    VOLUME_SCHEME,
    NameLimits,
    chapter_file_name,
    chapter_name,
    name_fits,
    sanitize_group,
    sanitize_series,
    sanitize_title,
    target_name,
    volume_file_name,
    volume_lookup,
    volume_name,
    volumes_from_list,
    windows_path,
)
from mangalist.parsing import Kind, Layer, ParseContext, parse_name

D = Decimal
WIN = NameLimits(windows_server="SERVER")


# --- the format ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs, expected", [
    (dict(chapter=D("102")), "Ch. 0102.00.cbz"),
    (dict(chapter="10.5"), "Ch. 0010.50.cbz"),
    (dict(chapter=7), "Ch. 0007.00.cbz"),
    (dict(chapter="0"), "Ch. 0000.00.cbz"),
    (dict(chapter="291.999"), "Ch. 0291.999.cbz"),
    (dict(chapter="12.50"), "Ch. 0012.50.cbz"),
    (dict(chapter="12345.1"), "Ch. 12345.10.cbz"),
    (dict(chapter="102", volume=12), "Ch. 0102.00 Vol. 012.cbz"),
    (dict(chapter="102", volume="1.5"), "Ch. 0102.00 Vol. 001.5.cbz"),
    (dict(chapter="102", title="A Title"), "Ch. 0102.00 (A Title).cbz"),
    (dict(chapter="102", group="Team"), "Ch. 0102.00 [Team].cbz"),
    (dict(chapter="102", volume=12, title="A Title", group="Team"), "Ch. 0102.00 Vol. 012 (A Title) [Team].cbz"),
    (dict(chapter="10", chapter_end="12"), "Ch. 0010.00-0012.00.cbz"),
    (dict(chapter="10", chapter_end="10"), "Ch. 0010.00.cbz"),
    (dict(chapter="10", chapter_end="12.5", volume=2, group="G"), "Ch. 0010.00-0012.50 Vol. 002 [G].cbz"),
    (dict(chapter="5", ext=".zip"), "Ch. 0005.00.zip"),
    (dict(chapter="5", ext="cbr"), "Ch. 0005.00.cbr"),
    (dict(chapter="5", title="  ", group=""), "Ch. 0005.00.cbz"),
])
def test_chapter_names(kwargs, expected):
    chapter = kwargs.pop("chapter")
    assert chapter_file_name(chapter, **kwargs) == expected


@pytest.mark.parametrize("args, kwargs, expected", [
    (("Some Series", 1), {}, "Some Series - Vol. 001.cbz"),
    (("Some Series", "12"), dict(group="Grp"), "Some Series - Vol. 012 [Grp].cbz"),
    (("Some Series", 1), dict(volume_end=3), "Some Series - Vol. 001-003.cbz"),
    (("Some Series", "10.5"), {}, "Some Series - Vol. 010.5.cbz"),
    (("Some Series", 100), {}, "Some Series - Vol. 100.cbz"),
    (("Series 2", 3), {}, "Series 2 - Vol. 003.cbz"),
])
def test_volume_names(args, kwargs, expected):
    assert volume_file_name(*args, **kwargs) == expected


def test_every_chapter_name_sorts_in_reading_order():
    names = [chapter_file_name(c, volume=v) for c, v in
             [("1", None), ("2", 1), ("2.5", None), ("10", None), ("10.1", 2), ("100", None), ("1000", 30)]]
    assert sorted(names) == names
    # a volume learned later never moves the file
    assert sorted(names + [chapter_file_name("3")]).index(chapter_file_name("3")) == 3


@pytest.mark.parametrize("bad", [None, "", "abc", "-1", D("-2"), 1.5, True])
def test_bad_numbers_raise(bad):
    with pytest.raises(ValueError):
        chapter_file_name(bad)


def test_bad_ranges_and_titles_raise():
    with pytest.raises(ValueError):
        chapter_file_name("12", chapter_end="10")
    with pytest.raises(ValueError):
        volume_file_name("  ", 1)
    with pytest.raises(ValueError):
        volume_file_name("???", 1)


# --- round trips: render -> parse -> the same values ------------------------------------------------------------

CHAPTERS = [
    ("1", None, None, None, None),
    ("102", None, "12", "Are you giving it another shot", "Some Scans"),
    ("10.5", None, "2", None, "G"),
    ("291.999", None, "30", "Title", None),
    ("0.1", None, None, "Prologue", None),
    ("10", "12", None, None, None),
    ("10", "12.5", "3", "A Triple", "Team"),
    ("12345", None, "999", None, None),
    ("45", None, "5", "The Vol 2 Begins", "Group"),                         # unit words in the title
    ("46", None, None, "Chapter 5 of the Ch. 3 Arc", "Ch. 99 Group"),       # ... and in the group
    ("47", None, None, "v3 c4 Ch. 5 - Back to Vol. 2", "c4 Scans"),
    ("48", None, None, "Memories in the Mist [2]", "Team [X]"),             # brackets inside title / group
    ("49", None, None, "Who (are) you?", "A [B] C"),
    ("50", None, None, "Part 1: Start / End \\ \"Q\" <x> | y*", "G: 1/2"),  # Windows-illegal characters
    ("51", None, None, "日本語のタイトル", "グループ"),  # unicode
    ("52", None, None, "Café à la crème \U0001f600", "Équipe"),
    ("53", None, None, "Ends with a dot.", "Group."),
    ("54", None, None, "  many   spaces\tand\ttabs  ", "  g  "),
    ("55", None, None, "4th Year Anniversary - 10-20 Years", "Team v2"),
    ("56", None, None, "(Whole title in parentheses)", "[Whole group in brackets]"),
    ("57", None, None, "Ch. 0010.00 (inner) [x]", None),                   # a title that looks like a scheme name
]


@pytest.mark.parametrize("chapter, end, volume, title, group", CHAPTERS)
def test_chapter_round_trip(chapter, end, volume, title, group):
    name = chapter_file_name(chapter, chapter_end=end, volume=volume, title=title, group=group)
    for ctx in (None, ParseContext(schemes=DEFAULT_SCHEMES), ParseContext(kind_hint="volumes", series_title="X")):
        p = parse_name(name, ctx)
        assert (p.layer, p.kind) == (Layer.SCHEME, Kind.CHAPTER), name
        assert p.chapter.start == D(chapter) and p.chapter.end == D(end or chapter)
        assert (p.volume.start if p.volume else None) == (D(volume) if volume else None)
        assert p.title == sanitize_title(title) and p.group == sanitize_group(group)
        assert not p.is_extra and not p.guessed
        # rendering the parse again gives the same name
        assert chapter_file_name(p.chapter.start, chapter_end=p.chapter.end if p.chapter.is_range else None,
                                 volume=p.volume.start if p.volume else None, title=p.title, group=p.group) == name


@pytest.mark.parametrize("series, volume, end, group", [
    ("Some Series", "1", None, None),
    ("Some Series", "12", None, "Grp"),
    ("Some Series", "1", "3", "Grp"),
    ("Some Series", "10.5", None, None),
    ("Some Series - Part 5 - Sub", "5", None, "Team [X]"),
    ("Series 2", "3", None, None),
    ("Title [English Title]", "2", None, "G"),
    ("Title: With / Illegal? Characters*", "7", None, None),
    ("日本語", "1", None, "グループ"),
    ("Vol. 2 Is In The Title", "4", None, None),
])
def test_volume_round_trip(series, volume, end, group):
    name = volume_file_name(series, volume, volume_end=end, group=group)
    p = parse_name(name)
    assert (p.layer, p.kind) == (Layer.SCHEME, Kind.VOLUME), name
    assert p.volume.start == D(volume) and p.volume.end == D(end or volume)
    assert p.series == sanitize_series(series) and p.group == sanitize_group(group)
    assert p.chapter is None


def test_two_decimals_read_back_as_the_number():
    p = parse_name("Ch. 0102.00.cbz")
    assert str(p.chapter) == "102" and not p.is_fraction
    p = parse_name("Ch. 0010.50.cbz")
    assert str(p.chapter) == "10.5" and p.is_fraction
    assert str(parse_name("Ch. 0291.999.cbz").chapter) == "291.999"


def test_scheme_names_never_leak_units_from_the_title():
    # Section 12: the generic layer read vol 2 / ch 3-10 from these before the scheme layer existed.
    p = parse_name("Ch. 0010.00 (The Vol 2 Begins) [Group].cbz")
    assert (str(p.chapter), p.volume, p.title) == ("10", None, "The Vol 2 Begins")
    p = parse_name("Ch. 0010.00 (Chapter 5 of the Ch. 3 Arc) [Group].cbz")
    assert (str(p.chapter), p.volume) == ("10", None)
    p = parse_name("Ch. 0010.00 Vol. 002 (Memories in the Mist [2]) [Group].cbz")
    assert (str(p.chapter), str(p.volume), p.title) == ("10", "2", "Memories in the Mist [2]")


def test_the_schemes_are_templates():
    assert CHAPTER_SCHEME == "Ch. %CN4{-%CE4}{ Vol. %V3}{ (%CT)}{ [%G]}"
    assert VOLUME_SCHEME == "%T{ %SQ} - Vol. %V3{-%VE3}{ [%G]}"
    assert DEFAULT_SCHEMES == (CHAPTER_SCHEME, VOLUME_SCHEME)


def test_a_custom_root_scheme_comes_first_and_the_default_still_reads():
    from mangalist.parsing import FMD2_CHAPTER_SCHEME

    ctx = ParseContext(schemes=FMD2_CHAPTER_SCHEME)
    assert parse_name("0012 [Vol. 0003 Ch. 0012.5 - T [G]].cbz", ctx).notes[0] == f"scheme {FMD2_CHAPTER_SCHEME!r}"
    p = parse_name("Ch. 0012.50 Vol. 003 (T) [G].cbz", ctx)
    assert (p.layer, str(p.chapter), str(p.volume)) == (Layer.SCHEME, "12.5", "3")


# --- sanitising -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, title, group", [
    ("Plain", "Plain", "Plain"),
    ("A (B) C", "A [B] C", "A (B) C"),
    ("A [B] C", "A [B] C", "A (B) C"),
    ("Part 1: Start", "Part 1 - Start", "Part 1 - Start"),
    ("12:30", "12-30", "12-30"),
    ("A/B\\C|D", "A-B-C-D", "A-B-C-D"),
    ('Say "hi"', "Say 'hi'", "Say 'hi'"),
    ("<x>", "[x]", "(x)"),
    ("Who? Me*", "Who Me", "Who Me"),
    ("a\x00b\x1fc\x7fd", "a b c d", "a b c d"),
    ("  lots \n of\t space  ", "lots of space", "lots of space"),
    ("?*", None, None),
    ("", None, None),
    (None, None, None),
    ("タイトル：副題", "タイトル：副題",
     "タイトル：副題"),                        # full-width colon is legal
])
def test_sanitising(text, title, group):
    assert sanitize_title(text) == title
    assert sanitize_group(text) == group


def test_series_titles_keep_brackets_and_lose_a_trailing_dot():
    assert sanitize_series("Title [English] (2019)") == "Title [English] (2019)"
    assert sanitize_series("Title: Sub...") == "Title - Sub"
    assert sanitize_series("<Title>") == "(Title)"


def test_illegal_characters_never_reach_a_name():
    name = chapter_file_name("1", volume=1, title='<>:"/\\|?*\x01 T', group='<>:"/\\|?*\x02 G')
    assert not any(c in name for c in '<>:"/\\|?*') and not any(ord(c) < 32 for c in name)
    name = volume_file_name('S<>:"/\\|?*\x03', 1, group="G")
    assert not any(c in name for c in '<>:"/\\|?*') and not name.endswith((" ", "."))


# --- the length rule --------------------------------------------------------------------------------------------

def test_the_group_is_always_capped():
    r = chapter_name("1", group="G" * 40)
    assert r.name == f"Ch. 0001.00 [{'G' * 31}{ELLIPSIS}].cbz" and r.shortened == ("group",) and r.fits
    assert chapter_file_name("1", group="G" * 32) == f"Ch. 0001.00 [{'G' * 32}].cbz"
    assert chapter_file_name("1", group="G" * 10, limits=NameLimits(group_cap=5)) == f"Ch. 0001.00 [GGGG{ELLIPSIS}].cbz"
    assert volume_file_name("S", 1, group="G" * 40).endswith(f"[{'G' * 31}{ELLIPSIS}].cbz")


def test_a_long_title_is_cut_to_255_bytes():
    r = chapter_name("102", volume=12, title="word " * 100, group="Team")
    assert r.fits and r.shortened == ("title",)
    assert len(r.name.encode("utf-8")) <= 255
    assert r.name.startswith("Ch. 0102.00 Vol. 012 (word") and r.name.endswith(f"{ELLIPSIS}) [Team].cbz")
    p = parse_name(r.name)
    assert (str(p.chapter), str(p.volume), p.group) == ("102", "12", "Team") and p.title.endswith(ELLIPSIS)
    assert not p.title[:-1].endswith(" ")                     # no space before the ellipsis


def test_bytes_not_characters_count():
    r = chapter_name("1", title="Ü" * 200)               # 200 characters, 400 bytes
    assert r.fits and r.shortened == ("title",)
    assert 250 <= len(r.name.encode("utf-8")) <= 255 and len(r.name) < 140
    assert chapter_name("1", title="U" * 200).name.count("U") > r.name.count("Ü")


def test_the_windows_path_limit():
    folder = "/data/Comics/Manga/" + "F" * 150
    r = chapter_name("1", volume=2, title="T" * 120, group="Grp", folder=folder, limits=WIN)
    wp = windows_path(f"{folder}/{r.name}", "SERVER")
    assert r.fits and len(wp) <= 259 and r.shortened == ("title",)
    assert r.name.endswith(f"{ELLIPSIS}) [Grp].cbz")
    # without a server name (or outside /data/<share>) only the 255-byte rule applies
    assert chapter_name("1", title="T" * 120, folder=folder).shortened == ()
    assert chapter_name("1", title="T" * 120, folder="/mnt/other/" + "F" * 200, limits=WIN).shortened == ()


def test_windows_counts_utf16_units():
    folder = "/data/R/" + "F" * 200
    r = chapter_name("1", title="\U0001f600" * 40, folder=folder, limits=WIN)      # each emoji is 2 UTF-16 units
    wp = windows_path(f"{folder}/{r.name}", "SERVER")
    assert r.fits and len(wp.encode("utf-16-le")) // 2 <= 259


def test_then_the_group_is_cut_never_the_number_or_volume():
    folder = "/data/R/" + "F" * 212
    r = chapter_name("102", volume=12, title="A Title", group="G" * 30, folder=folder, limits=WIN)
    assert r.fits and r.shortened == ("title", "group")
    assert r.name.startswith("Ch. 0102.00 Vol. 012") and r.name.endswith(".cbz")
    assert "(" not in r.name                                  # the title went first
    p = parse_name(r.name)
    assert (str(p.chapter), str(p.volume), p.title) == ("102", "12", None) and p.group.endswith(ELLIPSIS)


def test_too_long_even_without_title_and_group_is_flagged(caplog):
    folder = "/data/R/" + "F" * 300
    r = chapter_name("1", volume=1, title="T", group="G", folder=folder, limits=WIN)
    assert r.name == "Ch. 0001.00 Vol. 001.cbz" and not r.fits and r.shortened == ("title", "group")
    assert chapter_file_name("1", volume=1, title="T", group="G", folder=folder, limits=WIN) == r.name
    assert "longer than the length rule" in caplog.text


def test_volume_names_shorten_the_group_then_the_series_title():
    r = volume_name("S" * 300, 1, group="Grp")
    assert r.fits and r.shortened == ("group", "series") and len(r.name.encode()) <= 255
    assert r.name.endswith(f"{ELLIPSIS} - Vol. 001.cbz")
    p = parse_name(r.name)
    assert (p.kind, str(p.volume)) == (Kind.VOLUME, "1")
    r = volume_name("Some Series", 1, group="Grp", folder="/data/R/" + "F" * 220, limits=WIN)
    assert r.fits and r.shortened == ("group",) and r.name == "Some Series - Vol. 001.cbz"


def test_name_fits():
    assert name_fits("x" * 255) and not name_fits("x" * 256)
    assert not name_fits("Ü" * 128)
    assert name_fits("x" * 200, folder="/data/R/" + "F" * 100)
    assert not name_fits("x" * 200, folder="/data/R/" + "F" * 100, limits=WIN)
    assert name_fits("x" * 100, folder="/data/R/" + "F" * 100, limits=NameLimits(windows_server="S",
                                                                                     max_windows_path=300))


# --- Windows paths ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, server, expected", [
    ("/data/Comics/Manga/Series/Ch. 0001.00.cbz", "SERVER", "\\\\SERVER\\Comics\\Manga\\Series\\Ch. 0001.00.cbz"),
    ("/data/Comics", "SERVER", "\\\\SERVER\\Comics"),
    ("/data/Comics/", "SERVER", "\\\\SERVER\\Comics"),
    ("/data//Comics//a", "SERVER", "\\\\SERVER\\Comics\\a"),
    ("/data/Comics/a", "\\\\SERVER", "\\\\SERVER\\Comics\\a"),
    ("/data/Comics/a", " SERVER\\ ", "\\\\SERVER\\Comics\\a"),
    ("/data/My Share/a b/c", "srv", "\\\\srv\\My Share\\a b\\c"),
    ("/data", "SERVER", None),
    ("/data/", "SERVER", None),
    ("/mnt/user/Comics/a", "SERVER", None),
    ("/database/x", "SERVER", None),
    ("relative/data/x", "SERVER", None),
    ("/data/Comics/../x", "SERVER", None),
    ("/data/Comics/a", "", None),
    ("", "SERVER", None),
])
def test_windows_path(path, server, expected):
    assert windows_path(path, server) == expected


# --- the volume a chapter belongs to ------------------------------------------------------------------------------

def _vol(volume, lo=None, hi=None):
    return SimpleNamespace(volume=volume, chapters_from=lo, chapters_to=hi)


VOLUME_LIST = [_vol("1", "1", "8"), _vol("2", "9", "17"), _vol("3", "18", "26"), _vol("4"), _vol("5", "40", "38"),
               _vol("6", "45", "50"), _vol("7", "50", "55"), _vol("2", "100", "110")]


@pytest.mark.parametrize("chapter, volume", [
    ("1", "1"), ("8", "1"), ("9", "2"), ("17", "2"), ("12.5", "2"), ("8.5", None),   # 8.5 is between volumes
    ("17.1", None), ("26", "3"), ("27", None),
    ("39", None),                      # volume 5's range is backwards: skipped
    ("45", "6"), ("50", None),         # in two overlapping ranges: ambiguous
    ("52", "7"), ("105", None),        # the second "volume 2" entry is ignored (the first wins)
    ("0", None),
])
def test_volumes_from_list(chapter, volume):
    lookup = volumes_from_list(VOLUME_LIST)
    assert lookup(D(chapter)) == (D(volume) if volume else None)


def test_volumes_from_an_empty_list():
    assert volumes_from_list([])(D("1")) is None
    assert volumes_from_list(None)(D("1")) is None


def test_volume_lookup_reads_mangapixers_list(monkeypatch):
    import mangalist.downloads.placement as placement
    import mangalist.upgrades as upgrades

    series = SimpleNamespace(id=7, root_id=1, rel_path="Some Series")
    asked = []
    monkeypatch.setattr(placement, "locate_series", lambda db, sid: (series, None, "/data/R/Some Series"))

    def fake(db, s):
        asked.append(s)
        return [_vol("1", "1", "8"), _vol("2", "9", "17")], ""

    monkeypatch.setattr(upgrades, "_knowledge_volumes", fake)
    lookup = volume_lookup(object(), 7)
    assert asked == [series]
    assert lookup(D("3")) == D("1") and lookup(D("9.5")) == D("2") and lookup(D("18")) is None


@pytest.mark.parametrize("why", ["no list", "unknown series", "broken"])
def test_volume_lookup_without_a_list_knows_no_volume(monkeypatch, why):
    import mangalist.downloads.placement as placement
    import mangalist.upgrades as upgrades

    def locate(db, sid):
        if why == "unknown series":
            raise LookupError("series 7 is no longer in the library database")
        return SimpleNamespace(id=7, root_id=1, rel_path="S"), None, "/x"

    def volumes(db, s):
        if why == "broken":
            raise RuntimeError("boom")
        return None, "MangaPixer has no volume list for this series"

    monkeypatch.setattr(placement, "locate_series", locate)
    monkeypatch.setattr(upgrades, "_knowledge_volumes", volumes)
    assert volume_lookup(object(), 7)(D("1")) is None


# --- target_name ------------------------------------------------------------------------------------------------

def _t(name, series="Some Series", volume_of=None, **kw):
    return target_name(parse_name(name, kw.pop("ctx", None)), ext=Path(name).suffix or ".cbz", series_title=series,
                       volume_of=volume_of, **kw)


@pytest.mark.parametrize("name, expected", [
    ("0126 [Ch. 0102 - Are you giving it another shot [Last Minute Scans]].cbz",
     "Ch. 0102.00 (Are you giving it another shot) [Last Minute Scans].cbz"),
    ("0040 [Vol. 0004 Ch. 0040.5 - Title [G]].cbz", "Ch. 0040.50 Vol. 004 (Title) [G].cbz"),
    ("0060 [Ch. 60 - Back to Vol. 2 (Part 1) [Team [X]]].cbz", "Ch. 0060.00 (Back to Vol. 2 [Part 1]) [Team (X)].cbz"),
    ("0020 [Ch. 10-12 - Triple].cbz", "Ch. 0010.00-0012.00 (Triple).cbz"),
    ("Some Series c012 (2020) (Grp).cbz", "Ch. 0012.00 [Grp].cbz"),
    ("Some Series - c001 (v01) [Grp].cbz", "Ch. 0001.00 Vol. 001 [Grp].cbz"),
    ("Some Series v01 (2019) (Digital) (Grp).cbz", "Some Series - Vol. 001 [Grp].cbz"),
    ("Some Series v01-03 (2019) (Digital) (Grp).cbz", "Some Series - Vol. 001-003 [Grp].cbz"),
    ("Other Name - Part 5 - Sub v05 (2022) (Digital) (Grp).cbz", "Some Series Part 5 - Vol. 005 [Grp].cbz"),
    ("0012 [Contact. 0001 - Hello [G]].cbz", "Ch. 0001.00 (Hello) [G].cbz"),
    ("0076 [0076  The Arc Title (3)].cbz", "Ch. 0076.00 (The Arc Title [3]).cbz"),
    ("Some Series - Episode 35 (2023) (Digital) (Grp).cbz", "Ch. 0035.00 [Grp].cbz"),
    ("Vol. 1 Ch 3.cbz", "Ch. 0003.00 Vol. 001.cbz"),
    ("Ch. 0010.00 (The Vol 2 Begins) [G].cbz", "Ch. 0010.00 (The Vol 2 Begins) [G].cbz"),   # already in the scheme
    ("Some Series - Vol. 002 [G].cbz", "Some Series - Vol. 002 [G].cbz"),
    ("0001 [Ch. 1].CBZ", "Ch. 0001.00.CBZ"),
])
def test_target_names(name, expected):
    assert _t(name) == expected


def test_target_names_are_read_back_as_the_same_units():
    for name in ("0040 [Vol. 0004 Ch. 0040.5 - Title [G]].cbz", "Some Series v01-03 (2019) (Digital) (Grp).cbz",
                 "0020 [Ch. 10-12 - Triple].cbz"):
        before, after = parse_name(name), parse_name(_t(name))
        assert (after.kind, after.volume, after.chapter, after.title, after.group) == \
            (before.kind, before.volume, before.chapter, before.title, before.group)


def test_the_volume_lookup_fills_a_missing_volume():
    lookup = volumes_from_list([_vol("1", "1", "8"), _vol("2", "9", "17")])
    assert _t("0003 [Ch. 3 - T].cbz", volume_of=lookup) == "Ch. 0003.00 Vol. 001 (T).cbz"
    assert _t("0012 [Ch. 12.5].cbz", volume_of=lookup) == "Ch. 0012.50 Vol. 002.cbz"
    assert _t("0020 [Ch. 20].cbz", volume_of=lookup) == "Ch. 0020.00.cbz"                 # not in the list
    assert _t("0009 [Ch. 7-9].cbz", volume_of=lookup) == "Ch. 0007.00-0009.00.cbz"         # spans two volumes
    assert _t("0009 [Ch. 9-10].cbz", volume_of=lookup) == "Ch. 0009.00-0010.00 Vol. 002.cbz"
    # the name's own volume wins over the list
    assert _t("0003 [Vol. 2 Ch. 3].cbz", volume_of=lookup) == "Ch. 0003.00 Vol. 002.cbz"
    # a broken lookup means "not known"
    assert _t("0003 [Ch. 3].cbz", volume_of=lambda c: 1 / 0) == "Ch. 0003.00.cbz"


@pytest.mark.parametrize("name, why", [
    ("Some Series.cbz", "no units"),
    ("01.cbz", "a bare number: volumes or chapters?"),
    ("Some Series 07.cbz", "a bare number after the title: a chapter only by guess"),
    ("Some Series 012 (2019) (Digital) (G).cbz", "the same, in a release name"),
    ("0101 [Omake [G]].cbz", "an extra without a number"),
    ("0100 [Oneshot].cbz", "a one-shot without a number"),
    ("0005 [Vol. 2 Ch. Extra].cbz", "Ch. Extra"),
    ("Some Series v10 + 085-086 (2021) (Digital) (G).cbz", "a volume with loose chapters"),
    ("Title Vol 3 Vol 4.cbz", "the generic layer found two volume numbers"),
    ("Title Ch. 1 Ch. 2 Ch. 3.cbz", "... or several chapter numbers"),
    ("0003 [Vol. 1-2 Ch. 5].cbz", "a chapter that names a volume range"),
])
def test_files_left_alone(name, why):
    assert _t(name) is None, why


def test_a_volume_needs_a_series_title():
    assert _t("Some Series v01 (2019) (Digital) (G).cbz", series=None) is None
    assert _t("Some Series v01 (2019) (Digital) (G).cbz", series="  ") is None
    assert _t("Some Series v01 (2019) (Digital) (G).cbz", series="?*") is None
    assert _t("0001 [Ch. 1].cbz", series=None) == "Ch. 0001.00.cbz"            # chapters carry no series title


def test_the_series_answer_unlocks_a_guessed_chapter():
    ctx = ParseContext(kind_hint="chapters", series_title="Some Series")
    assert _t("Some Series 07.cbz", ctx=ctx) == "Ch. 0007.00.cbz"
    assert _t("Some Series 014.6  A Title (Grp).cbz", ctx=ctx) == "Ch. 0014.60 (A Title) [Grp].cbz"
    ctx = ParseContext(kind_hint="volumes", series_title="Some Series")
    assert _t("Some Series 07.cbz", ctx=ctx) == "Some Series - Vol. 007.cbz"


def test_target_name_applies_the_length_rule():
    folder = "/data/R/" + "F" * 200
    name = _t("0001 [Ch. 1 - " + "Long " * 30 + "[Group]].cbz", folder=folder, limits=WIN)
    assert name.endswith(f"{ELLIPSIS}) [Group].cbz")
    assert len(windows_path(f"{folder}/{name}", "SERVER")) <= 259


def test_target_name_of_nothing():
    assert target_name(None, ext=".cbz", series_title="S") is None


# --- the scanner's scheme hook ----------------------------------------------------------------------------------

def test_every_root_without_a_scheme_gets_the_default():
    from mangalist.scanner import _root_schemes

    assert _root_schemes(SimpleNamespace(naming_scheme=None)) == DEFAULT_SCHEMES
    assert _root_schemes(SimpleNamespace(naming_scheme="  ")) == DEFAULT_SCHEMES
    assert _root_schemes(SimpleNamespace()) == DEFAULT_SCHEMES
    assert _root_schemes(SimpleNamespace(naming_scheme="%I4 [Ch. %C4%CF]")) == ("%I4 [Ch. %C4%CF]",)


def test_a_scan_reads_scheme_names_exactly(tmp_path):
    from mangalist.scanner import scan_root

    folder = tmp_path / "Lib" / "Some Series"
    folder.mkdir(parents=True)
    names = [chapter_file_name("10", title="The Vol 2 Begins", group="G"),
             chapter_file_name("11", volume=2, title="Chapter 5 of the Ch. 3 Arc"),
             volume_file_name("Some Series", 1, group="G")]
    for n in names:
        (folder / n).write_bytes(b"x")
    (entry,) = scan_root(tmp_path / "Lib", schemes=naming.DEFAULT_SCHEMES)
    parsed = {f.path.name: f.parsed for f in entry.files}
    assert all(p.layer is Layer.SCHEME for p in parsed.values())
    assert parsed[names[0]].volume is None and parsed[names[1]].chapter.start == D("11")
    assert entry.inventory().volumes == (D("1"),)


def test_naming_is_qt_free():
    code = "import sys, mangalist.naming; assert 'PySide6' not in sys.modules, 'PySide6 loaded'"
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, "-c", code], check=True, cwd=root)
