"""Port of MangaPixer.Core.Tests/Metadata/TitleNormalizerTests.cs (synthetic names only)."""

from __future__ import annotations

import pytest

from manga_list.matcher.normalizer import (
    DerivedTitle,
    DerivedTitleKind,
    archive_base_title,
    archive_title,
    derived_variants,
    normalize,
    number_tokens,
    scoring_form,
)


@pytest.mark.parametrize(
    ("input_", "expected"),
    [
        ("Berserk", "Berserk"),
        ("Berserk v01.cbz", "Berserk"),
        ("Berserk Vol. 3", "Berserk"),
        ("Berserk Volume 1-5", "Berserk"),
        ("Berserk Ch 12", "Berserk"),
        ("Berserk ch.12.5", "Berserk"),
        ("Berserk c003", "Berserk"),
        ("Berserk #12", "Berserk"),
        ("Berserk Chapter 10", "Berserk"),
        ("Berserk - Chapter", "Berserk"),
    ],
)
def test_normalize_strips_volume_and_chapter_tokens(input_, expected):
    assert normalize(input_).primary == expected


@pytest.mark.parametrize(
    ("input_", "expected"),
    [
        ("[Group] Some Series (Digital) {HQ}", "Some Series"),
        ("Some Series [x2]", "Some Series"),
        ("(C99) [Circle] Some Doujin", "Some Doujin"),
    ],
)
def test_normalize_removes_bracket_tags(input_, expected):
    assert normalize(input_).primary == expected


def test_normalize_trailing_english_title_becomes_second_variant():
    n = normalize("Dungeon Meshi [Delicious in Dungeon]")

    assert n.primary == "Dungeon Meshi"
    assert list(n.variants) == ["Dungeon Meshi", "Delicious in Dungeon"]


def test_normalize_single_word_or_leading_bracket_is_not_a_variant():
    assert len(normalize("Some Series [Digital]").variants) == 1
    assert len(normalize("[Scan Group Name] Some Series").variants) == 1


@pytest.mark.parametrize(
    "name",
    [
        "Some Series v00 (2008) [Scan Team Name] [OneShot].cbz",
        "Some Series [Scan Team Name] (Digital)",
        "Some Series [Scan Team Name] {HQ} v01",
        "Some Series [Vol. 0007 Ch. 5 - A Chapter Title [Scan Team Name]].cbz",
    ],
)
def test_normalize_bracket_followed_by_further_tags_is_a_group_not_a_variant(name):
    n = normalize(name)

    assert n.primary == "Some Series"
    assert list(n.variants) == ["Some Series"]


def test_normalize_trailing_english_title_before_a_year_is_still_a_variant():
    n = normalize("Dungeon Meshi [Delicious in Dungeon] (2014)")

    assert list(n.variants) == ["Dungeon Meshi", "Delicious in Dungeon"]
    assert n.year_hint == 2014


def test_normalize_year_in_parentheses_becomes_a_hint():
    n = normalize("Some Run (1989) v02")

    assert n.primary == "Some Run"
    assert n.year_hint == 1989


def test_normalize_edition_words_are_removed_and_kept_as_hints():
    n = normalize("Some Series Deluxe Omnibus v01")

    assert n.primary == "Some Series"
    assert "Deluxe" in n.edition_hints
    assert "Omnibus" in n.edition_hints


@pytest.mark.parametrize(
    ("name", "primary", "hint"),
    [
        ("SOME TITLE! Master Edition", "SOME TITLE", "Master Edition"),
        ("Some Series Perfect Edition v01", "Some Series", "Perfect Edition"),
        ("Some Series - Complete Edition", "Some Series", "Complete Edition"),
        ("Some Series Collector's Edition", "Some Series", "Collector's Edition"),
        ("Some Series Full Color Edition", "Some Series", "Full Color Edition"),
        ("Some Series Kanzenban v03", "Some Series", "Kanzenban"),
        ("Some Series (Shinsoban)", "Some Series", None),  # a bracket tag is dropped before
    ],
)
def test_normalize_edition_phrases_are_removed_and_kept_as_hints(name, primary, hint):
    n = normalize(name)

    assert n.primary == primary
    if hint is not None:
        assert hint in n.edition_hints


@pytest.mark.parametrize(
    ("name", "primary", "with_exclamation"),
    [
        ("SOME TITLE! Master Edition", "SOME TITLE", "SOME TITLE!"),
        ("Some Series to! v01 [Group]", "Some Series to", "Some Series to!"),
        ("Some Series!!", "Some Series", "Some Series!!"),
        ("Some Series", "Some Series", None),
        ("!Some Series", "Some Series", None),
        ("Wow! Some Series", "Wow! Some Series", None),
    ],
)
def test_normalize_trailing_exclamation_is_kept_as_a_second_form(name, primary, with_exclamation):
    n = normalize(name)

    assert n.primary == primary
    assert n.primary_with_exclamation == with_exclamation


@pytest.mark.parametrize("name", ["Edition Wars", "The Editions of Master"])
def test_normalize_edition_alone_is_kept(name):
    assert normalize(name).primary == name


def test_normalize_underscores_and_dots_become_spaces_when_no_spaces():
    assert normalize("Some_Long_Series_v01.cbz").primary == "Some Long Series"
    assert normalize("Some.Long.Series.cbr").primary == "Some Long Series"
    # With spaces present, dots are kept (a title like "Dr. Stone" survives).
    assert normalize("Dr. Stone v01").primary == "Dr. Stone"


