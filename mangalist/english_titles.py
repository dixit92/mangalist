"""Official English titles of the library's series - the English part of the folder scheme (owner, 2026-10-16, answer
76: "How does animefetch do it?"). No Qt.

animefetch names a show folder ``<romaji> [<English>]`` with MyAnimeList's official English title
(``alternative_titles.en``: one per entry, the licensed one). MangaUpdates and MangaPixer do not say which of a series'
alternative titles is the English one, so MangaList takes **AniList's English title** (``title.english``: AniList keeps
the licensed title apart from fan titles), as Add series has since 2026.10.38:

- the series' AniList record: MangaPixer's AniList companion (``companions.anilist.id``), else the record MangaDex links
  (``links.al``) - MangaDex's record from MangaPixer's MangaDex companion, else a MangaDex title search whose result
  links back to the series' MangaUpdates page (``links.mu``; :func:`mangalist.add_series.mangadex_for_mu`). A MangaDex
  record that links ANOTHER MangaUpdates series is not used. MangaList's own AniList match (``links_cache.anilist_id``,
  a fuzzy title search kept for the Behind column) is not used either: a wrong match would put a wrong title on a
  folder;
- the English title of that record (:func:`mangalist.anilist_client.english_titles`, 50 records per request).

MangaDex's only English alternative title, when it lists exactly one, is kept too (``mangadex_english``): the last
fallback of the folder scheme, after the folder's own English (see :func:`mangalist.folder_scheme.titles_for`).

**Cached** in the ``english_titles`` table (schema 9), keyed by the series' record (:func:`ref_key`): a scan, a dry run
and the List's Rename pending counts read the cache only - nothing here is asked during a scan. :func:`fill` asks the
network for the series that are not cached (or whose answer is old: :data:`REFRESH_FOUND`, :data:`REFRESH_NONE`,
:data:`RETRY_FAILED`), politely: MangaDex records 100 per request and one request a second, AniList 50 records per
request and under its 30 requests a minute. The Renamer window runs it in the background when it opens (never on the
UI thread); a new title only changes what the Renamer proposes - folders are renamed only by the owner's Rename.
"""

from __future__ import annotations

import datetime as _dt
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlencode

_log = logging.getLogger(__name__)

FOUND, NONE, FAILED = "found", "none", "failed"
REFRESH_FOUND = _dt.timedelta(days=90)      # an English title found is asked again after this (a licence can change it)
REFRESH_NONE = _dt.timedelta(days=30)       # no English title yet: asked again after this (a new licence adds one)
RETRY_FAILED = _dt.timedelta(hours=12)      # the network failed: asked again after this
MANGADEX_BATCH = 100                        # MangaDex's page limit for ``/manga?ids[]=``
MANGADEX_DELAY = 1.0                        # seconds between MangaDex requests (it allows about five a second)
_CONTENT_RATINGS = ("safe", "suggestive", "erotica", "pornographic")   # ids[] answers only the asked ratings


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(when: _dt.datetime) -> str:
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: Optional[str]) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def ref_key(mu_id: Any = None, mangadex_id: Any = None) -> Optional[str]:
    """The cache key of a series' record: ``mu:<MangaUpdates id>``, else ``md:<MangaDex id>`` (a series MangaUpdates does
    not list), else None (no record: nothing to look up)."""
    mu = _int(mu_id)
    if mu is not None:
        return f"mu:{mu}"
    if isinstance(mangadex_id, str) and mangadex_id.strip():
        return f"md:{mangadex_id.strip().lower()}"
    return None


