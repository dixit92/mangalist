"""One library scan at a time, across processes: the GUI and the headless runner share one database, and a scan's
record step is its biggest writer (selective scanning, Stage 1; research note "Selective Scanning", question 4).

**The scan lock** (:func:`scan_lock`): an advisory OS lock (``flock``; ``msvcrt.locking`` on Windows) on
``scan.lock`` next to the library database, taken by every scan that records (a whole root or some of its folders) for
its walk and its record, root by root. A second scanner WAITS for it - it never fails because of it (owner, 2026-10-13:
the headless job held the write lock for 15-26 s and the GUI's record failed after its 10 s busy timeout with
``database is locked``). After :data:`DEFAULT_WAIT` the waiter goes ahead anyway (logged): SQLite's own locking still
keeps the database consistent, the lock only spares the long waits. The OS lock dies with its process, so there is no
stale lock to clean up (unlike the roots' ``.mangalist.lock`` files, which must reach across SMB; this one only has to
reach the processes that share one data folder, and ``flock`` does on Unraid's appdata share - measured 2026-10-13).

Within one process the lock is a re-entrant thread lock as well: a nested scan on the same thread (the Renamer's
rescan inside a pass) does not wait for itself, and two threads of one process take turns.

**Retry when the database is locked** (:func:`retry_if_locked`): a record step that still meets ``database is locked``
(a writer that is not a scan: the GUI's settings, the signature backfill) waits a little and tries again instead of
failing.

No Qt here.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional, TypeVar

_log = logging.getLogger(__name__)

LOCK_NAME = "scan.lock"
DEFAULT_WAIT = 15 * 60.0        # seconds a scanner waits for the other one (a full scan takes about a minute)
POLL_SECONDS = 0.25

T = TypeVar("T")

_guards: Dict[str, threading.RLock] = {}
_depth: Dict[str, int] = {}
_files: Dict[str, object] = {}
_guards_lock = threading.Lock()


def lock_path_for(db) -> Optional[Path]:
    """``scan.lock`` beside *db*'s database file (None without a file: nothing to share)."""
    path = getattr(db, "path", None)
    if not path:
        return None
    return Path(path).with_name(LOCK_NAME)


def _guard(key: str) -> threading.RLock:
    with _guards_lock:
        g = _guards.get(key)
        if g is None:
            g = _guards[key] = threading.RLock()
        return g


def _try_os_lock(fh) -> bool:
    if os.name == "nt":  # pragma: no cover - Windows only
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    except OSError as exc:      # a filesystem without locks: go ahead (SQLite still serialises the writes)
        _log.debug("Scan lock: %s cannot be locked (%s); going ahead without it", fh.name, exc)
        return True


def _os_unlock(fh) -> None:
    try:
        if os.name == "nt":  # pragma: no cover - Windows only
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def scan_lock(db=None, *, path: Optional[Path] = None, wait: float = DEFAULT_WAIT, what: str = "scan",
              on_wait: Optional[Callable[[], None]] = None, poll: float = POLL_SECONDS,
              clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> Iterator[bool]:
    """Hold the scan lock of *db*'s data folder (or of *path*) for the block. Yields True when it is held, False when
    the wait ran out (the block runs anyway). *on_wait* is called once when another process holds it (a window says
    "waiting for the other scan")."""
    target = Path(path) if path is not None else lock_path_for(db)
    if target is None:
        yield True
        return
    key = os.path.normcase(str(target))
    guard = _guard(key)
    started = clock()
    if not guard.acquire(timeout=max(0.0, wait)):
        _log.warning("Scan lock: another scan of this process still runs after %.0f s; %s goes ahead", wait, what)
        yield False
        return
    held = True
    try:
        depth = _depth.get(key, 0)
        if depth == 0:
            held = _acquire_file(key, target, started, wait, what, on_wait, poll, clock, sleep)
        _depth[key] = depth + 1
        try:
            yield held
        finally:
            _depth[key] -= 1
            if _depth[key] == 0:
                fh = _files.pop(key, None)
                if fh is not None:
                    _os_unlock(fh)
                    fh.close()
    finally:
        guard.release()


def _acquire_file(key: str, target: Path, started: float, wait: float, what: str,
                  on_wait: Optional[Callable[[], None]], poll: float, clock, sleep) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fh = open(target, "a+b")
    except OSError as exc:
        _log.warning("Scan lock: cannot open %s (%s); %s goes ahead without it", target, exc, what)
        return False
    told = False
    while True:
        if _try_os_lock(fh):
            _files[key] = fh
            if told:
                _log.info("Scan lock: the other scan finished after %.1f s; %s starts", clock() - started, what)
            return True
        if not told:
            told = True
            _log.info("Scan lock: another MangaList process is scanning; %s waits for it", what)
            if on_wait is not None:
                try:
                    on_wait()
                except Exception:  # noqa: BLE001 - a status line must not stop the scan
                    _log.debug("Scan lock: on_wait failed", exc_info=True)
        if clock() - started >= wait:
            fh.close()
            _log.warning("Scan lock: still held by another process after %.0f s; %s goes ahead", wait, what)
            return False
        sleep(poll)


def is_locked_error(exc: BaseException) -> bool:
    """``database is locked`` / ``database table is locked`` / busy: another connection holds the write lock."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def retry_if_locked(fn: Callable[[], T], *, what: str, tries: int = 4, first_wait: float = 2.0,
                    sleep: Callable[[float], None] = time.sleep) -> T:
    """``fn()``, tried again (after 2, 4, 8 s) while it fails with ``database is locked``; other errors, and the last
    try's, are raised. Each try already waits SQLite's busy timeout (10 s) before it fails."""
    for attempt in range(max(1, tries)):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not is_locked_error(exc) or attempt >= tries - 1:
                raise
            pause = first_wait * (2 ** attempt)
            _log.warning("%s: the database is locked by another writer; trying again in %.0f s (%d of %d)", what,
                         pause, attempt + 2, tries)
            sleep(pause)
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["DEFAULT_WAIT", "LOCK_NAME", "is_locked_error", "lock_path_for", "retry_if_locked", "scan_lock"]