def test_normalize_full_width_is_folded_by_nfkc():
    assert normalize("ＡＢＣ １２").primary == "ABC 12"


@pytest.mark.parametrize("input_", [None, "", "   ", "[Only Tags] (2020)"])
def test_normalize_empty_or_all_tags_yields_empty_primary(input_):
    n = normalize(input_)

    assert n.primary == ""
    assert len(n.variants) == 0


def test_scoring_form_strips_macrons_and_long_vowels():
    assert scoring_form("Shingeki no Kyojin") == scoring_form("Shingeki no Kyōjin")
    assert scoring_form("Kyoukai") == scoring_form("Kyōkai")
    assert scoring_form("Yuusha") == scoring_form("Yūsha")


def test_scoring_form_unifies_times_sign_ampersand_and_punctuation():
    assert scoring_form("A × B") == "a x b"
    assert scoring_form("Cats & Dogs!") == "cats and dogs"
    assert scoring_form("Re:Zero") == "re zero"


# --- Stage 2 (auto-match) additions -----------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Ren'ai Flops", "Renai Flops"),
        ("Hell’s Paradise", "Hells Paradise"),
        ("Kaguya-sama", "Kaguya sama"),
    ],
)
def test_scoring_form_removes_apostrophes_instead_of_splitting(a, b):
    assert scoring_form(a) == scoring_form(b)
    assert scoring_form("Ren'ai") == "renai"


@pytest.mark.parametrize(
    ("input_", "expected"),
    [
        ("Some Title (unclosed", "Some Title unclosed"),
        ("Some Title [Tag", "Some Title Tag"),
        ("Some Title) v01", "Some Title"),
        ("Some Title (Digital) [Group", "Some Title Group"),
    ],
)
def test_normalize_unbalanced_bracket_does_not_survive_into_primary(input_, expected):
    primary = normalize(input_).primary

    assert primary == expected
    assert not any(c in "()[]{}" for c in primary)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Some Series", []),
        ("Some Series 2", ["2"]),
        ("Some Series 2 - The Return", ["2"]),
        ("Some Series 2: The Return", ["2"]),
        ("Some Series Part 3 - Subtitle", ["3"]),
        ("Some Series Part III: Subtitle", ["3"]),
        ("Some Series Season 2", ["2"]),
        ("Some Series II", ["2"]),
        ("20th Century Boys", []),
        ("7 Seeds", []),
        ("Ranma 1/2", []),
        ("Mob Psycho 100", ["100"]),
        ("Some Series 2019", []),
    ],
)
def test_number_tokens_finds_sequel_and_part_numbers(title, expected):
    assert list(number_tokens(title)) == expected


def test_derived_variants_split_subtitle_and_sequel_number():
    derived = derived_variants("Some Long Series 2 - The Return")

    assert DerivedTitle("Some Long Series 2", DerivedTitleKind.SUBTITLE_SPLIT) in derived
    assert DerivedTitle("Some Long Series", DerivedTitleKind.SEQUEL_NUMBER_SPLIT) in derived


@pytest.mark.parametrize(
    "title",
    [
        "Series - Subtitle",  # one word before the separator: no split
        "Some Series",
        "",
    ],
)
def test_derived_variants_nothing_to_derive_is_empty(title):
    assert not any(d.kind == DerivedTitleKind.SUBTITLE_SPLIT for d in derived_variants(title))


def test_normalize_flags_derived_variants_and_keeps_primary_stable():
    n = normalize("Some Long Series Part 3 - Subtitle [English Series Name]")

    assert n.primary == "Some Long Series Part 3 - Subtitle"
    assert list(n.variants) == ["Some Long Series Part 3 - Subtitle", "English Series Name"]
    assert any(d.text == "Some Long Series Part 3" and d.kind == DerivedTitleKind.SUBTITLE_SPLIT for d in n.derived)
    assert any(d.text == "Some Long Series" and d.kind == DerivedTitleKind.SEQUEL_NUMBER_SPLIT for d in n.derived)
    assert not any(d.text in n.variants for d in n.derived)


@pytest.mark.parametrize(
    ("archive", "expected"),
    [
        ("Some Series v01 (2019) (Digital) (Group).cbz", "Some Series"),
        ("Some Series - Chapter 012.cbz", "Some Series"),
        ("Some Series 03.cbz", "Some Series"),
        ("Some Series 01-03.cbz", "Some Series"),
        ("[Group] Some Series v02 - A Subtitle.cbz", "Some Series"),
        ("(C99) [Circle (Artist)] Some Story (Some Parody) [English].cbz", "Some Story"),
        ("001 [A Chapter Title].cbz", ""),
        ("Vol 01.cbz", ""),
        ("012 - A Chapter Title.cbz", ""),
        ("7 Seeds v01.cbz", "7 Seeds"),
    ],
)
def test_archive_base_title_strips_units_and_trailing_numbers(archive, expected):
    assert archive_base_title(archive) == expected


def test_archive_title_returns_the_base_most_archives_share():
    assert archive_title(
        ["English Name v01.cbz", "English Name v02.cbz", "English Name v03.cbz", "Other Thing.cbz"]
    ) == "English Name"
    assert archive_title(["Two Halves 1.cbz", "Two Halves 2.cbz", "Else.cbz", "More.cbz"]) == "Two Halves"
    assert archive_title(["Alpha.cbz", "Beta.cbz", "Gamma.cbz"]) is None
    assert archive_title(["001.cbz", "002.cbz"]) is None
    assert archive_title([]) is None
