"""Port of the family-membership part of MangaPixer 1.31.1 ``SeriesFamiliesTests.cs`` (1.30.0): which candidates
of one work are the same series family. MangaPixer's roles and groups (``SeriesFamilies.Of``) feed its review
page only and are not ported, so each case is asserted pairwise with :func:`are_family`. Synthetic records."""

from __future__ import annotations

from mangalist.matcher.contracts import CandidateRelation, MatchCandidate, MetadataFormat
from mangalist.matcher.series_families import are_family, is_family_relation


def rec(id, title, alt=(), year=None, authors=(), related=(), provider="mangaupdates") -> MatchCandidate:
    return MatchCandidate(provider, id, title, tuple(alt), MetadataFormat.COMIC, "Manga", year, None, None,
                          tuple(authors), tuple(CandidateRelation(i, r) for i, r in related))


def test_main_story_and_spin_off_are_one_family():
    spin = rec("2", "Alpha Garden - Before the Frost", related=[("1", "main story")])
    main = rec("1", "Alpha Garden", related=[("2", "spin-off")])
    other = rec("9", "Something Else")

    assert are_family(spin, main) and are_family(main, spin)
    assert not are_family(main, other) and not are_family(spin, other)


def test_a_prequel_pair_is_one_family():
    assert are_family(rec("2", "Alpha Garden - Before the Frost", related=[("1", "sequel")]),
                      rec("1", "Alpha Garden", related=[("2", "prequel")]))


def test_a_relation_listed_on_one_side_only_is_enough():
    # A search hit carries no relations; the fetched record's list names it.
    assert are_family(rec("1", "Beta Tales"), rec("2", "Gamma Saga", related=[("1", "side story")]))


def test_no_relation_shared_head_and_same_author_is_the_fallback():
    assert are_family(rec("1", "Alpha Garden", authors=["SMITH Anna"]),
                      rec("2", "Alpha Garden: Before the Frost", authors=["Smith Anna", "JONES Bert"]))
    assert not are_family(rec("1", "Alpha Garden", authors=["SMITH Anna"]),
                          rec("2", "Alpha Garden: Before the Frost", authors=["JONES Bert"]))
    assert not are_family(rec("1", "Alpha Garden", authors=["SMITH Anna"]), rec("2", "Alpha Garden: Before the Frost"))


def test_same_title_same_author_no_relation_is_not_a_family():
    # Two records with the very same title and no subtitle are homonyms more often than editions.
    assert not are_family(rec("1", "Alpha Garden", authors=["SMITH Anna"]), rec("2", "Alpha Garden", authors=["SMITH Anna"]))


def test_a_non_family_relation_wins_over_the_fallback():
    assert not are_family(rec("1", "Alpha Garden", authors=["SMITH Anna"], related=[("2", "doujinshi")]),
                          rec("2", "Alpha Garden: Before the Frost", authors=["SMITH Anna"]))
    assert not is_family_relation("doujinshi")
    assert is_family_relation("Spin-Off")


def test_alternate_story_and_alternate_version_are_family_relations():
    assert are_family(rec("2", "Alpha Garden - Before the Frost", related=[("1", "alternate story")]), rec("1", "Alpha Garden"))
    assert are_family(rec("2", "Alpha Garden - Full Colour", related=[("1", "alternate version")]), rec("1", "Alpha Garden"))


def test_other_providers_are_never_one_family():
    assert not are_family(rec("1", "Alpha Garden", related=[("2", "spin-off")]),
                          rec("2", "Alpha Garden: Frost", provider="other"))
