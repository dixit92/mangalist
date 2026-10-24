"""The MangaDex gate of the scheduled chapter check: a MangaDex-sourced series whose latest upload has not changed since
its last successful check is not fetched again (owner-approved 2026-10-18, "Shared Chapter Signal"; lane GT). No Qt.

Why: the scheduled check (:mod:`mangalist.downloads.chapter_check`) asked Suwayomi for about 158 MangaDex series every
6 hours, each a request to MangaDex through Suwayomi, while MangaPixer reads MangaDex for the same series. MangaDex
says in one cheap request per 100 manga which chapter each one got last (``attributes.latestUploadedChapter``); a series
whose answer is the one the last successful check saw has nothing new on MangaDex.

**Which series** (:meth:`Gate.prepare`): those whose chapter source is MangaDex - the saved Suwayomi manga is on a
MangaDex source, or the series is found through the MangaDex id MangaPixer links. Their MangaDex manga uuid is the one
learned from Suwayomi's answer for the saved manga at an earlier check (its url, ``/manga/<uuid>``), else MangaPixer's
``companions.mangadex`` (checked against Suwayomi's answer before anything is kept). Other series are fetched every
run, as before.

**The latest upload** comes from, in this order:

a) MangaPixer's export item ``companions.mangadexLatestUpload`` = ``{"chapterId": "<uuid>" | null, "fetchedAt":
   "<ISO-8601 UTC>"}`` (MangaPixer 1.45.0), when its ``companions.mangadex`` is the same manga and the value was
   confirmed less than one check interval ago: its ``fetchedAt`` (when MangaPixer first read that chapter id) or its
   library's sweep time (the export page's ``sweeps.mangadexLatestUpload``: every value of the library confirmed at
   least then; kept by the last complete sync, :meth:`MangaPixerCache.sweeps`), whichever is newer. An absent or
   malformed block reads as "not there";
b) else MangaList's own request (:class:`mangalist.services.mangadex.MangaDexClient`: 100 ids per request, every
   content rating);
c) else (no MangaDex id, the request failed, the id missing from the answer) it is unknown, and the series is fetched.

**Skipped only when nothing changed** (:meth:`Gate.unchanged`): the latest upload is known and the same as the one kept
at the series' last successful check, for the same Suwayomi manga and MangaDex manga, AND MangaList's own side of the
series is the same too (:func:`local_state`: the newest chapter held, the chapters on their way or filed, the waiting
chapter findings - a download that failed, a file deleted or a finding the owner skipped makes the next check fetch the
series, as it would without the gate), AND
that check is younger than :data:`REFETCH_AFTER` (about a day: a chapter MangaDex lists later than it counts it as
uploaded - a group's publish delay - is still found within a day). Everything else is fetched: changed, unknown, first
time. **The value is kept only after the series' check succeeded** (:meth:`Gate.record`; the source was read and every
send went through), so the gate only ever saves requests and never hides a chapter.

**Where it is kept**: the database's ``meta`` table, one key per series (``chapter_check.mangadex.<series id>``, a small
JSON object, like the series' Suwayomi choice) - no schema change.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

_log = logging.getLogger(__name__)

META_PREFIX = "chapter_check.mangadex."             # + the series id
#: The interval MangaPixer's value must be younger than when the check's own interval is not known (its default).
DEFAULT_INTERVAL = timedelta(hours=6)
#: A series unchanged on MangaDex is fetched anyway once its last successful check is this old (every 4th run at 6 h).
REFETCH_AFTER = timedelta(hours=23)

#: The export's ``sweeps`` key that confirms ``companions.mangadexLatestUpload`` (MangaPixer 1.45.0).
SWEEP_KEY = "mangadexLatestUpload"

ORIGIN_MANGAPIXER = "MangaPixer"
ORIGIN_OWN = "MangaDex"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    """An ISO-8601 time (``Z`` or an offset; a time without one is UTC), else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# --- MangaPixer's signal -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Signal:
    """``companions.mangadexLatestUpload``: MangaDex's latest uploaded chapter (None: MangaDex lists none), and when
    MangaPixer first read that chapter id (it moves only with a new chapter; the library's sweep time says how
    recently the value was confirmed)."""

    chapter: Optional[str]
    fetched_at: datetime


