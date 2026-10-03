"""MangaPixer 1.32.0 parity: the parser / matcher rules ported from ``f727088`` (the manga path only).

Every expectation below was also checked against MangaPixer's own code at ``f727088`` (a throwaway probe of
the same names through ``AutoMatchText`` / ``TitleNormalizer`` / ``ArchiveNameAnatomy`` /
``MatchQueryPlanner``; identical line by line). Test names are synthetic or public, well-known titles.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from manga_list import models, mu_cache
from manga_list.matcher import auto_match_text as amt
from manga_list.matcher import detector, planner, scorer
from manga_list.matcher.anatomy import is_release_tag, parse
from manga_list.matcher.contracts import (
    DEFAULT_THRESHOLDS,
    FolderShape,
    MatchCandidate,
    MatchContext,
    MatchQuery,
    MetadataFormat,
    MetadataOrigin,
    QueryVariant,
    QueryVariantKind,
    WorkClass,
)
from manga_list.matcher.normalizer import normalize

D = Decimal


# --- 1. Webtoon(s) category origin (keyed by scoring form) -------------------------------------------------

@pytest.mark.parametrize("hint, expected", [
    ("Webtoon", {MetadataOrigin.Korea, MetadataOrigin.ChinaTaiwan}),
    ("WEBTOONS", {MetadataOrigin.Korea, MetadataOrigin.ChinaTaiwan}),
    ("Manga", {MetadataOrigin.Japan}),
    ("Japanese Manga", {MetadataOrigin.Japan}),
    ("Korean Manhwa", {MetadataOrigin.Korea}),
    ("Chinese Manhua", {MetadataOrigin.ChinaTaiwan}),
    ("Bandes dessinées", {MetadataOrigin.French}),
    ("BD", {MetadataOrigin.French}),
    ("Tebeos", {MetadataOrigin.Spanish}),
    ("Historietas", {MetadataOrigin.Spanish}),
    ("Fumetti", {MetadataOrigin.Italian}),
    ("Stripboeken", {MetadataOrigin.Dutch}),
    ("US Comics", {MetadataOrigin.EnglishOriginal}),
])
def test_category_words_give_their_origins(hint, expected):
    assert set(amt.origins_for_category(hint)) == expected


@pytest.mark.parametrize("hint", ["Comics", "Graphic Novels", "European Comics", "Ongoing", "Doujinshi", None])
def test_words_without_an_origin_give_none(hint):
    assert amt.origins_for_category(hint) is None


def test_new_origins_keep_the_reference_values():
    assert (MetadataOrigin.Italian, MetadataOrigin.Dutch) == (14, 15)


def _manhwa(external_id: str = "1") -> MatchCandidate:
    return MatchCandidate("mangaupdates", external_id, "Some Webtoon", (), MetadataFormat.COMIC, "Manhwa", None,
                          None, None, (), (), None)


def _query(category):
    return MatchQuery((QueryVariant("Some Webtoon", QueryVariantKind.PRIMARY),),
                      MatchContext(WorkClass.SERIES, 5, 0, 5, None, category, False, (), None))


def test_a_webtoons_folder_now_gives_its_origin_to_the_scorer():
    # Before 1.32.0 "webtoons" (scoring form "webtons") never matched the table, so no origin evidence.
    plain = scorer.score(_query(None), [_manhwa()], DEFAULT_THRESHOLDS).ranked[0]
    webtoons = scorer.score(_query("Webtoons"), [_manhwa()], DEFAULT_THRESHOLDS).ranked[0]
    assert webtoons.title_score == plain.title_score
    assert webtoons.adjusted_score == pytest.approx(plain.adjusted_score + scorer.ORIGIN_AGREE)


# --- 2. "No. N" issue numbers and the folder's own "No. N" -------------------------------------------------

@pytest.mark.parametrize("name, issue", [
    ("Saga No. 12.cbz", D(12)),
    ("Saga N°12.cbz", D(12)),
    ("Kaiju No. 8.cbz", D(8)),        # nothing unit-like follows: an issue (the folder rule handles the title)
    ("Robot No. 9 (2020).cbz", D(9)),  # brackets are not read
    ("Robot No. 9.5.cbz", D("9.5")),
    ("No. 6.cbz", None),               # no title text before it: a title
    ("No. 6 v01.cbz", None),
    ("Kaiju No. 8 v01.cbz", None),     # a volume token follows: part of the title
    ("Kaiju No. 8 - Chapter 012.cbz", None),
    ("Kaiju No. 8 c012.cbz", None),
    ("Monster No. 8 v01 c003.cbz", None),
    ("Robot No. 9 001.cbz", None),     # a bare number follows
    ("Saga No. 12 v02.cbz", None),
    ("Some Title v01.cbz", None),
    (None, None),
])
def test_issue_number_of(name, issue):
    assert amt.issue_number_of(name) == issue


def test_a_no_n_issue_is_a_chapter_not_a_volume():
    assert amt.is_chapter_like("Saga No. 12.cbz") and not amt.is_volume_like("Saga No. 12.cbz")
    assert amt.chapter_number_of("Saga No. 12.cbz") == 12
    assert amt.chapter_number_of("Robot No. 9.5.cbz") == 9
    assert amt.bare_number_of("Saga No. 12.cbz") is None
    assert amt.units_of("Saga No. 12.cbz") == amt.UnitNumbers(chapter=D(12))
    assert amt.units_of("Robot No. 9.5.cbz") == amt.UnitNumbers(chapter=D("9.5"), is_extra=True)
    # A title "No. N" next to real units is left alone.
    assert amt.is_volume_like("Kaiju No. 8 v01.cbz") and amt.volume_number_of("Kaiju No. 8 v01.cbz") == 1
    assert amt.units_of("Monster No. 8 v01 c003.cbz") == amt.UnitNumbers(volume=D(1), chapter=D(3))


@pytest.mark.parametrize("archive, folder, expected", [
    ("Robot No. 9.cbz", "Robot No. 9", "Robot No9.cbz"),
    ("Robot No. 09.cbz", "Robot No. 9", "Robot No9.cbz"),       # leading zeros aside
    ("Robot No.9.cbz", "Robot No. 9", "Robot No9.cbz"),
    ("Robot No. 9 - Issue 3.cbz", "Robot No. 9", "Robot No9 - Issue 3.cbz"),
    ("Robot No. 9 No. 3.cbz", "Robot No. 9", "Robot No9 No. 3.cbz"),  # only the folder's own number
    ("Robot No. 10.cbz", "Robot No. 9", "Robot No. 10.cbz"),
    ("Saga No. 12.cbz", "Saga", "Saga No. 12.cbz"),
    ("Robot No. 9.cbz", None, "Robot No. 9.cbz"),
    ("Kaiju No. 8 v01.cbz", "Kaiju No. 8", "Kaiju No8 v01.cbz"),
])
def test_mask_folder_title_number(archive, folder, expected):
    assert amt.mask_folder_title_number(archive, folder) == expected


def test_masked_name_states_no_issue():
    assert amt.units_of(amt.mask_folder_title_number("Robot No. 9.cbz", "Robot No. 9")).is_empty


def _plan(name, archives, category=None):
    folder = FolderShape(name, 2, tuple(archives), (), None, category)
    cls = detector.classify(folder)
    return cls, planner.plan_folder(folder, cls)


def test_planner_reads_a_folders_own_no_n_as_title():
    cls, q = _plan("Robot No. 9", ["Robot No. 9.cbz"])
    assert cls.cls == WorkClass.ONE_SHOT
    ctx = q.context
    assert (ctx.volume_like_count, ctx.chapter_like_count, ctx.local_volumes, ctx.local_chapters) == (0, 0, None, None)
    assert q.variants[0].text == "Robot No. 9"


def test_planner_still_reads_issues_inside_a_no_n_folder():
    _, q = _plan("Robot No. 9", ["Robot No. 9 No. 1.cbz", "Robot No. 9 No. 2.cbz"])
    u = q.context.units
    assert (q.context.chapter_like_count, u.chapter_archives, u.lowest_chapter, u.highest_chapter) == (2, 2, 1, 2)


def test_planner_reads_no_n_issues_of_a_comics_folder():
    _, q = _plan("Saga", ["Saga No. 1.cbz", "Saga No. 2.cbz", "Saga No. 3.cbz"])
    assert (q.context.chapter_like_count, q.context.local_chapters) == (3, 3)


def test_planner_keeps_a_manga_title_number():
    _, q = _plan("Kaiju No. 8", ["Kaiju No. 8 v01.cbz", "Kaiju No. 8 v02.cbz"])
    assert (q.context.volume_like_count, q.context.local_volumes) == (2, 2)
    assert q.variants[0].text == "Kaiju No. 8"


# --- 3. "Library Edition" and 6. comics format words --------------------------------------------------------

@pytest.mark.parametrize("name, primary, hints", [
    ("Saga - Library Edition v1.cbz", "Saga", ("Library Edition",)),
    ("Saga TPB.cbz", "Saga", ("TPB",)),
    ("Saga HC.cbz", "Saga", ("HC",)),
    ("Saga GN.cbz", "Saga", ("GN",)),
    ("Asterix - L'Intégrale 1.cbz", "Asterix - 1", ("L'Intégrale",)),  # the number is no unit token
    ("Some Title Gesamtausgabe 1.cbz", "Some Title 1", ("Gesamtausgabe",)),
    ("Some Title Integraal 1.cbz", "Some Title 1", ("Integraal",)),
    ("Saga hc.cbz", "Saga hc", ()),      # two letters only upper-case
    ("Saga Change.cbz", "Saga Change", ()),
])
def test_edition_and_format_words_leave_the_title(name, primary, hints):
    n = normalize(name)
    assert n.primary == primary
    assert n.edition_hints == hints


def test_planner_drops_format_and_edition_words_from_the_query():
    assert _plan("Saga TPB", ["Saga TPB v01.cbz", "Saga TPB v02.cbz"])[1].variants[0].text == "Saga"
    assert _plan("Some Title - Library Edition",
                 ["Some Title - Library Edition v01.cbz"])[1].variants[0].text == "Some Title"


@pytest.mark.parametrize("word", [
    "Comic Books", "Graphic Novels", "graphic novel", "BD", "Bandes Dessinées", "bande dessinee", "Fumetti", "Tebeos",
    "Historietas", "Stripboeken", "US Comics", "European Comics", "Eurocomics",
])
def test_comics_category_folder_words(word):
    assert amt.is_category_folder_name(word)
    assert amt.is_category_word(word)
    assert not amt.is_author_like(word, require_two_tokens=False)  # never a creator


@pytest.mark.parametrize("word", ["Strips", "Albums", "Webcomics", "Comics and Manga"])
def test_ambiguous_words_are_no_category(word):
    assert not amt.is_category_folder_name(word)


# --- 4. release tags in the anatomy -------------------------------------------------------------------------

@pytest.mark.parametrize("tag, expected", [
    ("c2c", True), ("C2C", True), ("Zone-Empire", True), ("danke-Empire", True), ("Some Group-Empire", True),
    ("TPB", True), ("hc", True), ("GN", True), ("OGN", True), ("Webrip", True),
    ("Some Parody", False), ("Empire", False), ("-Empire", False),
])
def test_release_tags(tag, expected):
    assert is_release_tag(tag) is expected


@pytest.mark.parametrize("name", [
    "[Circle (Artist)] Some Title (c2c).cbz",
    "[Circle] Some Title (Zone-Empire).cbz",
    "Some Title v01 (Digital) (danke-Empire).cbz",
    "[Circle] Some Title (OGN).cbz",
])
def test_release_tags_are_no_parody_and_no_creator(name):
    assert parse(name).parody is None
    hints = amt.creator_hints(name)
    assert not any(h.lower() in ("c2c", "zone-empire", "danke-empire", "ogn") for h in hints)


def test_a_real_parody_group_still_reads():
    assert parse("[Circle] Some Title (Some Parody).cbz").parody == "Some Parody"


# --- 5. unit grammar ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, volume, volume_end", [
    ("Blake et Mortimer - Tome 3.cbz", D(3), None),
    ("Suske en Wiske Deel 12.cbz", D(12), None),
    ("Mortadelo y Filemon Tomo 3.cbz", D(3), None),
    ("Some Series Band 4.cbz", D(4), None),
    ("Some Series Album 4.cbz", D(4), None),
    ("Some Series Livre 2.cbz", D(2), None),
    ("Asterix T03.cbz", D(3), None),
    ("Some Title Tome 1-3.cbz", D(1), D(3)),
    ("Some Series T01-T05.cbz", D(1), D(5)),
    ("Some Series T01 - T05.cbz", D(1), D(5)),
    ("Some Title (Tome 2).cbz", D(2), None),  # only brackets name a unit, a whole word
])
def test_album_tokens_are_volumes(name, volume, volume_end):
    u = amt.units_of(name)
    assert (u.volume, u.volume_end, u.chapter) == (volume, volume_end, None)
    assert amt.is_volume_like(name) and not amt.is_chapter_like(name)


@pytest.mark.parametrize("name", [
    "Asterix t03.cbz",      # T only upper-case
    "Asterix T1234.cbz",    # at most three digits
    "Some Title [T2].cbz",  # a single letter inside brackets is a revision
])
def test_t_tokens_that_are_not_volumes(name):
    assert amt.units_of(name).is_empty


def test_album_words_inside_titles_stay_title_words():
    assert normalize("Brass Band Story v01.cbz").primary == "Brass Band Story"
    assert amt.volume_number_of("Brass Band Story v01.cbz") == 1
    assert normalize("Blake et Mortimer - Tome 3.cbz").primary == "Blake et Mortimer"


@pytest.mark.parametrize("name, chapter, chapter_end", [
    ("Saga Issue 12.cbz", D(12), None),
    ("Saga - Issue #12.cbz", D(12), None),
    ("Saga Issue 12 (2019).cbz", D(12), None),
    ("Saga Issue 3-5.cbz", D(3), D(5)),
])
def test_issue_tokens_are_chapters(name, chapter, chapter_end):
    u = amt.units_of(name)
    assert (u.volume, u.chapter, u.chapter_end, u.is_extra) == (None, chapter, chapter_end, False)
    assert amt.is_chapter_like(name) and not amt.is_volume_like(name)
    assert normalize(name).primary == "Saga"


@pytest.mark.parametrize("name, chapter", [
    ("Saga Annual 2.cbz", D(2)),
    ("Saga Annual #3.cbz", D(3)),
    ("Saga Special #1.cbz", D(1)),
    ("Saga Specials 2.cbz", D(2)),
    ("Saga One-Shot 2.cbz", D(2)),
    ("Saga Free Comic Book Day 2.cbz", D(2)),
])
def test_comics_extras(name, chapter):
    assert amt.units_of(name) == amt.UnitNumbers(chapter=chapter, is_extra=True)


@pytest.mark.parametrize("name", [
    "Saga Annual.cbz",           # no number: nothing to count
    "Saga Annual 2019.cbz",      # a year, not a unit
    "FCBD 2019 - Saga.cbz",
    "Saga One Shot 1.cbz",       # "One Shot" with a space is no marker
    "Annually Yours v01.cbz",    # a word that starts like one
])
def test_not_comics_extras(name):
    assert not amt.units_of(name).is_extra


def test_special_edition_is_an_edition_not_an_extra():
    assert amt.units_of("Saga Special Edition v01.cbz") == amt.UnitNumbers(volume=D(1))
    assert normalize("Saga Special Edition v01.cbz").primary == "Saga"


def test_manga_units_unchanged():
    assert amt.units_of("Some Title v01.cbz") == amt.UnitNumbers(volume=D(1))
    assert amt.units_of("Some Title c012.cbz") == amt.UnitNumbers(chapter=D(12))
    assert amt.units_of("Some Webtoon Episode 45.cbz") == amt.UnitNumbers(chapter=D(45))
    assert amt.units_of("Some Title Episode 3 v01.cbz") == amt.UnitNumbers(volume=D(1))  # 1.31.1 rule kept
    assert amt.units_of("Some Title v02.5.cbz") == amt.UnitNumbers(volume=D("2.5"), is_extra=True)
    assert amt.units_of("001.cbz") == amt.UnitNumbers(chapter=D(1))


def test_planner_counts_album_volumes():
    _, q = _plan("Asterix", ["Asterix T01.cbz", "Asterix T02.cbz"])
    assert (q.context.volume_like_count, q.context.local_volumes) == (2, 2)
    assert q.variants[0].text == "Asterix"


# --- 7. score version ---------------------------------------------------------------------------------------

def test_score_version_is_5():
    # MangaPixer 1.32.0 (MatcherRules.Revision 2): older matches show "older version" until Check MU.
    assert mu_cache.MU_SCORE_VERSION == 5
    assert models.MangaEntry.__dataclass_fields__["mu_score_version"].default == mu_cache.MU_SCORE_VERSION
