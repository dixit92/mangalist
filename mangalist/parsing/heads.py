"""The series head of one folder's archive names (MangaPixer 1.42.0 ``AutoMatchText.SeriesHeadOf``, Mihon's rule): which
number in a name is the unit and which one belongs to the title. A name alone cannot tell ``<Series> 100 Vol 11 <title>``
(chapter 100 of volume 11) from ``<Series Title 100> Vol 1`` (a title that ends in a number); the folder can: the unit is
the number that VARIES between the names, a number every name shares is part of the title. Pure; sees names only."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, Optional, Set, Tuple

from ._common import split_extension, without_watermark
from .model import to_decimal


@dataclass(frozen=True)
class SeriesHead:
    """The text a folder's names carry in front of a standalone number (:func:`name_key` form) and whether that number
    varies between them. ``varies``: the number right after the head is the unit (``<Series> 099 Vol 10``,
    ``<Series> 100 Vol 11``); otherwise it is part of the title (``<Title> 009 v01``, ``<Title> 009 v02``)."""

    key: str
    varies: bool = True


def name_key(text: Optional[str]) -> str:
    """A text in comparison form: NFKC, case-folded, bracket groups left out, words of letters and digits separated by
    one space (``"[Grp] Some_Series -"`` -> ``"some series"``)."""
    if not text:
        return ""
    return " ".join(_WORD.findall(_without_brackets(unicodedata.normalize("NFKC", text)).casefold()))


_WORD = re.compile(r"[^\W_]+")
_BRACKET_GROUP = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}")


def _without_brackets(text: str) -> str:
    for _ in range(3):
        cut = _BRACKET_GROUP.sub(" ", text)
        if cut == text:
            break
        text = cut
    return text


# A standalone number outside brackets: the candidate unit after a series head, with an unspaced range ("001-003").
_HEAD_NUMBER = re.compile(r"(?<![^\W_])(?<!\.)(?P<n>\d{1,4}(?:\.\d{1,3})?)(?:-\d{1,4}(?:\.\d{1,3})?)?(?![^\W_])")
# A volume token right after the number ("<head> 100 Vol 11", "<head> 012.5 v02"), with the volume's number.
_VOLUME_AFTER = re.compile(r"[\s_\-\u2013\u2014]*(?:volumes|volume|vols|vol|v|tome|tomo|band|deel|album|livre)\.?\s*"
                           r"(?P<v>\d+(?:\.\d+)?)", re.IGNORECASE)
# A head that ends in a unit word owns the number after it ("Vol 01", "Season 2", "No 8"): never a series head.
_ENDS_IN_UNIT_WORD = re.compile(
    r"(?:^|\s)(?:v|vol|vols|volume|volumes|ch|chap|chapter|chapters|c|ep|episode|episodes|issue|no|tome|tomo|band|deel"
    r"|album|livre|part|season|book|arc)$")


def head_text(name: str) -> str:
    """A name for the series-head rules: NFKC, no extension or site watermark, ``_`` as spaces in a name without any,
    no bracket groups."""
    stem, _ = split_extension(without_watermark(unicodedata.normalize("NFKC", name).strip()))
    if " " not in stem:
        stem = stem.replace("_", " ")
    return " ".join(_without_brackets(stem).split()).strip(" -_.")


def series_head_of(names: Iterable[Optional[str]]) -> Optional[SeriesHead]:
    """The series head of one folder's archive names, or None (fewer than two names, or no head qualifies).

    MangaPixer's rule (1.42.0): the text that at least half of the names carry in front of a standalone number that
    VARIES - distinct in at least three of every four names carrying the head - is the head, and the number right after
    it the unit. MangaList adds the other side of Mihon's rule: when no head varies, a text at least half of the names
    carry in front of a number that repeats while the VOLUME after it changes (``<Title> 009 v01``, ``<Title> 009 v02``,
    sequels ``<Title> 02 v01`` / ``<Title> 02 v02`` / ``<Title> 03 v01``) is a head whose number is part of the title
    (``varies`` False). Two copies of one file (``<Series> 100 Vol 11``, twice) are neither."""
    total = 0
    heads: Dict[str, Tuple[int, Set[Decimal], Set[Decimal]]] = {}
    for name in names:
        total += 1
        if not name or not name.strip():
            continue
        s = head_text(name)
        seen: Set[str] = set()
        for m in _HEAD_NUMBER.finditer(s):
            key = name_key(s[:m.start()])
            if not key or not any(c.isalpha() for c in key) or _ENDS_IN_UNIT_WORD.search(key) or key in seen:
                continue
            seen.add(key)
            count, numbers, volumes = heads.get(key, (0, set(), set()))
            numbers.add(unit_number(m.group("n")))
            after = _VOLUME_AFTER.match(s, m.end())
            if after is not None:
                volumes.add(unit_number(after.group("v")))
            heads[key] = (count + 1, numbers, volumes)

    def best(qualifies) -> Optional[str]:
        found = [(key, h) for key, h in heads.items() if h[0] >= 2 and h[0] * 2 >= total and qualifies(h)]
        return min(found, key=lambda kv: (-kv[1][0], -len(kv[0]), kv[0]))[0] if found else None

    varying = best(lambda h: len(h[1]) >= 2 and len(h[1]) * 4 >= h[0] * 3)
    if varying is not None:
        return SeriesHead(varying, True)
    title = best(lambda h: len(h[1]) * 4 < h[0] * 3 and len(h[2]) >= 2)
    return SeriesHead(title, False) if title is not None else None


def unit_number(text: str) -> Decimal:
    """A unit number as written, a three-digit fraction with a leading zero being a padded sub-number (``014.005`` ->
    14.5, ``021.010`` -> 21.1; ``014.000`` stays 14, ``014.125`` is 14.125)."""
    return padded_sub_number(to_decimal(text))


def padded_sub_number(d: Decimal) -> Decimal:
    """``d`` with a sub-number padded to three digits read as that sub-number (owner, 2026-10-11, "Ch.014.005": "It is
    .5"): 14.005 -> 14.5, 21.010 -> 21.1; 14.000, 14.125 and every other number stay as written."""
    sign, digits, exp = d.as_tuple()
    if exp != -3 or len(digits) < 3 or digits[-3] != 0 or not any(digits[-2:]):
        return d
    whole = d.to_integral_value(rounding="ROUND_DOWN")
    return Decimal(f"{whole}.{int(''.join(map(str, digits[-3:])))}").normalize()
