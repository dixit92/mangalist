"""An unlicensed folder that holds volumes only (owner, 2026-10-15): its chapters are compared only with Unbundled
chapters on ("Start clean: they all stop wanting chapters until you tick Unbundled on the ones you want"); without it
the chapters out past its volumes are volume gaps - the original edition's volumes whose chapters are out, else one
open-ended gap after the last volume ("They shouldn't be marked as completed"). Made-up titles."""

from __future__ import annotations

from mangalist.gui.list_text import wanted_series
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_VOLUMES
from mangalist.gui.volumes_target import find_volumes_availability
from mangalist.knowledge import EnglishPublisher, VolumeInfo
from mangalist.states import GAP_CHAPTER, GAP_VOLUME, InventorySnapshot, State, compute_state

from .helpers import TODAY, own

# the original edition: volumes 1-3 hold chapters 1-30, volume 4 31-40, volume 5 41-50
LIST_1_3 = (VolumeInfo("1", "1", "10"), VolumeInfo("2", "11", "20"), VolumeInfo("3", "21", "30"))
LIST_1_5 = LIST_1_3 + (VolumeInfo("4", "31", "40"), VolumeInfo("5", "41", "50"))


def unlicensed(**kw):
    base = dict(licensed_en=False, latest_chapter="45", volumes=LIST_1_3, title="Example Sage")
    base.update(kw)
    return own(**base)


def state(knowledge, *, unbundled=False, volumes=("1", "2", "3"), chapters=(), **kw):
    return compute_state(InventorySnapshot(held_volumes=list(volumes), held_chapters=list(chapters)), knowledge,
                         folder_empty=False, today=TODAY, unbundled=unbundled, **kw)


def test_no_later_volume_known_is_one_open_ended_volume_gap_not_missing_chapters():
    s = state(unlicensed())
    assert s.state == State.MISSING_VOLUMES and not s.missing_chapters
    (gap,) = s.gaps
    assert gap.kind == GAP_VOLUME and gap.open_end and gap.start == "4" and gap.label == "Vol. 4+"
    assert gap.note == "chapters 31-45 out past volume 3; not licensed in English"
    assert s.gaps_text == "Vol. 4+" and s.missing_volumes == ("4",) and s.n_missing == 1
    assert "Missing volumes:\n  Vol. 4+  (chapters 31-45" in s.gaps_tooltip()
    why = " ".join(s.reasons)
    assert "chapters 31-45 are out past volume 3" in why and "turn on Unbundled chapters" in why
    # one chapter past the volumes
    s = state(unlicensed(latest_chapter="31"))
    assert s.gaps_text == "Vol. 4+" and "chapter 31 is out past volume 3" in " ".join(s.reasons)


def test_the_original_editions_volumes_out_are_the_gaps_then_the_open_end():
    s = state(unlicensed(volumes=LIST_1_5))
    # volume 4's chapters are all out; volume 5 ends at 50 > 45 - its chapters 41-45 are the open end
    assert s.state == State.MISSING_VOLUMES and not s.missing_chapters
    assert s.gaps_text == "Vol. 4, 5+"
    assert s.gaps[0].note == "original edition, chapters 31-40; not licensed in English" and not s.gaps[0].open_end
    assert s.gaps[1].open_end and "chapters 41-45 out past volume 4" in s.gaps[1].note
    assert "chapters 31-45 are out past volume 3" in " ".join(s.reasons)       # the reason counts from the held ones
    # every chapter of volume 5 out too: no open end
    s = state(unlicensed(volumes=LIST_1_5, latest_chapter="50"))
    assert s.gaps_text == "Vol. 4-5" and not any(g.open_end for g in s.gaps)
    # a volume missing in the middle is a gap as well (it was missing chapters 11-20)
    s = state(unlicensed(latest_chapter="30"), volumes=("1", "3"))
    assert s.state == State.MISSING_VOLUMES and s.gaps_text == "Vol. 2" and not s.missing_chapters
    assert "volumes out, by their chapters, that the folder lacks" in " ".join(s.reasons)


