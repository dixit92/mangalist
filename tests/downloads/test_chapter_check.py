"""The scheduled newer-chapter check (mangalist.downloads.chapter_check; owner, 2026-10-18: "Scheduled newer-chapter
check - every 6-12 h, ongoing chapter series with a saved source are checked for chapters newer than the newest held;
Auto sends, Notify waits"): which series are asked, what counts as new (a part N.2 after a held N.1 too; nothing older
or equal), Auto sends through the chapter send, Notify leaves a finding in Found that the find pass keeps, stopping, the
manual check's INFO line. A fake Suwayomi, a temporary library, made-up names."""

from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace

import pytest

from mangalist.downloads import chapter_check as cc
from mangalist.downloads.contracts import DownloadStatus, QueuedChapter
from mangalist.downloads.finder import retire_filled, run_find, send_finding
from mangalist.downloads.finding import REASON_NOTIFY, TRIGGER_SCHEDULE
from mangalist.states import State
from mangalist.store.findings import FindingStore
from mangalist.wanted import library_states

from .fakes import data, scan
from .finder_fakes import wire
from .suwayomi_fakes import MANGA, MANGADEX, chapter


def chapter_file(folder, number: str, group: str = "Alpha Scans") -> None:
    n = number.zfill(4) if "." not in number else number.split(".")[0].zfill(4) + "." + number.split(".")[1]
    (folder / f"{n} [Ch. {n} - Title {number} [{group}]].cbz").write_bytes(data(f"c{number}"))


@pytest.fixture
def lib(db, tmp_path):
    """'Series C' holding chapters 3-4 (a chapter series), wired to the fake Suwayomi."""
    root = tmp_path / "library" / "Manga"
    folder = root / "Series C"
    folder.mkdir(parents=True)
    for n in ("3", "4"):
        chapter_file(folder, n)
    r = db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(r.id, "Series C").id
    w = wire(db, (sid, folder), tmp_path, suwayomi=True)
    w.suwayomi.chapter_list = [chapter(101, "3"), chapter(102, "4"), chapter(103, "5"), chapter(104, "6"),
                               chapter(204, "4", group="Beta Scans")]
    w.root = r
    return w


def saved(w, sid=None, **kw):
    w.backend.suwayomi.set_series_choice(sid or w.series_id, source_id=MANGADEX.id, manga_id=MANGA.id,
                                         manga_title=MANGA.title, how="confirmed", **kw)


def check(db, w, **kw):
    kw.setdefault("pause", 0)
    return cc.run_chapter_check(db, backend=w.backend, **kw)


def enqueued(w):
    return [ids for name, *rest in w.suwayomi.calls if name == "enqueue" for ids in rest]


def fetches(w):
    return sum(1 for c in w.suwayomi.calls if c[0] == "chapters")


def findings(db, status="waiting"):
    return FindingStore(db).list(status)


# --- Auto and Notify ---------------------------------------------------------------------------------------------

def test_auto_sends_the_chapters_newer_than_the_newest_held_and_nothing_older(db, lib, caplog):
    caplog.set_level(logging.INFO)
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "auto")
    report = check(db, lib)
    assert enqueued(lib) == [[103, 104]]                                 # 5 and 6; never 3 or 4 (held)
    assert (report.series_checked, report.with_new, report.sent, report.waiting) == (1, 1, 1, 0)
    assert report.summary == "1 series checked, 1 with new chapters: 1 sent, 0 waiting"
    (f,) = findings(db, "sent")
    assert f.payload["newer"] is True and f.pick_key == f"chapters:{MANGADEX.id}:103,104"
    assert {r.wanted_chapters for r in lib.backend.chapter_ledger.all_records()} == {("5",), ("6",)}
    line = [r.getMessage() for r in caplog.records if "Series C:" in r.getMessage()]
    assert line == ["Chapter check: Series C: ch. 5-6 from MangaDex (EN) (Alpha Scans) new after ch 4: "
                    "Sent ch 5-6 to Suwayomi"]
    again = check(db, lib)                                               # sent before: never again
    assert enqueued(lib) == [[103, 104]] and again.with_new == 0 and again.sent == 0


