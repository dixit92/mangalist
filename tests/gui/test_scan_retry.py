"""The open window retries the MangaPixer scans MangaPixer held back, when they are due (owner, 2026-10-17)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mangalist.gui.scan_retry import MARGIN_MS, MAX_WAIT_MS, SYNC_DELAY_MS, ScanRetry

NOW = datetime(2026, 10, 17, 4, 46, tzinfo=timezone.utc)


class _Call:
    abandoned = False

    def abandon(self):
        self.abandoned = True


def _retry(qapp, answers, synced=None):
    """A ScanRetry whose background calls run at once; *answers* are what each retry returns (a sync answers
    ``("ok: ...", 7)``); *synced* collects the carry ids handed to ``on_synced``."""
    asked = []

    def start(fn, on_done, on_error):
        asked.append(1)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            on_error(str(answer))
        else:
            on_done(fn() if answer is None else answer)
        return None

    r = ScanRetry(object(), retry=lambda db: ("", None), sync=lambda db: ("ok: 2 libraries", 7),
                  on_synced=(synced.append if synced is not None else None), start=start, now=lambda: NOW)
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



def test_after_a_rename_mangapixer_is_read_again_once_every_scan_has_started(qapp):
    # owner, 2026-10-17: renamed series showed "needs review" until the 12-hourly sync
    synced = []
    r, asked = _retry(qapp, [("", NOW + timedelta(minutes=4)), ("MangaPixer scans: 1 started", None), ("", None),
                             ("ok: 2 libraries", 7)], synced)                # retry, retry, retry, then the sync
    r.check(sync_after=True)
    r._fire()                                                       # still pending: no sync yet
    assert r.armed and not r.sync_armed
    r._fire()                                                       # started: the sync waits for MangaPixer's scan
    assert not r.armed and r.sync_armed and r._sync_timer.interval() == SYNC_DELAY_MS
    r.check(sync_after=True)                                        # another batch: one sync after the last one
    assert not r.sync_armed
    r._fire()
    assert r.sync_armed
    r._fire_sync()
    assert synced == [7] and not r.sync_armed and asked == [1, 1, 1, 1]
    r._fire_sync()                                                  # once only
    assert synced == [7]


def test_an_add_or_a_check_without_a_rename_does_not_sync(qapp):
    synced = []
    r, _ = _retry(qapp, [("", None)], synced)
    r.check()
    r._fire()
    assert not r.sync_armed and synced == []