def test_caught_up_is_up_to_date_and_finished_with_every_volume_is_complete():
    s = state(unlicensed(latest_chapter="30"))
    assert s.state == State.UP_TO_DATE and not s.gaps and s.reasons == ("Latest chapter 30",)     # as before
    done = unlicensed(latest_chapter="30", origin_status="Complete", translation_complete=True)
    assert state(done).state == State.COMPLETE
    # finished, but chapters out past the volumes held: never Complete
    s = state(unlicensed(latest_chapter="45", origin_status="Complete", translation_complete=True))
    assert s.state == State.MISSING_VOLUMES and s.gaps_text == "Vol. 4+"


def test_marked_up_to_date_keeps_the_gap_for_the_tooltip():
    s = state(unlicensed(), behind_override="done")
    assert s.state == State.UP_TO_DATE and s.gaps_text == "Vol. 4+"


def test_unbundled_on_compares_the_chapters_as_before():
    s = state(unlicensed(), unbundled=True)
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("31", "45"),) and not s.missing_volumes
    # a volume missing in the middle: its chapters too (an unlicensed series' chapters, as before)
    s = state(unlicensed(latest_chapter="30"), volumes=("1", "3"), unbundled=True)
    assert s.missing_chapters == (("11", "20"),) and not s.missing_volumes


def test_a_newest_volume_the_list_does_not_map_is_not_compared_either_way():
    # which chapters volume 4 holds is unknown: nothing to compare with, as before (no open end is guessed)
    s = state(unlicensed(), volumes=("1", "2", "3", "4"))
    assert s.state == State.CANT_TELL and not s.gaps
    s = state(unlicensed(), volumes=("1", "2", "3", "4"), unbundled=True)       # the first chapter to get is asked
    assert not s.gaps and s.unbundled_ask is not None and s.unbundled_ask.needed


def test_a_newest_volume_the_list_does_not_map_counts_the_original_editions_volumes():
    # owner, 2026-10-16 (question 83: "yes"): the original edition's volume count gives the volume gaps there
    s = state(unlicensed(origin_volumes=8), volumes=("1", "2", "3", "4"))
    assert s.state == State.MISSING_VOLUMES and s.gaps_text == "Vol. 5-8" and not s.missing_chapters
    assert s.gaps[0].note == "original edition, 8 volumes; not licensed in English"
    assert not any(g.open_end for g in s.gaps)                      # volume 8 is not mapped: no chapter past it known
    why = " ".join(s.reasons)
    assert "the original edition has 8 volumes - counted as missing volumes" in why
    assert "turn on Unbundled chapters" in why and "out past volume 4" not in why     # volume 4's end is not known
    # a hole below the newest volume too
    s = state(unlicensed(origin_volumes=5), volumes=("1", "3", "4"))
    assert s.gaps_text == "Vol. 2, 5"


def test_the_count_adds_to_the_scanlation_volumes_and_their_gaps_are_never_searched():
    s = state(unlicensed(origin_volumes=7, scan_latest_volume=5), volumes=("1", "2", "3", "4"))
    assert s.gaps_text == "Vol. 5-7" and [g.note for g in s.gaps][:1] == ["scanlation"]
    k = unlicensed(origin_volumes=6, source="mangapixer")
    s = state(k, volumes=("1", "2", "3", "4"))
    volumes = find_volumes_availability(series_id=7, folder="/lib/Example Sage", title="Example Sage",
                                        english_title=None, knowledge=k, state=s, held=("1", "2", "3", "4"))
    (w,) = wanted_series(folder="/lib/Example Sage", title="Example Sage", english_title=None, state=s, knowledge=k,
                         held=("1", "2", "3", "4"), series_id=7, volumes=volumes)
    assert w.group == GROUP_VOLUMES and w.gaps == "Vol. 5-6" and not w.findable


def test_every_original_volume_held_reads_up_to_date_or_complete():
    s = state(unlicensed(origin_volumes=4), volumes=("1", "2", "3", "4"))
    assert s.state == State.UP_TO_DATE and not s.gaps
    assert "The folder holds every volume of the original edition (4)" in s.reasons
    done = unlicensed(origin_volumes=4, origin_status="Complete", translation_complete=True)
    assert state(done, volumes=("1", "2", "3", "4")).state == State.COMPLETE


