"""The retrieval loop around the scorer (MangaPixer 1.26.0's automatic search, as modelled by its
golden-set harness): search the variants in order - at most :data:`MAX_SEARCHES`, the next one only
while the best title score is below :data:`NEXT_VARIANT_BELOW` - then GET the top candidate (and
the second when it is within :data:`SECOND_GET_WITHIN`), only at or above the review floor, and
score with the given thresholds.

Pure: ``search`` and ``get`` are injected, so the worker runs it against the live API and the
golden tests against recorded responses - the same code in both.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Sequence

from . import scorer
from ._text import distinct_ignore_case
from .contracts import DEFAULT_THRESHOLDS, MatchCandidate, MatchOutcome, MatchQuery, MatchThresholds

MAX_SEARCHES = 4
NEXT_VARIANT_BELOW = 0.85
SECOND_GET_WITHIN = 0.10

SearchFn = Callable[[str], Sequence[MatchCandidate]]
GetFn = Callable[[str], Optional[MatchCandidate]]


@dataclass(frozen=True)
class RetrievalResult:
    outcome: MatchOutcome
    candidates: tuple
    searches: int
    gets: int


def retrieve_and_score(query: MatchQuery, search: SearchFn, get: GetFn,
                       thresholds: MatchThresholds = DEFAULT_THRESHOLDS) -> RetrievalResult:
    """Search, fetch the leading full records, and score. ``get`` returns None when a record is
    unavailable (the hit is then scored on its search data alone)."""
    candidates: List[MatchCandidate] = []
    searches = 0
    for variant in query.variants:
        if searches == MAX_SEARCHES:
            break
        searches += 1
        for hit in search(variant.text):
            if not any(x.external_id == hit.external_id for x in candidates):
                candidates.append(hit)
        probe = scorer.score(query, candidates, DEFAULT_THRESHOLDS)
        if probe.ranked and probe.ranked[0].title_score >= NEXT_VARIANT_BELOW:
            break

    gets = 0
    floor = DEFAULT_THRESHOLDS.review_floor
    ranked = scorer.score(query, candidates, DEFAULT_THRESHOLDS).ranked
    to_get = list(ranked[:1])
    if (len(ranked) > 1 and ranked[0].title_score >= floor
            and ranked[0].title_score - ranked[1].title_score <= SECOND_GET_WITHIN):
        to_get.append(ranked[1])
    for s in to_get:
        if s.title_score < floor:
            continue
        gets += 1
        full = get(s.candidate.external_id)
        if full is None:
            continue
        # The full record replaces the hit; the hit title stays an alt title.
        alt = distinct_ignore_case([*full.alt_titles, *s.candidate.alt_titles])
        index = next(i for i, x in enumerate(candidates) if x.external_id == s.candidate.external_id)
        candidates[index] = replace(full, alt_titles=tuple(alt))

    # Retrieval always runs at the defaults; only the final bands use the given thresholds.
    outcome = scorer.score(query, candidates, thresholds)
    return RetrievalResult(outcome, tuple(candidates), searches, gets)
