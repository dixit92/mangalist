"""Which chapters a held volume contains when the volume list no longer says (mangalist.volume_contents; owner,
2026-11-06: "fix the gap logic so it knows what volumes 7-9 contain."). A made-up mixed series: volumes 1-9 held, loose
chapters from 76; the list maps volumes 1-6 (chapters 1-50) and not 7-9 (the source lost its map); the series' records
put chapters 51-58 in volume 7, 59-66 in 8 and 67-75 in 9. Invented titles and numbers."""

from __future__ import annotations

from decimal import Decimal as D

from mangalist.inventory import Span, compute_inventory
from mangalist.knowledge import from_mangapixer_item
from mangalist.store.units import Unit
from mangalist.states import GAP_CHAPTER, InventorySnapshot, State, compute_state
from mangalist.volume_contents import (SOURCE_FILED, SOURCE_NAME, _batch_claims, claim, contents_text,
                                       held_volume_contents)

from .helpers import TODAY, item, vol

ENDS = {"1": ("1", "5"), "2": ("6", "14"), "3": ("15", "23"), "4": ("24", "32"), "5": ("33", "41"), "6": ("42", "50")}


def lost_map_item(**changes):
    """Volumes 1-6 mapped; 7-9 listed with MangaPixer's estimate only (or nothing); 9 English volumes out."""
    items = [vol(n, a, b) for n, (a, b) in ENDS.items()]
    items += [dict(vol(str(n), str(a), str(b)), chaptersEstimated=True, sources=["estimate"])
              for n, a, b in ((7, 51, 62), (8, 63, 74))] + [vol("9")]
    base = dict(record__latestChapter="90", record__originVolumes=12,
                record__englishPublishers=[{"name": "Example Press", "volumes": 9, "chapters": None,
                                            "status": "Ongoing", "omnibus": False}],
                volumes={"source": "mangadex", "fetchedAt": "2026-11-04T00:00:00.000Z", "items": items},
                completion=None, companions={})
    base.update(changes)
    return item(**base)


def tagged(lo, hi, volume):
    return [claim(volume, n, n, SOURCE_NAME) for n in range(lo, hi + 1)]


CLAIMS = tagged(51, 58, 7) + tagged(59, 66, 8) + tagged(67, 75, 9)


def state(chapters, claims=(), knowledge=None, volumes=range(1, 10), **kw):
    inv = InventorySnapshot(held_volumes=[str(v) for v in volumes], held_chapters=[str(c) for c in chapters],
                            recorded_volume_contents=tuple(claims))
    k = knowledge if knowledge is not None else from_mangapixer_item(lost_map_item())
    return compute_state(inv, k, folder_empty=False, today=TODAY, **kw)


def missing(st):
    return [(g.start, g.end) for g in st.gaps if g.kind == GAP_CHAPTER]


# --- the rule ----------------------------------------------------------------------------------------------------


def test_held_volumes_the_list_does_not_map_take_the_span_their_claims_name():
    mapped = {D(n): (D(a), D(b)) for n, (a, b) in ENDS.items()}
    got = held_volume_contents(CLAIMS, range(1, 10), mapped)
    assert got == {D(7): (D(51), D(58)), D(8): (D(59), D(66)), D(9): (D(67), D(75))}
    assert contents_text(got) == "volume 7: chapters 51-58, volume 8: chapters 59-66, volume 9: chapters 67-75"


def test_a_span_runs_from_the_lowest_to_the_highest_claim():
    got = held_volume_contents([claim(7, 51), claim(7, 58), claim(7, "55.5")], [7], {})
    assert got == {D(7): (D(51), D(58))}


def test_the_list_wins_and_volumes_not_held_get_nothing():
    claims = [claim(6, 49), claim(6, 52), claim(10, 76)]
    got = held_volume_contents(claims + [claim(7, 53)], [6, 7], {D(6): (D(42), D(50))})
    assert got == {D(7): (D(53), D(53))}          # 6: the list's 42-50 stands; 10: not held


