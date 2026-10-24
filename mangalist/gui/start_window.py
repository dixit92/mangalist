"""The minute after the owner switches the chapter backend to the built-in extensions (transition fixes A6, 2026-10-24).

The extension host is started by its supervisor, which looks at the setting every 15 seconds, and the JVM then needs a few
seconds more. A check made at once after the switch therefore fails, and the owner saw a red "Not connected" for a host
that was only starting. While this window is open a failed check is "Starting..." and is tried again every
:data:`RETRY_MS`; once :data:`WINDOW_S` has passed a failure is a failure. Each page that checks the host has its own
window (a test opens and closes it without touching another's). No Qt.
"""

from __future__ import annotations

import time
from typing import Callable

WINDOW_S = 75.0         # the supervisor's 15 s poll, the JVM's start, and some room
RETRY_MS = 5000


class StartWindow:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._until = 0.0

    def open(self) -> None:
        self._until = self._clock() + WINDOW_S

    def close(self) -> None:
        self._until = 0.0

    def active(self) -> bool:
        return self._clock() < self._until
