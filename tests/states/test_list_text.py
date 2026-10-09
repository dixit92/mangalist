"""The List tab's Qt-free rules (mangalist.gui.list_text): what the Download tab is handed per series, and the details
panel's texts. Made-up series; exact decimal strings throughout."""

from __future__ import annotations

from mangalist.gui.list_text import (
    REASON_CHAPTERS,
    REASON_UPGRADES,
    chapter_numbers,
    english_text,
    expand_numbers,
    group_gaps_text,
    holds_text,
    match_text,
    wanted_label,
    wanted_series,
)
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES
from mangalist.gui.volumes_target import Availability, VolumeTarget
from mangalist.knowledge import EnglishPublisher, SeriesKnowledge, from_mangapixer_item
from mangalist.states import GAP_CHAPTER, GAP_UPGRADE, GAP_VOLUME, Gap, SeriesState, State

from .helpers import item


def _state(*gaps, state=State.MISSING_VOLUMES) -> SeriesState:
    return SeriesState(state=state, gaps=tuple(gaps))


def test_expand_numbers_keeps_exact_decimals():
    assert expand_numbers(["3", "1", ("5", "7"), "1"]) == ("1", "3", "5", "6", "7")
    assert expand_numbers([("17", "19.5")]) == ("17", "18", "19", "19.5")
    assert expand_numbers([("2.5", "4")]) == ("2.5", "3", "4")
    assert expand_numbers(["x", None, ("a", "2")]) == ()
    assert chapter_numbers([("41", "44"), ("50", None), ("24.5", None)]) == ("24.5", "41", "42", "43", "44", "50")


def test_holds_text():
    assert holds_text(["1", "2", "3", "5"]) == "Volumes 1-3, 5"
    assert holds_text([], ["1", "2", ("3", "10"), "12.5"]) == "Chapters 1-10, 12.5"
    assert holds_text(["1"], ["9"]) == "Volumes 1 · Chapters 9"
    assert holds_text([], []) == "Nothing"


def test_english_and_match_texts():
    assert english_text(None) == ""
    licensed = SeriesKnowledge(source="mangapixer", english_publishers=(EnglishPublisher("Example Press"),
                                                                        EnglishPublisher("Second Press")))
    assert english_text(licensed) == "Example Press (+1)"
    assert english_text(SeriesKnowledge(source="own-matcher", licensed_en=False)) == "Not licensed"
    assert english_text(SeriesKnowledge(source="own-matcher", licensed_en=True)) == "Licensed"
    assert english_text(SeriesKnowledge(source="own-matcher")) == ""
    assert match_text(from_mangapixer_item(item())) == "MangaPixer: confirmed"
    assert match_text(SeriesKnowledge(source="own-matcher", link_state="NeedsReview")) == "MangaUpdates: needs review"
    assert match_text(None) == ""


def test_group_gaps_text():
    st = _state(Gap(GAP_VOLUME, "21"), Gap(GAP_VOLUME, "22"), Gap(GAP_VOLUME, "23"), Gap(GAP_CHAPTER, "41", "44"),
                Gap(GAP_UPGRADE, "13"))
    assert group_gaps_text(st, GROUP_VOLUMES) == "Vol. 21-23"
    assert group_gaps_text(st, GROUP_CHAPTERS) == "Ch. 41-44"
    assert group_gaps_text(st, GROUP_UPGRADES) == "Upgrade vol. 13"
    assert group_gaps_text(_state(), GROUP_VOLUMES) == ""


def _wanted(state, volumes=None, knowledge=None, **kw):
    args = dict(folder="/lib/Example Quest", title="Example Quest", english_title=None, state=state,
                knowledge=knowledge, held=("1",), series_id=7, volumes=volumes)
    args.update(kw)
    return wanted_series(**args)


def test_one_entry_per_kind_of_gap():
    st = _state(Gap(GAP_VOLUME, "2"), Gap(GAP_VOLUME, "3"), Gap(GAP_CHAPTER, "41", "42"), Gap(GAP_UPGRADE, "13"))
    target = VolumeTarget(series_id=7, folder="/lib/Example Quest", title="Example Quest", titles=("Example Quest",),
                          missing=("2", "3"), held=("1",))
    got = {w.group: w for w in _wanted(st, volumes=Availability(True, "", target),
                                       knowledge=from_mangapixer_item(item()))}
    assert set(got) == {GROUP_VOLUMES, GROUP_CHAPTERS, GROUP_UPGRADES}
    vols = got[GROUP_VOLUMES]
    assert vols.findable and vols.reason == "" and vols.missing == ("2", "3") and vols.gaps == "Vol. 2-3"
    assert vols.series_id == 7 and vols.held == ("1",) and vols.titles[:1] == ("Example Quest",)
    assert "Example Quest: Second Name" in vols.titles
    chs = got[GROUP_CHAPTERS]
    assert not chs.findable and chs.reason == REASON_CHAPTERS and chs.missing == ("41", "42")
    ups = got[GROUP_UPGRADES]
    assert not ups.findable and ups.reason == REASON_UPGRADES and ups.missing == ("13",)
    assert wanted_label(got) == "Get the missing volumes"
    assert wanted_label([GROUP_CHAPTERS]) == "Get the missing chapters"
    assert wanted_label([GROUP_UPGRADES]) == "Get the volume upgrades" and wanted_label([]) == ""


def test_volumes_carry_the_rules_reason_and_nothing_without_gaps():
    st = _state(Gap(GAP_VOLUME, "2"))
    (w,) = _wanted(st, volumes=Availability(False, "Not licensed in English: there are no English volumes to look for."))
    assert not w.findable and w.reason.startswith("Not licensed")
    (w,) = _wanted(st, volumes=None, english_title="Exemplar", title="Example Quest")
    assert not w.findable and w.reason == "downloads are off" and w.titles == ("Exemplar", "Example Quest")
    assert _wanted(_state(state=State.COMPLETE)) == [] and _wanted(None) == []
