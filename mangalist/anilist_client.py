"""Thin wrapper around the AniList GraphQL API (no authentication required).

Only the manga search and direct-ID lookup are implemented, returning just
the fields needed for chapter/volume cross-unit estimation in the Behind
column.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Set

import requests

from .http_identity import USER_AGENT

_log = logging.getLogger(__name__)

_URL = "https://graphql.anilist.co"
_SESSION = requests.Session()
_SESSION.headers.update({"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT})

# AniList allows 30 requests a minute (its X-RateLimit-Limit header, 2026 - the documented 90 is degraded);
# stay under it. A 429 says how long to wait (Retry-After); that wait is honoured once, up to MAX_RETRY_AFTER.
REQUEST_DELAY = 2.1
MAX_RETRY_AFTER = 65.0

_QUERY_SEARCH = """
query ($search: String) {
  Media(search: $search, type: MANGA, isAdult: false, format_not_in: [NOVEL, ONE_SHOT]) {
    id
    title { romaji english }
    chapters
    volumes
  }
}
"""

_QUERY_BY_ID = """
query ($id: Int) {
  Media(id: $id, type: MANGA) {
    id
    title { romaji english }
    chapters
    volumes
  }
}
"""


def _tokenize(text: str) -> Set[str]:
    """Lower-case alphanumeric tokens, dropping 1-2 char words."""
    return {
        w for w in text.lower().split()
        if len(w) >= 3 and w.isalnum()
    }


def _title_similar(query: str, result_title: str) -> bool:
    """Quick token-overlap check to reject blatantly wrong fuzzy matches."""
    q = _tokenize(query)
    t = _tokenize(result_title)
    if not q or not t:
        # Fallback: substring check for very short titles.
        return query.lower() in result_title.lower() or result_title.lower() in query.lower()
    overlap = len(q & t)
    # Require at least 30% overlap or one exact word match.
    return overlap >= max(1, int(len(q) * 0.3))


def _execute(query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Run a GraphQL query; one retry on a rate limit (429, after its Retry-After) or a server error.

    Returns the ``data.Media`` dict, or None on no match (AniList answers 404) or on error. Failures are
    logged with their real HTTP status and AniList's (or Cloudflare's) short reason.
    """
    for attempt in range(2):
        try:
            resp = _SESSION.post(_URL, json={"query": query, "variables": variables}, timeout=15)
        except requests.RequestException as exc:
            _log.warning("AniList request failed: %s", type(exc).__name__)
            return None
        status = resp.status_code
        if status == 404:
            _log.debug("AniList: no match")
            return None
        if (status == 429 or status >= 500) and attempt == 0:
            wait = _retry_after(resp, default=1.0 if status >= 500 else MAX_RETRY_AFTER)
            _log.info("AniList HTTP %s - retrying in %.0f s", status, wait)
            time.sleep(wait)
            continue
        if not resp.ok:
            _log.warning("AniList request failed (HTTP %s): %s", status, _reason(resp))
            return None
        try:
            data = resp.json()
        except ValueError:
            _log.warning("AniList returned no JSON (HTTP %s)", status)
            return None
        media = (data.get("data") or {}).get("Media") if isinstance(data, dict) else None
        if media is None and isinstance(data, dict) and data.get("errors"):
            _log.warning("AniList query error: %s", _reason(resp))
        return media
    return None


def _retry_after(resp: requests.Response, default: float) -> float:
    """Seconds to wait before the retry: the Retry-After header (seconds), capped at MAX_RETRY_AFTER."""
    try:
        value = float(resp.headers.get("Retry-After", ""))
    except ValueError:
        value = default
    return max(0.0, min(value, MAX_RETRY_AFTER))


def _reason(resp: requests.Response) -> str:
    """A short reason from an error body: GraphQL ``errors[0].message``, or Cloudflare's ``title``."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:120].strip() or "no details"
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict) and errors[0].get("message"):
            return str(errors[0]["message"])[:200]
        if body.get("title"):
            return str(body["title"])[:200]
    return "no details"


def _normalise(media: Dict[str, Any]) -> Dict[str, Any]:
    """Return a flat dict with id, title, chapters, volumes (all optional)."""
    titles = media.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or ""
    return {
        "id": media.get("id"),
        "title": title,
        "chapters": media.get("chapters"),   # int | None
        "volumes": media.get("volumes"),     # int | None
    }


def search_manga(title: str) -> Optional[Dict[str, Any]]:
    """Search AniList for *title*; return normalised dict or None.

    Returns None if the best result is too dissimilar to the query.
    """
    _log.debug("anilist search_manga: %r", title)
    media = _execute(_QUERY_SEARCH, {"search": title})
    if media is None:
        return None
    result = _normalise(media)
    if not _title_similar(title, result["title"]):
        _log.debug("AniList result rejected (too dissimilar): %r vs %r",
                   title, result["title"])
        return None
    _log.debug("anilist search_manga result: %r", result)
    return result


def get_manga(anilist_id: int) -> Optional[Dict[str, Any]]:
    """Fetch AniList entry by *anilist_id*; return normalised dict or None."""
    _log.debug("anilist get_manga: id=%s", anilist_id)
    media = _execute(_QUERY_BY_ID, {"id": anilist_id})
    if media is None:
        return None
    result = _normalise(media)
    _log.debug("anilist get_manga result: %r", result)
    return result
