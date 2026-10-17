"""The open window retries the MangaPixer scans MangaPixer held back, when they are due (owner, 2026-10-17)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mangalist.gui.scan_retry import MARGIN_MS, MAX_WAIT_MS, ScanRetry

NOW = datetime(2026, 10, 17, 4, 46, tzinfo=timezone.utc)


class _Call:
    abandoned = False

    def abandon(self):
        self.abandoned = True


def _retry(qapp, answers):
    """A ScanRetry whose background calls run at once; *answers* are what each retry returns."""
    asked = []

    def start(fn, on_done, on_error):
        asked.append(1)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            on_error(str(answer))
        else:
            on_done(fn() if answer is None else answer)
        return None

    r = ScanRetry(object(), retry=lambda db: ("", None), start=start, now=lambda: NOW)
    return r, asked


def test_a_held_back_scan_is_asked_again_when_due_and_the_retry_stops_when_none_is_pending(qapp):
    r, asked = _retry(qapp, [("", NOW + timedelta(minutes=4)), ("MangaPixer scans: 1 started", None)])
    r.check()
    assert r.armed and r._timer.interval() == 0
    r._fire()                                                       # the timer: look now
    assert asked == [1] and r.armed
    assert r._timer.interval() == 4 * 60 * 1000 + MARGIN_MS         # MangaPixer's time, a little after
    r._fire()                                                       # due: asked again, started - nothing pending
    assert asked == [1, 1] and not r.armed


def test_never_sooner_than_the_margin_and_never_a_wait_past_an_hour(qapp):
    r, _ = _retry(qapp, [("", NOW - timedelta(minutes=3)), ("", NOW + timedelta(days=2))])
    r._fire()
    assert r._timer.interval() == MARGIN_MS                         # overdue: in two seconds, not in a loop
    r._fire()
    assert r._timer.interval() == MAX_WAIT_MS


def test_a_failed_retry_leaves_it_to_the_hourly_pass_and_stop_ends_it(qapp):
    r, asked = _retry(qapp, [RuntimeError("unreachable")])
    r._fire()
    assert asked == [1] and not r.armed
    r, asked = _retry(qapp, [])
    call = _Call()
    r._call = call                                                  # a retry running
    r._fire()
    assert asked == []                                              # one at a time
    r.stop()
    assert call.abandoned and not r.armed
    r.check()
    assert not r.armed                                              # stopped for good (the window closed)

