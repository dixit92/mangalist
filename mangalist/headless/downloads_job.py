"""The ``downloads`` job for the headless runner: one arrivals pass (file finished ``mangalist`` torrents into
their series folders, then "Remove Completed"; :mod:`mangalist.downloads.arrivals`), then one pass of the download
queue (:func:`mangalist.downloads.queueing.run_queue`: sizes from qBittorrent, then queued downloads handed over in
order while they fit under the download budget - the room Remove Completed just freed is used at once).

Registered by :func:`mangalist.headless.jobs.build_registry`, enabled only when downloads are on
(``MANGALIST_DOWNLOADS``), every ``MANGALIST_DOWNLOADS_SCHEDULE`` (default ``every 1h``). Skipped while no
qBittorrent connection is stored, or while no client is wired. The qBittorrent client comes from
:func:`mangalist.services.qbittorrent.client_from_connection` (the Sources lane; the integrator wires it), or
from *client_factory* (tests).

**Chapter downloads** (the Suwayomi MVP, 2026-10-10): when chapter records are in progress, the same job first runs one
chapter arrivals pass (:mod:`mangalist.downloads.chapter_arrivals`: chapters Suwayomi finished -> the series folder,
named by the scheme; Suwayomi then deletes its copy) with the client from
:func:`mangalist.services.suwayomi.client_from_connection` (or *chapter_client_factory*, tests) and the stored download
folder. The qBittorrent part then runs exactly as before; the after-filing step (rescan, MangaPixer scan) sees the
chapters filed too. Without chapter records nothing about the job changes.
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

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

    def __init__(self, journal_since=None) -> None:
        self.journal_since = journal_since


def _journal_mark(ledger):
    """Where the journal stands before the pass (the after-filing scan reads what the pass moved since)."""
    store = getattr(ledger, "store", None)
    if store is None:
        return None
    try:
        from ..scanner import journal_mark

        return journal_mark(store)
    except Exception:  # noqa: BLE001 - without the mark only the filed series are scanned
        _log.debug("Downloads: the journal could not be read", exc_info=True)
        return None


def after_pass(ctx: JobContext, ledger, report) -> Optional[str]:
    """After a pass: when it filed volumes or moved files, scan and record the folders it touched (the series' state
    catches up now, not at the next periodic rescan): the filed series' folders and both sides of every journal step
    the pass wrote (the replaced chapters it set aside), never every root - one filed volume used to cost a walk of all
    roots, an all-root record and the Renamer's dry runs, about 90 s (selective scanning, 2026-10-13). Then the
    Renamer's automatic pass (it looks again only at series that changed). Then MangaPixer is asked to scan the
    libraries filed into (MangaPixer 1.36.0, the token's ``library:scan`` scope); scan requests MangaPixer could not
    start yet are retried whenever they are due - unless the owner switched the requests off (Settings > Automation).
    Returns a summary."""
    from ..downloads.options import KEY_SCAN_AFTER_FILING, get_flag
    from ..services.mangapixer import open_cache
    from ..services.mangapixer.scans import libraries_for_series, request_scans

    store = ledger.store
    every = ledger.for_tool(None) if callable(getattr(ledger, "for_tool", None)) else ledger    # chapters filed too
    records = [rec for rec in (every.get(i) for i in report.filed) if rec is not None]
    series_ids = [rec.series_id for rec in records]
    notes = []
    scopes = _scopes_after(store, records, getattr(report, "journal_since", None))
    if scopes:
        notes.append(_scan_after(ctx, store, scopes))
    if records:
        notes.extend(_retire_filled(store))
    if not get_flag(store, KEY_SCAN_AFTER_FILING):
        return "; ".join(notes) or None
    cache = open_cache(store)
    libraries = libraries_for_series(cache, series_ids)
    if libraries or cache.pending_scans():
        notes.append(request_scans(cache, libraries).summary())
    return "; ".join(notes) or None


def _retire_filled(store) -> List[str]:
    """Waiting findings the pass's filings made unnecessary leave Found now, not at the next find pass."""
    from ..downloads.finder import retire_filled

    try:
        n = retire_filled(store)
    except Exception:  # noqa: BLE001 - a courtesy; the next find pass retires them anyway
        _log.warning("Downloads: retiring filled findings failed", exc_info=True)
        return []
    return [f"{n} finding(s) no longer needed"] if n else []


def _scopes_after(store, records, journal_since) -> dict:
    """The folders a pass touched: each filed record's series (its row; the record's target folder when the row is
    gone) and both sides of the journal steps written since *journal_since*."""
    from ..scanner import folder_scopes, journal_folders_since, merge_scopes, series_scopes

    roots = store.list_roots()
    known = series_scopes(store, [r.series_id for r in records if r.series_id is not None])
    located = {r.series_id for r in records if r.series_id is not None and _series_known(store, r.series_id)}
    loose = folder_scopes(roots, [r.target_dir for r in records if r.target_dir and r.series_id not in located])
    moved: dict = {}
    if journal_since is not None:
        try:
            paths, _mark = journal_folders_since(store, journal_since)
            moved = folder_scopes(roots, paths)
        except Exception:  # noqa: BLE001 - the filed series are still scanned
            _log.warning("Downloads: reading the journal for the after-filing scan failed", exc_info=True)
    return merge_scopes(known, loose, moved)


def _series_known(store, series_id) -> bool:
    with store.connect() as con:
        return con.execute("SELECT 1 FROM series WHERE id = ?", (int(series_id),)).fetchone() is not None


def _scan_after(ctx: JobContext, store, scopes: dict) -> str:
    """Scan and record *scopes*, then the Renamer's automatic pass; ``rescan ok (2 folders)``."""
    from .jobs import automatic_renames, record_folders

    try:
        result = record_folders(store, scopes, ctx)
    except Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - the filing is done; the periodic rescan records it
        _log.warning("Downloads: the after-filing scan failed (%s)", type(exc).__name__, exc_info=True)
        return f"rescan error ({type(exc).__name__})"
    if result is None:
        return "rescan skipped (no library folder)"
    status = "error" if result.errors else "ok"
    n = sum(len(rs.scope) if rs.scope is not None else 0 for rs in result.roots)
    whole = [rs.root_name for rs in result.roots if rs.scope is None]
    what = ", ".join(([f"{n} folder{'' if n == 1 else 's'}"] if n else []) + whole)
    try:
        ctx.check()
        _log.info("Downloads: after filing: %s", automatic_renames(store, ctx))
    except Cancelled:
        raise
    except Exception:  # noqa: BLE001 - the scan is recorded; renames are tried next time
        _log.exception("Downloads: the automatic renames after filing failed")
    return f"rescan {status} ({what})"


def replaced_chapters_step(ledger) -> Optional[str]:
    """After a pass: the chapter files the filed volumes replace (:mod:`mangalist.upgrades`) - moved to the holding
    folder (holding mode, reversible) or recorded for the owner's confirmation (delete mode: never deleted here) - and
    the holding folder's retention purge. Runs before the rescan, so the rescan sees the result. Returns a summary."""
    from ..upgrades import after_filing

    return after_filing(ledger.store, ledger).summary() or None


def _queue_work(ledger) -> bool:
    """True when the queue pass has something to do without any download in progress: a queued download to hand over,
    or a failed one still counted against the budget (the pass looks whether its torrent is still in qBittorrent)."""
    from ..downloads.contracts import DownloadStatus

    return any(r.status == DownloadStatus.QUEUED or (r.status == DownloadStatus.FAILED and r.in_client)
               for r in ledger.all_records())


def _default_chapter_client_factory(conn):
    """The chosen program's client: Suwayomi, or the built-in extension host (one factory for both)."""
    try:
        from ..downloads import chapter_backend
    except ImportError:         # pragma: no cover - the client ships with MangaList
        return None
    return chapter_backend.client_from_connection(conn)


class _Merged:
    """The filed records of both passes, for the after-filing step, and where the journal stood before them."""

    def __init__(self, *reports, journal_since=None) -> None:
        self.filed = tuple(i for r in reports if r is not None for i in r.filed)
        self.journal_since = journal_since


def chapters_pass(ctx: JobContext, ledger, chapter_client_factory=None, namer=None):
    """One chapter arrivals pass when chapter downloads are in progress: ``(report, note)``; ``(None, None)`` when there
    is nothing to do (or *ledger* keeps no chapter records). *note* says why the pass could not run (no Suwayomi set up).

    Each record is finished by the program it was sent to (transition audit B2 / C2): the chosen program's records (and
    those of unknown program, an older build's) first, then the other program's - e.g. a chapter Suwayomi downloaded and
    MangaList filed just before a switch still has Suwayomi delete its copy. The other program not answering is not an
    error of the job (it may be stopped on purpose): its records wait, and say so."""
    from ..downloads.contracts import CHAPTER_BACKEND_EXTENSIONS, CHAPTER_BACKEND_SUWAYOMI, TOOL_SUWAYOMI

    for_tool = getattr(ledger, "for_tool", None)
    if not callable(for_tool):
        return None, None
    chapters = for_tool(TOOL_SUWAYOMI)
    active = list(chapters.active())
    if not active:
        return None, None
    from ..downloads import chapter_backend
    from ..downloads.arrivals import ArrivalsReport
    from ..downloads.chapter_arrivals import run_chapter_arrivals
    from ..store.downloads import SuwayomiSettings

    settings = SuwayomiSettings(ledger.store)
    chosen = settings.backend()
    other = CHAPTER_BACKEND_SUWAYOMI if chosen == CHAPTER_BACKEND_EXTENSIONS else CHAPTER_BACKEND_EXTENSIONS
    merged, notes, ran = ArrivalsReport(), [], False
    for backend, unknown_too in ((chosen, True), (other, False)):
        mine = [r for r in active if r.backend == backend or (unknown_too and not r.backend)]
        if not mine:
            continue
        name = chapter_backend.name_of_backend(backend)
        conn = settings.connection_for(backend)
        if conn is None or not conn.base_url:
            _log.info("Downloads: %d chapter download(s) of %s are in progress but it is not set up; skipped",
                      len(mine), name)
            notes.append(f"chapters: no {name} connection set up" if backend == chosen
                         else f"{len(mine)} chapter download(s) of {name} wait: it is not set up")
            continue
        client = (chapter_client_factory or _default_chapter_client_factory)(conn)
        if client is None:
            notes.append(f"chapters: no {name} client available")
            continue
        try:
            if backend == CHAPTER_BACKEND_EXTENSIONS:
                from ..downloads import host_identity

                host_identity.check(ledger.store, client)
            report = run_chapter_arrivals(client, chapters, download_dir=conn.download_dir, namer=namer,
                                          should_stop=lambda: ctx.stop_requested, unknown_too=unknown_too)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        ran = True
        if report.error and backend != chosen:      # e.g. Suwayomi stopped after the switch: its records just wait
            _log.info("Downloads: %d chapter download(s) of %s wait: %s", len(mine), name, report.error)
            report.waiting.extend((r.id, report.error) for r in mine)
            report.checked += len(mine)
            report.error = None
        _merge(merged, report)
        if ctx.stop_requested:
            raise Cancelled()
    if not ran:
        return None, "; ".join(notes) or None
    return merged, None


def _merge(into, report) -> None:
    """Add one program's arrivals report to the job's."""
    into.checked += report.checked
    for name in ("downloaded", "filed", "copied", "removed", "failed", "waiting", "errors"):
        getattr(into, name).extend(getattr(report, name))
    if report.error:
        into.error = f"{into.error}; {report.error}" if into.error else report.error


def chapters_text(report, note: Optional[str]) -> Optional[str]:
    """``chapters: 3 checked: 2 filed, 0 failed, 1 waiting`` (or the note; None when there was nothing to do)."""
    if report is None:
        return note
    if report.error:
        return f"chapters: {report.error}"
    return (f"chapters: {report.checked} checked: {len(report.filed)} filed, {len(report.failed)} failed, "
            f"{len(report.waiting)} waiting")


def make_downloads_job(open_ledger: Optional[Callable[[], object]] = None,
                       client_factory: Optional[Callable[[object], object]] = None,
                       after: Optional[Callable[[JobContext, object, object], Optional[str]]] = after_pass,
                       replaced: Optional[Callable[[object], Optional[str]]] = replaced_chapters_step,
                       chapter_client_factory: Optional[Callable[[object], object]] = None,
                       namer=None) -> JobFunc:
    """The job function. *open_ledger* / *client_factory* are for tests (default: the data folder's database and
    a client built from its stored connection); *after* runs after each pass (None: nothing); *replaced* is the
    replaced-chapters step (None: nothing). *chapter_client_factory* / *namer*: the Suwayomi client and the naming
    scheme of the chapter pass (tests; default: the stored connection's client, ``mangalist.naming``)."""

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
        mark = _journal_mark(ledger)
        chapter_report, chapter_note = chapters_pass(ctx, ledger, chapter_client_factory, namer)
        chapter_line = chapters_text(chapter_report, chapter_note)
        active = bool(ledger.active())
        queue_work = _queue_work(ledger)
        if not active and not queue_work:
            if chapter_line is not None:                    # chapter downloads only
                return _chapters_only(ctx, ledger, chapter_report, chapter_line, mark)
            notes = [n for n in (_replaced(ledger),          # waiting batches retried, the holding folder emptied
                                 _after(ctx, ledger, _NoReport(mark))) if n]   # pending MangaPixer scans retried too
            return JobResult("skipped", "no downloads in progress" + "".join(f"; {n}" for n in notes))
        conn = ledger.connection()
        if conn is None:
            _log.info("Downloads: no qBittorrent connection is set up; skipped")
            if chapter_report is not None:
                return _chapters_only(ctx, ledger, chapter_report, chapter_line + "; no qBittorrent connection set up",
                                      mark)
            return JobResult("skipped", "no qBittorrent connection set up")
        client = (client_factory or _default_client_factory)(conn)
        if client is None:
            _log.warning("Downloads: no qBittorrent client available in this build; skipped")
            if chapter_report is not None:
                return _chapters_only(ctx, ledger, chapter_report, chapter_line + "; no qBittorrent client available",
                                      mark)
            return JobResult("skipped", "no qBittorrent client available")
        from ..downloads.arrivals import ArrivalsReport
        from ..downloads.queueing import run_queue

        report = run_arrivals(client, ledger, should_stop=lambda: ctx.stop_requested) if active else ArrivalsReport()
        if ctx.stop_requested:
            raise Cancelled()
        extra = report.summary()
        extra["failed_records"] = [{"id": i, "why": why} for i, why in report.failed]
        extra["waiting_records"] = [{"id": i, "why": why} for i, why in report.waiting]
        if report.error:
            return JobResult("error", report.error, extra)
        queue = run_queue(client, ledger, should_stop=lambda: ctx.stop_requested)
        if ctx.stop_requested:
            raise Cancelled()
        extra.update(queue.summary())
        extra["queue_failed_records"] = [{"id": i, "why": why} for i, why in queue.failed]
        status = "error" if report.errors or queue.error else "ok"
        # "torrents:" next to "chapters:" - one check covers both (owner, 2026-10-10)
        message = (f"torrents: {report.checked} checked: {len(report.filed)} filed, {len(report.removed)} removed, "
                   f"{len(report.failed)} failed, {len(report.waiting)} waiting")
        if queue.text():
            message += f"; {queue.text()}"
        if chapter_line:
            message += f"; {chapter_line}"
            extra.update(_chapter_extra(chapter_report))
            if chapter_report is not None and (chapter_report.errors or chapter_report.error):
                status = "error"
        replaced_note = _replaced(ledger)
        if replaced_note:
            message += f"; {replaced_note}"
            extra["replaced"] = replaced_note
        note = _after(ctx, ledger, _Merged(report, chapter_report, journal_since=mark))
        if note:
            message += f"; {note}"
            extra["after"] = note
        _log.info("Downloads: %s", message)
        return JobResult(status, message, extra)

    def _chapters_only(ctx: JobContext, ledger, chapter_report, line: str, mark=None) -> JobResult:
        """The job's result when only chapter downloads were in progress (or qBittorrent could not run)."""
        extra = _chapter_extra(chapter_report)
        if chapter_report is None:                      # the pass could not run (no Suwayomi set up)
            return JobResult("skipped", line, extra)
        status = "error" if chapter_report.error or chapter_report.errors else "ok"
        message = line
        replaced_note = _replaced(ledger)
        if replaced_note:
            message += f"; {replaced_note}"
            extra["replaced"] = replaced_note
        note = _after(ctx, ledger, _Merged(chapter_report, journal_since=mark))
        if note:
            message += f"; {note}"
            extra["after"] = note
        _log.info("Downloads: %s", message)
        return JobResult(status, message, extra)

    return downloads


def _chapter_extra(report) -> dict:
    if report is None:
        return {}
    extra = {f"chapters_{k}": v for k, v in report.summary().items()}
    extra["chapters_failed_records"] = [{"id": i, "why": why} for i, why in report.failed]
    extra["chapters_waiting_records"] = [{"id": i, "why": why} for i, why in report.waiting]
    if report.error:
        extra["chapters_error"] = report.error
    return extra