@dataclass(frozen=True)
class SeriesRef:
    """What is known about a series' records, for the lookup: its MangaUpdates id, MangaPixer's MangaDex and AniList
    companions, and its title (a MangaDex title search when no MangaDex id is known)."""

    key: str
    title: str = ""
    mu_id: Optional[int] = None
    mangadex_id: Optional[str] = None
    anilist_id: Optional[int] = None

    @classmethod
    def of(cls, title: Optional[str], ids: Optional[Mapping[str, Any]]) -> Optional["SeriesRef"]:
        """A ref from a title and the ``{mu_id, mangadex_id, anilist_id}`` the Renamer's title lookup returns; None
        without a record id."""
        ids = ids or {}
        mu, md = _int(ids.get("mu_id")), ids.get("mangadex_id")
        md = md.strip().lower() if isinstance(md, str) and md.strip() else None
        key = ref_key(mu, md)
        if key is None:
            return None
        return cls(key, (title or "").strip(), mu, md, _int(ids.get("anilist_id")))


@dataclass(frozen=True)
class EnglishEntry:
    """One cached answer: *english* is AniList's official English title (None: none known), *mangadex_english*
    MangaDex's only English title (None: none, or several - MangaDex mixes official and fan titles)."""

    key: str
    status: str
    checked_at: str
    english: Optional[str] = None
    mangadex_english: Optional[str] = None
    anilist_id: Optional[int] = None
    mangadex_id: Optional[str] = None

    def due(self, now: Optional[_dt.datetime] = None) -> bool:
        """The answer is old enough to ask again (:data:`REFRESH_FOUND` / :data:`REFRESH_NONE` / :data:`RETRY_FAILED`)."""
        when = _parse(self.checked_at)
        if when is None:
            return True
        wait = {FOUND: REFRESH_FOUND, NONE: REFRESH_NONE}.get(self.status, RETRY_FAILED)
        return (now or _now()) - when >= wait


class EnglishTitles:
    """The ``english_titles`` cache of a library database (read whole: a few hundred short rows)."""

    def __init__(self, db):
        self.db = db

    def load(self) -> Dict[str, EnglishEntry]:
        try:
            with self.db.connect() as con:
                rows = con.execute("SELECT key, english, mangadex_english, anilist_id, mangadex_id, status, checked_at"
                                   " FROM english_titles").fetchall()
        except Exception:  # noqa: BLE001 - an older database / no table: nothing cached
            _log.debug("English titles: the cache could not be read", exc_info=True)
            return {}
        return {r["key"]: EnglishEntry(key=r["key"], status=r["status"], checked_at=r["checked_at"],
                                       english=r["english"], mangadex_english=r["mangadex_english"],
                                       anilist_id=r["anilist_id"], mangadex_id=r["mangadex_id"]) for r in rows}

    def save(self, entries: Iterable[EnglishEntry]) -> None:
        rows = [(e.key, e.english, e.mangadex_english, e.anilist_id, e.mangadex_id, e.status, e.checked_at)
                for e in entries]
        if not rows:
            return
        with self.db.connect() as con:
            con.executemany(
                "INSERT INTO english_titles (key, english, mangadex_english, anilist_id, mangadex_id, status,"
                " checked_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET english = excluded.english,"
                " mangadex_english = excluded.mangadex_english, anilist_id = excluded.anilist_id,"
                " mangadex_id = excluded.mangadex_id, status = excluded.status, checked_at = excluded.checked_at", rows)

    def due(self, refs: Iterable[SeriesRef], now: Optional[_dt.datetime] = None,
            cached: Optional[Mapping[str, EnglishEntry]] = None) -> List[SeriesRef]:
        """The refs (one per key) not cached yet or whose answer is old enough to ask again."""
        cached = self.load() if cached is None else cached
        out: Dict[str, SeriesRef] = {}
        for ref in refs:
            if ref is None or ref.key in out:
                continue
            entry = cached.get(ref.key)
            if entry is None or entry.due(now):
                out[ref.key] = ref
        return list(out.values())


def record(db, ref: SeriesRef, english: Optional[str], *, mangadex_english: Optional[str] = None,
           mangadex_id: Optional[str] = None, now: Optional[_dt.datetime] = None) -> None:
    """Store what a caller already looked up (Add series: AniList's English title through MangaDex's link) - the
    Renamer then needs no lookup of its own for that series. Best effort."""
    try:
        EnglishTitles(db).save([EnglishEntry(key=ref.key, status=FOUND if english else NONE,
                                             checked_at=_iso(now or _now()), english=english or None,
                                             mangadex_english=mangadex_english or None, anilist_id=ref.anilist_id,
                                             mangadex_id=mangadex_id or ref.mangadex_id)])
    except Exception:  # noqa: BLE001
        _log.info("English titles: could not record the title of %s", ref.key, exc_info=True)


