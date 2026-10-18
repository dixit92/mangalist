"""The ``chapter-check`` job (owner, 2026-10-18: "Scheduled newer-chapter check - every 6-12 h ... Auto sends, Notify
waits"): every 6 hours by default and only with downloads on, turned off or changed in Settings > Scheduled tasks like
the others, its summary as the job's message, skipped while a find pass runs, never holding the find pass's nyaa lock.
Fakes only, made-up names."""

from __future__ import annotations

import logging

import pytest

from mangalist import store
from mangalist.downloads import finder
from mangalist.gui import task_rules
from mangalist.headless import tasks
from mangalist.headless.chapter_check_job import JOB_NAME, make_chapter_check
from mangalist.headless.jobs import Cancelled, JobContext, build_registry
from mangalist.headless.schedule import UTC, EveryHours
from mangalist.headless.settings import SCHEDULE_BY_JOB, HeadlessSettings, resolve_schedules, save_schedule
from mangalist.store.findings import FindingStore
from mangalist.wanted import library_states

from ..downloads.fakes import data, scan
from ..downloads.finder_fakes import wire
from ..downloads.suwayomi_fakes import MANGA, MANGADEX, chapter


class NoRoots:
    def roots(self):
        return []


@pytest.fixture
def w(tmp_path):
    store.reset_stores()
    db = store.get_store()
    root = tmp_path / "library" / "Manga"
    folder = root / "Series C"
    folder.mkdir(parents=True)
    for n in (3, 4):
        (folder / f"{n:04d} [Ch. {n:04d} - Title {n} [Alpha Scans]].cbz").write_bytes(data(f"c{n}"))
    r = db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(r.id, "Series C").id
    w = wire(db, (sid, folder), tmp_path, suwayomi=True)
    w.suwayomi.chapter_list = [chapter(101, "3"), chapter(102, "4"), chapter(103, "5")]
    w.backend.suwayomi.set_series_choice(sid, source_id=MANGADEX.id, manga_id=MANGA.id, manga_title=MANGA.title,
                                         how="confirmed")
    return w


def job(w, enabled=True, **kw):
    return make_chapter_check(enabled, open_db=lambda: w.backend.db, backend_factory=lambda db: w.backend, pause=0,
                              **kw)


# --- the schedule ------------------------------------------------------------------------------------------------

def test_the_task_is_every_6_hours_by_default_and_only_with_downloads_on():
    off = HeadlessSettings.from_env({})
    assert off.chapter_check_schedule == EveryHours(6)
    assert not build_registry(off, NoRoots()).get(JOB_NAME).active               # downloads are off
    on = HeadlessSettings.from_env({"MANGALIST_DOWNLOADS": "1"})
    reg = build_registry(on, NoRoots())
    assert reg.get(JOB_NAME).active and reg.get(JOB_NAME).schedule == EveryHours(6)
    assert reg.names().index(JOB_NAME) == reg.names().index("downloads") + 1      # after the find pass and filing
    assert SCHEDULE_BY_JOB[JOB_NAME].label == "Check for new chapters"
    assert SCHEDULE_BY_JOB[JOB_NAME].key == "schedule_chapter_check"
    container = HeadlessSettings.from_env({"MANGALIST_DOWNLOADS": "1", "MANGALIST_CHAPTER_CHECK_SCHEDULE": "every 12h"})
    assert container.chapter_check_schedule == EveryHours(12)


def test_the_owner_turns_it_off_or_changes_it_in_settings(w):
    db = w.backend.db
    save_schedule(db, SCHEDULE_BY_JOB[JOB_NAME], "off")
    assert resolve_schedules(db, {})[JOB_NAME] is None
    s = HeadlessSettings.from_env({"MANGALIST_DOWNLOADS": "1"}, db=db)
    reg = build_registry(s, NoRoots())
    assert s.chapter_check_schedule is None and not reg.get(JOB_NAME).active
    save_schedule(db, SCHEDULE_BY_JOB[JOB_NAME], "every 12h")
    assert resolve_schedules(db, {})[JOB_NAME] == EveryHours(12)
    reg.get(JOB_NAME).set_schedule(resolve_schedules(db, {})[JOB_NAME])          # what the runner's tick does
    assert reg.get(JOB_NAME).active


