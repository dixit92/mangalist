"""The find pass, volumes (mangalist.downloads.finder): the greedy cover, the Auto / Notify rules, findings offered,
skipped and retired, the budget, one pass at a time. Fake nyaa / qBittorrent, a temporary library, made-up names."""

from __future__ import annotations

import logging
import threading

import pytest

from mangalist.downloads import finder
from mangalist.downloads.contracts import DownloadStatus
from mangalist.downloads.finder import FindBusy, best_uncertain, cover, run_find
from mangalist.downloads.finding import (
    REASON_BULK,
    REASON_NOTIFY,
    REASON_PACK_WHOLE,
    REASON_UNCERTAIN,
    TRIGGER_BULK,
    TRIGGER_NOW,
    TRIGGER_SCHEDULE,
)
from mangalist.downloads.options import KEY_PARTIAL_DOWNLOADS, set_budget_gb, set_flag
from mangalist.downloads.service import PackWait
from mangalist.gui.downloads_backend import BackendError
from mangalist.store.findings import FindingStore

from .finder_fakes import pack_release, release, single_release, wanted, wire

NAMES = [f"Series A v{n:02d} (Digital).cbz" for n in range(1, 9)]


@pytest.fixture(autouse=True)
def quick_packs(monkeypatch):
    monkeypatch.setattr("mangalist.downloads.service.PackWait", lambda: PackWait(timeout=2, interval=0.01))


@pytest.fixture
def w(db, series, tmp_path):
    return wire(db, series, tmp_path)


def run(db, w, trigger=TRIGGER_NOW, **kw):
    return run_find(db, trigger=trigger, wanted=w.wanted, backend=w.backend, **kw)


def auto(db, w):
    FindingStore(db).set_mode([w.series_id], "auto")


def findings(db, status="waiting"):
    return FindingStore(db).list(status)


# --- the greedy cover ---------------------------------------------------------------------------------------------

def test_cover_takes_the_best_then_the_best_for_what_is_still_missing():
    big = release(1, "2", "6", ("2", "3", "4", "5", "6"))
    rest = release(2, "6", "8", ("6", "7"), seeders=99)
    seven = release(3, "7", "7", ("7",), seeders=200)
    picks = cover([big, rest, seven], ["2", "3", "4", "5", "6", "7"])
    assert [(c.info_hash, fills) for c, fills in picks] == [(big.info_hash, ("2", "3", "4", "5", "6")),
                                                            (seven.info_hash, ("7",))]


def test_cover_ties_keep_the_d4_order_and_skip_known_releases_and_light_novels():
    scan = release(1, "2", "2", ("2",), digital=False, seeders=500)
    digital = release(2, "2", "2", ("2",), seeders=1)
    novel = release(3, "3", "3", ("3",), not_comic=True)
    assert [c for c, _ in cover([scan, digital], ["2"])] == [digital]
    assert [c for c, _ in cover([scan, digital], ["2"], skip_hashes=[digital.info_hash.upper()])] == [scan]
    assert cover([novel], ["3"]) == []


def test_numberless_releases_are_never_part_of_the_cover():
    numberless = release(1, None, None, (), is_pack=True)
    assert cover([numberless], ["2"]) == [] and best_uncertain([numberless]) is numberless


# --- Auto / Notify rules ---------------------------------------------------------------------------------------------

def test_auto_sends_a_single_volume_that_fills_a_missing_one(db, w):
    auto(db, w)
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    report = run(db, w)
    assert (report.sent, report.found, report.series_searched, report.errors) == (1, 0, 1, 0)
    (rec,) = w.backend.ledger.all_records()
    assert rec.status == DownloadStatus.SENT and rec.wanted_volumes == ("2",)
    (f,) = findings(db, "sent")
    assert f.record_id == rec.id and f.kind == "volumes" and f.fills == ("2",)
    assert [c for c in w.qbt.calls if c[0] == "set_file_priority"] == []          # a single volume: whole


def test_auto_sends_a_pack_narrowed_to_the_missing_volumes(db, w):
    auto(db, w)
    w.search.results = [pack_release(1, w.nyaa, w.qbt, NAMES, "1", "8", ("2", "3"))]
    w.entries = wanted(w.series_id, w.folder, volumes=["2", "3"])
    report = run(db, w)
    assert report.sent == 1
    (rec,) = w.backend.ledger.all_records()
    assert rec.size_bytes == 2 * 10 * 1024 * 1024                                  # the two kept files count
    assert [c[3] for c in w.qbt.calls if c[0] == "set_file_priority"] == [0]      # the other six: do not download


