"""The client of MangaList's extension host (``extension-host/``, prototype). No Qt.

The host runs Mihon / Tachiyomi extensions on a JVM and answers a small local JSON API (it binds 127.0.0.1 and has
no login). Source ids are 64-bit numbers and travel as strings. Endpoints (``extension-host/.../ApiServer.kt``):

- ``GET /health``; ``GET /repo`` (every listed repository's extensions, each naming its repository; ``?url=`` one)
- ``GET /repos``, ``POST /repos {url}`` (the index is fetched and checked first), ``DELETE /repos {url}``: the host's
  repository list (keiyoushi's ``index.json`` until changed); each repository's own signing key is enforced
- ``GET /extensions``; ``POST /extensions/install {pkg, repo?, format: jar|apk}``; ``POST /extensions/update {pkg,
  waitSeconds?}`` (only from the repository it was installed from; HTTP 409 while one of its sources downloads a
  chapter); ``POST /extensions/uninstall {pkg}``
- ``GET /sources``; ``GET /sources/{id}/search?q=&page=``, ``/popular?page=``, ``/latest?page=``
- ``POST /sources/{id}/manga {url, title}``, ``/chapters {url, title}``, ``/pages {url}`` (``?resolve=true``),
  ``/image {index, url, imageUrl}`` (the image's bytes)

Errors answer ``{"error", "type"}``: 400 a bad call, 404 an unknown source / endpoint / package, 409 busy (a chapter of the
extension's sources is downloading; try again later) or not allowed now, 502 the source (or the network behind it)
failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

DEFAULT_URL = "http://127.0.0.1:4570"
DEFAULT_TIMEOUT = 120.0           # a chapter list or an install can take a while on a slow source


class ExtensionHostError(Exception):
    """Base of every extension host client error. *status*: the HTTP status, if the host answered."""

    def __init__(self, message: str, status: Optional[int] = None, kind: str = ""):
        super().__init__(message)
        self.status = status
        self.kind = kind


class HostUnreachable(ExtensionHostError):
    """The host is not running or not reachable."""


class NotFound(ExtensionHostError):
    """HTTP 404: an unknown source, extension or endpoint."""


class SourceFailed(ExtensionHostError):
    """HTTP 502: the extension (or the site behind it) failed; the message is the extension's."""


class Busy(ExtensionHostError):
    """HTTP 409: not now - a chapter of the extension's sources is downloading (an update or removal waits for it), or
    the extension is installed from another repository / its repository was removed."""


@dataclass(frozen=True)
class RepoSource:
    id: str
    name: str
    lang: str
    base_url: str = ""


@dataclass(frozen=True)
class RepoExtension:
    """One extension of a repository index."""

    name: str
    pkg: str
    version: str
    lib_version: str
    lang: str
    nsfw: bool
    apk_url: str
    jar_url: Optional[str]
    sources: Tuple[RepoSource, ...] = ()
    repo: str = ""                    # the repository index it is listed in
    repo_name: str = ""


@dataclass(frozen=True)
class Repository:
    """One repository of the host's list (``GET /repos``)."""

    url: str
    name: str
    signing_key: Optional[str] = None     # SHA-256 its extensions must be signed with
    extensions: Optional[int] = None      # at the last fetch (None: never fetched)
    last_fetch: Optional[int] = None      # ms since the epoch
    error: Optional[str] = None           # why the last fetch failed (or the key changed)


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    lang: str
    base_url: Optional[str] = None
    supports_latest: bool = False
    extension: Optional[str] = None


@dataclass(frozen=True)
class Extension:
    pkg: str
    name: str
    version: str
    lib_version: str
    nsfw: int = 0
    sources: Tuple[Source, ...] = ()
    repo: str = ""                    # the repository it was installed from (its updates come only from there)
    repo_name: str = ""
    repo_listed: bool = True          # False: that repository was removed from the list (no updates)
    repo_version: Optional[str] = None
    update_available: bool = False
    signature_hash: Optional[str] = None
    url: str                          # the source's own path for the manga (what every later call takes)
    title: str
    thumbnail_url: Optional[str] = None
    author: Optional[str] = None
    artist: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    status: int = 0                   # Tachiyomi's: 0 unknown, 1 ongoing, 2 completed, 3 licensed, ...
    web_url: Optional[str] = None


@dataclass(frozen=True)
class MangasPage:
    mangas: Tuple[Manga, ...]
    has_next_page: bool


