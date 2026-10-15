"""OPTIONAL live checks against the real MangaUpdates API (owner, 2026-10-15, answer 24: "keep some optional tests that
use real MangaUpdates searches"). Skipped by default - the rest of the suite never touches the network. Run them with::

    MANGALIST_LIVE_MU=1 python -m pytest -q tests/golden/test_live_mangaupdates.py

They search only the PUBLIC, well-known titles the golden-set fixtures already record (see fixtures/README.md), one
request at a time with a pause between requests, and check that the recordings still describe MangaUpdates' answers and
that MangaList's own calls (the search filters, the series record, Add series' link ids) read them as the code
expects. A failure here means MangaUpdates changed - not that the code is broken: compare with the fixture named."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

LIVE = os.environ.get("MANGALIST_LIVE_MU") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="live MangaUpdates checks: set MANGALIST_LIVE_MU=1 to run them")

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAUSE_S = 1.0                    # at least MangaPixer's 0.75 s between requests


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _polite():
    yield
    time.sleep(PAUSE_S)


def test_a_recorded_search_still_finds_its_series_first():
    from mangalist import mu_client

    recorded = _fixture("search.berserk.json")
    first = recorded["response"]["results"][0]["record"]
    live = mu_client.search_series(recorded["query"], page_size=10, filter_types=list(recorded["filter_types"]))
    ids = [item["record"]["series_id"] for item in live]
    assert first["series_id"] in ids[:3], f"MangaUpdates now ranks {ids[:3]} first for {recorded['query']!r}"


def test_a_recorded_series_record_still_has_the_fields_the_matcher_reads():
    from mangalist import mu_client
    from mangalist.matcher.mangaupdates import map_series

    recorded = _fixture("series.15180124327.json")
    live = mu_client.get_series(int(recorded["series_id"]))
    assert live["series_id"] == recorded["series_id"] and live["title"]
    for key in ("associated", "type", "year", "status", "latest_chapter", "authors"):
        assert key in live, key
    candidate = map_series(live, str(live["series_id"]))
    assert candidate is not None and candidate.title == live["title"]


def test_add_series_hides_doujinshi_and_artbooks_in_a_live_search():
    from mangalist import add_series

    query = _fixture("search.berserk.json")["query"]
    hidden = {t.casefold() for t in add_series.HIDDEN_TYPES}
    shown = add_series.search_mu(query)
    assert shown and not any(str(r.get("type") or "").casefold() in hidden for r in shown)
    time.sleep(PAUSE_S)
    everything = add_series.search_mu(query, show_all=True)
    assert everything


def test_add_series_reads_a_mangaupdates_link_by_its_base36_id():
    from mangalist import add_series

    sid = int(_fixture("series.15180124327.json")["series_id"])
    link = f"https://www.mangaupdates.com/series/{add_series.base36_from_mu_id(sid)}/x"
    look = add_series.lookup(link)
    assert look.info is not None and look.info.mu_id == sid
    assert look.info.scheme_name()                       # the folder scheme names it
