"""find_duplicate_files: MangaPixer's "duplicate numbers" rule (its DuplicateUnitsTests, ported by name) on the database."""

from __future__ import annotations

import os

from mangalist.duplicates import (canonical_number, default_keep, find_duplicate_files, iso_from_ns, largest, newest,
                                  stated_unit)
from mangalist.split_chapters import splits_of
from mangalist.store import Unit

S = "Example Series"


def found(groups):
    return [(g.kind, g.number, len(g.files)) for g in groups]


def names(*stems, ext=".cbz"):
    return [s + ext for s in stems]


def build(lib, rels, series=S, **kw):
    for rel in rels:
        lib.add(series, rel, **kw)
    return lib.scan()


# --- MangaPixer's tests ------------------------------------------------------------------------------------------

def test_the_same_chapter_in_two_files_is_a_duplicate_with_its_file_count(db, lib):
    build(lib, names("Series c001", "Series c001 [2]", "Series c002", "Series c002 [2]", "Series c002 [3]", "Series c003"))
    assert found(find_duplicate_files(db)) == [("chapter", "1", 2), ("chapter", "2", 3)]


def test_nothing_repeated_is_no_duplicate(db, lib):
    build(lib, names("Series c001", "Series c002", "Series c003"))
    assert find_duplicate_files(db) == []


def test_an_empty_library_has_none(db):
    assert find_duplicate_files(db) == []


def test_split_chapters_ranges_and_names_without_a_number_are_not_duplicates(db, lib):
    rels = names("Series c002", "Series c002.1", "Series c002.2", "Series c005-c007", "Series c005-c007 [2]",
                 "Series c006", "Cover", "Notes")
    build(lib, rels)
    assert find_duplicate_files(db) == []
    # the parts are different numbers, MangaPixer's splits_of agrees they are parts of chapter 2
    from decimal import Decimal
    assert splits_of([Decimal(2), Decimal("2.1"), Decimal("2.2")]).chapters == frozenset({2})


def test_a_duplicated_extra_is_a_duplicate_of_its_own_number(db, lib):
    build(lib, names("Series c010", "Series c010.5", "Series c010.5 [2]"))
    assert found(find_duplicate_files(db)) == [("chapter", "10.5", 2)]


def test_a_part_of_a_split_chapter_in_two_files_is_a_duplicate_of_that_part(db, lib):
    build(lib, names("Series c002.1", "Series c002.1 [2]", "Series c002.2"))
    assert found(find_duplicate_files(db)) == [("chapter", "2.1", 2)]


def test_volumes_and_chapters_are_counted_apart_and_a_file_naming_both_is_a_chapter(db, lib):
    build(lib, names("Series v03", "Series v03 [2]", "Series v03 c012", "Series v04 c012", "Series c001"))
    # volume 3 twice; chapter 12 twice (v03 c012 and v04 c012 are the same chapter number)
    assert found(find_duplicate_files(db)) == [("volume", "3", 2), ("chapter", "12", 2)]


def test_each_folder_is_compared_on_its_own(db, lib):
    build(lib, ["Season 1/Series c001.cbz", "Season 1/Series c002.cbz",
                "Season 2/Series c001.cbz", "Season 2/Series c002.cbz",         # numbering restarts: not a duplicate
                "Season 2/Series c003.cbz", "Season 2/Series c003 [2].cbz"])     # a real one
    groups = find_duplicate_files(db)
    assert found(groups) == [("chapter", "3", 2)]
    assert {os.path.basename(os.path.dirname(f.path)) for f in groups[0].files} == {"Season 2"}


def test_a_bare_number_in_a_volumes_series_is_a_volume(db, lib):
    for rel in ("01.cbz", "01 (2nd copy).cbz", "Series c001.cbz"):
        lib.add(S, rel, hint="volumes")
    lib.scan()
    assert found(find_duplicate_files(db)) == [("volume", "1", 2)]


def test_a_bare_number_before_the_answer_states_no_kind_so_it_is_no_duplicate(db, lib):
    build(lib, ["01.cbz", "01 (2nd copy).cbz"])
    assert find_duplicate_files(db) == []


def test_two_editions_of_a_volume_are_listed_but_not_as_chapter_duplicates(db, lib):
    build(lib, names("Series v01", "Series v01 [other scan]", "Series v01 c001"))
    assert found(find_duplicate_files(db)) == [("volume", "1", 2)]


# --- the rest of the rule ----------------------------------------------------------------------------------------

def test_numbers_compare_as_exact_decimals(db, lib):
    build(lib, names("Series c012", "Series c012.0", "Series c012.5"))
    groups = find_duplicate_files(db)
    assert found(groups) == [("chapter", "12", 2)]


