"""The library database file: connections, migrations, the ``meta`` table."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .schema import MIGRATIONS, SCHEMA_VERSION

_log = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 10_000


def utcnow() -> str:
    """Timestamps are ISO-8601 UTC strings with seconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class StoreBase:
    """One SQLite file. Every call opens its own short connection (safe from worker threads)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._init_lock = threading.RLock()
        self._ready = False
        self._initializing = False

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

    def _after_init(self) -> None:
        """Hook run once after the schema is current (imports of older files)."""

    @contextmanager
    def connect(self, durable: bool = False) -> Iterator[sqlite3.Connection]:
        """A connection inside one transaction: committed on success, rolled back on error, closed.
        *durable* syncs the commit to disk before returning (the journal's write-ahead records)."""
        self.ensure_initialized()
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
