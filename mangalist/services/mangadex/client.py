"""A polite client for MangaDex's manga list: the latest uploaded chapter of up to 100 manga per request. No Qt.

What it asks (owner-approved 2026-10-18, "Shared Chapter Signal"; lane GT): ``GET https://api.mangadex.org/manga`` with
``ids[]`` (at most :data:`MAX_IDS` = 100 per request, MangaDex's own limit), ``limit=100`` and EVERY ``contentRating[]``
value (:data:`CONTENT_RATINGS` - MangaDex leaves out erotica and pornographic titles unless they are asked for, and a
title left out would read as "not answered", costing a fetch, never a chapter). From the answer it reads only
``data[].id`` and ``data[].attributes.latestUploadedChapter`` (a chapter uuid, or null when MangaDex lists none).

Rules (MangaDex is a free community site; its global limit is about 5 requests a second per address):

- At least ``min_interval`` seconds (default 1) between two requests, a timeout on every request, MangaList's fixed
  User-Agent (:mod:`mangalist.http_identity`), no cookies, no proxy / netrc credentials from the environment.
- HTTP 429: wait for ``X-RateLimit-Retry-After`` (MangaDex's: a Unix time) or ``Retry-After`` (seconds) once, when it
  is no longer than ``max_retry_after``; otherwise :class:`RateLimited`. HTTP 5xx and timeouts: one more try after a
  pause. Anything else that is not MangaDex's JSON list: :class:`UnexpectedResponse`.
- A request that fails stops the rest of the run's requests (:meth:`MangaDexClient.latest_uploads` returns what it
  has): the ids not answered are simply unknown, and the chapter check fetches those series as before.

``get`` is injectable (``get(url, params, timeout) -> response`` with ``status_code``, ``headers``, ``json()``) so tests
never touch the network; so are ``sleep`` and ``clock``.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ...http_identity import USER_AGENT

_log = logging.getLogger(__name__)

API_URL = "https://api.mangadex.org"
MAX_IDS = 100
#: Every content rating MangaDex has: without the last two, MangaDex leaves those titles out of the answer.
CONTENT_RATINGS = ("safe", "suggestive", "erotica", "pornographic")

DEFAULT_TIMEOUT = 20.0
DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_MAX_RETRY_AFTER = 60.0

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

Get = Callable[[str, Any, float], Any]


class MangaDexError(Exception):
    """Base of the client's errors."""


class Unreachable(MangaDexError):
    """MangaDex could not be reached (DNS, refused, timeout, TLS)."""


class RateLimited(MangaDexError):
    """HTTP 429 and the wait is longer than this client will sleep."""


