"""Scheduled tasks as the Settings page uses them (no Qt): the runner's records read without touching them, whether a
runner is up, the Run now requests and the scheduler running them once, the clean-up job and the rows' words."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest

from mangalist import store
from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.gui import task_rules
from mangalist.headless import tasks
from mangalist.headless.jobs import Job, JobContext, JobRegistry, JobResult
from mangalist.headless.records_job import current_retention, make_clean_records, retention_days
from mangalist.headless.runner import InstanceLock
from mangalist.headless.schedule import UTC, DailyAt, EveryHours, MonthlyAt, WeeklyAt
from mangalist.headless.scheduler import Scheduler
from mangalist.headless.settings import save_schedule, SCHEDULE_BY_JOB
from mangalist.headless.state import JobState, StateStore

from .conftest import zone


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


# --- reading the runner's records ----------------------------------------------------------------------------------------

def test_read_states_reads_without_touching_the_file(tmp_path):
    path = tmp_path / "headless-state.json"
    assert tasks.read_states(path) == {}
    st = StateStore(path)
    st.get("rescan").last_status = "ok"
    st.get("rescan").running_since = datetime(2026, 10, 15, 3, 30, tzinfo=UTC)
    st.save()
    got = tasks.read_states(path)
    assert got["rescan"].last_status == "ok" and got["rescan"].running_since == datetime(2026, 10, 15, 3, 30, tzinfo=UTC)
    path.write_text("{not json", encoding="utf-8")
    assert tasks.read_states(path) == {} and path.read_text(encoding="utf-8") == "{not json"      # not renamed aside


@pytest.mark.skipif(os.name == "nt", reason="the lock check is exercised on POSIX")
def test_runner_running_sees_the_instance_lock_without_taking_it(tmp_path):
    lock_path = tmp_path / "headless.lock"
    assert tasks.runner_running(lock_path) is False
    holder = InstanceLock(lock_path)
    assert holder.acquire()
    try:
        assert tasks.runner_running(lock_path) is True
        assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())      # untouched
    finally:
        holder.release()
    assert tasks.runner_running(lock_path) is False


# --- Run now ---------------------------------------------------------------------------------------------------------

def test_requests_are_left_listed_in_page_order_and_taken_once(db):
    assert tasks.pending_requests(db) == [] and tasks.take_requests(db) == []
    tasks.request_run(db, "clean-records")
    tasks.request_run(db, "rescan")
    tasks.request_run(db, "rescan")                           # asking twice is one request
    assert tasks.pending_requests(db) == ["rescan", "clean-records"]
    assert tasks.take_requests(db) == ["rescan", "clean-records"] and tasks.take_requests(db) == []
    with pytest.raises(ValueError):
        tasks.request_run(db, "nonsense")
    assert db.get_setting("headless.run_request.rescan") is None, "kept out of the settings the GUI copies around"


def _scheduler(tmp_path, clock, take, *jobs):
    store_ = StateStore(tmp_path / "state.json").load()
    return Scheduler(JobRegistry(jobs), store_, UTC, now=clock, take_requests=take), store_


def test_a_run_now_request_runs_the_job_once_and_keeps_its_schedule(tmp_path, clock):
    calls = []
    job = Job("rescan", lambda ctx: (calls.append(1), JobResult("ok", "done"))[1], EveryHours(12))
    asked = [["rescan"], [], ["nonsense"]]
    sched, store_ = _scheduler(tmp_path, clock, lambda: asked.pop(0) if asked else [], job)
    sched.plan()
    planned = store_.get("rescan").next_run
    clock.advance(minutes=30)
    assert sched.run_requested() == 1 and len(calls) == 1
    st = store_.get("rescan")
    assert st.last_status == "ok" and st.last_message == "done" and st.next_run == planned, "its schedule stays"
    assert st.running_since is None
    assert sched.run_requested() == 0 and sched.run_requested() == 0       # nothing asked; an unknown name is dropped
    assert len(calls) == 1


def test_a_job_that_is_off_can_still_be_run_now(tmp_path, clock):
    calls = []
    job = Job("rescan", lambda ctx: (calls.append(1), JobResult())[1], None, enabled=False)
    sched, store_ = _scheduler(tmp_path, clock, lambda: ["rescan"], job)
    sched.plan()
    assert sched.run_requested() == 1 and calls == [1] and store_.get("rescan").next_run is None


def test_a_running_job_is_flagged_and_a_killed_runners_flag_is_cleared_at_start(tmp_path, clock):
    seen = []
    sched, store_ = _scheduler(tmp_path, clock, None, Job("rescan", lambda ctx: (
        seen.append(tasks.read_states(tmp_path / "state.json")["rescan"].running_since), JobResult())[1], EveryHours(12)))
    sched.plan()
    sched.run_job(sched.registry.get("rescan"))
    assert seen == [clock.now] and store_.get("rescan").running_since is None
    store_.get("rescan").running_since = clock.now                 # the runner was killed mid-run
    store_.save()
    again, store2 = _scheduler(tmp_path, clock, None, Job("rescan", lambda ctx: JobResult(), EveryHours(12)))
    again.plan()
    assert store2.get("rescan").running_since is None


def test_the_loop_polls_for_requests_between_schedule_checks(tmp_path, clock):
    waits = []
    sched, _ = _scheduler(tmp_path, clock, lambda: [], Job("rescan", lambda ctx: JobResult(), EveryHours(12)))
    sched._wait = lambda seconds: (waits.append(seconds), sched.stop())[0]
    sched.run_forever()
    assert waits == [sched.request_poll] and sched.request_poll <= 10


# --- the clean-up job ------------------------------------------------------------------------------------------------------

def test_the_retention_follows_the_period():
    assert retention_days(DailyAt(5, 30)) == 1 and retention_days(WeeklyAt(6, 5, 30)) == 7
    assert retention_days(MonthlyAt(1, 5, 30)) == 30 and retention_days(None) is None
    assert retention_days(EveryHours(6)) == 1 and retention_days(EveryHours(72)) == 3


def _record(db, status, age_days, n, series_id, now):
    when = (now - timedelta(days=age_days)).replace(microsecond=0).isoformat()
    with db.connect() as con:
        rid = con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status, external_ref)"
                          " VALUES (?, ?, ?, '{}', 'qbittorrent', ?, ?)", (when, when, series_id, status, f"{n:040x}")).lastrowid
        if status == S.REMOVED:
            con.execute("INSERT INTO replacements (download_id, status, created_at, updated_at) VALUES (?, 'nothing', 't', 't')",
                        (rid,))


def test_the_job_is_skipped_while_off_and_clears_by_the_chosen_period(db, tmp_path):
    from datetime import timezone

    now = datetime.now(timezone.utc)
    root = db.add_root(str(tmp_path / "lib"), "Lib")
    db.record_scan(root.id, tmp_path / "lib", [store.SeriesSeen("S1", None, 0)])
    sid = db.list_series()[0].id
    for n, (status, age) in enumerate([(S.REMOVED, 20), (S.CANCELLED, 3), (S.REMOVED, 0.2), (S.FILED, 90), (S.SENT, 0)], 1):
        _record(db, status, age, n, sid, now)
    job = make_clean_records(open_db=lambda: db)
    off = job(JobContext())
    assert off.status == "skipped" and "Scheduled tasks" in off.message and current_retention(db) is None
    save_schedule(db, SCHEDULE_BY_JOB["clean-records"], "weekly@sun 05:30")
    assert current_retention(db) == 7
    result = job(JobContext())
    assert result.status == "ok" and result.extra["cleared"] == 1 and result.extra["older_than_days"] == 7
    assert result.message == "cleared 1 of 3 finished records (kept 2 newer than the period)"
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 4


# --- the rows' words -----------------------------------------------------------------------------------------------------------

def test_clock_and_durations_and_moments():
    tz = zone("America/New_York")
    now = datetime(2026, 10, 15, 18, 5, tzinfo=UTC)
    assert task_rules.clock_text(tz, now) == ("Times are in the container's time zone: America/New_York (UTC-04:00), "
                                              "now 14:05.")
    assert task_rules.moment_text(datetime(2026, 10, 15, 7, 30, tzinfo=UTC), tz, now) == "03:30"
    assert task_rules.moment_text(datetime(2026, 10, 16, 7, 30, tzinfo=UTC), tz, now) == "Oct 16 03:30"
    assert [task_rules.duration_text(s) for s in (7, 90, 120, 5400)] == ["7 s", "1 min 30 s", "2 min", "1 h 30 min"]


def test_rows_for_every_task(db):
    now = datetime(2026, 10, 15, 18, 5, tzinfo=UTC)
    rows = task_rules.build_rows(db, {}, {}, tz=UTC, now=now, runner=True, downloads_on=False)
    assert [r.job for r in rows] == [t.job for t in tasks.TASKS]
    by = {r.job: r for r in rows}
    assert by["clean-records"].badge == "Off" and not by["clean-records"].can_run
    assert by["downloads"].badge == "Downloads off" and by["rescan"].badge == "Scheduled" and by["rescan"].can_run
