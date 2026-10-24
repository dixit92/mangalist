"""The extension host on demand while Suwayomi is chosen (transition audit C1): a call that needs the host asks the
container's supervisor for it and waits; outside the container it says at once why it cannot. No JVM, a fake probe and
clock."""

from __future__ import annotations

import pytest

from mangalist import store
from mangalist.downloads.contracts import CHAPTER_BACKEND_EXTENSIONS
from mangalist.services.extensions import on_demand
from mangalist.services.extensions.client import HostUnreachable
from mangalist.store.downloads import SuwayomiSettings

URL = "http://127.0.0.1:4570"


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


class Probe:
    def __init__(self, up_after: int = 0):
        self.calls = 0
        self.up_after = up_after

    def __call__(self):
        self.calls += 1
        if self.calls <= self.up_after:
            raise HostUnreachable("not yet")
        return {"status": "ok"}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_with_suwayomi_chosen_the_host_is_asked_for_and_waited_for(db):
    probe, clock = Probe(up_after=3), Clock()
    on_demand.ensure_running(db, URL, probe=probe, env={"MANGALIST_EXTENSION_HOST": "auto"}, sleep=clock.sleep,
                             clock=clock)
    assert on_demand.is_wanted(db) and probe.calls == 4 and clock.now == 3 * on_demand.POLL_SECONDS


def test_a_host_that_does_not_start_is_said(db):
    clock = Clock()
    with pytest.raises(on_demand.HostNotRunning, match="did not start within 10 s"):
        on_demand.ensure_running(db, URL, probe=Probe(up_after=10 ** 6), env={"MANGALIST_EXTENSION_HOST": "auto"},
                                 wait=10, sleep=clock.sleep, clock=clock)


def test_outside_the_container_or_switched_off_it_says_so_at_once(db):
    with pytest.raises(on_demand.HostNotRunning, match="started by hand"):
        on_demand.ensure_running(db, URL, probe=Probe(up_after=1), env={})
    with pytest.raises(on_demand.HostNotRunning, match="MANGALIST_EXTENSION_HOST=0"):
        on_demand.ensure_running(db, URL, probe=Probe(up_after=1), env={"MANGALIST_EXTENSION_HOST": "0"})
    assert not on_demand.is_wanted(db)
    on_demand.ensure_running(db, URL, probe=Probe(), env={})             # a host started by hand: fine


def test_with_built_in_extensions_chosen_nothing_is_asked(db):
    SuwayomiSettings(db).set_backend(CHAPTER_BACKEND_EXTENSIONS)
    probe = Probe(up_after=10 ** 6)
    on_demand.ensure_running(db, URL, probe=probe, env={"MANGALIST_EXTENSION_HOST": "auto"})
    assert probe.calls == 0 and not on_demand.is_wanted(db)


def test_a_request_never_shortens_a_later_one(db):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    on_demand.request(db, minutes=60, now=now)
    on_demand.request(db, minutes=30, now=now)
    assert on_demand.wanted_until(db) == now + timedelta(minutes=60)
    assert not on_demand.is_wanted(db, now=now + timedelta(minutes=61))


def test_the_extensions_page_calls_ask_for_the_host(db, monkeypatch):
    """The GUI backend's host calls go through it (the Extensions page, Test connection)."""
    from mangalist.downloads.adapter import Backend
    from mangalist.gui.downloads_backend import BackendError

    backend = Backend(db)
    backend._host_probe = Probe(up_after=10 ** 6)
    monkeypatch.delenv("MANGALIST_EXTENSION_HOST", raising=False)
    with pytest.raises(BackendError, match="started by hand"):
        backend.installed_extensions()
