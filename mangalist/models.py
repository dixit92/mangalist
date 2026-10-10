"""Dataclasses shared between scanner, classifier, and GUI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable, List, Optional

if TYPE_CHECKING:  # the parser imports this module; import it lazily at run time
    from .inventory import Inventory
    from .parsing import ParsedName

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
    """Largest volume number across filenames (today's regex; see :meth:`MangaEntry.max_disk_volume`)."""
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


def _max_parsed(files: "Iterable[FileHit]", what: str) -> Optional[Decimal]:
    """Largest volume / chapter number over *files*: the parse's ``.end`` where a file was parsed,
    today's regex otherwise."""
    best: Optional[Decimal] = None
    legacy = []
    for f in files:
        p = f.parsed
        if p is None:
            legacy.append(f)
            continue
        r = p.volume if what == "volume" else p.chapter
        if r is not None and (best is None or r.end > best):
            best = r.end
    if legacy:
        old = _max_volume(legacy) if what == "volume" else _max_chapter(legacy)
        if old is not None:
            d = Decimal(repr(old))
            if best is None or d > best:
                best = d
    return best


def _is_under(path: Path, folder: Path) -> bool:
    try:
        Path(path).relative_to(folder)
        return True
    except ValueError:
        return False


class Verdict(str, Enum):
    VOLUMES = "Volumes"
    CHAPTERS = "Chapters"
    BOTH = "Both"
    UNKNOWN = "Unknown"


@dataclass
class FileHit:
    """A single archive file inside a manga folder.

    ``parsed`` is the layered parser's reading of the name (:func:`mangalist.parsing.parse_name`, with
    the series' kind hint), set by the scanner; ``has_volume`` / ``has_chapter`` then say whether the
    parse found a volume / chapter number. A hit built without ``parsed`` (older callers, tests) keeps
    the token flags it is given.
    """

    path: Path
    size: int
    depth: int  # 0 == directly under manga folder, >=1 == inside a subfolder
    has_volume: bool = False
    has_chapter: bool = False
    parsed: Optional["ParsedName"] = field(default=None, compare=False)
    mtime_ns: Optional[int] = field(default=None, compare=False)  # set by the scanner (archive rows)

    @property
    def kind(self) -> str:
        """``"volume"`` / ``"chapter"`` / ``"ambiguous"`` (the classifier's vocabulary).

        From the parse when there is one: a chapter (``Vol. X Ch. Y`` is a chapter of volume X) and an
        extra are ``"chapter"``; a volume archive that also carries loose chapters
        (``Title v10 + 085-086``, parser kind ``both``) is ``"volume"``; a bare number the series has
        no answer for yet is ``"ambiguous"``. Without a parse: chapter token wins, as before.
        """
        p = self.parsed
        if p is not None:
            k = getattr(p.kind, "value", p.kind)
            if k == "chapter":
                return "chapter"
            if k in ("volume", "both"):
                return "volume"
            return "ambiguous"
        if self.has_chapter:
            return "chapter"
        if self.has_volume:
            return "volume"
        return "ambiguous"

    @property
    def unit_kind(self) -> str:
        """The parser's kind: ``"volume"`` / ``"chapter"`` / ``"both"`` / ``"unknown"`` (no parse: from
        :attr:`kind`, ``"ambiguous"`` -> ``"unknown"``)."""
        if self.parsed is not None:
            return str(getattr(self.parsed.kind, "value", self.parsed.kind))
        k = self.kind
        return "unknown" if k == "ambiguous" else k

    @property
    def needs_kind(self) -> bool:
        """A bare number (``01.cbz``) whose kind neither the name nor the series' answer gives - also one read as a
        chapter only by guess (``Title 07.cbz``, :attr:`~mangalist.parsing.ParsedName.guessed`): the question stays
        open until the owner answers."""
        p = self.parsed
        if p is None or p.number is None:
            return False
        return getattr(p.kind, "value", p.kind) == "unknown" or bool(getattr(p, "guessed", False))


@dataclass
class MangaEntry:
    """One series folder: an immediate subfolder of a root (or a franchise parent's subseries)."""

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
    mu_score_version: int = 5           # see mu_cache.MU_SCORE_VERSION (1 = legacy Jaccard)
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
    # The library root (store.Root id) the folder was found in; None for a scan without the database.
    root_id: Optional[int] = None
    # The series' stored "volumes or chapters?" answer the files were parsed with (C12): None | volumes | chapters.
    kind_hint: Optional[str] = None
    # Series folders inside this one that are entries of their own (a franchise parent with direct
    # files): their archives stay in ``files`` (counts as before) but not in the inventory / units.
    nested_series: List[Path] = field(default_factory=list)
    _inventory_cache: Any = field(default=None, init=False, repr=False, compare=False)

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
        """Highest chapter number in any file, or None (a float for the Behind column; exact:
        :attr:`highest_chapter`).

        From the layered parser where a file was parsed (FMD2 names: the bracket head only, so a title
        like "Episode 3" is not read; a bare number counts once the series' answer is "chapters"),
        else today's regex. Chapter *ranges* like ``Ch.10-15`` count as their upper bound."""
        best = _max_parsed(self.files, "chapter")
        return None if best is None else float(best)

    @property
    def max_disk_volume(self) -> Optional[float]:
        """Highest volume number in any file, or None - including the volume a chapter belongs to
        (``Vol. 3 Ch. 12`` -> 3), as before. Parsed files as in :attr:`max_disk_chapter`."""
        best = _max_parsed(self.files, "volume")
        return None if best is None else float(best)

    @property
    def inventory_files(self) -> List[FileHit]:
        """The archives that belong to this series (``files`` minus those of :attr:`nested_series`)."""
        if not self.nested_series:
            return list(self.files)
        nested = [Path(p) for p in self.nested_series]
        return [f for f in self.files if not any(_is_under(f.path, n) for n in nested)]

    def inventory(self, volume_list: Optional[Iterable[Any]] = None) -> "Inventory":
        """The series' held units (:class:`mangalist.inventory.Inventory`), optionally merged with a
        volume list (MangaPixer's ``volumes.items``). Cached while the files and their parses are the
        same (without a volume list)."""
        from .inventory import inventory_of_entry

        if volume_list is not None:
            return inventory_of_entry(self, volume_list)
        sig = tuple((id(f), id(f.parsed)) for f in self.files) + (tuple(map(str, self.nested_series)),)
        cached = self._inventory_cache
        if cached is not None and cached[0] == sig:
            return cached[1]
        inv = inventory_of_entry(self)
        self._inventory_cache = (sig, inv)
        return inv

    @property
    def highest_volume(self) -> Optional[Decimal]:
        """Highest whole volume held (exact; a chapter's volume number does not count)."""
        return self.inventory().highest_volume

    @property
    def highest_chapter(self) -> Optional[Decimal]:
        """Highest chapter held as a chapter file (exact; no volume list merged here)."""
        return self.inventory().highest_chapter

    @property
    def needs_kind(self) -> bool:
        """Needs attention: bare-number files whose kind is unknown - ask "volumes or chapters?" once
        (Design Decisions C12) and store the answer (``store.set_series_kind``)."""
        return any(f.needs_kind for f in self.inventory_files)

    @property
    def unknown_kind_files(self) -> List[FileHit]:
        """The files :attr:`needs_kind` is about."""
        return [f for f in self.inventory_files if f.needs_kind]

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
