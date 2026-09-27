"""Candidate scoring and banding (port of MangaPixer 1.26.1 ``MatchScorer.cs``). Pure, deterministic.

- **Title**: :func:`similarity.score` over every (variant, record title / alt title) pair - a title's
  trailing ``(disambiguator)`` also counts stripped - minus :data:`NUMBER_PENALTY` when the pair
  disagrees on a sequel / part number. Retrieval-only variants (subtitle and sequel-number splits)
  are compared with the numbers of the name they came from and carry a small
  :data:`DERIVED_VARIANT_DISCOUNT`, so a full-name match always wins a tie.
- **Corroboration** re-ranks only: agreements and conflict penalties change the ADJUSTED score
  (ordering and margin), never the raw title score the auto threshold reads. Format, origin vs the
  category folder, tall strips, counts (volumes vs volumes, chapters vs chapters - never chapters vs
  volumes), earliest file year vs start year, one-shot shape, ComicInfo series, creator tags.
- **Vetoes** demote auto to review: any corroboration conflict, a related top pair the number-aware
  title does not separate, and - mandatory for archive-level works - an author conflict.
- **Bands**: auto = raw title >= auto_title, adjusted lead >= margin over the next distinct record,
  no veto and an auto-capable class; a ``ONE_SHOT`` folder additionally needs raw >= 0.95 (a code
  constant). A single archive may hold a one-shot, one volume or a whole series, so the record's
  volume count never blocks auto. Review = raw >= review_floor.
- **to_persist**: candidates within 0.15 of the top, at most 5; only the top when it leads by >= 0.30.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from . import auto_match_text as amt
from . import similarity
from ._text import any_letter, contains_ignore_case, is_null_or_whitespace
from .contracts import (
    DEFAULT_THRESHOLDS,
    MatchBand,
    MatchCandidate,
    MatchContext,
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
)
from .normalizer import number_tokens, scoring_form

NUMBER_PENALTY = 0.15
DERIVED_VARIANT_DISCOUNT = 0.03
ONE_SHOT_AUTO_TITLE = 0.95

FORMAT_CONFLICT = -0.30
CONFLICT = -0.10
ORIGIN_AGREE = 0.02
COUNT_AGREE = 0.01
YEAR_AGREE = 0.01
ONE_SHOT_AGREE = 0.02
COMIC_INFO_AGREE = 0.05
AUTHOR_AGREE = 0.05
# A creator hint from the name names the record's author or its "(AUTHOR Name)" disambiguator
# (1.26.1): enough to separate same-titled records. Replaces AUTHOR_AGREE when both apply.
CREATOR_HINT_AGREE = 0.10
# Title score of a record whose title up to its colon EQUALS the searched name ("Title" vs
# "Title: Long Subtitle", 1.26.1): below the auto threshold, so it ranks for review only.
SUBTITLE_HEAD_CAP = 0.80

RELATED_SEPARATION = 0.10

# A count conflict: local units > COUNT_FACTOR x published + COUNT_SLACK.
COUNT_FACTOR = 1.5
COUNT_SLACK = 2

PERSIST_WINDOW = 0.15
PERSIST_MAX = 5
PERSIST_CLEAR_LEAD = 0.30

VETO_REASONS = (MatchReason.COUNT_CONFLICT | MatchReason.YEAR_CONFLICT | MatchReason.TYPE_CONFLICT
                | MatchReason.RELATED_PAIR | MatchReason.AUTHOR_CONFLICT)


def score(query: MatchQuery, candidates: Sequence[MatchCandidate],
          thresholds: MatchThresholds = DEFAULT_THRESHOLDS) -> MatchOutcome:
    """Score provider candidates for one query and band the result."""
    if not thresholds.is_valid:
        raise ValueError("Match thresholds are outside their bounds.")

    distinct = _distinct(candidates)
    if not distinct:
        return MatchOutcome(MatchBand.UNMATCHED, (), ())

    ctx = query.context
    variants = _prepare_variants(query.variants)
    scored = [_score_one(c, variants, ctx) for c in distinct]

    # Stable sort, like LINQ OrderByDescending(...).ThenByDescending(...).ThenBy(...).ThenBy(...).
    ranked = sorted(scored, key=lambda s: (-s.adjusted_score, -s.title_score,
                                           s.candidate.provider, s.candidate.external_id))

    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    reasons = top.reasons

    if (second is not None and _are_related(top.candidate, second.candidate)
            and top.title_score - second.title_score < RELATED_SEPARATION):
        reasons |= MatchReason.RELATED_PAIR

    margin = top.adjusted_score - (second.adjusted_score if second is not None else 0)
    if margin < thresholds.margin:
        reasons |= MatchReason.CLOSE_SECOND

    auto_class = is_auto_capable(ctx.cls)
    if not auto_class:
        reasons |= MatchReason.REVIEW_ONLY_CLASS

    one_shot_ok = ctx.cls != WorkClass.ONE_SHOT or top.title_score >= ONE_SHOT_AUTO_TITLE

    if (auto_class and one_shot_ok
            and top.title_score >= thresholds.auto_title
            and margin >= thresholds.margin
            and not (reasons & VETO_REASONS)):
        band = MatchBand.AUTO
    elif top.title_score >= thresholds.review_floor:
        band = MatchBand.NEEDS_REVIEW
    else:
        band = MatchBand.UNMATCHED

    ranked[0] = replace(top, reasons=reasons)
    persisted = () if band == MatchBand.UNMATCHED else _choose_persisted(ranked)
    return MatchOutcome(band, tuple(ranked), persisted)


def is_auto_capable(cls: WorkClass) -> bool:
    """Classes whose works may be auto-linked (folder-level series and archive-level collections)."""
    return cls in (WorkClass.SERIES, WorkClass.SERIES_WITH_UNITS, WorkClass.ONE_SHOT,
                   WorkClass.COLLECTION_LEAF, WorkClass.ARTIST_COLLECTION)


def is_archive_level(cls: WorkClass) -> bool:
    """Archive-level classes: the author-conflict veto applies."""
    return cls in (WorkClass.COLLECTION_LEAF, WorkClass.ARTIST_COLLECTION)


@dataclass(frozen=True)
class _PreparedVariant:
    text: str
    numbers: Tuple[str, ...]
    derived: bool


def _is_derived(kind: QueryVariantKind) -> bool:
    return kind in (QueryVariantKind.SUBTITLE_SPLIT, QueryVariantKind.SEQUEL_NUMBER_SPLIT)


def _prepare_variants(variants: Sequence[QueryVariant]) -> List[_PreparedVariant]:
    # Derived variants compare numbers with the name they came from: the primary, else the first
    # full variant.
    source = next((v for v in variants if v.kind == QueryVariantKind.PRIMARY), None)
    if source is None:
        source = next((v for v in variants if not _is_derived(v.kind)), None)
    source_numbers = number_tokens(source.text if source is not None else None)
    result = []
    for v in variants:
        if is_null_or_whitespace(v.text):
            continue
        if _is_derived(v.kind):
            result.append(_PreparedVariant(v.text, source_numbers, True))
        else:
            result.append(_PreparedVariant(v.text, number_tokens(v.text), False))
    return result


def _score_one(c: MatchCandidate, variants: List[_PreparedVariant], ctx: MatchContext) -> ScoredCandidate:
    reasons = MatchReason.NONE
    titles = [t for t in [c.title, *(c.alt_titles or ())] if not is_null_or_whitespace(t)]
    stripped_titles = [s for s in (amt.without_disambiguator(t) for t in titles) if s is not None]
    for stripped in stripped_titles:
        if not contains_ignore_case(titles, stripped):
            titles.append(stripped)
    title_numbers = [number_tokens(t) for t in titles]
    # "Title: Long Subtitle" records also compare by the part before the colon, capped (1.26.1).
    heads: List[str] = []
    for t in titles:
        i = t.find(":")
        head = t[:i].strip() if i > 0 else None
        if head is not None and any_letter(head) and not contains_ignore_case(titles, head) \
                and not contains_ignore_case(heads, head):
            heads.append(head)
    capped = len(titles)
    titles.extend(heads)
    title_numbers.extend(number_tokens(h) for h in heads)

    best = 0.0
    best_penalized = False
    for v in variants:
        for i, t in enumerate(titles):
            if i >= capped:
                # Only a head EQUAL to the searched name counts, never a merely similar one - that is
                # how spin-offs ("Title: Side Story") look.
                if scoring_form(v.text) != scoring_form(t):
                    continue
                raw = SUBTITLE_HEAD_CAP
            else:
                raw = similarity.score(v.text, t)
            if raw <= 0:
                continue
            penalized = v.numbers != title_numbers[i]
            s = raw - (NUMBER_PENALTY if penalized else 0) - (DERIVED_VARIANT_DISCOUNT if v.derived else 0)
            if s > best:
                best = s
                best_penalized = penalized
    title = min(max(best, 0.0), 1.0)
    if best_penalized:
        reasons |= MatchReason.NUMBER_MISMATCH

    delta = 0.0

    # Format: the automatic search filters these out; a lifted filter may still return them.
    if c.format in (MetadataFormat.NOVEL, MetadataFormat.ARTBOOK, MetadataFormat.AUDIO):
        delta += FORMAT_CONFLICT
        reasons |= MatchReason.TYPE_CONFLICT

    # Origin vs the category folder, and tall strips vs a print record.
    origin = amt.parse_origin(c.origin)
    allowed = amt.origins_for_category(ctx.category_hint)
    if origin is not None and allowed is not None:
        if origin in allowed or (c.webtoon is True and MetadataOrigin.Korea in allowed):
            delta += ORIGIN_AGREE
        else:
            delta += CONFLICT
            reasons |= MatchReason.TYPE_CONFLICT
    if ctx.tall_strips:
        if c.webtoon is True or origin in (MetadataOrigin.Korea, MetadataOrigin.ChinaTaiwan):
            delta += ORIGIN_AGREE
        elif c.webtoon is False and origin == MetadataOrigin.Japan:
            delta += CONFLICT
            reasons |= MatchReason.TYPE_CONFLICT

    # Counts: volumes vs volumes, chapters vs chapters; unknown -> no signal. The latest chapter
    # restarts per season on renumbered webtoons, so a stated total wins.
    if ctx.volume_like_count > 0 and c.volumes is not None and c.volumes > 0:
        if ctx.volume_like_count > COUNT_FACTOR * c.volumes + COUNT_SLACK:
            delta += CONFLICT
            reasons |= MatchReason.COUNT_CONFLICT
        else:
            delta += COUNT_AGREE
    chapters = max(c.latest_chapter or 0, c.total_chapters or 0)
    if ctx.chapter_like_count > 0 and chapters > 0:
        if ctx.chapter_like_count > COUNT_FACTOR * chapters + COUNT_SLACK:
            delta += CONFLICT
            reasons |= MatchReason.COUNT_CONFLICT
        else:
            delta += COUNT_AGREE

    # Year: a file cannot predate the series (English release years bound it from above).
    if ctx.earliest_year is not None and c.start_year is not None:
        if ctx.earliest_year < c.start_year - 1:
            delta += CONFLICT
            reasons |= MatchReason.YEAR_CONFLICT
        else:
            delta += YEAR_AGREE

    # One-shot shape: a one-shot record gets a small tie-break; a multi-volume record is NOT a
    # conflict (one archive can hold a whole series).
    if ((ctx.cls == WorkClass.ONE_SHOT
         or (is_archive_level(ctx.cls) and ctx.archive_count == 1
             and ctx.volume_like_count == 0 and ctx.chapter_like_count == 0))
            and _is_one_shot_record(c)):
        delta += ONE_SHOT_AGREE

    # ComicInfo series names the record.
    if not is_null_or_whitespace(ctx.comic_info_series):
        key = scoring_form(ctx.comic_info_series)
        if key and any(scoring_form(t) == key for t in titles):
            delta += COMIC_INFO_AGREE

    # Creator tags: a tie-break everywhere; a veto at archive level when they name none of the
    # record's authors (undecidable when either side is empty).
    tags = [t for t in (ctx.author_tags or ()) if not is_null_or_whitespace(t)]
    authors = [a for a in (c.authors or ()) if not is_null_or_whitespace(a)]
    author_bonus = 0.0
    if tags and authors:
        if any(amt.names_equal(t, a) for t in tags for a in authors):
            author_bonus = AUTHOR_AGREE
        elif is_archive_level(ctx.cls):
            reasons |= MatchReason.AUTHOR_CONFLICT

    # Creator hints from the name: positive only. The record's authors come from a full read; its
    # "(AUTHOR Name)" disambiguator is on every search hit, so ties are broken without a read.
    hints = [h for h in (ctx.creator_hints or ()) if not is_null_or_whitespace(h)]
    if hints:
        named = authors + [d for d in (amt.disambiguator_tag(t) for t in (c.title, *(c.alt_titles or ())))
                           if d is not None]
        if any(amt.names_equal(h, n) for h in hints for n in named):
            author_bonus = CREATOR_HINT_AGREE
    delta += author_bonus

    return ScoredCandidate(c, title, title + delta, reasons)


def _is_one_shot_record(c: MatchCandidate) -> bool:
    return c.volumes == 1 or (c.volumes is None and c.latest_chapter == 1)


def _are_related(a: MatchCandidate, b: MatchCandidate) -> bool:
    return (a.provider == b.provider
            and (any(r.external_id == b.external_id for r in (a.relations or ()))
                 or any(r.external_id == a.external_id for r in (b.relations or ()))))


def _choose_persisted(ranked: List[ScoredCandidate]) -> Tuple[ScoredCandidate, ...]:
    top = ranked[0]
    if len(ranked) == 1 or top.adjusted_score - ranked[1].adjusted_score >= PERSIST_CLEAR_LEAD:
        return (top,)
    result = []
    for s in ranked:
        if not top.adjusted_score - s.adjusted_score <= PERSIST_WINDOW + 1e-9:
            break
        result.append(s)
        if len(result) == PERSIST_MAX:
            break
    return tuple(result)


def _distinct(candidates: Sequence[Optional[MatchCandidate]]) -> List[MatchCandidate]:
    """One entry per (provider, external id); a duplicate keeps the entry with more data."""
    by_key: Dict[Tuple[str, str], MatchCandidate] = {}
    order: List[Tuple[str, str]] = []
    for c in candidates:
        if c is None or is_null_or_whitespace(c.external_id) or is_null_or_whitespace(c.title):
            continue
        key = (c.provider or "", c.external_id)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = c
            order.append(key)
        elif _richness(c) > _richness(existing):
            by_key[key] = c
    return [by_key[k] for k in order]


def _richness(c: MatchCandidate) -> int:
    return (len(c.alt_titles or ()) + len(c.authors or ()) + len(c.relations or ())
            + (0 if c.volumes is None else 1) + (0 if c.latest_chapter is None else 1)
            + (0 if c.start_year is None else 1))
