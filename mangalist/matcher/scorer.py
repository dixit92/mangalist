"""Candidate scoring and banding (port of MangaPixer 1.31.1 ``MatchScorer.cs``). Pure, deterministic.

- **Title**: :func:`similarity.score` over every (variant, record title / alt title) pair - the MAIN title's
  trailing ``(disambiguator)`` also counts stripped - minus :data:`NUMBER_PENALTY` when the pair disagrees
  on a sequel / part number. Retrieval-only variants (subtitle, sequel-number and creator splits) are
  compared with the numbers of the name they came from and carry a small :data:`DERIVED_VARIANT_DISCOUNT`,
  so a full-name match always wins a tie. A record title's head before its subtitle break, the leading
  part of a long title, and a creator split without a named author score at most
  :data:`SUBTITLE_HEAD_CAP` (review only).
- **Corroboration** re-ranks only: agreements and conflict penalties change the ADJUSTED score (ordering
  and margin), never the raw title score the auto threshold reads. Format, origin vs the category folder
  (agreement only, 1.27.0), tall strips (agreement only), counts (:mod:`count_evidence`), earliest file year
  vs start year, one-shot shape, ComicInfo series, creator tags and creator hints.
- **Vetoes** demote auto to review: any corroboration conflict, a related top pair the number-aware title
  does not separate, a top that only the folder's subtitle separates from a record of its own series family
  (1.30.0, ``SUBTITLE_FAMILY``), and - mandatory for archive-level works - an author conflict.
- **Bands**: auto = raw title >= auto_title, adjusted lead >= margin over the next distinct record, no veto
  and an auto-capable class; a ``ONE_SHOT`` folder additionally needs raw >= 0.95 (a code constant). Review
  = raw >= review_floor.
- **to_persist**: candidates within 0.15 of the top, at most 5; only the top when it leads by >= 0.30 -
  plus, always, a record of the top's series family that only the folder's subtitle set apart.

Not ported (MangaPixer-only inputs): the cover tie-break (``CoverMatches``, +0.05) and the admin-declared
type (``DeclaredType``, +/-0.05). MangaList has no cover images and no declared facts, so for its inputs
the scorer is the reference's.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from . import auto_match_text as amt
from . import count_evidence
from . import series_families
from . import similarity
from ._text import contains_ignore_case, distinct_ignore_case, is_null_or_whitespace
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
from .normalizer import contains_number, name_subtitle, number_tokens, scoring_form, subtitle_head, subtitle_tail

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
# A creator hint from the name names the record's author or its "(AUTHOR Name)" disambiguator (1.26.1):
# enough to separate same-titled records. Replaces AUTHOR_AGREE when both apply.
CREATOR_HINT_AGREE = 0.10
# The title score of a record whose title, up to its subtitle break, EQUALS the searched name ("Title" vs
# "Title: Long Subtitle", 1.26.1; "Title ~Subtitle~" and "Title - Subtitle", 1.27.0), or that starts, word
# for word, with a searched name of at least LEADING_PART_MIN_WORDS words (1.27.0): below the lowest
# allowed auto threshold, so such a match only ranks the record for review.
SUBTITLE_HEAD_CAP = 0.80
LEADING_PART_MIN_WORDS = 3
# A title pair whose only shared tokens are numbers keeps this share of its similarity (1.27.0).
DIGIT_ONLY_OVERLAP_FACTOR = 0.5

RELATED_SEPARATION = 0.10

# A count conflict: local unit number > COUNT_FACTOR x published + COUNT_SLACK (count_evidence).
COUNT_FACTOR = count_evidence.FACTOR
COUNT_SLACK = count_evidence.SLACK

# Rounding tolerance of score comparisons against a threshold.
_SCORE_TOLERANCE = 1e-9

PERSIST_WINDOW = 0.15
PERSIST_MAX = 5
PERSIST_CLEAR_LEAD = 0.30

VETO_REASONS = (MatchReason.COUNT_CONFLICT | MatchReason.YEAR_CONFLICT | MatchReason.TYPE_CONFLICT
                | MatchReason.RELATED_PAIR | MatchReason.AUTHOR_CONFLICT | MatchReason.SUBTITLE_FAMILY)


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
    scored, capped = _subtitle_outweighs_head([_score_one(c, variants, ctx) for c in distinct], variants, thresholds)

    # Stable sort, like LINQ OrderByDescending(...).ThenByDescending(...).ThenBy(...).ThenBy(...).
    ranked = sorted(scored, key=lambda s: (-s.adjusted_score, -s.title_score,
                                           s.candidate.provider, s.candidate.external_id))

    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    reasons = top.reasons

    if (second is not None and _are_related(top.candidate, second.candidate)
            and top.title_score - second.title_score < RELATED_SEPARATION):
        reasons |= MatchReason.RELATED_PAIR

    # 1.30.0 ("not automatic - keep it in review"): the subtitle cap decided between the top and a record of
    # its own series family that matched the name's head and would otherwise have been a close second. The
    # rival is kept with the review candidates even outside the persist window.
    subtitle_rivals = [] if not capped else [
        s for s in ranked[1:]
        if s.candidate.external_id in capped
        and capped[s.candidate.external_id] >= top.title_score - RELATED_SEPARATION - _SCORE_TOLERANCE
        and series_families.are_family(top.candidate, s.candidate)]
    if subtitle_rivals:
        reasons |= MatchReason.SUBTITLE_FAMILY

    # 1.30.0: another candidate worth reviewing is the top's series family - a chip, never a veto.
    if any(s.title_score >= thresholds.review_floor - _SCORE_TOLERANCE
           and series_families.are_family(top.candidate, s.candidate) for s in ranked[1:]):
        reasons |= MatchReason.SERIES_FAMILY

    # "Close second" only means something for a top that could be reviewed (1.27.0). Compared with a
    # rounding tolerance (1.30.0).
    margin = top.adjusted_score - (second.adjusted_score if second is not None else 0)
    leads = margin >= thresholds.margin - _SCORE_TOLERANCE
    if not leads and top.title_score >= thresholds.review_floor:
        reasons |= MatchReason.CLOSE_SECOND

    auto_class = is_auto_capable(ctx.cls)
    if not auto_class:
        reasons |= MatchReason.REVIEW_ONLY_CLASS

    one_shot_ok = ctx.cls != WorkClass.ONE_SHOT or top.title_score >= ONE_SHOT_AUTO_TITLE

    if (auto_class and one_shot_ok
            and top.title_score >= thresholds.auto_title
            and leads
            and not (reasons & VETO_REASONS)):
        band = MatchBand.AUTO
    elif top.title_score >= thresholds.review_floor:
        band = MatchBand.NEEDS_REVIEW
    else:
        band = MatchBand.UNMATCHED

    ranked[0] = replace(top, reasons=reasons)
    persisted = () if band == MatchBand.UNMATCHED else _choose_persisted(ranked, subtitle_rivals)
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
    number_source: str
    kind: QueryVariantKind
    form: str = field(init=False)
    words: int = field(init=False)

    def __post_init__(self) -> None:
        f = scoring_form(self.text)
        object.__setattr__(self, "form", f)
        object.__setattr__(self, "words", len([w for w in f.split(" ") if w]))


def _is_derived(kind: QueryVariantKind) -> bool:
    return kind in (QueryVariantKind.SUBTITLE_SPLIT, QueryVariantKind.SEQUEL_NUMBER_SPLIT,
                    QueryVariantKind.CREATOR_SPLIT)


def _is_own_name(kind: QueryVariantKind) -> bool:
    """Variants that are the folder's (or group's) own name, not a bracket or a split."""
    return kind in (QueryVariantKind.PRIMARY, QueryVariantKind.COMIC_INFO_SERIES,
                    QueryVariantKind.ARCHIVE_DERIVED_TITLE)


def _is_leading_part(v: _PreparedVariant, title_form: str) -> bool:
    return (v.words >= LEADING_PART_MIN_WORDS and len(title_form) > len(v.form)
            and title_form.startswith(v.form + " "))


def _numbers_agree(v: _PreparedVariant, title: str, title_numbers: Tuple[str, ...]) -> bool:
    """The pair agrees on sequel / part numbers: the same numbers, or every number only one side reads as a
    sequel number still stands in the other side's text (1.27.0: ``Title Level 99`` vs a record
    ``Title Level 99 ~Subtitle~`` is the same work, not a sequel mismatch)."""
    if v.numbers == title_numbers:
        return True
    return (all(contains_number(title, n) for n in set(v.numbers) - set(title_numbers))
            and all(contains_number(v.number_source, n) for n in set(title_numbers) - set(v.numbers)))


def _prepare_variants(variants: Sequence[QueryVariant]) -> List[_PreparedVariant]:
    # Derived variants compare numbers with the name they came from: the primary, else the first full variant.
    source = next((v for v in variants if v.kind == QueryVariantKind.PRIMARY), None)
    if source is None:
        source = next((v for v in variants if not _is_derived(v.kind)), None)
    source_numbers = number_tokens(source.text if source is not None else None)
    result = []
    for v in variants:
        if is_null_or_whitespace(v.text):
            continue
        if _is_derived(v.kind):
            result.append(_PreparedVariant(v.text, source_numbers, True,
                                           source.text if source is not None else v.text, v.kind))
        else:
            result.append(_PreparedVariant(v.text, number_tokens(v.text), False, v.text, v.kind))
    return result


def _subtitle_outweighs_head(scored: List[Tuple[ScoredCandidate, bool]], variants: List[_PreparedVariant],
                             thresholds: MatchThresholds) -> Tuple[List[ScoredCandidate], Dict[str, float]]:
    """1.30.0: when the work's own name states a subtitle (``Series - Subtitle``) and a candidate at the
    review floor has that subtitle as its own (``Series: Subtitle``), a record that matched only the bare
    head (``Series``, through the retrieval-only subtitle split) is capped at :data:`SUBTITLE_HEAD_CAP`.
    Also returns the capped records with their title score before the cap."""
    subtitles = set()
    for v in variants:
        if _is_own_name(v.kind) or v.kind == QueryVariantKind.ENGLISH_TITLE:
            sub = name_subtitle(v.text)
            if sub is not None and scoring_form(sub):
                subtitles.add(scoring_form(sub))
    capped: Dict[str, float] = {}
    if not subtitles:
        return [s for s, _ in scored], capped

    def carries(c: MatchCandidate) -> bool:
        for t in (c.title, *(c.alt_titles or ())):
            stripped = amt.without_disambiguator(t)
            tail = subtitle_tail(stripped if stripped is not None else t)
            if tail is not None and scoring_form(tail) in subtitles:
                return True
        return False

    carriers = {s.candidate.external_id for s, _ in scored
                if s.title_score >= thresholds.review_floor and carries(s.candidate)}
    if not carriers:
        return [s for s, _ in scored], capped
    result = []
    for x, via_subtitle_split in scored:
        if not via_subtitle_split or x.candidate.external_id in carriers or x.title_score <= SUBTITLE_HEAD_CAP:
            result.append(x)
            continue
        capped[x.candidate.external_id] = x.title_score
        drop = x.title_score - SUBTITLE_HEAD_CAP
        result.append(replace(x, title_score=SUBTITLE_HEAD_CAP, adjusted_score=x.adjusted_score - drop))
    return result, capped


def _score_one(c: MatchCandidate, variants: List[_PreparedVariant], ctx: MatchContext) -> Tuple[ScoredCandidate, bool]:
    reasons = MatchReason.NONE
    titles = [t for t in [c.title, *(c.alt_titles or ())] if not is_null_or_whitespace(t)]
    # Only the MAIN title's trailing "(disambiguator)" counts stripped (1.27.0): MangaUpdates names
    # same-titled records "Word (AUTHOR Name)", so the stripped main title is the plain name. An ALT or hit
    # title "Word (Other Name)" is another record's name for a different work.
    stripped = amt.without_disambiguator(c.title)
    if stripped is not None and not contains_ignore_case(titles, stripped):
        titles.append(stripped)
    # 1.29.0: an ALT title whose disambiguator names THIS record's author is the record's own name - stripped,
    # it counts in full; any other stripped alias counts at DISAMBIGUATED_ALIAS_FACTOR, never alone an
    # automatic link.
    factors = [1.0] * len(titles)
    for alias, factor in amt.disambiguated_aliases(list(titles[1:]), c.authors):
        if contains_ignore_case(titles, alias):
            continue
        titles.append(alias)
        factors.append(factor)
    title_numbers = [number_tokens(t) for t in titles]
    # "Title: Long Subtitle", "Title ~Subtitle~" and "Title - Subtitle" records also compare by the part
    # before the break, capped (1.26.1 colon; 1.27.0 tilde and spaced dash).
    heads = distinct_ignore_case(h for h in (subtitle_head(t) for t in titles)
                                 if h is not None and not contains_ignore_case(titles, h))
    # An ALT title that is just the head of the record's own main title ("Title" on a record named
    # "Title - Spin-off Name") is the franchise's short name, not this record's full name (1.27.0). It counts
    # like a head: capped, review only.
    main_head = subtitle_head(c.title)
    if main_head is not None:
        head_form = scoring_form(main_head)
        for alias in [t for t in titles[1:] if scoring_form(t) == head_form]:
            at = titles.index(alias, 1)
            del titles[at]
            del factors[at]
            del title_numbers[at]
            if not contains_ignore_case(heads, alias):
                heads.append(alias)
    capped = len(titles)
    full_forms = [scoring_form(t) for t in titles]
    titles.extend(heads)
    title_numbers.extend(number_tokens(h) for h in heads)

    # Creator hints that name this record (its authors, or its "(AUTHOR Name)" disambiguator).
    authors = [a for a in (c.authors or ()) if not is_null_or_whitespace(a)]
    hints = [h for h in (ctx.creator_hints or ()) if not is_null_or_whitespace(h)]
    hint_names_record = bool(hints) and any(
        amt.names_equal(h, n) for h in hints
        for n in authors + [d for d in (amt.disambiguator_tag(t) for t in (c.title, *(c.alt_titles or ())))
                            if d is not None])

    # A trailing "[Two Words]" is read both as an English title and as a creator hint (1.27.0): as a title
    # it may carry an auto link only when the folder's own name also resembles the record.
    own_name_cache: List[bool] = []

    def own_name_resembles() -> bool:
        if not own_name_cache:
            own_name_cache.append(any(
                similarity.score(v.text, t) >= similarity.POSSIBLE_THRESHOLD
                for v in variants if _is_own_name(v.kind) for t in titles[:capped]))
        return own_name_cache[0]

    best = 0.0
    best_penalized = False
    best_via_subtitle_split = False
    for v in variants:
        review_only = ((v.kind == QueryVariantKind.CREATOR_SPLIT and not hint_names_record)
                       or (v.kind == QueryVariantKind.ENGLISH_TITLE
                           and any(amt.names_equal(h, v.text) for h in hints) and not own_name_resembles()))
        for i, t in enumerate(titles):
            if i >= capped:
                # Only a head EQUAL to the searched name counts, never a merely similar one - that is how
                # spin-offs ("Title: Side Story") look.
                if v.form != scoring_form(t):
                    continue
                raw = SUBTITLE_HEAD_CAP
            else:
                raw = similarity.score(v.text, t) * factors[i]
                # A shared number alone is no title evidence (1.27.0: "Title 99" vs an unrelated "... 99").
                if similarity.shares_only_digit_tokens(v.text, t):
                    raw *= DIGIT_ONLY_OVERLAP_FACTOR
                # The leading part of a long title (1.27.0): review only, never auto on its own.
                if raw < SUBTITLE_HEAD_CAP and _is_leading_part(v, full_forms[i]):
                    raw = SUBTITLE_HEAD_CAP
            if raw <= 0:
                continue
            if review_only:
                raw = min(raw, SUBTITLE_HEAD_CAP)
            penalized = not _numbers_agree(v, t, title_numbers[i])
            s = raw - (NUMBER_PENALTY if penalized else 0) - (DERIVED_VARIANT_DISCOUNT if v.derived else 0)
            if s > best:
                best = s
                best_penalized = penalized
                best_via_subtitle_split = v.kind == QueryVariantKind.SUBTITLE_SPLIT
    title = min(max(best, 0.0), 1.0)
    if best_penalized:
        reasons |= MatchReason.NUMBER_MISMATCH

    delta = 0.0

    # Format: the automatic search filters these out; a lifted filter may still return them.
    if c.format in (MetadataFormat.NOVEL, MetadataFormat.ARTBOOK, MetadataFormat.AUDIO):
        delta += FORMAT_CONFLICT
        reasons |= MatchReason.TYPE_CONFLICT

    # Origin vs the category folder: agreement only (MangaPixer owner option a', 2026-09-27). A manhwa filed
    # under a "Manga" folder is common, so a mismatch is neutral.
    origin = amt.parse_origin(c.origin)
    allowed = amt.origins_for_category(ctx.category_hint)
    if origin is not None and allowed is not None and (
            origin in allowed or (c.webtoon is True and MetadataOrigin.Korea in allowed)):
        delta += ORIGIN_AGREE
    # Tall strips are a hint, never a blocker (there are Japanese vertical manga).
    if ctx.tall_strips and (c.webtoon is True or origin in (MetadataOrigin.Korea, MetadataOrigin.ChinaTaiwan)):
        delta += ORIGIN_AGREE

    # Counts: volumes vs volumes, chapters vs chapters, unit NUMBERS (not file counts); a mixed folder gives
    # no signal, and the latest tracked chapter alone never conflicts with a record that counts its run in
    # volumes (count_evidence, 1.29.0).
    count = count_evidence.compare(count_evidence.from_context(ctx), count_evidence.PublishedUnitCounts.of(c))
    for signal in (count.volumes, count.chapters):
        if signal == count_evidence.CountSignal.CONFLICT:
            delta += CONFLICT
            reasons |= MatchReason.COUNT_CONFLICT
        elif signal == count_evidence.CountSignal.AGREE:
            delta += COUNT_AGREE

    # Year: a file cannot predate the series (English release years bound it from above).
    if ctx.earliest_year is not None and c.start_year is not None:
        if ctx.earliest_year < c.start_year - 1:
            delta += CONFLICT
            reasons |= MatchReason.YEAR_CONFLICT
        else:
            delta += YEAR_AGREE

    # One-shot shape: a one-shot record gets a small tie-break; a multi-volume record is NOT a conflict
    # (one archive can hold a whole series).
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

    # Creator tags: a tie-break everywhere; a veto at archive level when they name none of the record's
    # authors (undecidable when either side is empty).
    tags = [t for t in (ctx.author_tags or ()) if not is_null_or_whitespace(t)]
    author_bonus = 0.0
    if tags and authors:
        if any(amt.names_equal(t, a) for t in tags for a in authors):
            author_bonus = AUTHOR_AGREE
        elif is_archive_level(ctx.cls):
            reasons |= MatchReason.AUTHOR_CONFLICT

    # Creator hints from the name: positive only. The record's authors come from a full read; its
    # "(AUTHOR Name)" disambiguator is on every search hit, so ties are broken without a read.
    if hint_names_record:
        author_bonus = CREATOR_HINT_AGREE
    delta += author_bonus

    return ScoredCandidate(c, title, title + delta, reasons), best_via_subtitle_split


def _is_one_shot_record(c: MatchCandidate) -> bool:
    return c.volumes == 1 or (c.volumes is None and c.latest_chapter == 1)


def _are_related(a: MatchCandidate, b: MatchCandidate) -> bool:
    return (a.provider == b.provider
            and (any(r.external_id == b.external_id for r in (a.relations or ()))
                 or any(r.external_id == a.external_id for r in (b.relations or ()))))


def _choose_persisted(ranked: List[ScoredCandidate],
                      also_keep: Sequence[ScoredCandidate] = ()) -> Tuple[ScoredCandidate, ...]:
    """The adaptive review set, plus the records ``also_keep`` names (1.30.0), in rank order."""
    top = ranked[0]
    if len(ranked) == 1 or top.adjusted_score - ranked[1].adjusted_score >= PERSIST_CLEAR_LEAD:
        chosen = [top]
    else:
        chosen = []
        for s in ranked:
            if not top.adjusted_score - s.adjusted_score <= PERSIST_WINDOW + _SCORE_TOLERANCE:
                break
            chosen.append(s)
            if len(chosen) == PERSIST_MAX:
                break
    extra = [a for a in also_keep if not any(a is x for x in chosen)]
    if not extra:
        return tuple(chosen)
    keep = {s.candidate.external_id for s in chosen[:max(1, PERSIST_MAX - len(extra))]}
    keep.update(s.candidate.external_id for s in extra)
    return tuple([s for s in ranked if s.candidate.external_id in keep][:PERSIST_MAX])


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
