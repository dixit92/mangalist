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
