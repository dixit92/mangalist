"""The retrieval loop (port of MangaPixer 1.31.1 ``AutoMatchLookup``): page 2, the author-tag fetch, dropped
records. The golden set replays the whole loop on recordings; these pin its rules on synthetic pages."""

from __future__ import annotations

from typing import Dict, List

from mangalist.matcher import retrieval
from mangalist.matcher.contracts import (
    DEFAULT_THRESHOLDS,
    MatchCandidate,
    MatchContext,
    MatchQuery,
    QueryVariant,
    QueryVariantKind,
    WorkClass,
)
from mangalist.matcher.mangaupdates import SearchPage


def rec(id, title, alt=(), authors=()) -> MatchCandidate:
    return MatchCandidate("mangaupdates", id, title, tuple(alt), None, "Manga", None, None, None, tuple(authors))


def query(*texts: str) -> MatchQuery:
    return MatchQuery(tuple(QueryVariant(t, QueryVariantKind.PRIMARY) for t in texts),
                      MatchContext(WorkClass.SERIES, 5, 5, 0, None, None, False, ()))


class Provider:
    def __init__(self, pages: Dict[tuple, SearchPage], records: Dict[str, MatchCandidate]) -> None:
        self.pages, self.records = pages, records
        self.searches: List[tuple] = []
        self.gets: List[str] = []

    def search(self, text, page):
        self.searches.append((text, page))
        return self.pages.get((text, page), SearchPage((), 0))

    def get(self, external_id):
        self.gets.append(external_id)
        return self.records.get(external_id)


def full_page(*hits: MatchCandidate, total: int) -> SearchPage:
    filler = tuple(rec(f"f{i}", f"Unrelated Filler Words {i}") for i in range(10 - len(hits)))
    return SearchPage(hits + filler, total)


def test_page_two_is_read_on_a_tie_and_the_right_record_there_wins_review():
    p = Provider({
        ("Sprout", 1): full_page(rec("1", "Sprout (OTHER Person)"), rec("2", "Sprout (THIRD Person)"), total=25),
        ("Sprout", 2): SearchPage((rec("3", "Sprout (FAMILY Given)"),), 25),
    }, {"1": rec("1", "Sprout (OTHER Person)"), "2": rec("2", "Sprout (THIRD Person)"), "3": rec("3", "Sprout (FAMILY Given)")})

    r = retrieval.retrieve_and_score(query("Sprout"), p.search, p.get)

    assert p.searches == [("Sprout", 1), ("Sprout", 2)]
    assert r.searches == 2
    assert {c.external_id for c in r.candidates} >= {"1", "2", "3"}


def test_no_page_two_when_page_one_is_not_full_or_the_top_leads():
    lead = Provider({("Alpha Garden", 1): full_page(rec("1", "Alpha Garden"), total=40)}, {"1": rec("1", "Alpha Garden")})
    retrieval.retrieve_and_score(query("Alpha Garden"), lead.search, lead.get)
    assert lead.searches == [("Alpha Garden", 1)]

    short = Provider({("Sprout", 1): SearchPage((rec("1", "Sprout (A Person)"), rec("2", "Sprout (B Person)")), 2)}, {})
    retrieval.retrieve_and_score(query("Sprout"), short.search, short.get)
    assert short.searches == [("Sprout", 1)]


def test_only_hits_at_the_review_floor_are_fetched():
    p = Provider({("Alpha Garden", 1): SearchPage((rec("1", "Completely Different Words"),), 1)}, {})
    r = retrieval.retrieve_and_score(query("Alpha Garden"), p.search, p.get)
    assert p.gets == [] and r.gets == 0


def test_an_author_tagged_alias_hit_gets_one_extra_get():
    hit = rec("7", "Tsuki no Tegami", alt=["Moon Letter (SATO Hana)"])
    full = rec("7", "Tsuki no Tegami", alt=["Moon Letter (SATO Hana)"], authors=["SATO Hana"])
    other = rec("1", "Moon Letter")
    p = Provider({("Moon Letter", 1): SearchPage((other, hit), 2)}, {"1": other, "7": full})

    r = retrieval.retrieve_and_score(query("Moon Letter"), p.search, p.get)

    assert "7" in p.gets  # the tag is verified against the fetched record's authors
    tagged = next(s for s in r.outcome.ranked if s.candidate.external_id == "7")
    assert tagged.title_score > 0.99


def test_a_record_the_provider_no_longer_has_is_dropped():
    p = Provider({("Alpha Garden", 1): SearchPage((rec("1", "Alpha Garden"),), 1)}, {})
    r = retrieval.retrieve_and_score(query("Alpha Garden"), p.search, p.get)
    assert p.gets == ["1"]
    assert r.outcome.ranked == ()


def test_search_texts_collapse_whitespace_deduplicate_and_cap():
    q = query("Alpha  Garden", "alpha garden", "Beta", "Gamma", "Delta", "Epsilon")
    assert retrieval.search_texts(q) == ["Alpha Garden", "Beta", "Gamma", "Delta"]


def test_needs_page_two():
    assert retrieval.needs_page_two(retrieval.scorer.score(query("Alpha"), []), DEFAULT_THRESHOLDS)
