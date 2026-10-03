"""Port of MangaPixer.Core.Tests/Metadata/TitleSimilarityTests.cs."""

from __future__ import annotations

import pytest

from mangalist.matcher.similarity import (
    POSSIBLE_THRESHOLD,
    STRONG_THRESHOLD,
    MatchStrength,
    best,
    label,
    levenshtein,
    score,
)


def test_identical_after_normalization_is_one():
    assert score("Berserk", "BERSERK!") == 1.0
    assert score("Shingeki no Kyojin", "Shingeki no Kyōjin") == 1.0


def test_subset_title_is_penalized_not_a_perfect_match():
    # A plain token-set ratio would call these identical.
    subset = score("Berserk", "Berserk of Gluttony")

    assert subset < POSSIBLE_THRESHOLD, f"subset scored {subset}"


def test_word_order_does_not_matter_much():
    assert score("Dungeon Delicious", "Delicious Dungeon") >= STRONG_THRESHOLD


def test_small_typo_stays_strong_or_possible():
    s = score("Vinland Saga", "Vinland Sgaa")

    assert s >= POSSIBLE_THRESHOLD, f"typo scored {s}"


def test_unrelated_is_weak():
    assert score("Berserk", "Cooking Papa") < POSSIBLE_THRESHOLD


def test_side_story_ranks_below_the_exact_title():
    exact = score("Berserk", "Berserk")
    gaiden = score("Berserk", "Berserk Gaiden")

    assert exact > gaiden


def test_empty_is_zero():
    assert score("", "x") == 0
    assert score(None, None) == 0


def test_best_takes_the_max_over_variants_and_candidates():
    result = best(["Dungeon Meshi", "Delicious in Dungeon"], ["Delicious in Dungeon", "Other"])

    assert result == 1.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.95, MatchStrength.STRONG),
        (0.85, MatchStrength.STRONG),
        (0.7, MatchStrength.POSSIBLE),
        (0.6, MatchStrength.POSSIBLE),
        (0.59, MatchStrength.WEAK),
    ],
)
def test_label_uses_the_thresholds(value, expected):
    assert label(value) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("", "", 0),
        ("kitten", "sitting", 3),
        ("abc", "abc", 0),
        ("abc", "", 3),
    ],
)
def test_levenshtein_classic_cases(a, b, expected):
    assert levenshtein(a, b) == expected


def test_very_long_inputs_are_bounded_and_still_score():
    a = "a" * 10_000
    b = "a" * 9_000 + "b" * 1_000

    s = score(a, b)

    assert 0 <= s <= 1
