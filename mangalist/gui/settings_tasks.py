"""Settings > Scheduled tasks (owner, 2026-10-15): one list of what MangaList does on its own - each task with what it
does, when it runs (editable), its last run, its next run and "Run now".

The pattern is MangaPixer's Scheduled jobs section (one list of cards: name, what it does, its settings, last and next
run, the server's clock and time zone on top). The tasks run in the container's headless runner, another process, so
this page only *edits* the schedules (stored in the database; the runner re-reads them within a minute), *reads* the
runner's records, and *asks* for a run (a request in the database that the runner takes within seconds). The one thing
it does itself is **Clear finished now**: clearing finished download records is a quick database step that is safe from
any process (:meth:`~mangalist.store.downloads.DownloadLedger.clear_finished`).

The schedule controls moved here from Settings > Automation; the full re-read offers 6 hours, 12 hours or Daily ("Daily
at most"), the clean-up of finished records Off, Daily, Weekly or Monthly. Every change is stored as soon as it is made
(there is no Save).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Mapping, Optional

from PySide6.QtCore import QTime, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QMenu,
    QMessageBox,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..headless import tasks as headless_tasks
from ..headless.records_job import PERIOD_DAYS
from ..headless.settings import SOURCE_STORED, local_timezone
from ..headless.state import JobState
from .download_rules import (
    CHOICE_DAILY,
    CHOICE_MONTHLY,
    CHOICE_WEEKLY,
    DAY_NAMES,
    ScheduleChoice,
    choice_of,
    reset_schedule_text,
    save_schedule_text,
    schedule_choices,
    schedule_entries,
)
from .download_style import set_prop
from .download_widgets import button, card, hbox, label, pill
from .settings_common import SectionPage
from .task_rules import TaskRow, build_rows, clock_text

SECTION_TASKS = "tasks"

TASKS_LEAD = "What MangaList does on its own, and when. Change a time here; the container's runner uses it within a minute."
SCHEDULE_NOTE = ("These tasks run in MangaList's background runner in the Docker / Unraid container, in the container's "
                 "time zone; the desktop app on its own runs nothing on a timer. Run now asks that runner, which starts "
                 "the task within a few seconds and leaves its schedule as it is.")
RUNNER_UP = "The background runner is running."
RUNNER_DOWN = ("No background runner is running here, so nothing below runs on its own and Run now waits. (In the "
               "container it starts with MangaList.)")
CLEAN_KEEPS = ("Cleared: the Download tab's Show finished records (removed or cancelled) older than the period - a day, "
               "a week or a month. Never cleared: anything queued, sent or downloaded; filed downloads still seeding; "
               "failed downloads; the newest record; and replaced chapters still waiting for you. Your volumes and "
               "chapters are not touched, and a cleared release is remembered so Auto never sends it twice.")
CLEAR_NOW_AGES = (("Older than a day", PERIOD_DAYS["daily"]), ("Older than a week", PERIOD_DAYS["weekly"]),
                  ("Older than a month", PERIOD_DAYS["monthly"]), ("All finished records", 0.0))
REFRESH_MS = 5000


def _ask_yes(parent: QWidget, text: str) -> bool:
    return QMessageBox.question(parent, "Clear finished records", text) == QMessageBox.StandardButton.Yes


class ScheduledTasksPage(SectionPage):
    def __init__(self, db, parent: Optional[QWidget] = None, env: Optional[Mapping[str, str]] = None, *,
                 now: Optional[Callable[[], datetime]] = None,
                 states: Optional[Callable[[], Mapping[str, JobState]]] = None,
                 runner_running: Optional[Callable[[], bool]] = None,
                 downloads_on: Optional[Callable[[], bool]] = None,
                 confirm: Optional[Callable[[str], bool]] = None):
        super().__init__("Scheduled tasks", TASKS_LEAD, parent)
        self._db = db
        self._env = env
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._states = states or (lambda: headless_tasks.read_states())
        self._runner = runner_running or (lambda: headless_tasks.runner_running())
        self._downloads_on = downloads_on or _downloads_on
        self.confirm = confirm or (lambda text: _ask_yes(self, text))       # replaceable in tests (answers for the owner)
        self._loading = True
        self._tz = local_timezone(env)

        self.clock_label = label("", "muted", wrap=True)
        self.runner_label = label("", wrap=True)
        self.body.addWidget(self.clock_label)
        self.body.addWidget(self.runner_label)

        self.schedule_modes: Dict[str, QComboBox] = {}
        self.schedule_days: Dict[str, QComboBox] = {}
        self.schedule_monthdays: Dict[str, QComboBox] = {}
        self.schedule_times: Dict[str, QTimeEdit] = {}
        self.schedule_reading = {}
        self.schedule_reset: Dict[str, QPushButton] = {}
        self.schedule_source = {}
        self.cards: Dict[str, QFrame] = {}
        self.badges = {}
        self.last_labels = {}
        self.next_labels = {}
        self.run_buttons: Dict[str, QPushButton] = {}
        for task in headless_tasks.TASKS:
            self._build_card(task)
        self.schedule_status = label("", wrap=True)
        self.body.addWidget(self.schedule_status)
        self.body.addWidget(label(SCHEDULE_NOTE, "muted", wrap=True))

        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self._tick)
        self.refresh()
        self._loading = False

    # --- the cards -----------------------------------------------------------------------------------------------

    def _build_card(self, task: headless_tasks.TaskInfo) -> None:
        job = task.job
        box = card("true")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(20, 14, 20, 14)
        bv.setSpacing(6)
        badge = pill("", "muted")
        run = button("Run now", tip="Ask the background runner to run this now")
        run.setAccessibleName(f"Run now: {task.title}")
        run.clicked.connect(lambda _=False, j=job: self._run_now(j))
        bv.addLayout(hbox(label(task.title, "name"), badge, None, run, spacing=12))
        bv.addWidget(label(task.what, wrap=True))

        mode = QComboBox()
        mode.setAccessibleName(task.title)
        mode.setFixedWidth(160)
        day = QComboBox()
        day.setAccessibleName(f"{task.title}: day")
        day.setFixedWidth(130)
        for i, name in enumerate(DAY_NAMES):
            day.addItem(name, i)
        monthday = QComboBox()
        monthday.setAccessibleName(f"{task.title}: day of the month")
        monthday.setFixedWidth(90)
        for n in range(1, 29):
            monthday.addItem(f"Day {n}", n)
        at = QTimeEdit()
        at.setDisplayFormat("HH:mm")
        at.setAccessibleName(f"{task.title}: time")
        reset = button("Use the container's value", link=True,
                       tip="Forget the time set here; the container's own setting (or the default) applies again")
        source = label("", "muted", wrap=True)
        reading = label("", "mono", wrap=True)
        bv.addLayout(hbox(mode, day, monthday, at, reading, source, reset, None, spacing=10))
        last, nxt = label("", wrap=True), label("", wrap=True)
        bv.addWidget(last)
        bv.addWidget(nxt)
        if job == "clean-records":
            self._build_clean_extras(bv)
        self.body.addWidget(box)

        for store_, widget in ((self.schedule_modes, mode), (self.schedule_days, day),
                               (self.schedule_monthdays, monthday), (self.schedule_times, at),
                               (self.schedule_reading, reading), (self.schedule_reset, reset),
                               (self.schedule_source, source), (self.cards, box), (self.badges, badge),
                               (self.last_labels, last), (self.next_labels, nxt), (self.run_buttons, run)):
            store_[job] = widget
        for signal in (mode.currentIndexChanged, day.currentIndexChanged, monthday.currentIndexChanged):
            signal.connect(lambda _i, j=job: self._schedule_edited(j))
        at.editingFinished.connect(lambda j=job: self._schedule_edited(j))
        reset.clicked.connect(lambda _=False, j=job: self._schedule_reset(j))

    def _build_clean_extras(self, bv: QVBoxLayout) -> None:
        self.finished_label = label("", wrap=True)
        self.btn_clear = button("Clear finished now", tip="Clear the finished download records older than the age you "
                                                          "pick (nothing in progress, filed or failed is touched)")
        self.clear_menu = QMenu(self.btn_clear)
        self.clear_menu.aboutToShow.connect(self._fill_clear_menu)
        self.btn_clear.setMenu(self.clear_menu)
        self.clear_status = label("", wrap=True)
        bv.addWidget(self.finished_label)
        bv.addLayout(hbox(self.btn_clear, None))
        bv.addWidget(self.clear_status)
        bv.addWidget(label(CLEAN_KEEPS, "muted", wrap=True))

    # --- showing --------------------------------------------------------------------------------------------------

    def refresh(self) -> None:
        self._show_schedules()
        self.refresh_status()

    def on_show(self) -> None:
        self.refresh()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _tick(self) -> None:
        if self.isVisible():
            self.refresh_status()

    def rows(self) -> List[TaskRow]:
        runner = bool(self._runner())
        try:
            requests = headless_tasks.pending_requests(self._db)
        except Exception:  # noqa: BLE001 - the page still shows the schedules
            requests = []
        return build_rows(self._db, self._env, self._states(), tz=self._tz, now=self._now(), runner=runner,
                          downloads_on=bool(self._downloads_on()), requests=requests)

    def refresh_status(self) -> None:
        """The clock, the runner, and each card's badge, last run, next run and Run now - not the schedule controls
        (the owner may be in the middle of changing one)."""
        runner = bool(self._runner())
        self.clock_label.setText(clock_text(self._tz, self._now()))
        self.runner_label.setText(RUNNER_UP if runner else RUNNER_DOWN)
        set_prop(self.runner_label, "tone", "" if runner else "warn")
        for row in self.rows():
            job = row.job
            self.badges[job].setText(row.badge)
            set_prop(self.badges[job], "badge", row.badge_kind)
            self.last_labels[job].setText(f"Last run: {row.last}")
            set_prop(self.last_labels[job], "tone", row.last_kind)
            self.next_labels[job].setText(f"Next run: {row.next}")
            self.run_buttons[job].setEnabled(row.can_run)
            self.run_buttons[job].setToolTip(row.run_tip)
        self._show_finished_count()

    def _show_schedules(self) -> None:
        was, self._loading = self._loading, True               # setting the controls is not an edit
        try:
            for entry in schedule_entries(self._db, self._env):
                job, now = entry.job, choice_of(entry.edit_text, entry.job)
                mode, day, at = self.schedule_modes[job], self.schedule_days[job], self.schedule_times[job]
                mode.clear()
                for value, text in schedule_choices(job, entry.edit_text):
                    mode.addItem(text, value)
                mode.setCurrentIndex(max(0, mode.findData(now.choice)))
                day.setCurrentIndex(now.weekday)
                self.schedule_monthdays[job].setCurrentIndex(max(0, self.schedule_monthdays[job].findData(now.monthday)))
                at.setTime(QTime(now.hour, now.minute))
                self._show_day_time(job)
                self.schedule_reading[job].setText(entry.when)
                stored = entry.source == SOURCE_STORED
                self.schedule_source[job].setText("set here" if stored else "from the container")
                self.schedule_reset[job].setVisible(stored)
                set_prop(self.schedule_reading[job], "tone", "" if entry.valid else "bad")
        finally:
            self._loading = was

    def _show_day_time(self, job: str) -> None:
        choice = self.schedule_modes[job].currentData()
        self.schedule_days[job].setVisible(choice == CHOICE_WEEKLY)
        self.schedule_monthdays[job].setVisible(choice == CHOICE_MONTHLY)
        self.schedule_times[job].setVisible(choice in (CHOICE_WEEKLY, CHOICE_DAILY, CHOICE_MONTHLY))

    def schedule_choice(self, job: str) -> ScheduleChoice:
        """What *job*'s controls say now."""
        at = self.schedule_times[job].time()
        return ScheduleChoice(self.schedule_modes[job].currentData() or "", self.schedule_days[job].currentData() or 0,
                              at.hour(), at.minute(), self.schedule_monthdays[job].currentData() or 1)

    def _say_schedule(self, text: str, tone: str = "") -> None:
        self.schedule_status.setText(text)
        set_prop(self.schedule_status, "tone", tone if text else "")

    # --- editing a schedule ---------------------------------------------------------------------------------------

    def _schedule_edited(self, job: str) -> None:
        if self._loading:
            return
        self._show_day_time(job)
        current = next(e for e in schedule_entries(self._db, self._env) if e.job == job)
        text = self.schedule_choice(job).text()
        title = headless_tasks.TASK_BY_JOB[job].title
        if text == current.edit_text or (not current.valid and text == choice_of(current.edit_text, job).choice):
            return                                              # nothing changed (or the bad value still chosen)
        try:
            save_schedule_text(self._db, job, text)
        except ValueError as exc:
            self._say_schedule(f"{title}: {exc} Nothing was changed.", "bad")
            self._show_schedules()
            return
        self._show_schedules()
        self.refresh_status()
        self._say_schedule(f"{title}: saved. The runner uses it within a minute.", "ok")

    def _schedule_reset(self, job: str) -> None:
        reset_schedule_text(self._db, job)
        self._show_schedules()
        self.refresh_status()
        self._say_schedule("Back to the container's value.", "ok")

    # --- Run now --------------------------------------------------------------------------------------------------

    def _run_now(self, job: str) -> None:
        title = headless_tasks.TASK_BY_JOB[job].title
        try:
            headless_tasks.request_run(self._db, job)
        except Exception as exc:  # noqa: BLE001 - say it, do not crash the dialog
            self._say_schedule(f"{title}: could not ask the runner ({exc}).", "bad")
            return
        self.refresh_status()
        self._say_schedule(f"{title}: asked the runner to run it now. It starts within a few seconds.", "ok")

    # --- clearing finished download records ------------------------------------------------------------------------

    def _ledger(self):
        from ..store.downloads import DownloadLedger

        return DownloadLedger(self._db, tool=None)

    def _show_finished_count(self) -> None:
        if not hasattr(self, "finished_label"):
            return
        try:
            report = self._ledger().clear_finished(0, now=self._now(), dry_run=True)
        except Exception:  # noqa: BLE001
            self.finished_label.setText("")
            return
        if not report.finished:
            self.finished_label.setText("No finished download records.")
        else:
            self.finished_label.setText(f"{report.finished} finished download record"
                                        f"{'' if report.finished == 1 else 's'} in the Download tab's Show finished list; "
                                        f"{report.cleared} could be cleared now (any age).")

    def _fill_clear_menu(self) -> None:
        self.clear_menu.clear()
        for text, days in CLEAR_NOW_AGES:
            try:
                n = self._ledger().clear_finished(days, now=self._now(), dry_run=True).cleared
            except Exception:  # noqa: BLE001
                n = 0
            action = self.clear_menu.addAction(f"{text} ({n})")
            action.setEnabled(n > 0)
            action.triggered.connect(lambda _=False, d=days, t=text: self.clear_finished_now(d, t))

    def clear_finished_now(self, days: float, age_text: str = "") -> None:
        """Clear the finished download records older than *days* (0: any age), after asking."""
        ledger = self._ledger()
        report = ledger.clear_finished(days, now=self._now(), dry_run=True)
        if report.cleared == 0:
            self.clear_status.setText("Nothing to clear.")
            return
        age = f" ({age_text.lower()})" if age_text else ""
        if not self.confirm(f"Clear {report.cleared} finished download record{'' if report.cleared == 1 else 's'}{age}?\n\n"
                            "Records still in progress, filed, failed or waiting for you are kept, and so are your "
                            "volumes and chapters. This cannot be undone."):
            return
        done = ledger.clear_finished(days, now=self._now())
        self.clear_status.setText(f"Cleared {done.cleared} of {done.finished} finished record"
                                  f"{'' if done.finished == 1 else 's'}.")
        self.downloads_changed.emit()                           # the Download tab reloads its list
        self.refresh_status()


def _downloads_on() -> bool:
    from ..downloads.contracts import downloads_enabled

    return downloads_enabled()


__all__ = ["ScheduledTasksPage", "SECTION_TASKS"]
