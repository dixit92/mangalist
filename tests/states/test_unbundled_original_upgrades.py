"""A volume of the original edition that is not out in English, with Unbundled chapters on (owner, 2026-10-16, question
73: "Keep both, treat the volume as an upgrade"): its chapters are wanted as chapters (question 15) and the volume is an
upgrade, not a missing volume - Missing chapters while chapters are missing, Upgrade available once they are all held.
The synthetic item of ``helpers`` without English publishers (the English edition is chapters in an app): latest chapter
41, the original edition's volumes 1 (ch. 1-8), 2 (9-16), 3 (17-24). Made-up titles."""

from __future__ import annotations

from mangalist.gui.list_text import wanted_series
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES
from mangalist.gui.volumes_target import find_volumes_availability
from mangalist.knowledge import from_mangapixer_item
from mangalist.states import GAP_CHAPTER, GAP_UPGRADE, GAP_VOLUME, InventorySnapshot, State, compute_state

from .helpers import TODAY, item, vol


def released_list(*vols):
    return {"source": "merged", "fetchedAt": "2026-10-02T12:00:00.000Z", "items": list(vols)}


ORIGINAL = released_list(vol("1", "1", "8"), vol("2", "9", "16"), vol("3", "17", "24"))


def k(volumes=ORIGINAL):
    return from_mangapixer_item(item(volumes=volumes, record__englishPublishers=[]))


def state(unbundled=True, knowledge=None, **inv):
    return compute_state(InventorySnapshot(**inv), knowledge or k(), folder_empty=False, today=TODAY,
                         unbundled=unbundled)


def gaps(st, kind):
    return [(g.start, g.end) for g in st.gaps if g.kind == kind]


def chapters(lo, hi):
    return [str(n) for n in range(lo, hi + 1)]


def test_without_unbundled_the_original_volumes_are_missing_volumes():
    off = state(unbundled=False, held_volumes=["1"])
    assert off.state == State.MISSING_VOLUMES and gaps(off, GAP_VOLUME) == [("2", None), ("3", None)]
    assert not gaps(off, GAP_CHAPTER) and not gaps(off, GAP_UPGRADE)


def test_with_unbundled_the_volume_is_an_upgrade_while_its_chapters_are_wanted():
    on = state(held_volumes=["1"])
    assert on.state == State.MISSING_CHAPTERS
    assert gaps(on, GAP_CHAPTER) == [("9", "41")] and not gaps(on, GAP_VOLUME)
    assert gaps(on, GAP_UPGRADE) == [("2", None), ("3", None)]
    assert on.gaps[1].note == "original volume; chapters 9-16 come as chapters (Unbundled chapters) - the volume " \
                              "replaces them"
    assert on.gaps_text == "Ch. 9-41  ·  Upgrade vol. 2-3" and on.n_missing == 33       # upgrades are not missing


def test_once_its_chapters_are_held_the_series_reads_upgrade_available():
    some = state(held_volumes=["1"], held_chapters=chapters(9, 20))
    assert some.state == State.MISSING_CHAPTERS and gaps(some, GAP_CHAPTER) == [("21", "41")]
    assert gaps(some, GAP_UPGRADE) == [("2", None), ("3", None)]
    assert some.gaps[1].note == "chapters 9-16 held"           # volume 2's chapters are all held: the plain upgrade
    every = state(held_volumes=["1"], held_chapters=chapters(9, 41))
    assert every.state == State.UPGRADE and not gaps(every, GAP_CHAPTER) and not gaps(every, GAP_VOLUME)
    assert gaps(every, GAP_UPGRADE) == [("2", None), ("3", None)]
    # the volume arrives: no gap left for it
    filed = state(held_volumes=["1", "2"], held_chapters=chapters(17, 41))
    assert gaps(filed, GAP_UPGRADE) == [("3", None)] and filed.state == State.UPGRADE


def test_a_volume_before_where_the_unbundled_chapters_start_stays_a_missing_volume():
    # volume 3 is held, so the unbundled chapters start after chapter 24: volume 2's chapters come only with it
    on = state(held_volumes=["1", "3"])
    assert on.state == State.MISSING_VOLUMES and gaps(on, GAP_VOLUME) == [("2", None)]
    assert gaps(on, GAP_CHAPTER) == [("25", "41")] and not gaps(on, GAP_UPGRADE)


def test_without_the_first_chapter_to_get_no_chapter_is_compared_and_the_volumes_stay_gaps():
    # volume 4 is held but the list does not map it: nothing is compared until the owner answers
    on = state(held_volumes=["1", "4"])
    assert on.unbundled_ask is not None and on.unbundled_ask.needed
    assert gaps(on, GAP_VOLUME) == [("2", None), ("3", None)] and not gaps(on, GAP_CHAPTER)
    assert not gaps(on, GAP_UPGRADE)


def test_to_get_lists_the_upgrade_with_the_same_nyaa_rule_as_the_missing_volume():
    knowledge = k()
    rows = {}
    for unbundled in (False, True):
        st = state(unbundled=unbundled, held_volumes=["1"])
        volumes = find_volumes_availability(series_id=7, folder="/lib/Example Quest", title="Example Quest",
                                            english_title=None, knowledge=knowledge, state=st, held=("1",))
        rows[unbundled] = {w.group: w for w in wanted_series(folder="/lib/Example Quest", title="Example Quest",
                                                             english_title=None, state=st, knowledge=knowledge,
                                                             held=("1",), series_id=7, volumes=volumes)}
    assert set(rows[False]) == {GROUP_VOLUMES}
    assert set(rows[True]) == {GROUP_CHAPTERS, GROUP_UPGRADES}
    up = rows[True][GROUP_UPGRADES]
    assert up.gaps == "Upgrade vol. 2-3" and up.missing == ("2", "3")
    assert up.findable == rows[False][GROUP_VOLUMES].findable          # the find pass looks for it as before
    assert rows[True][GROUP_CHAPTERS].gaps == "Ch. 9-41"
