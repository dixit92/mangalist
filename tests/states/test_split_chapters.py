"""Split chapters (2.1 + 2.2 = chapter 2): MangaPixer's rule (MissingUnits.SplitsOf, 1.29.1 / 1.30.0), ported."""

from __future__ import annotations

from decimal import Decimal

from mangalist.knowledge import from_mangapixer_item
from mangalist.split_chapters import splits_of
from mangalist.states import GAP_CHAPTER, InventorySnapshot, State, compute_state

from .helpers import TODAY, item


def D(*values):
    return [Decimal(str(v)) for v in values]


def test_parts_are_told_from_extras():
    # MangaPixer's own case: 2.1 + 2.2 parts; 3 + 3.2 (the file 3 is the first part); 5.1-5.5 (.5 continues .4);
    # 6.1 + 6.3 (6.2 is missing).
    s = splits_of(D(2.1, 2.2, 3, 3.2, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.3))
    assert sorted(s.chapters) == [2, 3, 5, 6]
    assert sorted(s.parts) == D(2.1, 2.2, 3.2, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.3)
    assert s.missing_parts == tuple(D(6.2))


def test_extras_stay_extras():
    # A lone .5 (with or without its whole), a lone .2, a .1 next to its whole file, 14.25 / 14.75; 31 + 31.5 + 31.6
    # (listed extras, not a split missing 31.2-31.5); a run that starts late (15.4 + 15.5).
    s = splits_of(D(10, 10.5, 11.5, 12.2, 13, 13.1, 14.25, 14.75, 31, 31.5, 31.6, 15.4, 15.5))
    assert not s.chapters and not s.parts and not s.missing_parts


def test_a_range_file_is_a_whole_chapter():
    s = splits_of(D(3.2), ranges=[(Decimal(1), Decimal(4))])           # a "Ch. 1-4" file holds chapter 3: 3.2 is its part 2
    assert s.chapters == {3} and s.parts == {Decimal("3.2")}


def _finished_unlicensed(latest="14"):
    it = item()
    rec = it["record"]
    rec.update(licensedEn=False, englishPublishers=[], latestChapter=latest, originStatus="Complete",
               completedInOrigin=True, totalChapters=int(latest), statusText=f"{latest} Chapters (Complete)")
    it["volumes"] = None
    it["completion"] = None
    return from_mangapixer_item(it)


def test_a_chapter_folder_of_split_chapters_misses_nothing():
    # The owner's case (synthetic numbers): chapters 1-14 with 2, 3, 8-13 split into parts - MangaPixer says "you have
    # it all"; MangaList listed chapters 2-3 and 8-13 as missing.
    held = D(1, 2.1, 2.2, 3.1, 3.2, 3.3, 4, 5, 6, 7, 8.1, 8.2, 9.1, 9.2, 10.1, 10.2, 11.1, 11.2, 12.1, 12.2, 13.1, 13.2, 14)
    s = compute_state(InventorySnapshot(held_chapters=held), _finished_unlicensed(), folder_empty=False, today=TODAY)
    assert not s.gaps_of(GAP_CHAPTER), s.gaps
    assert s.state != State.MISSING_CHAPTERS


def test_a_missing_part_is_a_chapter_gap():
    held = D(1, 2.1, 2.3, 3)                                            # 2.2 is missing
    s = compute_state(InventorySnapshot(held_chapters=held), _finished_unlicensed("3"), folder_empty=False, today=TODAY)
    assert [g.start for g in s.gaps_of(GAP_CHAPTER)] == ["2.2"] and s.state == State.MISSING_CHAPTERS
