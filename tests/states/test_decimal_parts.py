"""Decimal chapter parts next to extras (owner, 2026-10-14): a folder holding a chapter's parts ``Ch. 0006.10`` /
``Ch. 0006.20`` beside its extras ``Ch. 0006.50`` ("Extras 1") and ``Ch. 0006.60`` ("Bonus Manga") was missing
"6.3, 6.4, 6.5", the find pass kept asking the source for 6.5, and filing it was refused ("chapter 13.5 is already in the
folder"). The split rule (:func:`mangalist.split_chapters.splits_of`) read 6.6 as a part (MangaPixer: ``.5`` is a part
only after ``.4``, every other tenth always), so the "parts in between" were missing - the held extra 6.5 among them.
Now ``.5`` and everything above it continue a split only after ``.4``, and a number the folder holds is never missing.
Made-up names in the folder's exact shapes."""

from __future__ import annotations

from decimal import Decimal

from mangalist.downloads.chapters import chapter_rows, extra_numbers, source_splits
from mangalist.inventory import as_state_inventory, inventory_of_parsed
from mangalist.knowledge import from_mangapixer_item
from mangalist.parsing import ParseContext, parse_names
from mangalist.split_chapters import splits_of
from mangalist.states import GAP_CHAPTER, State, compute_state

from ..downloads.suwayomi_fakes import chapter
from .helpers import TODAY, item

TITLE = "Ember Road Diaries"


def D(*values):
    return [Decimal(str(v)) for v in values]


def _names():
    """The folder's file-name shapes: zero-padded two-digit decimals, parts N.10 / N.20 (/ N.30), extras N.50, N.60 ...,
    whole chapters N.00, and a stray index-prefixed notice."""
    names = [f"Ch. {n:07.2f} Vol. 001 ({TITLE}) [Made Up Scans].cbz" for n in (1, 2, 3, 4, 5)]
    names += [
        f"Ch. 0006.10 Vol. 001 ({TITLE} [1]).cbz",
        f"Ch. 0006.20 Vol. 001 ({TITLE} [2]).cbz",
        "Ch. 0006.50 Vol. 001 (Extras 1).cbz",
        f"Ch. 0006.60 Vol. 001 (Bonus Manga {TITLE}).cbz",
        "Ch. 0007.10 Vol. 002 (Lanterns [1]).cbz",
        "Ch. 0007.20 Vol. 002 (Lanterns [2]).cbz",
        "Ch. 0007.50 Vol. 002 (Extras 2).cbz",
    ]
    names += [f"Ch. 0008.{t}0 Vol. 002 (Harbour [{t}]).cbz" for t in (1, 2)]
    names += [f"Ch. 0008.{t}0 Vol. 002 (Bonus {t}).cbz" for t in (5, 6, 7)]
    names += [f"Ch. 0009.{t}0 Vol. 002 (Bridges [{t}]).cbz" for t in (1, 2, 3)] + ["Ch. 0009.50 Vol. 002 (Extras 3).cbz"]
    names += ["Ch. 0010.00 Vol. 002 (Tides).cbz"]
    names += [f"Ch. 0011.{t}0 Vol. 002 (Ferns [{t}]).cbz" for t in (1, 2)]
    names += ["Ch. 0011.50 Vol. 002 (Extras 4).cbz", "Ch. 0011.60 Vol. 002 (Bonus Manga 2).cbz"]
    names += [f"Ch. 0012.{t}0 Vol. 002 (Kites [{t}]).cbz" for t in (1, 2, 3)] + ["Ch. 0012.50 Vol. 002 (Extras 5).cbz"]
    names += [f"Ch. 0013.{t}0 Vol. 003 (Stones [{t}]).cbz" for t in (1, 2, 3)]
    names += ["Ch. 0013.50 Vol. 003 (Extras 6).cbz", "Ch. 0013.90 Vol. 003 (Afterword).cbz"]
    names += [f"Ch. {n:07.2f} Vol. 003 ({TITLE}).cbz" for n in range(14, 20)]
    names += ["0050 [Ch. 0017.1.5 - Short hiatus notice [Made Up Scans]].cbz"]
    return names


def _held():
    names = _names()
    parsed = parse_names(names, ParseContext(series_title=TITLE))
    return inventory_of_parsed(list(zip(names, parsed)))


def _knowledge(latest="20"):
    it = item()
    rec = it["record"]
    rec.update(licensedEn=False, englishPublishers=[], latestChapter=latest, originStatus="Ongoing",
               completedInOrigin=False, totalChapters=None, statusText=f"{latest} Chapters (Ongoing)")
    it["volumes"] = None
    it["completion"] = None
    return from_mangapixer_item(it)


