"""Port of MangaPixer ``tests/MangaPixer.Core.Tests/Metadata/AutoMatch/MatchScorerTests.cs``."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence, Tuple

import pytest

from manga_list.matcher import auto_match_text as amt
from manga_list.matcher import detector, planner, scorer, similarity
from manga_list.matcher.count_evidence import LocalUnitCounts
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
    # Positive-only (1.27.0, owner option a'): the other origin is neutral, so the lead is only the +0.02.
    assert not (o.ranked[1].reasons & MatchReason.TYPE_CONFLICT)
    assert o.band == MatchBand.NEEDS_REVIEW


def test_category_origin_mismatch_is_neutral_no_penalty_and_no_veto():
    # A manhwa filed under a "Manga" folder still auto-links (live run, owner option a').
    o = score(query(["Some Series"], category="Manga"), rec("kr", "Some Series", origin="Manhwa", webtoon=True))

    assert o.band == MatchBand.AUTO
    assert o.ranked[0].reasons == MatchReason.NONE
    assert o.ranked[0].adjusted_score == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("name, expected", [
    ("Manga", True),
    (" manhwa ", True),
    ("WEBTOONS", True),
    ("Manga Collection", False),  # whole name only
    ("Ongoing", False),  # a shelf word is never a category hint
    (None, False),
])
def test_category_folder_name_is_the_exact_whole_name(name, expected):
    assert amt.is_category_folder_name(name) == expected


def test_category_and_shelf_words_are_one_stop_list_for_creator_names():
    assert amt.is_category_word("Manhwa")
    assert amt.is_category_word("Light Novels")
    assert not amt.is_author_like("Manga", require_two_tokens=False)
    assert not amt.is_author_like("Light Novels", require_two_tokens=True)
    assert not {w.lower() for w in amt.CATEGORY_FOLDER_WORDS} & {w.lower() for w in amt.SHELF_WORDS}


def test_origin_name_of_the_enum_is_accepted_too():
    o = score(query(["Some Series"], category="Manhwa"), rec("kr", "Some Series", origin=MetadataOrigin.Korea.name))

    assert not (o.ranked[0].reasons & MatchReason.TYPE_CONFLICT)


def test_tall_strips_are_a_hint_only_a_print_record_is_not_a_conflict():
    # Owner, 1.27.0 review: Japanese vertical manga exist - tall pages favour webtoon records but never block.
    o = score(query(["Some Series"], tall=True), rec("1", "Some Series", origin="Manga", webtoon=False))

    assert not (o.ranked[0].reasons & MatchReason.TYPE_CONFLICT)
    assert o.band == MatchBand.AUTO


def test_tall_strips_favour_a_webtoon_record_over_a_print_record_of_the_same_title():
    o = score(query(["Some Series"], tall=True),
              rec("jp", "Some Series", origin="Manga", webtoon=False), rec("kr", "Some Series", origin="Manhwa"))

    assert o.ranked[0].candidate.external_id == "kr"
    assert o.ranked[0].adjusted_score > o.ranked[1].adjusted_score


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


# --- 1.27.0 - 1.30.0 (ported from MangaPixer 1.31.1 MatchScorerTests) --------------------------

def test_disambiguator_is_stripped_from_the_main_title_only_never_from_an_alt_title():
    # Live run (B): the right record is "Sprout (FAMILY Given)"; another record carries the ALT title
    # "Sprout (OTHER Person)". Stripped, that alt scored a false 1.00 and tied the right record.
    o = score(query(["Sprout"]), rec("1", "Sprout (FAMILY Given)"), rec("2", "Hana no Me", alt=["Sprout (OTHER Person)"]))

    assert o.ranked[0].candidate.external_id == "1"
    assert o.ranked[0].title_score == pytest.approx(1.0, abs=5e-4)
    assert o.ranked[1].title_score < 0.92
    assert o.band == MatchBand.AUTO


def test_alt_title_disambiguator_naming_the_records_own_author_counts_in_full_otherwise_capped():
    own = score(query(["Moon Letter"]), rec("1", "Moon Letter"),
                rec("2", "Tsuki no Tegami", alt=["Moon Letter (SATO Hana)"], authors=["SATO Hana"]))
    assert next(r for r in own.ranked if r.candidate.external_id == "2").title_score == pytest.approx(1.0, abs=5e-4)
    assert own.band != MatchBand.AUTO  # two works share the name: review, the right one among the top

    # A search hit (no authors yet): the stripped alias is evidence, never alone an auto link.
    hit = score(query(["Moon Letter"]), rec("2", "Tsuki no Tegami", alt=["Moon Letter (SATO Hana)"]))
    assert hit.ranked[0].title_score == pytest.approx(amt.DISAMBIGUATED_ALIAS_FACTOR, abs=5e-4)
    assert hit.band not in (MatchBand.AUTO, MatchBand.UNMATCHED)


def test_tilde_subtitle_and_a_title_number_reach_review_without_a_number_penalty():
    o = score(query(["Alpha Beta Level 99"]),
              rec("1", "Arufa Beta Reberu 99: Hidden Subtitle Words", alt=["Alpha Beta Level 99 ~Long Subtitle Words Here~"]))

    assert o.ranked[0].title_score == pytest.approx(scorer.SUBTITLE_HEAD_CAP, abs=5e-4)
    assert not (o.ranked[0].reasons & MatchReason.NUMBER_MISMATCH)
    assert o.band == MatchBand.NEEDS_REVIEW


def test_spaced_dash_subtitle_head_equal_to_the_name_is_review_only():
    o = score(query(["Alpha Beta"]), rec("1", "Alpha Beta - The Long Subtitle of It"))

    assert o.ranked[0].title_score == pytest.approx(scorer.SUBTITLE_HEAD_CAP, abs=5e-4)
    assert o.band == MatchBand.NEEDS_REVIEW


def test_sequel_number_still_penalized_when_the_record_title_lacks_it():
    o = score(query(["Alpha Beta 2"]), rec("1", "Alpha Beta"))
    assert o.ranked[0].reasons & MatchReason.NUMBER_MISMATCH


def test_leading_words_of_a_long_title_are_review_only_even_at_the_loosest_thresholds():
    q = query(["Alpha to Beta Gamma"])
    record = rec("1", "Alpha to Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa Lambda Mu")
    o = score(q, record)
    loosest = scorer.score(q, [record], MatchThresholds(MatchThresholds.AUTO_TITLE_MIN, MatchThresholds.MARGIN_MIN,
                                                        MatchThresholds.REVIEW_FLOOR_MIN))

    assert o.ranked[0].title_score == pytest.approx(scorer.SUBTITLE_HEAD_CAP, abs=5e-4)
    assert o.band == MatchBand.NEEDS_REVIEW
    assert loosest.band == MatchBand.NEEDS_REVIEW


def test_leading_part_needs_three_whole_words():
    two = score(query(["Alpha Beta"]), rec("1", "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota"))
    partial = score(query(["Alpha Beta Gam"]), rec("1", "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota"))

    assert two.ranked[0].title_score < scorer.SUBTITLE_HEAD_CAP
    assert partial.ranked[0].title_score < scorer.SUBTITLE_HEAD_CAP


def test_close_second_is_only_raised_when_the_top_reaches_the_review_floor():
    poor = score(query(["Alpha to Beta Gamma"]), rec("1", "Unrelated Words Here"), rec("2", "Other Unrelated Words"))
    assert poor.band == MatchBand.UNMATCHED
    assert not (poor.ranked[0].reasons & MatchReason.CLOSE_SECOND)

    tied = score(query(["Sprout"]), rec("1", "Sprout (OTHER Person)"), rec("2", "Sprout (THIRD Person)"))
    assert tied.ranked[0].reasons & MatchReason.CLOSE_SECOND


def test_shared_number_alone_is_damped():
    damped = score(query(["Alpha Beta 99"]), rec("1", "Kappa Lambda 99"))
    plain = similarity.score("Alpha Beta 99", "Kappa Lambda 99")

    assert similarity.shares_only_digit_tokens("Alpha Beta 99", "Kappa Lambda 99")
    assert not similarity.shares_only_digit_tokens("Alpha Beta 99", "Alpha Kappa 99")
    assert damped.ranked[0].title_score == pytest.approx(plain * scorer.DIGIT_ONLY_OVERLAP_FACTOR, abs=5e-7)


def test_creator_split_is_review_only_unless_the_named_author_wrote_the_record():
    shape = FolderShape("Family Given - Sprout Garden", 2,
                        ("Family Given - Sprout Garden v01.cbz", "Family Given - Sprout Garden v02.cbz"), ())
    q = planner.plan_folder(shape, detector.classify(shape))
    assert any(v.kind == QueryVariantKind.CREATOR_SPLIT and v.text == "Sprout Garden" for v in q.variants)

    stranger = scorer.score(q, [rec("1", "Sprout Garden", authors=["Other Person"])], DEFAULT_THRESHOLDS)
    assert stranger.ranked[0].title_score == pytest.approx(scorer.SUBTITLE_HEAD_CAP - scorer.DERIVED_VARIANT_DISCOUNT, abs=5e-4)
    assert stranger.band == MatchBand.NEEDS_REVIEW

    author = scorer.score(q, [rec("1", "Sprout Garden", authors=["GIVEN Family"])], DEFAULT_THRESHOLDS)
    assert author.ranked[0].title_score >= 0.92
    assert author.band == MatchBand.AUTO


def test_trailing_two_word_bracket_as_a_title_never_auto_links_on_its_own():
    shape = FolderShape("Sprout [Family Given]", 2, ("Sprout v01.cbz", "Sprout v02.cbz"), ())
    q = planner.plan_folder(shape, detector.classify(shape))
    assert any(v.kind == QueryVariantKind.ENGLISH_TITLE and v.text == "Family Given" for v in q.variants)

    titled = scorer.score(q, [rec("1", "Family Given")], DEFAULT_THRESHOLDS)
    assert titled.band != MatchBand.AUTO
    loose = scorer.score(q, [rec("1", "Family Given")], MatchThresholds(
        MatchThresholds.AUTO_TITLE_MIN, MatchThresholds.MARGIN_MIN, MatchThresholds.REVIEW_FLOOR_MIN))
    assert loose.band != MatchBand.AUTO

    real = scorer.score(q, [rec("1", "Sprout", authors=["Family Given"])], DEFAULT_THRESHOLDS)
    assert real.band == MatchBand.AUTO


def test_romaji_with_english_bracket_still_auto_links_by_the_english_title():
    q = MatchQuery(
        (QueryVariant("Kappa Meshi", QueryVariantKind.PRIMARY), QueryVariant("Delicious Kappa", QueryVariantKind.ENGLISH_TITLE)),
        MatchContext(WorkClass.SERIES, 5, 5, 0, None, None, False, (), creator_hints=("Delicious Kappa",)))
    o = scorer.score(q, [rec("1", "Kappa Meshi", alt=["Delicious Kappa"])], DEFAULT_THRESHOLDS)

    assert o.band == MatchBand.AUTO


def test_an_alias_that_is_the_main_titles_head_is_review_only():
    o = score(query(["Alpha Beta"]),
              rec("spin", "Alpha Beta - Side Name Diary", alt=["Alpha Beta"]),
              rec("main", "Alpha Beta - Main Subtitle Words", alt=["Alpha Beta ~Main Subtitle Words~"]))

    assert all(r.title_score == pytest.approx(scorer.SUBTITLE_HEAD_CAP, abs=5e-4) for r in o.ranked)
    assert o.band == MatchBand.NEEDS_REVIEW

    # A plain alias of a record whose main title has no subtitle is still a full match.
    plain = score(query(["Alpha Beta"]), rec("1", "Arufa Beta", alt=["Alpha Beta"]))
    assert plain.band == MatchBand.AUTO


def _with_units(q: MatchQuery, volume_like: int, chapter_like: int, local_volumes, local_chapters) -> MatchQuery:
    return replace(q, context=replace(q.context, volume_like_count=volume_like, chapter_like_count=chapter_like,
                                      local_volumes=local_volumes, local_chapters=local_chapters))


def test_count_compares_the_highest_unit_number_not_the_file_count():
    extras = score(_with_units(query(["Some Series"]), 12, 0, 6, None), rec("1", "Some Series", volumes=6))
    assert not (extras.ranked[0].reasons & MatchReason.COUNT_CONFLICT)

    late = score(_with_units(query(["Some Series"]), 0, 150, None, 1100), rec("1", "Some Series", chapter=200))
    assert late.ranked[0].reasons & MatchReason.COUNT_CONFLICT


def test_count_mixed_volume_and_chapter_archives_give_no_count_signal():
    o = score(_with_units(query(["Some Series"]), 40, 300, 40, 300), rec("1", "Some Series", volumes=2, chapter=10))

    assert not (o.ranked[0].reasons & MatchReason.COUNT_CONFLICT)
    assert o.ranked[0].adjusted_score == pytest.approx(1.0, abs=1e-6)  # neither an agreement nor a conflict


def test_count_published_side_is_the_largest_number_of_any_source():
    season = score(_with_units(query(["Some Series"]), 0, 195, None, 195),
                   replace(rec("1", "Some Series", chapter=18), total_chapters=195))
    assert not (season.ranked[0].reasons & MatchReason.COUNT_CONFLICT)
    assert season.band == MatchBand.AUTO

    english = score(_with_units(query(["Some Series"]), 30, 0, 30, None),
                    replace(rec("1", "Some Series", volumes=12), english_volumes=30, english_chapters=250))
    assert not (english.ranked[0].reasons & MatchReason.COUNT_CONFLICT)
    none = score(_with_units(query(["Some Series"]), 30, 0, 30, None), rec("1", "Some Series", volumes=12))
    assert none.ranked[0].reasons & MatchReason.COUNT_CONFLICT


def test_count_chapter_folder_of_a_volume_record_with_only_a_latest_chapter_is_no_conflict():
    spin_off = score(_with_units(query(["Some Series"]), 0, 58, None, 58), rec("1", "Some Series", volumes=10, chapter=12))
    assert not (spin_off.ranked[0].reasons & MatchReason.COUNT_CONFLICT)
    assert spin_off.band == MatchBand.AUTO

    total = score(_with_units(query(["Some Series"]), 0, 58, None, 58),
                  replace(rec("1", "Some Series", volumes=10, chapter=12), total_chapters=20))
    assert total.ranked[0].reasons & MatchReason.COUNT_CONFLICT


def test_count_uses_the_planners_units_over_the_archive_counts():
    units = LocalUnitCounts(40, 0, 1, 10, None, None)
    b = query(["Some Series"], volumes=40)
    q = replace(b, context=replace(b.context, units=units))
    assert not (score(q, rec("1", "Some Series", volumes=10)).ranked[0].reasons & MatchReason.COUNT_CONFLICT)
    assert score(query(["Some Series"], volumes=40), rec("1", "Some Series", volumes=10)).ranked[0].reasons & MatchReason.COUNT_CONFLICT


def _planned(folder_name: str, chapters: int) -> MatchQuery:
    shape = FolderShape(folder_name, 2, tuple(f"{folder_name} - Chapter {i:03d}.cbz" for i in range(1, chapters + 1)), ())
    return planner.plan_folder(shape, detector.classify(shape))


def test_a_folder_subtitle_that_is_a_spin_offs_subtitle_ranks_the_spin_off_first_but_only_for_review():
    q = _planned("Alpha Garden - Before the Frost", 58)
    main = rec("1", "Alpha Garden", volumes=30, related=[("2", "Spin-off")])
    spin_off = rec("2", "Alpha Garden - Before the Frost", volumes=10, related=[("1", "Main Story")])

    o = score(q, main, spin_off)

    assert o.ranked[0].candidate.external_id == "2"
    assert o.ranked[1].title_score == pytest.approx(scorer.SUBTITLE_HEAD_CAP, abs=5e-4)
    assert o.band == MatchBand.NEEDS_REVIEW
    family = MatchReason.SUBTITLE_FAMILY | MatchReason.SERIES_FAMILY | MatchReason.RELATED_PAIR
    assert o.ranked[0].reasons & family == MatchReason.SUBTITLE_FAMILY | MatchReason.SERIES_FAMILY
    assert sorted(p.candidate.external_id for p in o.to_persist) == ["1", "2"]


def test_a_folder_subtitle_against_a_prequel_pair_goes_to_review():
    q = _planned("Alpha Garden - Before the Frost", 58)

    both = score(q, rec("1", "Alpha Garden", related=[("2", "prequel")]), rec("2", "Alpha Garden - Before the Frost", related=[("1", "sequel")]))
    one_way = score(q, rec("1", "Alpha Garden"), rec("2", "Alpha Garden - Before the Frost", related=[("1", "sequel")]))

    assert (both.ranked[0].candidate.external_id, both.band) == ("2", MatchBand.NEEDS_REVIEW)
    assert (one_way.ranked[0].candidate.external_id, one_way.band) == ("2", MatchBand.NEEDS_REVIEW)
    assert one_way.ranked[0].reasons & MatchReason.SUBTITLE_FAMILY


def test_a_folder_subtitle_against_an_unrelated_head_record_stays_automatic():
    q = _planned("Alpha Garden - Before the Frost", 58)

    o = score(q, rec("1", "Alpha Garden", authors=["SMITH Anna"]), rec("2", "Alpha Garden - Before the Frost", authors=["JONES Bert"]))

    assert (o.ranked[0].candidate.external_id, o.band) == ("2", MatchBand.AUTO)
    assert o.ranked[0].reasons & (MatchReason.SUBTITLE_FAMILY | MatchReason.SERIES_FAMILY) == MatchReason.NONE


def test_a_folder_subtitle_no_relation_but_the_same_author_is_one_family_review():
    q = _planned("Alpha Garden - Before the Frost", 58)

    o = score(q, rec("1", "Alpha Garden", authors=["SMITH Anna"]), rec("2", "Alpha Garden - Before the Frost", authors=["Smith Anna"]))

    assert (o.ranked[0].candidate.external_id, o.band) == ("2", MatchBand.NEEDS_REVIEW)
    assert o.ranked[0].reasons & MatchReason.SUBTITLE_FAMILY


def test_the_main_series_folder_auto_links_the_main_record_with_a_family_chip():
    o = score(query(["Alpha Garden"]), rec("1", "Alpha Garden", related=[("2", "spin-off")]),
              rec("2", "Alpha Garden - Before the Frost", related=[("1", "main story")]))

    assert (o.ranked[0].candidate.external_id, o.band) == ("1", MatchBand.AUTO)
    assert o.ranked[0].reasons & MatchReason.SERIES_FAMILY
    assert not (o.ranked[0].reasons & MatchReason.SUBTITLE_FAMILY)


def test_a_folder_subtitle_no_candidate_has_leaves_the_head_match_alone():
    q = _planned("Alpha Garden - Before the Frost", 20)

    o = score(q, rec("1", "Alpha Garden", volumes=30))

    assert o.ranked[0].title_score == pytest.approx(1.0 - scorer.DERIVED_VARIANT_DISCOUNT, abs=5e-4)


def test_a_folder_subtitle_the_main_record_carries_itself_is_not_capped():
    q = _planned("Alpha Garden - Before the Frost", 20)

    o = score(q, rec("1", "Arufa Gaaden", alt=["Alpha Garden: Before the Frost"], volumes=30), rec("3", "Alpha Garden Other"))

    assert o.ranked[0].candidate.external_id == "1"
    assert o.ranked[0].title_score > 0.95
