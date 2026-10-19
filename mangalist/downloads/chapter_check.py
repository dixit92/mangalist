"""The scheduled newer-chapter check (owner, 2026-10-18: "Scheduled newer-chapter check - every 6-12 h, ongoing chapter
series with a saved source are checked for chapters newer than the newest held; Auto sends, Notify waits"). No Qt here.

Why: the find pass (:mod:`mangalist.downloads.finder`) asks Suwayomi only for the chapters a series' wanted list says
are missing, and those gaps count up to the metadata's latest chapter (MangaUpdates through MangaPixer, refreshed about
every 30 days for an ongoing series). A chapter out on the source is invisible to it until the metadata catches up -
the owner's MAL-Sync showed new chapters about two days before MangaList acted, and only "Check for new chapters" (the
chapters panel, by hand) got ahead of the metadata. This is that check on a schedule (headless job ``chapter-check``,
:mod:`mangalist.headless.chapter_check_job`; every 6 hours by default, Settings > Scheduled tasks).

**Which series** (:func:`select_series`): every present series that

- is not Off (its automatic-download mode - its own, its library's or the default - is Notify or Auto);
- is ongoing: not finished in its origin by its knowledge, and not Complete / Not tracked / Not a series by its state
  (a finished series' gaps already count up to its last chapter, so the find pass covers it);
- follows chapters: it holds chapter files, or its Unbundled Chapters setting is on (a volumes-only series wants
  volumes, not the chapters after them);
- has a source to ask without a title search: the Suwayomi manga saved for it (confirmed by the owner, or found by the
  MangaDex id before), or the MangaDex id MangaPixer links (the find pass's rule, :func:`~.chapters.find_manga` with
  ``title_search=False``);
- has no download in flight (queued, sent or downloaded - the find pass's rule).

**What is new** (:func:`~.chapters.newer_lookup`, the manual check's own lookup): the source's chapters fetched now
(one fetch per series, serially, :data:`DEFAULT_PAUSE` apart), every chapter numbered after the newest chapter held -
a part ``N.2`` after a held ``N.1`` counts (MAL-Sync parity) - extras never, from the saved source only (sources number
differently: one site's "38" is another's "38.1") and from the group the usual default picks; chapters already sent or
filed, or in Suwayomi's queue, are left out. One form per chapter (:mod:`~.chapter_forms`; owner, 2026-10-18): a chapter
the source lists whole and in parts comes in the form the series holds (the whole as a stand-in when the source's parts
do not start at ``.1``), and a late part (``38.1`` listed after ``38.2`` was filed) is new although it is numbered below
the newest held chapter.

**The outcome is the find pass's**: Auto sends through the same chapter send (its guards apply: a chapter already sent,
or already on its way for another folder, is skipped); Notify leaves a finding waiting in Found - the find pass's
finding records, kind ``chapters``, the same pick key for the same chapters, marked ``newer`` in its payload: no gap
names these chapters, so the find pass does not retire it for "no longer wanted"; it goes once its chapters are held or
sent (:func:`still_new`, :func:`mangalist.downloads.finder.retire_filled`) or when the next check finds a different set.
A pick the owner skipped (or one sent before) is never offered or sent again. Chapters a waiting find-pass finding of
the series already offers are not offered twice.

**Locks**: not the find pass's nyaa lock - nyaa is never asked, and a check of a few hundred series must not hold back a
find pass. A check runs alone (its own lock, :class:`CheckBusy` otherwise) and not while a find pass runs, here or in
another MangaList on the same database (:class:`CheckBusy`: it runs again at its next time); sends are made safe against
a find pass in another process by the ledger (the in-hand check and the record are one transaction).

**Fewer requests** (owner-approved 2026-10-18, "Shared Chapter Signal"; lane GT):

- every fetch asks the source for the chapter list only (:meth:`mangalist.services.suwayomi.SuwayomiClient.chapters`,
  ``fetchManga: false``): one request to the source per series instead of two;
- **the MangaDex gate** (:mod:`mangalist.downloads.chapter_gate`): a MangaDex-sourced series whose latest upload on
  MangaDex (MangaPixer's ``companions.mangadexLatestUpload`` when fresh, else MangaList's own request for 100 series at
  a time) is the one its last successful check saw, and whose own side did not change, is not fetched. Changed,
  unknown or first time: fetched; the value is kept only after the series' check succeeded. Other sources are fetched
  every run, and the manual "Check for new chapters" is never gated;
- **a sync first**: the job (:mod:`mangalist.headless.chapter_check_job`) passes *sync* - an incremental MangaPixer
  sync when MangaPixer is connected (:func:`sync_mangapixer`; local network only), so MangaPixer's value is fresh. A
  sync that fails is logged and the check goes on with what is stored.

**Logging**: one INFO line per series with something new (what, from which source, sent or waiting and why), the
nothing-new series and the ones skipped as unchanged on MangaDex at DEBUG, and a summary (:attr:`CheckReport.summary`)
- the runner's line reads ``chapter-check: ok in N s - N series checked (N fetched, N unchanged on MangaDex), N with new
chapters: N sent, N waiting; MangaDex latest uploads: from MangaPixer N, own requests N``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..store.findings import GONE, MODE_OFF, WAITING, FindingConflict, FindingStore

_log = logging.getLogger(__name__)

LABEL = "Chapter check"
#: Seconds between two series' fetches (serial; Suwayomi asks the source each time, MangaFire through Cloudflare). About
#: 180 series take 3-6 minutes.
DEFAULT_PAUSE = 1.0

SKIP_OFF = "off"
SKIP_FINISHED = "finished"
SKIP_NOT_CHAPTERS = "not a chapter series"
SKIP_NO_SOURCE = "no saved source"
SKIP_IN_FLIGHT = "a download in flight"
SKIP_ORDER = (SKIP_OFF, SKIP_FINISHED, SKIP_NOT_CHAPTERS, SKIP_NO_SOURCE, SKIP_IN_FLIGHT)

_CHECK = threading.Lock()


class CheckBusy(RuntimeError):
    """A chapter check or a find pass is running; this check is not started."""


@dataclass(frozen=True)
class Candidate:
    """A series the check asks its source about."""

    series_id: int
    name: str
    mode: str                           # notify | auto
    mangadex_id: Optional[str] = None   # MangaPixer's link, when no manga is saved for the series


@dataclass
class Selection:
    series: List[Candidate] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)       # SKIP_* -> how many series

    def skipped_text(self) -> str:
        return ", ".join(f"{self.skipped[k]} {k}" for k in SKIP_ORDER if self.skipped.get(k))


@dataclass
class CheckReport:
    """What one check did."""

    selected: int = 0                   # series selected (before the source was asked)
    series_checked: int = 0             # series checked: fetched + unchanged on MangaDex
    fetched: int = 0                    # ... whose source was asked
    unchanged: int = 0                  # ... not fetched: nothing changed on MangaDex since their last check (the gate)
    from_mangapixer: int = 0            # series whose MangaDex latest upload came from MangaPixer
    own_requests: int = 0               # requests MangaList sent to MangaDex itself (100 series each)
    synced: Optional[str] = None        # how the MangaPixer sync before the check went (None: none ran)
    with_new: int = 0                   # ... that have chapters newer than the newest held (not skipped before)
    sent: int = 0                       # picks sent (Auto)
    waiting: int = 0                    # findings waiting for the owner
    unreadable: int = 0                 # series whose source could not be read now (tried again next time)
    errors: int = 0                     # sends that failed, Suwayomi down
    stopped: bool = False
    not_checked: Optional[str] = None   # why no series could be asked (Suwayomi not set up, not answering)
    skipped: Counter = field(default_factory=Counter)
    lines: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.not_checked:
            return f"nothing checked: {self.not_checked}"
        text = (f"{self.series_checked} series checked ({self.fetched} fetched, {self.unchanged} unchanged on "
                f"MangaDex), {self.with_new} with new chapters: {self.sent} sent, {self.waiting} waiting")
        if self.unreadable:
            text += f", {self.unreadable} could not be read"
        if self.errors:
            text += f", {self.errors} failed"
        if self.stopped:
            text += f" (stopped after {self.series_checked} of {self.selected})"
        return (f"{text}; MangaDex latest uploads: from MangaPixer {self.from_mangapixer}, "
                f"own requests {self.own_requests}")


# --- which series ------------------------------------------------------------------------------------------------

def _finished(view: Any) -> bool:
    from ..states import State

    state = getattr(getattr(view, "state", None), "state", None)
    if state in (State.COMPLETE, State.NOT_TRACKED, State.NOT_A_SERIES):
        return True
    knowledge = getattr(view, "knowledge", None)
    return bool(knowledge is not None and knowledge.finished_in_origin)


def _follows_chapters(view: Any) -> bool:
    if getattr(view.state, "unbundled", False):
        return True
    from ..wanted import inventory_of

    try:
        return bool(getattr(inventory_of(view.entry, None), "held_chapters", ()))
    except Exception:  # noqa: BLE001 - an unreadable inventory: not checked (the find pass still covers its gaps)
        _log.debug("%s: the inventory of series %s could not be read", LABEL, view.row.id, exc_info=True)
        return False


def _saved(choice: Dict[str, Any]) -> bool:
    from .chapters import HOW_CONFIRMED, HOW_MANGADEX

    return choice.get("how") in (HOW_MANGADEX, HOW_CONFIRMED) and isinstance(choice.get("manga_id"), int)


def select_series(db, *, views: Optional[Callable[[Any], Sequence[Any]]] = None) -> Selection:
    """The series the check asks about (see the module docstring), in the List's order. *views*: the series as the List
    evaluates them (default :func:`mangalist.wanted.library_states`; tests inject their own)."""
    from ..store.downloads import DownloadLedger, SuwayomiSettings
    from .chapters import mangadex_id_for
    from .finder import IN_FLIGHT

    if views is None:
        from ..wanted import library_states as views  # noqa: N806 - lazy: another lane's module

    store = FindingStore(db)
    modes = store.modes()
    default = store.default_mode()
    suwayomi = SuwayomiSettings(db)
    in_flight = {r.series_id for r in DownloadLedger(db, tool=None).all_records() if r.status in IN_FLIGHT}
    out = Selection()
    for view in views(db):
        sid = int(view.row.id)
        mode = modes.get(sid, default)
        if mode == MODE_OFF:
            out.skipped[SKIP_OFF] += 1
            continue
        if _finished(view):
            out.skipped[SKIP_FINISHED] += 1
            continue
        if not _follows_chapters(view):
            out.skipped[SKIP_NOT_CHAPTERS] += 1
            continue
        mangadex_id = None
        if not _saved(suwayomi.series_choice(sid)):
            mangadex_id = mangadex_id_for(db, sid)
            if not mangadex_id:
                out.skipped[SKIP_NO_SOURCE] += 1
                continue
        if sid in in_flight:
            out.skipped[SKIP_IN_FLIGHT] += 1
            continue
        name = getattr(view.entry, "title", "") or getattr(view.row, "rel_path", "") or f"series {sid}"
        out.series.append(Candidate(sid, name, mode, mangadex_id))
    return out


# --- what is still new -------------------------------------------------------------------------------------------

def _key(number: Any) -> Optional[Decimal]:
    from .finder import _key as key

    return key(number)


def still_new(db, series_id: int, fills: Sequence[str]) -> Optional[Tuple[str, ...]]:
    """The chapters of *fills* (a newer-chapter finding's) still wanted: numbered after the newest chapter held (or a
    late part, :func:`~.chapter_forms.is_late_part`), not held and not sent, on their way or filed by a chapter
    download. None when it cannot be told (the series is gone, its newest chapter is unknown) - the finding is then
    left as it is."""
    from ..store.downloads import DownloadLedger
    from .chapter_forms import is_late_part
    from .chapters import held_chapter_groups, newest_held_chapter
    from .contracts import TOOL_SUWAYOMI

    try:
        newest, why = newest_held_chapter(db, series_id)
        held = set(held_chapter_groups(db, series_id))
    except LookupError:
        return None
    if newest is None and why and why != "no file of this series states a chapter number":
        return None
    try:
        in_hand = {_key(n) for n in DownloadLedger(db, tool=TOOL_SUWAYOMI).chapters_in_hand(series_id)}
    except Exception:  # noqa: BLE001 - the ledger cannot be read: cannot tell
        _log.debug("%s: the chapter records of series %s could not be read", LABEL, series_id, exc_info=True)
        return None
    out = []
    for v in fills:
        k = _key(v)
        if k is None or k in in_hand or k in held:
            continue
        if newest is None or k > newest or is_late_part(k, held):      # a late part (38.1 after 38.2) stays new
            out.append(v)
    return tuple(out)


# --- the check ---------------------------------------------------------------------------------------------------

class _Stop(Exception):
    """Suwayomi stopped answering: the rest waits for the next check."""


def _wait(seconds: float, should_stop: Callable[[], bool], sleep: Callable[[float], None]) -> None:
    end = time.monotonic() + seconds
    while not should_stop():
        left = end - time.monotonic()
        if left <= 0:
            return
        sleep(min(left, 0.25))


def sync_mangapixer(db, should_stop: Optional[Callable[[], bool]] = None) -> str:
    """An incremental MangaPixer sync before the check, when MangaPixer is connected (an address and a token, not
    refused): the scheduled sync's own :func:`~mangalist.services.mangapixer.sync.sync_all` (``manual=False``), which
    takes no lock of its own - the runner runs its jobs one at a time. How it went, for the log."""
    from ..services.mangapixer import open_cache
    from ..services.mangapixer.sync import sync_all

    cache = open_cache(db)
    conn = cache.connection()
    if not conn.base_url or not conn.has_token:
        return "MangaPixer is not connected"
    if conn.token_rejected_at:
        return "MangaPixer refused the token (no automatic retry)"
    result = sync_all(cache, manual=False, should_stop=should_stop)
    return f"{result.status}: {result.message}"


def run_chapter_check(db, *, backend: Any = None, views: Optional[Callable[[Any], Sequence[Any]]] = None,
                      should_stop: Optional[Callable[[], bool]] = None, pause: float = DEFAULT_PAUSE,
                      sleep: Callable[[float], None] = time.sleep, sync: Optional[Callable[[], str]] = None,
                      mangadex: Any = None, interval: Optional[timedelta] = None,
                      now: Optional[datetime] = None) -> CheckReport:
    """One check (see the module docstring). *backend*: the :class:`~mangalist.downloads.adapter.Backend` whose Suwayomi
    and chapter ledger it uses (default: a new one over *db*). *should_stop*: asked between series and during the
    pause between them. *sync*: run first (the job's MangaPixer sync, :func:`sync_mangapixer`); its failure is logged
    and the check goes on. *mangadex*: the MangaDex client of the gate's own request (default
    :func:`~mangalist.downloads.chapter_gate.default_client`); *interval*: the check's interval (MangaPixer's latest
    upload must be younger; default 6 h); *now*: for tests. Raises :class:`CheckBusy` while another check or a find
    pass runs."""
    from . import finder

    if not _CHECK.acquire(blocking=False):
        raise CheckBusy("a chapter check is already running")
    try:
        if finder._PASS.locked() or finder._busy_elsewhere(FindingStore(db)):
            raise CheckBusy("a find pass is running; the chapter check runs at its next time")
        should_stop = should_stop or (lambda: False)
        if backend is None:
            from .adapter import Backend

            backend = Backend(db)
        report = CheckReport()
        started = time.monotonic()
        if sync is not None:
            try:
                report.synced = sync()
            except Exception as exc:  # noqa: BLE001 - the check goes on with what is stored
                report.synced = f"failed ({type(exc).__name__})"
                _log.warning("%s: the MangaPixer sync before the check failed (%s: %s); checking with what is stored",
                             LABEL, type(exc).__name__, exc)
            else:
                _log.info("%s: MangaPixer sync before the check: %s", LABEL, report.synced)
        selection = select_series(db, views=views)
        report.selected, report.skipped = len(selection.series), selection.skipped
        _log.info("%s started: %d series to ask%s", LABEL, len(selection.series),
                  f" (not asked: {selection.skipped_text()})" if selection.skipped else "")
        if selection.series:
            _check(db, backend, report, selection.series, should_stop, pause, sleep,
                   gate_args={"client": mangadex, "interval": interval, "now": now})
        _log.info("%s: %s (%.0f s)", LABEL, report.summary, time.monotonic() - started)
        return report
    finally:
        _CHECK.release()


def _gate(db, ctx, series: Sequence[Candidate], should_stop: Callable[[], bool], report: CheckReport, **kw):
    """The MangaDex gate of this check, or one that skips nothing when it cannot be prepared (the gate only ever
    saves requests: a failure here costs fetches, never a chapter)."""
    from .chapter_gate import Gate

    try:
        gate = Gate.prepare(db, series, ctx.sources or [], should_stop=should_stop, **kw)
    except Exception as exc:  # noqa: BLE001 - every series is fetched, as before the gate
        _log.warning("%s: the MangaDex gate could not be prepared (%s: %s); every series is fetched", LABEL,
                     type(exc).__name__, exc)
        return Gate.off(db)
    report.from_mangapixer, report.own_requests = gate.from_mangapixer, gate.own_requests
    return gate


def _check(db, backend, report: CheckReport, series: Sequence[Candidate], should_stop: Callable[[], bool],
           pause: float, sleep: Callable[[float], None], gate_args: Optional[Dict[str, Any]] = None) -> None:
    from .finder import _chapter_client, _close, _Context
    from .finding import FindReport, TRIGGER_SCHEDULE

    store = FindingStore(db)
    ctx = _Context(db=db, backend=backend, trigger=TRIGGER_SCHEDULE, store=store,
                   report=FindReport(run_id=None, trigger=TRIGGER_SCHEDULE), run_id=None,  # type: ignore[arg-type]
                   label=LABEL)
    try:
        client = _chapter_client(ctx)
        if client is None:                # (Suwayomi not answering counts as an error there)
            report.not_checked = ctx.chapters_off or "Suwayomi is not available"
            return
        try:
            queue = list(client.queue())
        except Exception:  # noqa: BLE001 - a courtesy check, as in the chapters panel; the ledger guards the sends
            _log.info("%s: Suwayomi's download queue could not be read", LABEL)
            queue = []
        gate = _gate(db, ctx, series, should_stop, report, **(gate_args or {}))
        for i, cand in enumerate(series):
            if i and pause > 0:
                _wait(pause, should_stop, sleep)
            if should_stop():
                report.stopped = True
                _log.info("%s: asked to stop; %d series wait for the next check", LABEL, len(series) - i)
                break
            try:
                _check_one(ctx, report, cand, queue, gate)
            except _Stop:
                report.lines.append("Suwayomi stopped answering; the rest waits for the next check")
                break
    finally:
        report.sent, report.waiting = ctx.report.sent, ctx.report.found
        report.errors += ctx.report.errors
        _close(ctx.chapter_client)


def _check_one(ctx, report: CheckReport, cand: Candidate, queue: Sequence[Any], gate: Any = None) -> None:
    from .chapter_gate import Gate, local_state
    from .chapters import HOW_MANGADEX, find_manga, newer_lookup, newer_text
    from .finder import _line, _suwayomi_up

    gate = gate if gate is not None else Gate.off(ctx.db)

    sid, name = cand.series_id, cand.name
    suwayomi = ctx.backend.suwayomi
    stored = suwayomi.series_choice(sid)
    try:
        found = find_manga(ctx.chapter_client, ctx.sources or [], mangadex_id=cand.mangadex_id, titles=(),
                           stored=stored, title_search=False)
    except Exception as exc:  # noqa: BLE001 - MangaDex's id search failed: next time
        if not _suwayomi_up(ctx):
            ctx.report.errors += 1
            _line(ctx, name, f"Suwayomi did not answer ({type(exc).__name__})")
            raise _Stop() from exc
        report.unreadable += 1
        _log.info("%s: %s: Suwayomi's MangaDex search failed (%s); next time", LABEL, name, type(exc).__name__)
        return
    match = found.match
    if match is None:                   # the saved source is no longer allowed, or MangaDex lacks the linked id
        report.skipped[SKIP_NO_SOURCE] += 1
        _log.debug("%s: %s: no source to ask (%s)", LABEL, name, "; ".join(found.notes) or "none saved")
        return
    if match.how == HOW_MANGADEX and stored.get("manga_id") != match.manga.id:
        suwayomi.set_series_choice(sid, source_id=match.source.id, manga_id=match.manga.id,
                                   manga_title=match.manga.title, how=HOW_MANGADEX)
    report.series_checked += 1
    if sid in gate.planned and match.source.is_mangadex:
        try:
            same = gate.unchanged(sid, match, local_state(ctx.db, ctx.backend, ctx.store, sid))
        except Exception:  # noqa: BLE001 - cannot tell: fetched
            _log.debug("%s: %s: the MangaDex gate could not tell; fetched", LABEL, name, exc_info=True)
            same = None
        if same is not None:
            report.unchanged += 1
            _log.debug("%s: %s: not fetched: unchanged on MangaDex since its last check (latest upload %s, from %s)",
                       LABEL, name, same.chapter or "none", same.origin)
            return
    report.fetched += 1
    try:
        lookup = newer_lookup(ctx.chapter_client, ctx.db, series_id=sid, match=match,
                              held=ctx.backend._held_groups(sid), stored_group=stored.get("group"),
                              in_hand=ctx.backend._chapters_in_hand(sid), queue=queue)
    except Exception as exc:  # noqa: BLE001 - the source failed (Cloudflare, taken down): next time
        if not _suwayomi_up(ctx):
            ctx.report.errors += 1
            _line(ctx, name, f"Suwayomi did not answer ({type(exc).__name__})")
            raise _Stop() from exc
        report.unreadable += 1
        _log.info("%s: %s: %s manga %s could not be read (%s: %s); next time", LABEL, name,
                  match.source.display_name, match.manga.id, type(exc).__name__, exc)
        return
    errors = ctx.report.errors
    _offer_or_send(ctx, report, cand, match, lookup, newer_text(lookup))
    if ctx.report.errors == errors:     # the series' check succeeded: what MangaDex said before it is kept
        try:
            gate.record(sid, match, lookup.manga_url, local_state(ctx.db, ctx.backend, ctx.store, sid))
        except Exception:  # noqa: BLE001 - not kept: the next check fetches the series
            _log.debug("%s: %s: the MangaDex gate's value could not be kept", LABEL, name, exc_info=True)


def _offer_or_send(ctx, report: CheckReport, cand: Candidate, match, lookup, found_text: str) -> None:
    from .finder import _offer, _send_chapters, _settled, _waits, chapter_pick, is_newer

    sid, name = cand.series_id, cand.name
    waiting = ctx.store.list(None, sid)
    elsewhere = {_key(v) for f in waiting if f.status == WAITING and f.kind == "chapters" and not is_newer(f)
                 for v in f.fills}
    rows = [r for r in lookup.rows if r.in_hand is None and not r.extra]
    picks = [r.default for r in rows if r.available and r.default is not None
             and r.number not in lookup.queued_in_suwayomi and _key(r.number) not in elsewhere]
    keep: Set[str] = set()
    if not picks:
        _log.debug("%s: %s: %s", LABEL, name, found_text)
        _retire_newer(ctx, sid, keep)
        return
    source_name = lookup.source_name or match.source.display_name
    pick = chapter_pick(match, picks, lookup.group.group, source_name, lookup.manga_title or match.manga.title,
                        newer=True, newer_than=lookup.newer_than)
    keep.add(pick.pick_key)
    existing = {(f.kind, f.pick_key): f for f in waiting}
    if not _settled(existing, pick.kind, pick.pick_key):
        report.with_new += 1
        after = f"after ch {lookup.newer_than}" if lookup.newer_than is not None else "(no chapter held)"
        reason = _waits(ctx, cand.mode, None)
        said: List[str] = []
        if reason is None:
            reason = _send_chapters(ctx, sid, name, pick, match, picks, pick.payload["manga_title"], source_name,
                                    said=said)
        if reason is None:
            text = f"{pick.title} new {after}: {'; '.join(said) or 'sent'}"
        else:
            _offer(ctx, sid, pick, reason)
            text = f"{pick.title} new {after}: waits ({reason})"
        # one form per chapter: why a whole chapter stands in, a late part, the other form not taken (owner, 2026-10-18)
        picked = {r.number for r in rows if r.default in picks}
        forms = [f"ch {r.number} {r.note}" for r in rows if r.note and r.number in picked]
        forms += [f"not taken: ch {r.number} ({r.note})" for r in lookup.rows if r.extra and r.note]
        if forms:
            text += "; " + "; ".join(forms)
        report.lines.append(f"{name}: {text}")
        _log.info("%s: %s: %s", LABEL, name, text)
    _retire_newer(ctx, sid, keep)


def _retire_newer(ctx, sid: int, keep: Set[str]) -> None:
    """The series' waiting newer-chapter findings this check did not find again: gone (their chapters were sent or
    filed, or a later chapter came and the pick changed)."""
    from .finder import is_newer

    for f in ctx.store.list(WAITING, sid):
        if not is_newer(f) or f.pick_key in keep:
            continue
        try:
            ctx.store.set_status(f.id, GONE, expect=(WAITING,))
        except FindingConflict:
            continue
        _log.info("%s: series %s: finding %r retired: not new any more", LABEL, sid, f.title)


__all__ = ["CheckBusy", "CheckReport", "Candidate", "DEFAULT_PAUSE", "Selection", "run_chapter_check",
           "select_series", "still_new", "sync_mangapixer"]
