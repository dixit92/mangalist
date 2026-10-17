"""Retry the MangaPixer scan requests MangaPixer held back, while the window is open (owner, 2026-10-17).

After a Rename (or an Add series) MangaList asks MangaPixer to scan the library. Inside MangaPixer's 5-minute cooldown,
or while a scan runs, the request is kept pending with the time it may be tried again; only the headless downloads pass
(hourly) retried it, so a renamed series could wait an hour to show up in MangaPixer. :class:`ScanRetry` wakes at that
time and asks again - never sooner, one request at a time, and it stops when nothing is pending.
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


class ScanRetry(QObject):
    """Wakes when the next pending MangaPixer scan request is due and asks again (``retry(db) -> (summary, next due)``,
    run on a background thread)."""

    def __init__(self, db, parent: Optional[QObject] = None, *,
                 retry: Optional[Callable[[Any], Tuple[str, Optional[datetime]]]] = None,
                 start: Callable[..., Any] = start_call,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        super().__init__(parent)
        if retry is None:
            from ..renamer import retry_mangapixer_scans as retry
        self._db = db
        self._retry = retry
        self._start = start
        self._now = now
        self._call = None
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)

    @property
    def armed(self) -> bool:
        return self._timer.isActive()

    def check(self) -> None:
        """Something may have left a request pending (a rename, an add): look now, then wait for the next due one."""
        if not self._stopped:
            self._timer.start(0)

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()
        if self._call is not None and hasattr(self._call, "abandon"):
            self._call.abandon()
        self._call = None

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

    def _arm(self, due: Optional[datetime]) -> None:
        if self._stopped or due is None:
            return
        ms = int((due - self._now()).total_seconds() * 1000) + MARGIN_MS
        self._timer.start(max(MARGIN_MS, min(ms, MAX_WAIT_MS)))
