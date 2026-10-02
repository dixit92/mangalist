"""Series families among the candidates of one work (port of MangaPixer 1.31.1 ``SeriesFamilies.cs``, 1.30.0).
Pure and deterministic.

- Two records of one provider are one family when either lists the other in its related series with a
  family relation (main story, spin-off, side story, prequel, sequel, alternate version / story, adapted
  from, full anthology).
- Fallback when neither lists the other: they share a title head (``Title`` and ``Title: Subtitle``, or two
  subtitles of one ``Title``) AND both have known authors that overlap. A search hit has no relations or
  authors of its own.

The scorer uses :func:`are_family` for the ``SubtitleFamily`` veto and the ``SeriesFamily`` chip. MangaPixer's
role / hub assignment (``SeriesFamilies.Of``) feeds its review page's grouping and is not ported.
"""

from __future__ import annotations

from typing import Iterable, Optional, Set, Tuple

from . import auto_match_text as amt
from ._text import is_null_or_whitespace
from .contracts import MatchCandidate
from .normalizer import scoring_form, subtitle_head

# The provider relation types that tie two records into one series family.
_FAMILY_RELATIONS = frozenset({
    "main story", "spin-off", "side story", "prequel", "sequel", "alternate version", "alternate story",
    "adapted from", "full anthology",
})


def is_family_relation(relation: Optional[str]) -> bool:
    """True when ``relation`` (a provider relation type) ties two records into one series family."""
    return relation is not None and relation.strip().lower() in _FAMILY_RELATIONS


def are_family(a: MatchCandidate, b: MatchCandidate) -> bool:
    """True when the two records are one series family (a family relation either way, else the head +
    author fallback)."""
    if a.provider != b.provider or a.external_id == b.external_id:
        return False
    ab = _relation_of(a, b)
    ba = _relation_of(b, a)
    if ab is not None or ba is not None:
        return is_family_relation(ab) or is_family_relation(ba)
    return _shares_head(a, b) and _authors_overlap(a, b)


def _relation_of(a: MatchCandidate, b: MatchCandidate) -> Optional[str]:
    """The relation type ``a`` lists for ``b`` ("b is a's R"), or None."""
    return next((r.relation for r in (a.relations or ()) if r.external_id == b.external_id), None)


def _titles_of(c: MatchCandidate) -> Iterable[str]:
    for t in (c.title, *(c.alt_titles or ())):
        if not is_null_or_whitespace(t):
            stripped = amt.without_disambiguator(t)
            yield stripped if stripped is not None else t


def _heads(c: MatchCandidate) -> Set[Tuple[str, bool]]:
    result = set()
    for t in _titles_of(c):
        h = subtitle_head(t)
        item = (scoring_form(h), True) if h is not None else (scoring_form(t), False)
        if item[0]:
            result.add(item)
    return result


def _shares_head(a: MatchCandidate, b: MatchCandidate) -> bool:
    """A title of one, up to its subtitle break, equals a title of the other up to its break (at least one
    has a break)."""
    ha = _heads(a)
    return any(x[0] == y[0] and (x[1] or y[1]) for x in _heads(b) for y in ha)


def _authors_overlap(a: MatchCandidate, b: MatchCandidate) -> bool:
    return any(not is_null_or_whitespace(x) and any(amt.names_equal(x, y) for y in (b.authors or ()))
               for x in (a.authors or ()))
