"""Port of MangaPixer.Core.Tests/Metadata/TitleNormalizerTests.cs (synthetic names only)."""

from __future__ import annotations

import pytest

from mangalist.matcher.normalizer import (
    DerivedTitle,
    DerivedTitleKind,
    archive_base_title,
    archive_title,
    derived_variants,
    normalize,
    number_tokens,
    numbered_series_head,
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
        # 1.26.1: an unmatched bracket marks a tag (a YACReader jump-bar convention), so the text on
        # its outer side leaves the title; "Some Title) v01" keeps its title (no title text after).
        ("Some Title (unclosed", "Some Title"),
        ("Some Title [Tag", "Some Title"),
        ("Some Title) v01", "Some Title"),
        ("Some Title (Digital) [Group", "Some Title"),
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


# --- 1.26.1 ---------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("names", "head"),
    [
        ([f"Cloud Flower {i:03d} Title Word{i}.cbz" for i in range(1, 13)], "Cloud Flower"),
        (["Steel Rider 000 Oneshot.cbz", "Steel Rider 001 Rise.cbz", "Steel Rider 002 Iron Fire!.cbz",
          "Steel Rider 006 HQ Version.cbz"], "Steel Rider"),
        ([f"Level 1 Hero {i:03d} Part Name.cbz" for i in range(1, 7)], "Level 1 Hero"),
        (["Alpha Story.cbz", "Beta Tale.cbz", "Gamma Saga.cbz", "Delta Night.cbz"], None),
        (["Alpha Story 1.cbz", "Alpha Story 2.cbz", "Beta Tale.cbz", "Gamma Saga.cbz", "Delta Night.cbz"], None),
        (["Same Title 005 Only One.cbz"], None),
    ],
)
def test_numbered_series_head_finds_the_title_in_front_of_a_varying_chapter_number(names, head):
    assert numbered_series_head(names, 0.8) == head


@pytest.mark.parametrize(
    ("name", "primary"),
    [
        ("Family Given] Some Words", "Some Words"),
        ("Some Words [Family Given", "Some Words"),
        ("Some Words]", "Some Words"),
    ],
)
def test_normalize_unmatched_bracket_tag_is_not_part_of_the_title(name, primary):
    assert normalize(name).primary == primary


def test_normalize_english_title_survives_a_trailing_creator_group_but_not_a_release_tag():
    assert list(normalize("Some Words [Joined Hands] (Family Given)").variants) == ["Some Words", "Joined Hands"]
    assert list(normalize("Some Words [Joined Hands] (Digital)").variants) == ["Some Words"]


# --- 1.27.0 / 1.30.0 (ported from MangaPixer 1.31.1 TitleNormalizerTests) ----------------------

from mangalist.matcher.normalizer import contains_number, name_subtitle, subtitle_head, subtitle_tail  # noqa: E402


@pytest.mark.parametrize(("title", "expected"), [
    ("Alpha Beta Level 99 ~Long Subtitle Here~", ("99",)),
    ("Alpha Beta Level 99~Long Subtitle Here~", ("99",)),
    ("Alpha Beta 2 ~ Subtitle", ("2",)),
])
def test_number_tokens_a_tilde_break_with_or_without_a_space_ends_the_number(title, expected):
    assert number_tokens(title) == expected


def test_derived_variants_split_at_a_tilde_without_a_space_after_it():
    derived = derived_variants("Alpha Beta Level 99 ~Long Subtitle Here~")
    assert DerivedTitle("Alpha Beta Level 99", DerivedTitleKind.SUBTITLE_SPLIT) in derived


@pytest.mark.parametrize(("title", "expected"), [
    ("Some Title: Long Subtitle", "Some Title"),
    ("Some Title:re", "Some Title"),
    ("Some Title ~Long Subtitle~", "Some Title"),
    ("Some Title~Long Subtitle~", "Some Title"),
    ("Some Title - Long Subtitle", "Some Title"),
    ("Some-Title Here", None),  # a hyphen inside a word is no break
    ("Some Title", None),
    ("Some Title ~", None),  # nothing after the break
    ("99: Something", None),  # nothing with a letter before it
])
def test_subtitle_head_is_the_text_before_the_first_break(title, expected):
    assert subtitle_head(title) == expected


@pytest.mark.parametrize(("name", "expected"), [
    ("Series Name - Side Story", "Side Story"),
    ("Series Name: Side Story", "Side Story"),
    ("Series Name ~Side Story~", "Side Story"),
    ("Word - Side Story", None),  # one word before the dash: no subtitle split (as derived_variants)
    ("Series Name", None),
    ("Series-Name Words", None),  # an unspaced hyphen is part of the name
])
def test_name_subtitle_is_what_the_subtitle_split_cuts_off(name, expected):
    assert name_subtitle(name) == expected


@pytest.mark.parametrize(("title", "expected"), [
    ("Series Name: Side Story", "Side Story"),
    ("Series Name - Side Story", "Side Story"),
    ("Series Name ~Side Story~", "Side Story"),
    ("Word:Re", "Re"),
    ("Series Name", None),
])
def test_subtitle_tail_is_the_text_after_the_first_break(title, expected):
    assert subtitle_tail(title) == expected


@pytest.mark.parametrize(("text", "number", "expected"), [
    ("Alpha Level 99 ~Sub~", "99", True),
    ("Alpha Level 099", "99", True),
    ("Alpha 1999", "99", False),
    ("Alpha Level", "99", False),
])
def test_contains_number_matches_whole_numbers_only(text, number, expected):
    assert contains_number(text, number) == expected
