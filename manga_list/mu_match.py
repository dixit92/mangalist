"""Bridge between Manga-List's scan results and the stage-2 matcher (``manga_list.matcher``).

The matcher decides the tier; Manga-List keeps its own flow and UI:

- ``auto``: shown as a normal (unconfirmed) match.
- ``review``: the top candidate is shown with the amber "needs review" highlight and the reasons.
- ``unmatched``: no confident candidate; an unconfirmed older match is cleared.
- ``not_a_work``: the folder is not one work (a collection of separate works, a container, a unit
  subfolder), so it is not matched at folder level. Manga-List shows one row per folder and cannot
  link each archive of a collection separately.

Confirmed matches are the user's decision and are never re-scored.

``mu_score`` holds the RAW title score of the chosen record (0..1). Its meaning changed with this
matcher (score version 2): version-1 scores (word-set Jaccard, with a subset scoring 1.0) are kept in
old cache rows but never compared with the new thresholds. Pure: no Qt import.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .matcher import (
    DEFAULT_THRESHOLDS,
    ChildFolderShape,
    FolderShape,
    MatchBand,
    MatchCandidate,
    MatchLevel,
    MatchReason,
    MatchThresholds,
    ScoredCandidate,
    WorkClass,
    WorkClassification,
    reason_names,
)
from .matcher import auto_match_text, detector, planner
from .matcher.retrieval import RetrievalResult, retrieve_and_score
from .models import MangaEntry
from .mu_cache import MU_SCORE_VERSION

# Version of the mu_score semantics (owned by the cache): 1 = legacy word-set Jaccard (rows written
# before this matcher); 2 = stage-2 raw title score (MangaPixer 1.26.0 port).
LEGACY_SCORE_VERSION = 1

BAND_AUTO = "auto"
BAND_REVIEW = "review"
BAND_UNMATCHED = "unmatched"
BAND_NOT_A_WORK = "not_a_work"

_BAND_BY_OUTCOME = {
    MatchBand.AUTO: BAND_AUTO,
    MatchBand.NEEDS_REVIEW: BAND_REVIEW,
    MatchBand.UNMATCHED: BAND_UNMATCHED,
}

REASON_LABELS = {
    "CloseSecond": "close second candidate",
    "CountConflict": "file count does not fit the record",
    "YearConflict": "files older than the series",
    "TypeConflict": "type / origin conflict",
    "RelatedPair": "related records (sequel, spin-off) score alike",
    "OneShotMismatch": "one-shot mismatch",
    "AuthorConflict": "creator tag names none of the authors",
    "NumberMismatch": "sequel / part number differs",
    "ReviewOnlyClass": "folder shape is review-only",
}

_CLASS_LABELS = {
    WorkClass.EXCLUDED: "no archives",
    WorkClass.COLLECTION_LEAF: "a collection of separate works",
    WorkClass.ARTIST_COLLECTION: "an artist / circle collection",
    WorkClass.FRANCHISE_CONTAINER: "a franchise container",
    WorkClass.COLLECTION_CONTAINER: "a collection container",
    WorkClass.WRAPPER: "a wrapper folder",
    WorkClass.MIXED: "a series folder plus separate loose works",
    WorkClass.UNIT_SUB: "a unit subfolder (Volumes, Chapters, Season N)",
}


@dataclass(frozen=True)
class EntryMatch:
    """What the matcher decided for one entry."""

    band: str
    classification: WorkClassification
    top: Optional[ScoredCandidate] = None
    reasons: Tuple[str, ...] = ()
    result: Optional[RetrievalResult] = None

    @property
    def title_score(self) -> float:
        return self.top.title_score if self.top is not None else 0.0


# ---------------------------------------------------------------------------
# Entry -> folder shape
# ---------------------------------------------------------------------------

def folder_shape(entry: MangaEntry) -> FolderShape:
    """The detector's view of a scanned entry: display names and counts only.

    Depth: the Manga Root is the library root (0), its folders 1, a franchise subseries 2. Archive
    names are the entry's direct archives; subfolders are counted from the archives found below
    them (the scanner walks at most three levels). The category hint is the nearest ancestor named
    like a category (e.g. a Manga Root called "Manhwa").
    """
    folder = Path(entry.folder)
    direct: List[str] = []
    below: Counter = Counter()
    for f in entry.files:
        try:
            rel = Path(f.path).relative_to(folder)
        except ValueError:
            continue
        if len(rel.parts) <= 1:
            direct.append(rel.name)
        else:
            below[rel.parts[0]] += 1
    subfolders = tuple(ChildFolderShape(name, count) for name, count in sorted(below.items()))

    ancestors = [folder.parent]
    if entry.parent_folder is not None:
        ancestors.append(folder.parent.parent)
    category = next((a.name for a in ancestors if a.name and auto_match_text.is_category_word(a.name)), None)

    return FolderShape(
        display_name=folder.name,
        depth=2 if entry.parent_folder is not None else 1,
        archive_names=tuple(sorted(direct)),
        subfolders=subfolders,
        parent_display_name=Path(entry.parent_folder).name if entry.parent_folder is not None else None,
        category_hint=category,
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_entry(
    entry: MangaEntry,
    search: Callable[[str], Sequence[MatchCandidate]],
    get: Callable[[str], Optional[MatchCandidate]],
    thresholds: MatchThresholds = DEFAULT_THRESHOLDS,
) -> EntryMatch:
    """Classify the entry and, when it is one work, search, score and band it."""
    shape = folder_shape(entry)
    classification = detector.classify(shape)
    if classification.level not in (MatchLevel.FOLDER, MatchLevel.REVIEW_ONLY):
        return EntryMatch(BAND_NOT_A_WORK, classification, reasons=classification.reasons)

    query = planner.plan_folder(shape, classification)
    result = retrieve_and_score(query, search, get, thresholds)
    outcome = result.outcome
    top = outcome.ranked[0] if outcome.ranked else None
    band = _BAND_BY_OUTCOME[outcome.band]
    reasons = reason_names(top.reasons) if top is not None else ()
    return EntryMatch(band, classification, top if band != BAND_UNMATCHED else None, reasons, result)


def candidate_titles(candidate: MatchCandidate) -> List[str]:
    """Title plus alt titles, de-duplicated (stored as ``mu_associated``)."""
    titles: List[str] = []
    for t in (candidate.title, *candidate.alt_titles):
        if t and t not in titles:
            titles.append(t)
    return titles


# ---------------------------------------------------------------------------
# Display helpers (used by the table model)
# ---------------------------------------------------------------------------

def is_legacy_score(entry: MangaEntry) -> bool:
    """The stored score was written by the old matcher and is not comparable with the new tiers."""
    return entry.mu_title is not None and entry.mu_score_version < MU_SCORE_VERSION


def needs_review(entry: MangaEntry) -> bool:
    """An unconfirmed match the matcher put in the review tier (amber highlight)."""
    return (entry.mu_title is not None and not entry.mu_confirmed
            and not is_legacy_score(entry) and entry.mu_band == BAND_REVIEW)


def reason_text(reasons: Sequence[str]) -> str:
    return ", ".join(REASON_LABELS.get(r, r) for r in reasons)


def match_tooltip(entry: MangaEntry) -> Optional[str]:
    """Tooltip text for the MU Title column."""
    if entry.mu_title is None:
        if entry.mu_band == BAND_UNMATCHED:
            return "No confident MangaUpdates match - right-click > Fix MangaUpdates match... to pick one"
        if entry.mu_band == BAND_NOT_A_WORK:
            what = _CLASS_LABELS.get(WorkClass[entry.mu_work_class]) if entry.mu_work_class else None
            return (f"Not matched automatically: this folder looks like {what or 'more than one work'}"
                    " - right-click > Fix MangaUpdates match... to link it anyway")
        return None
    parts = []
    if entry.mu_url:
        parts.append(entry.mu_url)
    if entry.mu_confirmed:
        parts.append("(confirmed - right-click or double-click to un-confirm)")
    elif is_legacy_score(entry):
        parts.append("(matched by an older Manga-List version - its score is not comparable; "
                     "use Check MU to re-match, or right-click / double-click to confirm)")
    else:
        pct = f"{entry.mu_score * 100:.0f}%"
        if entry.mu_band == BAND_REVIEW:
            why = reason_text(entry.mu_reasons)
            parts.append(f"(needs review, title score {pct}{': ' + why if why else ''}"
                         " - right-click to confirm or fix)")
        else:
            parts.append(f"(auto-matched, title score {pct} - right-click or double-click to confirm)")
    return "  ".join(parts)
