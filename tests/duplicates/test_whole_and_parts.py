"""A whole chapter held with its parts in one folder is listed in the Duplicates view as an overlap to review (owner,
2026-10-18: "Duplicates: flag overlaps", then "Own section, own count"): the whole ``N`` may be redundant next to a held
``N.1``; a plain split (``N`` + ``N.2``: the file ``N`` is part 1) is no longer listed (owner, 2026-10-24: "Rule
fixes") and is only found for the content verdicts. Its own list and count - never among the numbers held more
than once nor in the Duplicates chip; nothing is deleted. Made-up names."""

from __future__ import annotations

import os

from mangalist.duplicates import find_duplicate_files, find_whole_and_parts
from mangalist.gui.shell import OVERLAP_REDUNDANT, OVERLAP_STAND_IN
from mangalist.process_pool import count_by_folder

S = "Example Series"


def build(lib, *stems):
    for stem in stems:
        lib.add(S, f"{stem}.cbz")
    return lib.scan()


def overlaps(db):
    return find_whole_and_parts(db)


def test_a_plain_split_is_no_overlap_the_whole_is_part_1(db, lib):
    # owner, 2026-10-24: "Rule fixes" - a whole 38 next to 38.2 without 38.1 is the split model's normal split (the
    # file 38 is part 1): not listed. Research lane D: all 10 such items on the owner's library held different pages.
    build(lib, "Series c037.1", "Series c037.2", "Series c038", "Series c038.2", "Series c039.1",
          "Series c040", "Series c040.2", "Series c040.3")
    assert find_duplicate_files(db) == [] and overlaps(db) == []


def test_a_whole_chapter_next_to_its_later_parts_stands_in_for_the_missing_first_part(db, lib):
    # the owner's case: parts to 37.2, then a whole 38 and 38.2 from the two groups (38.1 on no source) - still found
    # for the content verdicts, which list it only when its pages overlap
    build(lib, "Series c037.1", "Series c037.2", "Series c038", "Series c038.2", "Series c039.1")
    assert find_duplicate_files(db) == []                                # not a duplicate number
    (g,) = find_whole_and_parts(db, stand_ins=True)
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
    assert find_whole_and_parts(db, stand_ins=True) == [] and find_duplicate_files(db) == []


def test_parts_without_a_whole_chapter_are_no_overlap(db, lib):
    build(lib, "Series c037.1", "Series c037.2", "Series c038.2")
    assert find_whole_and_parts(db, stand_ins=True) == []


def test_a_whole_chapter_held_twice_is_the_duplicate_first(db, lib):
    build(lib, "Series c038", "Series c038 [2]", "Series c038.1", "Series c038.2")
    assert [(g.number, len(g.files)) for g in find_duplicate_files(db)] == [("38", 2)]
    assert overlaps(db) == [] and find_whole_and_parts(db, stand_ins=True) == []


def test_overlaps_have_their_own_count_and_leave_the_duplicates_count_alone(db, lib):
    build(lib, "Series c001", "Series c001 [2]", "Series c038", "Series c038.2", "Series c040", "Series c040.1",
          "Series c040.2")
    dupes = find_duplicate_files(db)
    assert [(g.number, g.overlap) for g in dupes] == [("1", "")]
    assert count_by_folder(dupes) == (1, {str(lib.dir / S): (0, 1)})     # the Duplicates chip: same number twice only
    assert [(g.number, g.overlap) for g in overlaps(db)] == [("40", OVERLAP_REDUNDANT)]
    assert [(g.number, g.overlap) for g in find_whole_and_parts(db, stand_ins=True)] == [
        ("38", OVERLAP_STAND_IN), ("40", OVERLAP_REDUNDANT)]


def test_each_folder_is_compared_on_its_own(db, lib):
    for rel in ("Season 1/Series c038.cbz", "Season 2/Series c038.2.cbz"):
        lib.add(S, rel)
    lib.scan()
    assert find_whole_and_parts(db, stand_ins=True) == []
