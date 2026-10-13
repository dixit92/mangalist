"""Add a series by link (owner, 2026-10-11: "there should be an easier way to add series, including directly pasting
MangaDex or MangaUpdates URL"; "OK for MangaList to create folders in my library"). No Qt: the dialog
(:mod:`mangalist.gui.add_series_dialog`) only asks and shows; everything that reads or writes is here.

**The flow.** The owner pastes a MangaUpdates link, a MangaDex link or a title, picks the library and the folder name.
MangaList then

1. reads the series (:func:`lookup`): MangaUpdates' own record for a MangaUpdates link; MangaDex's public record
   (``GET /manga/<id>``, read only, no login) for a MangaDex link, which also names the series' MangaUpdates page when
   MangaDex lists one (else the owner picks it from MangaUpdates' search, or adds the series without a match);
2. proposes the folder name (:func:`propose_names`: the English title first, Windows-safe, editable);
3. creates the EMPTY folder (:func:`create_series_folder`): only inside a configured library folder, one component, a
   Windows-safe name, never over a path that exists (case-insensitively too, the library is shared over SMB), never
   under a name the library excludes, under the library's ``.mangalist.lock``, owned by the user MangaList runs as;
4. records what the owner chose, so nothing is searched or confirmed again (:func:`add_series`): the series row (made
   now, so its id exists before any scan), the MangaUpdates match as CONFIRMED (the same record "Fix MangaUpdates
   match..." writes), and for a MangaDex link the series' Suwayomi source (:func:`find_mangadex_source` asks
   Suwayomi for ``id:<uuid>`` on the allowed MangaDex sources, the way the chapters panel does, and the choice is
   stored with the same ``mangadex-id`` mark, so the panel lists the chapters without asking);
5. optionally asks MangaPixer to scan the library (the after-filing request, off when the owner switched it off).

The caller rescans the library afterwards; the empty folder is a normal series then (state Wanted: "Missing volumes /
chapters"), found by the Download tab's find pass like any other. The journal has no "create folder" step: the folder is
made with ``mkdir`` (atomic, fails on an existing path) and logged; undoing it is removing the empty folder.

Parsing a MangaUpdates link: the address carries the series' id in base 36 (``/series/<id>/<slug>``), which is the
API's decimal ``series_id`` (``int(id, 36)``, checked live: it is also the number the API reports). The old
``series.html?id=<n>`` form is not an API id (the API answers 404 for it): it is refused with a hint, not guessed.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from . import mu_cache, mu_client
from .http_identity import USER_AGENT
from .store.lock import LockError, RootLock
from .store.names import windows_name_problem, windows_safe_name

_log = logging.getLogger(__name__)

KIND_MANGAUPDATES = "mangaupdates"
KIND_MANGADEX = "mangadex"

MANGADEX_API = "https://api.mangadex.org"
MAX_ASSOCIATED_PROPOSALS = 6        # other Latin-script titles offered besides the main ones
MAX_SEARCH_RESULTS = 15

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_ONLY = re.compile(_UUID, re.I)
_MU_ID = re.compile(r"[0-9a-z]{1,12}", re.I)
_MD_PATH = re.compile(rf"^/(?:title|manga)/(?P<id>{_UUID})(?:/|$)", re.I)
_MU_PATH = re.compile(r"^/series/(?P<id>[0-9a-z]+)(?:/|$)", re.I)
_LOOKS_LIKE_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|(?:www\.)?(?:mangaupdates\.com|mangadex\.org)(?:/|$))", re.I)


class AddSeriesError(Exception):
    """Something the owner can be told in plain words (an unusable link, a folder that cannot be made)."""


class LinkError(AddSeriesError):
    """The text is a link, but not one MangaList can add a series from."""


# --- links ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedLink:
    kind: str                           # KIND_MANGAUPDATES | KIND_MANGADEX
    mu_id: Optional[int] = None         # the API's series_id
    mangadex_id: Optional[str] = None   # lower-case UUID


def mu_id_from_base36(token: str) -> int:
    """MangaUpdates' series id from the base-36 token of its address (and of MangaDex's ``links.mu``)."""
    if not _MU_ID.fullmatch(token or ""):
        raise ValueError(f"not a MangaUpdates series id: {token!r}")
    return int(token, 36)


def base36_from_mu_id(mu_id: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    n, out = int(mu_id), ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or "0"


def parse_link(text: str) -> Optional[ParsedLink]:
    """What a pasted text points at: a :class:`ParsedLink`; None when it is not a link at all (the text is then a
    title to search); :class:`LinkError` when it is a link MangaList cannot use (another site, the old MangaUpdates
    address)."""
    raw = (text or "").strip().strip("<>").strip()
    if not raw:
        return None
    if _UUID_ONLY.fullmatch(raw):
        return ParsedLink(KIND_MANGADEX, mangadex_id=raw.lower())
    if not _LOOKS_LIKE_URL.match(raw) or " " in raw:
        return None
    url = urlparse(raw if "://" in raw else "https://" + raw)
    host = (url.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "mangadex.org":
        m = _MD_PATH.match(url.path)
        if m:
            return ParsedLink(KIND_MANGADEX, mangadex_id=m.group("id").lower())
        raise LinkError("This MangaDex link is not a series page (https://mangadex.org/title/<id>/<name>).")
    if host == "mangaupdates.com":
        m = _MU_PATH.match(url.path)
        if m:
            try:
                return ParsedLink(KIND_MANGAUPDATES, mu_id=mu_id_from_base36(m.group("id")))
            except ValueError:
                pass
        if url.path.rstrip("/") == "/series.html" and parse_qs(url.query).get("id"):
            raise LinkError("This is MangaUpdates' old address form (series.html?id=...), which MangaUpdates' API does "
                            "not know. Open the series page and paste the address from the browser (it looks like "
                            "https://www.mangaupdates.com/series/<id>/<name>).")
        raise LinkError("This MangaUpdates link is not a series page "
                        "(https://www.mangaupdates.com/series/<id>/<name>).")
    raise LinkError("Only MangaUpdates and MangaDex links work here. Paste one of those, or type a title.")


# --- MangaDex (one read-only record) ----------------------------------------------------------------------------

@dataclass
class MangaDexSeries:
    id: str
    title: str                              # English when MangaDex has one, else its main title
    english_title: Optional[str] = None
    alt_titles: List[str] = field(default_factory=list)
    mu_id: Optional[int] = None             # from links.mu (a base-36 token), when MangaDex lists it
    year: Optional[int] = None
    status: Optional[str] = None
    original_language: Optional[str] = None

    @property
    def url(self) -> str:
        return f"https://mangadex.org/title/{self.id}"


def _http_get_json(url: str) -> Dict[str, Any]:
    import requests

    resp = requests.get(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, timeout=15)
    if resp.status_code == 404:
        raise AddSeriesError("MangaDex has no series with that id.")
    resp.raise_for_status()
    return resp.json()


def mangadex_from_record(body: Mapping[str, Any]) -> MangaDexSeries:
    """A :class:`MangaDexSeries` from ``GET /manga/<id>``'s body."""
    data = body.get("data") if isinstance(body, Mapping) else None
    if not isinstance(data, Mapping) or not isinstance(data.get("id"), str):
        raise AddSeriesError("MangaDex answered with something that is not a series.")
    attrs = data.get("attributes") if isinstance(data.get("attributes"), Mapping) else {}
    titles = attrs.get("title") if isinstance(attrs.get("title"), Mapping) else {}
    alts: List[str] = []
    english = titles.get("en") if isinstance(titles.get("en"), str) else None
    for alt in attrs.get("altTitles") or ():
        if not isinstance(alt, Mapping):
            continue
        for lang, text in alt.items():
            if not isinstance(text, str) or not text.strip():
                continue
            if lang == "en" and english is None:
                english = text
            alts.append(text.strip())
    main = next((t for t in ([english] + list(titles.values())) if isinstance(t, str) and t.strip()), None)
    if main is None:
        raise AddSeriesError("MangaDex lists no title for this series.")
    links = attrs.get("links") if isinstance(attrs.get("links"), Mapping) else {}
    mu_id: Optional[int] = None
    if isinstance(links.get("mu"), str):
        try:
            mu_id = mu_id_from_base36(links["mu"].strip())
        except ValueError:
            mu_id = None
    year = attrs.get("year")
    return MangaDexSeries(
        id=data["id"].lower(), title=main.strip(), english_title=english.strip() if english else None,
        alt_titles=list(dict.fromkeys(alts)), mu_id=mu_id,
        year=year if isinstance(year, int) and not isinstance(year, bool) else None,
        status=attrs.get("status") if isinstance(attrs.get("status"), str) else None,
        original_language=attrs.get("originalLanguage") if isinstance(attrs.get("originalLanguage"), str) else None)


