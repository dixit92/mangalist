"""A polite client for nyaa's public RSS search. No Qt, no account, no cookies.

Rules (nyaa is a free community site; be a good guest):

- At least ``min_interval`` seconds (default 2) between two requests of one client, a timeout on every request,
  a fixed descriptive User-Agent, no cookies, no proxy / netrc credentials from the environment.
- HTTP 429: wait for ``Retry-After`` and try again, at most ``max_retries`` times and never longer than
  ``max_retry_after`` seconds per wait; a longer wait is raised at once as :class:`RateLimited`.
- HTTP 5xx and timeouts (nyaa's front end answers 502 / 503 / 504 now and then): ``max_retries`` more tries with a
  growing pause, then :class:`UnexpectedResponse` / :class:`Unreachable`.
- Anything else that is not a feed (a maintenance page, a block page, HTML) -> :class:`UnexpectedResponse`.

``get`` is injectable (``get(url, params, timeout) -> response`` with ``status_code``, ``headers``, ``content``) so
tests never touch the network; so are ``sleep`` and ``clock``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from ...http_identity import USER_AGENT
from .rss import FeedError, RssItem, parse_feed

_log = logging.getLogger(__name__)

BASE_URL = "https://nyaa.si/"
CATEGORY_ENGLISH_TRANSLATED = "3_1"     # Literature - English-translated (the MVP)
CATEGORY_RAW = "3_3"                    # Literature - Raw (later)

DEFAULT_TIMEOUT = 20.0
DEFAULT_MIN_INTERVAL = 2.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_RETRY_AFTER = 60.0
MAX_BODY_BYTES = 8 * 1024 * 1024


# --- errors -------------------------------------------------------------------------------------------

class NyaaError(Exception):
    """Base of every nyaa error."""

    retryable = False


class Unreachable(NyaaError):
    """nyaa could not be reached (DNS, refused, timeout, TLS)."""

    retryable = True


class RateLimited(NyaaError):
    """HTTP 429 and the wait is longer than this client will sleep."""

    retryable = True

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class UnexpectedResponse(NyaaError):
    """An HTTP error, or an answer that is not the RSS feed."""

    retryable = True

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


# --- the client ---------------------------------------------------------------------------------------

Get = Callable[[str, Dict[str, Any], float], Any]


class NyaaClient:
    """One nyaa site (default https://nyaa.si/). Not thread-safe: one client per thread."""

    def __init__(self, base_url: str = BASE_URL, *, get: Optional[Get] = None, timeout: float = DEFAULT_TIMEOUT,
                 min_interval: float = DEFAULT_MIN_INTERVAL, max_retries: int = DEFAULT_MAX_RETRIES,
                 max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = float(timeout)
        self.min_interval = max(0.0, float(min_interval))
        self.max_retries = max(0, int(max_retries))
        self.max_retry_after = float(max_retry_after)
        self._sleep = sleep
        self._clock = clock
        self._last: Optional[float] = None
        self._session: Optional[requests.Session] = None
        if get is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/rss+xml, text/xml"})
            self._session.trust_env = False
            get = self._session_get
        self._get = get

    def _session_get(self, url: str, params: Dict[str, Any], timeout: float) -> requests.Response:
        assert self._session is not None
        return self._session.get(url, params=params, timeout=timeout, allow_redirects=True)

    def close(self) -> None:
        if self._session is not None:
            self._session.close()

    def __enter__(self) -> "NyaaClient":
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

    # --- requests ------------------------------------------------------------------------------------

    def _fetch(self, params: Dict[str, Any]) -> bytes:
        attempts = 0
        while True:
            self._pace()
            try:
                resp = self._get(self.base_url, params, self.timeout)
            except requests.exceptions.Timeout:
                resp, failure = None, Unreachable(f"nyaa did not answer in {self.timeout:.0f} s")
            except requests.exceptions.SSLError:
                raise Unreachable("the certificate of nyaa was not accepted") from None
            except requests.exceptions.RequestException as exc:
                raise Unreachable(f"cannot reach nyaa ({type(exc).__name__})") from None
            else:
                failure = None
            if resp is not None:
                status = resp.status_code
                if status == 429:
                    wait = self._retry_after(resp)
                    if attempts < self.max_retries and wait is not None and wait <= self.max_retry_after:
                        attempts += 1
                        _log.info("nyaa: rate limited; waiting %.0f s (retry %d of %d)", wait, attempts,
                                  self.max_retries)
                        self._sleep(wait)
                        continue
                    msg = "nyaa rate limit reached"
                    if wait is not None:
                        msg += f"; try again in {wait:.0f} s"
                    raise RateLimited(msg, retry_after=wait)
                if status >= 500:
                    failure = UnexpectedResponse(f"nyaa server error (HTTP {status})", status)
                elif status >= 400:
                    raise UnexpectedResponse(f"nyaa refused the request (HTTP {status})", status)
                else:
                    body = resp.content
                    if len(body) > MAX_BODY_BYTES:
                        raise UnexpectedResponse("nyaa's answer is unexpectedly large; ignored", status)
                    return body
            if attempts < self.max_retries:
                attempts += 1
                pause = self.min_interval * (2 ** attempts)
                _log.info("nyaa: %s; retrying in %.0f s (retry %d of %d)", failure, pause, attempts, self.max_retries)
                self._sleep(pause)
                continue
            assert failure is not None
            raise failure

    @staticmethod
    def _retry_after(resp: Any) -> Optional[float]:
        try:
            return max(0.0, float(resp.headers.get("Retry-After", "")))
        except (ValueError, AttributeError, TypeError):
            return None

    # --- the search ----------------------------------------------------------------------------------

    def rss(self, query: str, category: str = CATEGORY_ENGLISH_TRANSLATED, filter_: int = 0) -> List[RssItem]:
        """``?page=rss&q=<query>&c=<category>&f=<filter>``: the first page (up to 75 items). ``filter_`` is nyaa's
        ``f`` (0 = no filter)."""
        params = {"page": "rss", "q": query, "c": category, "f": str(filter_)}
        body = self._fetch(params)
        try:
            return parse_feed(body)
        except FeedError as exc:
            raise UnexpectedResponse(f"nyaa did not answer with its RSS feed ({exc})") from None


__all__ = ["BASE_URL", "CATEGORY_ENGLISH_TRANSLATED", "CATEGORY_RAW", "NyaaClient", "NyaaError", "RateLimited",
           "Unreachable", "UnexpectedResponse"]
