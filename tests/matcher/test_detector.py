"""Port of MangaPixer.Core.Tests/Metadata/AutoMatch/WorkDetectorTests.cs (synthetic names only)."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

import pytest

from manga_list.matcher import detector
from manga_list.matcher.contracts import (
    ChildFolderShape,
    ContentSuggestion,
    FolderShape,
    MatchLevel,
    WorkClass,
)


def folder(name: str, archives: Sequence[str], subs: Optional[Sequence[Tuple[str, int]]] = None,
           depth: int = 2, parent: Optional[str] = None,
           known_authors: Optional[Sequence[str]] = None) -> FolderShape:
    return FolderShape(name, depth, tuple(archives),
                       tuple(ChildFolderShape(n, c) for n, c in (subs or ())),
                       parent, None, tuple(known_authors) if known_authors is not None else None)


def numbered(fmt: str, count: int) -> list:
    """C# ``string.Format`` with ``{0:00}`` / ``{0:000}`` written as ``{0:02d}`` / ``{0:03d}``."""
    return [fmt.format(i) for i in range(1, count + 1)]


def test_library_root_is_excluded():
    c = detector.classify(folder("Root", numbered("Some Series v{0:02d}.cbz", 3), depth=0))

    assert c.cls == WorkClass.EXCLUDED
    assert c.level == MatchLevel.NONE


def test_empty_folder_is_excluded():
    assert detector.classify(folder("Nothing", [])).cls == WorkClass.EXCLUDED


def test_titled_leaf_is_series():
    c = detector.classify(folder("Some Series", numbered("Some Series v{0:02d} (2019) (Digital).cbz", 6)))

    assert c.cls == WorkClass.SERIES
    assert c.level == MatchLevel.FOLDER
    assert len(c.archive_groups) == 0


def test_unit_named_leaf_is_series():
    c = detector.classify(folder("Some Series", numbered("{0:03d} [A Chapter Title {0}].cbz", 12)))

    assert c.cls == WorkClass.SERIES


def test_same_base_leaf_under_a_different_folder_name_is_series():
    c = detector.classify(folder("Romaji Name", numbered("English Release Name v{0:02d}.cbz", 5)))

    assert c.cls == WorkClass.SERIES


def test_one_archive_is_one_shot():
    c = detector.classify(folder("A Short Story", ["A Short Story.cbz"]))

    assert c.cls == WorkClass.ONE_SHOT
    assert c.level == MatchLevel.FOLDER


def test_unit_subfolders_make_series_with_units():
    c = detector.classify(folder("Some Series", numbered("Some Series v{0:02d}.cbz", 3),
                                 [("Chapters", 40), ("Extras", 2)]))

    assert c.cls == WorkClass.SERIES_WITH_UNITS
    assert c.level == MatchLevel.FOLDER


@pytest.mark.parametrize("name", [
    "Volumes",
    "Chapters 1-50",
    "Season 2",
    "Part 3",
    "12",
    "Side Stories",
])
def test_unit_named_folder_below_a_non_root_parent_is_unit_sub(name):
    c = detector.classify(folder(name, numbered("{0:03d}.cbz", 3), depth=3))

    assert c.cls == WorkClass.UNIT_SUB
    assert c.level == MatchLevel.NONE


def test_part_with_subtitle_is_not_a_unit_it_is_a_separate_work():
    c = detector.classify(folder("Part 3 - A Subtitle", numbered("Part 3 - A Subtitle v{0:02d}.cbz", 4), depth=3))

    assert c.cls == WorkClass.SERIES


def test_related_subfolders_make_a_franchise_container():
    c = detector.classify(folder("Some Franchise", [],
                                 [("Some Franchise Part 1 - First Arc", 10), ("Some Franchise Part 2 - Second Arc", 12),
                                  ("Some Franchise Gaiden", 3)]))

    assert c.cls == WorkClass.FRANCHISE_CONTAINER
    assert c.level == MatchLevel.NONE


def test_unrelated_subfolders_make_a_collection_container():
    c = detector.classify(folder("Manga", [], [("Alpha Story", 10), ("Beta Tale", 12), ("Gamma Saga", 3)], depth=1))

    assert c.cls == WorkClass.COLLECTION_CONTAINER
    assert c.level == MatchLevel.NONE


def test_one_subfolder_no_archives_is_wrapper_and_with_archives_is_mixed():
    assert detector.classify(folder("Outer", [], [("Inner Series", 5)])).cls == WorkClass.WRAPPER

    # A loose titled archive next to one subfolder is its own work: matched one by one (owner, 2026-09-26).
    mixed = detector.classify(folder("Outer", ["Loose One.cbz"], [("Inner Series", 5)]))
    assert mixed.cls == WorkClass.MIXED
    assert mixed.level == MatchLevel.ARCHIVE
    assert len(mixed.archive_groups) == 1