def fetch_mangadex(manga_id: str, *, get: Callable[[str], Dict[str, Any]] = _http_get_json) -> MangaDexSeries:
    """MangaDex's record of *manga_id* (one request; *get* returns the parsed body of a URL)."""
    try:
        return mangadex_from_record(get(f"{MANGADEX_API}/manga/{manga_id}"))
    except AddSeriesError:
        raise
    except Exception as exc:  # noqa: BLE001 - the owner is told, the log keeps the type only (no URL, no secret)
        _log.info("Add a series: MangaDex lookup failed (%s)", type(exc).__name__)
        raise AddSeriesError("MangaDex could not be reached or did not answer.") from None


# --- what is known about the series -----------------------------------------------------------------------------

@dataclass
class SeriesInfo:
    """The series as the dialog shows it. ``mu_id`` None: not matched to MangaUpdates (a MangaDex-only series)."""

    title: str                              # MangaUpdates' title, else MangaDex's
    mu_id: Optional[int] = None
    mu_title: Optional[str] = None
    mu_url: str = ""
    kind: Optional[str] = None              # MangaUpdates' type (Manga, Manhwa, ...)
    year: Optional[str] = None
    licensed: Optional[bool] = None
    latest_chapter: Optional[float] = None
    associated: List[str] = field(default_factory=list)
    english_title: Optional[str] = None
    mangadex_id: Optional[str] = None
    mangadex_title: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None  # MangaUpdates' full record, for the progress fields

    @property
    def matched(self) -> bool:
        return self.mu_id is not None

    def summary(self) -> List[str]:
        """The lines the dialog shows under the title."""
        lines = []
        year = (f"({self.year})" if self.kind else self.year) if self.year else None
        what = " ".join(p for p in (self.kind, year) if p)
        if what:
            lines.append(what)
        if self.matched:
            lines.append("Licensed in English" if self.licensed else
                         "Not licensed in English" if self.licensed is False else "Licence unknown")
            if self.latest_chapter is not None:
                n = self.latest_chapter
                lines.append(f"Latest chapter {int(n) if float(n).is_integer() else n}")
        else:
            lines.append("No MangaUpdates match")
        return lines


