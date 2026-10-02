"""Offline MangaUpdates stand-ins built from the recorded golden-set fixtures (public data, see
tests/golden/fixtures/README.md). Shared by the matcher-integration tests."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

import requests

FIXTURES = Path(__file__).resolve().parent / "golden" / "fixtures"


@lru_cache(maxsize=1)
def raw_fixtures() -> Tuple[Dict[Tuple[str, bool, int], dict], Dict[int, dict]]:
    """Search responses keyed by (query, doujinshi allowed, page) and series records keyed by id."""
    searches: Dict[Tuple[str, bool, int], dict] = {}
    series: Dict[int, dict] = {}
    for path in sorted(FIXTURES.glob("*.json")):
        root = json.loads(path.read_text(encoding="utf-8"))
        if path.name.startswith("search."):
            key = (root["query"], "Doujinshi" not in root["filter_types"], int(root.get("page", 1)))
            searches[key] = root["response"]
        elif path.name.startswith("series."):
            series[int(root["series_id"])] = root
    return searches, series


def not_found(series_id: int) -> requests.HTTPError:
    """The error ``mu_client.get_series`` raises for a record MangaUpdates does not have."""
    response = requests.Response()
    response.status_code = 404
    return requests.HTTPError(f"404 Client Error: no recorded series {series_id}", response=response)


class FakeMangaUpdates:
    """Replays the fixtures through ``mu_client``'s call shapes and records every request
    (``searches``: (query, filter types, page))."""

    def __init__(self) -> None:
        self.searches: List[Tuple[str, Tuple[str, ...], int]] = []
        self.gets: List[int] = []

    def search_series_page(self, query: str, page: int = 1, page_size: int = 10, filter_types=None) -> dict:
        filters = tuple(filter_types or ())
        self.searches.append((query, filters, page))
        searches, _ = raw_fixtures()
        return searches.get((query, "Doujinshi" not in filters, page), {"total_hits": 0, "results": []})

    def get_series(self, series_id: int) -> dict:
        self.gets.append(int(series_id))
        _, series = raw_fixtures()
        if int(series_id) not in series:
            raise not_found(series_id)
        record = dict(series[int(series_id)])
        record.setdefault("url", f"https://www.mangaupdates.com/series/{series_id}")
        return record
