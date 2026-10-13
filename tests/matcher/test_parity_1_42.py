"""MangaPixer 1.34.0-1.42.0 parity: the matcher rules ported from ``v1.42.0`` (``4efb3028``) - dated doujin names
(1.34.2), the site watermark class, three decimals with a padded sub-number and placeholder groups as release tags
(1.42.0). 1.34.0's index-prefixed chapters and 1.39.0's author aliases are pinned in ``test_auto_match_text`` /
``test_scorer`` / ``test_detector`` beside the reference's own rows; ``tests/golden/test_name_probe.py`` compares a
wider set of names with MangaPixer's own output line by line. Synthetic names only."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mangalist.matcher import auto_match_text as amt
from mangalist.matcher import dated_doujin, detector, planner, scorer
from mangalist.matcher.anatomy import is_release_tag, parse
from mangalist.matcher.contracts import (
    DEFAULT_THRESHOLDS,
    FolderShape,
    MatchBand,
    MatchCandidate,
    MetadataFormat,
    QueryVariantKind,
)
from mangalist.matcher.normalizer import archive_base_title, normalize

D = Decimal


def _u(volume="", chapter="", extra=False, chapter_end=""):
    return amt.UnitNumbers(D(volume) if volume else None, None, D(chapter) if chapter else None,
                           D(chapter_end) if chapter_end else None, extra)


# --- 1.34.2: dated doujin names --------------------------------------------------------------------------------------

def test_dated_name_character_tag_title_the_last_group_is_the_title():
    d = dated_doujin.try_parse("Artist Name] [2023-05] Character Name (Some Tag) (The Real Title).cbz")
    assert d == dated_doujin.DatedDoujinName("Artist Name", "The Real Title", "Character Name", ("Some Tag",))


@pytest.mark.parametrize("name", [
    "[Artist Name] [2023-05] Character Name (The Real Title).cbz",
    "Artist Name] [2023-05-17] Character Name (The Real Title).cbz",
    "Artist Name] [2019] Character Name (The Real Title).cbz",
    "(Event 5) [Artist Name] [2023-05] Character Name (The Real Title).cbz",
    "Artist Name] [2023-05] Character Name (Some Tag) (The Real Title) (x1600) [Decensored] [English].cbz",
])
def test_every_date_form_and_both_tag_forms_are_read_and_release_tags_are_never_the_title(name):
    assert dated_doujin.try_parse(name).title == "The Real Title"


@pytest.mark.parametrize("name, title", [
    ("Artist] [2023-05] Character (Tag) (The Real Title) - Part 2.cbz", "The Real Title - Part 2"),
    ("Artist] [2023-05] Character (The Real Title) 2 (x1600).cbz", "The Real Title 2"),
    ("Artist] [2023-05] Character (The Real Title (Part 2)).cbz", "The Real Title Part 2"),
])
def test_text_after_the_title_and_a_nested_group_belong_to_it(name, title):
    assert dated_doujin.try_parse(name).title == title


@pytest.mark.parametrize("name", [
    "Artist Name] [2023-05] Character Name (x1600).cbz",
    "Artist Name] [2023-05] Character Name.cbz",
    "[Artist Name] [2023-05] Character Name [Decensored].cbz",
])
def test_no_title_group_has_no_title_and_reads_as_before(name):
    d = dated_doujin.try_parse(name)
    assert (d.title, d.character) == (None, "Character Name")
    assert normalize(name).primary == "Character Name"


@pytest.mark.parametrize("name", [
    "[Artist Name] Some Title (Some Parody).cbz",            # the common convention: no date
    "[Artist Name] Some Title (Some Parody) [2023-05].cbz",  # a date elsewhere is not the gate
    "[Artist Name] (2023) Some Title (Some Parody).cbz",     # a year group is not a bracketed date
    "[Artist Name] [2023-13] Character (Title).cbz",         # no month 13
    "[123] [2023-05] Character (Title).cbz",                 # a creator tag has letters
    "Some Series v01 [2023-05] (Title).cbz",
    "",
    None,
])
def test_other_names_are_not_dated(name):
    assert dated_doujin.try_parse(name) is None


def test_the_cleaned_title_and_the_base_title_are_the_title_not_the_character():
    assert normalize("Artist Name] [2023-05] Character Name (Some Tag) (The Real Title).cbz").primary == "The Real Title"
    assert any(x.text == "The Real Title" for x in normalize("Artist] [2023-05] Character (The Real Title) - Part 2.cbz").derived)
    assert archive_base_title("Artist] [2023-06] Character (Tag) (The Real Title 2).cbz") == "The Real Title"
    assert normalize("[Artist Name] Some Title (Some Parody) [English].cbz").primary == "Some Title"   # as before


def test_anatomy_a_dated_name_is_doujin_shaped_with_its_creator_and_no_parody():
    a = parse("Circle Name (Artist Name)] [2023-05] Character Name (Some Tag) (The Real Title).cbz")
    assert (a.is_dated, a.is_doujin_shaped, a.leading_tag, a.circle, a.artist, a.parody, a.title) == (
        True, True, "Circle Name (Artist Name)", "Circle Name", "Artist Name", None, "The Real Title")
    assert parse("[Artist Name] Some Title (Some Parody).cbz").parody == "Some Parody"                 # as before
    assert not parse("Group Name] [2023-05] Some Series Ch. 12 (Some Title).cbz").is_doujin_shaped


_ONE_SHOT = FolderShape("Artist] [2023-05] Hero Name (Some Tag) (First Story)", 2,
                        ("Artist] [2023-05] Hero Name (Some Tag) (First Story).cbz",), ())


def test_the_planner_searches_the_title_first_and_the_character_last():
    plan = planner.plan_folder(_ONE_SHOT, detector.classify(_ONE_SHOT))
    assert plan.variants[0].text == "First Story"
    assert (plan.variants[-1].text, plan.variants[-1].kind) == ("Hero Name", QueryVariantKind.CHARACTER_NAME)
    undated = FolderShape("[Artist Name] Some Title (Some Parody)", 2, ("[Artist Name] Some Title (Some Parody).cbz",), ())
    assert all(v.kind != QueryVariantKind.CHARACTER_NAME
               for v in planner.plan_folder(undated, detector.classify(undated)).variants)


def _rec(id_, title, fmt=MetadataFormat.DOUJINSHI):
    return MatchCandidate("mangaupdates", id_, title, (), fmt, "Manga", None, None, None, (), (), None)


def test_a_record_named_after_the_character_is_review_only_and_the_title_still_links():
    plan = planner.plan_folder(_ONE_SHOT, detector.classify(_ONE_SHOT))
    o = scorer.score(plan, [_rec("1", "Hero Name", MetadataFormat.COMIC)], DEFAULT_THRESHOLDS)
    assert o.ranked[0].title_score == pytest.approx(scorer.SUBTITLE_HEAD_CAP, abs=5e-4)
    assert o.band != MatchBand.AUTO
    o = scorer.score(plan, [_rec("1", "First Story"), _rec("2", "Hero Name", MetadataFormat.COMIC)], DEFAULT_THRESHOLDS)
    assert o.ranked[0].candidate.external_id == "1"


# --- 1.42.0: the site watermark class --------------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Some Series Read Online", "Some Series"),
    ("Some Series - Read Manga Online Free", "Some Series"),
    ("Some Series Online Free", "Some Series"),
    ("Some Series Read Free.cbz", "Some Series"),
    ("Some Series Read Manhwa Online", "Some Series"),
    ("Some Series Read Online for Free", "Some Series"),
    ("Some Series Free Online", "Some Series"),
    ("Some_Series_Read_Online", "Some Series"),
])
def test_a_watermark_is_not_part_of_the_search_text(name, expected):
    assert normalize(name).primary == expected


@pytest.mark.parametrize("name", [
    "Sword Art Online", "Some Series Online", "Some Series Manga Online", "Some Series Free", "Read or Die",
    "Online Free", "Free Online Stories",
])
def test_a_watermark_never_eats_a_title(name):
    assert normalize(name).primary == name


def test_a_watermark_is_not_part_of_the_base_title_and_hides_no_chapter():
    assert archive_base_title("Some Series 012 - Read Manga Online Free.cbz") == "Some Series"
    assert amt.units_of("001 Read Online.cbz") == _u(chapter="1")
    assert amt.units_of("Some Series - Some Series Ch.005 Read Online.cbz") == _u(chapter="5")


# --- 1.42.0: three decimals, a padded sub-number ----------------------------------------------------------------------

@pytest.mark.parametrize("name, chapter", [
    ("Some Series Ch.014.005.cbz", "14.5"),     # padded to three digits like the number before it: .5
    ("Some Series - Some Series Ch.021.005 Read Online.cbz", "21.5"),
    ("Some Series Ch.021.010.cbz", "21.1"),
    ("Some Series Ch.014.125.cbz", "14.125"),   # three digits not starting with 0: as written
    ("Some Series c014.25.cbz", "14.25"),
    ("Some Series Ch.014.5.cbz", "14.5"),
    ("0015 [0014.005 - A Title].cbz", "14.5"),
])
def test_three_decimals_are_kept_not_truncated_to_the_whole_chapter(name, chapter):
    assert amt.units_of(name) == _u(chapter=chapter, extra=True)


def test_a_zero_fraction_is_the_whole_chapter_and_the_matcher_integers_stay():
    assert amt.units_of("Some Series Ch.014.000.cbz") == _u(chapter="14")
    assert amt.chapter_number_of("Some Series Ch.014.005.cbz") == 14
    assert amt.units_of("Some Series v02.005.cbz") == amt.UnitNumbers(D("2.5"), None, None, None, True)


# --- 1.42.0: placeholder groups are release tags ----------------------------------------------------------------------

@pytest.mark.parametrize("tag, expected", [
    ("no group", True), ("No Group", True), ("no-group", True), ("No Groups", True), ("no scanlator", True),
    ("Unknown", True), ("unknown group", True), ("N/A", True), ("none", True), ("unofficial", True),
    ("Unofficial", True), ("No Group Scans", False), ("Unofficial Scans", False), ("Nobody", False),
    ("The None Team", False), ("Official", False),
])
def test_placeholder_groups_are_release_tags(tag, expected):
    assert is_release_tag(tag) == expected


def test_placeholder_groups_are_no_creator_and_no_parody():
    assert amt.creator_hints("Some Title [no group].cbz") == ()
    assert amt.creator_hints("Some Title (Unofficial).cbz") == ()
    a = parse("[Some Circle] Some Title (Unofficial).cbz")
    assert a.parody is None and not a.is_doujin_shaped
