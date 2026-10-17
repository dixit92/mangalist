"""The busy indicator: a small spinner for a UI element that waits for background work and can take over half a second
(owner, 2026-10-16, answer 80: "OK, keep a spinner if measured time for any UI element can go over 0.5s").

One shared clock (:func:`clock`) drives every spinner on screen: it is a single ``QTimer`` that runs only while something
is registered with it, and each client repaints only its own busy cells (a few hundred pixels), never a whole table.
The spinner's angle comes from the wall clock (:func:`angle`), so every spinner turns in step whatever the tick rate.

Clients (:meth:`BusyClock.watch`): the List's State cell of a series that has no state yet
(:class:`~.list_delegates.StateBadgeDelegate`), a chip's / the Download tab's count while the number is being worked out
(:class:`~.chips.ChipButton`, :class:`~.chips.TabButton`). A client is held by a weak reference and goes when it
returns False (nothing left to animate) or when its widget is deleted, so a stopped spinner costs nothing."""

from __future__ import annotations

import time
import weakref
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen

TICK_MS = 80                    # about 12 frames a second: smooth enough for a 14 px ring, cheap to repaint
TURN_MS = 900                   # one turn of the arc
ARC_SPAN = 100                  # degrees of the bright arc


def angle(now: Optional[float] = None) -> float:
    """The spinner's start angle in degrees (clockwise from 12 o'clock) at *now* (``time.monotonic()``)."""
    t = time.monotonic() if now is None else now
    return (t * 1000.0 % TURN_MS) / TURN_MS * 360.0


def paint_spinner(painter: QPainter, rect: QRectF, color: str, track: Optional[str] = None,
                  degrees: Optional[float] = None) -> None:
    """A ring in *track* with a bright arc of *color* turning on it, fitted in *rect* (square, centred)."""
    side = min(rect.width(), rect.height())
    width = max(1.6, side / 7.0)
    box = QRectF(rect.center().x() - side / 2 + width / 2, rect.center().y() - side / 2 + width / 2,
                 side - width, side - width)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if track:
        ring = QColor(track)
        painter.setPen(QPen(ring, width))
        painter.drawEllipse(box)
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    start = 90 - (angle() if degrees is None else degrees)         # Qt: 0 at 3 o'clock, counter-clockwise
    painter.drawArc(box, int(start * 16), int(-ARC_SPAN * 16))
    painter.restore()


class BusyClock(QObject):
    """The shared timer. ``watch(callback)``: *callback()* is called every tick until it returns False."""

    def __init__(self, interval_ms: int = TICK_MS, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._clients: List[object] = []                # weakref.WeakMethod / weakref.ref
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.tick)
        self.ticks = 0                                  # how many ticks ran (the tests)

    @property
    def active(self) -> bool:
        """Is the timer running (some client is registered)?"""
        return self._timer.isActive()

    def clients(self) -> int:
        return len(self._clients)

    def watch(self, callback: Callable[[], Optional[bool]]) -> None:
        """Register *callback* (a bound method or a function kept alive elsewhere); registering twice is harmless.
        Starts the timer when it was stopped."""
        try:
            ref = weakref.WeakMethod(callback)              # a bound method: its owner is not kept alive by the clock
        except TypeError:
            ref = lambda: callback                          # noqa: E731 - a plain function / builtin: held until unwatched
        for known in self._clients:
            if known() == callback:
                break
        else:
            self._clients.append(ref)
        if self._clients and not self._timer.isActive():
            self._timer.start()

    def unwatch(self, callback: Callable[[], Optional[bool]]) -> None:
        self._clients = [c for c in self._clients if c() not in (None, callback)]
        if not self._clients:
            self._timer.stop()

    def tick(self) -> None:
        """One frame: every client repaints its busy parts; those with nothing left (False) or gone are dropped, and
        the timer stops with the last one."""
        self.ticks += 1
        keep = []
        for ref in self._clients:
            fn = ref()
            if fn is None:
                continue
            try:
                alive = fn()
            except RuntimeError:                        # its widget was deleted under it
                alive = False
            if alive is not False:
                keep.append(ref)
        self._clients = keep
        if not keep:
            self._timer.stop()


_clock: Optional[BusyClock] = None


def clock() -> BusyClock:
    """The one shared clock (made on first use; needs the application's event loop to tick)."""
    global _clock
    from shiboken6 import isValid

    if _clock is None or not isValid(_clock):           # (a test run's application was destroyed with its clock)
        from PySide6.QtWidgets import QApplication

        _clock = BusyClock(parent=QApplication.instance())
    return _clock


__all__ = ["BusyClock", "angle", "clock", "paint_spinner"]