def test_notify_leaves_a_finding_waiting_in_found(db, lib, caplog):
    caplog.set_level(logging.INFO)
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    report = check(db, lib)
    (f,) = findings(db)
    assert enqueued(lib) == [] and (report.with_new, report.sent, report.waiting) == (1, 0, 1)
    assert f.reason == REASON_NOTIFY and f.fills == ("5", "6") and f.title == "ch. 5-6 from MangaDex (EN) (Alpha Scans)"
    assert f.payload["newer"] is True and f.payload["newer_than"] == "4" and f.payload["chapter_ids"] == [103, 104]
    assert "new after ch 4: waits (the series is on Notify)" in caplog.text
    # the owner's Send sends it (no wanted-list gap names these chapters, and still it is not "filled")
    assert send_finding(db, f.id, backend=lib.backend, wanted=[]) == "Sent ch 5-6 to Suwayomi."
    assert enqueued(lib) == [[103, 104]]


def test_the_find_pass_and_the_rescan_keep_a_newer_finding_until_its_chapters_are_held(db, lib):
    # without the newer mark, the find pass retired it at once: no wanted-list gap names chapters past the metadata
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    check(db, lib)
    run_find(db, trigger=TRIGGER_SCHEDULE, wanted=[], backend=lib.backend)
    assert retire_filled(db, wanted=[]) == 0
    (f,) = findings(db)
    assert f.payload["newer"] is True
    chapter_file(lib.folder, "5")                                        # 5 and 6 filed another way
    chapter_file(lib.folder, "6")
    scan(db)
    assert retire_filled(db, wanted=[]) == 1 and findings(db) == []


def test_a_gap_the_find_pass_picks_takes_over_its_chapters_and_the_check_offers_the_rest(db, lib):
    from .finder_fakes import wanted

    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    check(db, lib)                                                       # ch. 5-6 new
    run_find(db, trigger=TRIGGER_SCHEDULE, wanted=wanted(lib.series_id, lib.folder, chapters=["5"]),
             backend=lib.backend)                                        # the metadata caught up to 5
    (gap,) = findings(db)
    assert gap.fills == ("5",) and "newer" not in gap.payload
    check(db, lib)                                                       # 5 is offered already: only 6
    assert sorted((f.fills, bool(f.payload.get("newer"))) for f in findings(db)) == [(("5",), False), (("6",), True)]


def test_a_new_chapter_after_a_waiting_finding_replaces_it(db, lib):
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    check(db, lib)
    lib.suwayomi.chapter_list.append(chapter(105, "7"))
    report = check(db, lib)
    (f,) = findings(db)
    assert f.fills == ("5", "6", "7") and report.waiting == 1 and len(findings(db, "gone")) == 1


def test_a_skipped_newer_finding_is_not_offered_again(db, lib):
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    check(db, lib)
    (f,) = findings(db)
    FindingStore(db).set_status(f.id, "skipped")
    report = check(db, lib)
    assert findings(db) == [] and report.with_new == 0


def test_a_part_after_a_held_part_counts(db, lib):
    # MAL-Sync parity (owner, 2026-10-18): the folder holds 4.1, the source has 4.2 - that is new
    chapter_file(lib.folder, "4.1")
    scan(db)
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "auto")
    lib.suwayomi.chapter_list = [chapter(101, "3"), chapter(141, "4.1"), chapter(142, "4.2")]
    report = check(db, lib)
    assert enqueued(lib) == [[142]] and report.sent == 1


