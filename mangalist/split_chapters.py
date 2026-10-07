"""Split chapters: files numbered as parts of chapter N (``N.1``, ``N.2`` ...) are chapter N.

A port of MangaPixer's ``MissingUnits.SplitsOf`` (MangaPixer 1.29.1, refined in 1.30.0; the rule MangaPixer's Volumes
view, Missing report and - since 1.35.0 - volume covers use), so MangaList and MangaPixer agree on what a folder holds:

- A part is a one-decimal number (``12.1`` ... ``12.9``); ``12.25`` / ``12.75`` never are.
- ``N.5`` is the usual number of an extra (``10.5``), so it is a part only after ``N.4``.
- The parts must start at the beginning: at ``N.1``, or at ``N.2`` when a file ``N`` is here (that file is the first part;
  a file ``N.1`` next to it stays an extra). A run that starts later (``N.6`` next to ``N`` and ``N.5``) is extras.
- A part missing below the highest part here is reported (``4.1`` and ``4.3`` here: ``4.2`` is missing).

Numbers are exact ``Decimal`` values, as everywhere in MangaList. No Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import FrozenSet, Iterable, Optional, Set, Tuple

#: Numbers above this are not expanded (MangaPixer's ``MissingUnits.MaxNumber``).
MAX_NUMBER = 3000


@dataclass(frozen=True)
class SplitChapters:
    parts: FrozenSet[Decimal]           # the fractional numbers that are parts of a split chapter
    chapters: FrozenSet[int]            # the chapters held through their parts
    missing_parts: Tuple[Decimal, ...]  # parts missing below the highest part held


def _whole_chapters(singles: Iterable[Decimal], ranges: Iterable[Tuple[Decimal, Decimal]]) -> Set[int]:
    """Whole chapter numbers held as files: a whole single, or the whole numbers inside a range file."""
    out: Set[int] = set()
    for c in singles:
        if c == c.to_integral_value() and 0 <= c <= MAX_NUMBER:
            out.add(int(c))
    for lo, hi in ranges:
        low = int(lo) if lo == lo.to_integral_value() else int(lo) + 1
        high = int(hi) if hi - lo <= MAX_NUMBER else low
        out.update(range(max(0, low), min(high, MAX_NUMBER) + 1))
    return out


def splits_of(singles: Iterable[Optional[Decimal]],
              ranges: Iterable[Tuple[Decimal, Decimal]] = ()) -> SplitChapters:
    """The split chapters among chapter files: *singles* are the single chapter numbers held, *ranges* the
    ``(first, last)`` of range files (``Ch. 1-5``)."""
    singles = [c for c in singles if c is not None]
    ranges = list(ranges)
    wholes = _whole_chapters(singles, ranges)
    tenths_by_chapter: dict = {}
    for c in singles:
        if c <= 0 or c > MAX_NUMBER:
            continue
        tenths = (c - Decimal(int(c))) * 10
        if tenths == 0 or tenths != tenths.to_integral_value():
            continue                    # a whole chapter, or 12.25 / 12.75: never a part
        tenths_by_chapter.setdefault(int(c), set()).add(int(tenths))

    parts: Set[Decimal] = set()
    chapters: Set[int] = set()
    missing = []
    for n in sorted(tenths_by_chapter):
        tenths = tenths_by_chapter[n]
        has_file = n in wholes
        own = sorted(t for t in tenths if (t != 5 or 4 in tenths) and (not has_file or t >= 2))
        if not own or own[0] != (2 if has_file else 1):
            continue
        chapters.add(n)
        present = set(own) | ({1} if has_file else set())
        parts.update(Decimal(n) + Decimal(t) / 10 for t in own)
        missing.extend(Decimal(n) + Decimal(t) / 10 for t in range(1, own[-1]) if t not in present)
    return SplitChapters(frozenset(parts), frozenset(chapters), tuple(missing))