def test_mixed_with_loose_units_of_one_work_stays_review_only():
    c = detector.classify(folder("Some Series", numbered("Some Series v{0:02d}.cbz", 4), [("Side Story Title", 2)]))

    assert c.cls == WorkClass.MIXED
    assert c.level == MatchLevel.REVIEW_ONLY
    assert len(c.archive_groups) == 0


def test_container_loose_one_shots_are_matched_one_by_one_its_subfolders_stay_candidates():
    c = detector.classify(folder("Manga", ["Short Story.cbz", "Another Tale.cbz"],
                                 [("Alpha Story", 10), ("Beta Tale", 12), ("Gamma Saga", 3)], depth=1))

    assert c.cls == WorkClass.COLLECTION_CONTAINER
    assert c.level == MatchLevel.ARCHIVE
    assert len(c.archive_groups) == 2


def test_container_one_loose_archive_is_its_own_work_even_a_whole_series_in_one_file():
    c = detector.classify(folder("Manga", ["Complete Series Omnibus.cbz"], [("Alpha Story", 10), ("Beta Tale", 12)],
                                 depth=1))

    assert c.level == MatchLevel.ARCHIVE
    assert len(c.archive_groups) == 1


def test_container_loose_volumes_of_one_work_are_not_matched_one_by_one():
    c = detector.classify(folder("Manga", numbered("Some Series v{0:02d}.cbz", 3), [("Alpha Story", 10), ("Beta Tale", 12)],
                                 depth=1))

    assert c.level == MatchLevel.NONE
    assert len(c.archive_groups) == 0


def test_empty_subfolders_are_ignored():
    c = detector.classify(folder("Some Series", numbered("Some Series v{0:02d}.cbz", 3), [("Empty", 0), ("Also Empty", 0)]))

    assert c.cls == WorkClass.SERIES


def test_doujins_artists_tree_only_the_artist_folders_are_matched_archive_by_archive():
    # Doujins/ -> Artists/ -> <artist>/ -> archives: the top two levels are never matched themselves.
    top = detector.classify(folder("Doujins", [], [("Artists", 9)], depth=1))
    artists = detector.classify(folder("Artists", [], [("Artist One", 3), ("Artist Two", 3), ("Artist Three", 3)], depth=2))
    # Named after the creator tag its archives carry.
    tagged = detector.classify(folder("Artist One",
                                      ["(C99) [Artist One] First Story (Parody A).cbz", "[Artist One] Second Story.cbz",
                                       "(C101) [Artist One] Third Night (Parody B).cbz"], depth=3))
    # Archives that carry only a circle name: still one work per archive (distinct titles).
    circle = detector.classify(folder("Artist Two",
                                      ["[Some Circle] Alpha Story.cbz", "[Some Circle] Beta Tale.cbz",
                                       "[Some Circle] Gamma Saga.cbz", "[Some Circle] Delta Night.cbz",
                                       "[Some Circle] Epsilon Dawn.cbz"], depth=3))

    assert (top.cls, top.level) == (WorkClass.WRAPPER, MatchLevel.NONE)
    assert (artists.cls, artists.level) == (WorkClass.COLLECTION_CONTAINER, MatchLevel.NONE)
    assert (tagged.cls, tagged.level) == (WorkClass.ARTIST_COLLECTION, MatchLevel.ARCHIVE)
    assert len(tagged.archive_groups) == 3
    assert circle.level == MatchLevel.ARCHIVE
    assert len(circle.archive_groups) == 5


def test_distinct_titles_are_a_collection_leaf_matched_per_archive():
    c = detector.classify(folder("Anthology Shelf",
                                 ["Alpha Story.cbz", "Beta Tale.cbz", "Gamma Saga.cbz", "Delta Night.cbz", "Epsilon Dawn.cbz"]))

    assert c.cls == WorkClass.COLLECTION_LEAF
    assert c.level == MatchLevel.ARCHIVE
    assert len(c.archive_groups) == 5
    assert all(len(g.archive_indexes) == 1 for g in c.archive_groups)


def test_numbered_mini_series_are_grouped_not_matched_archive_by_archive():
    c = detector.classify(folder("Anthology Shelf",
                                 ["Alpha Story 1.cbz", "Alpha Story 2.cbz", "Alpha Story 3.cbz", "Beta Tale.cbz",
                                  "Gamma Saga.cbz", "Delta Night.cbz", "Epsilon Dawn 2.cbz"]))

    assert c.cls == WorkClass.COLLECTION_LEAF
    alphas = [g for g in c.archive_groups if g.query_title == "Alpha Story"]
    assert len(alphas) == 1
    assert list(alphas[0].archive_indexes) == [0, 1, 2]
    # A lone numbered archive keeps its number in the query (it is part 2 of something).
    assert any(g.query_title == "Epsilon Dawn 2" and list(g.archive_indexes) == [6] for g in c.archive_groups)
    assert len(c.archive_groups) == 5


