"""The creators of the library's series and the other names of each, for the List's ``creator:`` filter. No Qt.

MangaPixer's export carries no creators, and MangaList kept none (a series record is read for its licence and progress
and dropped), so this module asks MangaUpdates, the way MangaPixer 1.45.0 does ("MangaUpdates Author Aliases"):

- ``GET /v1/series/{id}`` -> the record's ``authors`` (name, author id, role);
- ``GET /v1/authors/{id}`` -> the author's ``name``, ``associated`` other names and ``actualname`` (the name in its own
  script, often not among the associated ones). The credited spelling on a series can differ from the author record's main
  name ("DUBU (Redice Studio)" / "JANG Seong-rak"), so the index matches on both.

Nothing is asked on the UI thread or during a keystroke. :func:`fill` runs in the background after the MangaUpdates
lookup (:meth:`MainWindow._start_creator_names`), politely: the series record at the client's usual pace
(:data:`mangalist.mu_client.REQUEST_DELAY`), an author at one request a second (MangaPixer's pace); it stops after a few
failures in a row (no network) and the next run goes on where this one stopped. Only the numbers of series MangaList
already knows and of the authors those series name are ever sent.

**Cached** in the ``meta`` table (not ``settings``: the window reads every setting at startup), two JSON values:

- ``creators.series``: ``{series id: {"at": time, "a": [[author id or null, credited name, role], ...]}}``;
- ``creators.authors``: ``{author id: {"at": time, "n": [main name, other names...]}}``.

A record asked again after :data:`REFRESH` (180 days, MangaPixer's); one MangaUpdates does not have (404) is stored empty
so it is not asked again; a failed one (``"f": 1``) is asked again after :data:`RETRY_FAILED`.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from .alias_search import CreatorIndex, distinct_titles

_log = logging.getLogger(__name__)

KEY_SERIES = "creators.series"
KEY_AUTHORS = "creators.authors"
REFRESH = _dt.timedelta(days=180)
RETRY_FAILED = _dt.timedelta(hours=12)
AUTHOR_DELAY = 1.0              # seconds between author requests (MangaPixer: one a second)
MAX_FAILURES = 5                # failures in a row before the run gives up (the network is down)
MAX_NAMES = 50                  # other names kept per author (MangaPixer: at most 50)
MAX_CREDITS = 50                # credits kept per series (matcher.mangaupdates.map_series)
SAVE_EVERY = 25                 # records fetched between two writes of the cache


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(when: _dt.datetime) -> str:
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: Any) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None


def _text(value: Any, limit: int = 256) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


# --- the cache -------------------------------------------------------------------------------------------------


def _load_map(db, key: str) -> Dict[str, Any]:
    try:
        value = json.loads(db.get_meta(key) or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def load_cache(db) -> "tuple[Dict[str, Any], Dict[str, Any]]":
    """The stored (series records, author records)."""
    return _load_map(db, KEY_SERIES), _load_map(db, KEY_AUTHORS)


def save_cache(db, series: Mapping[str, Any], authors: Mapping[str, Any]) -> None:
    db.set_meta(KEY_SERIES, json.dumps(series, ensure_ascii=False, separators=(",", ":")))
    db.set_meta(KEY_AUTHORS, json.dumps(authors, ensure_ascii=False, separators=(",", ":")))


def load_index(db) -> CreatorIndex:
    """The :class:`~mangalist.alias_search.CreatorIndex` of what is stored (an empty one when nothing is)."""
    try:
        series, authors = load_cache(db)
    except Exception:  # noqa: BLE001 - a broken cache must not break the List
        _log.warning("Reading the stored creator names failed", exc_info=True)
        return CreatorIndex()
    return CreatorIndex(series, authors)


# --- what the answers say --------------------------------------------------------------------------------------


def credits_of(series_data: Mapping[str, Any]) -> List[list]:
    """A series record's credits: ``[author id or None, name, role]``, a name once per role."""
    out: List[list] = []
    seen = set()
    for a in series_data.get("authors") or ():
        if not isinstance(a, Mapping) or len(out) >= MAX_CREDITS:
            continue
        name = _text(a.get("name"))
        role = _text(a.get("type"), 32).lower() or "other"
        if not name or (name.casefold(), role) in seen:
            continue
        seen.add((name.casefold(), role))
        out.append([_int(a.get("author_id")), name, role])
    return out


def names_of(author_data: Mapping[str, Any]) -> List[str]:
    """An author record's names: the main name, the associated names, then the name in its own script (``actualname``)."""
    others = [_text(a.get("name")) for a in author_data.get("associated") or () if isinstance(a, Mapping)]
    names = distinct_titles([_text(author_data.get("name")), *others, _text(author_data.get("actualname"))])
    return list(names[:MAX_NAMES + 1])


# --- what to ask -----------------------------------------------------------------------------------------------


def _due(record: Any, now: _dt.datetime) -> bool:
    """Is *record* (a stored series / author record, or None) to be asked for?"""
    if not isinstance(record, Mapping):
        return True
    at = _parse(record.get("at"))
    if at is None:
        return True
    return now - at >= (RETRY_FAILED if record.get("f") else REFRESH)


def wanted_series(series: Mapping[str, Any], mu_ids: Iterable[Any], now: Optional[_dt.datetime] = None) -> List[str]:
    """The series ids (of *mu_ids*, once each, in order) whose credits are not stored or are old."""
    now = now or _now()
    ids = dict.fromkeys(str(i) for i in (_int(m) for m in mu_ids) if i is not None)
    return [i for i in ids if _due(series.get(i), now)]


