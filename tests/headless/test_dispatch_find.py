"""The ``dispatch-batch`` job runs the find pass on its schedule (automatic downloads, 2026-10-10): trigger
``schedule``, its summary as the job's message, skipped while another pass runs; its Settings label. Fakes only."""

from __future__ import annotations

import logging

import pytest

from mangalist import store
from mangalist.downloads import finder
from mangalist.headless.jobs import JobContext, build_registry, make_dispatch
from mangalist.headless.settings import SCHEDULE_BY_JOB, HeadlessSettings

from ..downloads.fakes import data, scan
from ..downloads.finder_fakes import single_release, wanted, wire


@pytest.fixture
def w(tmp_path):
    store.reset_stores()
    db = store.get_store()
    root = tmp_path / "library" / "Manga"
    (root / "Series A").mkdir(parents=True)
    (root / "Series A" / "Series A v01 (Digital).cbz").write_bytes(data("held v01"))
    r = db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(r.id, "Series A").id
    return wire(db, (sid, root / "Series A"), tmp_path)


def job(w, enabled=True):
    return make_dispatch(enabled, open_db=lambda: w.backend.db, backend_factory=lambda db: w.backend, wanted=w.wanted)


def test_the_job_runs_a_scheduled_pass_and_reports_its_summary(w, caplog):
    caplog.set_level(logging.INFO)
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    result = job(w)(JobContext())
    assert result.status == "ok" and result.message == "1 series searched, 0 sent, 1 waiting for you"
    assert result.extra["found"] == 1 and w.backend.last_find_run().trigger == "schedule"
    assert "nothing dispatched" not in caplog.text


def test_a_pass_with_errors_is_an_error_result(w):
    from mangalist.services.nyaa import NyaaError

    w.search.error = NyaaError("nyaa answered 503")
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    result = job(w)(JobContext())
    assert result.status == "error" and "1 failed" in result.message


def test_downloads_off_runs_nothing(w):
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    result = job(w, enabled=False)(JobContext())
    assert result.status == "skipped" and w.search.calls == [] and w.backend.last_find_run() is None


def test_a_pass_already_running_skips_the_scheduled_one(w):
    assert finder._PASS.acquire(blocking=False)
    try:
        result = job(w)(JobContext())
    finally:
        finder._PASS.release()
    assert result.status == "skipped" and "already running" in result.message


def test_the_settings_label_and_the_job_description():
    assert SCHEDULE_BY_JOB["dispatch-batch"].label == "Find and send missing volumes and chapters"
    reg = build_registry(HeadlessSettings.from_env({"MANGALIST_DOWNLOADS": "1"}))
    job_ = reg.get("dispatch-batch")
    assert job_.active and job_.description.startswith("Find and send missing volumes and chapters")
