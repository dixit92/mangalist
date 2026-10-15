"""The event-loop watchdog (UI performance stage 2): a WARNING naming where the UI thread was when it blocks past the
threshold; nothing while the window reacts; on in the container, off on the desktop unless asked."""

from __future__ import annotations

import logging
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
        wait_until(qapp, lambda: dog._sent is not None or True, 1)
        for _ in range(20):                         # reacting: nothing logged
            qapp.processEvents()
            time.sleep(0.01)
        assert dog.blocks == 0
        _busy_for(0.4)                              # the UI thread does not get to its event loop
        wait_until(qapp, lambda: dog.blocks >= 1, 5)
    finally:
        dog.stop()
    line = next(r.getMessage() for r in caplog.records if "UI blocked" in r.getMessage())
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
