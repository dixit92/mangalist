"""A read-only client for MangaPixer's metadata export (MangaPixer 1.33.0 or newer, ``schemaVersion`` 1).

Endpoints (all ``GET``, under ``<base URL>/api/v1/export/``): ``ping``, ``libraries``, ``metadata``. See
MangaPixer's ``docs/metadata-export.md`` and ``docs/api-tokens.md``.

Client rules (MangaList's side of the agreed contract):

- The token travels only in the ``Authorization: Bearer`` header. It is never put in a URL, never
  logged, and never part of an exception message or ``repr``.
- **HTTP 401 is never retried** - not here, not by a caller: after 20 wrong tokens in 5 minutes
  MangaPixer refuses every token from this address, the valid one too. :class:`TokenRejected` says so.
- HTTP 429: wait for ``Retry-After`` and try again, at most ``max_retries`` times and never longer than
  ``max_retry_after`` seconds per wait; a longer wait (e.g. ``too_many_attempts``) is raised at once as
  :class:`RateLimited`.
- 403 -> :class:`Forbidden`; 404 ``libraryNotFound`` -> :class:`LibraryNotFound` (re-read the libraries
  and ask the owner to re-map); 409 ``fullSyncRequired`` -> :class:`FullSyncRequired`; 400 codes ->
  :class:`BadRequest`; network / TLS problems -> :class:`ConnectionFailed`.
- A ``schemaVersion`` above 1 is refused (:class:`UnsupportedSchema`); unknown fields are ignored (the
  JSON is passed through as given), and so are record providers other than ``mangaupdates``
  (:func:`usable_record`).
- HTTP and HTTPS both work. Certificates are verified by default; a self-signed MangaPixer certificate
  needs an opt-in: ``verify=False`` (accept any) or ``verify="/path/to/ca.pem"``.

No Qt here (the headless runner uses it).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union
from urllib.parse import urlsplit, urlunsplit

import requests

from ...http_identity import USER_AGENT

_log = logging.getLogger(__name__)

SUPPORTED_SCHEMA = 1
MIN_MANGAPIXER = "1.33.0"
INCLUDE_BLOCKS = ("volumes", "completion", "refresh")
MAX_LIMIT = 500
KNOWN_PROVIDERS = frozenset({"mangaupdates"})

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_RETRY_AFTER = 60.0

Verify = Union[bool, str]


# --- errors -------------------------------------------------------------------------------------------

class MangaPixerError(Exception):
    """Base of every client error. Messages never contain the token."""

    status: Optional[int] = None
    code: Optional[str] = None
    retryable = False

    def __init__(self, message: str, status: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code


class ConnectionFailed(MangaPixerError):
    """The server could not be reached (DNS, refused, timeout, TLS)."""

    retryable = True


class TokenRejected(MangaPixerError):
    """HTTP 401: no token, or a token that is wrong, revoked or expired. NEVER retry automatically."""

    status = 401


class Forbidden(MangaPixerError):
    status = 403


class NotFound(MangaPixerError):
    """HTTP 404 without ``libraryNotFound``: usually a MangaPixer older than 1.33.0 (no export)."""

    status = 404


class LibraryNotFound(NotFound):
    code = "libraryNotFound"


class FullSyncRequired(MangaPixerError):
    status = 409
    code = "fullSyncRequired"


class RateLimited(MangaPixerError):
    status = 429
    retryable = True

    def __init__(self, message: str, code: Optional[str] = None, retry_after: Optional[float] = None):
        super().__init__(message, 429, code)
        self.retry_after = retry_after


class BadRequest(MangaPixerError):
    status = 400


class UnsupportedSchema(MangaPixerError):
    """The answer's ``schemaVersion`` is newer than this MangaList understands."""


class ServerError(MangaPixerError):
    """A 5xx answer, or an answer that is not the documented JSON."""

    retryable = True


# --- data ---------------------------------------------------------------------------------------------

@dataclass
class Ping:
    ok: bool
    server_time: Optional[str]
    auth: Optional[str]


@dataclass
class Library:
    id: str
    display_name: str
    kind: Optional[str] = None
    folder_count: Optional[int] = None
    item_count: Optional[int] = None
    last_scan_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ExportPage:
    """One page of ``/api/v1/export/metadata``. ``items`` / ``removed`` are the JSON as given."""

    schema_version: int
    server_time: Optional[str]
    library: Dict[str, Any]
    items: List[Dict[str, Any]]
    removed: List[Dict[str, Any]]
    next_cursor: Optional[str]
    index: int = 0                # 0 = the first page (its serverTime is the one to keep)

    @property
    def first(self) -> bool:
        return self.index == 0


