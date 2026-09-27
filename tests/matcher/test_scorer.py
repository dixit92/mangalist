"""Port of MangaPixer ``tests/MangaPixer.Core.Tests/Metadata/AutoMatch/MatchScorerTests.cs``."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence, Tuple

import pytest

from manga_list.matcher import detector, planner, scorer
from manga_list.matcher.contracts import (
    DEFAULT_THRESHOLDS,
    CandidateRelation,
    FolderShape,
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
    WorkClass,
)


def rec(id: str, title: str, alt: Optional[Sequence[str]] = None,
        format: Optional[MetadataFormat] = MetadataFormat.COMIC, origin: Optional[str] = "Manga",
        year: Optional[int] = None, volumes: Optional[int] = None, chapter: Optional[int] = None,
        authors: Optional[Sequence[str]] = None, related: Optional[Sequence[Tuple[str, str]]] = None,
        webtoon: Optional[bool] = None) -> MatchCandidate:
    return MatchCandidate("mangaupdates", id, title, tuple(alt or ()), format, origin, year, volumes, chapter,
                          tuple(authors or ()), tuple(CandidateRelation(i, r) for i, r in (related or ())), webtoon)


def query(titles: Sequence[str], cls: WorkClass = WorkClass.SERIES, archives: int = 5, volumes: int = 0,
          chapters: int = 0, earliest_year: Optional[int] = None, category: Optional[str] = None,
          tall: bool = False, authors: Optional[Sequence[str]] = None, comic_info: Optional[str] = None,
          first_kind: QueryVariantKind = QueryVariantKind.PRIMARY) -> MatchQuery:
    return MatchQuery(
        tuple(QueryVariant(t, first_kind if i == 0 else QueryVariantKind.ENGLISH_TITLE) for i, t in enumerate(titles)),
        MatchContext(cls, archives, volumes, chapters, earliest_year, category, tall, tuple(authors or ()), comic_info))


def score(q: MatchQuery, *c: MatchCandidate) -> MatchOutcome:
    return scorer.score(q, c, DEFAULT_THRESHOLDS)


def test_one_shot_folder_named_edition_matches_the_series_record():
    # A whole series in one archive, named after a re-release: the planner drops the edition
    # phrase, so the series record leads its spin-offs instead of all scoring alike.
    shape = FolderShape("SOME TITLE! Master Edition", 1, ("SOME TITLE! Master Edition.cbz",), ())
    q = planner.plan_folder(shape, detector.classify(shape))

    o = scorer.score(q,
                     [rec("1", "Some Title!", volumes=10), rec("2", "Some Title! Academy and So On"),
                      rec("3", "Some Title 2")],
                     DEFAULT_THRESHOLDS)

    assert q.context.cls == WorkClass.ONE_SHOT
    assert q.variants[0].text == "SOME TITLE"
    # MangaUpdates search treats a trailing "!" as significant: the name as written is the second search.
    assert q.variants[1] == QueryVariant("SOME TITLE!", QueryVariantKind.PRIMARY)
    assert o.band != MatchBand.UNMATCHED
    assert o.ranked[0].candidate.external_id == "1"


def test_exact_title_clear_lead_is_auto():
    o = score(query(["Some Series"]), rec("1", "Some Series"), rec("2", "Completely Different"))

    assert o.band == MatchBand.AUTO
    assert o.ranked[0].candidate.external_id == "1"
    assert round(o.ranked[0].title_score, 6) == round(1.0, 6)


def test_alt_title_matches_the_english_variant():
    o = score(query(["Romaji Name Here", "English Name Here"]),
              rec("1", "Romaji Name Here Official", ["English Name Here"]))

    assert o.band == MatchBand.AUTO


def test_no_candidates_is_unmatched_with_nothing_persisted():
    o = score(query(["Some Series"]))

    assert o.band == MatchBand.UNMATCHED
    assert len(o.ranked) == 0
    assert len(o.to_persist) == 0


def test_weak_title_is_unmatched_medium_is_review():
    assert score(query(["Some Series"]), rec("1", "Nothing Alike At All")).band == MatchBand.UNMATCHED
    assert score(query(["Some Series Name"]), rec("1", "Some Series Name Gaiden")).band == MatchBand.NEEDS_REVIEW


def test_exact_tie_is_review_with_close_second():
    o = score(query(["Some Series"]), rec("1", "Some Series"), rec("2", "Some Series"))

    assert o.band == MatchBand.NEEDS_REVIEW
    assert o.ranked[0].reasons & MatchReason.CLOSE_SECOND
    assert len(o.to_persist) == 2


def test_novel_twin_is_pushed_down_by_the_format_conflict():
    o = score(query(["Some Series"]), rec("n", "Some Series", format=MetadataFormat.NOVEL, origin="Novel"),
              rec("m", "Some Series"))

    assert o.band == MatchBand.AUTO
    assert o.ranked[0].candidate.external_id == "m"
    assert o.ranked[1].reasons & MatchReason.TYPE_CONFLICT


def test_category_origin_breaks_a_tie_but_never_lifts_the_raw_score():
    o = score(query(["Some Series"], category="Manhwa"), rec("jp", "Some Series", origin="Manga"),
              rec("kr", "Some Series", origin="Manhwa"))

    assert o.ranked[0].candidate.external_id == "kr"
    assert round(o.ranked[0].title_score, 6) == round(1.0, 6)
    assert o.ranked[1].reasons & MatchReason.TYPE_CONFLICT
    assert o.band == MatchBand.AUTO  # 1.02 vs 0.90: lead 0.12


def test_origin_name_of_the_enum_is_accepted_too():
    o = score(query(["Some Series"], category="Manhwa"), rec("kr", "Some Series", origin=MetadataOrigin.Korea.name))

    assert not (o.ranked[0].reasons & MatchReason.TYPE_CONFLICT)


def test_tall_strips_conflict_with_a_print_record():
    o = score(query(["Some Series"], tall=True), rec("1", "Some Series", origin="Manga", webtoon=False))

    assert o.ranked[0].reasons & MatchReason.TYPE_CONFLICT
    assert o.band == MatchBand.NEEDS_REVIEW


def test_chapters_are_compared_with_chapters_not_volumes():
    # E3: 150 chapter archives of a 20-volume series is NOT a conflict.
    ok = score(query(["Some Series"], chapters=150), rec("1", "Some Series", volumes=20, chapter=160))
    assert ok.band == MatchBand.AUTO
    assert not (ok.ranked[0].reasons & MatchReason.COUNT_CONFLICT)

    too_many = score(query(["Some Series"], volumes=40), rec("1", "Some Series", volumes=20))
    assert too_many.ranked[0].reasons & MatchReason.COUNT_CONFLICT
    assert too_many.band == MatchBand.NEEDS_REVIEW


def test_season_renumbered_webtoon_compares_chapters_with_the_stated_total():
    # The latest chapter number restarts per season; the status line states the total.
    q = query(["Some Series"], chapters=600, category="Manhwa")

    assert score(q, rec("1", "Some Series", origin="Manhwa", chapter=235)).ranked[0].reasons & MatchReason.COUNT_CONFLICT
    with_total = score(q, replace(rec("1", "Some Series", origin="Manhwa", chapter=235), total_chapters=652))
    assert not (with_total.ranked[0].reasons & MatchReason.COUNT_CONFLICT)
    assert with_total.band == MatchBand.AUTO


def test_file_year_before_the_start_is_a_year_conflict():
    o = score(query(["Some Series"], earliest_year=2001), rec("1", "Some Series", year=2015))

    assert o.ranked[0].reasons & MatchReason.YEAR_CONFLICT
    assert o.band == MatchBand.NEEDS_REVIEW


def test_sequel_number_disagreement_is_penalized():
    o = score(query(["Some Series Part 3 - Subtitle"]),
              rec("p3", "Some Series Part 3: Subtitle"), rec("p4", "Some Series Part 4: Other Subtitle"),
              rec("all", "Some Series"))

    assert o.ranked[0].candidate.external_id == "p3"
    assert o.band == MatchBand.AUTO
    p4 = [r for r in o.ranked if r.candidate.external_id == "p4"]
    assert len(p4) == 1
    assert p4[0].reasons & MatchReason.NUMBER_MISMATCH


def test_sequel_split_variant_still_penalizes_the_missing_number():
    # "Title 2" must not auto-link to "Title" through its retrieval-only split variant.
    q = MatchQuery(
        (QueryVariant("Some Series 2", QueryVariantKind.PRIMARY),
         QueryVariant("Some Series", QueryVariantKind.SEQUEL_NUMBER_SPLIT)),
        MatchContext(WorkClass.SERIES, 5, 0, 0, None, None, False, ()))
    o = scorer.score(q, [rec("1", "Some Series")], DEFAULT_THRESHOLDS)

    assert o.band != MatchBand.AUTO
    assert o.ranked[0].reasons & MatchReason.NUMBER_MISMATCH


def test_related_pair_forces_review_unless_the_title_separates_them():
    main = rec("main", "Some Series", related=[("spin", "Spin-Off")])
    spin = rec("spin", "Some Series!")
    o = score(query(["Some Series"]), main, spin)
    assert o.band == MatchBand.NEEDS_REVIEW
    assert o.ranked[0].reasons & MatchReason.RELATED_PAIR

    far_spin = rec("spin", "Some Series Side Story Collection")
    o2 = score(query(["Some Series"]), main, far_spin)
    assert o2.band == MatchBand.AUTO
    assert not (o2.ranked[0].reasons & MatchReason.RELATED_PAIR)


@pytest.mark.parametrize("cls", [
    WorkClass.MIXED,
    WorkClass.AMBIGUOUS,
    WorkClass.COLLECTION_CONTAINER,
])
def test_review_only_classes_are_never_auto(cls):
    o = score(query(["Some Series"], cls=cls), rec("1", "Some Series"))

    assert o.band == MatchBand.NEEDS_REVIEW
    assert o.ranked[0].reasons & MatchReason.REVIEW_ONLY_CLASS


def test_one_shot_folder_auto_links_whatever_the_records_volume_count():
    # One archive can be a one-shot, one volume or a whole multi-volume series (owner, 2026-09-26):
    # the record's volume count never blocks auto; only the stricter title score does.
    assert score(query(["Short Story"], cls=WorkClass.ONE_SHOT, archives=1),
                 rec("1", "Short Story", volumes=1)).band == MatchBand.AUTO
    assert score(query(["Short Story"], cls=WorkClass.ONE_SHOT, archives=1),
                 rec("1", "Short Story")).band == MatchBand.AUTO

    whole_series = score(query(["Short Story"], cls=WorkClass.ONE_SHOT, archives=1),
                         rec("1", "Short Story", volumes=12))
    assert whole_series.band == MatchBand.AUTO
    assert not (whole_series.ranked[0].reasons & MatchReason.ONE_SHOT_MISMATCH)


def test_archive_level_author_conflict_vetoes_auto():
    q = query(["Short Story"], cls=WorkClass.COLLECTION_LEAF, archives=1, authors=["Some Artist"])

    conflict = score(q, rec("1", "Short Story", authors=["Other Person"]))
    assert conflict.band == MatchBand.NEEDS_REVIEW
    assert conflict.ranked[0].reasons & MatchReason.AUTHOR_CONFLICT

    agree = score(q, rec("1", "Short Story", authors=["ARTIST Some"]))
    assert agree.band == MatchBand.AUTO

    # Undecidable (no authors on the record): no veto.
    assert score(q, rec("1", "Short Story")).band == MatchBand.AUTO


def test_folder_level_author_mismatch_is_no_veto_agreement_is_a_tie_break():
    q = query(["Some Series"], authors=["Some Author"])

    assert score(q, rec("1", "Some Series", authors=["Other Person"])).band == MatchBand.AUTO

    o = score(q, rec("a", "Some Series", authors=["Other Person"]), rec("b", "Some Series", authors=["Some Author"]))
    assert o.ranked[0].candidate.external_id == "b"


def test_comic_info_series_breaks_a_tie():
    o = score(query(["Some Series"], comic_info="Some Series Deluxe"),
              rec("a", "Some Series"), rec("b", "Some Series", ["Some Series Deluxe"]))

    assert o.ranked[0].candidate.external_id == "b"


def test_thresholds_come_from_settings():
    q = query(["Some Series Name"])
    r = rec("1", "Some Series Name!!")
    close = rec("1", "Some Serie Name")

    strict = scorer.score(q, [close], MatchThresholds(0.99, 0.10, 0.60))
    loose = scorer.score(q, [close], MatchThresholds(0.85, 0.10, 0.60))
    assert strict.band == MatchBand.NEEDS_REVIEW
    assert loose.band == MatchBand.AUTO
    assert scorer.score(q, [r], MatchThresholds(0.99, 0.10, 0.60)).band == MatchBand.AUTO

    with pytest.raises(ValueError):
        scorer.score(q, [r], MatchThresholds(0.5, 0.1, 0.6))


def test_to_persist_only_the_top_when_it_leads_clearly():
    o = score(query(["Some Series Name"], cls=WorkClass.AMBIGUOUS),
              rec("1", "Some Series Name"), rec("2", "Unrelated Words Entirely"), rec("3", "Other Thing"))

    assert o.band == MatchBand.NEEDS_REVIEW
    assert [p.candidate.external_id for p in o.to_persist] == ["1"]


def test_to_persist_keeps_candidates_within_the_window_at_most_five():
    recs = [rec(str(i), "Some Series") for i in range(1, 8)]
    o = score(query(["Some Series"]), *recs)

    assert o.band == MatchBand.NEEDS_REVIEW
    assert len(o.to_persist) == 5
    assert len(o.ranked) == 7


def test_duplicate_candidates_are_merged_keeping_the_richer_entry():
    o = score(query(["Some Series"]), rec("1", "Some Series"), rec("1", "Some Series", authors=["A B"], volumes=3))

    assert len(o.ranked) == 1
    assert o.ranked[0].candidate.volumes == 3
    assert o.band == MatchBand.AUTO


def test_score_is_deterministic_regardless_of_input_order():
    a = rec("a", "Some Series")
    b = rec("b", "Some Series")
    o1 = score(query(["Some Series"]), a, b)
    o2 = score(query(["Some Series"]), b, a)

    assert [r.candidate.external_id for r in o1.ranked] == [r.candidate.external_id for r in o2.ranked]


# --- 1.26.1 ---------------------------------------------------------------------------------

def with_hints(q: MatchQuery, *hints: str) -> MatchQuery:
    return replace(q, context=replace(q.context, creator_hints=tuple(hints)))


def test_creator_hint_separates_same_titled_records_by_their_disambiguator():
    # "Sprout [Family Given].cbz" in a one-shot collection: three records score 1.00 on the title.
    q = with_hints(query(["Sprout"], WorkClass.COLLECTION_LEAF, archives=1), "Family Given")
    o = score(q, rec("1", "Sprout (OTHER Person)"), rec("2", "Sprout", volumes=15), rec("3", "Sprout (FAMILY Given)"))

    assert o.ranked[0].candidate.external_id == "3"
    assert o.band == MatchBand.AUTO


def test_creator_hint_matches_record_authors_in_either_name_order_and_never_vetoes():
    q = with_hints(query(["Some Series"]), "Family Given")
    agree = score(q, rec("1", "Some Series", authors=["Given Family"]), rec("2", "Some Series 2nd"))
    none = score(with_hints(query(["Some Series"]), "English Words"), rec("1", "Some Series", authors=["Given Family"]))

    assert round(agree.ranked[0].adjusted_score, 6) == round(1.0 + scorer.CREATOR_HINT_AGREE, 6)
    assert round(none.ranked[0].adjusted_score, 6) == round(1.0, 6)
    assert none.ranked[0].reasons & scorer.VETO_REASONS == MatchReason.NONE


def test_record_title_with_subtitle_ranks_by_the_head_before_the_colon_but_never_auto_links():
    o = score(query(["Fake Hero of the Year"]),
              rec("1", "Fake Hero of the Year: Ideal Hero? Sorry, a Fake"), rec("2", "The Year of Nothing"))
    exact = score(query(["Some Saga"]), rec("1", "Some Saga"), rec("2", "Some Saga: Before the Fall"))

    assert o.ranked[0].candidate.external_id == "1"
    assert round(o.ranked[0].title_score, 6) == round(scorer.SUBTITLE_HEAD_CAP, 6)
    assert o.band == MatchBand.NEEDS_REVIEW
    assert (exact.ranked[0].candidate.external_id, exact.band) == ("1", MatchBand.AUTO)