def wanted_authors(series: Mapping[str, Any], authors: Mapping[str, Any], mu_ids: Iterable[Any],
                   now: Optional[_dt.datetime] = None) -> List[str]:
    """The author ids credited by the stored records of *mu_ids* whose names are not stored or are old."""
    now = now or _now()
    out: Dict[str, None] = {}
    for m in mu_ids:
        record = series.get(str(m))
        if not isinstance(record, Mapping) or record.get("f"):
            continue
        for credit in record.get("a") or ():
            author_id = _int(credit[0]) if isinstance(credit, (list, tuple)) and credit else None
            if author_id is not None and _due(authors.get(str(author_id)), now):
                out[str(author_id)] = None
    return list(out)


def pending(db, mu_ids: Iterable[Any], now: Optional[_dt.datetime] = None) -> int:
    """How many requests :func:`fill` would make for *mu_ids* now (no network)."""
    ids = list(mu_ids)
    series, authors = load_cache(db)
    return len(wanted_series(series, ids, now)) + len(wanted_authors(series, authors, ids, now))


# --- the background fill ---------------------------------------------------------------------------------------


@dataclass
class FillResult:
    series: int = 0                 # series records fetched
    authors: int = 0                # author records fetched
    failed: int = 0                 # requests that failed (asked again after RETRY_FAILED)
    stopped: str = ""               # "" finished | "stop" asked to | "failures" too many in a row

    @property
    def requests(self) -> int:
        return self.series + self.authors + self.failed

    def summary(self) -> str:
        text = f"{self.series} series and {self.authors} authors read"
        if self.failed:
            text += f", {self.failed} failed"
        return text + (f" ({self.stopped})" if self.stopped else "")


def fill(db, mu_ids: Iterable[Any], *, get_series: Callable[[int], Mapping[str, Any]],
         get_author: Callable[[int], Mapping[str, Any]], not_found: Callable[[BaseException], bool],
         should_stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], None] = time.sleep,
         now: Callable[[], _dt.datetime] = _now, series_delay: float = 0.3, author_delay: float = AUTHOR_DELAY,
         progress: Optional[Callable[[int, int], None]] = None) -> FillResult:
    """Ask for the credits of *mu_ids* and the names of the authors they credit, as far as they are not cached.

    A request is made after the pace's wait and never when *should_stop* says so; ``MAX_FAILURES`` failures in a row end
    the run. The cache is written every :data:`SAVE_EVERY` records and when the run ends however it ends. *progress*:
    ``(requests made, requests planned so far)``: the authors of series not read yet are counted once they are."""
    ids = [str(i) for i in (_int(m) for m in mu_ids) if i is not None]
    series, authors = load_cache(db)
    result = FillResult()
    todo_series = wanted_series(series, ids, now())
    total = len(todo_series) + len(wanted_authors(series, authors, ids, now()))     # (the new authors: once read)
    streak = 0
    unsaved = 0
    first = True

    def save() -> None:
        nonlocal unsaved
        if unsaved:
            save_cache(db, series, authors)
            unsaved = 0

    def ask(fn: Callable[[int], Mapping[str, Any]], number: str, delay: float) -> Optional[Mapping[str, Any]]:
        """One request after the pace's wait; the answer, ``{}`` when the record does not exist, None when it failed."""
        nonlocal streak, first
        if not first:
            sleep(delay)
        first = False
        try:
            answer = fn(int(number))
        except Exception as exc:  # noqa: BLE001 - one record must not stop the rest
            if not_found(exc):
                streak = 0
                return {}
            streak += 1
            result.failed += 1
            _log.warning("Asking MangaUpdates for %s failed (%s)", number, type(exc).__name__)
            return None
        streak = 0
        return answer if isinstance(answer, Mapping) else {}

    def step() -> None:
        if progress is not None:
            progress(result.requests, total)

    def blocked() -> bool:
        if should_stop():
            result.stopped = "stop"
        elif streak >= MAX_FAILURES:
            result.stopped = "failures"
        return bool(result.stopped)

    try:
        for sid in todo_series:
            if blocked():
                break
            answer = ask(get_series, sid, series_delay)
            stamp = _iso(now())
            if answer is None:
                series[sid] = {"at": stamp, "f": 1, "a": (series.get(sid) or {}).get("a") or []}
            else:
                series[sid] = {"at": stamp, "a": credits_of(answer)}
                result.series += 1
            unsaved += 1
            if unsaved >= SAVE_EVERY:
                save()
            step()
        if not result.stopped:
            todo_authors = wanted_authors(series, authors, ids, now())
            total = result.requests + len(todo_authors)
            for aid in todo_authors:
                if blocked():
                    break
                answer = ask(get_author, aid, author_delay)
                stamp = _iso(now())
                if answer is None:
                    authors[aid] = {"at": stamp, "f": 1, "n": (authors.get(aid) or {}).get("n") or []}
                else:
                    authors[aid] = {"at": stamp, "n": names_of(answer)}
                    result.authors += 1
                unsaved += 1
                if unsaved >= SAVE_EVERY:
                    save()
                step()
    finally:
        save()
    if result.stopped:
        _log.info("Creator names: stopped (%s) after %s", result.stopped, result.summary())
    else:
        _log.info("Creator names: %s", result.summary())
    return result