def info_from_mu(detail: Mapping[str, Any], mangadex: Optional[MangaDexSeries] = None) -> SeriesInfo:
    """A :class:`SeriesInfo` from MangaUpdates' full record (+ MangaDex's, when the link came from there)."""
    title = str(detail.get("title") or "").strip()
    sid = detail.get("series_id")
    if not title or not isinstance(sid, int) or isinstance(sid, bool):
        raise AddSeriesError("MangaUpdates answered with something that is not a series.")
    latest = detail.get("latest_chapter")
    try:
        latest = float(latest) if latest is not None else None
    except (TypeError, ValueError):
        latest = None
    assoc = [str(a.get("title")).strip() for a in detail.get("associated") or ()
             if isinstance(a, Mapping) and str(a.get("title") or "").strip()]
    return SeriesInfo(
        title=title, mu_id=sid, mu_title=title, mu_url=str(detail.get("url") or ""),
        kind=str(detail["type"]) if detail.get("type") else None,
        year=str(detail["year"]) if detail.get("year") else None,
        licensed=detail["licensed"] if isinstance(detail.get("licensed"), bool) else None,
        latest_chapter=latest, associated=list(dict.fromkeys(assoc)),
        english_title=mangadex.english_title if mangadex else None,
        mangadex_id=mangadex.id if mangadex else None, mangadex_title=mangadex.title if mangadex else None,
        detail=dict(detail))


