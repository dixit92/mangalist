"""A qBittorrent Web API v2 client: :class:`mangalist.downloads.contracts.TorrentClient`. No Qt.

Targets qBittorrent 5.2.4 (the Unraid box's version; v5 renamed pause / resume to stop / start and the states to
``stoppedUP`` / ``stoppedDL``) and stays compatible with 4.x: MangaList only lists, adds, reads files and deletes,
and those endpoints and fields are the same in both. ``contracts.STOPPED_COMPLETE_STATES`` knows both state names.

Rules:

- **Login.** ``POST auth/login`` with ``Referer`` / ``Origin`` set to the base URL (qBittorrent's CSRF check); the
  session cookie lives in the session. qBittorrent 4.x answers HTTP 200 with the body ``Ok.`` or ``Fails.`` (wrong
  credentials); 5.x answers a good login with an empty HTTP 204 (and a refused one with 401 or ``Fails.``); HTTP
  403 means the address is banned (after repeated failures: one hour). **Wrong credentials
  are never retried** - not here, not by a caller - or the ban comes sooner: once a login has failed, this client
  refuses every further request without sending anything (build a new client with new credentials).
- A 403 on an API call means the session expired: log in again **once** and repeat the call.
- **The password** is held in a :class:`_Secret`; it never appears in a ``repr``, a log line, an exception
  message or a URL (it travels only in the login request's body).
- **``delete`` is defence in depth.** It fetches the torrent first and REFUSES (:class:`DeleteRefused`) unless
  the torrent's category is exactly :data:`~mangalist.downloads.contracts.QBITTORRENT_CATEGORY`. It takes one
  well-formed info hash; there is no bulk and no ``all``.
- ``add`` puts the torrent into the category with automatic torrent management on, so the category's save path
  decides where it lands whatever the box's default mode is.
- HTTP and HTTPS both work; certificates are verified unless the connection says ``verify_tls=False``.

Errors are all :class:`QbtError`: :class:`Unreachable`, :class:`AuthFailed` (and :class:`IpBanned`),
:class:`UnexpectedResponse`, :class:`TorrentRejected`, :class:`TorrentNotFound`, :class:`DeleteRefused`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import requests

from ...downloads.contracts import QBITTORRENT_CATEGORY, QbtConnection, TorrentFile, TorrentInfo
from ...http_identity import USER_AGENT

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
_HASH = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_VERSION = re.compile(r"^v?\d+\.\d+")
_ADD_SCHEMES = ("magnet:", "http://", "https://")


# --- errors -------------------------------------------------------------------------------------------

class QbtError(Exception):
    """Base of every qBittorrent client error. Messages never contain the password."""


class Unreachable(QbtError):
    """qBittorrent could not be reached (DNS, refused, timeout, TLS)."""


class AuthFailed(QbtError):
    """Wrong user name or password (or the Web UI refuses this client). Never retried automatically."""


class IpBanned(AuthFailed):
    """HTTP 403 on login: qBittorrent banned this address after too many failed logins (for an hour)."""


class UnexpectedResponse(QbtError):
    """An HTTP error, or an answer that is not what qBittorrent's API sends."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class TorrentRejected(QbtError):
    """qBittorrent answered ``Fails.`` to an add: an invalid link, or the torrent is already there."""


class TorrentNotFound(QbtError):
    """No torrent with that info hash."""


class DeleteRefused(QbtError):
    """The torrent is not in MangaList's category; MangaList never deletes anything else."""


# --- helpers ------------------------------------------------------------------------------------------

class _Secret:
    """Holds the password; prints as ``***`` (a ``repr`` / ``vars()`` dump or a traceback never shows it)."""

    __slots__ = ("_value",)

    def __init__(self, value: Optional[str]):
        self._value = value or ""

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "***"

    __str__ = __repr__


def normalize_base_url(url: str) -> str:
    """``host:port`` -> ``http://host:port``; no trailing slash, no ``/api/v2``; refuses credentials, queries and
    other schemes. A reverse-proxy sub-path is kept."""
    text = str(url or "").strip()
    if not text:
        raise ValueError("enter the qBittorrent address, e.g. http://192.168.1.10:8080")
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("the qBittorrent address must start with http:// or https://")
    if not parts.hostname:
        raise ValueError("the qBittorrent address has no host name")
    if parts.username or parts.password:
        raise ValueError("put no user name or password in the address; they are entered separately")
    if parts.query or parts.fragment:
        raise ValueError("the qBittorrent address must not contain '?' or '#'")
    path = parts.path.rstrip("/")
    if path.endswith("/api/v2"):
        path = path[: -len("/api/v2")]
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def _hash(value: str) -> str:
    h = str(value or "").strip().lower()
    if not _HASH.match(h):
        raise ValueError("an info hash is 40 (or 64) hexadecimal characters")
    return h


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _same_path(a: str, b: str) -> bool:
    return (a or "").rstrip("/\\") == (b or "").rstrip("/\\")


