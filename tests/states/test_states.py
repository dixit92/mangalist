"""compute_state: every state and flag on synthetic knowledge and inventories (no files, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangalist.knowledge import EnglishPublisher, VolumeInfo, from_mangapixer_item
from mangalist.models import FileHit, MangaEntry
from mangalist.states import (
    ATTENTION_KIND,
    ATTENTION_NOT_A_WORK,
    ATTENTION_REVIEW,
    ATTENTION_UNMATCHED,
    GAP_CHAPTER,
    GAP_UPGRADE,
    GAP_VOLUME,
    STATE_ORDER,
    Gap,
    InventoryLike,
    InventorySnapshot,
    State,
    compute_state,
    fallback_inventory_from_entry,
    mangapixer_agrees,
    state_for_entry,
)

from .helpers import TODAY, item, own, vol


def inv(**kw) -> InventorySnapshot:
    return InventorySnapshot(**kw)


def state(inventory, knowledge, **kw):
    kw.setdefault("folder_empty", False)
    return compute_state(inventory, knowledge, today=TODAY, **kw)


PUB = EnglishPublisher("Example Press", status="Ongoing")


# --- empty folder = wanted (A7) ----------------------------------------------------------------


def test_empty_folder_official_available():
    s = state(inv(), from_mangapixer_item(item()), folder_empty=True)
    assert s.state == State.WANTED_OFFICIAL and s.is_wanted
    assert s.missing_volumes == ("1", "2", "3")        # released ones; vol. 4 is announced
    assert s.upcoming and s.upcoming_date == "2027-02-09" and s.upcoming_volume == "4"


def test_empty_folder_awaiting_release_with_the_announced_date():
    it = item(volumes={"items": [vol("1", date="2027-01", kind="announced")]})
    it["record"]["englishPublishers"] = [{"name": "Example Press", "volumes": None, "chapters": None,
                                          "status": "Ongoing"}]
    s = state(inv(), from_mangapixer_item(it), folder_empty=True)
    assert s.state == State.WANTED_AWAITING
    assert "announced 2027-01" in s.reasons[0] and s.upcoming_date == "2027-01"


def test_partial_dates_decide_released_or_awaiting():
    it = item()
    it["record"]["englishPublishers"] = [{"name": "Example Press", "volumes": None, "status": "Ongoing"}]
    it["volumes"] = {"items": [vol("1", date="2026-10", kind="announced")]}     # this month: out
    assert state(inv(), from_mangapixer_item(it), folder_empty=True).state == State.WANTED_OFFICIAL
    it["volumes"] = {"items": [vol("1", date="2026-11", kind="announced")]}     # next month: awaiting
    assert state(inv(), from_mangapixer_item(it), folder_empty=True).state == State.WANTED_AWAITING
    it["volumes"] = {"items": [vol("1", date="2027", kind="announced")]}
    assert state(inv(), from_mangapixer_item(it), folder_empty=True).state == State.WANTED_AWAITING
    it["volumes"] = {"items": [vol("1", date="2026", kind="announced")]}        # this year: out
    assert state(inv(), from_mangapixer_item(it), folder_empty=True).state == State.WANTED_OFFICIAL


def test_empty_folder_scanlation_only():
    k = own(licensed_en=False, latest_chapter="50")
    s = state(inv(), k, folder_empty=True)
    assert s.state == State.WANTED_SCANLATION and not s.gaps


def test_empty_folder_official_from_the_publisher_count_without_a_volume_list():
    k = own(licensed_en=True, english_publishers=(EnglishPublisher("Example Press", volumes=2),))
    s = state(inv(), k, folder_empty=True)
    assert s.state == State.WANTED_OFFICIAL and s.missing_volumes == ("1", "2")


def test_empty_unmatched_folder_is_wanted_and_needs_attention():
    s = state(inv(), None, folder_empty=True)
    assert s.state == State.WANTED and s.is_wanted and s.needs_attention == (ATTENTION_UNMATCHED,)


# --- missing volumes / chapters / upgrade -----------------------------------------------------


def test_missing_volumes_with_a_hole():
    k = from_mangapixer_item(item())
    s = state(inv(held_volumes=["1", "3"]), k)
    assert s.state == State.MISSING_VOLUMES
    assert s.gaps == (Gap(GAP_VOLUME, "2", note="English 2025-07"),)
    assert s.gaps_text == "Vol. 2"
    assert "Missing volumes:" in s.gaps_tooltip() and "Vol. 2  (English 2025-07)" in s.gaps_tooltip()


def test_missing_chapters_with_decimal_holes():
    k = own(licensed_en=False, latest_chapter="12.5")
    s = state(inv(held_chapters=["1", "2", "2.5", "4", "5", "9", "10.1", "11", "12"]), k)
    assert s.state == State.MISSING_CHAPTERS
    assert s.missing_chapters == (("3", None), ("6", "8"), ("10", None), ("12.5", None))
    assert s.gaps_text == "Ch. 3, 6-8, 10, 12.5"


def test_exact_decimal_chapters_never_go_through_floats():
    k = own(licensed_en=False, latest_chapter="291.999")
    s = state(inv(held_chapters=[str(n) for n in range(1, 292)]), k)
    assert s.missing_chapters == (("291.999", None),)


def test_chapter_ranges_and_volume_coverage_count_as_held():
    k = own(licensed_en=False, latest_chapter="30")
    s = state(inv(held_volumes=["1"], chapters_covered_by_volumes=[("1", "10")],
                  held_chapters=[("11", "20"), "21", "23"]), k)
    assert s.missing_chapters == (("22", None), ("24", "30"))


def test_volumes_with_unknown_chapters_count_chapters_from_the_first_loose_one():
    k = own(licensed_en=False, latest_chapter="95", scan_latest_volume=None)
    s = state(inv(held_volumes=["1", "2"], held_chapters=["90", "91", "93"]), k)
    assert s.missing_chapters == (("92", None), ("94", "95"))


def test_upgrade_available_from_the_volume_list():
    k = from_mangapixer_item(item(completion=None))
    s = state(inv(held_chapters=[str(n) for n in range(1, 25)] + ["24.5"] + [str(n) for n in range(25, 42)]), k)
    assert s.state == State.UPGRADE
    assert s.upgrade_volumes == ("1", "2", "3") and s.gaps_text == "Upgrade vol. 1-3"
    assert s.gaps_of(GAP_UPGRADE)[2].note == "chapters 17-24.5 held"


def test_a_volume_whose_chapters_are_partly_held_is_not_an_upgrade():
    k = from_mangapixer_item(item(completion=None))
    chapters = [str(n) for n in range(1, 42) if n != 12]
    s = state(inv(held_chapters=chapters + ["24.5"]), k)
    # A chapter collector: vol. 2 is not upgradable (ch. 12 missing), and ch. 12 is the gap.
    assert s.state == State.MISSING_CHAPTERS
    assert s.upgrade_volumes == ("1", "3") and s.missing_chapters == (("12", None),)
    assert s.missing_volumes == ()


def test_mixed_folder_volumes_then_chapters():
    k = from_mangapixer_item(item(completion=None))
    held_ch = [str(n) for n in range(17, 42)] + ["24.5"]
    s = state(inv(held_volumes=["1", "2"], held_chapters=held_ch), k)
    assert s.state == State.UPGRADE and s.upgrade_volumes == ("3",)


def test_held_volumes_cover_their_chapters_through_the_volume_list():
    k = from_mangapixer_item(item(completion=None))
    held_ch = [str(n) for n in range(25, 42)]
    s = state(inv(held_volumes=["1", "2", "3"], held_chapters=held_ch), k)
    assert s.state == State.UP_TO_DATE and s.gaps == ()


def test_unlicensed_scanlation_volumes():
    k = own(licensed_en=False, scan_latest_volume=4)
    s = state(inv(held_volumes=["1", "2"]), k)
    assert s.state == State.MISSING_VOLUMES and s.missing_volumes == ("3", "4")


def test_missing_volumes_outrank_missing_chapters():
    k = own(licensed_en=True, english_publishers=(EnglishPublisher("Example Press", volumes=3, chapters=40),),
            latest_chapter="40")
    s = state(inv(held_volumes=["1"], held_chapters=["30", "32"]), k)
    assert s.state == State.MISSING_VOLUMES
    assert s.missing_volumes == ("2", "3") and s.missing_chapters == (("31", None), ("33", "40"))


# --- up to date / complete ---------------------------------------------------------------------


def test_up_to_date_and_disk_ahead():
    k = own(licensed_en=False, latest_chapter="10")
    assert state(inv(held_chapters=[str(n) for n in range(1, 11)]), k).state == State.UP_TO_DATE
    assert state(inv(held_chapters=[str(n) for n in range(1, 14)]), k).state == State.UP_TO_DATE


def test_complete_licensed_volume_collector():
    k = own(licensed_en=True, completed_in_origin=True,
            english_publishers=(EnglishPublisher("Example Press", volumes=3, status="Completed"),))
    s = state(inv(held_volumes=["1", "2", "3"]), k)
    assert s.state == State.COMPLETE
    assert state(inv(held_volumes=["1", "2"]), k).state == State.MISSING_VOLUMES
    ongoing = own(licensed_en=True, english_publishers=(EnglishPublisher("Example Press", volumes=3),))
    assert state(inv(held_volumes=["1", "2", "3"]), ongoing).state == State.UP_TO_DATE


def test_complete_scanlation_needs_the_finished_total_or_translation_complete():
    done = own(licensed_en=False, completed_in_origin=True, latest_chapter="20", translation_complete=True)
    assert state(inv(held_chapters=[str(n) for n in range(1, 21)]), done).state == State.COMPLETE
    total = own(licensed_en=False, completed_in_origin=True, latest_chapter="18", total_chapters=20)
    held = inv(held_chapters=[str(n) for n in range(1, 21)])
    assert state(held, total).state == State.COMPLETE
    unknown = own(licensed_en=False, completed_in_origin=True, latest_chapter="20")
    assert state(held, unknown).state == State.UP_TO_DATE      # the origin ended; the scanlation may not have


def test_owner_override_keeps_gaps_but_says_up_to_date():
    k = own(licensed_en=False, latest_chapter="10")
    s = state(inv(held_chapters=["1", "2"]), k, behind_override="done")
    assert s.state == State.UP_TO_DATE and s.missing_chapters == (("3", "10"),)


# --- can't tell -----------------------------------------------------------------------------------


def test_cant_tell_without_knowledge_or_numbers():
    s = state(inv(held_chapters=["1"]), None)
    assert s.state == State.CANT_TELL and s.needs_attention == (ATTENTION_UNMATCHED,)
    s = state(inv(held_chapters=["1"]), own(licensed_en=False))
    assert s.state == State.CANT_TELL and s.needs_attention == ()
    s = state(inv(held_chapters=["1"]), from_mangapixer_item(item(link={"state": "DontMatch"}, record=None)))
    assert s.state == State.CANT_TELL and "Don't match" in s.reasons[0] and s.needs_attention == ()


def test_only_bare_numbers_cannot_be_told_and_need_the_kind():
    s = state(inv(unknown_kind_files=["01.cbz", "02.cbz"]), own(licensed_en=False, latest_chapter="5"))
    assert s.state == State.CANT_TELL and s.needs_attention == (ATTENTION_KIND,)
    s = state(inv(held_chapters=["1"], unknown_kind_files=3), own(licensed_en=False, latest_chapter="1"))
    assert s.state == State.UP_TO_DATE and ATTENTION_KIND in s.needs_attention


# --- flags ----------------------------------------------------------------------------------------


def test_flags():
    k = from_mangapixer_item(item(completion=None))
    s = state(inv(held_volumes=["1", "2", "3"]), k, needs_kind=True)
    assert s.state == State.UP_TO_DATE
    assert s.upcoming and s.upcoming_date == "2027-02-09"
    assert s.flags == ("Upcoming", "Needs attention") and s.needs_attention == (ATTENTION_KIND,)
    assert not s.requested and not s.rename_pending
    s = state(inv(held_volumes=["1"]), k, requested=True, rename_pending=True)
    assert s.flags == ("Upcoming", "Requested", "Rename pending")
    assert "Upcoming: English vol. 4 2027-02-09" in s.tooltip()


def test_needs_attention_review_and_not_a_work():
    s = state(inv(held_chapters=["1"]), own(link_state="NeedsReview", licensed_en=False, latest_chapter="1"))
    assert s.needs_attention == (ATTENTION_REVIEW,) and s.state == State.CANT_TELL
    s = state(inv(held_chapters=["1"]), own(link_state="NotAWork", mu_id=None, title=None))
    assert s.needs_attention == (ATTENTION_NOT_A_WORK,)
    s = state(inv(held_chapters=["1"]), own(link_state="NotLookedUp", mu_id=None, title=None))
    assert s.needs_attention == ()                 # not looked up yet is not a problem


def test_no_upcoming_without_announced_volumes():
    s = state(inv(), own(licensed_en=False), folder_empty=True)
    assert not s.upcoming and s.upcoming_date is None


# --- MangaPixer cross-check -----------------------------------------------------------------------


def test_mangapixer_agreeing_answer():
    k = from_mangapixer_item(item())                         # MissingSome
    s = state(inv(held_volumes=["1", "3"]), k)
    assert s.state == State.MISSING_VOLUMES and s.mangapixer_answer == "MissingSome"
    assert not s.mangapixer_disagrees and "MangaPixer: MissingSome (Running)" in s.tooltip()


def test_mangapixer_disagreement_keeps_mangalists_state():
    k = from_mangapixer_item(item(completion={"answer": "MissingSome", "reason": "Running",
                                              "upgradeAvailable": False, "upgradeVolumes": []}))
    s = state(inv(held_volumes=["1", "2", "3"]), k)
    assert s.state == State.UP_TO_DATE and s.mangapixer_disagrees
    assert "MangaPixer: MissingSome (Running) - differs" in s.tooltip()


def test_mangapixer_cant_tell_is_not_compared():
    k = from_mangapixer_item(item(completion={"answer": "CantTell", "reason": "NoNumbers"}))
    s = state(inv(held_volumes=["1", "2", "3"]), k)
    assert not s.mangapixer_disagrees and s.mangapixer_text() == "MangaPixer: CantTell (NoNumbers)"


def test_mangapixer_upgrade_volumes_feed_upgrade_available():
    k = from_mangapixer_item(item(volumes=None, completion={"answer": "UpToDate", "reason": "None",
                                                           "upgradeAvailable": True, "upgradeVolumes": ["1"]}))
    k2 = k.__class__(**{**k.__dict__, "english_publishers": ()})
    s = state(inv(held_chapters=[str(n) for n in range(1, 42)]), k2)
    assert s.state == State.UPGRADE and s.upgrade_volumes == ("1",) and not s.mangapixer_disagrees
    assert s.gaps_of(GAP_UPGRADE)[0].note == "MangaPixer"


@pytest.mark.parametrize("st,answer,agrees", [
    (State.COMPLETE, "HaveItAll", True), (State.UPGRADE, "HaveItAll", True), (State.UP_TO_DATE, "HaveItAll", False),
    (State.UP_TO_DATE, "UpToDate", True), (State.COMPLETE, "UpToDate", False),
    (State.MISSING_CHAPTERS, "FinishedMissing", True), (State.WANTED_OFFICIAL, "MissingSome", True),
    (State.UP_TO_DATE, "MissingSome", False), (State.UP_TO_DATE, "CantTell", None),
    (State.CANT_TELL, "MissingSome", None), (State.UP_TO_DATE, "SomethingNew", None), (State.UP_TO_DATE, None, None),
])
def test_answer_mapping(st, answer, agrees):
    assert mangapixer_agrees(st, answer) is agrees


# --- interface, ordering, fallback ----------------------------------------------------------------


def test_snapshot_satisfies_the_inventory_protocol_and_accepts_decimals():
    from decimal import Decimal
    assert isinstance(InventorySnapshot(), InventoryLike)
    k = own(licensed_en=False, latest_chapter="3")
    s = state(inv(held_chapters=[Decimal("1"), Decimal("2")], highest_chapter=Decimal("2")), k)
    assert s.missing_chapters == (("3", None),)


def test_sort_key_follows_the_state_order():
    a = state(inv(), own(licensed_en=False), folder_empty=True)
    b = state(inv(held_chapters=["1"]), own(licensed_en=False, latest_chapter="5"))
    c = state(inv(held_chapters=["1"]), own(licensed_en=False, latest_chapter="1"))
    assert sorted([c, b, a], key=lambda s: s.sort_key) == [a, b, c]
    assert STATE_ORDER[0] == State.WANTED_OFFICIAL and STATE_ORDER[-1] == State.CANT_TELL


def _hit(name: str, has_volume=False, has_chapter=False) -> FileHit:
    return FileHit(path=Path("/library/Example Saga") / name, size=1, depth=0,
                   has_volume=has_volume, has_chapter=has_chapter)


def test_fallback_inventory_from_entry_file_names():
    e = MangaEntry(folder=Path("/library/Example Saga"), title="Example Saga", english_title=None)
    e.files = [
        _hit("Example Saga v01 (c001-008).cbz", True, True),
        _hit("Example Saga v02.cbz", True),
        _hit("Example Saga - Vol.3 Ch.17.cbz", True, True),
        _hit("Example Saga Ch.18-20.cbz", has_chapter=True),
        _hit("Example Saga c021.5.cbz", has_chapter=True),
        _hit("Example Saga 22.cbz"),
    ]
    got = fallback_inventory_from_entry(e)
    assert got.held_volumes == ["1", "2"]
    assert got.chapters_covered_by_volumes == [("001", "008")]
    assert got.held_chapters == ["17", ("18", "20"), "21.5"]
    assert got.unknown_kind_files == ["Example Saga 22.cbz"]


def test_state_for_entry_uses_the_fallback_and_the_own_matcher():
    e = MangaEntry(folder=Path("/library/Example Saga"), title="Example Saga", english_title=None)
    e.mu_id, e.mu_title, e.licensed, e.scan_latest_chapter, e.mu_band = 1, "Example Saga", False, 5.0, "auto"
    e.files = [_hit(f"Example Saga Ch.{n}.cbz", has_chapter=True) for n in (1, 2, 4)]
    s = state_for_entry(e, today=TODAY)
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("3", None), ("5", None))
    e.files = []
    assert state_for_entry(e, today=TODAY).state == State.WANTED_SCANLATION


def test_gap_labels_and_sizes():
    assert Gap(GAP_CHAPTER, "3", "8").label == "Ch. 3-8" and Gap(GAP_CHAPTER, "3", "8").size == 6
    assert Gap(GAP_VOLUME, "2").label == "Vol. 2" and Gap(GAP_UPGRADE, "1").label == "Vol. 1"
    assert VolumeInfo("1", "1", "8").has_chapters