def mangapixer_signal_of(item: Any) -> Optional[Signal]:
    """``companions.mangadexLatestUpload`` of a MangaPixer export item, or None: absent (MangaPixer does not export it,
    or has not read it) or malformed - both mean "not there", never "no chapter"."""
    from .chapters import _UUID

    companions = item.get("companions") if isinstance(item, Mapping) else None
    block = companions.get("mangadexLatestUpload") if isinstance(companions, Mapping) else None
    if not isinstance(block, Mapping) or "chapterId" not in block:
        return None
    fetched_at = parse_time(block.get("fetchedAt"))
    if fetched_at is None:
        return None
    chapter = block.get("chapterId")
    if chapter is None:
        return Signal(None, fetched_at)
    if not isinstance(chapter, str) or not _UUID.fullmatch(chapter.strip()):
        return None
    return Signal(chapter.strip().lower(), fetched_at)


def mangapixer_sweeps(db) -> Dict[str, datetime]:
    """Per MangaPixer library, when MangaPixer last confirmed every latest upload in it (the export's
    ``sweeps.mangadexLatestUpload`` kept by the last complete sync), for the libraries that have one; empty without
    MangaPixer data."""
    try:
        from ..services.mangapixer import open_cache

        kept = open_cache(db).sweeps()
    except Exception:  # noqa: BLE001 - no sweep times: each value's own fetchedAt decides
        _log.debug("Chapter check: no MangaPixer sweep times", exc_info=True)
        return {}
    out: Dict[str, datetime] = {}
    for library_id, sweeps in kept.items():
        at = parse_time(sweeps.get(SWEEP_KEY))
        if at is not None:
            out[library_id] = at
    return out


# --- what is kept per series -------------------------------------------------------------------------------------

_UNKNOWN = object()


@dataclass(frozen=True)
class Kept:
    """What the series' last successful check saw."""

    suwayomi_manga: int
    mangadex_id: str
    latest: Any                         # the chapter uuid, None (MangaDex listed none), or _UNKNOWN
    local: str
    at: Optional[datetime]

    @property
    def known(self) -> bool:
        return self.latest is not _UNKNOWN


def _key(series_id: int, backend: Optional[str] = None) -> str:
    """The kept state's key: one per program (transition audit B9 - the manga id it names is the program's own, so a
    switch back and forth keeps each program's state instead of fetching every MangaDex series again)."""
    if backend == "extensions":
        return f"{META_PREFIX}extensions.{int(series_id)}"
    return f"{META_PREFIX}{int(series_id)}"


def load(db, series_id: int, backend: Optional[str] = None) -> Optional[Kept]:
    """The series' kept state for *backend*'s manga. The extension host's falls back to the state kept before there was
    one per program (its manga id is compared with the match anyway, so another program's never counts)."""
    raw = db.get_meta(_key(series_id, backend))
    if raw is None and backend == "extensions":
        raw = db.get_meta(_key(series_id))
    try:
        data = json.loads(raw or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, Mapping) or not isinstance(data.get("suwayomi_manga"), int) \
            or not isinstance(data.get("mangadex"), str):
        return None
    latest = data["latest"] if "latest" in data and (data["latest"] is None or isinstance(data["latest"], str)) \
        else _UNKNOWN
    return Kept(data["suwayomi_manga"], data["mangadex"], latest, str(data.get("local") or ""),
                parse_time(data.get("at")))


def save(db, series_id: int, kept: Kept, backend: Optional[str] = None) -> None:
    data: Dict[str, Any] = {"suwayomi_manga": kept.suwayomi_manga, "mangadex": kept.mangadex_id,
                            "local": kept.local, "at": _iso(kept.at) if kept.at else None}
    if kept.known:
        data["latest"] = kept.latest
    db.set_meta(_key(series_id, backend), json.dumps(data, sort_keys=True, separators=(",", ":")))