@dataclass(frozen=True)
class Chapter:
    url: str
    name: str
    number: float                     # -1 when neither the source nor the name says
    scanlator: Optional[str]
    date_upload: int                  # ms since the epoch; 0 when unknown
    web_url: Optional[str] = None


@dataclass(frozen=True)
class Page:
    index: int
    url: str
    image_url: Optional[str]


#: A source preference's kinds (Mihon's ``androidx.preference`` classes, as the host names them; the class names are
#: read too): what the Extensions page can edit.
PREF_LIST, PREF_CHECKBOX, PREF_SWITCH, PREF_EDITTEXT, PREF_MULTISELECT = (
    "list", "checkbox", "switch", "edittext", "multiselect")
_PREF_KINDS = {"listpreference": PREF_LIST, "list": PREF_LIST, "checkboxpreference": PREF_CHECKBOX,
               "checkbox": PREF_CHECKBOX, "switchpreferencecompat": PREF_SWITCH, "switchpreference": PREF_SWITCH,
               "switch": PREF_SWITCH, "edittextpreference": PREF_EDITTEXT, "edittext": PREF_EDITTEXT,
               "multiselectlistpreference": PREF_MULTISELECT, "multiselect": PREF_MULTISELECT}


@dataclass(frozen=True)
class SourcePreference:
    """One setting of a source (MangaDex's languages, image quality, ...), as the extension declares it."""

    position: int                     # its place in the source's list (Suwayomi's way of naming one)
    key: str
    kind: str                         # PREF_* ('' for a kind MangaList cannot edit: shown, read-only)
    title: str
    summary: str = ""
    value: Any = None                 # str (list, edittext), bool (checkbox, switch), tuple of str (multiselect)
    default: Any = None
    entries: Tuple[str, ...] = ()     # list / multiselect: what the owner sees ...
    entry_values: Tuple[str, ...] = ()    # ... and what is stored
    visible: bool = True
    enabled: bool = True


def _pref_value(kind: str, value: Any) -> Any:
    if kind in (PREF_CHECKBOX, PREF_SWITCH):
        return bool(value) if not isinstance(value, str) else value.strip().lower() == "true"
    if kind == PREF_MULTISELECT:
        return tuple(str(v) for v in value or ()) if not isinstance(value, str) else (value,) if value else ()
    return None if value is None else str(value)


def _preference(d: Mapping[str, Any], index: int) -> SourcePreference:
    raw_kind = str(d.get("type") or d.get("kind") or "").replace("_", "").strip().lower()
    kind = _PREF_KINDS.get(raw_kind.rsplit(".", 1)[-1], "")
    value = d.get("value", d.get("currentValue"))
    default = d.get("default", d.get("defaultValue"))
    return SourcePreference(
        position=int(d.get("position", index)), key=str(d.get("key") or ""), kind=kind,
        title=str(d.get("title") or d.get("key") or ""), summary=str(d.get("summary") or ""),
        value=_pref_value(kind, value if value is not None else default), default=_pref_value(kind, default),
        entries=tuple(str(e) for e in d.get("entries") or ()),
        entry_values=tuple(str(e) for e in d.get("entryValues") or d.get("entries") or ()),
        visible=d.get("visible", True) is not False, enabled=d.get("enabled", True) is not False)


def _source(d: Mapping[str, Any]) -> Source:
    return Source(str(d["id"]), d["name"], d.get("lang") or "", d.get("baseUrl"), bool(d.get("supportsLatest")),
                  d.get("extension"))


def _manga(d: Mapping[str, Any]) -> Manga:
    return Manga(d["url"], d.get("title") or "", d.get("thumbnailUrl"), d.get("author"), d.get("artist"),
                 d.get("description"), d.get("genre"), int(d.get("status") or 0), d.get("webUrl"))


def _chapter(d: Mapping[str, Any]) -> Chapter:
    return Chapter(d["url"], d.get("name") or "", float(d.get("chapterNumber", -1)), d.get("scanlator"),
                   int(d.get("dateUpload") or 0), d.get("webUrl"))


