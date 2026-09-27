"""The work detector (port of MangaPixer 1.26.1 ``WorkDetector.cs``).

Classifies a folder from its shape alone - display names and counts, no IO - so only folders that
ARE one work are matched at folder level, collections are matched archive by archive (numbered
mini-series grouped), and anything unclear goes to review.

Order of the rules:

1. Depth 0 (the library root) is ``EXCLUDED``; a unit-named folder below a non-root parent
   (``Volumes``, ``Season 2``, ``Part 3``) is ``UNIT_SUB``.
2. Subfolders (empty ones ignored): two or more non-unit subfolders make a franchise container (at
   least half related to the parent's name, or ``Part N - subtitle`` children) or a collection
   container; exactly one makes a wrapper (no archives) or ``MIXED``; only unit subfolders make
   ``SERIES_WITH_UNITS``.
3. A leaf named like the creator its archives carry is an ``ARTIST_COLLECTION``.
4. One archive is a ``ONE_SHOT``.
5. The E6 discriminator on archive base titles: unit-named share >= 0.8, or one base title >= 80%,
   or >= 60% of the titled archives matching the folder -> ``SERIES``; distinct-base ratio >= 0.6
   with no base at 50% or more -> ``COLLECTION_LEAF``; otherwise ``AMBIGUOUS`` (review only).

Reasons never contain names (counts and shares only), so they are safe to log.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from . import anatomy as _anatomy
from . import auto_match_text as amt
from . import similarity
from ._text import format_fixed
from .contracts import (
    ArchiveGroup,
    ChildFolderShape,
    ContentSuggestion,
    FolderShape,
    MatchLevel,
    WorkClass,
    WorkClassification,
)
from .normalizer import (
    DerivedTitleKind,
    archive_base_title,
    derived_variants,
    normalize,
    numbered_series_head,
    scoring_form,
)

UNIT_NAMED_SHARE = 0.80
DOMINANT_BASE_SHARE = 0.80
FOLDER_MATCH_SHARE = 0.60
FOLDER_MATCH_SCORE = 0.80
ARCHIVE_LEVEL_DISTINCT_RATIO = 0.60
ARCHIVE_LEVEL_MAX_BASE_SHARE = 0.50
ARTIST_TAG_SHARE = 0.50
DOUJIN_SHARE = 0.50
FRANCHISE_RELATED_SCORE = 0.60


def classify(folder: FolderShape) -> WorkClassification:
    """Classify a folder from its shape. Pure and deterministic."""
    archives = tuple(folder.archive_names or ())
    subfolders = [s for s in (folder.subfolders or ()) if s.descendant_archive_count > 0]

    if folder.depth <= 0:
        return _result(WorkClass.EXCLUDED, MatchLevel.NONE, ["library root"])

    if folder.depth >= 2 and amt.is_unit_folder_name(folder.display_name):
        return _result(WorkClass.UNIT_SUB, MatchLevel.NONE, ["unit subfolder name below a non-root parent"])

    anatomies = [_anatomy.parse(a) for a in archives]
    content = _suggest_content(anatomies)
    unit_subs = [s for s in subfolders if amt.is_unit_folder_name(s.display_name)]
    work_subs = [s for s in subfolders if not amt.is_unit_folder_name(s.display_name)]

    if len(work_subs) >= 2:
        return _classify_container(folder, work_subs, archives, content)

    if len(work_subs) == 1:
        if not archives:
            return _result(WorkClass.WRAPPER, MatchLevel.NONE,
                           ["exactly one non-unit subfolder and no archives"], content=content)
        # Loose archives that are separate works (one-shots, or a whole series in one archive) are
        # matched one by one; loose units of one work keep the folder review-only.
        mixed_groups = _loose_work_groups(folder, archives, content)
        if mixed_groups:
            return _result(WorkClass.MIXED, MatchLevel.ARCHIVE,
                           [f"one non-unit subfolder plus {len(archives)} loose archives, matched one by one"],
                           mixed_groups, content)
        return _result(WorkClass.MIXED, MatchLevel.REVIEW_ONLY,
                       [f"one non-unit subfolder plus {len(archives)} loose archives"], content=content)

    if unit_subs:
        return _result(WorkClass.SERIES_WITH_UNITS, MatchLevel.FOLDER,
                       [f"{len(unit_subs)} unit subfolders, {len(archives)} loose archives"], content=content)

    if not archives:
        return _result(WorkClass.EXCLUDED, MatchLevel.NONE, ["no archives"])

    artist_reason = _artist_folder_reason(folder, anatomies)
    if artist_reason is not None:
        return _result(WorkClass.ARTIST_COLLECTION, MatchLevel.ARCHIVE, [artist_reason],
                       group_archives(archives), content)

    if len(archives) == 1:
        return _result(WorkClass.ONE_SHOT, MatchLevel.FOLDER, ["exactly one archive"], content=content)

    return _classify_leaf(folder, archives, content)


def _classify_container(folder: FolderShape, work_subs: List[ChildFolderShape], archives: Sequence[str],
                        content: ContentSuggestion) -> WorkClassification:
    parent = normalize(folder.display_name).primary

    def related(s: ChildFolderShape) -> bool:
        if amt.is_part_with_subtitle(s.display_name):
            return True
        if not parent:
            return False
        child = normalize(s.display_name).primary
        return amt.contains_tokens(child, parent) or similarity.score(child, parent) >= FRANCHISE_RELATED_SCORE

    n_related = sum(1 for s in work_subs if related(s))

    # The container itself is never matched (its subfolders are the candidates), but loose archives
    # that are separate works are matched one by one: level ARCHIVE + groups.
    groups = _loose_work_groups(folder, archives, content) if archives else ()
    level = MatchLevel.ARCHIVE if groups else MatchLevel.NONE
    if not archives:
        loose = ""
    elif groups:
        loose = f", {len(archives)} loose archives matched one by one"
    else:
        loose = f", {len(archives)} loose archives (units of one work - not matched)"
    if n_related * 2 >= len(work_subs):
        return _result(WorkClass.FRANCHISE_CONTAINER, level,
                       [f"{n_related} of {len(work_subs)} subfolders related to the folder name{loose}"],
                       groups, content)
    return _result(WorkClass.COLLECTION_CONTAINER, level,
                   [f"{len(work_subs)} unrelated subfolders{loose}"], groups, content)


def _loose_work_groups(folder: FolderShape, archives: Sequence[str],
                       content: ContentSuggestion) -> Tuple[ArchiveGroup, ...]:
    """Groups for archives lying loose next to subfolders, or none when they look like units of one
    work (``Vol 01``, ``Vol 02`` ...). A single titled archive is its own work."""
    if len(archives) == 1:
        return group_archives(archives)
    leaf = _classify_leaf(folder, archives, content)
    return () if leaf.cls == WorkClass.SERIES else group_archives(archives)


def _classify_leaf(folder: FolderShape, archives: Sequence[str], content: ContentSuggestion) -> WorkClassification:
    total = len(archives)
    bases = [archive_base_title(a) for a in archives]
    keys = [scoring_form(b) for b in bases]
    unit_named = sum(1 for k in keys if not k)
    titled = [k for k in keys if k]
    by_key = Counter(titled)
    max_base = max(by_key.values()) if by_key else 0
    distinct = len(by_key)

    folder_title = normalize(folder.display_name)
    folder_variants = list(folder_title.variants) + [d.text for d in folder_title.derived]
    folder_keys = {k for k in (scoring_form(v) for v in folder_variants) if k}
    matching = sum(1 for b in bases if b and (scoring_form(b) in folder_keys
                                              or similarity.best([b], folder_variants) >= FOLDER_MATCH_SCORE))

    unit_share = unit_named / total
    base_share = max_base / total
    match_share = 0 if not titled else matching / len(titled)
    distinct_ratio = distinct / total
    shares = (f"unit-named {format_fixed(unit_share, 2)}, top base {format_fixed(base_share, 2)}, "
              f"folder match {format_fixed(match_share, 2)}, distinct {format_fixed(distinct_ratio, 2)}")

    if unit_share >= UNIT_NAMED_SHARE or base_share >= DOMINANT_BASE_SHARE or match_share >= FOLDER_MATCH_SHARE:
        return _result(WorkClass.SERIES, MatchLevel.FOLDER, ["archives are units of one work: " + shares],
                       content=content)

    # "Title 025 Subtitle" chapters: the per-chapter subtitle makes every base different, but the
    # title in front of a varying number is shared (1.26.1).
    if numbered_series_head(archives, DOMINANT_BASE_SHARE) is not None:
        return _result(WorkClass.SERIES, MatchLevel.FOLDER,
                       ["archives are numbered chapters of one title (with chapter subtitles): " + shares],
                       content=content)

    # "Head - Subtitle" names sharing one head: a series with subtitled volumes or a creator folder
    # with unbracketed names - the shape cannot tell, so neither level is safe.
    head_texts = []
    for b in bases:
        if not b:
            continue
        first = next((d for d in derived_variants(b) if d.kind == DerivedTitleKind.SUBTITLE_SPLIT), None)
        if first is not None:
            head_texts.append(first.text)
    head_counts = Counter(scoring_form(h) for h in head_texts)
    heads = max(head_counts.values()) if head_counts else 0
    if titled and heads / len(titled) >= DOMINANT_BASE_SHARE:
        return _result(WorkClass.AMBIGUOUS, MatchLevel.REVIEW_ONLY,
                       ["archives share one title before a subtitle separator: " + shares], content=content)

    if distinct_ratio >= ARCHIVE_LEVEL_DISTINCT_RATIO and base_share < ARCHIVE_LEVEL_MAX_BASE_SHARE:
        return _result(WorkClass.COLLECTION_LEAF, MatchLevel.ARCHIVE, ["archives are different works: " + shares],
                       group_archives(archives), content)

    return _result(WorkClass.AMBIGUOUS, MatchLevel.REVIEW_ONLY, ["neither one work nor a collection: " + shares],
                   content=content)


def _artist_folder_reason(folder: FolderShape, anatomies: List[_anatomy.ArchiveNameAnatomy]) -> Optional[str]:
    """The reason when the folder name equals a caller-supplied provider author, or the creator tag
    most archives carry in at least half of them; None otherwise. An unbracketed ``Name - Title``
    prefix is deliberately NOT a creator tag (indistinguishable from ``Title - Chapter 001``)."""
    folder_name = normalize(folder.display_name).primary
    if not folder_name or amt.is_category_word(folder_name):
        return None

    known = folder.known_author_names
    if known and any(amt.is_author_like(a, require_two_tokens=False) and amt.names_equal(a, folder_name)
                     for a in known):
        return "folder name equals a provider author name"

    carrying = sum(1 for a in anatomies if any(amt.names_equal(t, folder_name) for t in a.creator_tags))
    if carrying > 0 and carrying / len(anatomies) >= ARTIST_TAG_SHARE:
        return f"folder name equals the creator tag of {carrying} of {len(anatomies)} archives"
    return None


def group_archives(archives: Sequence[str]) -> Tuple[ArchiveGroup, ...]:
    """Archive-level groups: archives sharing one base title (a numbered mini-series) form one group
    queried by that base; any other archive is its own group queried by its clean title. Unit-named
    archives with no title are left out (nothing to query)."""
    order: List[str] = []
    members: Dict[str, List[int]] = {}
    spelling: Dict[str, str] = {}
    for i, name in enumerate(archives):
        base = archive_base_title(name)
        key = scoring_form(base)
        if not key:
            continue
        if key not in members:
            members[key] = []
            order.append(key)
            spelling[key] = base
        elif base < spelling[key]:
            spelling[key] = base
        members[key].append(i)

    groups = []
    for key in order:
        idx = members[key]
        query = spelling[key]
        if len(idx) == 1:
            primary = normalize(archives[idx[0]]).primary
            if primary:
                query = primary
        groups.append(ArchiveGroup(query, tuple(idx)))
    return tuple(groups)


def _suggest_content(anatomies: List[_anatomy.ArchiveNameAnatomy]) -> ContentSuggestion:
    if not anatomies:
        return ContentSuggestion.NONE
    doujin = sum(1 for a in anatomies if a.is_doujin_shaped)
    if doujin > 0 and doujin / len(anatomies) >= DOUJIN_SHARE:
        return ContentSuggestion.DOUJINSHI_AND_ADULT_ONE_SHOTS
    return ContentSuggestion.NONE


def _result(cls: WorkClass, level: MatchLevel, reasons: List[str], groups: Sequence[ArchiveGroup] = (),
            content: ContentSuggestion = ContentSuggestion.NONE) -> WorkClassification:
    return WorkClassification(cls, level, tuple(reasons), tuple(groups), content)
