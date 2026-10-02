"""Thin wrapper around the public MangaDex API (read-only, no login): used only to find the MangaDex record of a
series that is already matched to MangaUpdates (:mod:`manga_list.handoff.mangadex`)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

import requests

from .http_identity import USER_AGENT

_log = logging.getLogger(__name__)

_BASE = "https://api.mangadex.org"
_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})

# MangaDex allows about 5 requests a second per client; one every half second stays well under.
REQUEST_DELAY = 0.5


def get_json(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """GET ``path`` with ``params``; raises ``requests.HTTPError`` on a failure."""
    time.sleep(REQUEST_DELAY)
    resp = _SESSION.get(_BASE + path, params=params, timeout=15)
    if resp.status_code == 429:
        try:
            wait = min(float(resp.headers.get("Retry-After") or 5), 30.0)
        except ValueError:  # an HTTP date instead of seconds
            wait = 5.0
        _log.info("MangaDex HTTP 429 - retrying in %.0f s", wait)
        time.sleep(wait)
        resp = _SESSION.get(_BASE + path, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()