def test_a_span_that_does_not_fit_is_dropped_with_the_one_it_collides_with():
    # numbering that restarts per volume: both volumes claim chapters 1-5
    restart = tagged(1, 5, 1) + tagged(1, 5, 2)
    assert held_volume_contents(restart, [1, 2], {}) == {}
    # a recorded span inside another volume's mapped range: unknown (the list's range is never dropped)
    assert held_volume_contents([claim(7, 45), claim(7, 58)], [6, 7], {D(6): (D(42), D(50))}) == {}
    # out of order: volume 8 before volume 7's chapters
    assert held_volume_contents([claim(8, 40), claim(7, 51)], [7, 8], {}) == {}
    # a volume that fits keeps its span when two others collide
    claims = tagged(1, 5, 1) + tagged(1, 5, 2) + tagged(20, 25, 4)
    assert held_volume_contents(claims, [1, 2, 4], {}) == {D(4): (D(20), D(25))}


def test_claims_of_a_replacement_batch():
    files = ('[{"chapters": "24", "volume": "4"}, {"chapters": "1-3, 5", "volume": "1"},'
             ' {"chapters": "8-9", "volume": "1, 2"}, {"chapters": "x", "volume": "2"}, "junk"]')
    got = _batch_claims(files)
    assert got == [claim(4, 24, 24, SOURCE_FILED), claim(1, 1, 5, SOURCE_FILED)]
    assert _batch_claims("not json") == [] and _batch_claims(None) == []


# --- in the states -------------------------------------------------------------------------------------------------


def test_without_records_the_held_volumes_chapters_are_missing():
    st = state(range(76, 91))
    assert st.state == State.MISSING_CHAPTERS and missing(st) == [("51", "75")]


def test_the_records_keep_the_held_volumes_chapters_off_the_missing_list():
    st = state(range(76, 91), CLAIMS)
    assert st.state == State.UP_TO_DATE and not st.gaps
    assert any("the series' own records do" in r and "volume 9: chapters 67-75" in r for r in st.reasons)


def test_a_chapter_no_volume_claims_stays_missing():
    st = state(range(87, 91), CLAIMS)                               # 76-86 gone too: no volume claims them
    assert missing(st) == [("76", "86")]
    st = state([*range(76, 80), *range(81, 91)], CLAIMS)            # a hole after the volumes
    assert missing(st) == [("80", None)]


def test_records_for_some_unmapped_volumes_only_leave_the_others_as_before():
    st = state(range(76, 91), tagged(67, 75, 9))                    # nothing says what 7 and 8 hold
    assert missing(st) == [("51", "66")]


def test_the_records_never_add_a_gap():
    # no volume list at all: the comparison starts at the first loose chapter, records or not (14 volumes, 48-69)
    k = from_mangapixer_item(item(volumes=None, completion=None, record__latestChapter="69"))
    before = state(range(48, 70), (), k, volumes=range(1, 15))
    after = state(range(48, 70), tagged(30, 33, 10), k, volumes=range(1, 15))
    assert before.state == after.state and before.gaps == after.gaps == ()
    # the same with a hole the records do not cover
    holed = [c for c in range(48, 70) if c != 60]
    assert state(holed, (), k, volumes=range(1, 15)).gaps == state(holed, tagged(30, 33, 10), k,
                                                                   volumes=range(1, 15)).gaps


def test_numbering_that_restarts_or_a_declared_edition_uses_no_records():
    assert missing(state(range(76, 91), CLAIMS, restarts=("chapter",))) == []      # chapters not compared at all
    assert state(range(76, 91), CLAIMS, restarts=("volume",)).reasons == state(range(76, 91), (),
                                                                              restarts=("volume",)).reasons
    edition = from_mangapixer_item(lost_map_item(completion={"answer": "MissingSome", "edition": "omnibus",
                                                              "volumeTotalOverride": 12}))
    assert state(range(76, 91), CLAIMS, edition).gaps == state(range(76, 91), (), edition).gaps


def test_unbundled_chapters_also_skip_what_the_records_put_in_a_volume():
    st = state(range(76, 91), CLAIMS, unbundled=True)
    assert not missing(st)


def test_an_estimated_range_is_unknown_coverage_in_the_inventory():
    units = [Unit(rel_path=f"v{n:02d}.cbz", kind="volume", vol_from=str(n), vol_to=str(n)) for n in (6, 9)]
    inv = compute_inventory(units, [vol("6", "42", "50"),
                                    dict(vol("9", "67", "86"), chaptersEstimated=True, sources=["estimate"])])
    assert inv.coverage == {D(6): Span(D(42), D(50)), D(9): None}
    assert [str(s) for s in inv.covered] == ["42-50"] and inv.volumes_unknown_coverage == (D(9),)
