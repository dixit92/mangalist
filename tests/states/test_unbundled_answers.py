"""Unbundled Chapters after the owner's answers of 2026-10-15 (questions 14-16, vault note "Manga-list - Owner Questions
2026-10-13"): the first chapter to get where the volume list does not map the newest held volume (asked once, stored,
asked again once a newer unmapped volume arrives), the chapters of a volume not out in English wanted as chapters, and a
Complete series whose unbundled chapters become an upgrade when the volume is out. The synthetic item of ``helpers``:
licensed, latest chapter 41, English volumes 1 (ch. 1-8), 2 (9-16), 3 (17-24.5) out, 4 announced. Made-up titles."""

from __future__ import annotations

from decimal import Decimal

from mangalist.knowledge import from_mangapixer_item
from mangalist.states import (ATTENTION_UNBUNDLED_START, GAP_CHAPTER, GAP_UPGRADE, GAP_VOLUME, InventorySnapshot,
                              State, compute_state, unbundled_suggestion)
from mangalist.store.units import Unit
from mangalist.unbundled import UnbundledChoice
from mangalist.upgrades import chapters_replaced

from .helpers import TODAY, item, vol


def k(**changes):
    return from_mangapixer_item(item(**changes))


def state(knowledge, unbundled=True, **inv):
    return compute_state(InventorySnapshot(**inv), knowledge, folder_empty=False, today=TODAY, unbundled=unbundled)


def gaps(st, kind):
    return [(g.start, g.end) for g in st.gaps if g.kind == kind]


def chapters(lo, hi):
    return [str(n) for n in range(lo, hi + 1)]


def released_list(*vols):
    return {"source": "merged", "fetchedAt": "2026-10-02T12:00:00.000Z", "items": list(vols)}


# volume 2 is out but the list does not say which chapters it collects
UNMAPPED = released_list(vol("1", "1", "8", "2025-01-01", "released"), vol("2", None, None, "2025-05-01", "released"),
                         vol("3", None, None, "2025-09-01", "released"))


def choice(first, for_volume):
    return UnbundledChoice(Decimal(first), Decimal(for_volume))


# --- question 14: the first chapter to get --------------------------------------------------------------------------


def test_without_an_answer_nothing_more_is_compared_and_the_series_asks():
    off = state(k(volumes=UNMAPPED), unbundled=False, held_volumes=["1", "2", "3"])
    on = state(k(volumes=UNMAPPED), held_volumes=["1", "2", "3"])
    assert (on.state, on.gaps) == (off.state, off.gaps)
    ask = on.unbundled_ask
    assert (ask.volume, ask.mapped_volume, ask.mapped_end, ask.current, ask.needed) == ("3", "1", "8", None, True)
    assert ask.suggestion == 25 == unbundled_suggestion(k(volumes=UNMAPPED).volumes, Decimal(3))   # 8 per volume
    assert not ask.not_held
    assert ATTENTION_UNBUNDLED_START in on.needs_attention and ATTENTION_UNBUNDLED_START not in off.needs_attention
    assert any("set the first chapter to get" in r for r in on.reasons)
    assert off.unbundled_ask is None


def test_the_answer_says_where_the_chapters_start():
    st = state(k(volumes=UNMAPPED), choice("25", "3"), held_volumes=["1", "2"])
    assert gaps(st, GAP_CHAPTER) == [("25", "41")] and st.state == State.MISSING_VOLUMES       # volume 3 still missing
    assert gaps(st, GAP_VOLUME) == [("3", None)]
    assert any(r.startswith("Unbundled chapters from chapter 25 (your first chapter to get") for r in st.reasons)
    assert st.unbundled_ask.current == "25" and not st.unbundled_ask.needed
    assert ATTENTION_UNBUNDLED_START not in st.needs_attention


