"""The stage-2 matcher contract (port of MangaPixer 1.31.1 ``AutoMatchContracts.cs``).

Rules carried over unchanged (MangaPixer owner decisions, 2026-09-26): only folders the detector
classes as series-like are matched at folder level; archive-level matching only inside collection
folders, numbered mini-series grouped, author conflicts veto auto; ambiguous folders go to review,
never auto. Thresholds are bounded (``MatchThresholds``). Enum values are kept identical to the
reference so reasons and classes mean the same thing in both projects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from .count_evidence import LocalUnitCounts


class WorkClass(IntEnum):
    """What a folder is, decided from its shape alone (names and counts; no IO)."""

    EXCLUDED = 0             # library root, or excluded by the caller
    SERIES = 1               # a leaf whose archives are units (chapters / volumes) of one work
    SERIES_WITH_UNITS = 2    # only unit subfolders (Volumes/, Chapters/, Season N/), maybe loose archives
    ONE_SHOT = 3             # exactly one archive and no subfolder
    COLLECTION_LEAF = 4      # a leaf of different works: archive-level matching
    ARTIST_COLLECTION = 5    # a collection leaf named after the artist / circle its archives carry
    FRANCHISE_CONTAINER = 6  # >= 2 related non-unit subfolders: its children are the candidates
    COLLECTION_CONTAINER = 7  # >= 2 unrelated non-unit subfolders: children are candidates
    WRAPPER = 8              # exactly one non-unit subfolder and no archives
    MIXED = 9                # one non-unit subfolder plus loose archives (MangaPixer matches loose works one by one)
    UNIT_SUB = 10            # a unit subfolder below a series: inherits, never a candidate
    AMBIGUOUS = 11           # neither a clear series nor a clear collection: review only


class MatchLevel(IntEnum):
    NONE = 0         # not matched (containers, wrappers, unit subfolders, exclusions)
    FOLDER = 1       # the folder itself is the work
    ARCHIVE = 2      # each archive (or numbered archive group) is its own work
    REVIEW_ONLY = 3  # may be matched but never auto-linked (Ambiguous; Mixed whose loose archives are units of one work)


class ContentSuggestion(IntEnum):
    NONE = 0
    DOUJINSHI_AND_ADULT_ONE_SHOTS = 1


class QueryVariantKind(IntEnum):
    """Where a query variant came from (order = query priority)."""

    COMIC_INFO_SERIES = 0
    PRIMARY = 1
    ENGLISH_TITLE = 2
    SUBTITLE_SPLIT = 3
    SEQUEL_NUMBER_SPLIT = 4
    ARCHIVE_DERIVED_TITLE = 5
    DOUJIN_PARODY_FORM = 6
    # 1.27.0: the title part of a name that also carries a plain-separator author ("Title by Author",
    # "Title - Chapter | Author", "Author - Title"). Retrieval only: it scores at most the review-only cap
    # unless a creator hint names one of the record's authors.
    CREATOR_SPLIT = 7


class MetadataOrigin(IntEnum):
    """Normalized origin (MangaUpdates ``type`` maps here); names match the reference enum."""

    Japan = 0
    Korea = 1
    ChinaTaiwan = 2
    EnglishOriginal = 3
    Philippines = 4
    Indonesia = 5
    Thailand = 6
    Vietnam = 7
    Malaysia = 8
    Nordic = 9
    French = 10
    Spanish = 11
    German = 12
    Other = 13


class MetadataFormat(IntEnum):
    COMIC = 0
    NOVEL = 1
    ARTBOOK = 2
    DOUJINSHI = 3
    AUDIO = 4


class MatchBand(IntEnum):
    UNMATCHED = 0
    NEEDS_REVIEW = 1
    AUTO = 2


class MatchReason(IntFlag):
    """Why a candidate was demoted or flagged (bit values identical to the reference)."""

    NONE = 0
    CLOSE_SECOND = 1 << 0
    COUNT_CONFLICT = 1 << 1
    YEAR_CONFLICT = 1 << 2
    TYPE_CONFLICT = 1 << 3
    RELATED_PAIR = 1 << 4
    ONE_SHOT_MISMATCH = 1 << 5  # retired 2026-09-26 in the reference; never raised, kept for stable values
    AUTHOR_CONFLICT = 1 << 6
    NUMBER_MISMATCH = 1 << 7
    REVIEW_ONLY_CLASS = 1 << 8
    # The flags below keep the reference's bit values. Manga-List's scorer raises only SUBTITLE_FAMILY and
    # SERIES_FAMILY of them; the others need MangaPixer-only inputs (cover images, admin-declared facts,
    # stored volume data) and are listed so a stored reason name always means the same in both projects.
    COVER_MATCH = 1 << 9              # 1.28.0: the candidate's cover equals the local cover (positive)
    DECLARED_TYPE_AGREE = 1 << 10     # 1.30.0: the record fits the folder's declared type (positive)
    DECLARED_TYPE_MISMATCH = 1 << 11  # 1.30.0: the record contradicts the declared type (never a veto)
    REACH_CONFLICT = 1 << 12          # 1.30.0: after linking, the folder goes far past the record
    # 1.30.0: only the folder name's subtitle decides between the top record and one of its series family
    # (a main series vs its spin-off). A veto: the work goes to review.
    SUBTITLE_FAMILY = 1 << 13
    # 1.30.0: another candidate at the review floor is the top's series family. Informational, never a veto.
    SERIES_FAMILY = 1 << 14
    COVER_DIFFERS = 1 << 15           # 1.31.0: after linking, the local volume covers differ from the record's


# .NET names of the reason flags, used for reports that must read like the reference's.
REASON_NAMES = {
    MatchReason.CLOSE_SECOND: "CloseSecond",
    MatchReason.COUNT_CONFLICT: "CountConflict",
    MatchReason.YEAR_CONFLICT: "YearConflict",
    MatchReason.TYPE_CONFLICT: "TypeConflict",
    MatchReason.RELATED_PAIR: "RelatedPair",
    MatchReason.ONE_SHOT_MISMATCH: "OneShotMismatch",
    MatchReason.AUTHOR_CONFLICT: "AuthorConflict",
    MatchReason.NUMBER_MISMATCH: "NumberMismatch",
    MatchReason.REVIEW_ONLY_CLASS: "ReviewOnlyClass",
    MatchReason.COVER_MATCH: "CoverMatch",
    MatchReason.DECLARED_TYPE_AGREE: "DeclaredTypeAgree",
    MatchReason.DECLARED_TYPE_MISMATCH: "DeclaredTypeMismatch",
    MatchReason.REACH_CONFLICT: "ReachConflict",
    MatchReason.SUBTITLE_FAMILY: "SubtitleFamily",
    MatchReason.SERIES_FAMILY: "SeriesFamily",
    MatchReason.COVER_DIFFERS: "CoverDiffers",
}


def reason_names(reasons: MatchReason) -> Tuple[str, ...]:
    """The set flags in ascending bit order, by their reference names."""
    return tuple(name for flag, name in REASON_NAMES.items() if reasons & flag)


def reasons_text(reasons: MatchReason) -> str:
    """``MatchReason.ToString()`` of the reference: ``None`` or ``A, B``."""
    names = reason_names(reasons)
    return ", ".join(names) if names else "None"


@dataclass(frozen=True)
class ChildFolderShape:
    """A direct subfolder. ``archive_names`` (optional, 1.29.0): the display names of the archives below a
    UNIT subfolder, so the count rule reads their unit numbers (``count_evidence.local_of``); None for other
    subfolders."""

    display_name: str
    descendant_archive_count: int
    archive_names: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class FolderShape:
    """One folder as the detector sees it: display names only (never paths) and counts.

    ``depth``: 0 = the library root, its direct children 1. ``category_hint``: the nearest ancestor
    named like a category ("Manga", "Manhwa"). ``known_author_names``: provider author names the
    caller already holds locally (MangaPixer passes the creators of records linked in the library, 1.28.0);
    a leaf of two or more archives named like one of them, whose shape is not one series, is an artist
    collection.
    """

    display_name: str
    depth: int
    archive_names: Tuple[str, ...] = ()
    subfolders: Tuple[ChildFolderShape, ...] = ()
    parent_display_name: Optional[str] = None
    category_hint: Optional[str] = None
    known_author_names: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class ArchiveGroup:
    """Archives of a collection folder that form one work (a numbered mini-series, or one archive)."""

    query_title: str
    archive_indexes: Tuple[int, ...]


@dataclass(frozen=True)
class WorkClassification:
    cls: WorkClass
    level: MatchLevel
    reasons: Tuple[str, ...]
    archive_groups: Tuple[ArchiveGroup, ...] = ()
    content_suggestion: ContentSuggestion = ContentSuggestion.NONE


@dataclass(frozen=True)
class QueryVariant:
    text: str
    kind: QueryVariantKind


@dataclass(frozen=True)
class MatchContext:
    """Local signals used to corroborate candidates (never sent anywhere).

    ``local_volumes`` / ``local_chapters`` (1.27.0): the highest unit number the archive names state (the
    count rule compares numbers, not file counts). ``units`` (1.29.0): the count rule's local side
    (``count_evidence.local_of``); when None the rule reads the fields above. MangaPixer's ``CoverMatches``
    and ``DeclaredType`` are not ported (no cover images, no declared facts in Manga-List).
    """

    cls: WorkClass
    archive_count: int
    volume_like_count: int
    chapter_like_count: int
    earliest_year: Optional[int]
    category_hint: Optional[str]
    tall_strips: bool
    author_tags: Tuple[str, ...]
    comic_info_series: Optional[str] = None
    # 1.26.1: names from [...] / (...) groups of the folder or archive name that may be an author;
    # positive evidence only - unlike author_tags they never veto.
    creator_hints: Tuple[str, ...] = ()
    local_volumes: Optional[int] = None
    local_chapters: Optional[int] = None
    units: Optional["LocalUnitCounts"] = None


@dataclass(frozen=True)
class MatchQuery:
    """Ordered, de-duplicated query variants plus the corroboration context."""

    variants: Tuple[QueryVariant, ...]
    context: MatchContext


@dataclass(frozen=True)
class CandidateRelation:
    external_id: str
    relation: str


@dataclass(frozen=True)
class MatchCandidate:
    """A provider record as the scorer sees it (public provider data only).

    ``origin`` accepts a provider type ("Manga", "Manhwa", "Manhua", "OEL") or a ``MetadataOrigin``
    name. ``total_chapters``: the stated chapter total (MangaUpdates status "652 Chapters"); the
    count rule compares chapters with the larger of it and ``latest_chapter``. ``english_volumes`` /
    ``english_chapters`` (1.27.0): the English publisher's totals (``publishers[].notes``); the count rule
    reads the largest published number of any source.
    """

    provider: str
    external_id: str
    title: str
    alt_titles: Tuple[str, ...] = ()
    format: Optional[MetadataFormat] = None
    origin: Optional[str] = None
    start_year: Optional[int] = None
    volumes: Optional[int] = None
    latest_chapter: Optional[int] = None
    authors: Tuple[str, ...] = ()
    relations: Tuple[CandidateRelation, ...] = ()
    webtoon: Optional[bool] = None
    total_chapters: Optional[int] = None
    english_volumes: Optional[int] = None
    english_chapters: Optional[int] = None


@dataclass(frozen=True)
class MatchThresholds:
    """Bounded thresholds (MangaPixer owner decision 13). The one-shot rule is a code constant."""

    auto_title: float
    margin: float
    review_floor: float

    AUTO_TITLE_MIN = 0.85
    AUTO_TITLE_MAX = 0.99
    MARGIN_MIN = 0.05
    MARGIN_MAX = 0.30
    REVIEW_FLOOR_MIN = 0.40
    REVIEW_FLOOR_MAX = 0.90

    @property
    def is_valid(self) -> bool:
        return (self.AUTO_TITLE_MIN <= self.auto_title <= self.AUTO_TITLE_MAX
                and self.MARGIN_MIN <= self.margin <= self.MARGIN_MAX
                and self.REVIEW_FLOOR_MIN <= self.review_floor <= self.REVIEW_FLOOR_MAX
                and self.review_floor < self.auto_title)


DEFAULT_THRESHOLDS = MatchThresholds(0.92, 0.10, 0.60)


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: MatchCandidate
    title_score: float
    adjusted_score: float
    reasons: MatchReason


@dataclass(frozen=True)
class MatchOutcome:
    """The scorer's decision. ``to_persist`` is the adaptive review set: candidates within 0.15 of the
    top, at most 5; only the top when it leads the next by >= 0.30."""

    band: MatchBand
    ranked: Tuple[ScoredCandidate, ...] = field(default=())
    to_persist: Tuple[ScoredCandidate, ...] = field(default=())
