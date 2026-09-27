"""Thin wrapper around the MangaUpdates v1 API.

Only the two endpoints we need are implemented:
  - POST /series/search  -> list of candidate matches
  - GET  /series/{id}    -> full record including licensed + publishers
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

_log = logging.getLogger(__name__)

_BASE = "https://api.mangaupdates.com/v1"
_SESSION = requests.Session()
_SESSION.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

# Polite inter-request delay (seconds).
REQUEST_DELAY = 0.3


def search_series(query: str, page_size: int = 10,
                  filter_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Return up to *page_size* candidates from /series/search.

    Each item is ``{"record": SeriesModelSearchV1, "hit_title": str}``.
    ``hit_title`` is the associated/alt title that MU matched against.
    *filter_types* excludes series types (the automatic match passes
    ``matcher.mangaupdates.AUTO_SEARCH_FILTER``; the manual picker passes none).
    """
    payload: Dict[str, Any] = {"search": query, "stype": "title", "page": 1, "perpage": page_size}
    if filter_types:
        payload["filter_types"] = list(filter_types)
    _log.debug("search_series: %r", query)
    resp = _SESSION.post(f"{_BASE}/series/search", json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    _log.debug("search_series: %d results for %r", len(results), query)
    return [
        {"record": r["record"], "hit_title": r.get("hit_title") or ""}
        for r in results if "record" in r
    ]


def get_series(series_id: int) -> Dict[str, Any]:
    """Return the full SeriesModelV1 dict for *series_id*."""
    _log.debug("get_series: id=%s", series_id)
    resp = _SESSION.get(f"{_BASE}/series/{series_id}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def is_licensed(series_id: int) -> Optional[bool]:
    """Return True/False for the licensed flag, or None on error."""
    try:
        time.sleep(REQUEST_DELAY)
        data = get_series(series_id)
        return data.get("licensed")
    except Exception:  # noqa: BLE001
        return None


def get_latest_releases(series_id: int, page_size: int = 5) -> List[Dict[str, Any]]:
    """Return up to *page_size* most-recent release records for *series_id*.

    Each item has ``{"volume": str|None, "chapter": str|None, "release_date": str|None}``.
    Results are ordered newest-first.
    """
    _log.debug("get_latest_releases: series_id=%s", series_id)
    payload = {
        "search": str(series_id),
        "search_type": "series",
        "orderby": "date",
        "asc": "desc",
        "perpage": page_size,
    }
    resp = _SESSION.post(f"{_BASE}/releases/search", json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    out = []
    for r in results:
        rec = r.get("record") or {}
        out.append({
            "volume": rec.get("volume"),
            "chapter": rec.get("chapter"),
            "release_date": rec.get("release_date"),
        })
    _log.debug("get_latest_releases: %d results for series_id=%s", len(out), series_id)
    return out


def english_publishers(series_data: Dict[str, Any]) -> List[str]:
    """Extract English-type publisher names from a full series record."""
    pubs = series_data.get("publishers") or []
    return [p["publisher_name"] for p in pubs if p.get("type") == "English"]
