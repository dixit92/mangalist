"""What a matched series is missing, as unit ranges another program can fetch (pure, no Qt, no network).

Manga-List never downloads: it works out what is wanted and hands it off (a gallery-dl input file, an FMD2 import
file, later Suwayomi and nyaa.si). The ranges use the same numbers the Behind column shows:

- **chapters**: the highest chapter on disk is below MangaUpdates' latest scanlated chapter -> chapters
  ``disk + 1`` .. ``latest``. Only for folders that hold chapters: a folder of volumes has no chapter numbers to
  continue from.
- **volumes**: licensed in English and the highest volume on disk is below the English publisher's volume count ->
  volumes ``disk + 1`` .. ``publisher``.
- **upgrade**: licensed in English, the publisher has volumes, and the folder holds chapters only -> the English
  volumes ``1`` .. ``publisher`` that collect them.

A row marked "up to date" or holding a finished omnibus / compilation gets nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..models import MangaEntry

CHAPTERS = "chapters"
VOLUMES = "volumes"
UPGRADE = "upgrade"


@dataclass(frozen=True)
class WantedRange:
    """Units ``start`` .. ``end`` (whole numbers, inclusive) of one kind."""

    kind: str
    start: int
    end: int

    def describe(self) -> str:
        unit = "chapter" if self.kind == CHAPTERS else "volume"
        what = f"{unit}s {self.start}-{self.end}" if self.end > self.start else f"{unit} {self.start}"
        return f"{what} (upgrade from chapters)" if self.kind == UPGRADE else what


@dataclass(frozen=True)
class Wanted:
    """One matched series and what it is missing."""

    folder: Path
    title: str
    mu_id: int
    mu_title: str
    titles: Tuple[str, ...]  # the MangaUpdates title first, then its alternative titles (for lookups)
    ranges: Tuple[WantedRange, ...]

    def of_kind(self, *kinds: str) -> Tuple[WantedRange, ...]:
        return tuple(r for r in self.ranges if r.kind in kinds)


def is_omnibus_complete(e: MangaEntry) -> bool:
    """True when disk files signal omnibus / compilation AND the translation is done (shown as up to date)."""
    if not e.has_compilation_files:
        return False
    pub_done = (e.publisher_status or "").strip().lower() in ("completed", "complete")
    return pub_done or e.completed_in_origin is True


def wanted_for(e: MangaEntry) -> Optional[Wanted]:
    """What ``e`` is missing, or None when it is not matched, marked up to date, or missing nothing."""
    if e.mu_id is None or e.behind_override == "done" or is_omnibus_complete(e):
        return None
    ranges: List[WantedRange] = []
    disk_ch = e.max_disk_chapter
    disk_vol = e.max_disk_volume

    if disk_ch is not None and e.scan_latest_chapter is not None:
        r = _after(disk_ch, e.scan_latest_chapter, CHAPTERS)
        if r is not None:
            ranges.append(r)

    if e.licensed is True and e.publisher_volumes is not None and e.publisher_volumes >= 1:
        if disk_vol is not None:
            r = _after(disk_vol, e.publisher_volumes, VOLUMES)
            if r is not None:
                ranges.append(r)
        elif disk_ch is not None:
            ranges.append(WantedRange(UPGRADE, 1, math.floor(e.publisher_volumes)))

    if not ranges:
        return None
    mu_title = e.mu_title or e.title
    titles = tuple(dict.fromkeys(t for t in (mu_title, *e.mu_associated) if t and t.strip()))
    return Wanted(e.folder, e.title, int(e.mu_id), mu_title, titles, tuple(ranges))


def wanted_list(entries: Sequence[MangaEntry]) -> List[Wanted]:
    """The wanted items of ``entries``, in their order."""
    return [w for w in (wanted_for(e) for e in entries) if w is not None]


def _after(have: float, latest: float, kind: str) -> Optional[WantedRange]:
    """The whole units after ``have`` up to ``latest`` (the Behind column's delta of at least one unit)."""
    start = math.floor(have) + 1
    end = math.floor(latest)
    return WantedRange(kind, start, end) if end >= start else None
