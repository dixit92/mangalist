"""Library scans on request (MangaPixer 1.36.0): the client's answers and the retry-later bookkeeping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mangalist.services.mangapixer import client as mpc
from mangalist.services.mangapixer.scans import libraries_for_series, request_scans
from mangalist.store.mangapixer import Mapping

from .conftest import TOKEN, add_root_with_series

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def test_the_client_maps_every_scan_answer(fake, client_factory):
    c = client_factory(fake.url)
    got = c.request_scan("lib0manga")
    assert got.outcome == mpc.SCAN_STARTED and got.run_id == "run1" and fake.scans == ["lib0manga"]
    fake.queue.append(("scan", 409, {"error": "scan_in_progress"}, {"Retry-After": "60"}))
    assert c.request_scan("lib0manga") == mpc.ScanRequest(mpc.SCAN_BUSY, retry_after=60.0)
    fake.queue.append(("scan", 429, {"error": "scan_cooldown"}, {"Retry-After": "240"}))
    assert c.request_scan("lib0manga") == mpc.ScanRequest(mpc.SCAN_COOLDOWN, retry_after=240.0)
    with pytest.raises(mpc.LibraryNotFound):
        c.request_scan("gone")
    fake.scan_scope = False
    assert c.request_scan("lib0manga").outcome == mpc.SCAN_FORBIDDEN
    assert [r["method"] for r in fake.requests] == ["POST"] * 5
    assert all(TOKEN not in r["path"] for r in fake.requests)          # never in the URL
    with pytest.raises(mpc.TokenRejected):
        client_factory(fake.url, token="mpx_wrong").request_scan("lib0manga")


def _client(connected, client_factory):
    return client_factory(connected.connection().base_url)


def test_a_started_scan_clears_the_request(connected, fake, client_factory):
    report = request_scans(connected, ["lib0manga"], client=_client(connected, client_factory), now=NOW)
    assert report.started == ["lib0manga"] and connected.pending_scans() == {} and fake.scans == ["lib0manga"]
    assert report.summary() == "MangaPixer scans: 1 started"


def test_busy_waits_for_retry_after_then_tries_again(connected, fake, client_factory):
    c = _client(connected, client_factory)
    fake.queue.append(("scan", 409, {"error": "scan_in_progress"}, {"Retry-After": "60"}))
    report = request_scans(connected, ["lib0manga"], client=c, now=NOW)
    assert report.pending == [("lib0manga", "2026-10-08T12:01:00Z")] and fake.scans == []
    request_scans(connected, client=c, now=NOW + timedelta(seconds=30))            # not due: no request
    assert sum(1 for r in fake.requests if r["method"] == "POST") == 1
    report = request_scans(connected, client=c, now=NOW + timedelta(seconds=61))
    assert report.started == ["lib0manga"] and connected.pending_scans() == {}


def test_a_token_without_the_scope_stops_requests_until_a_new_token(connected, fake, client_factory):
    fake.scan_scope = False
    report = request_scans(connected, ["lib0manga"], client=_client(connected, client_factory), now=NOW)
    assert "cannot request library scans" in report.skipped and connected.scan_forbidden_at()
    assert connected.pending_scans() == {}
    report = request_scans(connected, ["lib0manga"], client=_client(connected, client_factory), now=NOW)
    assert sum(1 for r in fake.requests if r["method"] == "POST") == 1 and report.skipped
    connected.set_connection(token="mpx_NewTokenWithScan_0123456789abcdef")         # a new token: asked again
    assert connected.scan_forbidden_at() is None


def test_unreachable_keeps_the_request_for_later(connected, client_factory):
    dead = client_factory("http://127.0.0.1:9", timeout=0.5)
    report = request_scans(connected, ["lib0manga"], client=dead, now=NOW)
    assert report.pending == [("lib0manga", "2026-10-08T12:05:00Z")] and report.started == []


def test_a_refused_token_is_remembered(connected, fake, client_factory):
    report = request_scans(connected, ["lib0manga"], client=client_factory(fake.url, token="mpx_wrong"), now=NOW)
    assert "refused the token" in report.skipped and connected.connection().token_rejected_at


def test_nothing_is_asked_without_a_connection(cache):
    report = request_scans(cache, ["lib0manga"], now=NOW)
    assert report.skipped == "no MangaPixer connection"


def test_libraries_come_from_the_roots_mappings(cache, db, tmp_path):
    root = add_root_with_series(db, tmp_path, "Manga", ["Series A", "Series B"])
    other = add_root_with_series(db, tmp_path, "Unmapped", ["Series C"])
    cache.save_mapping(Mapping(root_id=root.id, library_id="lib0manga"))
    with db.connect() as con:
        ids = {r["rel_path"]: r["id"] for r in con.execute("SELECT id, rel_path FROM series")}
    assert libraries_for_series(cache, [ids["Series A"], ids["Series B"]]) == {"lib0manga"}
    assert libraries_for_series(cache, [ids["Series C"], 999]) == set()
    assert other.id != root.id