class UnexpectedResponse(MangaDexError):
    """An HTTP error, or an answer that is not MangaDex's manga list."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@dataclass
class LatestUploads:
    """What :meth:`MangaDexClient.latest_uploads` learned. *values*: manga uuid -> its latest uploaded chapter's uuid
    (None: MangaDex lists no chapter); a uuid not in it is unknown (not answered, or its request failed)."""

    values: Dict[str, Optional[str]] = field(default_factory=dict)
    requests: int = 0                   # requests sent to MangaDex
    failed: int = 0                     # ... that failed (their ids, and the ones not asked after them, are unknown)
    error: Optional[str] = None         # the first failure, for the log


def chunks(ids: Sequence[str], size: int = MAX_IDS) -> List[List[str]]:
    return [list(ids[i:i + size]) for i in range(0, len(ids), size)]


def query(ids: Sequence[str]) -> List[Tuple[str, str]]:
    """The query of one request (repeated keys, in order): the ids, ``limit``, every content rating."""
    return ([("ids[]", i) for i in ids] + [("limit", str(MAX_IDS))]
            + [("contentRating[]", r) for r in CONTENT_RATINGS])


class MangaDexClient:
    """MangaDex's API. Not thread-safe: one client per thread."""

    def __init__(self, base_url: str = API_URL, *, get: Optional[Get] = None, timeout: float = DEFAULT_TIMEOUT,
                 min_interval: float = DEFAULT_MIN_INTERVAL, max_retries: int = DEFAULT_MAX_RETRIES,
                 max_retry_after: float = DEFAULT_MAX_RETRY_AFTER, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.min_interval = max(0.0, float(min_interval))
        self.max_retries = max(0, int(max_retries))
        self.max_retry_after = float(max_retry_after)
        self._sleep, self._clock, self._wall = sleep, clock, wall
        self._last: Optional[float] = None
        self._session = None
        if get is None:
            import requests

            self._session = requests.Session()
            self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
            self._session.trust_env = False
            get = self._session_get
        self._get = get

    def _session_get(self, url: str, params: Any, timeout: float):
        return self._session.get(url, params=params, timeout=timeout, allow_redirects=False)

    def close(self) -> None:
        if self._session is not None:
            self._session.close()

    def __enter__(self) -> "MangaDexClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- politeness ----------------------------------------------------------------------------------

    def _pace(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _retry_after(self, resp: Any) -> Optional[float]:
        headers = getattr(resp, "headers", None) or {}
        try:
            at = headers.get("X-RateLimit-Retry-After")
            if at not in (None, ""):
                return max(0.0, float(at) - self._wall())
            return max(0.0, float(headers.get("Retry-After", "")))
        except (TypeError, ValueError, AttributeError):
            return None

    def _fetch(self, path: str, params: Any) -> Any:
        url = f"{self.base_url}{path}"
        attempts = 0
        while True:
            self._pace()
            try:
                resp = self._get(url, params, self.timeout)
            except Exception as exc:  # noqa: BLE001 - requests' ConnectionError / Timeout / SSLError and the like
                failure: MangaDexError = Unreachable(f"MangaDex could not be reached ({type(exc).__name__})")
            else:
                status = int(getattr(resp, "status_code", 0) or 0)
                _log.debug("MangaDex: GET %s -> HTTP %s", path, status)
                if status == 429:
                    wait = self._retry_after(resp)
                    if attempts < self.max_retries and wait is not None and wait <= self.max_retry_after:
                        attempts += 1
                        _log.info("MangaDex: rate limited; waiting %.0f s", wait)
                        self._sleep(wait)
                        self._last = None           # the wait was the pause
                        continue
                    raise RateLimited("MangaDex's rate limit was reached" +
                                      (f"; try again in {wait:.0f} s" if wait is not None else ""))
                if status == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        raise UnexpectedResponse("MangaDex's answer is not JSON", status) from None
                if status < 500:
                    raise UnexpectedResponse(f"MangaDex refused the request (HTTP {status})", status)
                failure = UnexpectedResponse(f"MangaDex server error (HTTP {status})", status)
            if attempts < self.max_retries:
                attempts += 1
                pause = max(self.min_interval, 1.0) * (2 ** attempts)
                _log.info("MangaDex: %s; trying again in %.0f s", failure, pause)
                self._sleep(pause)
                self._last = None
                continue
            raise failure

    # --- the latest uploads --------------------------------------------------------------------------

    def latest_uploads(self, manga_ids: Sequence[str],
                       should_stop: Optional[Callable[[], bool]] = None) -> LatestUploads:
        """The latest uploaded chapter of each manga in *manga_ids* (MangaDex uuids; anything else is left out), at
        most 100 per request. Never raises: a failed request ends the asking, and what it would have answered stays
        unknown (see the module docstring)."""
        ids = list(dict.fromkeys(i.strip().lower() for i in manga_ids if isinstance(i, str)
                                 and _UUID.fullmatch(i.strip().lower())))
        out = LatestUploads()
        for part in chunks(ids):
            if should_stop is not None and should_stop():
                break
            out.requests += 1
            try:
                body = self._fetch("/manga", query(part))
                out.values.update(read_latest(body, part))
            except MangaDexError as exc:
                out.failed += 1
                out.error = str(exc)
                _log.info("MangaDex: the latest uploads of %d manga could not be read (%s)", len(part), exc)
                break
        return out


def read_latest(body: Any, asked: Sequence[str]) -> Dict[str, Optional[str]]:
    """``data[].id`` -> ``data[].attributes.latestUploadedChapter`` of a ``GET /manga`` answer, for the ids *asked*
    only. An item whose latest upload is neither a uuid nor null is left out (unknown). Raises
    :class:`UnexpectedResponse` for an answer that is not MangaDex's list."""
    if not isinstance(body, Mapping) or body.get("result", "ok") != "ok" or not isinstance(body.get("data"), list):
        raise UnexpectedResponse("MangaDex's answer is not a manga list")
    wanted = set(asked)
    out: Dict[str, Optional[str]] = {}
    for item in body["data"]:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            continue
        mid = item["id"].strip().lower()
        attributes = item.get("attributes")
        if mid not in wanted or not isinstance(attributes, Mapping) or "latestUploadedChapter" not in attributes:
            continue
        latest = attributes.get("latestUploadedChapter")
        if latest is None:
            out[mid] = None
        elif isinstance(latest, str) and _UUID.fullmatch(latest.strip().lower()):
            out[mid] = latest.strip().lower()
    return out
