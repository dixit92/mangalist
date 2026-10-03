"""The retrieval loop around the scorer (port of MangaPixer 1.31.1's production ``AutoMatchLookup``, which its
golden set replays since 1.27.0):

1. Page 1 of each search text in order - the query variants, whitespace-collapsed, de-duplicated, at most
   :data:`MAX_SEARCHES` - stopping at the first text whose best title score reaches
   :data:`CONFIDENT_TITLE`. After each page the hits are ranked BY THE SCORER and the best one is fetched
   (GET), plus the second when its title score is within :data:`SECOND_GET_WITHIN` - only hits at or above
   the review floor, so a search that found nothing usable costs no GET. Once per work, the best unfetched
   hit whose score comes from an author-tagged alias (``Fly Me to the Moon (HATA Kenjiro)``) is fetched
   too, so the tag can be checked against the record's authors (1.30.0).
2. Page 2 of the same texts (1.27.0), while the top two are tied or nothing reached the review floor and
   the provider reports more hits, inside the same search bound.
3. An automatic link needs the full record of the chosen candidate: GET it when it is still a search hit.

A fetched record replaces its hit; a record the provider no longer has is dropped (never linked).

Pure: ``search`` and ``get`` are injected, so the worker runs it against the live API and the golden tests
against recorded responses - the same code in both. Retrieval always runs at the default thresholds; only
the final bands use the given ones (as MangaPixer's golden sweep re-scores).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set

from . import auto_match_text as amt
from . import scorer, similarity
from ._text import distinct_ignore_case
from .contracts import DEFAULT_THRESHOLDS, MatchBand, MatchCandidate, MatchOutcome, MatchQuery, MatchThresholds, ScoredCandidate
from .mangaupdates import SEARCH_PAGE_SIZE, SearchPage

MAX_SEARCHES = 4
CONFIDENT_TITLE = 0.85
SECOND_GET_WITHIN = 0.10
PAGE_TWO_TIE_WITHIN = 0.02
MAX_QUERY_LENGTH = 200

# Kept for callers of the 1.26.1 port.
NEXT_VARIANT_BELOW = CONFIDENT_TITLE

SearchFn = Callable[[str, int], SearchPage]
# Returns None when the provider no longer has the record; raises on any other failure.
GetFn = Callable[[str], Optional[MatchCandidate]]


@dataclass(frozen=True)
class RetrievalResult:
    outcome: MatchOutcome
    candidates: tuple
    searches: int
    gets: int


def search_texts(query: MatchQuery) -> List[str]:
    """The texts the loop may send, in order (at most :data:`MAX_SEARCHES`)."""
    texts = (" ".join(v.text.split()) for v in query.variants)
    return distinct_ignore_case(t for t in texts if 0 < len(t) <= MAX_QUERY_LENGTH)[:MAX_SEARCHES]


def retrieve_and_score(query: MatchQuery, search: SearchFn, get: GetFn,
                       thresholds: MatchThresholds = DEFAULT_THRESHOLDS) -> RetrievalResult:
    """Search, fetch the leading full records, and score."""
    run = _Retrieval(query, search, get, DEFAULT_THRESHOLDS)

    with_page_two: List[str] = []
    for text in search_texts(query):
        page = run.search(text, 1)
        if len(page.hits) >= SEARCH_PAGE_SIZE and page.total_hits > len(page.hits):
            with_page_two.append(text)
        interim = run.score()
        if interim.ranked and interim.ranked[0].title_score >= CONFIDENT_TITLE:
            break

    # Page 2 (1.27.0), while the top two are tied or nothing reached the review floor: MangaUpdates ranks short
    # look-alike titles first, so the right record of a one-word or partial name can sit on page 2.
    for text in with_page_two:
        if run.searches >= MAX_SEARCHES or not needs_page_two(run.score(), DEFAULT_THRESHOLDS):
            break
        run.search(text, 2)

    outcome = run.score()
    # An automatic link needs the full record of the chosen candidate.
    if outcome.band == MatchBand.AUTO and outcome.ranked and outcome.ranked[0].candidate.external_id not in run.fetched:
        run.fetch(outcome.ranked[0].candidate.external_id)

    final = scorer.score(query, list(run.candidates.values()), thresholds)
    return RetrievalResult(final, tuple(run.candidates.values()), run.searches, run.gets)


def needs_page_two(interim: MatchOutcome, thresholds: MatchThresholds) -> bool:
    """Page 1 left the top two tied, or nothing at the review floor (1.27.0)."""
    if not interim.ranked or interim.ranked[0].title_score < thresholds.review_floor:
        return True
    if len(interim.ranked) < 2:
        return False
    top, second = interim.ranked[0], interim.ranked[1]
    return (second.title_score >= top.title_score - PAGE_TWO_TIE_WITHIN
            and top.adjusted_score - second.adjusted_score < thresholds.margin)


def tagged_alias_hit(query: MatchQuery, ranked: Sequence[ScoredCandidate], fetched: Set[str],
                     thresholds: MatchThresholds) -> Optional[ScoredCandidate]:
    """The best-ranked unfetched hit at the review floor whose title score comes from an author-tagged alias
    (an alternative title whose trailing ``(disambiguator)`` names a person, scored at
    ``DISAMBIGUATED_ALIAS_FACTOR``) - the score that would rise if the tag named the record's author (1.30.0)."""
    texts = [v.text for v in query.variants if v.text and v.text.strip()]
    for r in ranked:
        if (r.title_score < thresholds.review_floor or r.title_score >= 1.0 - 1e-9
                or r.candidate.external_id in fetched):
            continue
        for t in r.candidate.alt_titles or ():
            alias = amt.without_disambiguator(t)
            if (amt.is_person_tag(amt.disambiguator_tag(t)) and alias is not None
                    and amt.DISAMBIGUATED_ALIAS_FACTOR * similarity.best(texts, [alias]) >= r.title_score - 0.02):
                return r
    return None


