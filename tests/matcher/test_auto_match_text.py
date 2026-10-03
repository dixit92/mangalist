"""Port of MangaPixer 1.26.1 ``tests/MangaPixer.Core.Tests/Metadata/AutoMatch/AutoMatchTextTests.cs``
(creator hints and provider disambiguators; all names synthetic)."""

from __future__ import annotations

import pytest

from mangalist.matcher.auto_match_text import creator_hints, disambiguator_tag


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Until We Meet [Family Given] .cbz", ["Family Given"]),
        ("Hop Step! [Onlyname].cbz", ["Onlyname"]),
        ("Some Words [Joined Hands] (Family Given)", ["Joined Hands", "Family Given"]),
        ("Some Words [Joined Hands] (Family Given) (2019)", ["Joined Hands", "Family Given"]),
        ("[Family Given] Some Words", ["Family Given"]),
        ("(C99) [Circle Name (Family Given)] Some Words (Parody)", ["Circle Name", "Family Given", "Parody"]),
        ("Family Given] Some Words.cbz", ["Family Given"]),
        ("Some Words [Family Given.cbz", ["Family Given"]),
        ("Some Words (2019)", []),
        ("Some Words (Digital)", []),
        ("Some Words (Vol. 3)", []),
        ("[Only A Tag]", []),
        ("", []),
    ],
)
def test_creator_hints_are_the_bracketed_and_unmatched_names(name, expected):
    assert list(creator_hints(name)) == expected


@pytest.mark.parametrize(
    ("title", "tag"),
    [
        ("Sprout (FAMILY Given)", "FAMILY Given"),
        ("Sprout", None),
        ("Sprout (2019)", None),
    ],
)
def test_disambiguator_tag_is_the_provider_author_suffix(title, tag):
    assert disambiguator_tag(title) == tag


# --- 1.27.0 - 1.31.1 (ported from MangaPixer 1.31.1 AutoMatchTextTests) -------------------------

from decimal import Decimal  # noqa: E402

from mangalist.matcher import auto_match_text as amt  # noqa: E402


@pytest.mark.parametrize(("name", "expected"), [
    ("Some Title v03 (Digital).cbz", 3),
    ("Some Title Vol. 01-05.cbz", 5),
    ("Some Title v02.5.cbz", 2),
    ("Some Title - Chapter 012.cbz", None),  # a chapter, not a volume
    ("Some Title Extra.cbz", None),
])
def test_volume_number_of_is_the_highest_stated_volume(name, expected):
    assert amt.volume_number_of(name) == expected


@pytest.mark.parametrize(("name", "expected"), [
    ("Some Title - Chapter 012.cbz", 12),
    ("Some Title c045.5.cbz", 45),
    ("Some Title Ch. 001-010.cbz", 10),
    ("001 [Chapter Title].cbz", 1),
    ("0150 [Chapter Title].cbz", 150),
    ("Some Title v01.cbz", None),
    ("Some Title 001.cbz", None),  # no chapter token: not chapter-like
])
def test_chapter_number_of_is_the_highest_stated_chapter(name, expected):
    assert amt.chapter_number_of(name) == expected


# Unit numbers v2 (1.29.0): volume, volume end, chapter, chapter end ("" = None), extra.
@pytest.mark.parametrize(("name", "volume", "volume_end", "chapter", "chapter_end", "extra"), [
    ("Some Title v03 (Digital).cbz", "3", "", "", "", False),
    ("Some Title c045.5.cbz", "", "", "45.5", "", True),
    ("Some Title v02.5.cbz", "2.5", "", "", "", True),
    ("Some Title v03 c012.cbz", "3", "", "12", "", False),
    ("Some Title Vol.3 Ch.12.5.cbz", "3", "", "12.5", "", True),
    ("Some Title Vol. 01-05.cbz", "1", "5", "", "", False),
    ("Some Title v01-v05.cbz", "1", "5", "", "", False),
    ("Some Title c010-012.cbz", "", "", "10", "12", False),
    ("Some Title Ch. 001-010.cbz", "", "", "1", "10", False),
    ("Some Title - Chapter 012.cbz", "", "", "12", "", False),
    ("Some Title - Episode 7.cbz", "", "", "7", "", False),
    # 1.31.1: "Episode N" next to a volume is a part / arc, not a chapter (each arc's volumes restart at 1).
    ("Saga of Tides - Episode 1 - The First Storm v01 (2-in-1 Edition) (2012) (Digital).cbz", "1", "", "", "", False),
    ("Saga of Tides - Episode 3 - The Third Storm v02 (3-in-1 Edition).cbz", "2", "", "", "", False),
    ("Some Title v02 Ep. 5.cbz", "2", "", "", "", False),
    ("Some Title v02 Chapter 5.cbz", "2", "", "5", "", False),  # a real chapter word still counts
    ("Some Title #4.cbz", "", "", "4", "", False),
    ("001 [Chapter Title].cbz", "", "", "1", "", False),
    ("000.cbz", "", "", "0", "", False),
    ("012.5 [Side Story].cbz", "", "", "12.5", "", True),
    ("001-003 [Chapter Titles].cbz", "", "", "1", "3", False),
    ("2019 [Chapter Title].cbz", "", "", "", "", False),  # a leading year is not a chapter
    ("Some Title v03 - 2019.cbz", "3", "", "", "", False),  # a year never ends a range
    ("Some Title 001.cbz", "", "", "", "", False),  # a title and no token: not a unit
    ("Some Title (Vol. 3).cbz", "3", "", "", "", False),  # only a bracket names the unit
    ("[Group] Some Title - c007 [v2].cbz", "", "", "7", "", False),  # [v2] is a release revision
    ("[Group] Some Title [v2].cbz", "", "", "", "", False),
    ("Some Title Extra.cbz", "", "", "", "", False),
    ("", "", "", "", "", False),
])
def test_units_of_keeps_decimals_both_numbers_and_ranges(name, volume, volume_end, chapter, chapter_end, extra):
    def d(s):
        return Decimal(s) if s else None
    assert amt.units_of(name) == amt.UnitNumbers(d(volume), d(volume_end), d(chapter), d(chapter_end), extra)


