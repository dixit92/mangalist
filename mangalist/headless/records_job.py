"""The ``clean-records`` job: clear finished download records (owner, 2026-10-15: "Setting, user clearable, scheduled
task to clear [Daily, Weekly, Monthly]").

The chosen period is both how often the job runs and how old a finished record must be before it goes: Daily clears
what was finished more than a day ago, Weekly more than a week ago, Monthly more than 30 days ago. Which records are
"finished" and which stay however old they are is :meth:`mangalist.store.downloads.DownloadLedger.clear_finished`'s
business (nothing in progress, nothing filed or failed, nothing a replaced-chapters batch still points at).

Off until the owner picks a period (``schedule_clean_records`` in Settings > Scheduled tasks, or
``MANGALIST_CLEAN_RECORDS_SCHEDULE``); a "Run now" while it is off is skipped with that reason.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .jobs import JobContext, JobFunc, JobResult
from .schedule import DailyAt, EveryHours, MonthlyAt, Schedule, WeeklyAt, parse_schedule

_log = logging.getLogger(__name__)

JOB_NAME = "clean-records"
DESCRIPTION = "Clear finished download records"

#: The periods Settings offers, with how old (days) a finished record must be to be cleared at that period.
PERIOD_DAYS = {"daily": 1.0, "weekly": 7.0, "monthly": 30.0}


def retention_days(schedule: Optional[Schedule]) -> Optional[float]:
    """How old (days) a finished record must be for *schedule*: Daily 1, Weekly 7, Monthly 30; every N hours: that
    long, at least a day. None for a schedule that is off."""
    if schedule is None:
        return None
    if isinstance(schedule, MonthlyAt):
        return PERIOD_DAYS["monthly"]
    if isinstance(schedule, WeeklyAt):
        return PERIOD_DAYS["weekly"]
    if isinstance(schedule, DailyAt):
        return PERIOD_DAYS["daily"]
    if isinstance(schedule, EveryHours):
        return max(PERIOD_DAYS["daily"], schedule.hours / 24.0)
    return None


def current_retention(db: Any, env=None) -> Optional[float]:
    """The retention (days) of the schedule in force now (the stored value, else the container's), None when off."""
    from .settings import SCHEDULE_BY_JOB, resolve_schedule

    choice = resolve_schedule(SCHEDULE_BY_JOB[JOB_NAME], db, env)
    try:
        return retention_days(parse_schedule(choice.text))
    except ValueError:
        return None


def make_clean_records(open_db: Optional[Callable[[], Any]] = None) -> JobFunc:
    """The job function. *open_db* is for tests (default: the data folder's database)."""

    def clean_records(ctx: JobContext) -> JobResult:
        if open_db is not None:
            db = open_db()
        else:
            from ..store import get_store

            db = get_store()
        days = current_retention(db)
        if days is None:
            return JobResult("skipped", "choose Daily, Weekly or Monthly in Settings > Scheduled tasks first")
        from ..store.downloads import DownloadLedger

        report = DownloadLedger(db, tool=None).clear_finished(days)
        _log.info("Clear finished download records (older than %g days): %s", days, report.summary())
        return JobResult("ok", report.summary(), {"older_than_days": days, "cleared": report.cleared,
                                                  "finished": report.finished, "kept": report.kept})

    return clean_records
