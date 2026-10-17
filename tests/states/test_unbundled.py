"""Unbundled Chapters in the states (mangalist.states, ``unbundled=``): a folder holding volumes also wants the chapters
after its last volume; chapters held volumes collect are never wanted; a later volume turns them into an upgrade. The
synthetic item of ``helpers``: licensed, latest chapter 41, English volumes 1 (ch. 1-8), 2 (9-16) and 3 (17-24.5) out,
volume 4 announced without chapters. Made-up titles."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mangalist.knowledge import VolumeInfo, from_mangapixer_item
from mangalist.states import (GAP_CHAPTER, GAP_UPGRADE, GAP_VOLUME, UNBUNDLED_FLAG, InventorySnapshot, State,
                              compute_state, last_volume_chapter)

from .helpers import TODAY, item, vol


def k(**changes):
    return from_mangapixer_item(item(**changes))


def state(knowledge, unbundled=True, empty=False, **inv):
    return compute_state(InventorySnapshot(**inv), knowledge, folder_empty=empty, today=TODAY, unbundled=unbundled)


def gaps(st, kind):
    return [(g.start, g.end) for g in st.gaps if g.kind == kind]


def chapters(lo, hi):
    return [str(n) for n in range(lo, hi + 1)]


def released_list(*vols):
    return {"source": "merged", "fetchedAt": "2026-10-02T12:00:00.000Z", "items": list(vols)}


# --- the gap after the last volume ---------------------------------------------------------------------------------


def test_off_a_volume_folder_is_compared_on_volumes_only():
    st = state(k(), unbundled=False, held_volumes=["1", "2", "3"])
    assert st.state == State.UP_TO_DATE and not st.gaps and UNBUNDLED_FLAG not in st.flags


def test_on_the_chapters_after_the_last_volume_are_missing_chapters():
    st = state(k(), held_volumes=["1", "2", "3"])
    assert st.state == State.MISSING_CHAPTERS
    assert gaps(st, GAP_CHAPTER) == [("25", "41")]                 # volume 3 ends with 24.5
    assert "Unbundled chapters after chapter 24.5 (the end of volume 3)" in st.reasons
    assert st.flags[0] == UNBUNDLED_FLAG and st.unbundled


def test_chapters_inside_held_volumes_are_never_wanted_and_the_volume_side_is_unchanged():
    off = state(k(), unbundled=False, held_volumes=["1", "3"])
    on = state(k(), held_volumes=["1", "3"])
    assert gaps(on, GAP_VOLUME) == gaps(off, GAP_VOLUME) == [("2", None)]
    assert gaps(on, GAP_CHAPTER) == [("25", "41")]                 # not 1-8, 9-16 (volume 2 is the gap) or 17-24.5
    assert on.state == State.MISSING_VOLUMES


def test_a_released_volume_the_folder_lacks_brings_its_chapters_itself():
    on = state(k(), held_volumes=["1", "2"])
    assert gaps(on, GAP_VOLUME) == [("3", None)]
    assert gaps(on, GAP_CHAPTER) == [("25", "41")]                 # 17-24.5 come with volume 3
    assert "Unbundled chapters after chapter 24.5 (the end of volume 3)" in on.reasons


def test_held_unbundled_chapters_count_and_the_gap_moves_on():
    st = state(k(), held_volumes=["1", "2", "3"], held_chapters=chapters(25, 30) + ["33"])
    assert gaps(st, GAP_CHAPTER) == [("31", "32"), ("34", "41")]
    full = state(k(), held_volumes=["1", "2", "3"], held_chapters=chapters(25, 41))
    assert full.state == State.UP_TO_DATE and not full.gaps


def test_a_decimal_latest_chapter_after_the_last_volume_is_wanted_too():
    st = state(k(record__latestChapter="30.5"), held_volumes=["1", "2", "3"], held_chapters=chapters(25, 29))
    assert gaps(st, GAP_CHAPTER) == [("30", None), ("30.5", None)]


def test_a_later_volume_turns_the_held_chapters_into_an_upgrade():
    # volumes 1-2 and the unbundled chapters 17-30 held; volume 3 (17-24.5) is out: an upgrade, chapters 31-41 missing
    held = chapters(17, 24) + ["24.5"] + chapters(25, 30)
    st = state(k(), held_volumes=["1", "2"], held_chapters=held)
    assert gaps(st, GAP_UPGRADE) == [("3", None)] and not gaps(st, GAP_VOLUME)
    assert gaps(st, GAP_CHAPTER) == [("31", "41")]
    assert st.state == State.MISSING_CHAPTERS and st.upgrade_available


def test_a_later_volume_partly_held_as_chapters_is_a_missing_volume():
    st = state(k(), held_volumes=["1", "2"], held_chapters=chapters(17, 20) + chapters(25, 41))
    assert gaps(st, GAP_VOLUME) == [("3", None)] and not gaps(st, GAP_UPGRADE)     # the volume fills 21-24.5
    off = state(k(), unbundled=False, held_volumes=["1", "2"], held_chapters=chapters(17, 20) + chapters(25, 41))
    assert st.gaps == off.gaps                                      # a folder holding chapters compares them already


def test_the_setting_never_takes_a_gap_away():
    # a mixed folder already asks for the chapters of a missing volume (9-24, in volumes 2-3); the setting keeps them
    held = dict(held_volumes=["1"], held_chapters=chapters(25, 30))
    off = state(k(), unbundled=False, **held)
    on = state(k(), **held)
    assert gaps(off, GAP_CHAPTER) == gaps(on, GAP_CHAPTER) == [("9", "24"), ("31", "41")]
    assert gaps(on, GAP_VOLUME) == gaps(off, GAP_VOLUME)
    assert set(off.gaps) <= set(on.gaps)


def test_a_newer_unmapped_volume_out_in_a_mixed_folder_stays_an_upgrade():
    vols = released_list(vol("1", "1", "8", "2025-01-01", "released"), vol("2", "9", "16", "2025-05-01", "released"),
                         vol("3", None, None, "2026-01-01", "released"))
    st = state(k(volumes=vols), held_volumes=["1", "2"], held_chapters=chapters(17, 22))
    assert gaps(st, GAP_UPGRADE) == [("3", None)]
    assert gaps(st, GAP_CHAPTER) == [("23", "41")]
    assert "Unbundled chapters after chapter 16 (the end of volume 2)" in st.reasons


def test_where_the_newest_held_volume_ends_is_not_known_nothing_is_compared():
    vols = released_list(vol("1", "1", "8", "2025-01-01", "released"), vol("2", None, None, "2025-05-01", "released"))
    off = state(k(volumes=vols), unbundled=False, held_volumes=["1", "2"])
    on = state(k(volumes=vols), held_volumes=["1", "2"])
    assert on.state == off.state and on.gaps == off.gaps
    assert any("does not say which chapter volume 2 ends with" in r for r in on.reasons)


def test_a_mixed_folder_whose_newest_volume_is_unmapped_is_compared_as_without_the_setting():
    vols = released_list(vol("1", "1", "8", "2025-01-01", "released"), vol("2", None, None, "2025-05-01", "released"))
    held = dict(held_volumes=["1", "2"], held_chapters=chapters(20, 25))
    off = state(k(volumes=vols), unbundled=False, **held)
    on = state(k(volumes=vols), **held)
    assert on.state == off.state and on.gaps == off.gaps and gaps(on, GAP_CHAPTER)        # still counts its chapters
    assert any("does not say which chapter volume 2 ends with" in r for r in on.reasons)


def test_licensed_without_an_english_volume_list_the_held_volumes_list_still_says_where_they_end():
    # the English edition is chapters in an app: no English dates, volume list from the original edition
    vols = released_list(vol("1", "1", "8"), vol("2", "9", "16"))
    off = state(k(volumes=vols, record__englishPublishers=[]), unbundled=False, held_volumes=["1", "2"])
    on = state(k(volumes=vols, record__englishPublishers=[]), held_volumes=["1", "2"])
    assert gaps(on, GAP_CHAPTER) == [("17", "41")] and on.state == State.MISSING_CHAPTERS
    assert not gaps(off, GAP_CHAPTER)


# --- where it has no effect ----------------------------------------------------------------------------------------


def test_no_effect_on_a_chapter_folder():
    # a chapter folder is compared on chapters already. (An empty folder wants the chapters after the newest English
    # volume since 2026-10-17 - test_unbundled_empty_folder.py.)
    inv = dict(held_chapters=chapters(1, 30))
    off = state(k(), unbundled=False, **inv)
    on = state(k(), **inv)
    assert (on.state, on.gaps, on.reasons) == (off.state, off.gaps, off.reasons)


def test_no_effect_on_a_folder_without_a_chapter_target_or_restarting_chapter_numbers():
    no_target = k(record__latestChapter=None, record__totalChapters=None)
    held = ["1", "2", "3"]
    assert state(no_target, held_volumes=held).gaps == state(no_target, False, held_volumes=held).gaps
    on = compute_state(InventorySnapshot(held_volumes=["1", "2", "3"]), k(), folder_empty=False, today=TODAY,
                       unbundled=True, restarts=("chapter",))
    assert not on.gaps


def test_tracking_off_and_a_declared_edition_are_not_unbundled():
    off_item = item(tracking="off", completion=None)
    st = state(from_mangapixer_item(off_item), held_volumes=["1"])
    assert st.state == State.NOT_TRACKED and not st.gaps
    edition = item()
    edition["completion"] = {**edition["completion"], "volumeTotalOverride": 2, "edition": "master"}
    st = state(from_mangapixer_item(edition), held_volumes=["1", "2"])
    assert not gaps(st, GAP_CHAPTER)


def test_owner_marked_up_to_date_keeps_the_gaps_but_says_up_to_date():
    st = compute_state(InventorySnapshot(held_volumes=["1", "2", "3"]), k(), folder_empty=False, today=TODAY,
                       unbundled=True, behind_override="done")
    assert st.state == State.UP_TO_DATE and gaps(st, GAP_CHAPTER) == [("25", "41")]


# --- the shared rule ----------------------------------------------------------------------------------------------


def test_last_volume_chapter_is_check_for_new_chapters_rule():
    vols = [VolumeInfo(volume="1", chapters_from="1", chapters_to="8"), VolumeInfo(volume="2"),
            VolumeInfo(volume="3", chapters_from="17", chapters_to="24.5")]
    assert last_volume_chapter(vols, Decimal(3)) == Decimal("24.5")
    assert last_volume_chapter(vols, Decimal(2)) == Decimal(8)     # volume 2 unmapped: the newest mapped one below
    assert last_volume_chapter(vols, None) is None and last_volume_chapter([], Decimal(1)) is None