# --- asking the network ----------------------------------------------------------------------------------------------

def mangadex_records(ids: Sequence[str]) -> Dict[str, Any]:
    """MangaDex's records of up to :data:`MANGADEX_BATCH` ids in one request: ``{id: MangaDexSeries}`` (an id MangaDex
    does not know is left out). Raises on a network failure."""
    from .add_series import MANGADEX_API, AddSeriesError, _http_get_json, mangadex_from_record

    ids = list(dict.fromkeys(i.lower() for i in ids))[:MANGADEX_BATCH]
    if not ids:
        return {}
    query = urlencode([("ids[]", i) for i in ids] + [("contentRating[]", c) for c in _CONTENT_RATINGS]
                      + [("limit", str(MANGADEX_BATCH))])
    body = _http_get_json(f"{MANGADEX_API}/manga?{query}")
    out: Dict[str, Any] = {}
    for item in (body.get("data") if isinstance(body, Mapping) else None) or ():
        try:
            md = mangadex_from_record({"data": item})
        except AddSeriesError:
            continue
        out[md.id] = md
    return out


def mangadex_search(mu_id: int, title: str) -> Optional[Any]:
    """MangaDex's record of the MangaUpdates series *mu_id* by a title search (None: not found)."""
    from .add_series import mangadex_for_mu

    return mangadex_for_mu(mu_id, title)


def anilist_english(ids: Sequence[int]) -> Dict[int, Optional[str]]:
    from .anilist_client import english_titles

    return english_titles(list(ids))


@dataclass
class FillReport:
    """What one :func:`fill` did: series looked up, how many have an official English title now, how many failed (asked
    again later), and the keys whose English title changed."""

    looked: int = 0
    found: int = 0
    failed: int = 0
    stopped: bool = False
    changed: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.looked:
            return "Official English titles: nothing to look up."
        text = (f"Official English titles: {self.found} found for the {self.looked} series looked up"
                + (f", {self.failed} could not be asked (tried again later)" if self.failed else "") + ".")
        return text + (" Stopped before the end." if self.stopped else "")