def usable_record(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The item's ``record`` when its provider is one MangaList knows (``mangaupdates``), else None."""
    record = item.get("record") if isinstance(item, dict) else None
    if isinstance(record, dict) and record.get("provider") in KNOWN_PROVIDERS:
        return record
    return None


# --- helpers ------------------------------------------------------------------------------------------

def normalize_base_url(url: str) -> str:
    """``host:port`` -> ``http://host:port``; no trailing slash; refuses credentials, queries and other
    schemes (a token never goes into a URL)."""
    text = str(url or "").strip()
    if not text:
        raise ValueError("enter the MangaPixer address, e.g. http://192.168.1.10:8080")
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("the MangaPixer address must start with http:// or https://")
    if not parts.hostname:
        raise ValueError("the MangaPixer address has no host name")
    if parts.username or parts.password:
        raise ValueError("put no user name or password in the address; MangaList uses the API token")
    if parts.query or parts.fragment:
        raise ValueError("the MangaPixer address must not contain '?' or '#'")
    path = parts.path.rstrip("/")
    for suffix in ("/api/v1/export", "/api/v1", "/api"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def _check_token(token: Optional[str]) -> str:
    value = str(token or "").strip()
    if not value:
        raise TokenRejected("no API token entered: create one in MangaPixer (Administration > API tokens)")
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise TokenRejected("the API token contains spaces or control characters; paste it again")
    return value


def _schema_version(body: Dict[str, Any]) -> int:
    raw = body.get("schemaVersion")
    try:
        version = int(raw)
    except (TypeError, ValueError):
        raise ServerError("the answer has no schemaVersion - is this a MangaPixer 1.33.0+ export?") from None
    if version > SUPPORTED_SCHEMA:
        raise UnsupportedSchema(
            f"MangaPixer sends export schema version {version}; this MangaList understands version "
            f"{SUPPORTED_SCHEMA}. Update MangaList.")
    if version < 1:
        raise ServerError(f"unexpected export schema version {version}")
    return version


def _int(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


# --- the client ---------------------------------------------------------------------------------------

class MangaPixerClient:
    """One MangaPixer server. Thread-compatible (one client per thread is simplest)."""

    def __init__(self, base_url: str, token: Optional[str], verify: Verify = True,
                 timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES,
                 max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
                 sleep: Callable[[float], None] = time.sleep, session: Optional[requests.Session] = None):
        self.base_url = normalize_base_url(base_url)
        self.__token = token  # name-mangled: kept out of vars() dumps and repr
        self.verify: Verify = verify if isinstance(verify, str) and verify else bool(verify)
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.max_retry_after = float(max_retry_after)
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self._session.trust_env = False  # no proxy / netrc credentials from the environment
        if self.verify is False:
            _log.warning("MangaPixer: certificate checks are OFF for %s (opt-in)", self.base_url)
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:  # noqa: BLE001
                pass

    def __repr__(self) -> str:
        return f"MangaPixerClient(base_url={self.base_url!r}, token=***)"

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "MangaPixerClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- requests ------------------------------------------------------------------------------------

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/api/v1/export/{endpoint}"

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        token = _check_token(self.__token)
        url = self._url(endpoint)
        attempts = 0
        while True:
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout, verify=self.verify,
                                         headers={"Authorization": f"Bearer {token}"}, allow_redirects=True)
            except requests.exceptions.SSLError:
                raise ConnectionFailed(
                    f"the certificate of {self.base_url} was not accepted (a self-signed MangaPixer certificate "
                    "needs its CA file, or certificate checks turned off)") from None
            except requests.exceptions.Timeout:
                raise ConnectionFailed(f"{self.base_url} did not answer in {self.timeout:.0f} s") from None
            except requests.exceptions.RequestException as exc:
                raise ConnectionFailed(f"cannot reach {self.base_url} ({type(exc).__name__})") from None
            status = resp.status_code
            if status == 429:
                code = self._error_code(resp)
                wait = self._retry_after(resp)
                if attempts < self.max_retries and wait is not None and wait <= self.max_retry_after:
                    attempts += 1
                    _log.info("MangaPixer: rate limited (%s); waiting %.0f s (retry %d of %d)",
                              code or "429", wait, attempts, self.max_retries)
                    self._sleep(wait)
                    continue
                if code == "too_many_attempts":
                    msg = ("MangaPixer refuses token requests from this address for a few minutes "
                           "(too many wrong tokens)")
                else:
                    msg = "MangaPixer rate limit reached"
                if wait is not None:
                    msg += f"; try again in {wait:.0f} s"
                raise RateLimited(msg, code=code, retry_after=wait)
            if status >= 400:
                self._raise_for(resp)
            try:
                body = resp.json()
            except ValueError:
                raise ServerError(f"MangaPixer answered HTTP {status} without JSON - is the address right?",
                                  status) from None
            if not isinstance(body, dict):
                raise ServerError("MangaPixer's answer is not a JSON object", status)
            return body

    @staticmethod
    def _error_code(resp: requests.Response) -> Optional[str]:
        try:
            body = resp.json()
        except ValueError:
            return None
        if isinstance(body, dict) and isinstance(body.get("error"), str):
            return body["error"][:80]
        return None

    @staticmethod
    def _retry_after(resp: requests.Response) -> Optional[float]:
        try:
            return max(0.0, float(resp.headers.get("Retry-After", "")))
        except ValueError:
            return None

    def _raise_for(self, resp: requests.Response) -> None:
        status = resp.status_code
        code = self._error_code(resp)
        if status == 401:
            # Never retried: wrong tokens lock the whole address out (20 in 5 minutes).
            raise TokenRejected("MangaPixer refused the API token (wrong, revoked or expired). Enter a new "
                                "token; MangaList does not try again on its own.", 401, code)
        if status == 403:
            raise Forbidden("MangaPixer refused access (403): the token needs the metadata:read scope of an "
                            "active admin", 403, code)
        if status == 404:
            if code == "libraryNotFound":
                raise LibraryNotFound("the MangaPixer library is gone (re-read the libraries and re-map)", 404, code)
            raise NotFound(f"no metadata export at this address (MangaPixer {MIN_MANGAPIXER} or newer needed)",
                           404, code)
        if status == 409 and code == "fullSyncRequired":
            raise FullSyncRequired("MangaPixer asks for a full sync", 409, code)
        if status == 400:
            raise BadRequest(f"MangaPixer refused the request ({code or 'bad request'})", 400, code)
        if status >= 500:
            raise ServerError(f"MangaPixer server error (HTTP {status})", status, code)
        raise MangaPixerError(f"unexpected answer from MangaPixer (HTTP {status}{', ' + code if code else ''})",
                              status, code)

    # --- endpoints -----------------------------------------------------------------------------------

    def ping(self) -> Ping:
        """``GET /api/v1/export/ping``: checks the address and the token."""
        body = self._get("ping")
        return Ping(ok=bool(body.get("ok")), server_time=body.get("serverTime"), auth=body.get("auth"))

    def libraries(self) -> List[Library]:
        body = self._get("libraries")
        _schema_version(body)
        out: List[Library] = []
        for raw in body.get("libraries") or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            out.append(Library(id=str(raw["id"]), display_name=str(raw.get("displayName") or raw["id"]),
                               kind=raw.get("kind") or None, folder_count=_int(raw.get("folderCount")),
                               item_count=_int(raw.get("itemCount")), last_scan_at=raw.get("lastScanAt"),
                               raw=raw))
        return out

    def export_pages(self, library_id: str, updated_since: Optional[str] = None,
                     include: Optional[Sequence[str]] = None, limit: Optional[int] = None) -> Iterator[ExportPage]:
        """The pages of one library's export, following ``nextCursor`` until it is null.

        *updated_since*: an incremental sync from that (MangaPixer) time, inclusive; None = full sync.
        *include*: the optional blocks (``volumes``, ``completion``, ``refresh``); None = MangaPixer's
        default (all), an empty sequence = none. *limit*: items per page, 1-500 (None = default 200).
        """
        if not library_id:
            raise ValueError("library_id is required")
        params: Dict[str, Any] = {"library": library_id}
        if updated_since:
            params["updatedSince"] = updated_since
        if include is not None:
            blocks = [str(b).strip() for b in include if str(b).strip()]
            unknown = [b for b in blocks if b not in INCLUDE_BLOCKS]
            if unknown:
                raise ValueError(f"unknown include block(s): {', '.join(unknown)}")
            params["include"] = ",".join(blocks)
        if limit is not None:
            if not 1 <= int(limit) <= MAX_LIMIT:
                raise ValueError(f"limit must be 1-{MAX_LIMIT}")
            params["limit"] = int(limit)
        cursor: Optional[str] = None
        seen = set()
        index = 0
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            body = self._get("metadata", page_params)
            version = _schema_version(body)
            items = body.get("items")
            removed = body.get("removed")
            if not isinstance(items, list):
                raise ServerError("the export page has no item list")
            page = ExportPage(schema_version=version, server_time=body.get("serverTime"),
                              library=body.get("library") if isinstance(body.get("library"), dict) else {},
                              items=[i for i in items if isinstance(i, dict) and i.get("nodeId")],
                              removed=[r for r in (removed if isinstance(removed, list) else [])
                                       if isinstance(r, dict) and r.get("nodeId")],
                              next_cursor=body.get("nextCursor") or None, index=index)
            yield page
            if not page.next_cursor:
                return
            if page.next_cursor in seen:
                raise ServerError("MangaPixer repeated a page cursor; sync stopped")
            seen.add(page.next_cursor)
            cursor = page.next_cursor
            index += 1