def test_a_pack_that_cannot_be_narrowed_waits(db, w):
    auto(db, w)
    names = ["Series A v02 (Digital).cbz", "Bonus.cbz"]                            # a name that says no volume
    w.search.results = [pack_release(1, w.nyaa, w.qbt, names, "2", "3", ("2", "3"))]
    w.entries = wanted(w.series_id, w.folder, volumes=["2", "3"])
    report = run(db, w)
    (f,) = findings(db)
    assert report.sent == 0 and f.reason == REASON_PACK_WHOLE and w.backend.ledger.all_records() == []


def test_a_magnet_only_pack_waits(db, w):
    auto(db, w)
    w.search.results = [release(1, "2", "3", ("2", "3"), url="")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2", "3"])
    run(db, w)
    (f,) = findings(db)
    assert f.reason == REASON_PACK_WHOLE and w.nyaa.calls == []


def test_a_pack_waits_when_only_the_missing_volumes_is_off(db, w):
    auto(db, w)
    set_flag(db, KEY_PARTIAL_DOWNLOADS, False)
    w.search.results = [pack_release(1, w.nyaa, w.qbt, NAMES, "1", "8", ("2", "3"))]
    w.entries = wanted(w.series_id, w.folder, volumes=["2", "3"])
    run(db, w)
    (f,) = findings(db)
    assert f.reason == finder.REASON_PARTIAL_OFF and w.backend.ledger.all_records() == []


def test_a_release_whose_numbers_cannot_be_compared_waits_for_what_is_left(db, w):
    auto(db, w)
    w.search.results = [single_release(1, w.qbt, "2"), release(2, None, None, (), is_pack=True)]
    w.entries = wanted(w.series_id, w.folder, volumes=["2", "3"])
    report = run(db, w)
    (f,) = findings(db)
    assert report.sent == 1 and f.reason == REASON_UNCERTAIN and f.fills == ("3",)


def test_notify_offers_and_sends_nothing(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    report = run(db, w)
    (f,) = findings(db)
    assert (report.sent, report.found) == (0, 1) and f.reason == REASON_NOTIFY
    assert f.payload["candidate"]["info_hash"] == w.search.results[0].info_hash and f.size_bytes == 1000
    assert w.backend.ledger.all_records() == [] and w.qbt.calls == []


def test_a_bulk_find_searches_only_the_given_series_and_never_sends(db, w):
    auto(db, w)
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"]) + wanted(9999, w.folder, volumes=["5"], title="Other")
    report = run(db, w, trigger=TRIGGER_BULK, series_ids=[w.series_id])
    (f,) = findings(db)
    assert f.reason == REASON_BULK and report.sent == 0 and len(w.search.calls) == 1


def test_upgrades_are_found_with_the_missing_volumes_in_one_search(db, w):
    w.search.results = [single_release(1, w.qbt, "2"), single_release(2, w.qbt, "5")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"], upgrades=["5"])
    run(db, w)
    assert len(w.search.calls) == 1 and w.search.calls[0][1] == ("2", "5")
    assert sorted(f.kind for f in findings(db)) == ["upgrades", "volumes"]


def test_off_series_and_unfindable_entries_are_not_searched(db, w):
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    FindingStore(db).set_mode([w.series_id], "off")
    assert run(db, w).series_searched == 0 and w.search.calls == []
    FindingStore(db).set_mode([w.series_id], None)
    w.entries = wanted(w.series_id, w.folder, volumes=["2"], findable=False)
    assert run(db, w).series_searched == 0 and w.search.calls == []


def test_a_series_marked_up_to_date_is_searched_only_by_a_bulk_find(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)                                                      # a finding from before the owner's mark
    with db.connect() as con:
        con.execute("INSERT INTO links_cache (folder, behind_override) VALUES (?, 'done')", (str(w.folder),))
    w.search.calls.clear()
    for trigger in (TRIGGER_SCHEDULE, TRIGGER_NOW):
        assert run(db, w, trigger=trigger).series_searched == 0 and w.search.calls == []
    assert findings(db) == []                                       # no longer wanted: retired
    report = run(db, w, trigger=TRIGGER_BULK, series_ids=[w.series_id])
    assert report.series_searched == 1 and findings(db)[0].reason == REASON_BULK


def test_a_series_with_a_download_in_flight_is_skipped(db, w, caplog):
    caplog.set_level(logging.INFO)
    auto(db, w)
    w.backend.ledger.create(w.series_id, release(7, "4", "4", ("4",)), ["4"], str(w.folder))
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    assert run(db, w).series_searched == 0 and w.search.calls == []
    assert "a download is in flight" in caplog.text


def test_a_release_the_series_has_a_record_for_is_not_picked_again(db, w):
    old = single_release(1, w.qbt, "2")
    rec = w.backend.ledger.create(w.series_id, old, ["2"], str(w.folder))
    w.backend.ledger.set_status(rec.id, DownloadStatus.FAILED, expect=(DownloadStatus.SENT,), error="made up")
    w.search.results = [old]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    assert findings(db) == []


def test_a_release_whose_finished_record_was_cleared_is_not_picked_again(db, w):
    """Clearing finished download records (Settings > Scheduled tasks) must not make the find pass send a release twice."""
    old = single_release(1, w.qbt, "2")
    ledger = w.backend.ledger
    rec = ledger.create(w.series_id, old, ["2"], str(w.folder))
    ledger.set_status(rec.id, DownloadStatus.CANCELLED, expect=(DownloadStatus.SENT,), error="made up")
    newest = ledger.create(w.series_id, single_release(2, w.qbt, "3"), ["3"], str(w.folder))   # the newest one stays
    ledger.set_status(newest.id, DownloadStatus.FAILED, expect=(DownloadStatus.SENT,), error="made up")
    assert ledger.clear_finished(0).cleared == 1 and ledger.get(rec.id) is None
    w.search.results = [old]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    assert findings(db) == []


# --- findings: skipped, retired, refreshed --------------------------------------------------------------------------

def test_a_skipped_pick_is_never_offered_or_sent_again(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    (f,) = findings(db)
    w.backend.skip_finding(f.id)
    auto(db, w)
    report = run(db, w)
    assert (report.sent, report.found) == (0, 0) and findings(db) == [] and w.backend.ledger.all_records() == []


def test_the_next_best_release_is_offered_after_a_skip(db, w):
    first, second = single_release(1, w.qbt, "2", seeders=50), single_release(2, w.qbt, "2", seeders=5)
    w.search.results = [first, second]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    w.backend.skip_finding(findings(db)[0].id)
    run(db, w)
    (f,) = findings(db)
    assert f.pick_key == f"nyaa:{second.info_hash}"


def test_what_a_pass_does_not_find_again_is_retired(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    w.search.results = []
    run(db, w)
    assert findings(db) == [] and [f.status for f in findings(db, None)] == ["gone"]


def test_a_series_that_left_the_wanted_list_has_its_findings_retired(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    w.entries = []
    run(db, w)
    assert findings(db) == []


def test_a_failed_search_keeps_the_findings_and_counts_an_error(db, w):
    from mangalist.services.nyaa import NyaaError

    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    w.search.error = NyaaError("nyaa answered 503")
    report = run(db, w)
    assert report.errors == 1 and len(findings(db)) == 1 and "nyaa answered 503" in report.lines[0]


def test_nyaa_switched_off_searches_no_volumes(db, w):
    from dataclasses import replace

    w.backend.set_nyaa_options(replace(w.backend.nyaa_options(), enabled=False))
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    report = run(db, w)
    assert w.search.calls == [] and report.errors == 0 and report.series_searched == 0


# --- sending through the budget -----------------------------------------------------------------------------------

def test_what_does_not_fit_the_budget_is_queued_and_still_counts_as_sent(db, w):
    auto(db, w)
    set_budget_gb(db, 0.001)                                        # about 1 MB
    w.search.results = [single_release(1, w.qbt, "2", size=600_000), single_release(2, w.qbt, "3", size=600_000)]
    w.entries = wanted(w.series_id, w.folder, volumes=["2", "3"])
    report = run(db, w)
    assert report.sent == 2
    assert sorted(r.status for r in w.backend.ledger.all_records()) == [DownloadStatus.QUEUED, DownloadStatus.SENT]


def test_a_release_bigger_than_the_whole_budget_waits_with_the_reason(db, w):
    auto(db, w)
    set_budget_gb(db, 0.001)
    w.search.results = [single_release(1, w.qbt, "2", size=2_000_000)]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    (f,) = findings(db)
    assert "bigger than the download budget" in f.reason and w.backend.ledger.all_records() == []


def test_without_qbittorrent_an_auto_pick_waits(db, series, tmp_path):
    w = wire(db, series, tmp_path, qbittorrent=False)
    auto(db, w)
    w.search.results = [release(1, "2", "2", ("2",))]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    run(db, w)
    (f,) = findings(db)
    assert f.reason == finder.REASON_NO_QBITTORRENT


def test_qbittorrent_down_leaves_the_pick_waiting_and_the_next_pass_sends_it(db, w):
    auto(db, w)
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    w.qbt.fail["add"] = ConnectionError("connection refused")
    report = run(db, w)
    (f,) = findings(db)
    assert report.errors == 1 and "sending failed" in f.reason
    del w.qbt.fail["add"]
    report = run(db, w)
    assert report.sent == 1 and findings(db) == [] and findings(db, "sent")[0].id == f.id


def test_a_release_qbittorrent_rejects_is_not_offered(db, w):
    from mangalist.services.qbittorrent import TorrentRejected

    auto(db, w)
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    w.qbt.fail["add"] = TorrentRejected("the link is not a valid torrent")
    report = run(db, w)
    assert findings(db) == [] and report.sent == 0 and "is gone" in report.lines[0]


# --- runs, the log, one pass at a time ------------------------------------------------------------------------------

def test_a_run_is_recorded_and_logged_once(db, w, caplog):
    caplog.set_level(logging.INFO, logger="mangalist.downloads.finder")
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    report = run(db, w, trigger=TRIGGER_SCHEDULE)
    last = w.backend.last_find_run()
    assert last.id == report.run_id and last.trigger == "schedule" and last.finished_at
    assert (last.series_searched, last.found, last.summary) == (1, 1, "1 series searched, 0 sent, 1 waiting for you")
    summaries = [r.getMessage() for r in caplog.records if "series searched" in r.getMessage()]
    assert summaries == [f"Find pass {report.run_id} (schedule): 1 series searched, 0 sent, 1 waiting for you"]
    caplog.clear()
    run(db, w, trigger=TRIGGER_SCHEDULE)
    assert "waiting for you; nothing new" in caplog.text            # found again: not new


def test_a_second_pass_while_one_runs_is_refused(db, w):
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    started, release_it = threading.Event(), threading.Event()

    def slow_search(titles, missing, held):
        started.set()
        release_it.wait(5)
        return []

    w.search.search = slow_search
    errors = []
    t = threading.Thread(target=lambda: run(db, w))
    t.start()
    try:
        assert started.wait(5)
        with pytest.raises(FindBusy):
            run(db, w)
        try:
            w.backend.find_now()
        except BackendError as exc:
            errors.append(str(exc))
    finally:
        release_it.set()
        t.join(5)
    assert errors and "already running" in errors[0]


def test_an_unfinished_recent_run_of_another_process_counts_as_running(db, w):
    store = FindingStore(db)
    store.start_run("schedule")                                     # started, never finished: elsewhere, or a crash
    with pytest.raises(FindBusy, match="elsewhere"):
        run(db, w)
    with db.connect() as con:                                       # a crash hours ago does not block for ever
        con.execute("UPDATE find_runs SET started_at = '2020-01-01T00:00:00+00:00'")
    assert run(db, w).errors == 0


def test_stopping_between_series(db, w):
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    report = run(db, w, should_stop=lambda: True)
    assert report.series_searched == 0 and "stopped" in report.lines[-1]


def test_without_the_wanted_list_module_nothing_is_searched(db, w, monkeypatch):
    import builtins

    real = builtins.__import__

    def no_wanted(name, *args, **kw):
        if name == "wanted" or name.endswith(".wanted") or name == "mangalist.wanted":
            raise ImportError("not here")
        return real(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", no_wanted)
    report = run_find(db, trigger=TRIGGER_NOW, backend=w.backend)
    assert report.errors == 1 and report.lines == ["the wanted list is not available in this build"]


def test_the_trigger_is_checked(db, w):
    with pytest.raises(ValueError):
        run(db, w, trigger="hourly")


# --- the backend's quick calls -------------------------------------------------------------------------------------

def test_backend_modes_and_findings(db, w):
    b = w.backend
    assert b.default_mode() == "notify" and b.own_series_mode(w.series_id) is None
    b.set_series_mode([w.series_id], "auto")
    assert b.series_modes([w.series_id]) == {w.series_id: "auto"} and b.own_series_mode(w.series_id) == "auto"
    b.set_default_mode("off")
    b.set_series_mode([w.series_id], None)
    assert b.series_modes([w.series_id]) == {w.series_id: "off"}
    with pytest.raises(BackendError):
        b.set_default_mode("sometimes")
    with pytest.raises(BackendError):
        b.set_series_mode([w.series_id], "always")
    assert b.findings() == [] and b.last_find_run() is None


def test_backend_find_now_and_bulk(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    assert w.backend.find_now().trigger == "now"
    report = w.backend.find_now([w.series_id])
    assert report.trigger == "bulk" and w.backend.findings()[0].reason == REASON_BULK         # refreshed, not new
    with pytest.raises(BackendError, match="not skipped"):
        w.backend.skip_finding(12345)


def test_the_default_wanted_list_is_lane_as_wanted_list(db, w):
    """No injected list: mangalist.wanted.wanted_list over the database (a series without known gaps: nothing to do)."""
    report = run_find(db, trigger=TRIGGER_NOW, backend=w.backend)
    assert report.errors == 0 and report.series_searched == 0 and w.search.calls == []