def fill(db, refs: Iterable[SeriesRef], *, now: Optional[_dt.datetime] = None,
         mangadex_many: Callable[[Sequence[str]], Mapping[str, Any]] = mangadex_records,
         mangadex_find: Callable[[int, str], Optional[Any]] = mangadex_search,
         anilist_many: Callable[[Sequence[int]], Mapping[int, Optional[str]]] = anilist_english,
         should_stop: Optional[Callable[[], bool]] = None, sleep: Callable[[float], None] = time.sleep,
         progress: Optional[Callable[[int, int], None]] = None) -> FillReport:
    """Look up the official English title of every ref that is due (:meth:`EnglishTitles.due`) and cache the answers
    (see the module docstring). Network failures are cached as ``failed`` (asked again after :data:`RETRY_FAILED`);
    nothing raises. Runs for a minute or two on a whole library the first time: call it off the UI thread."""
    store = EnglishTitles(db)
    cached = store.load()
    todo = store.due(refs, now, cached)
    report = FillReport(looked=len(todo))
    if not todo:
        return report
    stop = should_stop or (lambda: False)
    stamp = _iso(now or _now())
    first = [True]

    def pause(seconds: float) -> None:
        if not first[0] and seconds > 0:
            sleep(seconds)
        first[0] = False

    # 1. MangaDex records: by MangaPixer's MangaDex id (100 a request), else a title search linking back to the series
    md_of: Dict[str, Any] = {}
    md_failed: set = set()
    with_id = [r for r in todo if r.mangadex_id]
    for start in range(0, len(with_id), MANGADEX_BATCH):
        if stop():
            report.stopped = True
            break
        chunk = with_id[start:start + MANGADEX_BATCH]
        pause(MANGADEX_DELAY)
        try:
            got = mangadex_many([r.mangadex_id for r in chunk])
        except Exception as exc:  # noqa: BLE001 - asked again later
            _log.info("English titles: MangaDex could not be asked (%s)", type(exc).__name__)
            md_failed.update(r.key for r in chunk)
            continue
        for r in chunk:
            if r.mangadex_id in got:
                md_of[r.key] = got[r.mangadex_id]
    searched = 0
    for r in todo:
        if r.mangadex_id or r.anilist_id or r.mu_id is None or not r.title or report.stopped:
            continue
        if stop():
            report.stopped = True
            break
        pause(MANGADEX_DELAY)
        try:
            md = mangadex_find(r.mu_id, r.title)
        except Exception as exc:  # noqa: BLE001
            _log.info("English titles: the MangaDex search failed (%s)", type(exc).__name__)
            md_failed.add(r.key)
            continue
        searched += 1
        if md is not None:
            md_of[r.key] = md
        if progress is not None:
            progress(searched, len(todo))

    # 2. the AniList record of each: MangaPixer's companion, else the one MangaDex links (when MangaDex's record is the
    #    same MangaUpdates series)
    al_of: Dict[str, int] = {}
    for r in todo:
        md = md_of.get(r.key)
        if md is not None and r.mu_id is not None and getattr(md, "mu_id", None) not in (None, r.mu_id):
            _log.info("English titles: MangaDex %s links another MangaUpdates series than %s; not used", md.id, r.key)
            md_of.pop(r.key)
            md = None
        al = r.anilist_id or (getattr(md, "anilist_id", None) if md is not None else None)
        if al:
            al_of[r.key] = int(al)
    english_of: Dict[int, Optional[str]] = {}
    al_failed: set = set()
    al_ids = list(dict.fromkeys(al_of.values()))
    from .anilist_client import PAGE_SIZE, REQUEST_DELAY

    for start in range(0, len(al_ids), PAGE_SIZE):
        if stop():
            report.stopped = True
            break
        chunk = al_ids[start:start + PAGE_SIZE]
        pause(REQUEST_DELAY)
        try:
            english_of.update(anilist_many(chunk))
        except Exception as exc:  # noqa: BLE001
            _log.info("English titles: AniList could not be asked (%s)", type(exc).__name__)
            al_failed.update(chunk)
        else:
            english_of.update({i: english_of.get(i) for i in chunk})

    # 3. the answers
    entries: List[EnglishEntry] = []
    for r in todo:
        md = md_of.get(r.key)
        al = al_of.get(r.key)
        if report.stopped and (al is None or al not in english_of):
            continue                                     # not wholly looked up: asked next time
        md_english = getattr(md, "english_title", None) if md is not None and len(
            getattr(md, "english_alts", ()) or ()) <= 1 else None
        failed = r.key in md_failed or (al is not None and al in al_failed)
        english = english_of.get(al) if al is not None else None
        old = cached.get(r.key)
        if failed and not english:
            report.failed += 1
            if old is not None and old.status != FAILED:
                continue                                 # the last answer stays (asked again next time)
            entries.append(EnglishEntry(r.key, FAILED, stamp, None, md_english, al,
                                        r.mangadex_id or getattr(md, "id", None)))
            continue
        entry = EnglishEntry(r.key, FOUND if english else NONE, stamp, english, md_english, al,
                             r.mangadex_id or (md.id if md is not None else None))
        if english:
            report.found += 1
        if old is None or old.english != entry.english or old.mangadex_english != entry.mangadex_english:
            report.changed.append(r.key)
        entries.append(entry)
    store.save(entries)
    _log.info("%s", report.summary())
    return report


__all__ = [
    "EnglishEntry", "EnglishTitles", "FAILED", "FOUND", "FillReport", "NONE", "REFRESH_FOUND", "REFRESH_NONE",
    "RETRY_FAILED", "SeriesRef", "fill", "record", "ref_key",
]
