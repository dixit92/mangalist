"""The ``chapter-check`` job for the headless runner: the scheduled newer-chapter check
(:func:`mangalist.downloads.chapter_check.run_chapter_check`; owner, 2026-10-18: "Scheduled newer-chapter check - every
6-12 h, ongoing chapter series with a saved source are checked for chapters newer than the newest held; Auto sends,
Notify waits").

Registered by :func:`mangalist.headless.jobs.build_registry` and switched on only with downloads on
(``MANGALIST_DOWNLOADS``), like the find pass; every ``MANGALIST_CHAPTER_CHECK_SCHEDULE`` (default ``every 6h``), which
the owner changes or turns off in Settings > Scheduled tasks. A check while another one or a find pass runs is skipped
(it runs again at its next time). The check logs its own lines; the runner's line carries its summary.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence

from .jobs import Cancelled, JobContext, JobFunc, JobResult

_log = logging.getLogger(__name__)

JOB_NAME = "chapter-check"
DESCRIPTION = "Check the saved sources for chapters newer than the newest held (opt-in)"


def make_chapter_check(downloads_enabled: bool, *, open_db: Optional[Callable[[], Any]] = None,
                       backend_factory: Optional[Callable[[Any], Any]] = None,
                       views: Optional[Callable[[Any], Sequence[Any]]] = None,
                       pause: Optional[float] = None) -> JobFunc:
    """The job. *open_db* / *backend_factory* / *views* / *pause* are for tests (default: the data folder's database,
    :class:`mangalist.downloads.adapter.Backend` over it, :func:`mangalist.wanted.library_states`, the check's own
    pause between series)."""

    def chapter_check(ctx: JobContext) -> JobResult:
        if not downloads_enabled:
            _log.info("Chapter check: downloads are off (opt-in); nothing checked")
            return JobResult("skipped", "downloads are off")
        from ..downloads.chapter_check import DEFAULT_PAUSE, CheckBusy, run_chapter_check

        if open_db is not None:
            db = open_db()
        else:
            from ..store import get_store

            db = get_store()
        backend = backend_factory(db) if backend_factory is not None else None
        try:
            report = run_chapter_check(db, backend=backend, views=views, should_stop=lambda: ctx.stop_requested,
                                       pause=DEFAULT_PAUSE if pause is None else pause)
        except CheckBusy as exc:
            _log.info("Chapter check: skipped: %s", exc)
            return JobResult("skipped", str(exc))
        if ctx.stop_requested:
            raise Cancelled()
        extra = {"series_selected": report.selected, "series_checked": report.series_checked,
                 "with_new": report.with_new, "sent": report.sent, "waiting": report.waiting,
                 "unreadable": report.unreadable, "errors": report.errors,
                 "not_asked": dict(report.skipped), "lines": report.lines[:100]}
        if report.not_checked and not report.errors:
            return JobResult("skipped", report.summary, extra)
        return JobResult("error" if report.errors else "ok", report.summary, extra)

    return chapter_check
