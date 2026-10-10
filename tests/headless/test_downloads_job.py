"""The ``downloads`` job: skipped without records / a connection / a client; one arrivals pass otherwise; the
qBittorrent password never reaches the job's result or the log."""

from __future__ import annotations

import logging

from mangalist import store
from mangalist.downloads.contracts import QbtConnection
from mangalist.headless.downloads_job import JOB_NAME, make_downloads_job
from mangalist.headless.jobs import JobContext
from mangalist.store.downloads import DownloadLedger

from ..downloads.fakes import HASH, FakeQbt, candidate, data, scan

SECRET = "not-a-real-password"


def _setup(tmp_path):
    store.reset_stores()
    db = store.get_store()
    root = tmp_path / "library" / "Manga"
    (root / "Series A").mkdir(parents=True)
    (root / "Series A" / "Series A v01.cbz").write_bytes(data("v01"))
    r = db.add_root(str(root))
    scan(db)
    ledger = DownloadLedger(db)
    qbt = FakeQbt(tmp_path / "torrents")
    qbt.save_root.mkdir()
    return ledger, qbt, db.get_series(r.id, "Series A").id, root / "Series A"


def test_skips_without_records_or_connection_or_client(tmp_path):
    ledger, qbt, sid, sdir = _setup(tmp_path)
    job = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt)
    assert JOB_NAME == "downloads"
    assert job(JobContext()).message == "no downloads in progress"
    ledger.create(sid, candidate(), ["2"], str(sdir))
    assert job(JobContext()).message == "no qBittorrent connection set up"
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", SECRET))
    none = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: None)
    assert none(JobContext()).status == "skipped"


def test_runs_one_pass(tmp_path, caplog):
    ledger, qbt, sid, sdir = _setup(tmp_path)
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", SECRET))
    rec = ledger.create(sid, candidate(), ["2"], str(sdir))
    qbt.put(HASH, "Pack", {"Series A v02.cbz": data("v02")}, state="stoppedUP")
    seen = []
    job = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: seen.append(conn) or qbt)
    with caplog.at_level(logging.DEBUG):
        result = job(JobContext())
    assert result.status == "ok" and result.extra["filed"] == 1 and result.extra["removed"] == 1
    assert ledger.get(rec.id).status == "removed" and seen[0].password == SECRET
    assert SECRET not in caplog.text and SECRET not in repr(result)


def test_qbittorrent_down_is_an_error_result(tmp_path):
    ledger, qbt, sid, sdir = _setup(tmp_path)
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", SECRET))
    ledger.create(sid, candidate(), ["2"], str(sdir))
    qbt.unreachable = True
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt)(JobContext())
    assert result.status == "error" and "could not list" in result.message


def test_after_filing_the_series_is_rescanned_and_its_mangapixer_library_asked_to_scan(tmp_path, monkeypatch):
    from mangalist.services.mangapixer import scans
    from mangalist.store.mangapixer import MangaPixerCache, Mapping

    ledger, qbt, sid, sdir = _setup(tmp_path)
    db = ledger.store
    root_id = db.list_roots()[0].id
    MangaPixerCache(db).save_mapping(Mapping(root_id=root_id, library_id="lib0manga"))
    asked = []
    monkeypatch.setattr(scans, "request_scans",
                        lambda cache, libs: asked.append(sorted(libs)) or scans.ScanReport(started=sorted(libs)))
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", SECRET))
    ledger.create(sid, candidate(), ["2"], str(sdir))
    qbt.put(HASH, "Pack", {"Series A v02.cbz": data("v02")}, state="uploading")
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt)(JobContext())
    assert result.extra["filed"] == 1 and asked == [["lib0manga"]]
    assert "rescan ok" in result.message and "MangaPixer scans: 1 started" in result.message
    held = {u.vol_from for u in db.list_units(sid) if u.kind == "volume"}
    assert held == {"1", "2"}                          # recorded now, not at the nightly rescan


def test_pending_mangapixer_scans_are_retried_even_without_downloads(tmp_path, monkeypatch):
    from mangalist.services.mangapixer import scans
    from mangalist.store.mangapixer import MangaPixerCache

    ledger, qbt, sid, sdir = _setup(tmp_path)
    MangaPixerCache(ledger.store).set_scan_pending("lib0manga", "2026-01-01T00:00:00Z")
    asked = []
    monkeypatch.setattr(scans, "request_scans", lambda cache, libs: asked.append(sorted(libs)) or scans.ScanReport())
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt)(JobContext())
    assert result.message.startswith("no downloads in progress") and asked == [[]]


def test_scan_requests_switched_off_in_settings_are_not_sent(tmp_path, monkeypatch):
    from mangalist.downloads.options import KEY_SCAN_AFTER_FILING, set_flag
    from mangalist.services.mangapixer import scans
    from mangalist.store.mangapixer import MangaPixerCache, Mapping

    ledger, qbt, sid, sdir = _setup(tmp_path)
    db = ledger.store
    MangaPixerCache(db).save_mapping(Mapping(root_id=db.list_roots()[0].id, library_id="lib0manga"))
    MangaPixerCache(db).set_scan_pending("lib1other", "2026-01-01T00:00:00Z")
    set_flag(db, KEY_SCAN_AFTER_FILING, False)
    asked = []
    monkeypatch.setattr(scans, "request_scans", lambda cache, libs: asked.append(sorted(libs)) or scans.ScanReport())
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", SECRET))
    ledger.create(sid, candidate(), ["2"], str(sdir))
    qbt.put(HASH, "Pack", {"Series A v02.cbz": data("v02")}, state="uploading")
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt)(JobContext())
    assert result.extra["filed"] == 1 and asked == []                     # neither the new library nor the pending one
    assert "rescan ok" in result.message and "MangaPixer" not in result.message


# --- the download budget: the queue is handed over in the same pass ---------------------------------------------------

def test_remove_completed_frees_room_and_the_queue_is_handed_over_in_the_same_pass(tmp_path, caplog):
    from mangalist.downloads.budget import GB
    from mangalist.downloads.options import set_budget_gb

    ledger, qbt, sid, sdir = _setup(tmp_path)
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", SECRET))
    set_budget_gb(ledger.store, 50)
    done = ledger.create(sid, candidate(size_bytes=40 * GB), ["2"], str(sdir))
    waiting = ledger.create(sid, candidate("cd" * 20, size_bytes=30 * GB, torrent_url="https://nyaa.example/q.torrent"),
                            ["3"], str(sdir), status="queued")
    qbt.put(HASH, "Pack", {"Series A v02.cbz": data("v02")}, state="stoppedUP")
    job = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt, after=None, replaced=None)
    with caplog.at_level(logging.INFO):
        result = job(JobContext())
    assert ledger.get(done.id).status == "removed" and ledger.get(waiting.id).status == "sent"
    assert result.status == "ok" and result.extra["queue_sent"] == 1
    assert "queue: 1 handed over; using 30 GB of 50 GB" in result.message
    assert "handed to qBittorrent (was 1st in line)" in caplog.text and SECRET not in caplog.text


def test_only_queued_downloads_still_run_the_pass(tmp_path):
    from mangalist.downloads.budget import GB

    ledger, qbt, sid, sdir = _setup(tmp_path)
    job = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt, after=None, replaced=None)
    ledger.create(sid, candidate(size_bytes=1 * GB), ["2"], str(sdir), status="queued")
    assert job(JobContext()).message == "no qBittorrent connection set up"          # not "no downloads in progress"
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", SECRET))
    result = job(JobContext())
    assert result.status == "ok" and result.extra["queue_sent"] == 1 and result.extra["checked"] == 0
