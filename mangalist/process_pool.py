"""Long Python jobs in a subprocess, without Qt (UI performance stage 2).

A Python thread shares the interpreter lock with the window: while the Rename-pending count (35-50 s for 700 series),
the duplicate count and the like ran in a ``QThread`` after every scan, every UI action was 40-50 % slower and the
window stalled for about 150 ms at a time (research note, "GIL contention"). These jobs are pure functions of the
library database, so they run in a separate process instead: a :class:`concurrent.futures.ProcessPoolExecutor` with the
*spawn* start method (the same on Windows, macOS and Linux, safe next to Qt's threads - a fork would copy them - and
what a PyInstaller build supports through ``multiprocessing.freeze_support()`` in ``mangalist/__main__.py``). A job
gets the database's path, opens its own store, and returns a plain picklable result.

Owner, 2026-10-13 (question 41): "OK" to about 50-100 MB more memory while they run. The pool starts with the first job
and is shut down when it has been idle for a while (:func:`release_if_idle`) and when the window closes
(:func:`shutdown`, which also stops a job still running).

``MANGALIST_SUBPROCESS=0`` runs the jobs in the calling thread as before (a fallback for a platform where a subprocess
cannot start; a pool that fails to start falls back by itself).

Every job here imports only Qt-free modules: a child process loads ``mangalist`` without PySide6.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Optional, Sequence, Tuple

if TYPE_CHECKING:       # imported on first use: concurrent.futures.process and multiprocessing cost start-up time
    from concurrent.futures import Future, ProcessPoolExecutor

_log = logging.getLogger(__name__)

ENV_SUBPROCESS = "MANGALIST_SUBPROCESS"
MAX_WORKERS = 2

_lock = threading.Lock()
_pool: Optional["ProcessPoolExecutor"] = None
_running = 0                    # jobs submitted and not finished
_broken = False                 # the pool could not start: jobs run in the caller's thread from then on


def enabled() -> bool:
    """Do long jobs run in a subprocess (the default; ``MANGALIST_SUBPROCESS=0`` turns it off)?"""
    return os.environ.get(ENV_SUBPROCESS, "1").strip().lower() not in ("0", "false", "no", "off") and not _broken


def _child_init() -> None:
    """In the child: no handlers of the parent's (its log file stays the window's); warnings and up to stderr when
    there is one."""
    logging.getLogger().setLevel(logging.WARNING)


def _get_pool() -> "ProcessPoolExecutor":
    global _pool
    if _pool is None:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        _pool = ProcessPoolExecutor(max_workers=MAX_WORKERS, mp_context=multiprocessing.get_context("spawn"),
                                    initializer=_child_init)
    return _pool


def submit(fn: Callable[..., Any], *args: Any) -> "Future":
    """Run ``fn(*args)`` in the pool (a module-level function and picklable arguments); the future's result is its
    return value. Falls back to running it in the calling thread when subprocesses are off or cannot start."""
    global _running, _broken
    if enabled():
        with _lock:
            try:
                future = _get_pool().submit(fn, *args)
            except Exception:  # noqa: BLE001 - e.g. a platform without working subprocesses: in this thread instead
                _log.warning("Starting a subprocess for %s failed; running it in this process", fn.__name__,
                             exc_info=True)
                _broken = True
            else:
                _running += 1
                future.add_done_callback(_job_done)
                return future
    from concurrent.futures import Future

    future = Future()
    try:
        future.set_result(fn(*args))
    except BaseException as exc:  # noqa: BLE001 - handed to the caller through the future
        future.set_exception(exc)
    return future


def _job_done(_future: "Future") -> None:
    global _running
    with _lock:
        _running = max(0, _running - 1)


def busy() -> bool:
    with _lock:
        return _running > 0


def release_if_idle() -> bool:
    """Shut the pool down when no job runs (its processes' memory goes back). True when it was shut down."""
    global _pool
    with _lock:
        if _pool is None or _running:
            return False
        pool, _pool = _pool, None
    pool.shutdown(wait=False, cancel_futures=True)
    return True


def shutdown() -> None:
    """Stop the pool now, a running job included (the window closes; its result is not wanted any more)."""
    global _pool, _running
    with _lock:
        pool, _pool = _pool, None
        _running = 0
    if pool is None:
        return
    for process in list(getattr(pool, "_processes", {}).values()):     # a job of 40 s must not hold the exit up
        try:
            process.terminate()
        except Exception:  # noqa: BLE001 - already gone
            pass
    pool.shutdown(wait=False, cancel_futures=True)


# --- the jobs ----------------------------------------------------------------------------------------------------


def _open(db_path: str):
    from .store import Store

    return Store(Path(db_path), import_legacy=False)


def _under_any(folder: str, tops: Iterable[str]) -> bool:
    f = os.path.normcase(str(folder))
    for t in tops:
        t = os.path.normcase(str(t)).rstrip("/\\")
        if f == t or f.startswith(t + os.sep):
            return True
    return False


def rename_pending_counts(db_path: str, folders: Optional[Sequence[str]] = None) -> Dict[str, int]:
    """Per series folder: how many files the naming scheme would rename (the List's "Rename pending") - for every
    present series, or the series below *folders* (a folder scan's top-level folders). The series are rebuilt from the
    last scan's records exactly as the scanner parsed them (:func:`mangalist.wanted.entries_from_db`, what the headless
    runner's To get list reads), so the count is the window's own count of its scanned rows."""
    from .renamer import Renamer
    from .wanted import entries_from_db

    db = _open(db_path)
    entries = []
    for root in db.list_roots():
        try:
            entries += [e for _row, e in entries_from_db(db, root)]
        except Exception:  # noqa: BLE001 - one unreadable library must not hide the others' counts
            _log.warning("Rename pending: the series of %s could not be read", root.name, exc_info=True)
    if folders is not None:
        entries = [e for e in entries if _under_any(str(e.folder), folders)]
    return Renamer(db, rescan=None).pending_counts(entries)


def count_by_folder(groups) -> Tuple[int, Dict[str, Tuple[int, int]]]:
    """Lane C's duplicate groups -> (how many, per series folder (volume numbers, chapter numbers))."""
    groups = list(groups)
    per_folder: Dict[str, list] = {}
    for g in groups:
        folder = getattr(g, "folder", None)
        if folder is None:
            continue
        counts = per_folder.setdefault(str(folder), [0, 0])
        counts[0 if getattr(g, "kind", "") == "volume" else 1] += 1
    return len(groups), {k: (v[0], v[1]) for k, v in per_folder.items()}


def duplicate_counts(db_path: str) -> Tuple[int, Dict[str, Tuple[int, int]]]:
    """The Duplicates chip's count: numbers held by more than one file, in all and per series folder."""
    from .duplicates import find_duplicate_files

    return count_by_folder(find_duplicate_files(_open(db_path)))


__all__ = ["enabled", "submit", "busy", "release_if_idle", "shutdown", "rename_pending_counts", "duplicate_counts",
           "count_by_folder", "ENV_SUBPROCESS"]
