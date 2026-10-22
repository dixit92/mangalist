"""MangaList's chapter client over its own extension host: :class:`mangalist.downloads.contracts.ChapterClient` on the
host's Suwayomi-compatible ``/compat`` API (``extension-host/COMPAT.md``). No Qt.

:class:`ExtensionsChapterClient` answers every call MangaList makes of :class:`mangalist.services.suwayomi.SuwayomiClient`
(the ChapterClient protocol plus :meth:`~ExtensionsChapterClient.server_settings`) with **the same dataclasses and the same
semantics**, so the chapters panel, Check for new chapters, the find pass, the chapter arrivals pass and Retry / Cancel
work unchanged when the owner picks "Built-in extensions" for chapter downloads:

- chapter numbers are exact decimal strings formed as the Suwayomi client forms them (``'2'``, ``'10.5'``, ``'-1'`` for
  none; the answer is parsed with ``parse_float=Decimal``). A number that travelled as a widened 32-bit float
  (``1.100000023841858``) is read back as the float it was (``'1.1'``): Suwayomi's own numbers are 32-bit floats too;
- the source's display name is the host's ``displayName`` (``MangaDex (EN)``): the folder its downloads land in;
- a source that lists no chapters is an empty chapter list, never an error (Suwayomi's "No chapters found" is handled the
  same way when the host passes it on);
- errors are the Suwayomi client's own types (:class:`~mangalist.services.suwayomi.SuwayomiError` and its subclasses), so
  every caller's ``except SuwayomiError`` holds: the host down is :class:`HostUnreachable` (an ``Unreachable``), an
  unknown id or a failing source (HTTP 404 / 502) is :class:`HostRefused` (a ``GraphQLError``), any other HTTP error or a
  non-JSON answer :class:`HostAnswer` (an ``UnexpectedResponse``). The messages name the extension host, never Suwayomi.

The host binds 127.0.0.1 and has no login (it runs inside MangaList's container); the connection's user name and password
are not used.
"""

from __future__ import annotations

import json
import logging
import struct
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

from ...downloads.contracts import (
    MangaChapters,
    QueuedChapter,
    SuwayomiChapter,
    SuwayomiConnection,
    SuwayomiManga,
    SuwayomiSource,
)
from ...http_identity import USER_AGENT
from ..suwayomi.client import NO_CHAPTERS_MESSAGE, GraphQLError, UnexpectedResponse, Unreachable, exact

_log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:4570"
DEFAULT_TIMEOUT = 30.0
#: Asking a source (a search, a chapter list) can take a while; a Cloudflare challenge solved by FlareSolverr first
#: takes up to a minute on its own.
FETCH_TIMEOUT = 150.0
NAME = "the extension host"


# --- errors (the Suwayomi client's types, so callers' except clauses hold) -----------------------------------------

class HostUnreachable(Unreachable):
    """The extension host is not running, or not reachable at its address."""


class HostAnswer(UnexpectedResponse):
    """An HTTP error other than 404 / 502 (a bad call: 400), or an answer that is not the host's JSON."""

    def __init__(self, message: str, status: Optional[int] = None, kind: str = ""):
        super().__init__(message, status)
        self.kind = kind


class HostRefused(GraphQLError):
    """HTTP 404 (an unknown manga, chapter or source) or 502 (the extension or its site failed): the host's message."""

    def __init__(self, message: str, status: Optional[int] = None, kind: str = ""):
        super().__init__(message)
        self.status = status
        self.kind = kind


# --- helpers ----------------------------------------------------------------------------------------------------

def normalize_host_url(url: str) -> str:
    """``127.0.0.1:4570`` -> ``http://127.0.0.1:4570``; no trailing slash, no ``/compat``. Refuses credentials, queries
    and other schemes."""
    text = str(url or "").strip() or DEFAULT_URL
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("the extension host's address must start with http:// or https://")
    if not parts.hostname:
        raise ValueError("the extension host's address has no host name")
    if parts.username or parts.password:
        raise ValueError("the extension host has no login: put no user name or password in the address")
    if parts.query or parts.fragment:
        raise ValueError("the extension host's address must not contain '?' or '#'")
    path = parts.path.rstrip("/")
    if path.endswith("/compat"):
        path = path[: -len("/compat")]
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def _float32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", value))[0]


def chapter_number(value: Any) -> str:
    """The exact chapter number string the Suwayomi client would give for this answer value (:func:`exact`). A value
    with more than six decimals that is a 32-bit float widened to a double (``1.100000023841858``) is read as that
    float's shortest form (``1.1``) - Suwayomi stores chapter numbers as 32-bit floats and prints them shortest."""
    if isinstance(value, Decimal) and value.is_finite():
        exponent = value.normalize().as_tuple().exponent
        if isinstance(exponent, int) and exponent < -6:
            as_float = float(value)
            try:
                single = _float32(as_float)
            except (OverflowError, struct.error):
                return exact(value)
            if single == as_float:
                for digits in range(1, 10):
                    text = f"{as_float:.{digits}g}"
                    if _float32(float(text)) == single:
                        return exact(Decimal(text))
    return exact(value)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if not isinstance(value, bool) else default
    except (TypeError, ValueError, InvalidOperation):
        return default