def test_nothing_newer_on_the_source_sends_and_offers_nothing(db, lib):
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "auto")
    lib.suwayomi.chapter_list = [chapter(101, "3"), chapter(102, "4"), chapter(99, "2.5")]
    report = check(db, lib)
    assert enqueued(lib) == [] and findings(db, None) == [] and report.series_checked == 1 and report.with_new == 0
    assert report.summary == "1 series checked, 0 with new chapters: 0 sent, 0 waiting"


def test_only_the_series_group_and_never_what_is_queued_or_sent(db, lib):
    saved(lib, group="Beta Scans")
    FindingStore(db).set_mode([lib.series_id], "auto")
    lib.suwayomi.chapter_list += [chapter(205, "5", group="Beta Scans"), chapter(206, "6", group="Beta Scans")]
    lib.suwayomi.queued[206] = QueuedChapter(206, "QUEUED")
    check(db, lib)
    assert enqueued(lib) == [[205]]                                     # Beta's 5; its 6 is in Suwayomi's queue


# --- which series ------------------------------------------------------------------------------------------------

def test_a_chapter_series_on_notify_or_auto_with_a_saved_source_is_selected(db, lib):
    saved(lib)
    sel = cc.select_series(db)
    assert [(c.series_id, c.name, c.mode) for c in sel.series] == [(lib.series_id, "Series C", "notify")]


def test_off_finished_no_source_and_in_flight_series_are_not_asked(db, lib):
    fs = FindingStore(db)
    # no saved source and no MangaDex id MangaPixer links
    assert cc.select_series(db).skipped == {cc.SKIP_NO_SOURCE: 1}
    report = check(db, lib)
    assert fetches(lib) == 0 and report.series_checked == 0
    saved(lib)
    fs.set_mode([lib.series_id], "off")
    assert cc.select_series(db).skipped == {cc.SKIP_OFF: 1}
    fs.set_mode([lib.series_id], "auto")

    def finished(db_):
        return [dataclasses.replace(v, knowledge=SimpleNamespace(finished_in_origin=True)) for v in library_states(db_)]

    def complete(db_):
        return [dataclasses.replace(v, state=dataclasses.replace(v.state, state=State.COMPLETE))
                for v in library_states(db_)]

    assert cc.select_series(db, views=finished).skipped == {cc.SKIP_FINISHED: 1}
    assert cc.select_series(db, views=complete).skipped == {cc.SKIP_FINISHED: 1}
    check(db, lib, views=finished)
    assert fetches(lib) == 0
    lib.backend.chapter_ledger.create_chapters(lib.series_id, [chapter(103, "5")], source=MANGADEX, manga=MANGA,
                                               target_dir=str(lib.folder), batch="b1")
    assert cc.select_series(db).skipped == {cc.SKIP_IN_FLIGHT: 1}
    check(db, lib)
    assert fetches(lib) == 0 and enqueued(lib) == []


def test_a_volumes_only_series_is_not_asked_unless_unbundled(db, lib):
    for f in lib.folder.iterdir():
        f.unlink()
    (lib.folder / "Series C v01.cbz").write_bytes(data("v1"))
    scan(db)
    saved(lib)
    assert cc.select_series(db).skipped == {cc.SKIP_NOT_CHAPTERS: 1}

    def unbundled(db_):
        return [dataclasses.replace(v, state=dataclasses.replace(v.state, unbundled=True)) for v in library_states(db_)]

    assert [c.series_id for c in cc.select_series(db, views=unbundled).series] == [lib.series_id]


# --- running -----------------------------------------------------------------------------------------------------

def test_a_stop_request_ends_the_run_between_series(db, lib, tmp_path):
    other = lib.folder.parent / "Series D"
    other.mkdir()
    chapter_file(other, "1")
    scan(db)
    second = db.get_series(lib.root.id, "Series D").id
    saved(lib)
    saved(lib, sid=second)
    FindingStore(db).set_mode([lib.series_id, second], "auto")
    report = check(db, lib, should_stop=lambda: fetches(lib) >= 1)
    assert fetches(lib) == 1 and report.stopped and report.series_checked == 1 and report.selected == 2
    assert "stopped after 1 of 2" in report.summary


