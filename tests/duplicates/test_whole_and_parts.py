"""A whole chapter held with its parts in one folder is listed in the Duplicates view as an overlap to review (owner,
2026-10-18: "Duplicates: flag overlaps"): the whole ``N`` stands in for a missing ``N.1`` (keep both), or may be
redundant next to a held ``N.1``. Counted like a number held more than once; nothing is deleted. Made-up names."""

from __future__ import annotations

import os

from mangalist.duplicates import find_duplicate_files
from mangalist.gui.shell import OVERLAP_REDUNDANT, OVERLAP_STAND_IN
from mangalist.process_pool import count_by_folder

S = "Example Series"


def build(lib, *stems):
    for stem in stems:
        lib.add(S, f"{stem}.cbz")
    return lib.scan()


def overlaps(db):
    return [g for g in find_duplicate_files(db) if g.overlap]


def test_a_whole_chapter_next_to_its_later_parts_stands_in_for_the_missing_first_part(db, lib):
    # the owner's case: parts to 37.2, then a whole 38 and 38.2 from the two groups (38.1 on no source)
    build(lib, "Series c037.1", "Series c037.2", "Series c038", "Series c038.2", "Series c039.1")
    (g,) = find_duplicate_files(db)
    assert (g.kind, g.number, g.overlap) == ("chapter", "38", OVERLAP_STAND_IN)
    assert [os.path.basename(f.path) for f in g.files] == ["Series c038.cbz", "Series c038.2.cbz"]
    assert g.note == ("Whole chapter 38 and the part 38.2: the whole chapter 38 stands in for the missing part 38.1 - "
                      "keep both.")


def test_a_whole_chapter_next_to_its_first_part_may_be_redundant(db, lib):
    build(lib, "Series c038", "Series c038.1", "Series c038.2")
    (g,) = overlaps(db)
    assert (g.number, g.overlap, len(g.files)) == ("38", OVERLAP_REDUNDANT, 3)
    assert "part 38.1 is held too, so the whole chapter 38 may be redundant" in g.note


def test_an_extra_next_to_a_whole_chapter_is_no_overlap(db, lib):
    # the split model: 10.5 (no 10.4), 11.25, a lone 12.1 next to 12 and a run from 13.3 are extras, and so is a file
    # whose name says it is one
    build(lib, "Series c010", "Series c010.5", "Series c011", "Series c011.25", "Series c012", "Series c012.1",
          "Series c013", "Series c013.3", "Series c014", "Series c014.1 (Extra 1)", "Series c014.2 (Extra 2)",
          "Series c015", "Series c015.1 (Special)", "Series c015.2 (Omake)",
          "Series c016", "Series c016.1 (Extra)", "Series c016.2")    # 16.1 is held: 16 stands in for nothing
    assert find_duplicate_files(db) == []


def test_parts_without_a_whole_chapter_are_no_overlap(db, lib):
    build(lib, "Series c037.1", "Series c037.2", "Series c038.2")
    assert find_duplicate_files(db) == []


def test_a_whole_chapter_held_twice_is_the_duplicate_first(db, lib):
    build(lib, "Series c038", "Series c038 [2]", "Series c038.2")
    groups = find_duplicate_files(db)
    assert [(g.number, g.overlap, len(g.files)) for g in groups] == [("38", "", 2)]


def test_overlaps_count_like_numbers_held_more_than_once(db, lib):
    build(lib, "Series c001", "Series c001 [2]", "Series c038", "Series c038.2", "Series c040", "Series c040.1",
          "Series c040.2")
    groups = find_duplicate_files(db)
    assert [(g.number, g.overlap) for g in groups] == [("1", ""), ("38", OVERLAP_STAND_IN), ("40", OVERLAP_REDUNDANT)]
    total, per_folder = count_by_folder(groups)
    assert total == 3 and per_folder == {str(lib.dir / S): (0, 3)}


def test_each_folder_is_compared_on_its_own(db, lib):
    for rel in ("Season 1/Series c038.cbz", "Season 2/Series c038.2.cbz"):
        lib.add(S, rel)
    lib.scan()
    assert find_duplicate_files(db) == []
