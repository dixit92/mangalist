"""Port of MangaPixer ``tests/MangaPixer.Core.Tests/Metadata/AutoMatch/MatchQueryPlannerTests.cs``."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from mangalist.matcher import detector, planner
from mangalist.matcher.contracts import (
    ChildFolderShape,
    FolderShape,
    MatchQuery,
    QueryVariantKind,
    WorkClass,
)


def folder(name: str, archives: Sequence[str], subs: Optional[Sequence[Tuple[str, int]]] = None,
           parent: Optional[str] = None, category: Optional[str] = None) -> FolderShape:
    return FolderShape(name, 2, tuple(archives), tuple(ChildFolderShape(n, c) for n, c in (subs or ())),
                       parent, category)


def plan_folder(f: FolderShape, comic_info: Optional[str] = None) -> MatchQuery:
    return planner.plan_folder(f, detector.classify(f), comic_info)


def test_variants_are_ordered_by_kind_and_deduplicated():
    q = plan_folder(
        folder("Some Long Series 2 - The Return [English Name Here]",
               ["English Name Here v01.cbz", "English Name Here v02.cbz", "Archive Only Name v03.cbz",
                "Archive Only Name v04.cbz", "Archive Only Name v05.cbz"]),
        comic_info="Some Long Series 2 - The Return")

    assert [(v.text, v.kind) for v in q.variants] == [
        ("Some Long Series 2 - The Return", QueryVariantKind.COMIC_INFO_SERIES),
        ("English Name Here", QueryVariantKind.ENGLISH_TITLE),
        ("Some Long Series 2", QueryVariantKind.SUBTITLE_SPLIT),
        ("Some Long Series", QueryVariantKind.SEQUEL_NUMBER_SPLIT),
        ("Archive Only Name", QueryVariantKind.ARCHIVE_DERIVED_TITLE),
    ]


def test_variants_dedupe_ignores_case_and_punctuation():
    q = plan_folder(folder("Re:Zero Story", ["Re Zero Story v01.cbz", "re zero story v02.cbz"]))

    assert len(q.variants) == 1
    assert q.variants[0].kind == QueryVariantKind.PRIMARY


def test_context_counts_volumes_and_chapters_separately_including_unit_subfolders():
    q = plan_folder(folder("Some Series",
                           ["Some Series v01 (2011).cbz", "Some Series v02 (2012).cbz", "Some Series Ch 015.cbz"],
                           [("Chapters", 30), ("Volumes 3-5", 3), ("Extras", 2)], category="Manga"))

    assert q.context.cls == WorkClass.SERIES_WITH_UNITS
    assert q.context.archive_count == 3 + 30 + 3 + 2
    assert q.context.volume_like_count == 2 + 3
    assert q.context.chapter_like_count == 1 + 30
    assert q.context.earliest_year == 2011
    assert q.context.category_hint == "Manga"


def test_context_unit_named_archives_are_chapter_like():
    q = plan_folder(folder("Some Series", ["001 [First].cbz", "002 [Second].cbz", "003 [Third].cbz"]))

    assert q.context.chapter_like_count == 3
    assert q.context.volume_like_count == 0


def test_context_author_like_parent_is_a_tie_break_tag_category_parent_is_not():
    assert "Given Family" in plan_folder(
        folder("Some Series", ["Some Series v01.cbz", "Some Series v02.cbz"], parent="Given Family")).context.author_tags
    assert len(plan_folder(
        folder("Some Series", ["Some Series v01.cbz", "Some Series v02.cbz"], parent="Manga")).context.author_tags) == 0


def _single(items, predicate):
    matches = [x for x in items if predicate(x)]
    assert len(matches) == 1
    return matches[0]


def test_archive_group_of_an_artist_collection_carries_the_artist_and_the_parody_form():
    f = folder("Some Artist", [
        "(Event 1) [Some Circle (Some Artist)] Alpha Story (Parody Name).cbz",
        "[Some Artist] Beta Tale.cbz",
        "[Some Artist] Gamma Saga 1.cbz",
        "[Some Artist] Gamma Saga 2.cbz",
    ])
    c = detector.classify(f)
    assert c.cls == WorkClass.ARTIST_COLLECTION

    alpha = planner.plan_archive_group(f, c, _single(c.archive_groups, lambda g: g.query_title == "Alpha Story"))
    assert (alpha.variants[0].text, alpha.variants[0].kind) == ("Alpha Story", QueryVariantKind.PRIMARY)
    assert any(v.kind == QueryVariantKind.DOUJIN_PARODY_FORM and v.text == "Parody Name dj - Alpha Story"
               for v in alpha.variants)
    assert "Some Circle" in alpha.context.author_tags
    assert "Some Artist" in alpha.context.author_tags
    assert alpha.context.archive_count == 1

    gamma = planner.plan_archive_group(f, c, _single(c.archive_groups, lambda g: g.query_title == "Gamma Saga"))
    assert gamma.context.archive_count == 2
    assert list(gamma.context.author_tags) == ["Some Artist"]


def test_one_shot_folder_adds_the_archive_title():
    q = plan_folder(folder("Folder Label", ["[Some Artist] Actual Story Title.cbz"]))

    assert q.context.cls == WorkClass.ONE_SHOT
    assert any(v.kind == QueryVariantKind.ARCHIVE_DERIVED_TITLE and v.text == "Actual Story Title"
               for v in q.variants)


def test_plan_is_deterministic():
    f = folder("Some Series [English Name Here]", ["English Name Here v01.cbz", "English Name Here v02.cbz"])

    assert plan_folder(f).variants == plan_folder(f).variants


def test_loose_archive_group_in_a_container_is_scored_as_a_collection_work():
    f = FolderShape("Manga", 1, ("Short Story.cbz",),
                    (ChildFolderShape("Alpha Story", 10), ChildFolderShape("Beta Tale", 12)))
    c = detector.classify(f)

    assert len(c.archive_groups) == 1
    q = planner.plan_archive_group(f, c, c.archive_groups[0])

    assert c.cls == WorkClass.COLLECTION_CONTAINER
    assert q.context.cls == WorkClass.COLLECTION_LEAF


def test_folder_with_english_title_and_creator_searches_both_and_carries_the_creator_hint():
    q = plan_folder(folder("Tsunagu Te [Joined Hands] (Family Given)", ["Tsunagu Te v01.cbz", "Tsunagu Te v02.cbz"]))

    assert any(v.text == "Joined Hands" and v.kind == QueryVariantKind.ENGLISH_TITLE for v in q.variants)
    assert "Family Given" in q.context.creator_hints


# --- 1.27.0 / 1.29.0 (ported from MangaPixer 1.31.1 MatchQueryPlannerTests) ---------------------

def test_archive_title_that_extends_the_folder_name_is_the_second_search():
    # The folder is the leading words of a long title; the archives carry the whole title.
    q = plan_folder(folder("Alpha to Beta Gamma [Some English Name]",
                           ["Alpha to Beta Gamma Delta Epsilon Zeta v01.cbz", "Alpha to Beta Gamma Delta Epsilon Zeta v02.cbz"]))

    assert [(v.text, v.kind) for v in q.variants] == [
        ("Alpha to Beta Gamma", QueryVariantKind.PRIMARY),
        ("Alpha to Beta Gamma Delta Epsilon Zeta", QueryVariantKind.ARCHIVE_DERIVED_TITLE),
        ("Some English Name", QueryVariantKind.ENGLISH_TITLE),
    ]


def test_context_local_units_are_the_highest_numbers_and_unit_subfolder_counts():
    names = [f"Some Series v0{i}.cbz" for i in range(1, 7)] + [f"Some Series v0{i}.5.cbz" for i in range(1, 7)]
    q = plan_folder(folder("Some Series", names))
    assert q.context.volume_like_count == 12
    assert q.context.local_volumes == 6
    assert q.context.local_chapters is None

    # 1.29.0: a unit subfolder adds the numbers its archive names state, never its archive count.
    units = plan_folder(FolderShape("Some Series", 2, (), (
        ChildFolderShape("Volumes", 3, ("Some Series v09.cbz", "Some Series v10.cbz", "Some Series v10.5.cbz")),
        ChildFolderShape("Chapters", 80, tuple(f"Some Series - Chapter {i}.cbz" for i in range(101, 181))),
    )))
    assert units.context.local_volumes == 10
    assert units.context.local_chapters == 180
    u = units.context.units
    assert (u.volume_archives, u.chapter_archives, u.lowest_volume, u.lowest_chapter) == (3, 80, 9, 101)

    # Without the names only a range the subfolder's own name states counts (never the 11 archives).
    counted = plan_folder(folder("Some Series", [], subs=[("Volumes", 11), ("Chapters 1-50", 80)]))
    assert counted.context.local_volumes is None
    assert counted.context.local_chapters == 50
