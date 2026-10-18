"""The event-loop watchdog (UI performance stage 2): a WARNING naming where the UI thread was when it blocks past the
threshold; nothing while the window reacts; on in the container, off on the desktop unless asked."""

from __future__ import annotations

import logging
import sys
import time

import pytest

pytest.importorskip("PySide6")

from mangalist.gui import watchdog  # noqa: E402

from .conftest import qapp, wait_until  # noqa: E402,F401


def _busy_for(seconds: float) -> None:
    time.sleep(seconds)


def test_a_blocked_ui_thread_is_logged_with_where_it_was(qapp, caplog):
    caplog.set_level(logging.WARNING, logger="mangalist.gui.watchdog")
    dog = watchdog.UiWatchdog(threshold=80, interval=10).start()
    try:
        for _ in range(20):                         # reacting (a slow CI runner may still stall once: not asserted)
            qapp.processEvents()
            time.sleep(0.01)
        before = dog.blocks
        _busy_for(0.4)                              # the UI thread does not get to its event loop
        wait_until(qapp, lambda: dog.blocks > before, 5)
    finally:
        dog.stop()
    line = next(r.getMessage() for r in reversed(caplog.records) if "_busy_for" in r.getMessage())
    assert "test_watchdog.py" in line and "_busy_for" in line


@pytest.mark.parametrize("env, level, on", [
    ({"MANGALIST_UI_WATCHDOG": "1"}, "info", True),           # the container image sets it
    ({"MANGALIST_UI_WATCHDOG": "0"}, "debug", False),
    ({}, "info", False),                                       # the desktop: off ...
    ({}, "debug", True),                                       # ... unless the log level is Debug
])
def test_on_or_off(env, level, on):
    assert watchdog.wanted(env, level) is on


def test_the_threshold_can_be_set():
    assert watchdog.threshold_ms({}) == 250 and watchdog.threshold_ms({"MANGALIST_UI_WATCHDOG_MS": "400"}) == 400
    assert watchdog.threshold_ms({"MANGALIST_UI_WATCHDOG_MS": "x"}) == 250


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="True and False are immortal from Python 3.12: a lost "
                                                         "reference cannot be seen (nor do harm) there")
def test_a_signal_emit_keeps_the_reference_counts_of_true_and_false(qapp):
    # owner's container (Debian 12's Python 3.11), 2026-10-15 to 2026-10-17: the window aborted every ~82 min ("Fatal
    # Python error: bool_dealloc: deallocating True or False"). PySide6 6.12.0's emit() returns a bool without its
    # reference, and the watchdog emits ten times a second - requirements.txt keeps PySide6 below 6.12; this fails on
    # a version with that bug (CI's Python 3.10 / 3.11 jobs run it).
    from PySide6.QtCore import QObject, Qt, Signal

    class Pinger(QObject):
        ping = Signal(float)

        def pong(self, _value: float) -> None:
            pass

    pinger = Pinger()
    pinger.ping.connect(pinger.pong, Qt.ConnectionType.QueuedConnection)
    before = sys.getrefcount(True), sys.getrefcount(False)
    for _ in range(500):
        pinger.ping.emit(1.0)
    qapp.processEvents()
    after = sys.getrefcount(True), sys.getrefcount(False)
    assert after[0] >= before[0] - 50 and after[1] >= before[1] - 50, f"True / False lost references: {before} -> {after}"
