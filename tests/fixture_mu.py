"""Offline MangaUpdates stand-ins built from the recorded golden-set fixtures (public data, see
tests/golden/fixtures/README.md). Shared by the matcher-integration tests."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

FIXTURES = Path(__file__).resolve().parent / "golden" / "fixtures"


@lru_cache(maxsize=1)
def raw_fixtures() -> Tuple[Dict[Tuple[str, bool], List[dict]], Dict[int, dict]]:
    """Search results keyed by (query, doujinshi allowed) and series records keyed by id."""
    searches: Dict[Tuple[str, bool], List[dict]] = {}
    series: Dict[int, dict] = {}
    for path in sorted(FIXTURES.glob("*.json")):
        root = json.loads(path.read_text(encoding="utf-8"))
        if path.name.startswith("search."):
            searches[(root["query"], "Doujinshi" not in root["filter_types"])] = root["response"]["results"]
        elif path.name.startswith("series."):
            series[int(root["series_id"])] = root
    return searches, series


class FakeMangaUpdates:
    """Replays the fixtures through ``mu_client``'s call shapes and records every request."""

    def __init__(self) -> None:
        self.searches: List[Tuple[str, Tuple[str, ...]]] = []
        self.gets: List[int] = []

    def search_series(self, query: str, page_size: int = 10, filter_types=None):
        filters = tuple(filter_types or ())
        self.searches.append((query, filters))
        searches, _ = raw_fixtures()
        return list(searches.get((query, "Doujinshi" not in filters), []))

    def get_series(self, series_id: int) -> dict:
        self.gets.append(int(series_id))
        _, series = raw_fixtures()
        if int(series_id) not in series:
            raise KeyError(f"no recorded series {series_id}")
        record = dict(series[int(series_id)])
        record.setdefault("url", f"https://www.mangaupdates.com/series/{series_id}")
        return record
