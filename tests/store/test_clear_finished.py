"""Clearing finished download records (owner, 2026-10-15): which records go, which stay and why, what is remembered.
Made-up series and torrents; the ledger and the replaced-chapters table are real, in a temporary database."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from mangalist import store
from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.store.downloads import META_CLEARED_RELEASES, DownloadLedger

from .test_downloads_store import candidate

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def hash_of(n: int) -> str:
    return f"{n:040x}"


@pytest.fixture
def lib(db, tmp_path):
    root = db.add_root(str(tmp_path / "lib"), "Lib")
    db.record_scan(root.id, tmp_path / "lib", [store.SeriesSeen(f"S{i}", None, 0) for i in (1, 2)])
    return DownloadLedger(db, tool=None), db, {s.rel_path: s.id for s in db.list_series()}


def add(db, series_id, status, age_days, *, n=0, tool="qbittorrent", examined=True) -> int:
    """A record in *status* last changed *age_days* before NOW (written straight into the table). A REMOVED one has
    been looked at by the replaced-chapters pass (a batch "nothing") unless *examined* is False."""
    when = (NOW - timedelta(days=age_days)).replace(microsecond=0).isoformat()
    with db.connect() as con:
        cur = con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status, external_ref)"
                          " VALUES (?, ?, ?, '{}', ?, ?, ?)", (when, when, series_id, tool, status, hash_of(n)))
        rid = cur.lastrowid
    if examined and status == S.REMOVED:
        batch(db, rid, "nothing")
    return rid


def ids(db) -> set:
    with db.connect() as con:
        return {r[0] for r in con.execute("SELECT id FROM ledger")}


def batch(db, download_id, status):
    with db.connect() as con:
        con.execute("INSERT INTO replacements (download_id, status, created_at, updated_at) VALUES (?, ?, 't', 't')"
                    " ON CONFLICT(download_id) DO UPDATE SET status = excluded.status", (download_id, status))


def test_only_old_finished_records_go(lib):
    ledger, db, series = lib
    a = series["S1"]
    old_removed = add(db, a, S.REMOVED, 40, n=1)
    old_cancelled = add(db, a, S.CANCELLED, 40, n=2)
    recent = add(db, a, S.REMOVED, 2, n=3)
    filed = add(db, a, S.FILED, 90, n=4)
    failed = add(db, a, S.FAILED, 90, n=5)
    sent = add(db, a, S.SENT, 90, n=6)
    queued = add(db, a, S.QUEUED, 90, n=7)
    downloaded = add(db, a, S.DOWNLOADED, 90, n=8)
    newest = add(db, a, S.REMOVED, 60, n=9)
    batch(db, old_removed, "purged")                    # (examined long ago; the batch is settled)
    report = ledger.clear_finished(30, now=NOW)
    assert ids(db) == {recent, filed, failed, sent, queued, downloaded, newest}
    assert (report.finished, report.cleared, report.kept_recent, report.kept_newest) == (4, 2, 1, 1)


def test_a_dry_run_changes_nothing(lib):
    ledger, db, series = lib
    add(db, series["S1"], S.REMOVED, 40, n=1)
    add(db, series["S1"], S.SENT, 1, n=2)
    before = ids(db)
    assert ledger.clear_finished(7, now=NOW, dry_run=True).cleared == 1
    assert ids(db) == before and db.get_meta(META_CLEARED_RELEASES) is None


def test_the_period_is_the_age_cut(lib):
    ledger, db, series = lib
    add(db, series["S1"], S.REMOVED, 0.5, n=1)
    add(db, series["S1"], S.REMOVED, 3, n=2)
    add(db, series["S1"], S.REMOVED, 10, n=3)
    add(db, series["S1"], S.REMOVED, 45, n=4)
    add(db, series["S1"], S.SENT, 1, n=5)                       # the newest row: always kept
    counts = {days: ledger.clear_finished(days, now=NOW, dry_run=True).cleared for days in (1, 7, 30, 0)}
    assert counts == {1: 3, 7: 2, 30: 1, 0: 4}


def test_the_newest_record_stays_so_its_id_is_never_reused(lib):
    ledger, db, series = lib
    old = add(db, series["S1"], S.REMOVED, 40, n=1)
    last = add(db, series["S1"], S.CANCELLED, 40, n=2)
    ledger.clear_finished(7, now=NOW)
    assert ids(db) == {last}
    assert add(db, series["S1"], S.SENT, 0, n=3) > last > old


def test_replaced_chapters_still_waiting_keep_their_download(lib):
    ledger, db, series = lib
    a = series["S1"]
    waiting = add(db, a, S.REMOVED, 40, n=1)
    held = add(db, a, S.REMOVED, 40, n=2)
    settled = add(db, a, S.REMOVED, 40, n=3)
    add(db, a, S.SENT, 0, n=4)
    for rid, state in ((waiting, "pending"), (held, "held"), (settled, "deleted")):
        batch(db, rid, state)
    report = ledger.clear_finished(7, now=NOW)
    assert ids(db) >= {waiting, held} and settled not in ids(db) and report.kept_waiting_chapters == 2


def test_a_removed_record_the_replaced_chapters_pass_has_not_seen_stays(lib):
    ledger, db, series = lib
    a = series["S1"]
    db.set_meta("upgrades.first_download", "2")
    before_upgrades = add(db, a, S.REMOVED, 40, n=1, examined=False)    # id 1: from before the upgrades existed
    add(db, a, S.REMOVED, 40, n=2, examined=False)              # id 2: also never looked at (ids <= first are not)
    unseen = add(db, a, S.REMOVED, 40, n=3, examined=False)     # id 3 > first and no batch: not looked at yet
    add(db, a, S.REMOVED, 40, n=4)
    add(db, a, S.SENT, 0, n=5)
    report = ledger.clear_finished(7, now=NOW)
    assert ids(db) == {unseen, ids_max(db)} and report.kept_unexamined == 1 and before_upgrades not in ids(db)


def ids_max(db) -> int:
    return max(ids(db))


def test_chapter_records_are_never_looked_at_so_none_waits_for_that(lib):
    ledger, db, series = lib
    db.set_meta("upgrades.first_download", "0")
    chapter = add(db, series["S1"], S.REMOVED, 40, n=1, tool="suwayomi", examined=False)
    add(db, series["S1"], S.SENT, 0, n=2)
    assert ledger.clear_finished(7, now=NOW).cleared == 1 and chapter not in ids(db)


def test_a_cleared_torrent_is_remembered_so_it_is_never_picked_again(lib):
    ledger, db, series = lib
    add(db, series["S1"], S.REMOVED, 40, n=11)
    add(db, series["S2"], S.CANCELLED, 40, n=12)
    add(db, series["S1"], S.REMOVED, 40, n=13, tool="suwayomi")      # a chapter: no torrent, nothing to remember
    add(db, series["S1"], S.SENT, 0, n=14)
    ledger.clear_finished(7, now=NOW)
    assert ledger.cleared_releases() == {series["S1"]: {hash_of(11)}, series["S2"]: {hash_of(12)}}
    add(db, series["S1"], S.FILED, 80, n=15)
    add(db, series["S1"], S.REMOVED, 80, n=16)
    add(db, series["S1"], S.SENT, 0, n=17)
    ledger.clear_finished(7, now=NOW)
    assert ledger.cleared_releases()[series["S1"]] == {hash_of(11), hash_of(16)}
    assert json.loads(db.get_meta(META_CLEARED_RELEASES))[str(series["S2"])] == [hash_of(12)]


def test_clearing_nothing_writes_nothing(lib):
    ledger, db, series = lib
    add(db, series["S1"], S.SENT, 40, n=1)
    report = ledger.clear_finished(7, now=NOW)
    assert report.cleared == 0 and report.summary() == "no finished records" and db.get_meta(META_CLEARED_RELEASES) is None


def test_the_summary_says_what_was_kept(lib):
    ledger, db, series = lib
    add(db, series["S1"], S.REMOVED, 40, n=1)
    add(db, series["S1"], S.REMOVED, 1, n=2)
    add(db, series["S1"], S.SENT, 0, n=3)
    assert ledger.clear_finished(7, now=NOW).summary() == "cleared 1 of 2 finished records (kept 1 newer than the period)"