def source_of(node: Mapping[str, Any]) -> SuwayomiSource:
    ext = node.get("extension")
    pkg = _text(ext.get("pkgName") or ext.get("pkg")) if isinstance(ext, Mapping) else _text(ext)
    return SuwayomiSource(id=_text(node.get("id")), name=_text(node.get("name")),
                          display_name=_text(node.get("displayName")) or _text(node.get("name")),
                          lang=_text(node.get("lang")), extension=pkg)


def manga_of(node: Mapping[str, Any]) -> SuwayomiManga:
    return SuwayomiManga(id=_int(node.get("id")), title=_text(node.get("title")), url=_text(node.get("url")),
                         source_id=_text(node.get("sourceId")), in_library=bool(node.get("inLibrary")))


def chapter_of(node: Mapping[str, Any]) -> SuwayomiChapter:
    scanlator = _text(node.get("scanlator")).strip() or None
    downloaded = node.get("downloaded") if "downloaded" in node else node.get("isDownloaded")
    upload = node.get("uploadDate")
    return SuwayomiChapter(id=_int(node.get("id")), manga_id=_int(node.get("mangaId")), name=_text(node.get("name")),
                           number=chapter_number(node.get("chapterNumber")), scanlator=scanlator,
                           url=_text(node.get("url")), real_url=_text(node.get("realUrl")),
                           upload_date=_text(_int(upload) if isinstance(upload, (int, Decimal)) else upload),
                           downloaded=bool(downloaded), source_order=_int(node.get("sourceOrder")))


def queue_of(status: Any) -> List[QueuedChapter]:
    items = status.get("queue") if isinstance(status, Mapping) else status
    out = []
    for item in items or ():
        if not isinstance(item, Mapping):
            continue
        chapter_id = item.get("chapterId")
        if chapter_id is None and isinstance(item.get("chapter"), Mapping):
            chapter_id = item["chapter"].get("id")
        try:
            progress = float(item.get("progress") or 0)
        except (TypeError, ValueError):
            progress = 0.0
        out.append(QueuedChapter(chapter_id=_int(chapter_id), state=_text(item.get("state")), progress=progress,
                                 tries=_int(item.get("tries"))))
    return out


# --- the client -------------------------------------------------------------------------------------------------

