"""Port of MangaPixer.Core.Tests/Metadata/AutoMatch/ArchiveNameAnatomyTests.cs (synthetic names only)."""

from __future__ import annotations

from manga_list.matcher import anatomy


def test_parse_full_doujin_anatomy():
    a = anatomy.parse("(Event 12) [Some Circle (Some Artist)] A Short Story (Some Parody) [English] [Digital].zip")

    assert a.event == "Event 12"
    assert a.circle == "Some Circle"
    assert a.artist == "Some Artist"
    assert a.title == "A Short Story"
    assert a.parody == "Some Parody"
    assert a.has_unit_token is False
    assert a.is_doujin_shaped is True
    assert list(a.creator_tags) == ["Some Circle", "Some Artist"]


def test_parse_lone_tag_is_a_creator_tag_but_not_doujin_without_parody():
    a = anatomy.parse("[Some Artist] A Short Story.cbz")

    assert a.leading_tag == "Some Artist"
    assert a.circle is None
    assert list(a.creator_tags) == ["Some Artist"]
    assert a.is_doujin_shaped is False


def test_parse_scene_release_is_not_doujin():
    a = anatomy.parse("[Group] Some Series v01 (2019) (Digital) (Scan Team).cbz")

    assert a.has_unit_token is True
    assert a.is_doujin_shaped is False


def test_parse_year_and_release_tags_are_never_a_parody():
    a = anatomy.parse("[Some Artist] A Short Story (2015) (Decensored) (Colorized).cbz")

    assert a.parody is None


def test_parse_multiple_artists_are_split():
    a = anatomy.parse("[Circle Name (First Artist, Second Artist)] Story.cbz")

    assert list(a.creator_tags) == ["Circle Name", "First Artist", "Second Artist"]


def test_parse_plain_name_has_no_anatomy():
    a = anatomy.parse("Some Series 03.cbz")

    assert a.leading_tag is None
    assert a.event is None
    assert len(a.creator_tags) == 0
    assert a.is_doujin_shaped is False