def _extension(d: Mapping[str, Any]) -> Extension:
    return Extension(d["pkg"], d["name"], d["version"], d.get("libVersion") or "", int(d.get("nsfw") or 0),
                     tuple(_source(s) for s in d.get("sources") or ()), repo=str(d.get("repo") or ""),
                     repo_name=str(d.get("repoName") or ""), repo_listed=d.get("repoListed", True) is not False,
                     repo_version=d.get("repoVersion"), update_available=bool(d.get("updateAvailable")),
                     signature_hash=d.get("signatureHash"))


def _repo_extension(d: Mapping[str, Any]) -> RepoExtension:
    return RepoExtension(d["name"], d["pkg"], d.get("version") or "", d.get("libVersion") or "", d.get("lang") or "",
                         bool(d.get("nsfw")), d.get("apkUrl") or "", d.get("jarUrl"),
                         tuple(RepoSource(str(s["id"]), s["name"], s.get("lang") or "", s.get("baseUrl") or "")
                               for s in d.get("sources") or ()), str(d.get("repo") or ""), str(d.get("repoName") or ""))


def _repository(d: Mapping[str, Any]) -> Repository:
    def _int(key: str) -> Optional[int]:
        value = d.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    return Repository(str(d["url"]), str(d.get("name") or d["url"]), d.get("signingKey"), _int("extensions"),
                      _int("lastFetch"), d.get("error"))


