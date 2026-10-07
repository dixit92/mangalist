"""The ``downloads`` job for the headless runner: one arrivals pass (file finished ``mangalist`` torrents into
their series folders, then "Remove Completed"; :mod:`mangalist.downloads.arrivals`).

Registered by :func:`mangalist.headless.jobs.build_registry`, enabled only when downloads are on
(``MANGALIST_DOWNLOADS``), every ``MANGALIST_DOWNLOADS_SCHEDULE`` (default ``every 1h``). Skipped while no
qBittorrent connection is stored, or while no client is wired. The qBittorrent client comes from
:func:`mangalist.services.qbittorrent.client_from_connection` (the Sources lane; the integrator wires it), or
from *client_factory* (tests).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .jobs import Cancelled, JobContext, JobFunc, JobResult

_log = logging.getLogger(__name__)

JOB_NAME = "downloads"
DESCRIPTION = "File finished downloads into their series and remove completed torrents (opt-in)"


def _default_client_factory(conn):
    try:
        from ..services import qbittorrent  # the Sources lane's client
    except ImportError:
        return None
    factory = getattr(qbittorrent, "client_from_connection", None)
    return factory(conn) if factory is not None else None


def make_downloads_job(open_ledger: Optional[Callable[[], object]] = None,
                       client_factory: Optional[Callable[[object], object]] = None) -> JobFunc:
    """The job function. *open_ledger* / *client_factory* are for tests (default: the data folder's database and
    a client built from its stored connection)."""

    def downloads(ctx: JobContext) -> JobResult:
        from ..downloads.arrivals import run_arrivals
        from ..store.downloads import DownloadLedger

        if open_ledger is not None:
            ledger = open_ledger()
        else:
            from ..store import get_store

            ledger = DownloadLedger(get_store())
        if not ledger.active():
            return JobResult("skipped", "no downloads in progress")
        conn = ledger.connection()
        if conn is None:
            _log.info("Downloads: no qBittorrent connection is set up; skipped")
            return JobResult("skipped", "no qBittorrent connection set up")
        client = (client_factory or _default_client_factory)(conn)
        if client is None:
            _log.warning("Downloads: no qBittorrent client available in this build; skipped")
            return JobResult("skipped", "no qBittorrent client available")
        report = run_arrivals(client, ledger, should_stop=lambda: ctx.stop_requested)
        if ctx.stop_requested:
            raise Cancelled()
        extra = report.summary()
        extra["failed_records"] = [{"id": i, "why": why} for i, why in report.failed]
        extra["waiting_records"] = [{"id": i, "why": why} for i, why in report.waiting]
        if report.error:
            return JobResult("error", report.error, extra)
        status = "error" if report.errors else "ok"
        message = (f"{report.checked} checked: {len(report.filed)} filed, {len(report.removed)} removed, "
                   f"{len(report.failed)} failed, {len(report.waiting)} waiting")
        _log.info("Downloads: %s", message)
        return JobResult(status, message, extra)

    return downloads
