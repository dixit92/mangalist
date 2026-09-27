"""Query planning (port of MangaPixer 1.26.0 ``MatchQueryPlanner.cs``).

Builds the provider queries for one work: ordered, de-duplicated query variants plus the local
corroboration context. Pure; the variants come from display names only and nothing here is sent
anywhere - the caller decides how many variants it sends.

Variant order (= ``QueryVariantKind`` order): ComicInfo series, folder primary, trailing
``[English Title]``, subtitle split, sequel-number split, archive-derived title, and for doujin-shaped
archives the MangaUpdates ``<parody> dj - <title>`` form. Variants are de-duplicated by their scoring
form (a variant that differs only in case or punctuation is one query).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import anatomy as _anatomy
from . import auto_match_text as amt
from .contracts import (
    ArchiveGroup,
    FolderShape,
    MatchContext,
    MatchQuery,
    QueryVariant,
    QueryVariantKind,
    WorkClass,
    WorkClassification,
)
from .normalizer import DerivedTitleKind, NormalizedTitle, archive_title, normalize, scoring_form

MAX_VARIANTS = 8


class _VariantList:
    """Variants in kind order, de-duplicated by scoring form, capped."""

    def __init__(self) -> None:
        self._items: List[QueryVariant] = []
        self._keys: set = set()

    def add(self, text: Optional[str], kind: QueryVariantKind) -> None:
        t = text.strip() if text is not None else None
        if not t:
            return
        key = scoring_form(t)
        if not key:
            return
        # A trailing "!" changes MangaUpdates' results although it scores the same: keep both.
        dedupe_key = key + "!" if t.endswith("!") else key
        if dedupe_key in self._keys:
            return
        self._keys.add(dedupe_key)
        self._items.append(QueryVariant(t, kind))

    def to_tuple(self) -> Tuple[QueryVariant, ...]:
        ordered = sorted(enumerate(self._items), key=lambda iv: (int(iv[1].kind), iv[0]))
        return tuple(v for _, v in ordered[:MAX_VARIANTS])


def plan_folder(folder: FolderShape, classification: WorkClassification,
                comic_info_series: Optional[str] = None) -> MatchQuery:
    """The query for a folder-level work."""
    archives = tuple(folder.archive_names or ())
    variants = _VariantList()

    ci = normalize(comic_info_series)
    if ci.primary:
        variants.add(ci.primary, QueryVariantKind.COMIC_INFO_SERIES)

    name = normalize(folder.display_name)
    _add_name_variants(variants, name)

    derived_title = archive_title(archives)
    if derived_title is not None:
        variants.add(derived_title, QueryVariantKind.ARCHIVE_DERIVED_TITLE)
    elif len(archives) == 1:
        single = normalize(archives[0]).primary
        if single:
            variants.add(single, QueryVariantKind.ARCHIVE_DERIVED_TITLE)

    anatomies = [_anatomy.parse(a) for a in archives]
    author_tags = _dominant_creator_tags(anatomies)
    if classification.cls == WorkClass.ARTIST_COLLECTION and name.primary:
        _add_distinct(author_tags, name.primary)
    parent = folder.parent_display_name
    if parent is not None and amt.is_author_like(parent, require_two_tokens=True):
        _add_distinct(author_tags, normalize(parent).primary)

    volume_like = sum(1 for a in archives if amt.is_volume_like(a))
    chapter_like = sum(1 for a in archives if amt.is_chapter_like(a))
    archive_count = len(archives)
    for sub in folder.subfolders or ():
        if sub.descendant_archive_count <= 0 or not amt.is_unit_folder_name(sub.display_name):
            continue
        archive_count += sub.descendant_archive_count
        if amt.is_volume_folder_name(sub.display_name):
            volume_like += sub.descendant_archive_count
        elif amt.is_chapter_folder_name(sub.display_name):
            chapter_like += sub.descendant_archive_count

    years = []
    archive_year = amt.earliest_year(archives)
    if archive_year is not None:
        years.append(archive_year)
    if name.year_hint is not None:
        years.append(name.year_hint)

    context = MatchContext(
        cls=classification.cls,
        archive_count=archive_count,
        volume_like_count=volume_like,
        chapter_like_count=chapter_like,
        earliest_year=min(years) if years else None,
        category_hint=folder.category_hint,
        tall_strips=False,
        author_tags=tuple(author_tags),
        comic_info_series=comic_info_series.strip() if comic_info_series and comic_info_series.strip() else None,
    )
    return MatchQuery(variants.to_tuple(), context)


def plan_archive_group(folder: FolderShape, classification: WorkClassification, group: ArchiveGroup) -> MatchQuery:
    """The query for an archive group of a collection folder."""
    all_names = tuple(folder.archive_names or ())
    names = [all_names[i] for i in group.archive_indexes if 0 <= i < len(all_names)]
    anatomies = [_anatomy.parse(n) for n in names]
    variants = _VariantList()

    # The group's query title first (a mini-series base, or the one archive's clean title), then what
    # the archive names add: an [English Title], splits, a common clean title.
    group_title = normalize(group.query_title)
    if group_title.primary:
        variants.add(group_title.primary, QueryVariantKind.PRIMARY)
    variants.add(group_title.primary_with_exclamation, QueryVariantKind.PRIMARY)
    for n in (normalize(x) for x in names):
        if len(n.variants) > 1:
            variants.add(n.variants[1], QueryVariantKind.ENGLISH_TITLE)
    for d in group_title.derived:
        variants.add(d.text, QueryVariantKind.SUBTITLE_SPLIT if d.kind == DerivedTitleKind.SUBTITLE_SPLIT
                     else QueryVariantKind.SEQUEL_NUMBER_SPLIT)
    derived_title = archive_title(names)
    if derived_title is not None:
        variants.add(derived_title, QueryVariantKind.ARCHIVE_DERIVED_TITLE)

    for a in anatomies:
        if a.is_doujin_shaped and a.parody is not None and a.title:
            title = group_title.primary if len(names) > 1 and group_title.primary else a.title
            variants.add(f"{a.parody} dj - {title}", QueryVariantKind.DOUJIN_PARODY_FORM)
            break

    author_tags: List[str] = []
    for a in anatomies:
        for tag in a.creator_tags:
            if amt.is_author_like(tag, require_two_tokens=False):
                _add_distinct(author_tags, tag)
    if classification.cls == WorkClass.ARTIST_COLLECTION:
        artist = normalize(folder.display_name).primary
        if artist:
            _add_distinct(author_tags, artist)

    # A group of loose archives in a container or mixed folder is a work inside a collection: score
    # it as one, so it may auto-link and the author veto applies.
    work_class = (classification.cls if classification.cls in (WorkClass.COLLECTION_LEAF, WorkClass.ARTIST_COLLECTION)
                  else WorkClass.COLLECTION_LEAF)
    context = MatchContext(
        cls=work_class,
        archive_count=len(names),
        volume_like_count=sum(1 for n in names if amt.is_volume_like(n)),
        chapter_like_count=sum(1 for n in names if amt.is_chapter_like(n)),
        earliest_year=amt.earliest_year(names),
        category_hint=folder.category_hint,
        tall_strips=False,
        author_tags=tuple(author_tags),
    )
    return MatchQuery(variants.to_tuple(), context)


def _add_name_variants(variants: _VariantList, name: NormalizedTitle) -> None:
    if name.primary:
        variants.add(name.primary, QueryVariantKind.PRIMARY)
    # Second search text, only sent when the first finds nothing confident (the loop stops at 0.85).
    variants.add(name.primary_with_exclamation, QueryVariantKind.PRIMARY)
    if len(name.variants) > 1:
        variants.add(name.variants[1], QueryVariantKind.ENGLISH_TITLE)
    for d in name.derived:
        if d.kind == DerivedTitleKind.SUBTITLE_SPLIT:
            variants.add(d.text, QueryVariantKind.SUBTITLE_SPLIT)
    for d in name.derived:
        if d.kind == DerivedTitleKind.SEQUEL_NUMBER_SPLIT:
            variants.add(d.text, QueryVariantKind.SEQUEL_NUMBER_SPLIT)


def _dominant_creator_tags(anatomies: List[_anatomy.ArchiveNameAnatomy]) -> List[str]:
    """Creator tags carried by at least half of the archives (folder level: a tie-break only)."""
    result: List[str] = []
    if not anatomies:
        return result
    counts: Dict[str, List] = {}
    for a in anatomies:
        for tag in a.creator_tags:
            if not amt.is_author_like(tag, require_two_tokens=False):
                continue
            key = scoring_form(tag)
            if key in counts:
                counts[key][1] += 1
            else:
                counts[key] = [tag, 1]
    for key in sorted(counts):
        spelling, count = counts[key]
        if count * 2 >= len(anatomies):
            result.append(spelling)
    return result


def _add_distinct(values: List[str], value: str) -> None:
    if value and not any(amt.names_equal(v, value) for v in values):
        values.append(value)
