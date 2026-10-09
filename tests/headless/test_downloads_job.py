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