def test_settings_lists_it_with_the_other_tasks(w):
    from datetime import datetime

    db = w.backend.db
    assert [t.job for t in tasks.TASKS].index(JOB_NAME) == [t.job for t in tasks.TASKS].index("dispatch-batch") + 1
    now = datetime(2026, 10, 18, 18, 5, tzinfo=UTC)
    rows = {r.job: r for r in task_rules.build_rows(db, {}, {}, tz=UTC, now=now, runner=True, downloads_on=True)}
    row = rows[JOB_NAME]
    assert row.info.title == "Check for new chapters" and row.entry.when == "every 6 hours"
    assert row.badge == "Scheduled" and row.can_run
    assert task_rules.build_rows(db, {}, {}, tz=UTC, now=now, runner=True, downloads_on=False)[
        [t.job for t in tasks.TASKS].index(JOB_NAME)].badge == "Downloads off"
    save_schedule(db, SCHEDULE_BY_JOB[JOB_NAME], "off")
    off = {r.job: r for r in task_rules.build_rows(db, {}, {}, tz=UTC, now=now, runner=True, downloads_on=True)}
    assert off[JOB_NAME].badge == "Off" and off[JOB_NAME].next == "not scheduled"
    tasks.request_run(db, JOB_NAME)                                               # Run now
    assert tasks.take_requests(db) == [JOB_NAME]


# --- running -----------------------------------------------------------------------------------------------------

def test_the_job_runs_a_check_and_reports_its_summary(w, caplog):
    caplog.set_level(logging.INFO)
    FindingStore(w.backend.db).set_mode([w.series_id], "auto")
    result = job(w)(JobContext())
    assert result.status == "ok"
    assert result.message == ("1 series checked (1 fetched, 0 unchanged on MangaDex), 1 with new chapters: 1 sent, "
                              "0 waiting; MangaDex latest uploads: from MangaPixer 0, own requests 0")
    assert result.extra["sent"] == 1 and result.extra["series_checked"] == 1
    assert "Chapter check: Series C: ch. 5 from MangaDex (EN) (Alpha Scans) new after ch 4: Sent ch 5" in caplog.text
    assert "Chapter check: 1 series checked (1 fetched, 0 unchanged on MangaDex), 1 with new chapters" in caplog.text


def test_the_job_does_not_hold_the_nyaa_lock(w):
    seen = []

    def views(db):
        seen.append(finder._PASS.acquire(blocking=False))       # a find pass could start now
        finder._PASS.release()
        return library_states(db)

    assert job(w, views=views)(JobContext()).status == "ok" and seen == [True]


def test_a_running_find_pass_skips_the_check(w):
    with finder._PASS:
        result = job(w)(JobContext())
    assert result.status == "skipped" and "a find pass is running" in result.message
    assert not [c for c in w.suwayomi.calls if c[0] == "chapters"]


def test_downloads_off_checks_nothing(w):
    result = job(w, enabled=False)(JobContext())
    assert result.status == "skipped" and w.suwayomi.calls == []


def test_suwayomi_not_set_up_is_skipped_not_failed(w):
    w.backend.suwayomi.forget_connection()
    result = job(w)(JobContext())
    assert result.status == "skipped" and result.message == "nothing checked: Suwayomi is not set up"


def test_a_shutdown_during_the_check_cancels_the_job(w):
    stop = []

    def views(db):
        stop.append(True)                                       # SIGTERM while the series are being read
        return library_states(db)

    with pytest.raises(Cancelled):
        job(w, views=views)(JobContext(lambda: bool(stop)))
    assert not [c for c in w.suwayomi.calls if c[0] == "chapters"]
