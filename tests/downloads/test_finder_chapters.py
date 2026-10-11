"""The find pass, chapters (mangalist.downloads.finder): only the saved Suwayomi source, the series' group, extras never,
"no source" and "source dropped" findings, Suwayomi down. A fake Suwayomi, a temporary library, made-up names."""

from __future__ import annotations

import pytest

from mangalist.downloads.contracts import DownloadStatus, QueuedChapter
from mangalist.downloads.finder import run_find
from mangalist.downloads.finding import REASON_NO_SOURCE, REASON_NOTIFY, REASON_SOURCE_DROPPED, TRIGGER_NOW
from mangalist.store.findings import FindingStore

from .finder_fakes import wanted, wire
from .suwayomi_fakes import MANGA, MANGADEX, chapter


@pytest.fixture
def w(db, series, tmp_path):
    w = wire(db, series, tmp_path, suwayomi=True)
    w.suwayomi.chapter_list = [chapter(101, "4"), chapter(102, "5"), chapter(103, "6"), chapter(104, "5.5"),
                               chapter(202, "5", group="Beta Scans"), chapter(203, "6", group="Beta Scans")]
    return w


def saved(w, **kw):
    w.backend.suwayomi.set_series_choice(w.series_id, source_id=MANGADEX.id, manga_id=MANGA.id,
                                         manga_title=MANGA.title, how="confirmed", **kw)


def run(db, w, **kw):
    return run_find(db, trigger=TRIGGER_NOW, wanted=w.wanted, backend=w.backend, **kw)


def findings(db, status="waiting"):
    return FindingStore(db).list(status)


def enqueued(w):
    return [ids for name, *rest in w.suwayomi.calls if name == "enqueue" for ids in rest]


# --- rules ---------------------------------------------------------------------------------------------------------

def test_no_saved_source_is_a_finding_without_a_pick(db, w):
    FindingStore(db).set_mode([w.series_id], "auto")
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    report = run(db, w)
    (f,) = findings(db)
    assert f.reason == REASON_NO_SOURCE and f.fills == ("5", "6") and f.payload == {"type": "no-source"}
    assert report.series_searched == 1 and not [c for c in w.suwayomi.calls if c[0] in ("search", "chapters")]


def test_auto_sends_the_missing_chapters_from_the_series_group_and_never_extras(db, w):
    saved(w, group="Beta Scans")
    FindingStore(db).set_mode([w.series_id], "auto")
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    report = run(db, w)
    assert report.sent == 1 and enqueued(w) == [[202, 203]]                         # 5.5 (an extra) is not sent
    (f,) = findings(db, "sent")
    recs = w.backend.chapter_ledger.all_records()
    assert f.record_id == recs[0].id and {r.wanted_chapters for r in recs} == {("5",), ("6",)}


def test_without_a_stored_group_the_panels_default_rule_picks(db, w):
    saved(w)
    FindingStore(db).set_mode([w.series_id], "auto")
    w.suwayomi.chapter_list.append(chapter(105, "7"))
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6", "7"])
    run(db, w)
    assert enqueued(w) == [[102, 103, 105]]                                          # the group with the most


def test_notify_offers_the_chapters_with_what_sending_needs(db, w):
    saved(w)
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    report = run(db, w)
    (f,) = findings(db)
    assert report.found == 1 and f.reason == REASON_NOTIFY and enqueued(w) == []
    assert f.pick_key == f"chapters:{MANGADEX.id}:102,103" and f.payload["chapter_ids"] == [102, 103]
    assert f.title == "ch. 5-6 from MangaDex (EN) (Alpha Scans)"


def test_chapters_in_suwayomis_queue_are_not_sent_again(db, w):
    saved(w)
    FindingStore(db).set_mode([w.series_id], "auto")
    w.suwayomi.queued[102] = QueuedChapter(102, "QUEUED")
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    run(db, w)
    assert enqueued(w) == [[103]]


def test_a_saved_manga_that_cannot_be_fetched_is_a_dropped_source(db, w, monkeypatch):
    saved(w)
    FindingStore(db).set_mode([w.series_id], "auto")

    def gone(manga_id):
        raise RuntimeError("manga not found")

    monkeypatch.setattr(w.suwayomi, "chapters", gone)
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    report = run(db, w)
    (f,) = findings(db)
    assert f.reason == REASON_SOURCE_DROPPED.format(source=MANGADEX.display_name) and report.errors == 0
    assert f.pick_key == f"chapters:{MANGADEX.id}:dropped" and enqueued(w) == []


def test_a_source_listing_none_of_the_missing_chapters_within_its_numbers_is_dropped(db, w):
    saved(w)
    w.suwayomi.chapter_list = [chapter(101, "4"), chapter(107, "8")]               # 5 and 6 are gone from it
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    run(db, w)
    (f,) = findings(db)
    assert f.reason == "not on MangaDex (EN) any more - Change source?"


def test_a_source_that_is_only_behind_says_nothing(db, w):
    saved(w)
    w.suwayomi.chapter_list = [chapter(101, "4")]                                   # 5 and 6 are not out there yet
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    report = run(db, w)
    assert findings(db) == [] and report.series_searched == 1 and report.lines == []


def test_suwayomi_down_counts_one_error_and_says_nothing_dropped(db, w):
    saved(w)
    w.suwayomi.unreachable = True
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"]) + wanted(9999, w.folder, chapters=["2"])
    report = run(db, w)
    assert report.errors == 1 and findings(db) == [] and report.series_searched == 0


def test_without_suwayomi_chapters_are_not_searched(db, series, tmp_path):
    w = wire(db, series, tmp_path, suwayomi=False)
    w.entries = wanted(w.series_id, w.folder, chapters=["5"])
    report = run(db, w)
    assert findings(db) == [] and report.errors == 0 and w.suwayomi.calls == []


def test_a_series_with_chapters_on_their_way_is_skipped(db, w):
    saved(w)
    FindingStore(db).set_mode([w.series_id], "auto")
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    run(db, w)
    w.suwayomi.calls.clear()
    report = run(db, w)
    assert report.series_searched == 0 and w.suwayomi.calls == []
    assert all(r.status == DownloadStatus.SENT for r in w.backend.chapter_ledger.all_records())


def test_new_chapters_replace_the_waiting_pick(db, w):
    saved(w)
    w.entries = wanted(w.series_id, w.folder, chapters=["5"])
    run(db, w)
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    run(db, w)
    (f,) = findings(db)
    assert f.fills == ("5", "6") and [g.status for g in findings(db, "gone")] == ["gone"]
