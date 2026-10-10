"""Editable schedules: validation, the stored value in front of the container's variable, and the scheduler
picking a change up while it runs (next-run times recomputed and logged at INFO)."""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from mangalist import store
from mangalist.headless.jobs import Job, JobContext, JobRegistry, JobResult, build_registry
from mangalist.headless.schedule import UTC, DailyAt, EveryHours, validate_schedule
from mangalist.headless.scheduler import Scheduler
from mangalist.headless.settings import (
    SCHEDULE_BY_JOB,
    SCHEDULES,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_STORED,
    HeadlessSettings,
    reset_schedule,
    resolve_schedule,
    resolve_schedules,
    save_schedule,
)
from mangalist.headless.state import StateStore


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


# --- validation -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("typed, stored", [
    ("daily@03:30", "daily@03:30"), ("3:30", "daily@03:30"), ("Daily 04:05", "daily@04:05"),
    ("every 12h", "every 12h"), ("12h", "every 12h"), ("every:1h", "every 1h"), ("every 0.5h", "every 0.5h"),
    ("  EVERY 6 hours ", "every 6h"), ("off", "off"), ("none", "off"), ("Disabled", "off"),
])
def test_validate_accepts_every_form_the_parser_does_and_canonicalises(typed, stored):
    assert validate_schedule(typed) == stored


@pytest.mark.parametrize("typed, message", [
    ("sometimes", "Not understood"), ("25:00", "Not understood"), ("daily@3", "Not understood"),
    ("every 1m", "Not understood"), ("every h", "Not understood"), ("every 0h", "Not understood"),
    ("", "Enter a time"), ("   ", "Enter a time"), ("every 0.1h", "too often"),
])
def test_validate_refuses_the_rest_with_a_plain_message(typed, message):
    with pytest.raises(ValueError) as exc:
        validate_schedule(typed)
    assert message in str(exc.value)
    assert "daily@03:30" in str(exc.value) or "Enter a time" in str(exc.value) or "too often" in str(exc.value)


# --- precedence -------------------------------------------------------------------------------------------------

def test_stored_beats_the_environment_which_beats_the_default(db):
    spec = SCHEDULE_BY_JOB["rescan"]
    assert resolve_schedule(spec, db, {}) == ("daily@03:30", SOURCE_DEFAULT)
    assert resolve_schedule(spec, db, {"MANGALIST_RESCAN_SCHEDULE": "daily@02:00"}) == ("daily@02:00", SOURCE_ENV)
    save_schedule(db, spec, "every 6h")
    assert resolve_schedule(spec, db, {"MANGALIST_RESCAN_SCHEDULE": "daily@02:00"}) == ("every 6h", SOURCE_STORED)
    reset_schedule(db, spec)
    assert resolve_schedule(spec, db, {"MANGALIST_RESCAN_SCHEDULE": "daily@02:00"}).source == SOURCE_ENV


def test_a_stored_off_is_a_choice_not_a_missing_value(db):
    save_schedule(db, SCHEDULE_BY_JOB["rescan"], "off")
    assert resolve_schedules(db, {})["rescan"] is None


def test_a_hand_edited_bad_stored_value_is_ignored_with_a_warning(db, caplog):
    db.set_setting("schedule_rescan", "whenever")
    with caplog.at_level(logging.WARNING):
        choice = resolve_schedule(SCHEDULE_BY_JOB["rescan"], db, {})
    assert choice.source == SOURCE_DEFAULT and "Ignoring the stored rescan schedule" in caplog.text


def test_an_unreadable_database_falls_back_to_the_environment(caplog):
    class Broken:
        def get_setting(self, *a, **k):
            raise OSError("gone")

    with caplog.at_level(logging.WARNING):
        choice = resolve_schedule(SCHEDULE_BY_JOB["rescan"], Broken(), {"MANGALIST_RESCAN_SCHEDULE": "daily@01:00"})
    assert choice == ("daily@01:00", SOURCE_ENV) and "Could not read" in caplog.text