class _Retrieval:
    """What one lookup has collected: candidates by id (hits, replaced by full records once fetched)."""

    def __init__(self, query: MatchQuery, search: SearchFn, get: GetFn, thresholds: MatchThresholds) -> None:
        self._query = query
        self._search = search
        self._get = get
        self._thresholds = thresholds
        self.candidates: Dict[str, MatchCandidate] = {}
        self.fetched: Set[str] = set()
        self.searches = 0
        self.gets = 0
        self._tag_fetch_used = False

    def score(self) -> MatchOutcome:
        return scorer.score(self._query, list(self.candidates.values()), self._thresholds)

    def search(self, text: str, page_number: int) -> SearchPage:
        self.searches += 1
        page = self._search(text, page_number)
        for hit in page.hits:
            if hit.external_id not in self.fetched:
                self.candidates[hit.external_id] = hit

        # GET the best hit, and the runner-up when its title score is close (alt titles, authors, counts).
        # Hits are ranked by the scorer (1.27.0), and only hits that reach the review floor are fetched.
        page_ids = list(dict.fromkeys(h.external_id for h in page.hits))
        page_candidates = [self.candidates[i] for i in page_ids if i in self.candidates]
        ranked = scorer.score(self._query, page_candidates, self._thresholds).ranked
        for i in range(min(2, len(ranked))):
            if ranked[i].title_score < self._thresholds.review_floor:
                break
            if i == 1 and ranked[0].title_score - ranked[1].title_score > SECOND_GET_WITHIN:
                break
            self.fetch(ranked[i].candidate.external_id)

        # 1.30.0: a hit that matched through a title with a trailing author tag scores only the alias factor
        # until its authors are known; the best such hit gets ONE GET per work.
        if not self._tag_fetch_used:
            tagged = tagged_alias_hit(self._query, ranked, self.fetched, self._thresholds)
            if tagged is not None:
                self._tag_fetch_used = True
                self.fetch(tagged.candidate.external_id)
        return page

    def fetch(self, external_id: str) -> None:
        if external_id in self.fetched:
            return
        self.gets += 1
        record = self._get(external_id)
        if record is None:
            self.candidates.pop(external_id, None)  # Gone at the provider: never link to it.
            return
        self.fetched.add(external_id)
        self.candidates[external_id] = record