def test_the_pause_between_series_is_cut_short_by_a_stop(db, lib):
    other = lib.folder.parent / "Series D"
    other.mkdir()
    chapter_file(other, "1")
    scan(db)
    saved(lib)
    saved(lib, sid=db.get_series(lib.root.id, "Series D").id)
    slept = []
    stop = []

    def sleep(seconds):
        slept.append(seconds)
        stop.append(True)

    report = check(db, lib, pause=30, sleep=sleep, should_stop=lambda: bool(stop))
    assert slept == [0.25] and report.stopped and fetches(lib) == 1


def test_suwayomi_down_ends_the_run_as_an_error(db, lib):
    saved(lib)
    lib.suwayomi.unreachable = True
    report = check(db, lib)
    assert report.errors == 1 and report.summary.startswith("nothing checked: Suwayomi did not answer")


def test_suwayomi_not_set_up_checks_nothing(db, lib):
    saved(lib)
    lib.backend.suwayomi.forget_connection()
    report = check(db, lib)
    assert report.errors == 0 and report.summary == "nothing checked: Suwayomi is not set up"


def test_a_check_does_not_take_the_nyaa_lock_and_waits_for_a_running_find_pass(db, lib):
    from mangalist.downloads import finder

    saved(lib)
    seen = []

    def views(db_):
        seen.append(finder._PASS.locked())
        return library_states(db_)

    check(db, lib, views=views)
    assert seen == [False]
    with finder._PASS:                                                  # a find pass in this process
        with pytest.raises(cc.CheckBusy, match="a find pass is running"):
            check(db, lib)
    run = FindingStore(db).start_run("now")                              # ... or in another MangaList
    with pytest.raises(cc.CheckBusy):
        check(db, lib)
    FindingStore(db).finish_run(run, series_searched=0, found=0, sent=0, errors=0, summary="")
    check(db, lib)


def test_the_find_pass_runs_while_a_check_is_running(db, lib):
    # the check holds its own lock, not the find pass's: a find pass in the same process is not refused
    saved(lib)
    results = []

    def views(db_):
        results.append(run_find(db_, trigger=TRIGGER_SCHEDULE, wanted=[], backend=lib.backend).run_id)
        return library_states(db_)

    check(db, lib, views=views)
    assert results and results[0] is not None


# --- the manual check logs what it found -------------------------------------------------------------------------

def test_the_manual_check_says_what_it_found_at_info(db, lib, caplog):
    caplog.set_level(logging.INFO)
    saved(lib)
    lookup = lib.backend.chapter_lookup(lib.series_id, (), ("Series C",), newer=True)
    assert lookup.missing == ("5", "6") and lookup.newer_than == "4"
    assert (f"Check for new chapters: series {lib.series_id}: ch 5-6 after ch 4 on MangaDex (EN) (Alpha Scans)"
            in caplog.text)
    lib.suwayomi.chapter_list = [chapter(101, "3"), chapter(102, "4")]
    lib.backend.chapter_lookup(lib.series_id, (), ("Series C",), newer=True)
    assert f"Check for new chapters: series {lib.series_id}: nothing after ch 4 on MangaDex (EN)" in caplog.text


def test_still_new_counts_what_is_after_the_newest_held_and_not_sent(db, lib):
    assert cc.still_new(db, lib.series_id, ("4", "5", "6")) == ("5", "6")
    rec, = lib.backend.chapter_ledger.create_chapters(lib.series_id, [chapter(103, "5")], source=MANGADEX,
                                                      manga=MANGA, target_dir=str(lib.folder), batch="b1")[0]
    assert rec.status == DownloadStatus.SENT
    assert cc.still_new(db, lib.series_id, ("5", "6")) == ("6",)
    assert cc.still_new(db, 9999, ("5",)) is None