def test_from_env_uses_the_stored_schedules_and_ignores_a_bad_variable_that_is_overridden(db):
    save_schedule(db, SCHEDULE_BY_JOB["rescan"], "every 8h")
    env = {"MANGALIST_RESCAN_SCHEDULE": "nonsense", "MANGALIST_MANGAPIXER_SYNC_SCHEDULE": "off"}
    s = HeadlessSettings.from_env(env, db=db)
    assert s.rescan_schedule == EveryHours(8) and s.mangapixer_schedule is None
    assert s.downloads_schedule == EveryHours(1) and s.dispatch_schedule == DailyAt(4, 30)
    with pytest.raises(ValueError, match="MANGALIST_RESCAN_SCHEDULE.*unrecognised schedule"):
        HeadlessSettings.from_env(env)                          # no stored value: the bad variable is refused


def test_every_schedule_has_its_own_setting_key():
    assert len({spec.key for spec in SCHEDULES}) == len(SCHEDULES) == 4
    assert {spec.job for spec in SCHEDULES} == {"rescan", "mangapixer-sync", "dispatch-batch", "downloads"}


# --- the scheduler picks a change up ----------------------------------------------------------------------------

def _job(name="rescan", schedule=DailyAt(3, 30), **kw):
    ran = []
    return Job(name, lambda ctx: (ran.append(1), JobResult("ok"))[1], schedule, **kw), ran


def _sched(tmp_path, clock, *jobs, **kw):
    state = StateStore(tmp_path / "state.json").load()
    return Scheduler(JobRegistry(jobs), state, UTC, now=clock, **kw), state


def test_a_changed_schedule_is_replanned_from_now_and_logged_at_info(tmp_path, clock, caplog):
    job, ran = _job()
    sched, state = _sched(tmp_path, clock, job)
    sched.plan()
    assert state.get("rescan").next_run == datetime(2026, 6, 2, 3, 30, tzinfo=UTC)
    with caplog.at_level(logging.INFO, logger="mangalist.headless.scheduler"):
        assert sched.apply_schedules({"rescan": EveryHours(6)})
    st = state.get("rescan")
    assert st.next_run == datetime(2026, 6, 1, 18, 0, tzinfo=UTC), "the first slot after now, never at once"
    assert st.schedule == "every 6h" and job.schedule == EveryHours(6)
    assert "rescan: schedule changed (daily@03:30 -> every 6h); next run 2026-06-01 18:00" in caplog.text
    assert [r.levelno for r in caplog.records if "schedule changed" in r.message] == [logging.INFO]
    assert sched.run_once() == 0 and ran == []
    reloaded = StateStore(tmp_path / "state.json").load()
    assert reloaded.get("rescan").next_run == st.next_run, "persisted"


def test_an_unchanged_schedule_changes_nothing(tmp_path, clock, caplog):
    job, _ = _job()
    sched, state = _sched(tmp_path, clock, job)
    sched.plan()
    before = state.get("rescan").next_run
    clock.advance(hours=1)
    assert not sched.apply_schedules({"rescan": DailyAt(3, 30)})
    assert state.get("rescan").next_run == before


def test_a_job_switched_off_stops_and_switched_on_again_is_planned_afresh(tmp_path, clock, caplog):
    job, _ = _job()
    sched, state = _sched(tmp_path, clock, job)
    sched.plan()
    with caplog.at_level(logging.INFO, logger="mangalist.headless.scheduler"):
        sched.apply_schedules({"rescan": None})
    assert not job.active and state.get("rescan").next_run is None and sched.due_jobs() == []
    assert "rescan: schedule changed (daily@03:30 -> off); not scheduled" in caplog.text
    clock.advance(days=5)
    sched.apply_schedules({"rescan": EveryHours(12)})
    assert job.active and state.get("rescan").next_run == clock.now + EveryHours(12).interval