class ExtensionHostClient:
    """The host's API. *session*: anything with ``request(method, url, data=, headers=, timeout=)`` returning an
    object with ``status_code``, ``content`` and ``headers`` (tests pass a fake; default ``requests.Session``)."""

    def __init__(self, base_url: str = DEFAULT_URL, *, session=None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self._timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def __repr__(self) -> str:
        return f"ExtensionHostClient({self.base_url!r})"

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()

    # --- transport ------------------------------------------------------------------------------------------------

    def _call(self, method: str, path: str, body: Optional[Mapping[str, Any]] = None, *,
              raw: bool = False) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(dict(body)).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = self._session.request(method, url, data=data, headers=headers, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - requests' ConnectionError / Timeout and the like
            raise HostUnreachable(f"the extension host could not be reached at {self.base_url} "
                                  f"({type(exc).__name__})") from None
        status = int(getattr(resp, "status_code", 0) or 0)
        if status >= 400:
            message, kind = f"HTTP {status}", ""
            try:
                err = json.loads(resp.content.decode("utf-8"))
                message, kind = str(err.get("error") or message), str(err.get("type") or "")
            except (ValueError, AttributeError, UnicodeDecodeError):
                pass
            cls = {404: NotFound, 409: Busy, 502: SourceFailed}.get(status, ExtensionHostError)
            raise cls(message, status, kind)
        if raw:
            return resp
        try:
            return json.loads(resp.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ExtensionHostError(f"the extension host answered something that is not JSON ({path})", status) \
                from None

    # --- the host and its extensions --------------------------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return self._call("GET", "/health")

    def repo(self, index_url: Optional[str] = None) -> List[RepoExtension]:
        """Every listed repository's extensions (each names its repository); *index_url*: that repository's only."""
        path = "/repo" + (f"?url={quote(index_url, safe='')}" if index_url else "")
        return [_repo_extension(d) for d in self._call("GET", path)]

    def repos(self) -> List[Repository]:
        """The host's repository list."""
        return [_repository(d) for d in self._call("GET", "/repos")]

    def add_repo(self, url: str) -> Repository:
        """Add a repository: the host fetches and checks its index first (an address that is not an extension index,
        or one without a signing key, is refused: :class:`ExtensionHostError` with the host's reason)."""
        return _repository(self._call("POST", "/repos", {"url": url}))

    def remove_repo(self, url: str) -> Repository:
        """Remove a repository; the extensions installed from it stay installed (and get no updates)."""
        return _repository(self._call("POST", "/repos/remove", {"url": url}))

    def extensions(self) -> List[Extension]:
        return [_extension(d) for d in self._call("GET", "/extensions")]

    def install(self, pkg: str, *, repo: Optional[str] = None, fmt: str = "jar") -> Extension:
        body: Dict[str, Any] = {"pkg": pkg, "format": fmt}
        if repo:
            body["repo"] = repo
        return _extension(self._call("POST", "/extensions/install", body))

    def update(self, pkg: str, *, repo: Optional[str] = None, wait_seconds: int = 0) -> Extension:
        """Install the newer version of an installed extension from the repository it was installed from (its sources
        are loaded again). While a chapter of its sources downloads the host waits up to *wait_seconds*, then answers
        :class:`Busy` (nothing changed)."""
        body: Dict[str, Any] = {"pkg": pkg}
        if repo:
            body["repo"] = repo
        if wait_seconds:
            body["waitSeconds"] = int(wait_seconds)
        return _extension(self._call("POST", "/extensions/update", body))

    def uninstall(self, pkg: str) -> None:
        """Remove an installed extension and its sources."""
        self._call("POST", "/extensions/uninstall", {"pkg": pkg})

    def sources(self) -> List[Source]:
        return [_source(d) for d in self._call("GET", "/sources")]

    def preferences(self, source_id: str) -> List[SourcePreference]:
        """The source's settings (its extension's ``setupPreferenceScreen``), with their current values."""
        got = self._call("GET", f"/sources/{source_id}/preferences")
        items = got.get("preferences") if isinstance(got, Mapping) else got
        return [_preference(d, i) for i, d in enumerate(items or ()) if isinstance(d, Mapping)]

    def set_preference(self, source_id: str, pref: SourcePreference, value: Any) -> List[SourcePreference]:
        """Change one setting (by its key, and its position for a host that names settings Suwayomi's way); the
        source's settings as they are now."""
        if pref.kind == PREF_MULTISELECT:
            value = [str(v) for v in value or ()]
        elif pref.kind in (PREF_CHECKBOX, PREF_SWITCH):
            value = bool(value)
        else:
            value = "" if value is None else str(value)
        got = self._call("POST", f"/sources/{source_id}/preferences",
                         {"key": pref.key, "position": pref.position, "value": value})
        items = got.get("preferences") if isinstance(got, Mapping) else got
        if not isinstance(items, list):
            return self.preferences(source_id)
        return [_preference(d, i) for i, d in enumerate(items) if isinstance(d, Mapping)]

    # --- one source -----------------------------------------------------------------------------------------------

    def search(self, source_id: str, query: str, page: int = 1) -> MangasPage:
        got = self._call("GET", f"/sources/{source_id}/search?q={quote(query, safe='')}&page={int(page)}")
        return MangasPage(tuple(_manga(m) for m in got["mangas"]), bool(got["hasNextPage"]))

    def popular(self, source_id: str, page: int = 1) -> MangasPage:
        got = self._call("GET", f"/sources/{source_id}/popular?page={int(page)}")
        return MangasPage(tuple(_manga(m) for m in got["mangas"]), bool(got["hasNextPage"]))

    def manga(self, source_id: str, url: str, title: str = "") -> Manga:
        return _manga(self._call("POST", f"/sources/{source_id}/manga", {"url": url, "title": title}))

    def chapters(self, source_id: str, manga_url: str, title: str = "") -> List[Chapter]:
        """The manga's chapters, newest first as the source lists them. *title* helps read a chapter number from a
        chapter's name when the source gives none."""
        return [_chapter(d) for d in self._call("POST", f"/sources/{source_id}/chapters",
                                                {"url": manga_url, "title": title})]

    def pages(self, source_id: str, chapter_url: str, *, resolve: bool = False) -> List[Page]:
        """The chapter's pages. *resolve*: ask the source for every image URL now (one request per page on sources
        that need it); else :meth:`image` asks when a page has none."""
        got = self._call("POST", f"/sources/{source_id}/pages" + ("?resolve=true" if resolve else ""),
                         {"url": chapter_url})
        return [Page(int(d["index"]), d.get("url") or "", d.get("imageUrl")) for d in got]

    def image(self, source_id: str, page: Page) -> Tuple[bytes, str]:
        """One page's image (its bytes and content type), fetched by the extension with the source's own headers."""
        resp = self._call("POST", f"/sources/{source_id}/image",
                          {"index": page.index, "url": page.url, "imageUrl": page.image_url}, raw=True)
        headers = getattr(resp, "headers", {}) or {}
        return resp.content, str(headers.get("Content-Type") or headers.get("content-type") or "")


__all__ = ["DEFAULT_URL", "Busy", "Chapter", "Extension", "ExtensionHostClient", "ExtensionHostError", "HostUnreachable",
           "Manga", "MangasPage", "NotFound", "Page", "RepoExtension", "RepoSource", "Repository", "Source", "SourceFailed",
           "SourcePreference", "PREF_CHECKBOX", "PREF_EDITTEXT", "PREF_LIST", "PREF_MULTISELECT", "PREF_SWITCH"]
