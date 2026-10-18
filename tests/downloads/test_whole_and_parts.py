"""One form per chapter - a whole chapter ``N`` or its parts ``N.1``, ``N.2`` - in the newer-chapter lookup (the manual
"Check for new chapters" and the scheduled chapter check share it) and in the find pass, and late parts (owner,
2026-10-18: "Check: one form per chapter"; two scanlation groups, one releasing whole chapters, one in parts, both
labelled "unofficial" on the source). Made-up names, a fake Suwayomi, no network."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from mangalist.downloads import chapter_check as cc
from mangalist.downloads.chapter_forms import choose, held_form, is_late_part, late_parts
from mangalist.downloads.chapters import chapter_rows
from mangalist.downloads.finder import retire_filled, run_find
from mangalist.downloads.finding import TRIGGER_NOW
from mangalist.store.findings import FindingStore

from .fakes import data, scan
from .finder_fakes import wanted, wire
from .suwayomi_fakes import MANGA, MANGADEX, chapter

PARTS_HELD = ("35.1", "35.2", "36.1", "36.2", "37.1", "37.2")
WHOLE_HELD = ("33", "34", "35", "36", "37")


def cid(number: str) -> int:
    """A made-up chapter id from its number: 38 -> 3800, 38.2 -> 3820."""
    return int(round(float(number) * 100))


def listing(*numbers: str):
    return [chapter(cid(n), n, group="unofficial") for n in numbers]


# the owner's source on 2026-10-18 (made-up ids): whole chapters from one group, parts from the other; no 38.1
OWNER_SOURCE = ("35", "35.1", "35.2", "36", "36.1", "36.2", "37", "37.1", "37.2", "38", "38.2", "39.1")


def chapter_file(folder, number: str, group: str = "Alpha Scans") -> None:
    whole, _, frac = number.partition(".")
    n = whole.zfill(4) + (f".{frac}" if frac else "")
    (folder / f"{n} [Ch. {n} - Title {number} [{group}]].cbz").write_bytes(data(f"c{number}"))


def make_series(db, tmp_path, held, name="Series W"):
    root = tmp_path / "library" / "Manga"
    folder = root / name
    folder.mkdir(parents=True)
    for n in held:
        chapter_file(folder, n)
    r = db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(r.id, name).id
    w = wire(db, (sid, folder), tmp_path, suwayomi=True)
    w.backend.suwayomi.set_series_choice(sid, source_id=MANGADEX.id, manga_id=MANGA.id, manga_title=MANGA.title,
                                         how="confirmed")
    return w


@pytest.fixture
def parts(db, tmp_path):
    """'Series W' holding its chapters in parts up to 37.2 (the owner's series 390, made up)."""
    return make_series(db, tmp_path, PARTS_HELD)


@pytest.fixture
def wholes(db, tmp_path):
    """'Series W' holding whole chapters 33-37."""
    return make_series(db, tmp_path, WHOLE_HELD)


def newer(w):
    return w.backend.chapter_lookup(w.series_id, (), ("Series W",), newer=True)


def check(db, w, mode="auto"):
    FindingStore(db).set_mode([w.series_id], mode)
    return cc.run_chapter_check(db, backend=w.backend, pause=0)


def enqueued(w):
    return [ids for name, *rest in w.suwayomi.calls if name == "enqueue" for ids in rest]


def D(*numbers):
    return [Decimal(n) for n in numbers]


# --- the owner's case ------------------------------------------------------------------------------------------

def test_the_38_case_takes_the_whole_38_as_a_stand_in_with_38_2_and_39_1(parts, caplog):
    caplog.set_level(logging.INFO)
    parts.suwayomi.chapter_list = listing(*OWNER_SOURCE)
    lookup = newer(parts)
    assert lookup.missing == ("38", "38.2", "39.1") and lookup.newer_than == "37.2"
    rows = {r.number: r for r in lookup.rows}
    assert set(rows) == {"38", "38.2", "39.1"} and not any(r.extra for r in rows.values())
    assert rows["38"].note == "stands in for the missing part 38.1 (the source lists ch 38 from 38.2)"
    # the older whole chapters (35, 36, 37) the series holds in parts are never offered
    assert "ch 38 stands in for the missing part 38.1" in caplog.text


def test_the_scheduled_check_says_why_in_its_log_line(parts, db, caplog):
    caplog.set_level(logging.INFO)
    parts.suwayomi.chapter_list = listing(*OWNER_SOURCE)
    check(db, parts, "auto")
    (line,) = [r.getMessage() for r in caplog.records if "Series W:" in r.getMessage()]
    assert line == ("Chapter check: Series W: ch. 38, 38.2, 39.1 from MangaDex (EN) (unofficial) new after ch 37.2: "
                    "Sent ch 38, 38.2, 39.1 to Suwayomi; ch 38 stands in for the missing part 38.1 (the source lists "
                    "ch 38 from 38.2)")


def test_a_source_with_every_part_gives_the_parts_and_not_the_whole(parts):
    parts.suwayomi.chapter_list = listing("37", "37.1", "37.2", "38", "38.1", "38.2")
    lookup = newer(parts)
    assert lookup.missing == ("38.1", "38.2")
    rows = {r.number: r for r in lookup.rows}
    assert not rows["38.1"].extra and not rows["38.2"].extra and rows["38.1"].part_of == "38"
    assert rows["38"].extra and rows["38"].note.startswith("the whole chapter; its parts 38.1, 38.2 are taken")
    assert [r.number for r in lookup.available if not r.extra] == ["38.1", "38.2"]


def test_a_whole_chapter_series_takes_the_whole_and_ignores_the_parts(wholes):
    wholes.suwayomi.chapter_list = listing("37", "37.1", "37.2", "38", "38.1", "38.2")
    lookup = newer(wholes)
    assert lookup.missing == ("38",)
    rows = {r.number: r for r in lookup.rows}
    assert rows["38.1"].extra and rows["38.2"].extra and not rows["38"].extra
    assert "the series holds whole chapters" in rows["38.1"].note


def test_a_whole_chapter_series_does_not_take_the_parts_of_a_whole_it_holds(wholes):
    wholes.suwayomi.chapter_list = listing("36", "37", "37.1", "37.2")
    lookup = newer(wholes)
    assert lookup.missing == ()


def test_never_a_whole_n_together_with_n_1():
    held = D(*PARTS_HELD)
    for listed in (("38", "38.1"), ("38", "38.1", "38.2"), ("38", "38.1", "38.3")):
        took = choose(D(*listed), held, D(*listed)).take
        assert not (Decimal("38") in took and Decimal("38.1") in took), listed


# --- late parts ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stand_in", [True, False])
def test_a_late_part_is_offered_after_the_later_chapters_are_held(db, tmp_path, stand_in):
    held = PARTS_HELD + (("38",) if stand_in else ()) + ("38.2", "39.1")
    w = make_series(db, tmp_path, held)
    w.suwayomi.chapter_list = listing(*OWNER_SOURCE, "38.1")
    lookup = newer(w)
    assert lookup.newer_than == "39.1" and lookup.missing == ("38.1",)
    (row,) = lookup.rows
    held_text = "38, 38.2" if stand_in else "38.2"
    assert row.note == f"a late part of ch 38 (held: {held_text})" and not row.extra


def test_the_scheduled_check_sends_a_late_part_and_keeps_its_finding_until_it_is_held(db, tmp_path):
    w = make_series(db, tmp_path, PARTS_HELD + ("38", "38.2", "39.1"))
    w.suwayomi.chapter_list = listing(*OWNER_SOURCE, "38.1")
    report = check(db, w, "notify")
    (f,) = FindingStore(db).list("waiting")
    assert f.fills == ("38.1",) and report.waiting == 1
    assert cc.still_new(db, w.series_id, f.fills) == ("38.1",)          # below the newest held, still new
    assert retire_filled(db, wanted=[]) == 0
    chapter_file(w.folder, "38.1")
    scan(db)
    assert cc.still_new(db, w.series_id, f.fills) == ()
    assert retire_filled(db, wanted=[]) == 1


def test_no_older_whole_chapter_and_no_part_of_an_old_chapter_is_offered(db, tmp_path):
    held = ("20.1", "20.2") + PARTS_HELD + ("38", "38.2", "39.1")
    w = make_series(db, tmp_path, held)
    # the source has whole chapters the series holds in parts (35-37), a whole 21 the series lacks, and a third part of
    # chapter 20 - far behind the series' last chapters: none of them is asked for
    w.suwayomi.chapter_list = listing("20", "20.1", "20.2", "20.3", "21", *OWNER_SOURCE)
    assert newer(w).missing == ()
    assert late_parts(D("20.3", "21", "37"), D(*held), Decimal("39.1")) == {}


def test_a_whole_chapter_is_never_a_late_part():
    held = D(*PARTS_HELD, "38.2")
    assert not is_late_part(Decimal("38"), held) and is_late_part(Decimal("38.1"), held)
    assert not is_late_part(Decimal("38.5"), held)                       # an extra, not a part (no 38.4)


# --- the series' form ------------------------------------------------------------------------------------------

def test_the_series_form_follows_its_newest_and_recent_chapters():
    assert held_form(D(*PARTS_HELD)).parts
    assert not held_form(D(*WHOLE_HELD)).parts
    # the whole 38 a stand-in filed last: most of the recent chapters are still held in parts
    assert held_form(D(*PARTS_HELD, "38")).parts
    # a series that just switched to the parts group: its newest chapter is held in parts
    assert held_form(D("33", "34", "35", "36", "37.1", "37.2")).parts
    assert not held_form(()).parts


# --- the manual and the scheduled check agree; the find pass follows the same rule -------------------------------

@pytest.mark.parametrize("source", [OWNER_SOURCE, ("37", "37.1", "37.2", "38", "38.1", "38.2")])
def test_the_manual_check_and_the_scheduled_check_agree(parts, db, source):
    parts.suwayomi.chapter_list = listing(*source)
    manual = newer(parts).missing
    check(db, parts, "auto")
    (sent,) = enqueued(parts)
    assert tuple(sent) == tuple(cid(n) for n in manual)
    assert manual in (("38", "38.2", "39.1"), ("38.1", "38.2"))


def test_the_find_pass_fills_a_gap_in_the_series_form(parts, db):
    parts.suwayomi.chapter_list = listing("37", "37.1", "37.2", "38", "38.1", "38.2")
    FindingStore(db).set_mode([parts.series_id], "auto")
    parts.entries = wanted(parts.series_id, parts.folder, chapters=["38"])
    report = run_find(db, trigger=TRIGGER_NOW, wanted=parts.wanted, backend=parts.backend)
    assert report.sent == 1 and enqueued(parts) == [[3810, 3820]]


def test_without_the_held_chapters_the_rows_are_as_before():
    source = listing("37", "37.1", "37.2", "38", "38.1", "38.2")
    assert [(r.number, r.extra) for r in chapter_rows(source, ["38"], None)] == [("38", False)]
    rows = chapter_rows(source, ["38"], None, held=D(*PARTS_HELD))
    assert [(r.number, r.extra, r.part_of) for r in rows] == [("38", True, None), ("38.1", False, "38"),
                                                             ("38.2", False, "38")]
