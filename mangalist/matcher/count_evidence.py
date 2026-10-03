"""The count rule (port of MangaPixer 1.31.1 ``CountEvidence.cs``; extracted from the scorer in 1.29.0). Pure.

- Volumes are compared only with volume totals, chapters only with chapter totals - never one with the
  other. A folder that mixes volume and chapter archives gives no signal.
- The local side is the highest unit NUMBER the archive names state, in the folder and in its unit
  subfolders (:func:`local_of`) - never an archive count.
- The published side is the largest number any source states: volumes = origin or English volumes;
  chapters = the status line's total, the English chapters, or the latest tracked chapter (it restarts per
  season on renumbered webtoons, so the larger one counts).
- The latest tracked chapter follows scanlation releases, not the run: when the record counts its run in
  volumes and states no chapter total, it may lag far behind and never makes a conflict.
- Conflict: local > :data:`FACTOR` x published + :data:`SLACK`; otherwise agreement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from typing import Iterable, Optional, Sequence

from . import auto_match_text as amt
from .contracts import ChildFolderShape, MatchCandidate, MatchContext

FACTOR = 1.5
SLACK = 2

# Unit subfolders whose archives are not the numbered run (they never add a number).
_SIDE_WORDS = ("extra", "special", "side", "one", "oneshot", "bonus", "omake", "raw", "color", "colour")


class CountSignal(IntEnum):
    NONE = 0      # nothing to compare: no local number, no published total, or a mixed folder
    AGREE = 1     # the local number fits the published total
    CONFLICT = 2  # the local number is far above the published total


@dataclass(frozen=True)
class LocalUnitCounts:
    """How many archives name volumes / chapters and the unit NUMBERS they state (the highest, and the
    lowest for wording). ``highest_named_volume`` (1.29.0): the highest volume that CHAPTER archive names
    state ("Title v09 c060"), compared with volume totals only (never makes the folder "mixed")."""

    volume_archives: int = 0
    chapter_archives: int = 0
    lowest_volume: Optional[int] = None
    highest_volume: Optional[int] = None
    lowest_chapter: Optional[int] = None
    highest_chapter: Optional[int] = None
    highest_named_volume: Optional[int] = None

    @property
    def is_mixed(self) -> bool:
        return self.volume_archives > 0 and self.chapter_archives > 0


EMPTY = LocalUnitCounts()


@dataclass(frozen=True)
class PublishedUnitCounts:
    """The published side (public provider data)."""

    volumes: Optional[int]
    english_volumes: Optional[int]
    status_chapters: Optional[int]
    english_chapters: Optional[int]
    latest_chapter: Optional[int]

    @staticmethod
    def of(candidate: MatchCandidate) -> "PublishedUnitCounts":
        return PublishedUnitCounts(candidate.volumes, candidate.english_volumes, candidate.total_chapters,
                                   candidate.english_chapters, candidate.latest_chapter)


@dataclass(frozen=True)
class CountComparison:
    volumes: CountSignal
    chapters: CountSignal
    published_volumes: Optional[int]
    published_chapters: Optional[int]

    @property
    def is_conflict(self) -> bool:
        return CountSignal.CONFLICT in (self.volumes, self.chapters)


NO_COMPARISON = CountComparison(CountSignal.NONE, CountSignal.NONE, None, None)


def is_side_folder_name(name: Optional[str]) -> bool:
    """A unit subfolder that holds side material, not the numbered run: Extras, Specials, Side Stories,
    Oneshots, Bonus, Omake, Raws, Colored."""
    if not amt.is_unit_folder_name(name):
        return False
    s = name.lstrip("[( ").lower()
    return any(s.startswith(w) for w in _SIDE_WORDS)


def local_of(archive_names: Sequence[str], subfolders: Optional[Iterable[ChildFolderShape]]) -> LocalUnitCounts:
    """The local side of a folder: its own archive names, plus its unit subfolders (not side folders). A
    ``Volumes`` / ``Vol 1-5`` subfolder counts its archives as volumes and a ``Chapters`` one as chapters;
    other unit subfolders (``Season 2``, ``Part 3``, ``12``) are read by their archive names. A subfolder
    without names adds only the range its own name states (``Volumes 3-5``), never its archive count."""
    acc = _Accumulator()
    for name in archive_names:
        acc.add_loose(name)
    for sub in subfolders or ():
        if (sub.descendant_archive_count <= 0 or not amt.is_unit_folder_name(sub.display_name)
                or is_side_folder_name(sub.display_name)):
            continue
        names = sub.archive_names or ()
        if amt.is_volume_folder_name(sub.display_name):
            acc.volume_archives += sub.descendant_archive_count
            for n in names:
                u = amt.units_of(n)
                high = amt.volume_number_of(n)
                acc.volume(high if high is not None else amt.bare_number_of(n),
                           (u.volume if u.volume is not None else u.chapter) if not u.is_extra else None)
            if not names:
                acc.volume(_folder_range_end(sub.display_name), amt.units_of(sub.display_name).volume)
        elif amt.is_chapter_folder_name(sub.display_name):
            acc.chapter_archives += sub.descendant_archive_count
            for n in names:
                u = amt.units_of(n)
                high = amt.chapter_number_of(n)
                acc.chapter(high if high is not None else amt.bare_number_of(n), u.chapter if not u.is_extra else None)
                acc.named_volume(n)
            if not names:
                acc.chapter(_folder_range_end(sub.display_name), amt.units_of(sub.display_name).chapter)
        else:
            for n in names:
                acc.add_loose(n)
    return acc.result()


def from_context(context: MatchContext) -> LocalUnitCounts:
    """The local side a context carries: ``units`` when the planner set it, otherwise the older fields (the
    highest numbers, or the archive counts when no number is known)."""
    if context.units is not None:
        return context.units
    volumes = (context.local_volumes if context.local_volumes is not None else context.volume_like_count) \
        if context.volume_like_count > 0 else None
    chapters = (context.local_chapters if context.local_chapters is not None else context.chapter_like_count) \
        if context.chapter_like_count > 0 else None
    return LocalUnitCounts(context.volume_like_count, context.chapter_like_count, None, volumes, None, chapters)


def compare(local: LocalUnitCounts, published: PublishedUnitCounts) -> CountComparison:
    """Compares the local unit numbers with a record's published totals (see the module rules)."""
    if local.is_mixed:
        return NO_COMPARISON

    volume_total = max(published.volumes or 0, published.english_volumes or 0)
    chapter_total = max(published.status_chapters or 0, published.english_chapters or 0)
    latest = published.latest_chapter or 0
    chapter_bound = max(chapter_total, latest)
    # Only the latest tracked chapter, and the record counts its run in volumes: agreement at most.
    latest_only = chapter_total == 0 and volume_total > 0

    volumes = CountSignal.NONE
    if local.volume_archives > 0 and (local.highest_volume or 0) > 0 and volume_total > 0:
        volumes = CountSignal.CONFLICT if exceeds(local.highest_volume, volume_total) else CountSignal.AGREE
    # A chapter folder whose names state their volume ("v09 c060", 1.29.0): compared with the volume totals.
    elif local.volume_archives == 0 and (local.highest_named_volume or 0) > 0 and volume_total > 0:
        volumes = CountSignal.CONFLICT if exceeds(local.highest_named_volume, volume_total) else CountSignal.AGREE
    chapters = CountSignal.NONE
    if local.chapter_archives > 0 and (local.highest_chapter or 0) > 0 and chapter_bound > 0:
        if not exceeds(local.highest_chapter, chapter_bound):
            chapters = CountSignal.AGREE
        else:
            chapters = CountSignal.NONE if latest_only else CountSignal.CONFLICT
    return CountComparison(volumes, chapters, volume_total if volume_total > 0 else None,
                           chapter_bound if chapter_bound > 0 else None)