def test_with_unbundled_on_or_no_count_the_unmapped_volume_compares_as_before():
    s = state(unlicensed(origin_volumes=8), volumes=("1", "2", "3", "4"), unbundled=True)
    assert not s.gaps and s.unbundled_ask is not None and s.unbundled_ask.needed      # the first chapter to get
    s = state(unlicensed(), volumes=("1", "2", "3", "4"))                              # no count: Can't tell
    assert s.state == State.CANT_TELL and not s.gaps
    # a licensed series is not counted this way
    licensed = own(licensed_en=True, latest_chapter="45", volumes=LIST_1_3, origin_volumes=8,
                   english_publishers=(EnglishPublisher("Example Press", volumes=3),))
    assert not state(licensed, volumes=("1", "2", "3", "4")).missing_volumes


def test_scanlation_volumes_past_the_list_leave_no_open_end():
    # MangaUpdates' scanlated volumes 4-5 are gaps; volume 5 is not mapped, so it may hold chapters 31-45
    s = state(unlicensed(scan_latest_volume=5))
    assert s.state == State.MISSING_VOLUMES and s.gaps_text == "Vol. 4-5" and not any(g.open_end for g in s.gaps)
    # scanlated volumes the list maps: the open end after the last of them
    s = state(unlicensed(scan_latest_volume=4, volumes=LIST_1_5))
    assert s.gaps_text == "Vol. 4, 5+"


def test_chapter_files_or_a_licence_change_nothing():
    s = state(unlicensed(), chapters=["31", "32"])
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("33", "45"),) and not s.missing_volumes
    licensed = own(licensed_en=True, latest_chapter="45", volumes=LIST_1_3,
                   english_publishers=(EnglishPublisher("Example Press", volumes=3),))
    s = state(licensed)
    assert s.state == State.UP_TO_DATE and not s.gaps
    s = compute_state(InventorySnapshot(), unlicensed(), folder_empty=True, today=TODAY)
    assert s.state == State.WANTED_SCANLATION and s.missing_chapters == (("1", "45"),)


def test_to_get_shows_the_open_gap_under_volumes_never_searched():
    k = unlicensed(source="mangapixer")
    s = state(k)
    volumes = find_volumes_availability(series_id=7, folder="/lib/Example Sage", title="Example Sage",
                                        english_title=None, knowledge=k, state=s, held=("1", "2", "3"))
    assert not volumes.enabled and "Unbundled chapters" in volumes.reason
    (w,) = wanted_series(folder="/lib/Example Sage", title="Example Sage", english_title=None, state=s, knowledge=k,
                         held=("1", "2", "3"), series_id=7, volumes=volumes)
    assert w.group == GROUP_VOLUMES and w.gaps == "Vol. 4+" and not w.findable
    assert w.group != GROUP_CHAPTERS and not s.gaps_of(GAP_CHAPTER)


def test_a_finished_series_with_every_volume_held_is_complete_whatever_the_list_maps():
    # owner, 2026-10-17: 8 volumes, all held, MangaUpdates "8 Volumes | 40 Chapters (Complete)"; MangaDex's list maps
    # volume 8 to 36-38 only (its translation stopped) - the chapters it does not map are in the final volume
    k = unlicensed(latest_chapter="32", origin_status="Complete", origin_volumes=3)
    s = state(k)
    assert s.state == State.COMPLETE and not s.gaps
    # still ongoing in the original: the chapters past volume 3 may be a volume 4 to come
    s = state(unlicensed(latest_chapter="32", origin_volumes=3))
    assert s.state == State.MISSING_VOLUMES and s.gaps_text == "Vol. 4+"
    # finished, but a volume of the original edition is not held: not Complete, and no gap past the final volume
    s = state(k, volumes=("1", "2"))
    assert s.state == State.MISSING_VOLUMES and s.gaps_text == "Vol. 3"
