"""The ``extension-updates`` job for the headless runner: the scheduled extension update check
(:func:`mangalist.downloads.extension_updates.run_check`; owner, 2026-10-22: "Update notice" and "Auto-update", off by
default).

Daily by default (``MANGALIST_EXTENSION_UPDATES_SCHEDULE``; Settings > Scheduled tasks offers Weekly, Daily, every 12 or
6 hours, or Off). It runs only while "Built-in extensions" is chosen for chapter downloads (else skipped: the host is not
started) and the host answers (else skipped, the stored notice kept). It asks the host which installed extensions have
a newer version in their own repository, keeps the result in the database for the notices (Settings > Connected
services, the Download tab) and, with Auto-update on, installs them - never while a chapter of that extension's sources
downloads (the update then waits for the next run).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable, Optional

from .jobs import Cancelled, JobContext, JobFunc, JobResult

_log = logging.getLogger(__name__)

JOB_NAME = "extension-updates"
DESCRIPTION = "Check the built-in extensions for updates (Auto-update installs them)"


def schedule_interval(db: Any, env=None) -> Optional[timedelta]:
    """The update check's interval by its schedule now (stored, else the container's variable, else daily): an
    interval's own, a day for a daily time, a week for a weekly one, 31 days for a monthly one. None when it is off."""
    from .schedule import DailyAt, EveryHours, MonthlyAt, WeeklyAt, parse_schedule
    from .settings import SCHEDULE_BY_JOB, resolve_schedule

    try:
        schedule = parse_schedule(resolve_schedule(SCHEDULE_BY_JOB[JOB_NAME], db, env).text)
    except Exception:  # noqa: BLE001 - a bad variable: daily
        return timedelta(days=1)
    if isinstance(schedule, EveryHours):
        return schedule.interval
    if isinstance(schedule, DailyAt):
        return timedelta(days=1)
    if isinstance(schedule, WeeklyAt):
        return timedelta(days=7)
    if isinstance(schedule, MonthlyAt):
        return timedelta(days=31)
    return None


def make_extension_updates(open_db: Optional[Callable[[], Any]] = None,
                           client_factory: Optional[Callable[[str], Any]] = None) -> JobFunc:
    """The job. *open_db* / *client_factory* are for tests (default: the data folder's database and a client for the
    host's address in Settings)."""

    def extension_updates(ctx: JobContext) -> JobResult:
        from ..downloads.extension_updates import notice_payload, run_check
        from ..services.extensions.client import ExtensionHostClient, ExtensionHostError
        from ..store.downloads import SuwayomiSettings

        if open_db is not None:
            db = open_db()
        else:
            from ..store import get_store

            db = get_store()
        settings = SuwayomiSettings(db)
        if not settings.builtin:
            _log.info("Extension updates: chapter downloads go through Suwayomi; nothing checked")
            return JobResult("skipped", "Built-in extensions is not chosen for chapter downloads")
        url = settings.extension_host().url
        client = client_factory(url) if client_factory is not None else ExtensionHostClient(url, timeout=300.0)
        try:
            try:
                client.health()
            except ExtensionHostError as exc:
                _log.info("Extension updates: the extension host is not running (%s); checked at the next run", exc)
                return JobResult("skipped", f"the extension host is not running: {exc}")
            try:
                check = run_check(db, client, auto=settings.extension_auto_update(),
                                  should_stop=lambda: ctx.stop_requested)
            except ExtensionHostError as exc:
                return JobResult("error", f"the extension host could not be asked: {exc}")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        if ctx.stop_requested:
            raise Cancelled()
        return JobResult("error" if check.errors else "ok", check.summary(), notice_payload(check))

    return extension_updates
