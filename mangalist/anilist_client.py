"""Thin wrapper around the AniList GraphQL API (no authentication required).

Only the manga search and direct-ID lookup are implemented, returning the
fields needed for chapter/volume cross-unit estimation in the Behind column
and the series' external links (official sources: publisher pages, official
readers - ``externalLinks { url site type language }``, read in the same
request, so no extra calls). :func:`english_titles` reads the official English
title (``title.english``) of up to :data:`PAGE_SIZE` records in ONE request
(the folder scheme's English part, :mod:`mangalist.english_titles`).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set

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
    externalLinks { url site type language }
  }
}
"""

# Up to PAGE_SIZE records by id in one request (AniList's page limit): the official English titles of a whole library
# in a handful of requests instead of one per series.
PAGE_SIZE = 50
_QUERY_ENGLISH = """
query ($ids: [Int]) {
  Page(page: 1, perPage: 50) {
    media(id_in: $ids, type: MANGA) {
      id
      title { romaji english }
    }
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
    externalLinks { url site type language }
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


class AniListUnavailable(Exception):
    """AniList could not be asked or did not answer (network, rate limit after the retry, server error)."""


def _execute(query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Run a GraphQL query; one retry on a rate limit (429, after its Retry-After) or a server error.

    Returns the ``data.Media`` dict, or None on no match (AniList answers 404) or on error. Failures are
    logged with their real HTTP status and AniList's (or Cloudflare's) short reason.
    """
    try:
        data = _execute_data(query, variables)
    except AniListUnavailable:
        return None
    return data.get("Media") if isinstance(data, dict) else None


def _execute_data(query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """:func:`_execute`'s request: the answer's ``data`` dict (None: no match - AniList answers 404); raises
    :class:`AniListUnavailable` when AniList could not be asked or failed, so a caller can tell "nothing there" from
    "ask again later"."""
    for attempt in range(2):
        try:
            resp = _SESSION.post(_URL, json={"query": query, "variables": variables}, timeout=15)
        except requests.RequestException as exc:
            _log.warning("AniList request failed: %s", type(exc).__name__)
            raise AniListUnavailable(type(exc).__name__) from None
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
            raise AniListUnavailable(f"HTTP {status}")
        try:
            data = resp.json()
        except ValueError:
            _log.warning("AniList returned no JSON (HTTP %s)", status)
            raise AniListUnavailable("no JSON") from None
        body = data.get("data") if isinstance(data, dict) else None
        if isinstance(data, dict) and data.get("errors") and not any((body or {}).values()):
            _log.warning("AniList query error: %s", _reason(resp))
        return body if isinstance(body, dict) else None
    raise AniListUnavailable("no answer after the retry")


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


def parse_external_links(media: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
    """The ``externalLinks`` rows of a Media answer as ``{url, site, type, language}`` dicts: rows
    without a URL or of another shape are dropped, the order is AniList's."""
    out: List[Dict[str, Optional[str]]] = []
    for row in media.get("externalLinks") or ():
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        out.append({
            "url": url.strip(),
            "site": row.get("site") if isinstance(row.get("site"), str) else None,
            "type": row.get("type") if isinstance(row.get("type"), str) else None,
            "language": row.get("language") if isinstance(row.get("language"), str) else None,
        })
    return out


def _normalise(media: Dict[str, Any]) -> Dict[str, Any]:
    """Return a flat dict with id, title, english_title, chapters, volumes and external_links
    (all optional; external_links a list)."""
    titles = media.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or ""
    return {
        "id": media.get("id"),
        "title": title,
        "english_title": titles.get("english") or None,
        "chapters": media.get("chapters"),   # int | None
        "volumes": media.get("volumes"),     # int | None
        "external_links": parse_external_links(media),
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


def english_titles(anilist_ids: List[int]) -> Dict[int, Optional[str]]:
    """The official English title (``title.english``: AniList keeps the licensed title apart from fan titles) of each
    AniList manga record in *anilist_ids* (at most :data:`PAGE_SIZE`, one request): ``{id: title or None}`` - None
    when the record has no English title; an id AniList does not know is left out. Raises
    :class:`AniListUnavailable` when AniList could not be asked (the caller tries again later)."""
    ids = [int(i) for i in dict.fromkeys(anilist_ids)][:PAGE_SIZE]
    if not ids:
        return {}
    data = _execute_data(_QUERY_ENGLISH, {"ids": ids})
    out: Dict[int, Optional[str]] = {}
    for media in ((data or {}).get("Page") or {}).get("media") or ():
        if not isinstance(media, dict) or not isinstance(media.get("id"), int):
            continue
        english = (media.get("title") or {}).get("english")
        out[media["id"]] = english.strip() if isinstance(english, str) and english.strip() else None
    return out