def info_from_mangadex(md: MangaDexSeries) -> SeriesInfo:
    """A series with no MangaUpdates match (yet): MangaDex's own facts."""
    return SeriesInfo(title=md.title, year=str(md.year) if md.year else None, associated=list(md.alt_titles),
                      english_title=md.english_title, mangadex_id=md.id, mangadex_title=md.title)


@dataclass
class Lookup:
    """The outcome of :func:`lookup`: the series (``info``), or MangaUpdates candidates for the owner to pick from."""

    info: Optional[SeriesInfo] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)    # MangaUpdates search records
    mangadex: Optional[MangaDexSeries] = None
    notes: List[str] = field(default_factory=list)


def _get_mu(mu_id: int, get: Callable[[int], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return get(mu_id)
    except Exception as exc:  # noqa: BLE001
        if mu_client.is_not_found(exc):
            raise AddSeriesError("MangaUpdates has no series with that id.") from None
        _log.info("Add a series: MangaUpdates lookup failed (%s)", type(exc).__name__)
        raise AddSeriesError("MangaUpdates could not be reached or did not answer.") from None


def search_mu(query: str,
              search: Callable[..., List[Dict[str, Any]]] = mu_client.search_series) -> List[Dict[str, Any]]:
    """MangaUpdates' search records for *query* (no type filter: the owner picks)."""
    try:
        return [item["record"] for item in search(query, page_size=MAX_SEARCH_RESULTS) if "record" in item]
    except Exception as exc:  # noqa: BLE001
        _log.info("Add a series: MangaUpdates search failed (%s)", type(exc).__name__)
        raise AddSeriesError("MangaUpdates could not be reached or did not answer.") from None


def lookup(text: str, *, mu_get: Callable[[int], Dict[str, Any]] = mu_client.get_series,
           mu_search: Callable[..., List[Dict[str, Any]]] = mu_client.search_series,
           mangadex_get: Callable[[str], Dict[str, Any]] = _http_get_json) -> Lookup:
    """Read what the owner pasted. A MangaUpdates link: its record. A MangaDex link: MangaDex's record and, when
    MangaDex lists the series' MangaUpdates page, that record too; else MangaUpdates' search results for MangaDex's
    title (``candidates``) and ``info`` as a series without a match. A title: the search results only."""
    link = parse_link(text)
    if link is None:
        query = (text or "").strip()
        if not query:
            raise AddSeriesError("Paste a MangaUpdates or MangaDex link, or type a title.")
        out = Lookup(candidates=search_mu(query, mu_search))
        if not out.candidates:
            out.notes.append("MangaUpdates found nothing for that title.")
        return out
    if link.kind == KIND_MANGAUPDATES:
        return Lookup(info=info_from_mu(_get_mu(link.mu_id, mu_get)))
    md = fetch_mangadex(link.mangadex_id, get=mangadex_get)
    out = Lookup(mangadex=md)
    if md.mu_id is not None:
        try:
            out.info = info_from_mu(_get_mu(md.mu_id, mu_get), md)
            return out
        except AddSeriesError as exc:
            out.notes.append(f"MangaDex links a MangaUpdates page, but: {exc}")
    else:
        out.notes.append("MangaDex lists no MangaUpdates page for this series.")
    out.info = info_from_mangadex(md)
    try:
        out.candidates = search_mu(md.english_title or md.title, mu_search)
    except AddSeriesError as exc:
        out.notes.append(str(exc))
    return out


def with_mu_record(look: Lookup, record: Mapping[str, Any], *,
                   mu_get: Callable[[int], Dict[str, Any]] = mu_client.get_series) -> SeriesInfo:
    """The series after the owner picked a MangaUpdates search *record* (its full record is read for the licence and the
    latest chapter); MangaDex's facts are kept when the link came from there."""
    sid = record.get("series_id")
    if not isinstance(sid, int) or isinstance(sid, bool):
        raise AddSeriesError("That search result has no MangaUpdates id.")
    return info_from_mu(_get_mu(sid, mu_get), look.mangadex)


# --- the folder name --------------------------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def clean_folder_name(text: str) -> str:
    """*text* as a folder name every platform of the library takes: ``:`` becomes `` - `` (before a space) or ``-``,
    ``/ \\ |`` become ``-``, ``"`` becomes ``'``, ``< > ? *`` and control characters are dropped, white space is collapsed and
    a trailing dot or space goes; whatever still breaks a Windows rule is replaced (:func:`windows_safe_name`)."""
    s = unicodedata.normalize("NFC", text or "")
    s = re.sub(r":(?=\s)", " -", s).replace(":", "-")
    s = re.sub(r"[/\\|]", "-", s).replace('"', "'")
    s = re.sub(r'[<>?*\x00-\x1f]', "", s)
    s = _WS.sub(" ", s).strip().rstrip(". ").lstrip(".")        # a leading dot would hide the folder from the scan
    s = s.strip()
    return windows_safe_name(s, "_") if s else ""


def _plain(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def _latin(text: str) -> bool:
    return all(ord(c) < 0x250 for c in text)


def propose_names(info: SeriesInfo) -> List[str]:
    """Folder names for the series, best first, each cleaned: the English title (MangaDex's), MangaUpdates' title,
    MangaDex's main title, the library's ``<title> [<English title>]`` form when the two differ, then other Latin-script titles
    MangaUpdates knows. The owner may edit the choice."""
    english = info.english_title
    main = info.mu_title or info.title
    ordered: List[str] = []
    if english:
        ordered.append(english)
    ordered.append(main)
    if info.mangadex_title:
        ordered.append(info.mangadex_title)
    if english and _plain(english) != _plain(main):
        ordered.append(f"{main} [{english}]")
    ordered += [a for a in info.associated if _latin(a)][:MAX_ASSOCIATED_PROPOSALS]
    out: List[str] = []
    seen = set()
    for name in ordered:
        cleaned = clean_folder_name(name)
        if cleaned and _plain(cleaned) not in seen:
            seen.add(_plain(cleaned))
            out.append(cleaned)
    return out


# --- the folder -------------------------------------------------------------------------------------------------

def folder_problem(root, name: str) -> Optional[str]:
    """Why a series folder called *name* cannot be made in library folder *root* (a store ``Root``), or None. Checks the
    name (one component, Windows-safe, not hidden), the library's exclusions and what the library folder already holds
    (same name in any case counts: the library is shared with Windows)."""
    if not name or name != name.strip():
        return "The folder needs a name (no spaces at either end)."
    if name.startswith("."):
        return "A folder name starting with a dot is hidden from the scan."
    problem = windows_name_problem(name)
    if problem:
        return f"{name!r}: {problem}."
    try:
        if root.exclusion_set().excludes(name, True):
            return f"The library excludes a folder called {name!r}: it would never show up."
    except Exception:  # noqa: BLE001 - an exclusion that cannot be read is not a reason to refuse
        _log.debug("Add a series: exclusions of %s unreadable", getattr(root, "name", "?"), exc_info=True)
    folder = Path(root.path)
    if not folder.is_dir():
        return f"The library folder {root.path} is not reachable."
    wanted = unicodedata.normalize("NFC", name).casefold()
    try:
        for entry in os.listdir(folder):
            if unicodedata.normalize("NFC", entry).casefold() == wanted:
                return f"{entry!r} already exists in {root.name}."
    except OSError as exc:
        return f"The library folder cannot be read ({type(exc).__name__})."
    return None


def create_series_folder(root, name: str) -> Path:
    """Make the empty folder *name* in library folder *root* and return it as the scanner will key it (under the
    resolved root). Refuses (:class:`AddSeriesError`) on :func:`folder_problem`, on a library another MangaList is
    writing, and when the path appears meanwhile (``mkdir`` never replaces). The folder belongs to the user MangaList runs as."""
    problem = folder_problem(root, name)
    if problem:
        raise AddSeriesError(problem)
    base = Path(root.path)
    target = base / name
    if os.path.dirname(os.path.normpath(target)) != os.path.normpath(base):      # belt and braces: one component only
        raise AddSeriesError(f"{name!r} is not a single folder name.")
    try:
        with RootLock(base):
            try:
                os.mkdir(target)
            except FileExistsError:
                raise AddSeriesError(f"{name!r} already exists in {root.name}.") from None
    except LockError as exc:
        raise AddSeriesError(f"{exc}. Try again in a moment.") from None
    except OSError as exc:
        raise AddSeriesError(f"The folder could not be made ({type(exc).__name__}: {exc.strerror or exc}).") from None
    _log.info("Add a series: made the folder %r in the library folder %s", name, root.name)
    return Path(base).resolve() / name


def existing_folders_for(mu_id: Optional[int]) -> List[str]:
    """Folders the library already links to MangaUpdates series *mu_id* (the dialog warns before a second one)."""
    if mu_id is None:
        return []
    try:
        return sorted(folder for folder, row in mu_cache.load_all().items() if row.get("mu_id") == mu_id)
    except Exception:  # noqa: BLE001
        _log.debug("Add a series: could not read the links cache", exc_info=True)
        return []


# --- Suwayomi: the series' source ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceChoice:
    """The Suwayomi manga a MangaDex link resolved to: what the chapters panel stores for a series."""

    source_id: str
    source_name: str
    manga_id: int
    manga_title: str


@dataclass
class SourceLookup:
    choice: Optional[SourceChoice] = None
    note: str = ""


def find_mangadex_source(db, mangadex_id: str, *, client_factory: Optional[Callable] = None) -> SourceLookup:
    """Ask Suwayomi for the series by its MangaDex id on the allowed MangaDex sources (reads only; the chapters panel's
    own lookup). Never raises: no Suwayomi, no MangaDex source or no such manga is a note, and the chapters panel finds
    the series later as it does for any other."""
    from .downloads.chapters import allowed_sources, find_manga
    from .services.suwayomi import SuwayomiError, client_from_connection
    from .store.downloads import SuwayomiSettings

    settings = SuwayomiSettings(db)
    conn = settings.connection()
    if conn is None or not conn.base_url:
        return SourceLookup(note="Suwayomi is not set up: the chapters are found when you first open them.")
    try:
        from .downloads.options import load_nyaa_options

        languages = load_nyaa_options(db).source_languages()
    except Exception:  # noqa: BLE001 - English, the default
        languages = ("all", "en")
    client = (client_factory or client_from_connection)(conn)
    try:
        sources = allowed_sources(list(client.sources()), settings.sources(), languages)
        if not any(s.is_mangadex for s in sources):
            return SourceLookup(note="No MangaDex source is allowed in Suwayomi: the chapters are found when you first "
                                     "open them.")
        found = find_manga(client, sources, mangadex_id=mangadex_id, titles=(), title_search=False)
    except SuwayomiError as exc:
        return SourceLookup(note=f"Suwayomi could not be asked ({exc}).")
    except Exception as exc:  # noqa: BLE001
        _log.info("Add a series: Suwayomi lookup failed (%s)", type(exc).__name__)
        return SourceLookup(note="Suwayomi could not be asked.")
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    if found.match is None:
        return SourceLookup(note="Suwayomi's MangaDex source has no such series: the chapters are found when you first "
                                 "open them.")
    m = found.match
    return SourceLookup(SourceChoice(m.source.id, m.source.display_name, int(m.manga.id), m.manga.title))


# --- adding -----------------------------------------------------------------------------------------------------

@dataclass
class AddResult:
    folder: Path
    root_id: Optional[int]
    series_id: Optional[int]
    notes: List[str] = field(default_factory=list)      # what did not work after the folder was made (non-fatal)
    mangapixer: str = ""                                # the scan request's summary ("" when none was asked)


def record_mu_match(folder: Path, info: SeriesInfo, progress: Optional[Mapping[str, Any]] = None) -> None:
    """Store *info*'s MangaUpdates series as the CONFIRMED match of *folder* - what "Fix MangaUpdates match..." writes
    (no score, band or reasons: the owner chose it). *progress* are the record's progress fields as
    :func:`mangalist.gui.mu_worker._detail_progress` builds them (``mu_cache.save_entry``'s keyword arguments)."""
    if info.mu_id is None:
        raise ValueError("the series has no MangaUpdates match")
    titles = list(dict.fromkeys([info.mu_title or info.title, *info.associated]))
    mu_cache.save_entry(folder, info.mu_id, info.mu_title or info.title, info.mu_url, info.licensed, mu_confirmed=True,
                        mu_associated=titles, **dict(progress or {}))


def record_source(db, series_id: int, choice: SourceChoice) -> None:
    """Store the Suwayomi manga as the series' source, marked as found by its MangaDex id (no question asked later)."""
    from .downloads.chapters import HOW_MANGADEX
    from .store.downloads import SuwayomiSettings

    SuwayomiSettings(db).set_series_choice(series_id, source_id=choice.source_id, manga_id=int(choice.manga_id),
                                           manga_title=choice.manga_title, how=HOW_MANGADEX)


def add_series(db, root, name: str, info: SeriesInfo, *, source: Optional[SourceChoice] = None,
               progress: Optional[Mapping[str, Any]] = None,
               request_scans: Optional[Callable[[Any, Sequence[int]], str]] = None) -> AddResult:
    """Make the folder and record the owner's choices (see the module docstring). Raises :class:`AddSeriesError` only
    when the folder could not be made; after that every step is best effort and a failure is a note (the folder is
    there, and a rescan still shows it). *request_scans* ``(db, series ids) -> summary`` asks MangaPixer for a scan
    (default: the after-filing request, which the owner can switch off; None here: not asked)."""
    folder = create_series_folder(root, name)
    result = AddResult(folder=folder, root_id=root.id, series_id=None)
    rel = name
    try:
        result.series_id = db.ensure_series(root.id, rel).id
    except Exception:  # noqa: BLE001
        _log.warning("Add a series: could not make the series row of %r", name, exc_info=True)
        result.notes.append("The series row could not be made; a rescan will make it.")
    if info.matched:
        try:
            record_mu_match(folder, info, progress)
        except Exception:  # noqa: BLE001
            _log.warning("Add a series: could not record the MangaUpdates match of %r", name, exc_info=True)
            result.notes.append("The MangaUpdates match was not saved: use Fix MangaUpdates match... on the series.")
    if source is not None and result.series_id is not None:
        try:
            record_source(db, result.series_id, source)
        except Exception:  # noqa: BLE001
            _log.warning("Add a series: could not record the Suwayomi source of %r", name, exc_info=True)
            result.notes.append("The Suwayomi source was not saved: the chapters panel will find it again.")
    if request_scans is not None and result.series_id is not None:
        try:
            result.mangapixer = request_scans(db, [result.series_id]) or ""
        except Exception:  # noqa: BLE001 - MangaPixer scans on its own schedule anyway
            _log.info("Add a series: the MangaPixer scan request failed", exc_info=True)
            result.mangapixer = "MangaPixer was not asked to scan (it will at its next scan)."
    return result