def test_zero_padded_two_digit_decimals_are_the_sources_numbers():
    held = set(_held().chapters)
    assert {Decimal("6.5"), Decimal("6.1"), Decimal("13.5"), Decimal("13.9")} <= held
    assert {str(c) for c in held} >= {"6.5", "6.1", "6.6", "13.5"}         # no "6.50": the source's "6.5" is the same


def test_parts_and_the_extras_after_them():
    s = splits_of(_held().chapters)
    assert sorted(s.chapters) == [6, 7, 8, 9, 11, 12, 13]
    assert sorted(s.parts) == D(6.1, 6.2, 7.1, 7.2, 8.1, 8.2, 9.1, 9.2, 9.3, 11.1, 11.2, 12.1, 12.2, 12.3, 13.1, 13.2, 13.3)
    assert s.missing_parts == ()                         # no 6.3 / 6.4 invented, and the held 6.5 is never missing


def test_the_folder_misses_only_its_real_gap():
    s = compute_state(as_state_inventory(_held()), _knowledge("20"), folder_empty=False, today=TODAY)
    assert [g.label for g in s.gaps_of(GAP_CHAPTER)] == ["Ch. 20"], s.gaps
    assert s.state == State.MISSING_CHAPTERS


def test_nothing_missing_when_every_chapter_is_held():
    s = compute_state(as_state_inventory(_held()), _knowledge("19"), folder_empty=False, today=TODAY)
    assert not s.gaps_of(GAP_CHAPTER), s.gaps


def test_the_find_pass_asks_for_no_held_extra():
    held = _held()
    s = compute_state(as_state_inventory(held), _knowledge("20"), folder_empty=False, today=TODAY)
    missing = [g.start for g in s.gaps_of(GAP_CHAPTER)]
    numbers = ("6.1", "6.2", "6.5", "6.6", "11.1", "11.2", "11.5", "11.6", "13.1", "13.2", "13.3", "13.5", "19", "20")
    source = [chapter(i, n) for i, n in enumerate(numbers, 1)]
    extras = extra_numbers(source, missing, held.chapters)
    assert extras == []
    assert [r.number for r in chapter_rows(source, missing, None, extras=extras)] == ["20"]


def test_a_sources_extras_after_n5_are_extras():
    # the source's 6.6 / 13.9 follow the extra 6.5 / 13.5: offered as extras when the folder lacks them, never parts
    source = [chapter(i, n) for i, n in enumerate(("5", "6.1", "6.2", "6.5", "6.6", "7", "13.1", "13.2", "13.5", "13.9",
                                                    "14"), 1)]
    assert set(source_splits(source).parts) == set(D(6.1, 6.2, 13.1, 13.2))
    assert extra_numbers(source, [], D(5, 6.1, 6.2, 6.5, 7, 13.1, 13.2, 13.5, 14)) == D(6.6, 13.9)


def test_mangapixer_gaps_between_parts_still_count():
    # .5 and above continue a split after .4 (5.1-5.6 parts, 5.5 among them); a part missing below the highest part
    # here is still missing (4.1 + 4.3: 4.2; 7.1-7.4 + 7.6: 7.5)
    s = splits_of(D(4.1, 4.3, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 7.1, 7.2, 7.3, 7.4, 7.6))
    assert sorted(s.chapters) == [4, 5, 7]
    assert s.missing_parts == tuple(D(4.2, 7.5))
    assert Decimal("5.6") in s.parts and Decimal("7.6") in s.parts


def test_parts_above_a_missing_n4_are_not_parts():
    # 3.1 + 3.2 + 3.7 (no 3.4): 3.7 is an extra, so 3.3-3.6 are not missing; a whole 9 + 9.2 + 9.6: 9.6 is an extra
    s = splits_of(D(3.1, 3.2, 3.7, 9, 9.2, 9.6))
    assert sorted(s.chapters) == [3, 9]
    assert sorted(s.parts) == D(3.1, 3.2, 9.2) and s.missing_parts == ()


def test_a_missing_part_a_held_volume_covers_is_not_missing():
    # loose 4.1 + 4.3 next to a held volume whose chapters end at 4.2: 4.2 is held through the volume
    from mangalist.states import InventorySnapshot

    inv = InventorySnapshot(held_volumes=D(1), held_chapters=D(4.1, 4.3, 5), chapters_covered_by_volumes=((D(1)[0], D(4.2)[0]),))
    s = compute_state(inv, _knowledge("5"), folder_empty=False, today=TODAY)
    assert not s.gaps_of(GAP_CHAPTER), s.gaps
