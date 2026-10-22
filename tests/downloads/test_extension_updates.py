"""The extension update check (lane U, 2026-10-22; :mod:`mangalist.downloads.extension_updates`): what the host says
needs an update, Auto-update installing it (one by one; a failure keeps the old version and is reported; a source that
is downloading makes it wait for the next check), the stored result the notices read, and when the window checks. The
real client talks to the in-process fake host: no JVM, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mangalist.downloads import extension_updates as eu
from mangalist.services.extensions import ExtensionHostClient, HostUnreachable

from ..services.extensions.fake_host import MANGADEX_PKG, WEEB_PKG, FakeHost

NOW = datetime(2026, 10, 22, 5, 15, tzinfo=timezone.utc)


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "exthost", auto_download=False).start()
    yield h
    h.stop()


@pytest.fixture
def client(host):
    c = ExtensionHostClient(host.url, timeout=10)
    yield c
    c.close()


def installed(host, **versions):
    for pkg, version in versions.items():
        host.installed[pkg] = version


def test_no_updates_is_stored_and_said(db, host, client):
    installed(host, **{MANGADEX_PKG: "1.4.200"})
    check = eu.run_check(db, client, now=NOW)
    assert check.ok and check.count == 0 and check.installed == 1 and not check.updated
    stored = eu.read_check(db)
    assert stored == check and stored.checked == NOW
    assert check.short_text() == ""                                  # the Download tab shows nothing
    assert check.detail_text(NOW).startswith("Extensions up to date (1 installed)\nChecked today")
    assert check.summary() == "1 installed extension, all up to date"


def test_updates_found_are_listed_and_nothing_is_installed_without_auto_update(db, host, client):
    installed(host, **{MANGADEX_PKG: "1.4.199", WEEB_PKG: "1.4.1"})
    check = eu.run_check(db, client, now=NOW)
    assert [(p.name, p.version, p.new_version, p.repo_name) for p in check.pending] == [
        ("MangaDex", "1.4.199", "1.4.200", "Keiyoushi"), ("Weeb Example", "1.4.1", "1.4.3", "Keiyoushi")]
    assert host.installed == {MANGADEX_PKG: "1.4.199", WEEB_PKG: "1.4.1"}           # nothing installed
    assert check.short_text() == "2 extension updates"
    assert "2 extension updates: MangaDex 1.4.199 → 1.4.200, Weeb Example 1.4.1 → 1.4.3" in check.detail_text(NOW)
    assert check.summary().startswith("2 updates available: MangaDex 1.4.199 -> 1.4.200")


def test_auto_update_installs_them_and_the_notice_says_what_was_updated(db, host, client):
    installed(host, **{MANGADEX_PKG: "1.4.199", WEEB_PKG: "1.4.1"})
    check = eu.run_check(db, client, auto=True, now=NOW)
    assert host.installed == {MANGADEX_PKG: "1.4.200", WEEB_PKG: "1.4.3"}
    assert [(u.name, u.old_version, u.new_version) for u in check.updated] == [
        ("MangaDex", "1.4.199", "1.4.200"), ("Weeb Example", "1.4.1", "1.4.3")]
    assert check.pending == [] and check.failed == [] and check.updated_at == check.checked_at
    assert check.short_text() == "2 extensions updated automatically"
    assert "Updated automatically (today" in check.detail_text(NOW) and "Auto-update on" in check.detail_text(NOW)
    # the next day's check finds nothing new: the notice still says what was updated (for a week)
    later = eu.run_check(db, client, auto=True, now=NOW + timedelta(days=1))
    assert [u.name for u in later.updated] == ["MangaDex", "Weeb Example"] and later.updated_at == check.updated_at
    assert "updated automatically" not in later.summary()                    # the log line is this run's
    gone = eu.run_check(db, client, auto=True, now=NOW + timedelta(days=8))
    assert gone.updated == [] and gone.short_text() == ""


def test_a_failed_auto_update_keeps_the_old_version_and_is_reported(db, host, client):
    installed(host, **{MANGADEX_PKG: "1.4.199", WEEB_PKG: "1.4.1"})
    host.update_errors[WEEB_PKG] = "signed by abc, not by the repository's key 9add: not installed"
    check = eu.run_check(db, client, auto=True, now=NOW)
    assert host.installed == {MANGADEX_PKG: "1.4.200", WEEB_PKG: "1.4.1"}        # the old version stays
    assert [u.name for u in check.updated] == ["MangaDex"]
    assert [(f.name, f.waiting) for f in check.failed] == [("Weeb Example", False)]
    assert "not by the repository's key" in check.failed[0].error
    assert [p.name for p in check.pending] == ["Weeb Example"]               # still to update
    assert check.short_text() == "1 extension update · 1 extension updated automatically · 1 extension could not be updated"
    assert "Weeb Example: not updated (signed by abc" in check.detail_text(NOW)


def test_an_extension_whose_source_downloads_waits_for_the_next_check(db, host, client):
    installed(host, **{WEEB_PKG: "1.4.1"})
    host.busy.add(WEEB_PKG)
    check = eu.run_check(db, client, auto=True, now=NOW, wait_seconds=1)
    assert host.installed == {WEEB_PKG: "1.4.1"} and check.errors == []
    assert [(f.name, f.waiting) for f in check.failed] == [("Weeb Example", True)]
    assert "waits for its download" in check.detail_text(NOW)
    host.busy.clear()
    assert eu.run_check(db, client, auto=True, now=NOW + timedelta(days=1)).updated[0].new_version == "1.4.3"


def test_a_host_that_cannot_be_asked_is_stored_and_raised(db, host, client):
    installed(host, **{WEEB_PKG: "1.4.1"})
    eu.run_check(db, client, now=NOW)
    down = ExtensionHostClient("http://127.0.0.1:9", timeout=2)
    with pytest.raises(HostUnreachable):
        eu.run_check(db, down, now=NOW + timedelta(hours=1))
    stored = eu.read_check(db)
    assert not stored.ok and "could not be reached" in stored.error
    assert [p.name for p in stored.pending] == ["Weeb Example"]               # what was known is kept
    assert stored.detail_text(NOW).startswith("Extension updates: the host could not be asked")


def test_due_by_the_stored_result_and_the_interval(db, host, client):
    assert eu.is_due(db, timedelta(days=1), now=NOW)                         # never checked
    eu.run_check(db, client, now=NOW)
    assert not eu.is_due(db, timedelta(days=1), now=NOW + timedelta(hours=23))
    assert eu.is_due(db, timedelta(days=1), now=NOW + timedelta(hours=24))
    assert not eu.is_due(db, None, now=NOW + timedelta(days=30))             # the schedule is off


def test_an_unreadable_stored_result_is_none(db):
    db.set_meta(eu.META_KEY, "{not json")
    assert eu.read_check(db) is None
    db.set_meta(eu.META_KEY, '{"checked_at": "2026-10-22T05:15:00+00:00", "pending": [{"pkg": "p", "name": "P", '
                             '"version": "1", "new_version": "2", "unknown": 1}, "junk"]}')
    assert [p.name for p in eu.read_check(db).pending] == ["P"]
