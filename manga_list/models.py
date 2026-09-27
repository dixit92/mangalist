"""Dataclasses shared between scanner, classifier, and GUI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional

# Capture the numeric component of a vol/ch token. Mirrors classifier's regexes
# but exposes the number (incl. decimals like "Ch.12.5") for max-token extraction.
_RE_VOL_NUM = re.compile(
    r"(?<![A-Za-z])(?:vol(?:ume)?\.?|v)\s*[_\-.]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# Match a chapter token, optionally a range (Ch.10-15). Group 1 = start, group 2 = end (or None).
_RE_CH_NUM = re.compile(
    r"(?<![A-Za-z])(?:ch(?:apter|ap|p|\.)?|c)\s*[_\-.]?\s*"
    r"(\d{1,4}(?:\.\d+)?)(?:\s*[-–]\s*(\d{1,4}(?:\.\d+)?))?",
    re.IGNORECASE,
)
# Filename hints that the file bundles multiple chapters into one volume archive.
_RE_COMPILATION = re.compile(r"(?:compilation|omnibus|bundle|collection|box[ _]?set)",
                             re.IGNORECASE)


def _max_volume(files: "Iterable[FileHit]") -> Optional[float]:
    best: Optional[float] = None
    for f in files:
        stem = f.path.name.rsplit(".", 1)[0] if "." in f.path.name else f.path.name
        for m in _RE_VOL_NUM.finditer(stem):
            try:
                v = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if best is None or v > best:
                best = v
    return best


def _max_chapter(files: "Iterable[FileHit]") -> Optional[float]:
    """Largest chapter number across filenames. Honours ranges (Ch.10-15 → 15)."""
    best: Optional[float] = None
    for f in files:
        stem = f.path.name.rsplit(".", 1)[0] if "." in f.path.name else f.path.name
        for m in _RE_CH_NUM.finditer(stem):
            try:
                lo = float(m.group(1))
            except (TypeError, ValueError):
                continue
            hi_grp = m.group(2)
            try:
                hi = float(hi_grp) if hi_grp else lo
            except (TypeError, ValueError):
                hi = lo
            v = max(lo, hi)
            if best is None or v > best:
                best = v
    return best


class Verdict(str, Enum):
    VOLUMES = "Volumes"
    CHAPTERS = "Chapters"
    BOTH = "Both"
    UNKNOWN = "Unknown"


@dataclass
class FileHit:
    """A single archive file inside a manga folder."""

    path: Path
    size: int
    depth: int  # 0 == directly under manga folder, >=1 == inside a subfolder
    has_volume: bool = False
    has_chapter: bool = False

    @property
    def kind(self) -> str:
        # "Vol. X Ch. Y" -> chapter wins.
        if self.has_chapter:
            return "chapter"
        if self.has_volume:
            return "volume"
        return "ambiguous"


@dataclass
class MangaEntry:
    """One immediate subfolder of the Manga Root."""

    folder: Path
    title: str
    english_title: Optional[str]
    files: List[FileHit] = field(default_factory=list)
    n_subfolders: int = 0
    last_modified: float = 0.0

    # Filled in by classifier.classify():
    vol_pct: float = 0.0
    ch_pct: float = 0.0
    both_pct: float = 0.0
    verdict: Verdict = Verdict.UNKNOWN
    reasons: List[str] = field(default_factory=list)

    # User-facing flag, persisted in config.json by absolute folder path.
    examined: bool = False

    # MangaUpdates match — populated asynchronously after scan.
    mu_id: Optional[int] = None          # series_id on MangaUpdates
    mu_title: Optional[str] = None       # matched title as returned by MU
    mu_url: Optional[str] = None         # series page URL
    licensed: Optional[bool] = None      # True / False / None = unknown
    mu_confirmed: bool = False           # user has manually confirmed the match
    mu_associated: List[str] = field(default_factory=list)  # all alt titles from MU
    mu_score: float = 0.0               # raw title score of the match (meaning depends on the version)
    mu_score_version: int = 3           # see mu_cache.MU_SCORE_VERSION (1 = legacy Jaccard)
    mu_band: Optional[str] = None       # "auto" | "review" | "unmatched" | "not_a_work" (mu_match.BAND_*)
    mu_reasons: List[str] = field(default_factory=list)  # matcher reason names of the match
    mu_work_class: Optional[str] = None  # detector class name (in memory only)
    # Latest chapter reported by scanlation feed (MU's series.latest_chapter).
    scan_latest_chapter: Optional[float] = None
    # English publisher info parsed from publishers[].notes:
    publisher_name: Optional[str] = None
    publisher_chapters: Optional[float] = None
    publisher_volumes: Optional[float] = None
    publisher_status: Optional[str] = None  # "Ongoing" | "Completed" | "Cancelled" | …
    # Latest volume reported by scanlation (from MU /releases/search).
    scan_latest_volume: Optional[float] = None
    # AniList supplementary data for cross-unit estimation:
    anilist_id: Optional[int] = None
    anilist_chapters: Optional[float] = None   # total chapters per AniList
    anilist_volumes: Optional[float] = None    # total volumes per AniList
    # Completion status in country of origin (MU series.completed).
    completed_in_origin: Optional[bool] = None
    # User override for Behind column: 'done' = treat as up to date; None = normal.
    behind_override: Optional[str] = None
    # For franchise subseries: the parent folder that contains this series.
    # None for normal entries, Path for subseries extracted from franchise parent.
    parent_folder: Optional[Path] = None

    @property
    def n_files(self) -> int:
        return len(self.files)

    @property
    def n_volume_files(self) -> int:
        return sum(1 for f in self.files if f.kind == "volume")

    @property
    def n_chapter_files(self) -> int:
        return sum(1 for f in self.files if f.kind == "chapter")

    @property
    def n_ambiguous(self) -> int:
        return sum(1 for f in self.files if f.kind == "ambiguous")

    @property
    def parent_volume_files(self) -> int:
        return sum(1 for f in self.files if f.kind == "volume" and f.depth == 0)

    @property
    def subfolder_chapter_files(self) -> int:
        return sum(1 for f in self.files if f.kind == "chapter" and f.depth >= 1)

    @property
    def max_disk_chapter(self) -> Optional[float]:
        """Highest chapter number found in any filename, or None if no chapter tokens.

        Chapter *ranges* like ``Ch.10-15`` count as their upper bound (useful for
        volume-compilation archives that bundle multiple chapters)."""
        return _max_chapter(self.files)

    @property
    def max_disk_volume(self) -> Optional[float]:
        """Highest volume number found in any filename, or None if no volume tokens."""
        return _max_volume(self.files)

    @property
    def has_compilation_files(self) -> bool:
        """True if any filename signals it bundles multiple chapters (compilation/omnibus/…)."""
        return any(_RE_COMPILATION.search(f.path.name) for f in self.files)

    @property
    def median_size(self) -> int:
        if not self.files:
            return 0
        sizes = sorted(f.size for f in self.files)
        mid = len(sizes) // 2
        if len(sizes) % 2 == 1:
            return sizes[mid]
        return (sizes[mid - 1] + sizes[mid]) // 2
