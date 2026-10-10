"""A Suwayomi-Server GraphQL client: :class:`mangalist.downloads.contracts.ChapterClient`. No Qt.

Pinned against **Suwayomi-Server v2.4.2366** (Stable, revision r2366, image ``ghcr.io/suwayomi/suwayomi-server:v2.4.2366``;
schema introspected and the answers recorded 2026-10-10 - ``tests/fixtures/suwayomi``). One endpoint, ``POST
<base>/api/graphql`` with ``{"query", "variables"}``; every operation MangaList sends is a named constant below, so the
recorded answers and the code cannot drift apart unnoticed:

- :data:`Q_ABOUT` ``aboutServer`` (the connection test), :data:`Q_SETTINGS` ``settings`` (Download as CBZ, the
  downloads path, FlareSolverr - shown, never changed);
- :data:`Q_SOURCES` ``sources`` (the installed sources: id, name, display name, language, extension);
- :data:`M_SEARCH` ``fetchSourceManga(type: SEARCH)`` - on MangaDex ``id:<uuid>`` finds the one manga with that id;
- :data:`M_CHAPTERS` ``fetchMangaAndChapters`` (the manga's details and its chapters, fetched from the source now);
- :data:`M_LIBRARY` ``updateManga(patch: {inLibrary: true})``;
- :data:`M_ENQUEUE` ``enqueueChapterDownloads(ids)``; :data:`Q_QUEUE` ``downloadStatus`` (a finished chapter LEAVES the
  queue); :data:`Q_CHAPTERS_BY_ID` ``chapters(filter: {id: {in}})`` (``isDownloaded``);
- :data:`M_DELETE` ``deleteDownloadedChapters(ids)`` - Suwayomi deletes its downloaded copy (and empty folders) and
  marks the chapters not downloaded; it answers the same when the file is already gone.

Rules:

- **Login**: none, or HTTP basic auth (Suwayomi's ``authMode: BASIC_AUTH``). A 401 is :class:`AuthFailed` and is not
  retried. Suwayomi's other modes (``SIMPLE_LOGIN``: a cookie session; ``UI_LOGIN``: JWT) are not supported by the MVP.
- **The password** is held in a :class:`_Secret`; it never appears in a ``repr``, a log line, an exception message or a
  URL (it travels only in the ``Authorization`` header).
- **Numbers are exact**: the answer is parsed with ``parse_float=Decimal``, so ``chapterNumber`` 10.5 is ``'10.5'``,
  never a float.
- Suwayomi answers a GraphQL error with HTTP 200 and ``errors``: :class:`GraphQLError` with Suwayomi's first message.

Errors are all :class:`SuwayomiError`: :class:`Unreachable`, :class:`AuthFailed`, :class:`UnexpectedResponse`,
:class:`GraphQLError`.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
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

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
#: Fetching a manga's chapters asks the source itself (MangaDex lists hundreds): give it longer.
FETCH_TIMEOUT = 120.0
PINNED_VERSION = "v2.4.2366"
LOCAL_SOURCE_ID = "0"

# --- the operations (pinned; tests/fixtures/suwayomi holds Suwayomi's recorded answers) ------------------------

Q_ABOUT = "query MangaListAbout { aboutServer { name version buildType revision } }"
Q_SETTINGS = ("query MangaListSettings { settings { downloadAsCbz downloadsPath authMode flareSolverrEnabled "
              "flareSolverrUrl } aboutServer { name version buildType revision } }")
Q_SOURCES = ("query MangaListSources { sources { nodes { id name lang displayName contentWarning supportsLatest "
             "extension { pkgName name } } } }")
M_SEARCH = ("mutation MangaListSearch($source: LongString!, $query: String) { fetchSourceManga(input: {source: $source, "
            "type: SEARCH, page: 1, query: $query}) { hasNextPage mangas { id title url sourceId inLibrary initialized "
            "} } }")
M_CHAPTERS = ("mutation MangaListChapters($id: Int!) { fetchMangaAndChapters(input: {id: $id, fetchManga: true, "
              "fetchChapters: true}) { manga { id title url status inLibrary initialized source { id displayName } } "
              "chapters { id name chapterNumber scanlator url realUrl uploadDate sourceOrder isDownloaded pageCount "
              "mangaId } } }")
M_LIBRARY = ("mutation MangaListAddToLibrary($id: Int!) { updateManga(input: {id: $id, patch: {inLibrary: true}}) "
             "{ manga { id inLibrary } } }")
M_ENQUEUE = ("mutation MangaListEnqueue($ids: [Int!]!) { enqueueChapterDownloads(input: {ids: $ids}) { downloadStatus "
             "{ state queue { position progress state tries chapter { id name chapterNumber scanlator isDownloaded "
             "mangaId } manga { id title } } } } }")
Q_QUEUE = "query MangaListQueue { downloadStatus { state queue { position progress state tries chapter { id } } } }"
Q_CHAPTERS_BY_ID = ("query MangaListChaptersById($ids: [Int!]!) { chapters(filter: {id: {in: $ids}}) { nodes { id name "
                    "chapterNumber scanlator isDownloaded url realUrl uploadDate sourceOrder mangaId } } }")
M_DELETE = ("mutation MangaListDeleteDownloaded($ids: [Int!]!) { deleteDownloadedChapters(input: {ids: $ids}) "
            "{ chapters { id isDownloaded } } }")

OPERATIONS = {"aboutServer": Q_ABOUT, "settings": Q_SETTINGS, "sources": Q_SOURCES, "fetchSourceManga": M_SEARCH,
              "fetchMangaAndChapters": M_CHAPTERS, "updateManga": M_LIBRARY, "enqueueChapterDownloads": M_ENQUEUE,
              "downloadStatus": Q_QUEUE, "chapters": Q_CHAPTERS_BY_ID, "deleteDownloadedChapters": M_DELETE}


# --- errors ---------------------------------------------------------------------------------------------------

class SuwayomiError(Exception):
    """Base of every Suwayomi client error. Messages never contain the password."""


class Unreachable(SuwayomiError):
    """Suwayomi could not be reached (DNS, refused, timeout, TLS)."""


class AuthFailed(SuwayomiError):
    """HTTP 401: no login, or a wrong user name / password (Suwayomi's basic auth). Never retried."""


class UnexpectedResponse(SuwayomiError):
    """An HTTP error, or an answer that is not what Suwayomi's GraphQL API sends."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class GraphQLError(SuwayomiError):
    """Suwayomi answered with ``errors`` (e.g. an unknown id, a source that failed)."""


# --- helpers --------------------------------------------------------------------------------------------------

class _Secret:
    """Holds the password; prints as ``***``."""

    __slots__ = ("_value",)

    def __init__(self, value: Optional[str]):
        self._value = value or ""

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "***"

    __str__ = __repr__


def normalize_base_url(url: str) -> str:
    """``host:4567`` -> ``http://host:4567``; no trailing slash, no ``/api/graphql``; refuses credentials, queries and
    other schemes. A reverse-proxy sub-path is kept."""
    text = str(url or "").strip()
    if not text:
        raise ValueError("enter the Suwayomi address, e.g. http://192.168.1.10:4567")
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("the Suwayomi address must start with http:// or https://")
    if not parts.hostname:
        raise ValueError("the Suwayomi address has no host name")
    if parts.username or parts.password:
        raise ValueError("put no user name or password in the address; they are entered separately")
    if parts.query or parts.fragment:
        raise ValueError("the Suwayomi address must not contain '?' or '#'")
    path = parts.path.rstrip("/")
    for tail in ("/api/graphql", "/api"):
        if path.endswith(tail):
            path = path[: -len(tail)]
            break
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def exact(value: Any) -> str:
    """A number from the answer (a Decimal - the parser's - or an int / str) as an exact decimal string: ``Decimal('10.50')``
    -> ``'10.5'``, ``Decimal('1.0')`` -> ``'1'``, ``-1`` stays ``'-1'`` (Suwayomi's "no number")."""
    if isinstance(value, bool) or value is None:
        return "-1"
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "-1"
    if not d.is_finite():
        return "-1"
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if not isinstance(value, bool) else default
    except (TypeError, ValueError):
        return default


def source_of(node: Mapping[str, Any]) -> SuwayomiSource:
    ext = node.get("extension") if isinstance(node.get("extension"), Mapping) else {}
    return SuwayomiSource(id=_text(node.get("id")), name=_text(node.get("name")),
                          display_name=_text(node.get("displayName")) or _text(node.get("name")),
                          lang=_text(node.get("lang")), extension=_text(ext.get("pkgName")))


def manga_of(node: Mapping[str, Any]) -> SuwayomiManga:
    return SuwayomiManga(id=_int(node.get("id")), title=_text(node.get("title")), url=_text(node.get("url")),
                         source_id=_text(node.get("sourceId")), in_library=bool(node.get("inLibrary")))


def chapter_of(node: Mapping[str, Any]) -> SuwayomiChapter:
    scanlator = _text(node.get("scanlator")).strip() or None
    return SuwayomiChapter(id=_int(node.get("id")), manga_id=_int(node.get("mangaId")), name=_text(node.get("name")),
                           number=exact(node.get("chapterNumber")), scanlator=scanlator, url=_text(node.get("url")),
                           real_url=_text(node.get("realUrl")), upload_date=_text(node.get("uploadDate")),
                           downloaded=bool(node.get("isDownloaded")), source_order=_int(node.get("sourceOrder")))


def queue_of(status: Mapping[str, Any]) -> List[QueuedChapter]:
    out = []
    for item in status.get("queue") or ():
        if not isinstance(item, Mapping):
            continue
        chapter = item.get("chapter") if isinstance(item.get("chapter"), Mapping) else {}
        try:
            progress = float(item.get("progress") or 0)
        except (TypeError, ValueError):
            progress = 0.0
        out.append(QueuedChapter(chapter_id=_int(chapter.get("id")), state=_text(item.get("state")),
                                 progress=progress, tries=_int(item.get("tries"))))
    return out


# --- the client -----------------------------------------------------------------------------------------------

class SuwayomiClient:
    """Suwayomi's GraphQL API for one stored connection. *session*: anything with ``post(url, data=..., headers=...,
    auth=..., timeout=...)`` returning an object with ``status_code`` and ``text`` (tests pass a fake; default
    ``requests.Session``)."""

    def __init__(self, conn: SuwayomiConnection, *, session=None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = normalize_base_url(conn.base_url)
        self._username = (conn.username or "").strip()
        self._password = _Secret(conn.password)
        self._timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def __repr__(self) -> str:
        return f"SuwayomiClient({self.base_url!r}, user={'set' if self._username else 'none'})"

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/api/graphql"

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()

    # --- transport ----------------------------------------------------------------------------------------

    def call(self, query: str, variables: Optional[Mapping[str, Any]] = None, *,
             timeout: Optional[float] = None) -> Dict[str, Any]:
        """POST one operation; its ``data`` (a dict). Raises a :class:`SuwayomiError`."""
        body = json.dumps({"query": query, "variables": dict(variables or {})})
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
        auth = (self._username, self._password.reveal()) if self._username else None
        name = query.split("(")[0].split("{")[0].split()[-1] if query.split() else "?"
        try:
            resp = self._session.post(self.endpoint, data=body.encode("utf-8"), headers=headers, auth=auth,
                                      timeout=timeout or self._timeout)
        except Exception as exc:  # noqa: BLE001 - requests' ConnectionError / Timeout / SSLError, and the like
            raise Unreachable(f"Suwayomi could not be reached at {self.base_url} ({type(exc).__name__})") from None
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 401:
            raise AuthFailed("Suwayomi refused the login (HTTP 401): check the user name and password, or Suwayomi's "
                             "authentication mode (MangaList supports none or basic authentication)")
        if status != 200:
            raise UnexpectedResponse(f"Suwayomi answered HTTP {status} to {name}", status)
        try:
            answer = json.loads(getattr(resp, "text", "") or "", parse_float=Decimal)
        except ValueError:
            raise UnexpectedResponse(f"Suwayomi's answer to {name} is not JSON (is this Suwayomi's address?)",
                                     status) from None
        if not isinstance(answer, dict):
            raise UnexpectedResponse(f"Suwayomi's answer to {name} is not a GraphQL answer", status)
        errors = answer.get("errors")
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else errors
            message = first.get("message") if isinstance(first, Mapping) else str(first)
            raise GraphQLError(f"Suwayomi: {_text(message)[:300]}")
        data = answer.get("data")
        if not isinstance(data, dict):
            raise UnexpectedResponse(f"Suwayomi's answer to {name} has no data", status)
        return data

    @staticmethod
    def _field(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = data.get(key)
        if not isinstance(value, Mapping):
            raise UnexpectedResponse(f"Suwayomi's answer has no {key}")
        return value

    # --- ChapterClient --------------------------------------------------------------------------------------

    def version(self) -> str:
        """The server's version (``v2.4.2366``): the connection test."""
        about = self._field(self.call(Q_ABOUT), "aboutServer")
        return _text(about.get("version")) or "?"

    def server_settings(self) -> Dict[str, Any]:
        """What MangaList shows about Suwayomi's own settings: ``version``, ``download_as_cbz``, ``downloads_path``,
        ``auth_mode``, ``flaresolverr`` (on?) and ``flaresolverr_url``."""
        data = self.call(Q_SETTINGS)
        settings = self._field(data, "settings")
        about = data.get("aboutServer") if isinstance(data.get("aboutServer"), Mapping) else {}
        return {"version": _text(about.get("version")), "download_as_cbz": settings.get("downloadAsCbz") is True,
                "downloads_path": _text(settings.get("downloadsPath")), "auth_mode": _text(settings.get("authMode")),
                "flaresolverr": settings.get("flareSolverrEnabled") is True,
                "flaresolverr_url": _text(settings.get("flareSolverrUrl"))}

    def sources(self) -> List[SuwayomiSource]:
        """Every source Suwayomi has (from its installed extensions), the local source left out."""
        nodes = self._field(self.call(Q_SOURCES), "sources").get("nodes") or ()
        return [source_of(n) for n in nodes if isinstance(n, Mapping) and _text(n.get("id")) != LOCAL_SOURCE_ID]

    def search(self, source_id: str, query: str) -> List[SuwayomiManga]:
        """The first page of *source_id*'s search for *query* (MangaDex: ``id:<uuid>`` finds that one manga)."""
        data = self.call(M_SEARCH, {"source": str(source_id), "query": query}, timeout=FETCH_TIMEOUT)
        mangas = self._field(data, "fetchSourceManga").get("mangas") or ()
        return [manga_of(m) for m in mangas if isinstance(m, Mapping)]

    def chapters(self, manga_id: int) -> MangaChapters:
        """The manga (details fetched now) and its chapters, fetched from the source now."""
        data = self._field(self.call(M_CHAPTERS, {"id": int(manga_id)}, timeout=FETCH_TIMEOUT), "fetchMangaAndChapters")
        manga_node = data.get("manga") if isinstance(data.get("manga"), Mapping) else {}
        source = manga_node.get("source") if isinstance(manga_node.get("source"), Mapping) else {}
        manga = manga_of({**manga_node, "sourceId": manga_node.get("sourceId") or source.get("id")})
        chapters = tuple(chapter_of({"mangaId": manga_id, **c}) for c in data.get("chapters") or ()
                         if isinstance(c, Mapping))
        return MangaChapters(manga=manga, source_name=_text(source.get("displayName")), chapters=chapters)

    def add_to_library(self, manga_id: int) -> None:
        self.call(M_LIBRARY, {"id": int(manga_id)})

    def enqueue(self, chapter_ids: Sequence[int]) -> List[QueuedChapter]:
        ids = [int(i) for i in chapter_ids]
        if not ids:
            return []
        data = self._field(self.call(M_ENQUEUE, {"ids": ids}), "enqueueChapterDownloads")
        return queue_of(self._field(data, "downloadStatus"))

    def queue(self) -> List[QueuedChapter]:
        return queue_of(self._field(self.call(Q_QUEUE), "downloadStatus"))

    def chapters_by_id(self, chapter_ids: Sequence[int]) -> List[SuwayomiChapter]:
        ids = [int(i) for i in chapter_ids]
        if not ids:
            return []
        nodes = self._field(self.call(Q_CHAPTERS_BY_ID, {"ids": ids}), "chapters").get("nodes") or ()
        return [chapter_of(n) for n in nodes if isinstance(n, Mapping)]

    def delete_downloaded(self, chapter_ids: Sequence[int]) -> None:
        ids = [int(i) for i in chapter_ids]
        if ids:
            self.call(M_DELETE, {"ids": ids})


def client_from_connection(conn: SuwayomiConnection, **kwargs) -> SuwayomiClient:
    """The client for a stored connection (the downloads job and the GUI's backend build theirs here)."""
    return SuwayomiClient(conn, **kwargs)


ClientFactory = Callable[[SuwayomiConnection], SuwayomiClient]