def exceeds(local: int, published: int) -> bool:
    """True when the local number is far above the published one."""
    return local > FACTOR * published + SLACK


def _folder_range_end(name: str) -> Optional[int]:
    u = amt.units_of(name)
    for end in (u.volume_end, u.volume, u.chapter_end, u.chapter):
        if end is not None:
            return math.floor(end)
    return None


class _Accumulator:
    def __init__(self) -> None:
        self.volume_archives = 0
        self.chapter_archives = 0
        self._low_volume: Optional[int] = None
        self._high_volume: Optional[int] = None
        self._low_chapter: Optional[int] = None
        self._high_chapter: Optional[int] = None
        self._high_named_volume: Optional[int] = None

    def add_loose(self, name: str) -> None:
        if amt.is_volume_like(name):
            self.volume_archives += 1
            u = amt.units_of(name)
            self.volume(amt.volume_number_of(name), u.volume if not u.is_extra else None)
        elif amt.is_chapter_like(name):
            self.chapter_archives += 1
            u = amt.units_of(name)
            self.chapter(amt.chapter_number_of(name), u.chapter if not u.is_extra else None)
            self.named_volume(name)

    def named_volume(self, name: str) -> None:
        """The volume a chapter archive's name states (``Title v09 c060`` -> 9), extras and ranges' top included."""
        u = amt.units_of(name)
        v = u.volume_end if u.volume_end is not None else u.volume
        if v is not None and v >= 1:
            self._high_named_volume = max(self._high_named_volume or 0, math.floor(v))

    # high: the matcher's integer (the 1.27.0 helpers); low: the start a name states, for wording only.
    def volume(self, high: Optional[int], low: Optional[Decimal]) -> None:
        self._low_volume, self._high_volume = _add(self._low_volume, self._high_volume, high, low)

    def chapter(self, high: Optional[int], low: Optional[Decimal]) -> None:
        self._low_chapter, self._high_chapter = _add(self._low_chapter, self._high_chapter, high, low)

    def result(self) -> LocalUnitCounts:
        return LocalUnitCounts(self.volume_archives, self.chapter_archives, self._low_volume, self._high_volume,
                               self._low_chapter, self._high_chapter, self._high_named_volume)


def _add(lowest: Optional[int], highest: Optional[int], high: Optional[int], low: Optional[Decimal]):
    if high is None:
        return lowest, highest
    highest = high if highest is None else max(highest, high)
    lo = min(math.floor(low), high) if low is not None else high
    lowest = lo if lowest is None else min(lowest, lo)
    return lowest, highest
