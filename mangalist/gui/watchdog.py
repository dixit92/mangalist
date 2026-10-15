"""The event-loop watchdog (UI performance stage 2): a WARNING in the log whenever the window does not react for more
than 250 ms, with where the UI thread was at that moment - so "it feels slow" becomes log lines that name the function.

How it works: a small thread posts a ping to the UI thread every 100 ms and notes when; the UI thread answers when its
event loop gets to it. A ping not answered within the threshold means the UI thread is busy: the thread takes the UI
thread's Python stack then (``sys._current_frames``), and when the answer finally comes the watchdog logs how long the
window was blocked and that stack. Cheap: one queued call every 100 ms, nothing at all while the window is idle beyond
that; at most :data:`MAX_PER_MINUTE` lines a minute (the rest are counted in the next line).

On or off (owner, 2026-10-13, question 42: on by default in the container):

- ``MANGALIST_UI_WATCHDOG=1`` / ``0`` switches it on / off (the container image sets ``1``);
- unset (the desktop app): on while the log level is Debug (Settings > Logging), off otherwise;
- ``MANGALIST_UI_WATCHDOG_MS`` sets the threshold (default 250).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from typing import Mapping, Optional

from PySide6.QtCore import QObject, Qt, Signal

_log = logging.getLogger(__name__)

ENV_WATCHDOG = "MANGALIST_UI_WATCHDOG"
ENV_THRESHOLD = "MANGALIST_UI_WATCHDOG_MS"
THRESHOLD_MS = 250
INTERVAL_MS = 100
MAX_PER_MINUTE = 20
STACK_FRAMES = 8


def wanted(env: Optional[Mapping[str, str]] = None, log_level: Optional[str] = None) -> bool:
    """Should the watchdog run? *env* (default ``os.environ``) decides when it says so; otherwise the log level does
    (``"debug"``: on)."""
    env = os.environ if env is None else env
    value = (env.get(ENV_WATCHDOG) or "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return (log_level or "").strip().lower() == "debug"


def threshold_ms(env: Optional[Mapping[str, str]] = None) -> int:
    env = os.environ if env is None else env
    try:
        return max(50, int(str(env.get(ENV_THRESHOLD) or THRESHOLD_MS).strip()))
    except ValueError:
        return THRESHOLD_MS


def _where(frame) -> str:
    """The innermost frames of *frame*'s stack, innermost first: ``table_model.py:290 _evaluate < ...`` (code places
    only - no values, no paths beyond the file name)."""
    if frame is None:
        return "unknown"
    frames = traceback.extract_stack(frame)[-STACK_FRAMES:]
    return " < ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in reversed(frames))


class UiWatchdog(QObject):
    """See the module docstring. Create it on the UI thread, :meth:`start`, and :meth:`stop` before the application
    goes."""

    _ping = Signal(float)

    def __init__(self, threshold: int = THRESHOLD_MS, interval: int = INTERVAL_MS, logger: Optional[logging.Logger] = None):
        super().__init__()
        self.threshold = threshold / 1000
        self.interval = interval / 1000
        self.log = logger or _log
        self.blocks = 0                 # how many blocks over the threshold were seen (the tests read it)
        self._ui_ident = threading.get_ident()
        self._lock = threading.Lock()
        self._sent: Optional[float] = None      # the ping in flight (monotonic time it was posted)
        self._stack: Optional[str] = None       # where the UI thread was when that ping was late
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._logged: list = []                 # times of the lines of the last minute
        self._suppressed = 0
        self._ping.connect(self._pong, Qt.ConnectionType.QueuedConnection)

    def start(self) -> "UiWatchdog":
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="ui-watchdog", daemon=True)
            self._thread.start()
            self.log.info("UI watchdog on: a warning when the window is blocked for more than %d ms",
                          round(self.threshold * 1000))
        return self

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(1.0)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            now = time.monotonic()
            post = False
            with self._lock:
                if self._sent is None:
                    self._sent, self._stack = now, None
                    post = True
                elif self._stack is None and now - self._sent >= self.threshold:
                    self._stack = _where(sys._current_frames().get(self._ui_ident))
            if post:
                try:
                    self._ping.emit(now)
                except RuntimeError:        # the object is gone (application shutting down)
                    return

    def _pong(self, sent: float) -> None:
        now = time.monotonic()
        with self._lock:
            stack, self._sent, self._stack = self._stack, None, None
        blocked = now - sent
        if blocked < self.threshold:
            return
        self.blocks += 1
        self._logged = [t for t in self._logged if now - t < 60]
        if len(self._logged) >= MAX_PER_MINUTE:
            self._suppressed += 1
            return
        self._logged.append(now)
        more = f" ({self._suppressed} more not logged)" if self._suppressed else ""
        self._suppressed = 0
        self.log.warning("UI blocked %d ms%s; the UI thread was in: %s", round(blocked * 1000), more,
                         stack or "(it answered before the stack was taken)")


def start_if_wanted(log_level: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> Optional[UiWatchdog]:
    """The application's watchdog when :func:`wanted`, started; else None."""
    if not wanted(env, log_level):
        return None
    return UiWatchdog(threshold_ms(env)).start()


__all__ = ["UiWatchdog", "start_if_wanted", "wanted", "threshold_ms", "ENV_WATCHDOG", "ENV_THRESHOLD"]
