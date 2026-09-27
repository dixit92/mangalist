"""Title similarity (port of MangaPixer 1.26.1 ``TitleSimilarity.cs``).

``score = 0.45 * tokenSortRatio + 0.35 * tokenSetRatio' + 0.20 * trigramDice``, where
``tokenSetRatio'`` is the classic token-set ratio multiplied by ``(1 - 0.5 * unmatchedTokenMass)``.
The penalty is what keeps "Berserk" from scoring as a perfect match for "Berserk of Gluttony" (a
pure token-set ratio - and the old Manga-List subset rule - treats a subset as identical). Inputs go
through :func:`scoring_form` first. No package: Levenshtein, token ratios and trigram Dice are small
enough to own, and owning them keeps the arithmetic identical to the reference.
"""

from __future__ import annotations

from enum import IntEnum
from functools import lru_cache
from typing import Iterable, List, Sequence, Set

from .normalizer import scoring_form

STRONG_THRESHOLD = 0.85
POSSIBLE_THRESHOLD = 0.60
MAX_COMPARE_LENGTH = 256


class MatchStrength(IntEnum):
    WEAK = 0
    POSSIBLE = 1
    STRONG = 2


def score(a: str | None, b: str | None) -> float:
    """Similarity in [0, 1] between two titles (1 = identical after scoring normalization)."""
    x = _truncate(scoring_form(a))
    y = _truncate(scoring_form(b))
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    return _score_forms(x, y)


@lru_cache(maxsize=65536)
def _score_forms(x: str, y: str) -> float:
    tokens_x = _tokens(x)
    tokens_y = _tokens(y)
    s = (0.45 * token_sort_ratio(tokens_x, tokens_y)
         + 0.35 * penalized_token_set_ratio(tokens_x, tokens_y)
         + 0.20 * trigram_dice(x, y))
    return min(max(s, 0.0), 1.0)


def best(queries: Iterable[str], candidates: Iterable[str]) -> float:
    """The best score over every (query, candidate) pair; 0 when either side is empty."""
    candidate_list = [c for c in candidates if c is not None and c.strip()]
    result = 0.0
    for q in queries:
        if q is None or not q.strip():
            continue
        for c in candidate_list:
            result = max(result, score(q, c))
    return result


def label(value: float) -> MatchStrength:
    if value >= STRONG_THRESHOLD:
        return MatchStrength.STRONG
    if value >= POSSIBLE_THRESHOLD:
        return MatchStrength.POSSIBLE
    return MatchStrength.WEAK


def levenshtein(a: str, b: str) -> int:
    """Levenshtein distance (two-row DP)."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    curr = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        curr[0] = i
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            cost = 0 if ai == b[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[len(b)]


def ratio(a: str, b: str) -> float:
    """1 - distance / max length, in [0, 1]."""
    mx = max(len(a), len(b))
    if mx == 0:
        return 1.0
    return 1.0 - levenshtein(a, b) / mx


def token_sort_ratio(a: Sequence[str], b: Sequence[str]) -> float:
    """Ratio of the two token lists after sorting them (word order ignored)."""
    return ratio(" ".join(sorted(a)), " ".join(sorted(b)))


def penalized_token_set_ratio(a: Sequence[str], b: Sequence[str]) -> float:
    """Token-set ratio multiplied by ``1 - 0.5 * unmatchedTokenMass`` (the share of characters in
    tokens found on only one side)."""
    set_a = set(a)
    set_b = set(b)
    common = sorted(set_a & set_b)
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)

    t0 = " ".join(common)
    t1 = " ".join(common + only_a)
    t2 = " ".join(common + only_b)
    raw = max(ratio(t0, t1), max(ratio(t0, t2), ratio(t1, t2)))
    if not common:
        raw = ratio(t1, t2)

    total = sum(len(t) for t in set_a) + sum(len(t) for t in set_b)
    unmatched = sum(len(t) for t in only_a) + sum(len(t) for t in only_b)
    mass = 0 if total == 0 else unmatched / total
    return raw * (1 - 0.5 * mass)


def trigram_dice(a: str, b: str) -> float:
    """Dice coefficient over the character trigram sets of the padded strings."""
    ta = _trigrams(a)
    tb = _trigrams(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    common = sum(1 for t in ta if t in tb)
    return 2.0 * common / (len(ta) + len(tb))


def _trigrams(s: str) -> Set[str]:
    padded = "  " + s + " "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def _tokens(s: str) -> List[str]:
    return [t for t in s.split(" ") if t]


def _truncate(s: str) -> str:
    return s[:MAX_COMPARE_LENGTH] if len(s) > MAX_COMPARE_LENGTH else s
