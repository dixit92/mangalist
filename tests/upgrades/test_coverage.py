"""Which chapter files a filed volume replaces (pure): full coverage only, ranges across volumes, split chapters and
decimals exact, never a chapter of a volume that was not filed, nothing without MangaPixer's volume list."""

from __future__ import annotations

from decimal import Decimal

from mangalist.knowledge import VolumeInfo
from mangalist.store.units import Unit
from mangalist.upgrades import chapters_replaced, volume_spans

VOLS = (VolumeInfo("1", "1", "5"), VolumeInfo("2", "6", "10"), VolumeInfo("3", "11", "15"))


def ch(rel, lo, hi=None, vol=None):
    return Unit(rel_path=rel, kind="chapter", ch_from=lo, ch_to=hi or lo, vol_from=vol, vol_to=vol)


def replaced(cov):
    return [rel for rel, _, _ in cov.replaced]


def test_only_chapters_of_the_filed_volume_are_replaced():
    units = [ch(f"c{n:03d}.cbz", str(n)) for n in range(1, 13)]
    cov = chapters_replaced(units, VOLS, ["2"])
    assert replaced(cov) == [f"c{n:03d}.cbz" for n in range(6, 11)]
    assert [(c, v) for _, c, v in cov.replaced][:1] == [("6", "2")]
    assert cov.kept == ()                       # chapters of volumes 1 and 3 are simply not part of this upgrade


def test_a_range_file_needs_every_chapter_in_filed_volumes():
    units = [ch("c004-007.cbz", "4", "7"), ch("c001-003.cbz", "1", "3")]
    cov = chapters_replaced(units, VOLS, ["1"])
    assert replaced(cov) == ["c001-003.cbz"]
    assert cov.kept == (("c004-007.cbz", "chapter 6-7 is not in the filed volumes"),)
    cov = chapters_replaced(units, VOLS, ["1", "2"])
    assert replaced(cov) == ["c001-003.cbz", "c004-007.cbz"]
    assert dict((r, v) for r, _, v in cov.replaced)["c004-007.cbz"] == "1, 2"


def test_split_parts_belong_to_their_chapter_and_extras_stay_exact():
    units = [ch("c005.1.cbz", "5.1"), ch("c005.2.cbz", "5.2"), ch("c003.5.cbz", "3.5"), ch("c010.5.cbz", "10.5"),
             ch("c010.cbz", "10")]
    cov = chapters_replaced(units, VOLS, ["1"])
    # 5.1 / 5.2 are chapter 5 (volume 1); 3.5 lies inside 1-5; 10 and 10.5 are not volume 1's.
    assert replaced(cov) == ["c003.5.cbz", "c005.1.cbz", "c005.2.cbz"]
    cov = chapters_replaced(units, VOLS, ["2"])
    assert replaced(cov) == ["c010.cbz"]        # 10.5 is an extra after 10, in no volume's range: never replaced


def test_a_part_of_the_last_chapter_of_a_volume_goes_with_that_volume():
    units = [ch("c010.1.cbz", "10.1"), ch("c010.2.cbz", "10.2"), ch("c011.cbz", "11")]
    assert replaced(chapters_replaced(units, VOLS, ["2"])) == ["c010.1.cbz", "c010.2.cbz"]
    assert replaced(chapters_replaced(units, VOLS, ["3"])) == ["c011.cbz"]


def test_equal_numbers_compare_exactly_not_as_text():
    vols = (VolumeInfo("1", "1", "2.50"),)
    units = [ch("a.cbz", "2.5"), ch("b.cbz", "2.6")]
    cov = chapters_replaced(units, vols, ["01"])
    assert replaced(cov) == ["a.cbz"]


def test_no_volume_list_or_no_chapters_in_it_replaces_nothing():
    units = [ch("c001.cbz", "1")]
    cov = chapters_replaced(units, (), ["1"])
    assert cov.replaced == () and "not in MangaPixer's volume list" in cov.notes[0]
    cov = chapters_replaced(units, (VolumeInfo("1"),), ["1"])
    assert cov.replaced == () and "does not say which chapters" in cov.notes[0]
    assert chapters_replaced(units, VOLS, []).replaced == ()
    spans, notes = volume_spans((VolumeInfo("1", "5", "1"),), [Decimal(1)])
    assert spans == {} and notes                # from > to is not a range


def test_volumes_extras_unknowns_and_mixed_files_are_never_replaced():
    units = [Unit(rel_path="v01.cbz", kind="volume", vol_from="1", vol_to="1"),
             Unit(rel_path="extra.cbz", kind="extra"),
             Unit(rel_path="01.cbz", kind="unknown", num_from="1", num_to="1"),
             Unit(rel_path="v01 + 004.cbz", kind="volume", vol_from="1", vol_to="1"),
             ch("v01 + 004.cbz", "4"),
             ch("c002.cbz", "2")]
    assert replaced(chapters_replaced(units, VOLS, ["1"])) == ["c002.cbz"]


def test_a_chapter_whose_name_says_another_volume_is_kept():
    units = [ch("Vol.02 Ch.005.cbz", "5", vol="2"), ch("Vol.01 Ch.004.cbz", "4", vol="1")]
    cov = chapters_replaced(units, VOLS, ["1"])
    assert replaced(cov) == ["Vol.01 Ch.004.cbz"]
    assert cov.kept[0][0] == "Vol.02 Ch.005.cbz" and "name says volume 2" in cov.kept[0][1]


def test_a_chapter_in_two_folders_is_kept_numbering_may_restart():
    units = [ch("Season 1/c001.cbz", "1"), ch("Season 2/c001.cbz", "1"), ch("Season 1/c002.cbz", "2")]
    cov = chapters_replaced(units, VOLS, ["1"])
    assert replaced(cov) == ["Season 1/c002.cbz"]
    assert {rel for rel, _ in cov.kept} == {"Season 1/c001.cbz", "Season 2/c001.cbz"}


def test_two_copies_in_one_folder_are_both_replaced():
    units = [ch("c001 [A].cbz", "1"), ch("c001 [B].cbz", "1")]
    assert replaced(chapters_replaced(units, VOLS, ["1"])) == ["c001 [A].cbz", "c001 [B].cbz"]


def test_an_absurd_range_is_never_replaced():
    units = [ch("c001-5000.cbz", "1", "5000")]
    assert chapters_replaced(units, (VolumeInfo("1", "1", "9999"),), ["1"]).replaced == ()
