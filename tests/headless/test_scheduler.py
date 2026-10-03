"""Scheduler: planning, missed runs after downtime (once, not N times), persistence, shutdown."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from mangalist.headless.jobs import Job, JobContext, JobRegistry, JobResult
from mangalist.headless.schedule import UTC, DailyAt, EveryHours
from mangalist.headless.scheduler import Scheduler
from mangalist.headless.state import StateStore


def _counting_job(name="rescan", schedule=EveryHours(12), **kw):
    calls = []

    def func(ctx: JobContext) -> JobResult:
        calls.append(ctx)
        return JobResult("ok", f"run {len(calls)}")

    return Job(name, func, schedule, **kw), calls


def _scheduler(tmp_path, clock, *jobs, tz=UTC):
    store = StateStore(tmp_path / "state.json").load()
    return Scheduler(JobRegistry(jobs), store, tz, now=clock), store


def test_first_start_plans_the_next_slot_and_runs_nothing(tmp_path, clock):
    job, calls = _counting_job(schedule=DailyAt(3, 30))
    sched, store = _scheduler(tmp_path, clock, job)
    planned = sched.plan()
    assert planned["rescan"] == datetime(2026, 6, 2, 3, 30, tzinfo=UTC)
    assert sched.run_once() == 0 and calls == []
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["jobs"]["rescan"]["next_run"] == "2026-06-02T03:30:00+00:00"
    assert saved["jobs"]["rescan"]["schedule"] == "daily@03:30"


def test_due_job_runs_and_is_replanned_from_its_finish_time(tmp_path, clock):
    job, calls = _counting_job(schedule=EveryHours(12))
    sched, store = _scheduler(tmp_path, clock, job)
    sched.plan()
    clock.advance(hours=12)
    assert sched.run_once() == 1
    st = store.get("rescan")
    assert st.last_run == clock.now and st.last_status == "ok" and st.runs == 1
    assert st.next_run == clock.now + timedelta(hours=12)
    assert sched.run_once() == 0, "not due again until the next slot"


def test_missed_runs_after_downtime_run_once_not_n_times(tmp_path, clock):
    job, calls = _counting_job(schedule=EveryHours(12))
    sched, _ = _scheduler(tmp_path, clock, job)
    sched.plan()

    # The runner is down for three days (six 12-hour slots), then starts again.
    clock.advance(days=3)
    job2, calls2 = _counting_job(schedule=EveryHours(12))
    sched2, store2 = _scheduler(tmp_path, clock, job2)
    sched2.plan()
    assert sched2.run_once() == 1
    assert sched2.run_once() == 0
    assert len(calls2) == 1
    assert store2.get("rescan").next_run == clock.now + timedelta(hours=12)


def test_missed_daily_run_runs_once_then_returns_to_its_slot(tmp_path, clock):
    job, calls = _counting_job(schedule=DailyAt(3, 30))
    sched, _ = _scheduler(tmp_path, clock, job)
    sched.plan()                                   # next: 06-02 03:30
    clock.now = datetime(2026, 6, 5, 9, 0, tzinfo=UTC)   # down over four slots
    job2, calls2 = _counting_job(schedule=DailyAt(3, 30))
    sched2, store2 = _scheduler(tmp_path, clock, job2)
    sched2.plan()
    assert sched2.run_once() == 1 and sched2.run_once() == 0
    assert store2.get("rescan").next_run == datetime(2026, 6, 6, 3, 30, tzinfo=UTC)


def test_catch_up_off_skips_the_missed_slot(tmp_path, clock):
    job, _ = _counting_job(schedule=DailyAt(3, 30))
    sched, _ = _scheduler(tmp_path, clock, job)
    sched.plan()
    clock.now = datetime(2026, 6, 5, 9, 0, tzinfo=UTC)
    job2, calls2 = _counting_job(schedule=DailyAt(3, 30), catch_up=False)
    sched2, store2 = _scheduler(tmp_path, clock, job2)
    sched2.plan()
    assert sched2.run_once() == 0 and calls2 == []
    assert store2.get("rescan").next_run == datetime(2026, 6, 6, 3, 30, tzinfo=UTC)


def test_changed_schedule_is_replanned_from_now_without_firing(tmp_path, clock):
    job, _ = _counting_job(schedule=EveryHours(12))
    sched, _ = _scheduler(tmp_path, clock, job)
    sched.plan()
    clock.advance(days=2)                          # old slot missed, but the schedule changed
    job2, calls2 = _counting_job(schedule=DailyAt(5, 0))
    sched2, store2 = _scheduler(tmp_path, clock, job2)
    sched2.plan()
    assert sched2.run_once() == 0 and calls2 == []
    assert store2.get("rescan").schedule == "daily@05:00"
    assert store2.get("rescan").next_run == datetime(2026, 6, 4, 5, 0, tzinfo=UTC)


def test_inactive_jobs_are_never_run(tmp_path, clock):
    off, off_calls = _counting_job("dispatch-batch", EveryHours(1), enabled=False)
    unscheduled, un_calls = _counting_job("other", None)
    sched, store = _scheduler(tmp_path, clock, off, unscheduled)
    sched.plan()
    clock.advance(days=1)
    assert sched.run_once() == 0
    assert off_calls == [] and un_calls == []
    assert store.get("dispatch-batch").next_run is None


def test_a_failing_job_is_recorded_and_replanned(tmp_path, clock):
    def boom(ctx):
        raise RuntimeError("disk on fire")

    sched, store = _scheduler(tmp_path, clock, Job("rescan", boom, EveryHours(1)))
    sched.plan()
    clock.advance(hours=1)
    assert sched.run_once() == 1
    st = store.get("rescan")
    assert st.last_status == "error" and "disk on fire" in st.last_message
    assert st.next_run == clock.now + timedelta(hours=1)


def test_cancelled_job_keeps_its_due_time_for_a_catch_up(tmp_path, clock):
    holder = {}

    def long_job(ctx: JobContext) -> JobResult:
        holder["sched"].stop()      # SIGTERM arrives mid-run
        ctx.check()
        return JobResult("ok")

    sched, store = _scheduler(tmp_path, clock, Job("rescan", long_job, EveryHours(1)))
    holder["sched"] = sched
    sched.plan()
    due = store.get("rescan").next_run
    clock.advance(hours=1)
    sched.run_once()
    st = store.get("rescan")
    assert st.last_status == "cancelled"
    assert st.next_run == due, "unchanged, so the next start runs it once"


def test_run_forever_sleeps_until_the_next_slot_and_stops(tmp_path, clock):
    job, calls = _counting_job(schedule=EveryHours(2))
    sleeps = []
    store = StateStore(tmp_path / "state.json").load()

    def fake_wait(seconds: float) -> bool:
        sleeps.append(seconds)
        clock.advance(seconds=seconds)
        if len(calls) >= 3:
            sched.stop()
        return sched.stopping

    sched = Scheduler(JobRegistry([job]), store, UTC, now=clock, max_sleep=3600, wait=fake_wait)
    sched.run_forever()
    assert len(calls) == 3
    assert all(0 <= s <= 3600 for s in sleeps)
    assert store.get("rescan").runs == 3


def test_state_survives_a_restart(tmp_path, clock):
    job, _ = _counting_job(schedule=EveryHours(6))
    sched, _ = _scheduler(tmp_path, clock, job)
    sched.plan()
    clock.advance(hours=6)
    sched.run_once()
    reloaded = StateStore(tmp_path / "state.json").load().get("rescan")
    assert reloaded.last_run == clock.now
    assert reloaded.last_status == "ok" and reloaded.runs == 1
    assert reloaded.next_run == clock.now + timedelta(hours=6)


def test_status_rows(tmp_path, clock):
    on, _ = _counting_job("rescan", DailyAt(3, 30))
    off, _ = _counting_job("dispatch-batch", DailyAt(4, 30), enabled=False)
    sched, _ = _scheduler(tmp_path, clock, on, off)
    sched.plan()
    rows = {r["job"]: r for r in sched.status()}
    assert rows["rescan"]["active"] and rows["rescan"]["next_run"].startswith("2026-06-02 03:30")
    assert not rows["dispatch-batch"]["active"] and rows["dispatch-batch"]["next_run"] == ""
