"""MangaPixer logging: the sync's outcome is on the INFO line, and a token never reaches the LOG FILE (through the
real handler with the debug level on, not only through caplog)."""

from __future__ import annotations

import logging
import logging.handlers

import pytest

from mangalist import log_config, paths
from mangalist.services.mangapixer.sync import sync_all

from .conftest import TOKEN


@pytest.fixture
def debug_log():
    root = logging.getLogger()
    saved = (list(root.handlers), root.level, logging.getLogger("urllib3").level)
    log_config.shutdown()
    log_config.setup(level="debug")
    yield
    log_config.shutdown()
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    logging.getLogger("urllib3").setLevel(saved[2])


def _file_text() -> str:
    for h in logging.getLogger().handlers:
        h.flush()
    return (paths.log_dir() / "mangalist.log").read_text(encoding="utf-8")


def test_a_sync_logs_its_start_and_outcome_and_never_the_token(connected, fake, client_factory, debug_log):
    fake.token = "mpx_rotated"                                  # a revoked token: the whole failure path runs too
    bad = sync_all(connected, client=client_factory(fake.url), list_series=lambda rid: [])
    assert bad.status == "error"
    connected.set_connection(token="mpx_rotated")
    ok = sync_all(connected, client=client_factory(fake.url, token="mpx_rotated"), list_series=lambda rid: [])
    assert ok.status == "ok"
    skipped_cache = connected
    skipped_cache.set_connection(base_url="", token=None)
    assert sync_all(skipped_cache, list_series=lambda rid: []).status == "skipped"
    text = _file_text()
    assert "MangaPixer: sync started (scheduled)" in text
    assert "MangaPixer: sync finished" in text and "sync ended with error" in text
    assert "MangaPixer: sync skipped: no MangaPixer source configured" in text
    assert "GET ping -> HTTP" in text or "-> HTTP 200" in text, "the debug request line names the endpoint"
    assert TOKEN not in text and "mpx_rotated" not in text
    assert "token set" in text and "token forgotten" in text, "the connection change says what, not the value"
    assert "Bearer" not in text.replace("Bearer ***", "")