@pytest.mark.parametrize(("name", "expected"), [
    ("Some Title c045.5.cbz", (None, 45)),
    ("Some Title v03 c012.cbz", (None, 12)),
    ("Some Title Vol. 01-05.cbz", (5, None)),
    ("001 [Chapter Title].cbz", (None, 1)),
])
def test_units_of_leaves_the_matcher_integers_alone(name, expected):
    assert not amt.units_of(name).is_empty
    assert (amt.volume_number_of(name), amt.chapter_number_of(name)) == expected


@pytest.mark.parametrize(("name", "expected"), [
    ("Some Title by Family Given.cbz", ["Family Given"]),
    ("Some Title - Chapter 012 | Family Given.cbz", ["Family Given", "Some Title"]),
    # Either order: both name-like parts of a dash are hints (a hint only counts when a record's authors name it).
    ("Family Given - Some Title", ["Family Given", "Some Title"]),
    ("Some Title 2 - The Return", ["The Return"]),
    ("Some Title - Chapter 012", ["Some Title"]),
    ("Some Title v01 - 2019", []),
])
def test_creator_hints_read_plain_separators_in_either_order(name, expected):
    assert list(creator_hints(name)) == expected


@pytest.mark.parametrize(("name", "expected"), [
    ("Some Title by Family Given.cbz", ["Some Title"]),
    ("Some Title - Chapter 012 | Family Given.cbz", ["Some Title - Chapter 012"]),
    ("Family Given - Some Title", ["Some Title"]),
    ("Frieren - Beyond the End", []),  # a one-word head is a title, not an author
    ("Stand by 2 Me", []),  # not a name after "by"
    ("Some Title", []),
])
def test_creator_split_titles_are_the_title_part(name, expected):
    assert list(amt.creator_split_titles(name)) == expected


@pytest.mark.parametrize(("tag", "expected"), [
    ("HATA Kenjiro", True),
    ("JO Yongseok", True),
    ("Webtoon", False),
    ("Pre-serialization", False),
    ("Some Studio", False),
    (None, False),
])
def test_is_person_tag_is_a_mangaupdates_style_name(tag, expected):
    assert amt.is_person_tag(tag) == expected


def test_disambiguated_aliases_count_in_full_only_when_the_tag_names_the_records_author():
    aliases = amt.disambiguated_aliases(["Moon Letter (SATO Hana)", "Other Name (KATO Ken)", "No Tag", None], ["SATO Hana"])
    assert aliases == [("Moon Letter", 1.0), ("Other Name", amt.DISAMBIGUATED_ALIAS_FACTOR)]

    assert amt.best_title_score(["Moon Letter"], "Tsuki no Tegami", ["Moon Letter (SATO Hana)"], ["SATO Hana"]) == pytest.approx(1.0, abs=5e-4)
    assert amt.best_title_score(["Moon Letter"], "Tsuki no Tegami", ["Moon Letter (SATO Hana)"]) == pytest.approx(amt.DISAMBIGUATED_ALIAS_FACTOR, abs=5e-4)
    assert amt.best_title_score(["Look Up"], "Look Up (SATO Hana)", []) == pytest.approx(1.0, abs=5e-4)  # the main title strips in full
