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


class _NoReport:
    """A pass that filed nothing (no download in progress)."""

    filed: tuple = ()


def after_pass(ctx: JobContext, ledger, report) -> Optional[str]:
    """After a pass: when it filed volumes, record a rescan (the series' state catches up now, not at the nightly
    rescan) and ask MangaPixer to scan the libraries filed into (MangaPixer 1.36.0, the token's ``library:scan``
    scope); scan requests MangaPixer could not start yet are retried whenever they are due - unless the owner switched
    the requests off (Settings > Automation). Returns a summary."""
    from ..downloads.options import KEY_SCAN_AFTER_FILING, get_flag
    from ..services.mangapixer import open_cache
    from ..services.mangapixer.scans import libraries_for_series, request_scans

    store = ledger.store
    series_ids = [rec.series_id for rec in (ledger.get(i) for i in report.filed) if rec is not None]
    notes = []
    if series_ids:
        from .jobs import StoreRootsProvider, make_rescan

        res = make_rescan(StoreRootsProvider(db=store), backfill=False)(ctx)   # the nightly rescan signs archives
        notes.append(f"rescan {res.status}")
    if not get_flag(store, KEY_SCAN_AFTER_FILING):
        return "; ".join(notes) or None
    cache = open_cache(store)
    libraries = libraries_for_series(cache, series_ids)
    if libraries or cache.pending_scans():
        notes.append(request_scans(cache, libraries).summary())
    return "; ".join(notes) or None


def replaced_chapters_step(ledger) -> Optional[str]:
    """After a pass: the chapter files the filed volumes replace (:mod:`mangalist.upgrades`) - moved to the holding
    folder (holding mode, reversible) or recorded for the owner's confirmation (delete mode: never deleted here) - and
    the holding folder's retention purge. Runs before the rescan, so the rescan sees the result. Returns a summary."""
    from ..upgrades import after_filing

    return after_filing(ledger.store, ledger).summary() or None


def make_downloads_job(open_ledger: Optional[Callable[[], object]] = None,
                       client_factory: Optional[Callable[[object], object]] = None,
                       after: Optional[Callable[[JobContext, object, object], Optional[str]]] = after_pass,
                       replaced: Optional[Callable[[object], Optional[str]]] = replaced_chapters_step) -> JobFunc:
    """The job function. *open_ledger* / *client_factory* are for tests (default: the data folder's database and
    a client built from its stored connection); *after* runs after each pass (None: nothing); *replaced* is the
    replaced-chapters step (None: nothing)."""

    def _replaced(ledger) -> Optional[str]:
        if replaced is None:
            return None
        try:
            return replaced(ledger)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - the filing is done; the chapters stay where they are
            _log.warning("Downloads: the replaced-chapters step failed (%s)", type(exc).__name__, exc_info=True)
            return f"replaced chapters: {type(exc).__name__}"

    def _after(ctx: JobContext, ledger, report) -> Optional[str]:
        if after is None:
            return None
        try:
            return after(ctx, ledger, report)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - the filing is done; this extra step must not fail the job
            _log.warning("Downloads: the after-filing step failed (%s)", type(exc).__name__, exc_info=True)
            return f"after filing: {type(exc).__name__}"

    def downloads(ctx: JobContext) -> JobResult:
        from ..downloads.arrivals import run_arrivals
        from ..store.downloads import DownloadLedger

        if open_ledger is not None:
            ledger = open_ledger()
        else:
            from ..store import get_store

            ledger = DownloadLedger(get_store())
        if not ledger.active():
            notes = [n for n in (_replaced(ledger),          # waiting batches retried, the holding folder emptied
                                 _after(ctx, ledger, _NoReport())) if n]   # pending MangaPixer scans are retried too
            return JobResult("skipped", "no downloads in progress" + "".join(f"; {n}" for n in notes))
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
        replaced_note = _replaced(ledger)
        if replaced_note:
            message += f"; {replaced_note}"
            extra["replaced"] = replaced_note
        note = _after(ctx, ledger, report)
        if note:
            message += f"; {note}"
            extra["after"] = note
        _log.info("Downloads: %s", message)
        return JobResult(status, message, extra)

    return downloads