def test_neither_series_nor_collection_is_ambiguous_review_only():
    # Half one base, half distinct titles.
    c = detector.classify(folder("Some Shelf",
                                 ["Alpha Story 1.cbz", "Alpha Story 2.cbz", "Alpha Story 3.cbz", "Beta Tale.cbz",
                                  "Gamma Saga.cbz", "Delta Night.cbz"]))

    assert c.cls == WorkClass.AMBIGUOUS
    assert c.level == MatchLevel.REVIEW_ONLY


def test_subtitled_names_sharing_one_head_are_ambiguous():
    c = detector.classify(folder("Some Shelf",
                                 ["Some Head - First Tale.cbz", "Some Head - Second Tale.cbz", "Some Head - Third Tale.cbz",
                                  "Some Head - Fourth Tale.cbz"]))

    assert c.cls == WorkClass.AMBIGUOUS


def test_folder_named_like_the_dominant_artist_tag_is_an_artist_collection():
    # E6: artist folders whose archives repeat the artist read as "titled series" when bracket
    # text counts as coherence. Here every archive also carries a trailing two-word
    # [Some Artist] group, which the stage-1 normalizer would take as an English variant.
    c = detector.classify(folder("Some Artist", [
        "[Some Artist] Alpha Story.cbz",
        "[Some Artist] Beta Tale [Some Artist].cbz",
        "(Event 3) [Some Circle (Some Artist)] Gamma Saga (Some Parody).cbz",
        "[Some Artist] Alpha Story 2.cbz",
    ]))

    assert c.cls == WorkClass.ARTIST_COLLECTION
    assert c.level == MatchLevel.ARCHIVE
    assert any(g.query_title == "Alpha Story" and list(g.archive_indexes) == [0, 3] for g in c.archive_groups)


def test_folder_named_like_a_known_provider_author_is_an_artist_collection():
    c = detector.classify(folder("Given Family", ["Alpha Story.cbz", "Beta Tale.cbz"], known_authors=["FAMILY Given"]))

    assert c.cls == WorkClass.ARTIST_COLLECTION


def test_category_word_folder_is_never_an_artist_folder():
    c = detector.classify(folder("Manga", ["[Manga] Alpha Story.cbz", "[Manga] Beta Tale.cbz"], known_authors=["Manga"]))

    assert c.cls != WorkClass.ARTIST_COLLECTION


def test_series_named_like_a_tag_on_few_archives_stays_series():
    c = detector.classify(folder("Some Series",
                                 ["[Some Series] Extra.cbz", "Some Series v01.cbz", "Some Series v02.cbz",
                                  "Some Series v03.cbz", "Some Series v04.cbz"]))

    assert c.cls == WorkClass.SERIES


def test_doujin_shaped_archives_suggest_the_content_setting():
    c = detector.classify(folder("Some Shelf", [
        "(Event 1) [Circle One (Artist One)] Alpha Story (Parody A).cbz",
        "(Event 2) [Circle Two (Artist Two)] Beta Tale (Parody B).cbz",
        "[Circle Three (Artist Three)] Gamma Saga (Parody C).cbz",
    ]))

    assert c.content_suggestion == ContentSuggestion.DOUJINSHI_AND_ADULT_ONE_SHOTS
    assert c.cls == WorkClass.COLLECTION_LEAF


def test_scanlation_names_do_not_suggest_doujin_content():
    c = detector.classify(folder("Some Series", numbered("[Scan Team] Some Series v{0:02d} (Digital) (Group).cbz", 4)))

    assert c.content_suggestion == ContentSuggestion.NONE


def test_reasons_carry_no_names():
    c = detector.classify(folder("Secret Folder Name", ["Secret Archive One.cbz", "Hidden Archive Two.cbz", "Private Three.cbz"]))

    for r in c.reasons:
        assert "secret" not in r.lower()
        assert "hidden" not in r.lower()
        assert "private" not in r.lower()


def test_numbered_chapters_with_subtitles_are_one_series_not_a_collection():
    # "<Title> 025 <chapter subtitle>" (1.26.1): every base differs, the head does not.
    subtitled = [f"Cloud Flower {i:03d} Title Word{i} Other{i % 7}.cbz" for i in range(1, 26)]
    c = detector.classify(folder("Kumo no Hana Senpai [Cloud Flower]", subtitled, depth=1))
    zero = detector.classify(folder("Steel Rider", ["Steel Rider 000 Oneshot.cbz", "Steel Rider 001 Rise.cbz",
                                                    "Steel Rider 002 Iron Fire!.cbz", "Steel Rider 006 HQ Version.cbz"],
                                    depth=1))
    shelf = detector.classify(folder("Anthology Shelf", ["Alpha Story.cbz", "Beta Tale.cbz", "Gamma Saga.cbz",
                                                         "Delta Night.cbz", "Epsilon Dawn.cbz"]))

    assert (c.cls, c.level) == (WorkClass.SERIES, MatchLevel.FOLDER)
    assert (zero.cls, zero.level) == (WorkClass.SERIES, MatchLevel.FOLDER)
    assert shelf.cls == WorkClass.COLLECTION_LEAF