def test_held_chapters_after_the_answer_count_and_the_unmapped_volume_keeps_its_chapters():
    # once chapters 17-30 are held the folder is mixed; the answer still says volume 2 holds 9-16: never asked for
    held = dict(held_volumes=["1", "2"], held_chapters=chapters(17, 30))
    st = state(k(volumes=UNMAPPED), choice("17", "2"), **held)
    assert gaps(st, GAP_CHAPTER) == [("31", "41")]
    off = state(k(volumes=UNMAPPED), unbundled=False, **held)
    assert ("9", "16") in gaps(off, GAP_CHAPTER)          # the mixed-folder rule alone cannot tell (no answer)


def test_the_question_is_about_the_newest_volume_out_not_the_newest_held():
    # owner, 2026-10-17: volumes 1-2 held, 1-13 out in English, no volume list - the question was about volume 2,
    # whose chapters volumes 3-13 bring; it is about volume 13, the first chapter after the volumes out
    st = state(k(volumes=UNMAPPED), held_volumes=["1", "2"])
    ask = st.unbundled_ask
    assert (ask.volume, ask.not_held, ask.needed) == ("3", True, True) and gaps(st, GAP_VOLUME) == [("3", None)]
    old = state(k(volumes=UNMAPPED), choice("17", "2"), held_volumes=["1", "2"])   # an answer given for volume 2
    assert old.unbundled_ask.stale and not gaps(old, GAP_CHAPTER)                   # asked again, nothing assumed


def test_a_newer_unmapped_volume_makes_it_ask_again():
    st = state(k(volumes=UNMAPPED), choice("17", "2"), held_volumes=["1", "2", "3"])
    assert not gaps(st, GAP_CHAPTER)
    ask = st.unbundled_ask
    assert (ask.volume, ask.current, ask.given_for, ask.stale) == ("3", None, "2", True)
    assert ATTENTION_UNBUNDLED_START in st.needs_attention
    assert any("volume 3 arrived after the first chapter to get (17) was set for volume 2" in r for r in st.reasons)


def test_once_the_list_maps_the_volume_the_list_wins():
    st = state(k(), choice("30", "3"), held_volumes=["1", "2", "3"])
    assert gaps(st, GAP_CHAPTER) == [("25", "41")] and st.unbundled_ask is None
    assert "Unbundled chapters after chapter 24.5 (the end of volume 3)" in st.reasons


def test_a_released_mapped_volume_after_the_answer_still_brings_its_chapters():
    vols = released_list(vol("1", "1", "8", "2025-01-01", "released"), vol("2", None, None, "2025-05-01", "released"),
                         vol("3", "17", "24", "2025-09-01", "released"))
    st = state(k(volumes=vols), choice("17", "2"), held_volumes=["1", "2"])
    assert gaps(st, GAP_VOLUME) == [("3", None)]
    assert gaps(st, GAP_CHAPTER) == [("25", "41")]         # 17-24 come with volume 3
    assert "Unbundled chapters after chapter 24 (the end of volume 3)" in st.reasons


def test_nothing_mapped_at_all_suggests_from_the_latest_chapter_and_the_original_volume_count():
    vols = released_list(vol("1", None, None, "2025-01-01", "released"))
    st = state(k(volumes=vols), held_volumes=["1"])                  # 41 chapters over 5 original volumes
    assert st.unbundled_ask.suggestion == 9 and st.unbundled_ask.mapped_volume is None
    assert state(k(volumes=vols, record__originVolumes=None), held_volumes=["1"]).unbundled_ask.suggestion == 1
    assert unbundled_suggestion([], Decimal(9), Decimal(41), Decimal(5)) == 1          # more volumes held than known
    late = released_list(vol("1", "1", "8", "2025-01-01", "released"), vol("9", None, None, "2025-05-01", "released"))
    assert state(k(volumes=late), held_volumes=["1", "9"]).unbundled_ask.suggestion == 42   # after the latest: none yet
    answered = state(k(volumes=vols), choice("9", "1"), held_volumes=["1"])
    assert gaps(answered, GAP_CHAPTER) == [("9", "41")]


