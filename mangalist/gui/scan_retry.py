"""Retry the MangaPixer scan requests MangaPixer held back, while the window is open (owner, 2026-10-17).

After a Rename (or an Add series) MangaList asks MangaPixer to scan the library. Inside MangaPixer's 5-minute cooldown,
or while a scan runs, the request is kept pending with the time it may be tried again; only the headless downloads pass
(hourly) retried it, so a renamed series could wait an hour to show up in MangaPixer. :class:`ScanRetry` wakes at that
time and asks again - never sooner, one request at a time, and it stops when nothing is pending.

After a Rename it also re-reads MangaPixer's export (owner, 2026-10-17): MangaPixer matches folders by name, so a renamed
series read "needs review" until the 12-hourly sync. Once nothing is pending (every requested scan started), a sync runs
:data:`SYNC_DELAY_MS` later - batches in a row share one sync.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

from PySide6.QtCore import QObject, QTimer

from .background import start_call

_log = logging.getLogger(__name__)

#: A little after the time MangaPixer gave, so the cooldown is surely over.
MARGIN_MS = 2000
#: The longest single wait (a QTimer interval is an int of milliseconds; a far-off time is re-checked).
MAX_WAIT_MS = 60 * 60 * 1000
#: After the last requested scan started: time for MangaPixer to scan and carry the folders over, then sync.
SYNC_DELAY_MS = 2 * 60 * 1000


class ScanRetry(QObject):
    """Wakes when the next pending MangaPixer scan request is due and asks again (``retry(db) -> (summary, next due)``,
    run on a background thread)."""

    def __init__(self, db, parent: Optional[QObject] = None, *,
                 retry: Optional[Callable[[Any], Tuple[str, Optional[datetime]]]] = None,
                 sync: Optional[Callable[[Any], Tuple[str, int]]] = None,
                 on_synced: Optional[Callable[[int], None]] = None,
                 start: Callable[..., Any] = start_call,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        super().__init__(parent)
        if retry is None:
            from ..renamer import retry_mangapixer_scans as retry
        if sync is None:
            from ..renamer import sync_mangapixer_after_rename as sync
        self._db = db
        self._retry = retry
        self._sync = sync
        self._on_synced = on_synced
        self._sync_wanted = False
        self._sync_call = None
        self._start = start
        self._now = now
        self._call = None
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._fire_sync)

    @property
    def armed(self) -> bool:
        return self._timer.isActive()

    @property
    def sync_armed(self) -> bool:
        return self._sync_timer.isActive()

    def check(self, sync_after: bool = False) -> None:
        """Something may have left a request pending (a rename, an add): look now, then wait for the next due one.
        *sync_after*: files were renamed - re-read MangaPixer once its scans have started."""
        if self._stopped:
            return
        if sync_after:
            self._sync_wanted = True
            self._sync_timer.stop()             # a batch in a row: one sync after the last one
        self._timer.start(0)

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()
        self._sync_timer.stop()
        for call in (self._call, self._sync_call):
            if call is not None and hasattr(call, "abandon"):
                call.abandon()
        self._call = self._sync_call = None

    def _fire(self) -> None:
        self._timer.stop()
        if self._stopped or self._call is not None:
            return
        self._call = self._start(lambda: self._retry(self._db), self._done, self._failed)

    def _done(self, result) -> None:
        self._call = None
        summary, due = result if result else ("", None)
        if summary:
            _log.info("Pending %s", summary)
        self._arm(due)

    def _failed(self, message: str) -> None:
        self._call = None
        _log.info("Retrying the pending MangaPixer scans failed: %s", message)      # the hourly pass still retries
        self._arm(None)

    def _arm(self, due: Optional[datetime]) -> None:
        if self._stopped:
            return
        if due is None:                         # nothing pending: every requested scan has started
            if self._sync_wanted and not self._sync_timer.isActive() and self._sync_call is None:
                self._sync_timer.start(SYNC_DELAY_MS)
            return
        ms = int((due - self._now()).total_seconds() * 1000) + MARGIN_MS
        self._timer.start(max(MARGIN_MS, min(ms, MAX_WAIT_MS)))

    def _fire_sync(self) -> None:
        self._sync_timer.stop()
        if self._stopped or self._sync_call is not None or not self._sync_wanted:
            return
        self._sync_wanted = False
        self._sync_call = self._start(lambda: self._sync(self._db), self._synced, self._sync_failed)

    def _synced(self, result) -> None:
        self._sync_call = None
        message, carry_before = result if result else ("", 0)
        _log.info("MangaPixer re-read after a rename: %s", message)
        if self._on_synced is not None and not self._stopped:
            self._on_synced(carry_before)
        self._arm(None)                         # another rename meanwhile: its own sync

    def _sync_failed(self, message: str) -> None:
        self._sync_call = None
        _log.info("Re-reading MangaPixer after a rename failed: %s", message)      # the 12-hourly sync still runs
