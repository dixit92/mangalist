"""A source that splits a chapter into parts (``62.1``, ``62.2``): MangaPixer's split rule on the source's list (owner,
2026-10-13: "We need to bring .1 .2 rules on parity with MangaPixer"). The parts of a missing chapter are its rows -
counted as missing, picked, sent by a pass on Auto; parts of a held chapter are not extras; a real extra stays one.
Made-up chapters, a fake Suwayomi."""

from __future__ import annotations

from decimal import Decimal

from mangalist.downloads.chapters import chapter_rows, extra_numbers, source_splits
from mangalist.downloads.finder import run_find
from mangalist.downloads.finding import TRIGGER_NOW
from mangalist.store.findings import FindingStore

from .finder_fakes import wanted, wire
from .suwayomi_fakes import MANGA, MANGADEX, chapter

SOURCE = [chapter(160, "60"), chapter(161, "61"), chapter(1621, "62.1"), chapter(1622, "62.2"), chapter(163, "63"),
          chapter(1371, "37.1"), chapter(1372, "37.2"), chapter(1715, "71.5")]
HELD = [Decimal(n) for n in ("37", "60", "61", "63", "71", "72")]


def test_a_missing_chapter_the_source_splits_is_its_parts():
    rows = {r.number: r for r in chapter_rows(SOURCE, ["62"], None)}
    assert set(rows) == {"62.1", "62.2"}
    assert all(r.available and not r.extra and r.part_of == "62" for r in rows.values())
    assert [o.id for o in (rows["62.1"].options + rows["62.2"].options)] == [1621, 1622]


def test_parts_are_not_extras_but_a_real_extra_stays_one():
    assert set(source_splits(SOURCE).parts) == {Decimal("62.1"), Decimal("62.2"), Decimal("37.1"), Decimal("37.2")}
    assert extra_numbers(SOURCE, ["62"], HELD) == [Decimal("71.5")]


def test_a_chapter_the_source_has_whole_keeps_its_own_row():
    rows = chapter_rows(SOURCE + [chapter(162, "62")], ["62"], None)
    assert [(r.number, r.part_of) for r in rows] == [("62", None)]


def test_a_lone_first_part_stands_in_as_mangapixer_counts_it():
    rows = chapter_rows([chapter(10, "10"), chapter(111, "11.1"), chapter(12, "12")], ["11"], None)
    assert [(r.number, r.part_of, r.available) for r in rows] == [("11.1", "11", True)]


def test_a_pass_on_auto_sends_the_parts(db, series, tmp_path):
    w = wire(db, series, tmp_path, suwayomi=True)
    w.suwayomi.chapter_list = list(SOURCE)
    w.backend.suwayomi.set_series_choice(w.series_id, source_id=MANGADEX.id, manga_id=MANGA.id,
                                         manga_title=MANGA.title, how="confirmed")
    FindingStore(db).set_mode([w.series_id], "auto")
    w.entries = wanted(w.series_id, w.folder, chapters=["62"])
    report = run_find(db, trigger=TRIGGER_NOW, wanted=w.wanted, backend=w.backend)
    assert report.sent == 1
    assert [ids for name, *rest in w.suwayomi.calls if name == "enqueue" for ids in rest] == [[1621, 1622]]
