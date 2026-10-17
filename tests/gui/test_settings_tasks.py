"""Settings > Scheduled tasks (offscreen Qt, a real database in a temporary folder, made-up job records): the list of
tasks, the schedule choices (6 h / 12 h / Daily for the full re-read; Off / Daily / Weekly / Monthly for the clean-up),
last and next run, Run now, and Clear finished now."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTime  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from mangalist import store  # noqa: E402
from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.gui.settings_tasks import ScheduledTasksPage  # noqa: E402
from mangalist.headless import tasks as headless_tasks  # noqa: E402
from mangalist.headless.state import JobState  # noqa: E402

from .conftest import qapp  # noqa: E402,F401

NOW = datetime(2026, 10, 15, 14, 5, tzinfo=timezone.utc)
UTC_ENV = {"TZ": "UTC"}


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


def make(db, *, env=None, states=None, runner=True, downloads=True, answer=True, **kw):
    asked = []

    def confirm(text):
        asked.append(text)
        return answer

    page = ScheduledTasksPage(db, env={**UTC_ENV, **(env or {})}, now=lambda: NOW, states=lambda: dict(states or {}),
                              runner_running=lambda: runner, downloads_on=lambda: downloads, confirm=confirm, **kw)
    page.asked = asked
    return page


def choices(page, job):
    combo = page.schedule_modes[job]
    return [combo.itemText(i) for i in range(combo.count())]


def pick(page, job, label):
    combo = page.schedule_modes[job]
    combo.setCurrentIndex(combo.findText(label))


def text_of(widget):
    return "\n".join(w.text() + w.toolTip() for w in widget.findChildren(QLabel))


def state(**kw):
    return JobState(**kw)


# --- the list --------------------------------------------------------------------------------------------------------

def test_one_card_per_task_in_order_with_what_it_does(qapp, db):
    page = make(db)
    assert list(page.cards) == ["mangapixer-sync", "rescan", "downloads", "dispatch-batch", "clean-records"]
    text = text_of(page)
    for title in ("Sync with MangaPixer", "Full re-read of the library", "Check downloads",
                  "Find missing volumes and chapters", "Clear finished download records"):
        assert title in text
    assert "Times are in the container's time zone: UTC (UTC+00:00), now 14:05." in text
    assert "The background runner is running." in text


def test_the_full_re_read_offers_6_hours_12_hours_and_daily(qapp, db):
    page = make(db, env={"MANGALIST_RESCAN_SCHEDULE": "daily@02:15"})
    assert choices(page, "rescan") == ["Every 6 hours", "Every 12 hours", "Daily"]          # daily at most; never off
    assert page.schedule_modes["rescan"].currentText() == "Daily"
    assert page.schedule_times["rescan"].time().toString("HH:mm") == "02:15"
    assert not page.schedule_times["rescan"].isHidden() and page.schedule_days["rescan"].isHidden()
    assert page.schedule_reading["rescan"].text() == "daily 02:15"
    assert all(btn.isHidden() for btn in page.schedule_reset.values()), "nothing stored yet: nothing to reset"


def test_a_choice_is_stored_and_beats_the_container(qapp, db):
    page = make(db, env={"MANGALIST_RESCAN_SCHEDULE": "daily@02:15"})
    at = page.schedule_times["rescan"]
    at.setTime(QTime(4, 5))
    at.editingFinished.emit()
    assert db.get_setting("schedule_rescan") == "daily@04:05"
    assert page.schedule_reading["rescan"].text() == "daily 04:05"
    assert not page.schedule_reset["rescan"].isHidden() and "saved" in page.schedule_status.text()
    pick(page, "rescan", "Every 6 hours")                       # the owner's own setting today
    assert db.get_setting("schedule_rescan") == "every 6h" and page.schedule_times["rescan"].isHidden()
    page.schedule_reset["rescan"].click()                       # "Use the container's value"
    assert db.get_setting("schedule_rescan") is None
    assert page.schedule_modes["rescan"].currentText() == "Daily" and page.schedule_reset["rescan"].isHidden()


def test_a_value_stored_before_is_kept_as_its_own_choice(qapp, db):
    db.set_setting("schedule_rescan", "weekly@sun 03:30")
    page = make(db)
    assert choices(page, "rescan") == ["Every 6 hours", "Every 12 hours", "Daily", "Weekly"]
    assert page.schedule_modes["rescan"].currentText() == "Weekly" and not page.schedule_days["rescan"].isHidden()
    assert db.get_setting("schedule_rescan") == "weekly@sun 03:30"      # showing it changes nothing


def test_a_bad_container_value_is_shown_flagged_not_hidden(qapp, db):
    page = make(db, env={"MANGALIST_RESCAN_SCHEDULE": "whenever"})
    assert page.schedule_modes["rescan"].currentData() == "whenever"
    assert page.schedule_reading["rescan"].text() == "whenever (not understood)"
    assert page.schedule_reading["rescan"].property("tone") == "bad"
    pick(page, "rescan", "Daily")
    assert db.get_setting("schedule_rescan") == "daily@03:30" and page.schedule_reading["rescan"].property("tone") == ""


def test_the_clean_up_is_weekly_until_another_period_or_off_is_chosen(qapp, db):
    page = make(db)
    job = "clean-records"
    assert choices(page, job) == ["Off", "Daily", "Weekly", "Monthly"]
    # A fresh install clears records older than a week, weekly (owner, 2026-10-16: "Weekly"); nothing is stored for it.
    assert page.schedule_modes[job].currentText() == "Weekly" and not page.schedule_times[job].isHidden()
    assert db.get_setting("schedule_clean_records") is None
    assert page.schedule_reading[job].text() == "every Sunday 05:30 (records older than a week)"
    pick(page, job, "Off")
    assert db.get_setting("schedule_clean_records") == "off" and page.schedule_reading[job].text() == "off"
    pick(page, job, "Weekly")                                   # starts after the rescan and the find pass
    assert db.get_setting("schedule_clean_records") == "weekly@sun 05:30"
    pick(page, job, "Monthly")
    assert db.get_setting("schedule_clean_records") == "monthly@1 05:30"
    assert not page.schedule_monthdays[job].isHidden() and page.schedule_days[job].isHidden()
    page.schedule_monthdays[job].setCurrentIndex(page.schedule_monthdays[job].findData(15))
    assert db.get_setting("schedule_clean_records") == "monthly@15 05:30"
    assert page.schedule_reading[job].text() == "the 15th of each month 05:30 (records older than a month)"
    pick(page, job, "Daily")
    assert db.get_setting("schedule_clean_records") == "daily@05:30" and page.schedule_monthdays[job].isHidden()
    pick(page, job, "Off")
    assert db.get_setting("schedule_clean_records") == "off"


# --- last run, next run, status ----------------------------------------------------------------------------------------

def test_last_and_next_run_come_from_the_runners_records(qapp, db):
    ran = NOW - timedelta(hours=2)
    page = make(db, states={"rescan": state(last_run=ran, last_finished=ran + timedelta(seconds=25), last_status="ok",
                                            last_message="2 of 2 roots scanned", schedule="daily@03:30",
                                            next_run=datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc))},
                env={"MANGALIST_RESCAN_SCHEDULE": "daily@03:30"})
    assert page.last_labels["rescan"].text() == "Last run: 12:05 - ok: 2 of 2 roots scanned (took 25 s)"
    assert page.next_labels["rescan"].text() == "Next run: Oct 16 03:30"
    assert page.badges["rescan"].text() == "Scheduled"
    assert page.last_labels["downloads"].text() == "Last run: Never run yet."


def test_a_schedule_the_runner_has_not_picked_up_yet_says_so(qapp, db):
    page = make(db, states={"rescan": state(schedule="every 6h", next_run=NOW + timedelta(hours=1))},
                env={"MANGALIST_RESCAN_SCHEDULE": "daily@03:30"})
    assert page.next_labels["rescan"].text() == "Next run: Oct 16 03:30 (the runner picks the new schedule up within a minute)"


def test_a_failed_run_is_red_and_a_running_one_says_so(qapp, db):
    page = make(db, states={"downloads": state(last_run=NOW - timedelta(minutes=5), last_status="error",
                                               last_message="qBittorrent could not be reached"),
                            "rescan": state(running_since=NOW - timedelta(seconds=30), last_run=NOW)})
    assert page.badges["downloads"].text() == "Last run failed" and page.last_labels["downloads"].property("tone") == "bad"
    assert page.badges["rescan"].text() == "Running now" and not page.run_buttons["rescan"].isEnabled()


def test_without_downloads_their_tasks_are_not_available(qapp, db):
    page = make(db, downloads=False)
    for job in ("downloads", "dispatch-batch"):
        assert page.badges[job].text() == "Downloads off" and not page.run_buttons[job].isEnabled()
        assert page.next_labels[job].text().startswith("Next run: not available")
    assert page.run_buttons["rescan"].isEnabled()


def test_without_a_runner_nothing_runs_and_run_now_waits(qapp, db):
    page = make(db, runner=False)
    assert "No background runner is running here" in page.runner_label.text()
    assert not any(btn.isEnabled() for btn in page.run_buttons.values())
    assert page.next_labels["rescan"].text() == "Next run: waiting for a background runner"


# --- Run now ---------------------------------------------------------------------------------------------------------

def test_run_now_leaves_a_request_for_the_runner_and_waits_for_it(qapp, db):
    page = make(db)
    assert page.run_buttons["rescan"].isEnabled()
    page.run_buttons["rescan"].click()
    assert headless_tasks.pending_requests(db) == ["rescan"]
    assert page.badges["rescan"].text() == "Run requested" and not page.run_buttons["rescan"].isEnabled()
    assert "asked the runner" in page.schedule_status.text()
    assert headless_tasks.take_requests(db) == ["rescan"]       # what the runner does
    page.refresh_status()
    assert page.run_buttons["rescan"].isEnabled()


def test_run_now_of_an_off_clean_up_is_not_offered(qapp, db):
    page = make(db)
    assert page.run_buttons["clean-records"].isEnabled()        # Weekly until chosen
    pick(page, "clean-records", "Off")
    assert not page.run_buttons["clean-records"].isEnabled()
    assert "Clear finished now" in page.run_buttons["clean-records"].toolTip()
    pick(page, "clean-records", "Daily")
    assert page.run_buttons["clean-records"].isEnabled()


# --- Clear finished now ----------------------------------------------------------------------------------------------

def add_record(db, status, age_days, n, series_id):
    when = (NOW - timedelta(days=age_days)).replace(microsecond=0).isoformat()
    with db.connect() as con:
        rid = con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status, external_ref)"
                          " VALUES (?, ?, ?, '{}', 'qbittorrent', ?, ?)", (when, when, series_id, status, f"{n:040x}")).lastrowid
        if status == S.REMOVED:
            con.execute("INSERT INTO replacements (download_id, status, created_at, updated_at) VALUES (?, 'nothing', 't', 't')",
                        (rid,))
    return rid


@pytest.fixture
def records(db, tmp_path):
    root = db.add_root(str(tmp_path / "lib"), "Lib")
    db.record_scan(root.id, tmp_path / "lib", [store.SeriesSeen("S1", None, 0)])
    sid = db.list_series()[0].id
    return {"old": add_record(db, S.REMOVED, 40, 1, sid), "week": add_record(db, S.CANCELLED, 10, 2, sid),
            "new": add_record(db, S.REMOVED, 1, 3, sid), "filed": add_record(db, S.FILED, 90, 4, sid),
            "sent": add_record(db, S.SENT, 0, 5, sid)}


def ids(db):
    with db.connect() as con:
        return {r[0] for r in con.execute("SELECT id FROM ledger")}


def test_the_page_counts_finished_records_and_the_menu_says_how_many_each_age_clears(qapp, db, records):
    page = make(db)
    assert page.finished_label.text().startswith("3 finished download records")
    page._fill_clear_menu()
    labels = [a.text() for a in page.clear_menu.actions()]
    assert labels == ["Older than a day (3)", "Older than a week (2)", "Older than a month (1)",
                      "All finished records (3)"]


def test_clear_finished_now_asks_clears_only_the_old_ones_and_tells_the_download_tab(qapp, db, records):
    page = make(db)
    changed = []
    page.downloads_changed.connect(lambda: changed.append(1))
    page.clear_finished_now(30, "Older than a month")
    assert page.asked and "Clear 1 finished download record (older than a month)?" in page.asked[0]
    assert ids(db) == {records["week"], records["new"], records["filed"], records["sent"]} and changed == [1]
    assert page.clear_status.text() == "Cleared 1 of 3 finished records."


def test_saying_no_clears_nothing(qapp, db, records):
    page = make(db, answer=False)
    page.clear_finished_now(0, "All finished records")
    assert len(ids(db)) == 5 and not page.clear_status.text()


def test_nothing_to_clear_is_said_without_asking(qapp, db, records):
    page = make(db)
    page.clear_finished_now(400, "")
    assert page.asked == [] and page.clear_status.text() == "Nothing to clear."