def _torrent_info(raw: Dict[str, Any]) -> TorrentInfo:
    return TorrentInfo(
        info_hash=str(raw.get("hash") or "").lower(), name=str(raw.get("name") or ""),
        category=str(raw.get("category") or ""), state=str(raw.get("state") or ""),
        progress=_float(raw.get("progress")), save_path=str(raw.get("save_path") or ""),
        content_path=str(raw.get("content_path") or ""), ratio=_float(raw.get("ratio")),
        seeding_time=_int(raw.get("seeding_time")))


# --- the client ---------------------------------------------------------------------------------------

class QbtClient:
    """One qBittorrent Web UI. One client per thread."""

    def __init__(self, conn: QbtConnection, session: Optional[requests.Session] = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.base_url = normalize_base_url(conn.base_url)
        self.username = conn.username
        self._password = _Secret(conn.password)
        self.verify_tls = bool(conn.verify_tls)
        self.timeout = float(timeout)
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._session.trust_env = False  # no proxy / netrc credentials from the environment
        self._logged_in = False
        self._login_failed: Optional[AuthFailed] = None
        self._version: Optional[str] = None
        if not self.verify_tls:
            _log.warning("qBittorrent: certificate checks are OFF for %s (opt-in)", self.base_url)
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:  # noqa: BLE001
                pass

    def __repr__(self) -> str:
        return f"QbtClient(base_url={self.base_url!r}, username={self.username!r}, password=***)"

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "QbtClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- transport -----------------------------------------------------------------------------------

    def _origin(self) -> str:
        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    def _send(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
              data: Optional[Dict[str, Any]] = None) -> requests.Response:
        url = f"{self.base_url}/api/v2/{path}"
        headers = {"Referer": self.base_url + "/", "Origin": self._origin()}
        try:
            return self._session.request(method, url, params=params, data=data, headers=headers,
                                         timeout=self.timeout, verify=self.verify_tls, allow_redirects=False)
        except requests.exceptions.SSLError:
            raise Unreachable(f"the certificate of {self.base_url} was not accepted (self-signed? turn "
                              "certificate checks off for this connection)") from None
        except requests.exceptions.Timeout:
            raise Unreachable(f"{self.base_url} did not answer in {self.timeout:.0f} s") from None
        except requests.exceptions.RequestException as exc:
            raise Unreachable(f"cannot reach {self.base_url} ({type(exc).__name__})") from None

    def _login(self) -> None:
        if self._login_failed is not None:
            raise self._login_failed          # never try twice with credentials qBittorrent refused
        resp = self._send("POST", "auth/login",
                          data={"username": self.username, "password": self._password.reveal()})
        status, body = resp.status_code, (resp.text or "").strip()
        if status == 403:
            self._login_failed = IpBanned(
                "qBittorrent refused the login (403): this address is banned after too many failed logins "
                "(it lasts an hour); wait, or restart qBittorrent")
            raise self._login_failed
        if status == 204 or (status == 200 and body == "Ok."):    # 5.x: 204 No Content; 4.x: 200 "Ok."
            if not self._session.cookies:
                raise UnexpectedResponse("qBittorrent accepted the login but set no session cookie - is the address "
                                         "the Web UI's (not a proxy that strips cookies)?", status)
            self._logged_in = True
            return
        if status == 401 or body == "Fails.":
            self._login_failed = AuthFailed(
                "qBittorrent refused the user name or password. MangaList does not try again on its own "
                "(repeated failures get this address banned).")
            raise self._login_failed
        if status != 200:
            raise UnexpectedResponse(f"qBittorrent answered the login with HTTP {status}", status)
        raise UnexpectedResponse("the login answer is not qBittorrent's - is the address the Web UI's?", status)

    def _call(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
              data: Optional[Dict[str, Any]] = None) -> requests.Response:
        """One API call: log in first if needed, log in again once on 403. Returns a 2xx / 404 / 409 response."""
        if not self._logged_in:
            self._login()
        resp = self._send(method, path, params, data)
        if resp.status_code == 403:
            _log.info("qBittorrent: session refused; logging in again")
            self._logged_in = False
            self._login()
            resp = self._send(method, path, params, data)
            if resp.status_code == 403:
                self._logged_in = False
                raise AuthFailed("qBittorrent refuses this session even after a new login (403): check the "
                                 "Web UI's CSRF / host-header settings")
        status = resp.status_code
        if status >= 500:
            raise UnexpectedResponse(f"qBittorrent server error (HTTP {status})", status)
        if status not in (200, 404, 409):
            raise UnexpectedResponse(f"unexpected answer from qBittorrent (HTTP {status}) for {path}", status)
        return resp

    def _json(self, resp: requests.Response, what: str) -> Any:
        if resp.status_code != 200:
            raise UnexpectedResponse(f"qBittorrent answered {what} with HTTP {resp.status_code}", resp.status_code)
        try:
            return resp.json()
        except ValueError:
            raise UnexpectedResponse(f"qBittorrent's answer to {what} is not JSON", resp.status_code) from None

    def _ok(self, resp: requests.Response, what: str) -> None:
        if not 200 <= resp.status_code < 300:              # 5.x answers some actions with an empty 2xx
            raise UnexpectedResponse(f"qBittorrent refused {what} (HTTP {resp.status_code})", resp.status_code)

    # --- TorrentClient -------------------------------------------------------------------------------

    def version(self) -> str:
        """Log in and return the version (``v5.2.4``): the connection test."""
        resp = self._call("GET", "app/version")
        text = (resp.text or "").strip()
        if resp.status_code != 200 or not _VERSION.match(text):
            raise UnexpectedResponse("the answer to the version request is not qBittorrent's", resp.status_code)
        self._version = text
        return text

    def ensure_category(self, name: str, save_path: str) -> None:
        """Make sure category ``name`` exists and saves to ``save_path`` (create it, or edit a different path)."""
        name = str(name or "").strip()
        if not name:
            raise ValueError("a category needs a name")
        categories = self._json(self._call("GET", "torrents/categories"), "the category list")
        if not isinstance(categories, dict):
            raise UnexpectedResponse("qBittorrent's category list is not an object")
        existing = categories.get(name)
        if existing is None:
            resp = self._call("POST", "torrents/createCategory", data={"category": name, "savePath": save_path})
            self._ok(resp, f"creating the category {name!r}")
            _log.info("qBittorrent: created category %r", name)
        elif not _same_path(str((existing or {}).get("savePath") or ""), save_path):
            resp = self._call("POST", "torrents/editCategory", data={"category": name, "savePath": save_path})
            self._ok(resp, f"changing the save path of the category {name!r}")
            _log.info("qBittorrent: category %r now saves to %s", name, save_path)

    def add(self, url: str, *, category: str) -> None:
        """Add a torrent by ``.torrent`` URL or magnet link into ``category`` (one link; never several)."""
        link = str(url or "").strip()
        if not link.lower().startswith(_ADD_SCHEMES) or any(c in link for c in "\r\n\t "):
            raise ValueError("add takes one magnet link or http(s) .torrent URL")
        category = str(category or "").strip()
        if not category:
            raise ValueError("add needs a category")
        resp = self._call("POST", "torrents/add", data={"urls": link, "category": category, "autoTMM": "true"})
        if resp.status_code == 409 or (resp.text or "").strip() == "Fails.":
            raise TorrentRejected("qBittorrent did not add the torrent: the link is not a valid torrent, or it is "
                                  "already in qBittorrent")
        self._ok(resp, "adding the torrent")

    def torrents(self, category: str) -> List[TorrentInfo]:
        """The torrents of exactly this category (a sub-category's torrents are not included)."""
        category = str(category or "").strip()
        if not category:
            raise ValueError("a category is required (an empty one would list the uncategorised torrents)")
        rows = self._json(self._call("GET", "torrents/info", params={"category": category}), "the torrent list")
        if not isinstance(rows, list):
            raise UnexpectedResponse("qBittorrent's torrent list is not a list")
        return [info for info in (_torrent_info(r) for r in rows if isinstance(r, dict))
                if info.category == category and info.info_hash]

    def files(self, info_hash: str) -> List[TorrentFile]:
        h = _hash(info_hash)
        resp = self._call("GET", "torrents/files", params={"hash": h})
        if resp.status_code == 404:
            raise TorrentNotFound("qBittorrent has no torrent with that info hash")
        rows = self._json(resp, "the file list")
        if not isinstance(rows, list):
            raise UnexpectedResponse("qBittorrent's file list is not a list")
        return [TorrentFile(name=str(r.get("name") or "").replace("\\", "/"), size=_int(r.get("size")),
                            progress=_float(r.get("progress"))) for r in rows if isinstance(r, dict)]

    def delete(self, info_hash: str, *, delete_files: bool) -> None:
        """Ask qBittorrent to delete ONE torrent (and its data when ``delete_files``), only when it is in the
        ``mangalist`` category. Raises :class:`DeleteRefused` otherwise and :class:`TorrentNotFound` when it is
        gone already."""
        h = _hash(info_hash)
        rows = self._json(self._call("GET", "torrents/info", params={"hashes": h}), "the torrent")
        found = [r for r in rows if isinstance(r, dict) and str(r.get("hash") or "").lower() == h] \
            if isinstance(rows, list) else []
        if not found:
            raise TorrentNotFound("qBittorrent has no torrent with that info hash")
        category = str(found[0].get("category") or "")
        if category != QBITTORRENT_CATEGORY:
            raise DeleteRefused(f"this torrent is not in the {QBITTORRENT_CATEGORY!r} category; MangaList only "
                                "deletes torrents it added itself")
        resp = self._call("POST", "torrents/delete",
                          data={"hashes": h, "deleteFiles": "true" if delete_files else "false"})
        self._ok(resp, "deleting the torrent")
        _log.info("qBittorrent: deleted torrent %s (files: %s)", h, bool(delete_files))


__all__ = ["AuthFailed", "DeleteRefused", "IpBanned", "QbtClient", "QbtError", "TorrentNotFound", "TorrentRejected",
           "UnexpectedResponse", "Unreachable", "normalize_base_url"]