def forget(db, series_id: int, backend: Optional[str] = None) -> None:
    if db.get_meta(_key(series_id, backend)) is not None:
        db.set_meta(_key(series_id, backend), None)


def local_state(db, backend: Any, store: Any, series_id: int) -> str:
    """MangaList's own side of a series, as a short fingerprint: the newest chapter held, the chapters on their way or
    filed (the chapter ledger), the waiting chapter findings. A change makes the next check fetch the series even when
    MangaDex shows nothing new, so what the check would do without the gate it still does."""
    from ..store.findings import WAITING
    from .chapters import newest_held_chapter

    try:
        newest, _why = newest_held_chapter(db, series_id)
    except LookupError:
        newest = None
    in_hand = sorted(str(n) for n in backend._chapters_in_hand(series_id))
    waiting = sorted(f.pick_key for f in store.list(WAITING, series_id) if f.kind == "chapters")
    text = json.dumps([None if newest is None else str(newest), in_hand, waiting], separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


# --- the gate ----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Latest:
    mangadex_id: str
    chapter: Optional[str]
    origin: str                         # ORIGIN_*


def default_client():
    """MangaList's own MangaDex client (tests replace this factory: no test asks MangaDex)."""
    from ..services.mangadex import MangaDexClient

    return MangaDexClient()


@dataclass
class Gate:
    """One check's gate: the MangaDex id asked about per series, and the latest uploads read."""

    db: Any
    now: datetime
    refetch_after: Optional[timedelta] = REFETCH_AFTER
    planned: Dict[int, str] = field(default_factory=dict)          # series id -> MangaDex manga uuid
    latest: Dict[str, Latest] = field(default_factory=dict)        # MangaDex manga uuid -> its latest upload
    from_mangapixer: int = 0            # series whose latest upload came from MangaPixer
    by_sweep: int = 0                   # ... of them fresh only through their library's sweep time
    own_requests: int = 0               # requests MangaList sent to MangaDex itself
    own_failed: int = 0

    @classmethod
    def off(cls, db) -> "Gate":
        """A gate that skips nothing (the check fetches every series)."""
        return cls(db=db, now=utcnow())

    @classmethod
    def prepare(cls, db, candidates: Sequence[Any], sources: Sequence[Any], *, client: Any = None,
                interval: Optional[timedelta] = None, now: Optional[datetime] = None,
                refetch_after: Optional[timedelta] = REFETCH_AFTER,
                should_stop: Optional[Callable[[], bool]] = None) -> "Gate":
        """The gate for *candidates* (:class:`~mangalist.downloads.chapter_check.Candidate`) on the allowed Suwayomi
        *sources*: their MangaDex ids, MangaPixer's fresh values, and one own request per 100 of the rest (*client*:
        a :class:`~mangalist.services.mangadex.MangaDexClient`; default :func:`default_client`)."""
        from ..store.downloads import SuwayomiSettings
        from .chapters import HOW_CONFIRMED, HOW_MANGADEX, mangadex_id_of
        from .chapters import linked as linked_to

        gate = cls(db=db, now=now or utcnow(), refetch_after=refetch_after)
        fresh_after = gate.now - (interval or DEFAULT_INTERVAL)
        by_id = {s.id: s for s in sources}
        suwayomi = SuwayomiSettings(db)
        program = suwayomi.backend()
        signals: Dict[str, Signal] = {}
        swept: set = set()                  # MangaDex ids fresh only through their library's sweep time
        sweeps = mangapixer_sweeps(db)
        for cand in candidates:
            sid = int(cand.series_id)
            choice = suwayomi.series_choice(sid)
            saved = choice.get("how") in (HOW_MANGADEX, HOW_CONFIRMED) and isinstance(choice.get("manga_id"), int)
            if saved:
                source = by_id.get(str(choice.get("source_id") or ""))
                if source is None or not source.is_mangadex:
                    continue                # another source (or none allowed): fetched every run
                kept = load(db, sid, program)
                mangadex_id = kept.mangadex_id if kept is not None and kept.suwayomi_manga == choice["manga_id"] \
                    else None
            else:
                mangadex_id = cand.mangadex_id      # found through MangaPixer's MangaDex id
            found = linked_to(db, sid)
            item = found.item if found is not None else None
            linked = mangadex_id_of(item)
            mangadex_id = mangadex_id or linked     # checked against Suwayomi's answer before it is kept
            if not mangadex_id:
                continue
            gate.planned[sid] = mangadex_id
            signal = mangapixer_signal_of(item) if linked == mangadex_id else None
            if signal is None:
                continue
            swept_at = sweeps.get(found.library_id) if found is not None else None
            if signal.fetched_at > fresh_after:
                signals[mangadex_id] = signal
            elif swept_at is not None and swept_at > fresh_after:
                signals[mangadex_id] = signal
                swept.add(mangadex_id)
        for mangadex_id, signal in signals.items():
            gate.latest[mangadex_id] = Latest(mangadex_id, signal.chapter, ORIGIN_MANGAPIXER)
        gate.from_mangapixer = sum(1 for m in gate.planned.values() if m in signals)
        gate.by_sweep = sum(1 for m in gate.planned.values() if m in swept)
        ask = list(dict.fromkeys(m for m in gate.planned.values() if m not in gate.latest))
        if ask:
            own = client if client is not None else default_client()
            try:
                got = own.latest_uploads(ask, should_stop=should_stop)
            finally:
                if client is None:
                    getattr(own, "close", lambda: None)()
            gate.own_requests, gate.own_failed = got.requests, got.failed
            for mangadex_id, chapter in got.values.items():
                gate.latest[mangadex_id] = Latest(mangadex_id, chapter, ORIGIN_OWN)
        _log.info("Chapter check: MangaDex latest uploads of %d series: %d from MangaPixer (%d confirmed by its "
                  "library sweep), %d from %d own request(s)%s", len(gate.planned), gate.from_mangapixer, gate.by_sweep,
                  sum(1 for m in gate.planned.values() if gate.latest.get(m) and gate.latest[m].origin == ORIGIN_OWN),
                  gate.own_requests, f" ({gate.own_failed} failed)" if gate.own_failed else "")
        return gate

    def unchanged(self, series_id: int, match: Any, local: str) -> Optional[Latest]:
        """The latest upload when the series may be skipped (see the module docstring), else None."""
        mangadex_id = self.planned.get(int(series_id))
        value = self.latest.get(mangadex_id) if mangadex_id else None
        if value is None or not match.source.is_mangadex:
            return None
        kept = load(self.db, series_id, getattr(match, "backend", None))
        if kept is None or not kept.known or kept.suwayomi_manga != match.manga.id or kept.mangadex_id != mangadex_id:
            return None
        if kept.latest != value.chapter or kept.local != local:
            return None
        if self.refetch_after is not None and (kept.at is None or self.now - kept.at >= self.refetch_after):
            return None
        return value

    def record(self, series_id: int, match: Any, manga_url: str, local: str) -> None:
        """The series' check succeeded: keep the latest upload read before its fetch, for the MangaDex manga
        Suwayomi's answer names (only when it is the one asked about; otherwise the manga only, the value unknown)."""
        from .chapters import mangadex_id_in_url

        program = getattr(match, "backend", None)
        if not match.source.is_mangadex:
            forget(self.db, series_id, program)
            return
        answered = mangadex_id_in_url(manga_url) or mangadex_id_in_url(match.manga.url)
        if not answered:
            forget(self.db, series_id, program)
            return
        value = self.latest.get(answered) if self.planned.get(int(series_id)) == answered else None
        save(self.db, series_id, Kept(int(match.manga.id), answered, value.chapter if value else _UNKNOWN, local,
                                      self.now), program)


__all__ = ["DEFAULT_INTERVAL", "Gate", "Kept", "Latest", "REFETCH_AFTER", "Signal", "load", "local_state",
           "mangapixer_signal_of", "mangapixer_sweeps", "save"]
