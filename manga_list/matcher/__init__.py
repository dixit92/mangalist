"""Stage-2 matcher: a Python port of MangaPixer 1.26.1's metadata auto-match core.

Pure (no Qt, no IO): the work detector decides what a folder is, the planner builds the query
variants and local evidence, the scorer ranks provider candidates and bands the result
(auto / needs review / unmatched), and the retrieval loop drives injected search / get calls.
Thresholds, vetoes and reasons are identical to the reference; the golden set in
``tests/golden`` checks that the bands and ids match MangaPixer's.
"""

from .contracts import (
    DEFAULT_THRESHOLDS,
    ArchiveGroup,
    CandidateRelation,
    ChildFolderShape,
    ContentSuggestion,
    FolderShape,
    MatchBand,
    MatchCandidate,
    MatchContext,
    MatchLevel,
    MatchOutcome,
    MatchQuery,
    MatchReason,
    MatchThresholds,
    MetadataFormat,
    MetadataOrigin,
    QueryVariant,
    QueryVariantKind,
    ScoredCandidate,
    WorkClass,
    WorkClassification,
    reason_names,
    reasons_text,
)

__all__ = [
    "DEFAULT_THRESHOLDS", "ArchiveGroup", "CandidateRelation", "ChildFolderShape", "ContentSuggestion",
    "FolderShape", "MatchBand", "MatchCandidate", "MatchContext", "MatchLevel", "MatchOutcome", "MatchQuery",
    "MatchReason", "MatchThresholds", "MetadataFormat", "MetadataOrigin", "QueryVariant", "QueryVariantKind",
    "ScoredCandidate", "WorkClass", "WorkClassification", "reason_names", "reasons_text",
]