class ExtensionsChapterClient:
    """The host's ``/compat`` API for one stored connection (its ``base_url``; no login). *session*: anything with
    ``request(method, url, data=, headers=, timeout=)`` returning an object with ``status_code`` and ``content`` (tests
    pass a fake; default ``requests.Session``)."""

    backend = "extensions"

    def __init__(self, conn: Optional[SuwayomiConnection] = None, *, base_url: Optional[str] = None, session=None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.base_url = normalize_host_url(base_url if base_url is not None else (conn.base_url if conn else ""))
        self._timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def __repr__(self) -> str:
        return f"ExtensionsChapterClient({self.base_url!r})"

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()

    # --- transport ----------------------------------------------------------------------------------------------

    def call(self, method: str, path: str, body: Optional[Mapping[str, Any]] = None, *,
             timeout: Optional[float] = None) -> Any:
        """One call under ``/compat``; its JSON answer (numbers with a fraction as Decimal). Raises a Suwayomi error."""
        url = f"{self.base_url}/compat{path}"
        data = json.dumps(dict(body)).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = self._session.request(method, url, data=data, headers=headers, timeout=timeout or self._timeout)
        except Exception as exc:  # noqa: BLE001 - requests' ConnectionError / Timeout and the like
            raise HostUnreachable(f"{NAME} could not be reached at {self.base_url} ({type(exc).__name__})") from None
        status = int(getattr(resp, "status_code", 0) or 0)
        raw = getattr(resp, "content", None)
        if raw is None:
            raw = (getattr(resp, "text", "") or "").encode("utf-8")
        if status >= 400:
            message, kind = f"HTTP {status}", ""
            try:
                err = json.loads(raw.decode("utf-8"))
                if isinstance(err, Mapping):
                    message, kind = _text(err.get("error")) or message, _text(err.get("type"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                pass
            if message.strip() == NO_CHAPTERS_MESSAGE:
                raise _NoChapters(message, status, kind)
            if status in (404, 502):
                raise HostRefused(f"Extension host: {message[:300]}", status, kind)
            raise HostAnswer(f"{NAME} answered HTTP {status} to {method} /compat{path}: {message[:300]}", status, kind)
        try:
            return json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (ValueError, UnicodeDecodeError, AttributeError):
            raise HostAnswer(f"{NAME}'s answer to {method} /compat{path} is not JSON (is this the extension host's "
                             "address?)", status) from None

    def _object(self, answer: Any, what: str) -> Mapping[str, Any]:
        if not isinstance(answer, Mapping):
            raise HostAnswer(f"{NAME}'s answer has no {what}")
        return answer

    # --- ChapterClient ------------------------------------------------------------------------------------------

    def version(self) -> str:
        """The host's version: the connection test."""
        return _text(self._object(self.call("GET", "/about"), "about").get("version")) or "?"

    def server_settings(self) -> Dict[str, Any]:
        """As :meth:`SuwayomiClient.server_settings`: ``version``, ``download_as_cbz``, ``downloads_path``, ``auth_mode``
        (always ``NONE``), ``flaresolverr`` (on?) and ``flaresolverr_url``."""
        s = self._object(self.call("GET", "/settings"), "settings")
        version = _text(s.get("version"))
        if not version:
            try:
                version = self.version()
            except Exception:  # noqa: BLE001 - the settings are what matters here
                version = ""
        return {"version": version, "download_as_cbz": s.get("downloadAsCbz") is not False,
                "downloads_path": _text(s.get("downloadsPath")), "auth_mode": "NONE",
                "flaresolverr": s.get("flareSolverr") is True or s.get("flareSolverrEnabled") is True,
                "flaresolverr_url": _text(s.get("flareSolverrUrl"))}

    def sources(self) -> List[SuwayomiSource]:
        """Every source of the installed extensions (the host has no local source; one with id 0 is left out anyway)."""
        answer = self.call("GET", "/sources")
        nodes = answer.get("sources") if isinstance(answer, Mapping) else answer
        return [source_of(n) for n in nodes or () if isinstance(n, Mapping) and _text(n.get("id")) not in ("", "0")]

    def search(self, source_id: str, query: str) -> List[SuwayomiManga]:
        """The first page of *source_id*'s search for *query* (MangaDex: ``id:<uuid>`` finds that one manga). A manga
        gets its id here, on first sight."""
        answer = self._object(self.call("POST", "/search", {"sourceId": str(source_id), "query": query, "page": 1},
                                        timeout=FETCH_TIMEOUT), "search result")
        return [manga_of({"sourceId": str(source_id), **m}) for m in answer.get("mangas") or ()
                if isinstance(m, Mapping)]

    def chapters(self, manga_id: int) -> MangaChapters:
        """The manga's chapters, fetched from the source now, and the manga as the host holds it (its details are
        fetched by the host only when it never fetched them). A source that lists no chapters answers with none."""
        try:
            answer = self._object(self.call("POST", f"/manga/{int(manga_id)}/chapters", {},
                                            timeout=FETCH_TIMEOUT), "chapters")
        except _NoChapters:
            answer = {"manga": {"id": int(manga_id)}, "chapters": []}
        manga_node = answer.get("manga") if isinstance(answer.get("manga"), Mapping) else {"id": int(manga_id)}
        source = manga_node.get("source") if isinstance(manga_node.get("source"), Mapping) else {}
        manga = manga_of({**manga_node, "sourceId": manga_node.get("sourceId") or source.get("id")})
        source_name = _text(answer.get("sourceName")) or _text(source.get("displayName"))
        chapters = tuple(chapter_of({"mangaId": int(manga_id), **c}) for c in answer.get("chapters") or ()
                         if isinstance(c, Mapping))
        return MangaChapters(manga=manga, source_name=source_name, chapters=chapters)

    def add_to_library(self, manga_id: int) -> None:
        self.call("POST", f"/manga/{int(manga_id)}/library", {})

    def enqueue(self, chapter_ids: Sequence[int]) -> List[QueuedChapter]:
        """Queue chapters for download; a chapter in the ``ERROR`` state gets another try (as Suwayomi 2.4)."""
        ids = [int(i) for i in chapter_ids]
        if not ids:
            return []
        return queue_of(self.call("POST", "/downloads/enqueue", {"ids": ids}))

    def dequeue(self, chapter_ids: Sequence[int]) -> List[QueuedChapter]:
        ids = [int(i) for i in chapter_ids]
        if not ids:
            return []
        return queue_of(self.call("POST", "/downloads/dequeue", {"ids": ids}))

    def queue(self) -> List[QueuedChapter]:
        """The download queue (a finished chapter LEAVES it and reads ``downloaded``)."""
        return queue_of(self.call("GET", "/downloads"))

    def chapters_by_id(self, chapter_ids: Sequence[int]) -> List[SuwayomiChapter]:
        ids = [int(i) for i in chapter_ids]
        if not ids:
            return []
        answer = self.call("POST", "/chapters", {"ids": ids})
        nodes = answer.get("chapters") if isinstance(answer, Mapping) else answer
        return [chapter_of(n) for n in nodes or () if isinstance(n, Mapping)]

    def delete_downloaded(self, chapter_ids: Sequence[int]) -> None:
        """The host deletes its CBZ (and the folders left empty) and marks the chapters not downloaded; it answers the
        same when the file is already gone."""
        ids = [int(i) for i in chapter_ids]
        if ids:
            self.call("POST", "/chapters/delete-downloaded", {"ids": ids})


class _NoChapters(HostRefused):
    """The host passed on a source's "No chapters found": an empty chapter list for :meth:`chapters`."""


def client_from_connection(conn: SuwayomiConnection, **kwargs) -> ExtensionsChapterClient:
    return ExtensionsChapterClient(conn, **kwargs)
