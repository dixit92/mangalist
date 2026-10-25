"""The library database file: connections, migrations, the ``meta`` table."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .schema import MIGRATIONS, SCHEMA_VERSION

_log = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 10_000
INIT_LOCK_SUFFIX = ".init.lock"
INIT_LOCK_WAIT_SECONDS = 300.0          # a migration of a large database may take a while; the other process waits


@contextmanager
def init_file_lock(db_path: Path, wait: float = INIT_LOCK_WAIT_SECONDS) -> Iterator[None]:
    """Exclusive, between PROCESSES, for creating / upgrading the database at *db_path*.

    The window and the headless runner of the container both open the store at its first start, found the file
    missing / old at the same moment and both ran the schema migration; the second failed with "duplicate column
    name" (owner, 2026-10-24, backlog bug). Whoever takes this lock first migrates; the other waits, then reads
    the schema version again and finds nothing to do. An OS lock (``flock`` / ``msvcrt.locking``) on a file beside the
    database - it is released by the system when the holder dies, so a crash never leaves it stuck. If the lock cannot
    be used at all (a filesystem without locks) the migration goes ahead as before, with a warning."""
    lock_path = Path(str(db_path) + INIT_LOCK_SUFFIX)
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
    except OSError as exc:
        _log.warning("Library database: cannot open the lock file %s (%s); migrating without it", lock_path, exc)
    held = False
    if handle is not None:
        deadline = time.monotonic() + wait
        while True:
            try:
                _lock_handle(handle)
                held = True
                break
            except OSError as exc:
                if not _lock_busy(exc):
                    _log.warning("Library database: cannot lock %s (%s); migrating without it", lock_path, exc)
                    break
                if time.monotonic() > deadline:
                    _log.warning("Library database: still locked by another process after %.0f s; going ahead", wait)
                    break
                time.sleep(0.05)
    try:
        yield
    finally:
        if handle is not None:
            if held:
                try:
                    _unlock_handle(handle)
                except OSError:
                    pass
            handle.close()


if os.name == "nt":                                 # pragma: no cover - exercised on Windows only
    import msvcrt

    def _lock_handle(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_handle(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def _lock_busy(exc: OSError) -> bool:
        return True                                  # LK_NBLCK fails only when the byte is locked (EACCES / EDEADLOCK)
else:
    import errno
    import fcntl

    def _lock_handle(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_handle(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _lock_busy(exc: OSError) -> bool:
        return exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK)


def utcnow() -> str:
    """Timestamps are ISO-8601 UTC strings with seconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class StoreBase:
    """One SQLite file. Every call opens its own short connection (safe from worker threads) - except on the UI thread
    of a window that keeps one (:attr:`keep_ui_connection`, UI performance stage 2): there a connection is opened once
    and reused, so a click that reads or writes a setting no longer pays for opening the file, three PRAGMAs and a
    close (which checkpoints the write-ahead log when it was the last connection: 20-40 ms per call measured)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._init_lock = threading.RLock()
        self._ready = False
        self._initializing = False
        self.keep_ui_connection = False
        self._ui_con: Optional[sqlite3.Connection] = None
        self._ui_file: Optional[tuple] = None       # (device, inode) of the file it was opened on
        self._ui_depth = 0

    # --- connections -------------------------------------------------------------------------------

    def _raw_connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        con.execute("PRAGMA synchronous=NORMAL")  # WAL: durable at checkpoints, never corrupt
        return con

    def ensure_initialized(self) -> None:
        """Create / upgrade the schema and run the one-time imports, once per process. Other threads
        wait until that is done; the same thread may use the store from inside :meth:`_after_init`."""
        if self._ready and self.path.exists():
            return
        with self._init_lock:
            if self._initializing or (self._ready and self.path.exists()):
                return
            self._ready = False
            self._initializing = True
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self._schema_is_current():
                    # Nothing to create or migrate: no lock. Every ordinary open takes this path - the subprocess
                    # pool's jobs too, which on Windows otherwise met the lock of another process (CI, 2026-10-25).
                    self._after_init()
                else:
                    with init_file_lock(self.path):     # one process creates / migrates; the others wait, then see it
                        con = self._raw_connect()
                        try:
                            con.execute("PRAGMA journal_mode=WAL")
                            self._migrate(con)
                        finally:
                            con.close()
                        self._after_init()
                self._ready = True
            finally:
                self._initializing = False

    def _schema_is_current(self) -> bool:
        """True when the file exists and its schema is this build's (or newer): opening it then needs no migration and
        so no init lock. Any doubt (missing, unreadable, older) takes the locked path."""
        if not self.path.exists():
            return False
        try:
            con = self._raw_connect()
            try:
                return int(con.execute("PRAGMA user_version").fetchone()[0]) >= SCHEMA_VERSION
            finally:
                con.close()
        except sqlite3.Error:
            return False

    def _after_init(self) -> None:
        """Hook run once after the schema is current (imports of older files)."""

    def _reused_connection(self) -> Optional[sqlite3.Connection]:
        """The UI thread's kept connection (opened again when the file was replaced or a transaction was left open),
        or None where none is kept: another thread, a nested call, or not asked for."""
        if not self.keep_ui_connection or self._ui_depth or threading.current_thread() is not threading.main_thread():
            return None
        try:
            st = os.stat(self.path)
            file_id = (st.st_dev, st.st_ino)
        except OSError:
            file_id = None
        con = self._ui_con
        if con is not None and (file_id is None or file_id != self._ui_file or con.in_transaction):
            self.release_ui_connection()
            con = None
        if con is None and file_id is not None:
            con = self._ui_con = self._raw_connect()
            self._ui_file = file_id
        return con

    def release_ui_connection(self) -> None:
        """Close the UI thread's kept connection (the window closes; the store is forgotten)."""
        con, self._ui_con, self._ui_file = self._ui_con, None, None
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass

    @contextmanager
    def connect(self, durable: bool = False) -> Iterator[sqlite3.Connection]:
        """A connection inside one transaction: committed on success, rolled back on error, closed.
        *durable* syncs the commit to disk before returning (the journal's write-ahead records)."""
        self.ensure_initialized()
        kept = None if durable else self._reused_connection()
        if kept is not None:
            self._ui_depth += 1
            try:
                yield kept
                kept.commit()
            except BaseException:
                kept.rollback()
                raise
            finally:
                self._ui_depth -= 1
            return
        con = self._raw_connect()
        if durable:
            con.execute("PRAGMA synchronous=FULL")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    # --- schema ------------------------------------------------------------------------------------

    @staticmethod
    def _migrate(con: sqlite3.Connection) -> None:
        current = con.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            _log.warning("Library database is schema %d, newer than this build (%d); using it as is",
                         current, SCHEMA_VERSION)
            return
        for version, script in MIGRATIONS:
            if version <= current:
                continue
            _log.info("Library database: applying schema %d", version)
            # executescript commits first; wrap the script and the version bump in one transaction.
            con.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={int(version)};\nCOMMIT;")

    def schema_version(self) -> int:
        with self.connect() as con:
            return int(con.execute("PRAGMA user_version").fetchone()[0])

    # --- meta --------------------------------------------------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        with self.connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: Optional[str]) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
