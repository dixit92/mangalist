"""Unbundled Chapters on an empty folder (owner, 2026-10-17: "if I click unbundle, it should let me get the volumes and
the chapters after the volumes"): the English volumes out, plus the chapters after the newest of them - from where the
volume list says it ends, else from the owner's "first chapter to get". The synthetic item of ``helpers``: licensed,
latest chapter 41, English volumes 1 (ch. 1-8), 2 (9-16), 3 (17-24.5) out, 4 announced. Made-up titles."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mangalist.knowledge import EnglishPublisher, from_mangapixer_item
from mangalist.states import (ATTENTION_UNBUNDLED_START, GAP_CHAPTER, GAP_VOLUME, InventorySnapshot, State,
                              compute_state)
from mangalist.unbundled import UnbundledChoice

from .helpers import TODAY, item, own, vol


def question_text(title, ask):
    """The dialog's wording (Qt): checked where PySide6 is installed - this file also runs in the no-Qt job."""
    return pytest.importorskip("mangalist.gui.unbundled_dialog").question_text(title, ask)


def k(**changes):
    return from_mangapixer_item(item(**changes))


def empty(knowledge, unbundled=True):
    return compute_state(InventorySnapshot(), knowledge, folder_empty=True, today=TODAY, unbundled=unbundled)


def gaps(st, kind):
    return [(g.start, g.end) for g in st.gaps if g.kind == kind]


UNMAPPED = {"source": "merged", "fetchedAt": "2026-10-02T12:00:00.000Z",
            "items": [vol("1", "1", "8", "2025-01-01", "released"), vol("2", None, None, "2025-05-01", "released"),
                      vol("3", None, None, "2025-09-01", "released")]}


def test_off_an_empty_folder_wants_the_english_volumes_only():
    st = empty(k(), unbundled=False)
    assert st.state == State.WANTED_OFFICIAL and gaps(st, GAP_VOLUME) == [("1", None), ("2", None), ("3", None)]
    assert not gaps(st, GAP_CHAPTER) and st.unbundled_ask is None


def test_on_the_chapters_after_the_newest_english_volume_are_wanted_too():
    st = empty(k())
    assert st.state == State.WANTED_OFFICIAL
    assert gaps(st, GAP_VOLUME) == [("1", None), ("2", None), ("3", None)]
    assert gaps(st, GAP_CHAPTER) == [("25", "41")]                     # volume 3 ends with 24.5
    assert st.missing_chapters == (("25", "41"),) and st.missing_volumes == ("1", "2", "3")
    assert "Unbundled chapters after chapter 24.5 (the end of volume 3)" in st.reasons
    assert st.unbundled_ask is None


def test_where_the_list_does_not_map_the_newest_volume_the_first_chapter_to_get_is_asked():
    st = empty(k(volumes=UNMAPPED))
    assert gaps(st, GAP_VOLUME) == [("1", None), ("2", None), ("3", None)] and not gaps(st, GAP_CHAPTER)
    ask = st.unbundled_ask
    assert (ask.volume, ask.mapped_volume, ask.mapped_end, ask.needed, ask.not_held) == ("3", "1", "8", True, True)
    assert ATTENTION_UNBUNDLED_START in st.needs_attention
    answered = empty(k(volumes=UNMAPPED), unbundled=UnbundledChoice(Decimal(30), Decimal(3)))
    assert gaps(answered, GAP_CHAPTER) == [("30", "41")] and not answered.unbundled_ask.needed
    assert ATTENTION_UNBUNDLED_START not in answered.needs_attention
    # a newer English volume since the answer: asked again, nothing assumed
    newer = dict(UNMAPPED, items=UNMAPPED["items"] + [vol("4", None, None, "2026-01-01", "released")])
    again = empty(k(volumes=newer), unbundled=UnbundledChoice(Decimal(30), Decimal(3)))
    assert again.unbundled_ask.needed and again.unbundled_ask.stale and not gaps(again, GAP_CHAPTER)
    text = question_text("Example", ask)                                # last: skipped without Qt
    assert "the newest volume out in English, which the folder does not hold yet" in text and "after volume 3" in text


def test_an_english_volume_count_without_a_list_asks_then_wants_the_chapters_from_the_answer():
    # the owner's series: Seven Seas has 13 volumes out, no volume list (MangaPixer links no empty folder), chapter 192
    kanan = own(licensed_en=True, latest_chapter="192",
                english_publishers=(EnglishPublisher("Example Press", volumes=13),))
    st = empty(kanan)
    assert gaps(st, GAP_VOLUME) == [(str(n), None) for n in range(1, 14)] and not gaps(st, GAP_CHAPTER)
    assert st.unbundled_ask.needed and st.unbundled_ask.volume == "13" and st.unbundled_ask.mapped_volume is None
    st = empty(kanan, unbundled=UnbundledChoice(Decimal(160), Decimal(13)))
    assert gaps(st, GAP_CHAPTER) == [("160", "192")] and len(gaps(st, GAP_VOLUME)) == 13
    assert "Unbundled chapters from chapter 160 (your first chapter to get" in " ".join(st.reasons)


def test_the_question_for_a_folder_with_volumes_reads_as_before():
    held = compute_state(InventorySnapshot(held_volumes=["1", "2", "3"]), k(volumes=UNMAPPED), folder_empty=False,
                         today=TODAY, unbundled=True)
    assert not held.unbundled_ask.not_held
    assert "the newest volume you hold" in question_text("Example", held.unbundled_ask)


def test_with_the_first_volumes_arrived_the_question_stays_on_the_newest_english_volume():
    # the owner's series a day later: volumes 1-2 downloaded, 13 out
    kanan = own(licensed_en=True, latest_chapter="192",
                english_publishers=(EnglishPublisher("Example Press", volumes=13),))
    st = compute_state(InventorySnapshot(held_volumes=["1", "2"]), kanan, folder_empty=False, today=TODAY,
                       unbundled=True)
    assert st.unbundled_ask.volume == "13" and st.unbundled_ask.not_held
    st = compute_state(InventorySnapshot(held_volumes=["1", "2"]), kanan, folder_empty=False, today=TODAY,
                       unbundled=UnbundledChoice(Decimal(160), Decimal(13)))
    assert gaps(st, GAP_CHAPTER) == [("160", "192")] and gaps(st, GAP_VOLUME) == [(str(n), None) for n in range(3, 14)]
