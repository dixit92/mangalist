"""The find pass of automatic downloads (lane B; the contract is :mod:`mangalist.downloads.finding`). No Qt here.

:func:`run_find` walks the wanted list (the Download tab's "To get" entries) and, per series whose mode is not Off and
with no download in flight, looks for what fills its gaps; :func:`send_finding` sends one waiting finding later, on the
owner's word. Owner decisions 2026-10-10 (vault note "Manga-list - Automatic Downloads").

**Volumes** (Missing volumes and Upgrade available, one nyaa search per series for both): the backend's nyaa search and
its D4 ranking, then a greedy cover (:func:`cover`) - the best release, then the best for what is still missing, until
nothing more is filled. A release the series already has a ledger record for is never picked again; a light novel
never. Each pick is checked like a manual send: a pack's file list is read from its ``.torrent``
(:meth:`~mangalist.downloads.adapter.Backend.inspect_pack`, nyaa's pacing applies) and the send goes through the
download budget (it queues what does not fit). A pick may go out by itself only when the series is on Auto, the pass
is not a bulk find, the release certainly fills a missing volume (its title's numbers say so), and it is a single
volume or a pack whose files narrow to the missing volumes ("Only the missing volumes" on, the owner's default).
Everything else waits as a finding with the reason (``REASON_*``). When the cover leaves volumes unfilled, the best
release whose title states no volumes is offered too (it may hold them; never sent by itself).

**Chapters** (Missing chapters): only the series' saved Suwayomi source - the manga the owner confirmed, or MangaDex by
the id MangaPixer links (:func:`mangalist.downloads.chapters.find_manga` without its title search). No saved source:
a finding without a pick (:data:`REASON_NO_SOURCE`). The missing chapters the source has, each from the series' group
(the chapters panel's default rule); extras never; chapters already in hand or in Suwayomi's queue are left out.
**Source dropped** (owner: "If a series appears removed from the saved source, notify."): the saved manga cannot be
fetched while Suwayomi answers, or it lists chapters but none of the missing ones although some of them lie within its
numbers, or it lists no chapters at all -> a finding whose reason says which (``REASON_SOURCE_*``: cannot open the
series; taken down / never had any, for MangaDex by MangaPixer's ``mangadexReleased``; lists none; lacks the missing ones
and which it has). Missing chapters past the source's last chapter only mean the source is behind: nothing is said
(steady state stays quiet).

**Not searched**: a series on Off, one with a download in flight (queued, sent, downloaded), and - except in a bulk
find of explicitly selected series - one the owner marked up to date (the List's Behind override "done"; since
2026-10-15 the wanted list leaves such a series out as well, so this is a second guard).

**Findings.** Every pick is recorded (:meth:`FindingStore.offer`); one the owner skipped - or one sent before - is
never offered or sent again. What a searched series no longer offers is retired (``gone``), and so are the waiting
findings of a series that left the wanted list (its gap was filled). A sent pick is a finding with status ``sent`` and
the ledger row, so the owner sees what Auto did. The scheduled chapter check's findings (chapters after the newest held,
:mod:`mangalist.downloads.chapter_check`; ``newer`` in the payload, :func:`is_newer`) name no gap of the wanted list:
a pass keeps them unless it picks their chapters itself, and they leave once their chapters are held or sent.

**One pass at a time** (nyaa's politeness): a process-wide lock, plus the last run in the database - a run started
less than :data:`STALE_RUN_HOURS` ago that has not finished means another process is searching. A second pass is
refused with :class:`FindBusy`. Every series line and the summary (:attr:`FindReport.summary`) are logged at INFO.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..gui.shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES
from ..store.findings import GONE, MODE_AUTO, MODE_OFF, SENT, SKIPPED, WAITING, FindingConflict, FindingStore, NewFinding
from .contracts import DownloadStatus, NyaaCandidate, SuwayomiChapter, SuwayomiManga
from .finding import (
    REASON_BULK,
    REASON_NO_SOURCE,
    REASON_NOTIFY,
    REASON_PACK_WHOLE,
    CHANGE_SOURCE,
    REASON_SOURCE_EMPTY,
    REASON_SOURCE_GONE,
    REASON_SOURCE_LACKS,
    REASON_SOURCE_BEHIND,
    REASON_SOURCE_NEVER,
    REASON_SOURCE_TAKEN_DOWN,
    REASON_UNCERTAIN,
    TRIGGER_BULK,
    TRIGGER_NOW,
    TRIGGER_SCHEDULE,
    FindReport,
)
from .spinoffs import search_titles

_log = logging.getLogger(__name__)

#: More reasons a pick waits (shown to the owner as they are), besides the contract's.
REASON_PARTIAL_OFF = 'a pack, and "Only the missing volumes" is switched off (Settings > Download sources)'
REASON_NO_QBITTORRENT = "qBittorrent is not set up yet"

KIND_VOLUMES, KIND_UPGRADES, KIND_CHAPTERS = "volumes", "upgrades", "chapters"
PAYLOAD_NYAA, PAYLOAD_SUWAYOMI, PAYLOAD_NO_SOURCE, PAYLOAD_DROPPED = "nyaa", "suwayomi", "no-source", "dropped"

#: In flight: a download of the series that has not reached its end (filed counts as an end: the gap is filled).
IN_FLIGHT = (DownloadStatus.QUEUED, DownloadStatus.SENT, DownloadStatus.DOWNLOADED)
STALE_RUN_HOURS = 3             # an unfinished run older than this was cut short (a crash), not still running

_TIER = 1_000_000.0             # services.nyaa.ranking's weight of one missing volume in ``rank``
_PASS = threading.Lock()


class FindBusy(RuntimeError):
    """A find pass is already running (here, or in another MangaList on the same database)."""


# --- numbers ------------------------------------------------------------------------------------------------------

def _key(number: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(number).strip())
    except Exception:  # noqa: BLE001 - InvalidOperation, ValueError
        return None
    return d.normalize() if d.is_finite() and d >= 0 else None


def _numbers(values: Iterable[Any]) -> List[str]:
    """Exact strings in ascending order, without duplicates."""
    seen: Dict[Decimal, str] = {}
    for v in values:
        k = _key(v)
        if k is not None and k not in seen:
            seen[k] = str(v).strip()
    return [seen[k] for k in sorted(seen)]


def _text(numbers: Sequence[str], *, volumes: bool) -> str:
    from ..gui.volumes_target import numbers_text

    return f"{'vol.' if volumes else 'ch.'} {numbers_text(list(numbers))}"


# --- the greedy cover ------------------------------------------------------------------------------------------------

def _rest(candidate: NyaaCandidate) -> float:
    """The D4 rank without its "missing volumes held" part: Digital, trusted, seeders, remake."""
    return candidate.rank - len(candidate.covers_missing) * _TIER


def certain(candidate: NyaaCandidate) -> bool:
    """The release's title names volumes that are missing (not a light novel; numbers without a volume mark - "001-051"
    - may be chapters, so they are never certain)."""
    return (candidate.vol_from is not None and bool(candidate.covers_missing) and not candidate.not_comic
            and not candidate.numbers_only)


def best_next(pool: Sequence[NyaaCandidate], remaining: Set[Decimal]) -> Optional[Tuple[NyaaCandidate, Tuple[str, ...]]]:
    """The release that fills the most of *remaining* (ties: the D4 order of *pool*), with what it fills."""
    best = None
    for c in pool:
        fills = tuple(v for v in c.covers_missing if _key(v) in remaining)
        if not fills:
            continue
        score = (len(fills), _rest(c))
        if best is None or score > best[0]:
            best = (score, c, fills)
    return (best[1], best[2]) if best is not None else None


def cover(candidates: Sequence[NyaaCandidate], missing: Sequence[str],
          skip_hashes: Iterable[str] = ()) -> List[Tuple[NyaaCandidate, Tuple[str, ...]]]:
    """The greedy cover of *missing* by *candidates* (best first, as ranked): ``[(release, the volumes it is picked
    for)]``, each volume picked once. Releases whose numbers cannot be compared are not part of it."""
    skip = {h.lower() for h in skip_hashes}
    pool = [c for c in candidates if certain(c) and c.info_hash.lower() not in skip]
    remaining = {k for k in (_key(v) for v in missing) if k is not None}
    out = []
    while remaining:
        nxt = best_next(pool, remaining)
        if nxt is None:
            break
        pool.remove(nxt[0])
        out.append(nxt)
        remaining -= {_key(v) for v in nxt[1]}
    return out


def best_uncertain(candidates: Sequence[NyaaCandidate], skip_hashes: Iterable[str] = ()) -> Optional[NyaaCandidate]:
    """The best release whose title states no volumes - or only numbers without a volume mark (it may hold the missing
    ones)."""
    skip = {h.lower() for h in skip_hashes}
    return next((c for c in candidates if (c.vol_from is None or c.numbers_only) and not c.not_comic
                 and c.info_hash.lower() not in skip), None)


def _uncertain_volume_release(ctx, candidates: Sequence[NyaaCandidate], skip: Set[str],
                              fills: Sequence[str]) -> Optional[NyaaCandidate]:
    """:func:`best_uncertain`, passing over a release of bare numbers ("001-051") whose files are all chapters - a
    chapter pack, not volumes (owner, 2026-10-13). A few tries at most (each reads a .torrent)."""
    passed = set(skip)
    for _ in range(3):
        unsure = best_uncertain(candidates, passed)
        if unsure is None or not unsure.numbers_only or not _partial_on(ctx):
            return unsure
        try:
            selection = ctx.backend.inspect_pack(unsure, list(fills))
        except Exception:  # noqa: BLE001 - not readable now: offered as uncertain, as before
            return unsure
        if not getattr(selection, "chapters_only", False):
            return unsure
        _log.info("Find pass %d: %s: its files are chapters, not volumes - not offered", ctx.run_id, unsure.title)
        passed.add(unsure.info_hash.lower())
    return None


def retire_filled(db, wanted: "Wanted" = None) -> int:
    """Retire (``gone``) the waiting findings none of whose numbers is still missing - a gap filled by a manual send or
    another release - without a search. A find pass retires them too, but only when it runs (owner, 2026-10-13: a
    finding for chapters already filed by hand waited in Found, and its Change source said the series had nothing to
    look up). A part (``62.1``) counts while its whole chapter is missing. Returns how many were retired."""
    store = FindingStore(db)
    waiting = store.list(WAITING)
    if not waiting:
        return 0
    kinds_of = {GROUP_VOLUMES: (KIND_VOLUMES, KIND_UPGRADES), GROUP_UPGRADES: (KIND_VOLUMES, KIND_UPGRADES),
                GROUP_CHAPTERS: (KIND_CHAPTERS,)}
    missing: Dict[Tuple[int, str], Set[Decimal]] = {}
    for e in load_wanted(db, wanted):
        for kind in kinds_of.get(e.group, ()):
            missing.setdefault((e.series_id, kind), set()).update(k for k in (_key(v) for v in e.missing) if k is not None)
    retired = 0
    for f in waiting:
        if is_newer(f):
            from .chapter_check import still_new

            if still_new(db, f.series_id, f.fills) != ():
                continue                # (None: cannot tell - it stays)
        else:
            still = missing.get((f.series_id, f.kind), set())
            keys = [k for k in (_key(v) for v in f.fills) if k is not None]
            if not keys or any(k in still or Decimal(int(k)) in still for k in keys):
                continue
        try:
            store.set_status(f.id, GONE, expect=(WAITING,))
        except FindingConflict:
            continue                    # moved meanwhile (sent / skipped in the window)
        retired += 1
        _log.info("Findings: %s finding %s of series %s retired: %s no longer missing", f.kind, f.id, f.series_id,
                  ", ".join(f.fills))
    return retired


# --- the wanted list --------------------------------------------------------------------------------------------------

Wanted = Any        # a sequence of WantedSeries, or a callable (db, *, root_ids=None) -> that sequence


def load_wanted(db, wanted: Wanted = None, root_ids: Optional[Sequence[int]] = None) -> list:
    """The wanted list: *wanted* as given (a list, or a callable), else :func:`mangalist.wanted.wanted_list` (imported
    only here: it is another lane's module)."""
    if wanted is None:
        from ..wanted import wanted_list  # noqa: WPS433 - lazy on purpose

        return list(wanted_list(db, root_ids=root_ids))
    if callable(wanted):
        return list(wanted(db, root_ids=root_ids))
    return list(wanted)


# --- one pick ------------------------------------------------------------------------------------------------------

@dataclass
class _Pick:
    kind: str
    pick_key: str
    title: str
    fills: Tuple[str, ...]
    size: Optional[int]
    payload: Dict[str, Any]
    blocker: Optional[str] = None       # why it may not go out by itself (None: it may)


def candidate_payload(candidate: NyaaCandidate) -> Dict[str, Any]:
    data = asdict(candidate)
    for name in ("covers_missing", "covers_held", "reasons"):
        data[name] = list(data[name])
    return data


def candidate_from(data: Dict[str, Any]) -> NyaaCandidate:
    known = set(NyaaCandidate.__dataclass_fields__)
    values = {k: v for k, v in data.items() if k in known}
    for name in ("covers_missing", "covers_held", "reasons"):
        values[name] = tuple(values.get(name) or ())
    return NyaaCandidate(**values)


# --- the pass ---------------------------------------------------------------------------------------------------------

@dataclass
class _Context:
    db: Any
    backend: Any
    trigger: str
    store: FindingStore
    report: FindReport
    run_id: int
    new: int = 0                        # findings offered for the first time (or back from gone)
    nyaa_off: Optional[str] = None
    chapters_off: Optional[str] = None
    chapter_client: Any = None
    sources: Optional[list] = None
    qbt_ready: Optional[bool] = None
    partial_on: Optional[bool] = None
    record_hashes: Dict[int, Set[str]] = field(default_factory=dict)
    label: Optional[str] = None         # the log lines' head (None: "Find pass <run id>"; the chapter check's own)

    @property
    def who(self) -> str:
        return self.label or f"Find pass {self.run_id}"


def _busy_elsewhere(store: FindingStore) -> bool:
    last = store.last_run()
    if last is None or last.finished_at:
        return False
    try:
        started = datetime.fromisoformat(last.started_at)
    except (TypeError, ValueError):
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started < timedelta(hours=STALE_RUN_HOURS)


def run_find(db, *, trigger: str, series_ids: Optional[Sequence[int]] = None, wanted: Wanted = None,
             backend: Any = None, should_stop: Optional[Callable[[], bool]] = None) -> FindReport:
    """One find pass (see the module docstring). *series_ids*: only these series (a bulk find: trigger ``bulk``, which
    never sends by itself). *backend*: the :class:`~mangalist.downloads.adapter.Backend` whose nyaa client, qBittorrent
    and Suwayomi it uses (default: a new one over *db*). *should_stop*: asked between series. Raises :class:`FindBusy`
    while another pass runs."""
    if trigger not in (TRIGGER_SCHEDULE, TRIGGER_NOW, TRIGGER_BULK):
        raise ValueError(f"not a find trigger: {trigger!r}")
    if not _PASS.acquire(blocking=False):
        raise FindBusy("a find pass is already running; its results appear when it has finished")
    try:
        store = FindingStore(db)
        if _busy_elsewhere(store):
            raise FindBusy("a find pass is already running (started elsewhere); its results appear when it has "
                           "finished")
        if backend is None:
            from .adapter import Backend

            backend = Backend(db)
        run_id = store.start_run(trigger)
        report = FindReport(run_id=run_id, trigger=trigger)
        ctx = _Context(db=db, backend=backend, trigger=trigger, store=store, report=report, run_id=run_id)
        _log.info("Find pass %d (%s) started%s", run_id, trigger,
                  f" for {len(set(series_ids))} series" if series_ids is not None else "")
        try:
            _run(ctx, series_ids, wanted, should_stop or (lambda: False))
        except Exception as exc:  # noqa: BLE001 - the run is closed with what it did; the caller hears of it
            report.errors += 1
            report.lines.append(f"the pass stopped: {type(exc).__name__}: {exc}")
            _log.exception("Find pass %d stopped by an unexpected error", run_id)
        finally:
            _close(ctx.chapter_client)
            store.finish_run(run_id, series_searched=report.series_searched, found=report.found, sent=report.sent,
                             errors=report.errors, summary=report.summary)
        quiet = "" if ctx.new or report.sent else "; nothing new"
        _log.info("Find pass %d (%s): %s%s", run_id, trigger, report.summary, quiet)
        return report
    finally:
        _PASS.release()


def _run(ctx: _Context, series_ids: Optional[Sequence[int]], wanted: Wanted, should_stop: Callable[[], bool]) -> None:
    from ..store.downloads import DownloadLedger

    try:
        entries = load_wanted(ctx.db, wanted)
    except ImportError:
        ctx.report.errors += 1
        ctx.report.lines.append("the wanted list is not available in this build")
        _log.warning("Find pass %d: the wanted list (mangalist.wanted) is not available; nothing searched", ctx.run_id)
        return
    only = set(int(i) for i in series_ids) if series_ids is not None else None
    done = up_to_date_folders(ctx.db) if ctx.trigger != TRIGGER_BULK else set()
    by_series: Dict[int, list] = {}
    for e in entries:
        if e.series_id is None or (only is not None and e.series_id not in only):
            continue
        if _folder_key(e.folder) in done:
            continue                    # the owner marked it up to date: no gap to search (a bulk find still may)
        by_series.setdefault(int(e.series_id), []).append(e)
    if done:
        _log.info("Find pass %d: %d series marked up to date are not searched", ctx.run_id, len(done))
    if only is None:
        _retire_left(ctx, by_series)
    ledger = DownloadLedger(ctx.db, tool=None)
    records = ledger.all_records()
    in_flight = {r.series_id for r in records if r.status in IN_FLIGHT}
    for r in records:
        if not r.is_chapters:
            ctx.record_hashes.setdefault(r.series_id, set()).add(r.info_hash.lower())
    for sid, hashes in ledger.cleared_releases().items():       # releases whose finished records were cleared
        ctx.record_hashes.setdefault(sid, set()).update(hashes)
    modes = ctx.store.modes(by_series)
    for sid, group in by_series.items():
        if should_stop():
            ctx.report.lines.append("stopped before every series was searched")
            _log.info("Find pass %d: asked to stop; the remaining series wait for the next pass", ctx.run_id)
            return
        mode = modes.get(sid, ctx.store.default_mode())
        if mode == MODE_OFF:
            continue
        name = group[0].title or f"series {sid}"
        if sid in in_flight:
            _log.info("Find pass %d: %s skipped: a download is in flight", ctx.run_id, name)
            continue
        searched = False
        volumes = [e for e in group if e.group in (GROUP_VOLUMES, GROUP_UPGRADES) and e.findable and e.missing]
        chapters = [e for e in group if e.group == GROUP_CHAPTERS and e.missing]
        if volumes:
            searched = _volumes(ctx, sid, name, mode, volumes) or searched
        if chapters:
            searched = _chapters(ctx, sid, name, mode, chapters) or searched
        if searched:
            ctx.report.series_searched += 1


def _folder_key(folder: Any) -> str:
    return os.path.normcase(os.path.normpath(str(folder or "")))


def up_to_date_folders(db) -> Set[str]:
    """The series folders the owner marked up to date (the List's Behind override "done"): a scheduled pass or Find now
    never searches them (integrator, 2026-10-11). The wanted list leaves them out too since 2026-10-15 (owner: marking
    takes the series out of To get); this stays as a guard for a wanted list read before the mark."""
    try:
        with db.connect() as con:
            rows = con.execute("SELECT folder FROM links_cache WHERE behind_override = 'done'").fetchall()
    except Exception:  # noqa: BLE001 - no MangaUpdates links table yet: nothing is marked
        _log.debug("Find pass: the Behind overrides could not be read", exc_info=True)
        return set()
    return {_folder_key(r[0]) for r in rows}


def _retire_left(ctx: _Context, by_series: Dict[int, list]) -> None:
    """Waiting findings of a series whose gap of that kind left the wanted list: gone (filled another way)."""
    kinds_of = {GROUP_VOLUMES: (KIND_VOLUMES, KIND_UPGRADES), GROUP_UPGRADES: (KIND_VOLUMES, KIND_UPGRADES),
                GROUP_CHAPTERS: (KIND_CHAPTERS,)}
    still = {(sid, kind) for sid, group in by_series.items() for e in group for kind in kinds_of.get(e.group, ())}
    for f in ctx.store.list(WAITING):
        if is_newer(f):
            continue                    # the chapter check's: not a gap of the wanted list (retire_filled retires it)
        if (f.series_id, f.kind) not in still:
            ctx.store.retire(f.series_id, f.kind, _newer_keys(ctx.store, f.series_id))
            _log.info("Find pass %d: series %s: %s finding %r retired: no longer wanted", ctx.run_id, f.series_id,
                      f.kind, f.title)


def _line(ctx: _Context, name: str, text: str) -> None:
    ctx.report.lines.append(f"{name}: {text}")
    _log.info("%s: %s: %s", ctx.who, name, text)


def _waits(ctx: _Context, mode: str, blocker: Optional[str]) -> Optional[str]:
    """Why a pick waits for the owner (None: it may go out by itself)."""
    if blocker:
        return blocker
    if ctx.trigger == TRIGGER_BULK:
        return REASON_BULK
    if mode != MODE_AUTO:
        return REASON_NOTIFY
    return None


def _offer(ctx: _Context, sid: int, pick: _Pick, reason: str) -> None:
    found, fresh = ctx.store.offer(NewFinding(series_id=sid, kind=pick.kind, pick_key=pick.pick_key, title=pick.title,
                                              fills=pick.fills, size_bytes=pick.size, reason=reason,
                                              payload=pick.payload), run_id=ctx.run_id)
    if found is not None and found.status == WAITING:
        ctx.report.found += 1
        ctx.new += 1 if fresh else 0


def _mark_sent(ctx: _Context, sid: int, pick: _Pick, record_id: Optional[int]) -> None:
    found, _fresh = ctx.store.offer(NewFinding(series_id=sid, kind=pick.kind, pick_key=pick.pick_key, title=pick.title,
                                               fills=pick.fills, size_bytes=pick.size, reason="sent automatically",
                                               payload=pick.payload), run_id=ctx.run_id)
    try:
        ctx.store.set_status(found.id, SENT, expect=(WAITING,), record_id=record_id)
    except FindingConflict as exc:      # the owner acted on it meanwhile; the download stands
        _log.info("%s: finding %s not marked sent: %s", ctx.who, found.id, exc)
    ctx.report.sent += 1


def _settled(existing: Dict[Tuple[str, str], Any], kind: str, pick_key: str) -> bool:
    """The owner skipped this pick, or it was sent before: it is never offered or sent again."""
    f = existing.get((kind, pick_key))
    return f is not None and f.status in (SKIPPED, SENT)


# --- volumes -----------------------------------------------------------------------------------------------------------

def _volumes(ctx: _Context, sid: int, name: str, mode: str, entries: list) -> bool:
    from ..gui.downloads_backend import BackendError

    if ctx.nyaa_off is None:
        options = getattr(ctx.backend, "nyaa_options", None)
        if callable(options) and not options().enabled:
            ctx.nyaa_off = "nyaa is switched off (Settings > Download sources)"
            _log.info("Find pass %d: volumes not searched: %s", ctx.run_id, ctx.nyaa_off)
        else:
            ctx.nyaa_off = ""
    if ctx.nyaa_off:
        return False
    missing = _numbers(v for e in entries if e.group == GROUP_VOLUMES for v in e.missing)
    upgrades = _numbers(v for e in entries if e.group == GROUP_UPGRADES for v in e.missing)
    wanted = _numbers([*missing, *upgrades])
    first = entries[0]
    # not by the names of a series whose folder holds this one's (owner, 2026-10-18: a spin-off took its parent's pack)
    titles = search_titles(ctx.db, sid, list(first.titles) or [first.title], label=f"Find pass {ctx.run_id}: {name}")
    try:
        candidates = list(ctx.backend.search(list(titles), wanted, list(first.held)))
    except BackendError as exc:
        ctx.report.errors += 1
        _line(ctx, name, f"the nyaa search failed: {exc}")
        return False
    existing = {(f.kind, f.pick_key): f for f in ctx.store.list(None, sid)}
    have = ctx.record_hashes.get(sid, set())
    skip = set(have) | {key.split(":", 1)[1] for (kind, key), f in existing.items()
                        if kind in (KIND_VOLUMES, KIND_UPGRADES) and f.status in (SKIPPED, SENT)
                        and key.startswith("nyaa:")}
    volume_keys = {_key(v) for v in missing}
    pool = [c for c in candidates if certain(c) and c.info_hash.lower() not in skip]
    remaining = {k for k in (_key(v) for v in wanted) if k is not None}
    keep: Set[str] = set()
    while remaining:
        nxt = best_next(pool, remaining)
        if nxt is None:
            break
        candidate, fills = nxt
        pool.remove(candidate)
        pick = _volume_pick(ctx, candidate, fills, volume_keys)
        if pick is None:
            continue                    # its file list holds none of them after all: they stay to be filled
        remaining -= {_key(v) for v in pick.fills}
        keep.add(pick.pick_key)
        _send_or_offer_volumes(ctx, sid, name, mode, pick)
    if remaining:
        unsure = _uncertain_volume_release(ctx, candidates, skip, [v for v in wanted if _key(v) in remaining])
        if unsure is not None:
            fills = tuple(v for v in wanted if _key(v) in remaining)
            pick = _Pick(_kind(fills, volume_keys), f"nyaa:{unsure.info_hash.lower()}", unsure.title, fills,
                         unsure.size_bytes or None, {"type": PAYLOAD_NYAA, "candidate": candidate_payload(unsure),
                                                     "wanted": list(fills), "only_missing": _partial_on(ctx)},
                         blocker=REASON_UNCERTAIN)
            keep.add(pick.pick_key)
            _offer(ctx, sid, pick, REASON_UNCERTAIN)
            _line(ctx, name, f"{unsure.title} may hold {_text(fills, volumes=True)}: waits ({REASON_UNCERTAIN})")
    for kind in (KIND_VOLUMES, KIND_UPGRADES):
        ctx.store.retire(sid, kind, keep)
    return True


def _kind(fills: Sequence[str], volume_keys: Set[Optional[Decimal]]) -> str:
    return KIND_VOLUMES if any(_key(v) in volume_keys for v in fills) else KIND_UPGRADES


def _partial_on(ctx: _Context) -> bool:
    if ctx.partial_on is None:
        getter = getattr(ctx.backend, "partial_default", None)
        ctx.partial_on = bool(getter()) if callable(getter) else True
    return ctx.partial_on


def _volume_pick(ctx: _Context, candidate: NyaaCandidate, fills: Tuple[str, ...],
                 volume_keys: Set[Optional[Decimal]]) -> Optional[_Pick]:
    """The pick for *fills*, checked like a manual send: a pack's file list read (``None``: its files hold none of
    them after all)."""
    single = not candidate.is_pack and (candidate.vol_to is None or _key(candidate.vol_to) == _key(candidate.vol_from))
    only_missing, size, blocker = False, candidate.size_bytes or None, None
    if not single:
        if not _partial_on(ctx):
            blocker = REASON_PARTIAL_OFF
        else:
            only_missing = True
            selection = ctx.backend.inspect_pack(candidate, list(fills))
            if not selection.readable or selection.whole_reason or selection.unknown_kept:
                blocker = REASON_PACK_WHOLE
                if not selection.readable:
                    _log.info("Find pass %d: %s: file list not read (%s)", ctx.run_id, candidate.title,
                              selection.problem)
            else:
                absent = {_key(v) for v in selection.not_found}
                held = tuple(v for v in fills if _key(v) not in absent)
                if not held:
                    _log.info("Find pass %d: %s: its files hold none of %s", ctx.run_id, candidate.title,
                              _text(fills, volumes=True))
                    return None
                fills, size = held, selection.kept_bytes or size
    payload = {"type": PAYLOAD_NYAA, "candidate": candidate_payload(candidate), "wanted": list(fills),
               "only_missing": only_missing}
    return _Pick(_kind(fills, volume_keys), f"nyaa:{candidate.info_hash.lower()}", candidate.title, tuple(fills),
                 size, payload, blocker)


def _send_or_offer_volumes(ctx: _Context, sid: int, name: str, mode: str, pick: _Pick) -> None:
    existing = {(f.kind, f.pick_key): f for f in ctx.store.list(None, sid)}
    if _settled(existing, pick.kind, pick.pick_key):
        return
    what = _text(pick.fills, volumes=True)
    reason = _waits(ctx, mode, pick.blocker)
    if reason is None:
        reason = _send_volumes(ctx, sid, name, pick)
        if reason is None:
            return
    _offer(ctx, sid, pick, reason)
    _line(ctx, name, f"{pick.title} ({what}) waits: {reason}")


def _send_volumes(ctx: _Context, sid: int, name: str, pick: _Pick) -> Optional[str]:
    """Send an Auto pick through the budget. Returns None when it went out (sent or queued), else why it waits."""
    from ..gui.downloads_backend import BackendError
    from ..services.qbittorrent.client import TorrentRejected
    from .service import PackSetupError, SendRefused

    if ctx.qbt_ready is None:
        conn = ctx.backend.ledger.connection()
        ctx.qbt_ready = conn is not None and bool(conn.base_url)
    if not ctx.qbt_ready:
        return REASON_NO_QBITTORRENT
    try:
        placement = ctx.backend.placement(sid)
    except BackendError as exc:
        return str(exc)
    if not placement.target_dir:
        return f"where to file the volumes is not decided ({placement.reason}); choose a folder first"
    candidate = candidate_from(pick.payload["candidate"])
    try:
        record = ctx.backend._submit(sid, candidate, list(pick.fills), placement.target_dir,
                                     only_missing=bool(pick.payload.get("only_missing")),
                                     size_bytes=pick.size if pick.payload.get("only_missing") else None)
    except TorrentRejected as exc:
        _line(ctx, name, f"{pick.title} is gone (qBittorrent: {exc}); not offered")
        return None
    except SendRefused as exc:          # the budget's "bigger than the cap", a torrent already tracked, ...
        return str(exc)
    except (BackendError, PackSetupError) as exc:
        ctx.report.errors += 1
        return f"sending failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - qBittorrent down: it waits, and the next pass tries again
        ctx.report.errors += 1
        return f"sending failed: {type(exc).__name__}: {exc}"
    _mark_sent(ctx, sid, pick, record.id)
    how = "queued (download budget)" if record.status == DownloadStatus.QUEUED else "sent to qBittorrent"
    _line(ctx, name, f"{pick.title} ({_text(pick.fills, volumes=True)}) {how}, download {record.id}")
    return None


# --- chapters ----------------------------------------------------------------------------------------------------------

def _chapter_client(ctx: _Context):
    """Suwayomi's client and the allowed sources, once per pass (None when chapters cannot be searched now)."""
    from .chapters import allowed_sources

    if ctx.chapters_off is not None:
        return None
    if ctx.chapter_client is not None:
        return ctx.chapter_client
    from .chapter_backend import wording

    settings = getattr(ctx.backend, "suwayomi", None)
    builtin = bool(getattr(settings, "builtin", False))
    ready = getattr(ctx.backend, "suwayomi_ready", None)
    if not callable(ready) or not ready():
        ctx.chapters_off = wording("Suwayomi is not set up", builtin)
        _log.info("%s: chapters not searched: Suwayomi is not set up", ctx.who)
        return None
    try:
        client = ctx.backend._chapter_client()
        ctx.sources = allowed_sources(list(client.sources()), ctx.backend.suwayomi.sources(),
                                      ctx.backend._source_languages())
    except Exception as exc:  # noqa: BLE001 - Suwayomi down: chapters wait for the next pass
        ctx.chapters_off = wording(f"Suwayomi did not answer ({type(exc).__name__})", builtin)
        ctx.report.errors += 1
        ctx.report.lines.append(f"chapters not searched: {ctx.chapters_off}")
        _log.warning("%s: chapters not searched: Suwayomi did not answer: %s: %s", ctx.who,
                     type(exc).__name__, exc)
        return None
    ctx.chapter_client = client
    return client


def _suwayomi_up(ctx: _Context) -> bool:
    try:
        ctx.chapter_client.sources()
        return True
    except Exception:  # noqa: BLE001
        return False


def _chapters(ctx: _Context, sid: int, name: str, mode: str, entries: list) -> bool:
    from .chapters import (
        HOW_MANGADEX,
        chapter_rows,
        default_group,
        find_manga,
        group_counts,
        held_chapter_groups,
        mangadex_id_for,
        to_decimal,
    )

    client = _chapter_client(ctx)
    if client is None:
        return False
    missing = tuple(_numbers(v for e in entries for v in e.missing))
    suwayomi = ctx.backend.suwayomi
    stored = suwayomi.series_choice(sid)
    existing = {(f.kind, f.pick_key): f for f in ctx.store.list(None, sid)}
    keep: Set[str] = set()
    try:
        found = find_manga(client, ctx.sources or [], mangadex_id=mangadex_id_for(ctx.db, sid), titles=(),
                           stored=stored, title_search=False)
    except Exception as exc:  # noqa: BLE001 - MangaDex's id search failed: try again next pass
        ctx.report.errors += 1
        _line(ctx, name, f"Suwayomi's MangaDex search failed ({type(exc).__name__})")
        return False
    match = found.match
    if match is None:
        pick = _Pick(KIND_CHAPTERS, "chapters:no-source", f"{_text(missing, volumes=False)} (no source saved)",
                     missing, None, {"type": PAYLOAD_NO_SOURCE})
        keep.add(pick.pick_key)
        if not _settled(existing, pick.kind, pick.pick_key):
            _offer(ctx, sid, pick, REASON_NO_SOURCE)
            _line(ctx, name, f"{_text(missing, volumes=False)} wait: {REASON_NO_SOURCE}")
        _retire_chapters(ctx, sid, keep)
        return True
    if match.how == HOW_MANGADEX and stored.get("manga_id") != match.manga.id:
        suwayomi.set_series_choice(sid, source_id=match.source.id, manga_id=match.manga.id,
                                   manga_title=match.manga.title, how=HOW_MANGADEX)
    source_name = match.source.display_name
    try:
        fetched = client.chapters(match.manga.id)
    except Exception as exc:  # noqa: BLE001 - the saved manga cannot be fetched: dropped, unless Suwayomi is down
        if not _suwayomi_up(ctx):
            ctx.report.errors += 1
            _line(ctx, name, f"Suwayomi did not answer ({type(exc).__name__}); chapters wait for the next pass")
            return False
        _log.info("Find pass %d: %s: %s manga %s cannot be fetched: %s: %s", ctx.run_id, name, source_name,
                  match.manga.id, type(exc).__name__, exc)
        _dropped(ctx, sid, name, match, missing, existing, keep,
                 reason=REASON_SOURCE_GONE.format(source=match.source.display_name))
        _retire_chapters(ctx, sid, keep)
        return True
    source_name = fetched.source_name or source_name
    if not fetched.chapters:
        # the saved manga lists no chapters at all now (Suwayomi's "No chapters found"): taken down there, or never had any
        _dropped(ctx, sid, name, match, missing, existing, keep, source_name,
                 reason=_empty_reason(ctx, sid, match.source, source_name))
        _retire_chapters(ctx, sid, keep)
        return True
    numbers = [d for d in (to_decimal(n) for n in missing) if d is not None]
    try:
        held = held_chapter_groups(ctx.db, sid)
    except LookupError:
        held = {}
    choice = default_group(group_counts(fetched.chapters, numbers), held, numbers, stored.get("group"))
    # one form per chapter (owner, 2026-10-18): a gap the source lists whole and in parts is filled in the series' form
    rows = chapter_rows(fetched.chapters, missing, choice.group, ctx.backend._chapters_in_hand(sid), held=held)
    pending = [r for r in rows if r.in_hand is None and not r.extra]
    queued = _queued_numbers(ctx, client, fetched.chapters, numbers + [to_decimal(r.number) for r in pending])
    picks = [r.default for r in pending if r.available and r.default is not None and r.number not in queued]
    if not picks:
        listed = [d for d in (to_decimal(c.number) for c in fetched.chapters) if d is not None]
        within = listed and any(to_decimal(r.number) <= max(listed) for r in pending)
        if pending and within and not any(r.available for r in pending):
            from ..parsing.model import plain

            has = f"ch. {plain(min(listed))}" + (f"-{plain(max(listed))}" if max(listed) != min(listed) else "")
            lacks = _text([r.number for r in pending], volumes=False)
            _dropped(ctx, sid, name, match, missing, existing, keep, source_name,
                     reason=REASON_SOURCE_LACKS.format(source=source_name, missing=lacks, has=has))
        elif pending and listed and not within and not any(r.available for r in pending):
            from ..parsing.model import plain

            newest = max(listed)
            behind = [r.number for r in pending]
            _dropped(ctx, sid, name, match, missing, existing, keep, source_name,
                     key=f"behind:{plain(newest)}", title=f"{_text(behind, volumes=False)} (past {source_name}'s newest)",
                     reason=REASON_SOURCE_BEHIND.format(source=source_name, newest=f"ch. {plain(newest)}",
                                                        missing=_text(behind, volumes=False),
                                                        verb="is" if len(behind) == 1 else "are"))
        _retire_chapters(ctx, sid, keep)
        return True
    manga_title = fetched.manga.title or match.manga.title
    pick = chapter_pick(match, picks, choice.group, source_name, manga_title)
    keep.add(pick.pick_key)
    if not _settled(existing, pick.kind, pick.pick_key):
        reason = _waits(ctx, mode, None)
        if reason is None:
            reason = _send_chapters(ctx, sid, name, pick, match, picks, manga_title, source_name)
        if reason is not None:
            _offer(ctx, sid, pick, reason)
            _line(ctx, name, f"{pick.title} waits: {reason}")
    _retire_chapters(ctx, sid, keep, pick.fills)
    return True


def chapter_pick(match, picks: Sequence[SuwayomiChapter], group: Optional[str], source_name: str, manga_title: str,
                 **payload: Any) -> _Pick:
    """The pick of chapters *picks* from *match*'s source: ``chapters:<source id>:<chapter ids>`` (the same chapters from
    the same source are the same pick, whoever finds them), with what sending it later needs (*payload*: more of it)."""
    chosen = tuple(_numbers(p.number for p in picks))
    ids = sorted(int(p.id) for p in picks)
    return _Pick(KIND_CHAPTERS, f"chapters:{match.source.id}:{','.join(str(i) for i in ids)}",
                 f"{_text(chosen, volumes=False)} from {source_name} ({group or 'no group'})", chosen, None,
                 {"type": PAYLOAD_SUWAYOMI, "source_id": match.source.id, "source_name": source_name,
                  "manga_id": int(match.manga.id), "manga_title": manga_title, "manga_url": match.manga.url,
                  "how": match.how, "chapter_ids": ids, "group": group, **payload})


def is_newer(finding) -> bool:
    """A finding of the scheduled chapter check (:mod:`mangalist.downloads.chapter_check`): chapters numbered after the
    newest one held, which no wanted-list gap names (the metadata has not caught up). Its life is not the wanted
    list's: it waits until its chapters are held or sent (:func:`retire_filled`), or the next check finds more."""
    return finding.kind == KIND_CHAPTERS and bool((finding.payload or {}).get("newer"))


def _newer_keys(store: FindingStore, sid: int) -> Set[str]:
    return {f.pick_key for f in store.list(WAITING, sid) if is_newer(f)}


def _retire_chapters(ctx: _Context, sid: int, keep: Set[str], chosen: Sequence[str] = ()) -> None:
    """The series' waiting chapter findings this pass did not find again are gone - except the chapter check's
    (:func:`is_newer`), which no gap names, unless this pass picked one of its chapters (*chosen*): the gap's pick
    stands for them now (the same chapters from the same source are the same pick key anyway)."""
    taken = {_key(v) for v in chosen}
    newer = {f.pick_key for f in ctx.store.list(WAITING, sid)
             if is_newer(f) and not any(_key(v) in taken for v in f.fills)}
    ctx.store.retire(sid, KIND_CHAPTERS, set(keep) | newer)


def _queued_numbers(ctx: _Context, client, chapters: Sequence[SuwayomiChapter], numbers: Sequence[Decimal]) -> Set[str]:
    """Missing chapters already in Suwayomi's download queue (not sent again)."""
    from .chapters import to_decimal

    try:
        by_id = {c.id: c for c in chapters}
        wanted = set(numbers)
        return {str(by_id[q.chapter_id].number) for q in client.queue()
                if q.chapter_id in by_id and to_decimal(by_id[q.chapter_id].number) in wanted}
    except Exception:  # noqa: BLE001 - a courtesy check, as in the chapters panel
        _log.info("%s: Suwayomi's download queue could not be read", ctx.who)
        return set()


def _empty_reason(ctx: _Context, sid: int, source, source_name: str) -> str:
    """Why a saved source lists no chapters: for MangaDex, MangaPixer's count (it includes unavailable chapters) tells
    taken down (it lists some) from never had any (none) - in the source's language only; else it just lists none."""
    from .chapters import mangadex_released_for

    released = mangadex_released_for(ctx.db, sid) if getattr(source, "is_mangadex", False) else None
    if released is not None and released[0] == (source.lang or "").strip().casefold():
        return (REASON_SOURCE_TAKEN_DOWN if released[1] > 0 else REASON_SOURCE_NEVER).format(source=source_name)
    return REASON_SOURCE_EMPTY.format(source=source_name)


def _dropped(ctx: _Context, sid: int, name: str, match, missing: Tuple[str, ...], existing, keep: Set[str],
             source_name: Optional[str] = None, *, reason: str, key: str = "dropped",
             title: Optional[str] = None) -> None:
    """A finding without a pick: the saved source cannot give the missing chapters (*reason* says which case). Its key
    ``chapters:<source>:<key>`` decides how long a Skip holds: ``dropped`` for good (for that source), ``behind:<newest>``
    until the source lists a newer chapter."""
    source = source_name or match.source.display_name
    pick = _Pick(KIND_CHAPTERS, f"chapters:{match.source.id}:{key}",
                 title or f"{_text(missing, volumes=False)} (not on {source})", missing, None,
                 {"type": PAYLOAD_DROPPED, "source_id": match.source.id, "source_name": source,
                  "manga_id": int(match.manga.id)})
    keep.add(pick.pick_key)
    if _settled(existing, pick.kind, pick.pick_key):
        return
    _offer(ctx, sid, pick, reason)
    _line(ctx, name, f"{_text(missing, volumes=False)} wait: {reason}")


def _send_chapters(ctx: _Context, sid: int, name: str, pick: _Pick, match, picks: Sequence[SuwayomiChapter],
                   manga_title: str, source_name: str, said: Optional[List[str]] = None) -> Optional[str]:
    """Send an Auto pick of chapters. Returns None when it went out, else why it waits. What was sent is logged as the
    series' line - or added to *said* for the caller's own line (the chapter check's)."""
    from .chapters import ChapterSendRefused, chapter_placement_for, send_chapters

    try:
        placement = chapter_placement_for(ctx.db, sid)
    except LookupError as exc:
        return str(exc)
    if not placement.target_dir:
        return f"where to file the chapters is not decided ({placement.reason}); choose a folder first"
    try:
        outcome = send_chapters(ctx.chapter_client, ctx.backend.chapter_ledger, sid, match, picks,
                                placement.target_dir, manga_title=manga_title, source_name=source_name)
    except ChapterSendRefused as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - Suwayomi down: it waits, and the next pass tries again
        ctx.report.errors += 1
        return f"sending failed: {type(exc).__name__}: {exc}"
    sent = [r for r in outcome.records if r.status == DownloadStatus.SENT]
    if not sent:
        if outcome.error:
            ctx.report.errors += 1
            return f"Suwayomi did not take the download: {outcome.error}"
        return outcome.text()
    _mark_sent(ctx, sid, pick, sent[0].id)
    if said is None:
        _line(ctx, name, outcome.text())
    else:
        said.append(outcome.text())
    return None


# --- sending one finding later (the owner's Send) ---------------------------------------------------------------------

def _still_missing(db, finding, wanted: Wanted) -> Optional[Set[Decimal]]:
    """What of the series' gap of this kind is still missing, per the wanted list now (None: cannot tell)."""
    from .placement import locate_series

    if is_newer(finding):
        from .chapter_check import still_new

        new = still_new(db, finding.series_id, finding.fills)
        return None if new is None else {_key(v) for v in new}
    try:
        _series, root, _dir = locate_series(db, finding.series_id)
        entries = load_wanted(db, wanted, root_ids=[root.id])
    except (LookupError, ImportError):
        return None
    groups = (GROUP_CHAPTERS,) if finding.kind == KIND_CHAPTERS else (GROUP_VOLUMES, GROUP_UPGRADES)
    return {_key(v) for e in entries if e.series_id == finding.series_id and e.group in groups for v in e.missing}


def _gone(store: FindingStore, finding_id: int, why: str) -> str:
    try:
        store.set_status(finding_id, GONE, expect=(WAITING,))
    except FindingConflict:
        pass
    _log.info("Finding %s: not sent and dropped from the list: %s", finding_id, why)
    return f"Not sent: {why}."


def send_finding(db, finding_id: int, *, backend: Any = None, wanted: Wanted = None) -> str:
    """Send one waiting finding (the owner's Send in the Found view) and say what happened, in a sentence. Refused,
    with the reason: a finding that is not waiting, one without a pick (no saved source, source dropped), a gap that
    has been filled meanwhile (the finding is then ``gone``), a release qBittorrent can no longer add (``gone``), and
    whatever a manual send refuses (the budget, an undecided folder, ...)."""
    store = FindingStore(db)
    f = store.get(finding_id)
    if f is None:
        return "Not sent: this finding no longer exists."
    if f.status != WAITING:
        return f"Not sent: it is {f.status} already."
    kind = f.payload.get("type")
    if kind == PAYLOAD_NO_SOURCE:
        return ("Not sent: no Suwayomi source is saved for the series yet; open its chapters in the Download tab to "
                "choose one.")
    if kind == PAYLOAD_DROPPED:
        why = (f.reason or "").removesuffix(CHANGE_SOURCE) or f"{f.payload.get('source_name') or 'its source'} " \
                                                                 "has none of these chapters"
        return f"Not sent: {why}; use Change source... in its chapters."
    if kind not in (PAYLOAD_NYAA, PAYLOAD_SUWAYOMI):
        return "Not sent: this finding holds nothing that can be sent."
    if backend is None:
        from .adapter import Backend

        backend = Backend(db)
    still = _still_missing(db, f, wanted)
    fills = f.fills if still is None else tuple(v for v in f.fills if _key(v) in still)
    if not fills:
        return _gone(store, finding_id, f"the gap is filled ({_text(f.fills, volumes=kind == PAYLOAD_NYAA)} no longer "
                                        "missing)")
    if kind == PAYLOAD_NYAA:
        return _send_nyaa_finding(store, backend, f, fills)
    return _send_chapter_finding(db, store, backend, f, fills)


def _send_nyaa_finding(store: FindingStore, backend, f, fills: Tuple[str, ...]) -> str:
    from ..gui.downloads_backend import BackendError
    from ..services.qbittorrent.client import TorrentRejected
    from .service import PackSetupError, SendRefused

    candidate = candidate_from(f.payload.get("candidate") or {})
    for rec in backend.ledger.for_series(f.series_id):
        if rec.info_hash.lower() == candidate.info_hash.lower() and rec.status in (*IN_FLIGHT, DownloadStatus.FILED):
            try:
                store.set_status(f.id, SENT, expect=(WAITING,), record_id=rec.id)
            except FindingConflict:
                pass
            return f"Already sent: {candidate.title} (download {rec.id})."
    if candidate.vol_from is not None and fills != f.fills:
        _log.info("Finding %s: %s is sent for %s only (the rest is no longer missing)", f.id, candidate.title,
                  ", ".join(fills))
    try:
        placement = backend.placement(f.series_id)
    except BackendError as exc:
        return f"Not sent: {exc}."
    if not placement.target_dir:
        return (f"Not sent: where to file the volumes is not decided ({placement.reason}); choose a folder in the "
                "series' releases first.")
    only_missing = bool(f.payload.get("only_missing"))
    try:
        record = backend._submit(f.series_id, candidate, list(fills), placement.target_dir, only_missing=only_missing,
                                 size_bytes=f.size_bytes if only_missing and fills == f.fills else None)
    except TorrentRejected as exc:
        return _gone(store, f.id, f"the release is gone (qBittorrent: {exc})")
    except (SendRefused, PackSetupError, BackendError) as exc:
        _log.info("Finding %s: not sent: %s", f.id, exc)
        return f"Not sent: {exc}"
    except Exception as exc:  # noqa: BLE001 - qBittorrent down: the finding waits
        _log.warning("Finding %s: not sent: %s: %s", f.id, type(exc).__name__, exc)
        return f"Not sent: qBittorrent could not be reached ({type(exc).__name__})."
    try:
        store.set_status(f.id, SENT, expect=(WAITING,), record_id=record.id)
    except FindingConflict as exc:
        _log.info("Finding %s: sent, but not marked so: %s", f.id, exc)
    _log.info("Finding %s: %s sent by the owner as download %d (%s)", f.id, candidate.title, record.id, record.status)
    if record.status == DownloadStatus.QUEUED:
        return f"Queued: {candidate.title} waits for room under the download budget."
    return f"Sent to qBittorrent: {candidate.title}."


def _send_chapter_finding(db, store: FindingStore, backend, f, fills: Tuple[str, ...]) -> str:
    from ..gui.downloads_backend import BackendError
    from .chapters import HOW_CONFIRMED, MangaMatch, ChapterSendRefused, chapter_placement_for, send_chapters

    p = f.payload
    try:
        client = backend._chapter_client()
    except BackendError as exc:
        return f"Not sent: {exc}."
    try:
        source = next((s for s in client.sources() if s.id == str(p.get("source_id"))), None)
        if source is None:
            return "Not sent: the series' source is not installed in Suwayomi any more."
        try:
            fetched = client.chapters(int(p["manga_id"]))
        except Exception as exc:  # noqa: BLE001 - the manga is gone from the source
            return _gone(store, f.id, f"{p.get('source_name') or source.display_name} no longer has the series "
                                      f"({type(exc).__name__})")
        ids = {int(i) for i in p.get("chapter_ids") or ()}
        keys = {_key(v) for v in fills}
        picks = [c for c in fetched.chapters if c.id in ids and _key(c.number) in keys]
        if not picks:
            return _gone(store, f.id, f"the chapters are not on {source.display_name} any more")
        try:
            placement = chapter_placement_for(db, f.series_id)
        except LookupError as exc:
            return f"Not sent: {exc}."
        if not placement.target_dir:
            return (f"Not sent: where to file the chapters is not decided ({placement.reason}); choose a folder in "
                    "the series' chapters first.")
        manga = SuwayomiManga(id=int(p["manga_id"]), title=p.get("manga_title") or "", url=p.get("manga_url") or "",
                              source_id=source.id)
        match = MangaMatch(source, manga, p.get("how") or HOW_CONFIRMED)
        try:
            outcome = send_chapters(client, backend.chapter_ledger, f.series_id, match, picks, placement.target_dir,
                                    manga_title=fetched.manga.title or manga.title,
                                    source_name=fetched.source_name or source.display_name)
        except ChapterSendRefused as exc:
            return f"Not sent: {exc}."
    except Exception as exc:  # noqa: BLE001 - Suwayomi down: the finding waits
        _log.warning("Finding %s: not sent: %s: %s", f.id, type(exc).__name__, exc)
        return f"Not sent: Suwayomi could not be reached ({type(exc).__name__})."
    finally:
        _close(client)
    sent = [r for r in outcome.records if r.status == DownloadStatus.SENT]
    if not sent:
        return f"Not sent: {outcome.text()}"
    try:
        store.set_status(f.id, SENT, expect=(WAITING,), record_id=sent[0].id)
    except FindingConflict as exc:
        _log.info("Finding %s: sent, but not marked so: %s", f.id, exc)
    _log.info("Finding %s: %d chapter(s) sent by the owner", f.id, len(sent))
    return outcome.text() + "."


def _close(client) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["FindBusy", "IN_FLIGHT", "REASON_NO_QBITTORRENT", "REASON_PARTIAL_OFF", "STALE_RUN_HOURS", "best_next",
           "best_uncertain", "candidate_from", "candidate_payload", "certain", "cover", "load_wanted", "run_find",
           "send_finding"]