def test_a_range_file_is_ignored_even_next_to_a_single_chapter_of_it(db, lib):
    build(lib, names("Series c001-c005", "Series c001-c005 [2]", "Series c003"))
    assert find_duplicate_files(db) == []


def test_the_group_is_read_from_the_name_and_a_copy_marker_is_not_a_group(db, lib):
    build(lib, ["Series - Ch. 012 [GroupX].cbz", "Series - Ch. 012 [2].cbz"])
    (group,) = find_duplicate_files(db)
    assert sorted(f.group or "" for f in group.files) == ["", "GroupX"]


def test_a_group_carries_the_series_the_folder_and_each_files_path_size_and_time(db, lib):
    path = lib.add(S, "Series c001.cbz", size=30, mtime_ns=1_700_000_000_123_456_789)
    lib.add(S, "Series c001 [2].cbz", size=70, mtime_ns=1_700_000_100_000_000_000)
    lib.scan()
    (group,) = find_duplicate_files(db)
    assert (group.series_id, group.folder, group.title) == (lib.series_id(S), str(lib.dir / S), S)
    by_size = {f.size: f for f in group.files}
    assert by_size[30].path == str(path)
    assert by_size[30].modified == "2023-11-14T22:13:20.123456+00:00"
    assert by_size[70].modified == iso_from_ns(1_700_000_100_000_000_000)


def test_a_file_that_is_gone_or_not_a_plain_file_is_left_out(db, lib):
    build(lib, names("Series c001", "Series c001 [2]", "Series c002", "Series c002 [2]"))
    (lib.dir / S / "Series c001 [2].cbz").unlink()
    link = lib.dir / S / "Series c002 [2].cbz"
    link.unlink()
    link.symlink_to(lib.dir / S / "Series c002.cbz")
    assert find_duplicate_files(db) == []


def test_only_present_series_and_only_the_roots_asked_for(db, lib, tmp_path):
    from tests.duplicates.conftest import Library
    build(lib, names("Series c001", "Series c001 [2]"))
    other = Library(db, tmp_path / "other", "Other")
    other.add("Another Series", "Another c001.cbz")
    other.add("Another Series", "Another c001 [2].cbz")
    other.scan()
    assert {g.title for g in find_duplicate_files(db)} == {S, "Another Series"}
    assert {g.title for g in find_duplicate_files(db, [lib.root.id])} == {S}
    assert find_duplicate_files(db, []) == []
    db.record_scan(lib.root.id, lib.dir, [])                     # the series folder is gone from the root
    assert {g.title for g in find_duplicate_files(db)} == {"Another Series"}


def test_the_order_is_series_then_volumes_before_chapters_then_number(db, lib):
    build(lib, names("B c010", "B c010 [2]", "B c002", "B c002 [2]", "B v02", "B v02 [2]"), series="B Series")
    for rel in names("A c001", "A c001 [2]"):
        lib.add("a series", rel)
    lib.scan()
    assert [(g.title, g.kind, g.number) for g in find_duplicate_files(db)] == [
        ("a series", "chapter", "1"), ("B Series", "volume", "2"), ("B Series", "chapter", "2"),
        ("B Series", "chapter", "10")]


# --- the default choice, helpers ---------------------------------------------------------------------------------

def test_the_default_keeps_the_newest_then_the_largest(db, lib):
    lib.add(S, "Series c001.cbz", size=900, mtime_ns=1_000_000_000_000_000_000)
    lib.add(S, "Series c001 [2].cbz", size=100, mtime_ns=2_000_000_000_000_000_000)
    lib.add(S, "Series c002.cbz", size=100, mtime_ns=1_000_000_000_000_000_000)
    lib.add(S, "Series c002 [2].cbz", size=300, mtime_ns=1_000_000_000_000_000_000)
    lib.scan()
    one, two = find_duplicate_files(db)
    assert default_keep(one).size == 100 and newest(one).size == 100 and largest(one).size == 900
    assert default_keep(two).size == 300


def test_canonical_number_drops_trailing_zeros():
    from decimal import Decimal
    assert [canonical_number(Decimal(x)) for x in ("12.50", "3", "0", "10.0", "0.25")] == ["12.5", "3", "0", "10", "0.25"]


def test_stated_unit_reads_stored_unit_rows():
    row = lambda **kw: Unit(rel_path="a.cbz", **kw)  # noqa: E731
    assert stated_unit([row(kind="extra")]) is None
    assert stated_unit([row(kind="unknown", num_from="3")]) is None
    assert stated_unit([row(kind="volume", vol_from="2", vol_to="4")]) is None
    chapter = stated_unit([row(kind="volume", vol_from="3"), row(kind="chapter", ch_from="12", ch_to="12", group_name="G")])
    assert (chapter.kind, str(chapter.number), chapter.group) == ("chapter", "12", "G")
