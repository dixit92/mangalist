"""MangaDex's readable chapters (MangaPixer 1.43.0 ``companions.mangadexReleased.readable``; owner, 2026-10-13, answer 8:
"ask MangaPixer to also export the readable-only latest chapter"): where known, their highest whole chapter joins the
chapter target of every series - the largest wins - and the released list's latest chapter (taken-down ones included) is
not used; where not known, today's rule stays (MangaDex fills in only for Can't tell). Synthetic items, made-up titles."""

from __future__ import annotations

from decimal import Decimal

from mangalist import knowledge as kn
from mangalist.knowledge import from_mangapixer_item
from mangalist.states import GAP_CHAPTER, InventorySnapshot, State, compute_state

from .helpers import TODAY, item


def released(latest, chapters=None, readable=None, language="en"):
    rel = {"language": language, "latestChapter": latest,
           "chapters": chapters if chapters is not None else int(Decimal(latest)) if latest else 0,
           "fetchedAt": "2026-10-16T12:00:00.000Z"}
    if readable is not None:
        rel["readable"] = readable
    return rel


def readable(latest, chapters=None):
    return {"latestWholeChapter": latest, "chapters": chapters if chapters is not None else int(latest or 0)}


def series(latest="20", rel=None, **changes):
    """An unlicensed series MangaPixer links, MangaUpdates' latest chapter *latest*."""
    it = item(record__licensedEn=False, record__englishPublishers=[], record__latestChapter=latest, volumes=None,
              completion=None, **changes)
    if rel is not None:
        it["companions"]["mangadexReleased"] = rel
    return from_mangapixer_item(it)


def state(knowledge, held=None, **kw):
    kw.setdefault("folder_empty", False)
    return compute_state(InventorySnapshot(held_chapters=held or []), knowledge, today=TODAY, **kw)


def chapters(lo, hi):
    return [str(n) for n in range(lo, hi + 1)]


# --- knowledge ---------------------------------------------------------------------------------------------------------


def test_the_readable_block_is_read_and_the_released_numbers_keep_counting_taken_down_ones():
    k = series(rel=released("30", 30, readable("18", 17)))
    r = k.mangadex_released
    assert (r.latest_chapter, r.chapters) == ("30", 30)                          # taken-down ones still counted
    assert r.readable == kn.MangaDexReadable(latest_whole_chapter="18", chapters=17)
    assert k.mangadex_readable.latest_whole == Decimal(18) and k.mangadex_latest == Decimal(30)


def test_absent_or_malformed_readable_is_unknown_and_null_is_nothing_readable():
    assert series(rel=released("30", 30)).mangadex_readable is None
    for bad in ("18", [], {"latestWholeChapter": "18"}, {"latestWholeChapter": "18", "chapters": -1},
                {"latestWholeChapter": "x", "chapters": 3}, {"latestWholeChapter": True, "chapters": 3},
                {"latestWholeChapter": "18", "chapters": True}):
        k = series(rel=released("30", 30, bad))
        assert k.mangadex_released is not None and k.mangadex_readable is None, bad
    none = series(rel=released("30", 30, readable(None, 0))).mangadex_readable
    assert none is not None and none.latest_whole is None and none.chapters == 0
    # another language: not counted (the states compare with English)
    assert series(rel=released("30", 30, readable("18"), language="fr")).mangadex_readable is None


# --- states ------------------------------------------------------------------------------------------------------------


def test_readable_chapters_past_mangaupdates_are_new_chapters_for_every_series():
    # MangaUpdates says 20 and the folder holds 1-20: up to date by MangaUpdates; MangaDex serves 22 already
    assert state(series(rel=released("22", 22))).state != State.MISSING_CHAPTERS     # before 1.43.0: not used
    s = state(series(rel=released("22", 22, readable("22"))), chapters(1, 20))
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("21", "22"),)
    assert any("MangaDex has readable English chapters up to 22 (MangaUpdates says 20)" in r for r in s.reasons)


def test_taken_down_chapters_make_no_gap_when_the_readable_list_is_known():
    # MangaDex lists 111 chapters, most taken down; 18 are readable - MangaUpdates' 20 stays the target
    s = state(series(rel=released("111", 111, readable("18", 18))), chapters(1, 20))
    assert s.state == State.UP_TO_DATE and not s.gaps and not any("MangaDex" in r for r in s.reasons)


def test_the_largest_target_wins_never_the_readable_one_below_it():
    s = state(series(rel=released("25", 25, readable("12"))), chapters(1, 15))
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("16", "20"),)


def test_nothing_readable_adds_no_target_and_the_released_latest_is_not_used():
    held = chapters(1, 5)
    # today's rule (no readable block): MangaDex's 9 settles a Can't tell
    assert state(series(latest=None, rel=released("9", 9)), held).missing_chapters == (("6", "9"),)
    # readable known and empty: no target from MangaDex at all
    s = state(series(latest=None, rel=released("9", 9, readable(None, 0))), held)
    assert s.state == State.CANT_TELL and not s.gaps
    # readable known: its 7, not the released 9
    s = state(series(latest=None, rel=released("9", 9, readable("7"))), held)
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("6", "7"),)
    assert any("nothing else gives a latest chapter" in r for r in s.reasons)


def test_an_empty_folder_wants_the_readable_chapters():
    s = state(series(latest=None, rel=released("40", 40, readable("12"))), folder_empty=True)
    assert s.state == State.WANTED_SCANLATION and s.missing_chapters == (("1", "12"),)


def test_readable_counts_only_for_a_matched_tracked_series():
    it = series(latest="20", rel=released("25", 25, readable("25")), link={"state": "NeedsReview"}, record=None)
    assert not [g for g in state(it, chapters(1, 3)).gaps if g.kind == GAP_CHAPTER]


def test_a_finished_series_total_caps_the_readable_chapter():
    # MangaDex can number an extra after the last chapter: a finished series with a known total stays Complete / up to
    # date by it, while an ongoing series still gets the readable chapters (integrator, 2026-10-17)
    from dataclasses import replace

    k = series(rel=released("22", 22, readable("22")))
    finished = replace(k, completed_in_origin=True, total_chapters=Decimal(20))
    s = state(finished, chapters(1, 20))
    assert s.state != State.MISSING_CHAPTERS and not s.missing_chapters
    assert state(k, chapters(1, 20)).state == State.MISSING_CHAPTERS                 # ongoing: 21-22 still wanted
