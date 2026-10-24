"""The ``extension-updates`` job (lane U, 2026-10-22): skipped unless "Built-in extensions" is chosen and the host is up;
otherwise the update check (and, with Auto-update on, the updates) with the runner's record. In-process fake host."""

from __future__ import annotations

from datetime import timedelta

import pytest

from mangalist import store
from mangalist.downloads import extension_updates as eu
from mangalist.downloads.contracts import CHAPTER_BACKEND_EXTENSIONS
from mangalist.headless.extension_updates_job import JOB_NAME, make_extension_updates, schedule_interval
from mangalist.headless.jobs import JobContext
from mangalist.store.downloads import ExtensionHostConfig, SuwayomiSettings

from ..services.extensions.fake_host import MANGADEX_PKG, WEEB_PKG, FakeHost


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "exthost", auto_download=False).start()
    yield h
    h.stop()


def choose(db, url: str) -> SuwayomiSettings:
    s = SuwayomiSettings(db)
    s.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    s.save_extension_host(ExtensionHostConfig(url=url))
    return s


def run(db):
    return make_extension_updates(open_db=lambda: db)(JobContext())


def test_skipped_while_chapter_downloads_go_through_suwayomi(db, host):
    host.installed[WEEB_PKG] = "1.4.1"
    SuwayomiSettings(db).save_extension_host(ExtensionHostConfig(url=host.url))
    result = run(db)
    assert result.status == "skipped" and "Built-in extensions is not chosen" in result.message
    assert eu.read_check(db) is None and host.requests == []


def test_skipped_while_the_host_is_down(db):
    choose(db, "http://127.0.0.1:9")
    result = run(db)
    assert result.status == "skipped" and "not running" in result.message and eu.read_check(db) is None


def test_updates_found_are_recorded_for_the_notices(db, host):
    choose(db, host.url)
    host.installed.update({MANGADEX_PKG: "1.4.199", WEEB_PKG: "1.4.3"})
    result = run(db)
    assert result.status == "ok" and result.message.startswith("1 update available: MangaDex 1.4.199 -> 1.4.200")
    assert [p["name"] for p in result.extra["pending"]] == ["MangaDex"] and result.extra["auto"] is False
    assert eu.read_check(db).count == 1 and host.installed[MANGADEX_PKG] == "1.4.199"


def test_auto_update_installs_and_a_failure_makes_the_run_an_error(db, host):
    s = choose(db, host.url)
    s.set_extension_auto_update(True)
    host.installed.update({MANGADEX_PKG: "1.4.199", WEEB_PKG: "1.4.1"})
    host.update_errors[WEEB_PKG] = "signature check failed"
    result = run(db)
    assert result.status == "error" and "updated automatically: MangaDex 1.4.199 -> 1.4.200" in result.message
    assert "Weeb Example not updated: signature check failed" in result.message
    assert host.installed == {MANGADEX_PKG: "1.4.200", WEEB_PKG: "1.4.1"}
    assert [u["name"] for u in result.extra["updated"]] == ["MangaDex"]
    host.update_errors.clear()
    again = run(db)
    assert again.status == "ok" and host.installed[WEEB_PKG] == "1.4.3"


def test_the_interval_follows_the_schedule(db):
    from mangalist.headless.settings import SCHEDULE_BY_JOB, save_schedule

    assert schedule_interval(db, {}) == timedelta(days=1)                         # daily by default
    assert schedule_interval(db, {"MANGALIST_EXTENSION_UPDATES_SCHEDULE": "every 12h"}) == timedelta(hours=12)
    save_schedule(db, SCHEDULE_BY_JOB[JOB_NAME], "weekly@sun 05:15")
    assert schedule_interval(db, {}) == timedelta(days=7)
    save_schedule(db, SCHEDULE_BY_JOB[JOB_NAME], "off")
    assert schedule_interval(db, {}) is None