def test_a_gated_job_stays_off_whatever_its_schedule(tmp_path, clock):
    job, _ = _job("downloads", None, enabled=False, gate=False)
    sched, state = _sched(tmp_path, clock, job)
    sched.apply_schedules({"downloads": EveryHours(1)})
    assert not job.active and job.schedule == EveryHours(1), "downloads are opt-in: the schedule alone does not run it"


def test_run_forever_re_reads_the_schedules_every_loop(tmp_path, clock, db):
    job, ran = _job()
    waits = []
    holder = {}

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 1:
            save_schedule(db, SCHEDULE_BY_JOB["rescan"], "every 2h")     # the owner edits it in Settings
        elif len(waits) == 2:
            clock.advance(hours=2, minutes=1)
        else:
            holder["sched"].stop()
        return holder["sched"].stopping

    sched, state = _sched(tmp_path, clock, job, wait=wait,
                          reload_schedules=lambda: resolve_schedules(db, {}))
    holder["sched"] = sched
    sched.run_forever()
    # Planned with the stored default (daily 03:30), edited to every 2h on the first wait, run when 2 h had passed.
    assert len(ran) == 1 and job.schedule == EveryHours(2)
    assert state.get("rescan").next_run == clock.now + EveryHours(2).interval


def test_a_failing_reload_keeps_the_schedules_and_is_reported_once(tmp_path, clock, caplog):
    job, _ = _job()
    calls = []

    def reload():
        calls.append(1)
        raise OSError("database is locked")

    ticks = []
    sched, _ = _sched(tmp_path, clock, job, reload_schedules=reload,
                      on_tick=lambda: ticks.append(1) or (_ for _ in ()).throw(RuntimeError("log")))
    with caplog.at_level(logging.WARNING, logger="mangalist.headless.scheduler"):
        for _ in range(3):
            assert sched.refresh() is False
    assert len(calls) == 3 and len(ticks) == 3 and job.schedule == DailyAt(3, 30)
    warned = [r.message for r in caplog.records]
    assert sum("Could not re-read the schedules" in m for m in warned) == 1
    assert sum("Could not refresh the logging settings" in m for m in warned) == 1


def test_build_registry_marks_the_downloads_jobs_as_gated():
    settings = HeadlessSettings.from_env({"MANGALIST_DOWNLOADS": "0"})
    registry = build_registry(settings, provider=type("P", (), {"roots": lambda self: []})())
    assert [j.gate for j in registry.all()] == [True, True, False, False]


# --- the Automation section's helpers (no Qt) -------------------------------------------------------------------

def test_schedule_entries_say_where_each_value_comes_from(db):
    from mangalist.gui import download_rules as rules

    env = {"MANGALIST_MANGAPIXER_SYNC_SCHEDULE": "daily@02:00", "MANGALIST_DOWNLOADS_SCHEDULE": "banana"}
    rules.save_schedule_text(db, "rescan", "12h")
    entries = {e.job: e for e in rules.schedule_entries(db, env)}
    assert list(entries) == ["rescan", "mangapixer-sync", "downloads"], "the dispatch stub has no row"
    assert (entries["rescan"].edit_text, entries["rescan"].when, entries["rescan"].source) == \
        ("every 12h", "every 12 hours", "stored")
    assert (entries["mangapixer-sync"].edit_text, entries["mangapixer-sync"].source) == ("daily@02:00", "env")
    assert (entries["downloads"].edit_text, entries["downloads"].when, entries["downloads"].valid) == \
        ("banana", "banana (not understood)", False)
    rules.reset_schedule_text(db, "rescan")
    assert {e.job: e.source for e in rules.schedule_entries(db, {})}["rescan"] == "default"


def test_schedule_rows_without_a_database_are_the_containers_values():
    from mangalist.gui import download_rules as rules

    assert [(w, e) for w, _when, e in rules.schedule_rows({"MANGALIST_RESCAN_SCHEDULE": "off"})] == [
        ("Rescan the library", True), ("Sync with MangaPixer", False), ("File finished downloads", False)]
