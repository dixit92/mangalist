"""A volume list that names a chapter 0 (owner, 2026-10-16; MangaPixer 1.43.2's rule): MangaDex lists a prologue, a
pilot or an announcement as chapter 0 of volume 1, and many releases have none. A listed chapter 0 counts only when a
file numbered 0 is held - never missing, and a volume "0-6" is held as chapters once chapters 1-6 are. Every other
number keeps its rules. Synthetic items, made-up titles."""

from __future__ import annotations

from decimal import Decimal

from mangalist.knowledge import from_mangapixer_item
from mangalist.states import (GAP_CHAPTER, GAP_UPGRADE, GAP_VOLUME, InventorySnapshot, State, _held, _range_held,
                              compute_state)

from .helpers import TODAY, item, vol


def listed(*vols):
    return {"source": "merged", "fetchedAt": "2026-10-02T12:00:00.000Z", "items": list(vols)}


# The owner's shape: volume 1 = chapters 0-6 (chapter 0 a prologue), volume 2 = 7-12, both out in English.
ENGLISH = listed(vol("1", "0", "6", "2025-03-04", "released"), vol("2", "7", "12", "2025-07-01", "released"))


def english(volumes=ENGLISH, **changes):
    return from_mangapixer_item(item(volumes=volumes, record__latestChapter="12", completion=None, **changes))


def state(knowledge, folder_empty=False, unbundled=False, **inv):
    return compute_state(InventorySnapshot(**inv), knowledge, folder_empty=folder_empty, today=TODAY,
                         unbundled=unbundled)


def gaps(st, kind):
    return [(g.start, g.end) for g in st.gaps if g.kind == kind]


def chapters(lo, hi):
    return [str(n) for n in range(lo, hi + 1)]


def test_chapters_1_to_6_held_make_volume_0_to_6_an_upgrade():
    # a folder of volume 2 and chapters 1-6: before, volume 1 was a missing volume ("Ch. 0 - missing")
    st = state(english(), held_volumes=["2"], held_chapters=chapters(1, 6))
    assert st.state == State.UPGRADE and st.upgrade_volumes == ("1",)
    assert not gaps(st, GAP_VOLUME) and not gaps(st, GAP_CHAPTER)
    assert st.gaps[0].note == "chapters 0-6 held"


def test_a_chapter_folder_holding_1_to_12_has_both_volumes_as_upgrades_and_no_chapter_0_missing():
    st = state(english(), held_chapters=chapters(1, 12))
    assert st.state == State.UPGRADE and st.upgrade_volumes == ("1", "2") and not gaps(st, GAP_CHAPTER)


def test_a_file_numbered_0_held_too_changes_nothing():
    st = state(english(), held_volumes=["2"], held_chapters=chapters(0, 6))
    assert st.state == State.UPGRADE and st.upgrade_volumes == ("1",) and not gaps(st, GAP_CHAPTER)
    st = state(english(), held_chapters=chapters(0, 12))
    assert st.state == State.UPGRADE and st.upgrade_volumes == ("1", "2")


def test_every_other_number_keeps_its_rules():
    # chapter 1 is not held: volume 1 is still a missing volume, and chapter 1 a missing chapter; 0 is not
    st = state(english(), held_volumes=["2"], held_chapters=chapters(2, 6))
    assert st.state == State.MISSING_VOLUMES and st.missing_volumes == ("1",)
    assert gaps(st, GAP_CHAPTER) == [("1", None)] and not gaps(st, GAP_UPGRADE)


def test_nothing_of_volume_0_to_6_held_is_a_missing_volume_as_before():
    st = state(english(), held_volumes=["2"])
    assert st.state == State.MISSING_VOLUMES and st.missing_volumes == ("1",) and not gaps(st, GAP_UPGRADE)
    empty = state(english(), folder_empty=True)
    assert empty.state == State.WANTED_OFFICIAL and empty.missing_volumes == ("1", "2")


def test_without_an_english_volume_list_the_original_volume_0_to_6_is_an_upgrade_too():
    # the English edition is chapters in an app: the original edition's volumes are compared (no English date)
    original = listed(vol("1", "0", "6"), vol("2", "7", "12"))
    k = from_mangapixer_item(item(volumes=original, record__latestChapter="12", record__englishPublishers=[],
                                  completion=None))
    st = state(k, held_volumes=["2"], held_chapters=chapters(1, 6))
    assert st.state == State.UPGRADE and st.upgrade_volumes == ("1",) and not gaps(st, GAP_VOLUME)


def test_unbundled_original_volume_0_to_8_is_an_upgrade_while_chapter_1_is_wanted():
    # Unbundled chapters on, volume 3 held, chapters 2-8 held: chapter 1 is wanted, 0 is not - volume 1 is an upgrade
    original = listed(vol("1", "0", "8"), vol("2", "9", "16"), vol("3", "17", "24"))
    k = from_mangapixer_item(item(volumes=original, record__englishPublishers=[], completion=None))
    st = state(k, unbundled=True, held_volumes=["3"], held_chapters=chapters(2, 8))
    assert ("1", None) in gaps(st, GAP_UPGRADE) and ("1", None) not in gaps(st, GAP_VOLUME)
    assert gaps(st, GAP_CHAPTER)[0] == ("1", None)
    assert next(g for g in st.gaps if g.kind == GAP_UPGRADE and g.start == "1").note.startswith("original volume")


def test_a_volume_that_maps_only_chapter_0_still_needs_it():
    k = english()
    assert not _range_held(_held(InventorySnapshot(held_chapters=chapters(1, 6)), k), Decimal(0), Decimal(0))
    assert _range_held(_held(InventorySnapshot(held_chapters=["0"]), k), Decimal(0), Decimal(0))
    # a range file from 0 counts as a file numbered 0
    assert _range_held(_held(InventorySnapshot(held_chapters=[("0", "6")]), k), Decimal(0), Decimal(6))