# --- question 15: a volume not out in English -----------------------------------------------------------------------


def test_the_chapters_of_an_original_volume_not_out_in_english_are_wanted_as_chapters():
    # no English volume list (the English edition is chapters in an app): the original volumes whose chapters are out
    vols = released_list(vol("1", "1", "8"), vol("2", "9", "16"), vol("3", "17", "24"))
    knowledge = k(volumes=vols, record__englishPublishers=[])
    off = state(knowledge, unbundled=False, held_volumes=["1"])
    on = state(knowledge, held_volumes=["1"])
    assert gaps(off, GAP_VOLUME) == [("2", None), ("3", None)] and not gaps(off, GAP_CHAPTER)
    # the volumes are kept as upgrades (owner, 2026-10-16, question 73: "Keep both, treat the volume as an upgrade")
    assert not gaps(on, GAP_VOLUME) and gaps(on, GAP_UPGRADE) == [("2", None), ("3", None)]
    assert gaps(on, GAP_CHAPTER) == [("9", "41")]          # not only 25-41: 9-24 may be out in English already
    assert "Unbundled chapters after chapter 8 (the end of volume 1)" in on.reasons


def test_a_released_english_volume_the_folder_lacks_still_brings_its_own_chapters():
    on = state(k(), held_volumes=["1", "2"])
    assert gaps(on, GAP_VOLUME) == [("3", None)] and gaps(on, GAP_CHAPTER) == [("25", "41")]


# --- question 16: a Complete series ---------------------------------------------------------------------------------


def _complete(**changes):
    # finished, the English edition stopped at volume 3 (chapter 24.5); the original went on to chapter 41
    return k(record__completedInOrigin=True, record__originStatus="Complete",
             record__englishPublishers=[{"name": "Example Press", "volumes": 3, "chapters": None,
                                         "status": "Completed", "omnibus": False}], **changes)


def test_a_complete_series_may_have_it_and_its_chapters_become_an_upgrade():
    assert state(_complete(), unbundled=False, held_volumes=["1", "2", "3"]).state == State.COMPLETE
    on = state(_complete(), held_volumes=["1", "2", "3"])
    assert on.state == State.MISSING_CHAPTERS and gaps(on, GAP_CHAPTER) == [("25", "41")]
    held = state(_complete(), held_volumes=["1", "2", "3"], held_chapters=chapters(25, 41))
    assert held.state == State.COMPLETE and not held.gaps
    # later an English volume 4 (25-32) comes out after all: an upgrade (Complete + Upgrade available)
    vols = item()["volumes"]
    vols["items"][3] = vol("4", "25", "32", "2026-09-01", "released")
    later = state(_complete(volumes=vols), held_volumes=["1", "2", "3"], held_chapters=chapters(25, 41))
    assert gaps(later, GAP_UPGRADE) == [("4", None)] and later.complete_with_upgrade
    assert not gaps(later, GAP_CHAPTER)


def test_the_filed_volume_replaces_the_unbundled_chapter_files_beside_the_volumes():
    # the unbundled chapters were filed in the series folder itself, next to the volumes (no chapters held before)
    units = [Unit(rel_path=f"Example Quest - Vol. 00{n} [Grp].cbz", kind="volume", vol_from=str(n), vol_to=str(n))
             for n in (1, 2, 3)]
    units += [Unit(rel_path=f"Ch. 00{n}.00 [Grp].cbz", kind="chapter", ch_from=str(n), ch_to=str(n))
              for n in range(25, 42)]
    vols = item()["volumes"]
    vols["items"][3] = vol("4", "25", "32", "2026-09-01", "released")
    coverage = chapters_replaced(units, _complete(volumes=vols).volumes, ["4"])
    assert [r for r, _c, _v in coverage.replaced] == [f"Ch. 00{n}.00 [Grp].cbz" for n in range(25, 33)]
    assert all(v == "4" for _r, _c, v in coverage.replaced)
