"""The download queue: sends that would take MangaList past its download budget wait here (QUEUED records) and are
handed to qBittorrent, in queue order, as room frees up. No Qt here.

Owner, 2026-10-09: "the rest of the items are queued if that cap is reached" (the budget itself: :mod:`.budget`).

- **Submitting** (:func:`submit`, the GUI's send): the pick is checked exactly as a send is (:func:`.service.check_pick`:
  target folder, wanted volumes, save path, not already queued or in qBittorrent). Then the budget decides: it fits ->
  sent now; it would go over the cap, or others are waiting in the queue (first come, first served) -> recorded as
  QUEUED, with everything a later send needs (the ledger's ``request`` JSON: the release, the wanted volumes, the
  partial-or-whole choice; the target folder) - unless the owner chose to send it now, past the cap. A release bigger
  than the cap on its own is never queued (it would never fit): it is refused unless the owner sends it past the cap.
- **Handing over** (:func:`run_queue`; the hourly ``downloads`` job and "Check qBittorrent now", after the arrivals
  pass and Remove Completed): first every record's size is brought up to date from qBittorrent (its own figure for the
  files it downloads) and FAILED records whose torrent is gone stop counting; then the queue is walked in order. The
  first item that does not fit stops the walk - nothing behind it jumps ahead. An item bigger than the cap on its own
  (the owner lowered the cap after queueing it) is passed over with a note on the record ("send it now, past the cap,
  or remove it"), so it never blocks the others. An item is claimed (QUEUED -> SENT) before it is added, so two passes
  never send it twice; when qBittorrent cannot be reached it goes back to the queue, in its place, and the walk stops;
  when the send itself fails (the release is gone from nyaa, the target folder is gone, a partial pack cannot be set
  up) it becomes FAILED with the reason and the walk goes on.
- **The owner's overrides**: :func:`send_now` (a queued item, past the cap), ``ledger.move_to_front`` and
  ``ledger.cancel`` (remove it from the queue).

Every hand-over and every refusal is logged at INFO with the sizes in GB, the cap and the place in the queue.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .budget import (
    COUNTED,
    OVER,
    OVER_CAP_QUEUE,
    OVER_CAP_SEND,
    SIZE_CLIENT,
    SIZE_RELEASE,
    SIZE_SELECTED,
    TOO_BIG,
    BudgetState,
    gb_text,
    place_text,
    state_of,
)
from .contracts import QBITTORRENT_CATEGORY, DownloadRecord, DownloadStatus, NyaaCandidate, Placement, TorrentClient
from .options import get_budget_gb
from .partial import PackOutcome
from .placement import locate_series
from .service import PackWait, SendRefused, check_pick, dispatch

_log = logging.getLogger(__name__)

# One hand-over at a time in this process (the scheduler's pass and "Check qBittorrent now" may meet): the budget is
# read and spent as one step. Records are claimed compare-and-set as well, so another process can never send one twice.
_HAND_OVER = threading.Lock()


class OverBudget(SendRefused):
    """A release bigger than the whole cap on its own, sent without the owner's override (nothing was added)."""


class ClientDown(RuntimeError):
    """The download client could not be reached (or would not let MangaList in); the queue is left as it was."""


def budget_state(ledger) -> BudgetState:
    """The budget right now: every record of the ledger against the cap in Settings > Download sources."""
    return state_of(list(ledger.all_records()), get_budget_gb(ledger.store))


def client_down(exc: BaseException) -> bool:
    """True when *exc* says the client is not there (try again later), not that this one send is wrong."""
    if isinstance(exc, (ConnectionError, TimeoutError, ClientDown)):
        return True
    try:
        from ..services.qbittorrent.client import AuthFailed, UnexpectedResponse, Unreachable
    except ImportError:         # pragma: no cover - the client ships with MangaList
        return False
    return isinstance(exc, (Unreachable, AuthFailed, UnexpectedResponse))


# --- submitting (the GUI's send) ------------------------------------------------------------------------------------


def submit(client: TorrentClient, ledger, series_id: int, candidate: NyaaCandidate, wanted_volumes: Sequence[str],
           placement: Placement, save_path: str, *, only_missing: bool = False, size_bytes: Optional[int] = None,
           size_source: Optional[str] = None, over_cap: str = OVER_CAP_QUEUE, wait: Optional[PackWait] = None,
           on_pack: Optional[Callable[[PackOutcome], None]] = None) -> DownloadRecord:
    """Send the pick now when it fits the budget, else queue it (``over_cap="queue"``, the default) or send it past the
    cap (``over_cap="send"``, the owner's override). *size_bytes* is what it counts (default: the release's size; a
    partial send passes its selected files' total, ``size_source="selected files"``). Returns the record: SENT or
    QUEUED. Raises :class:`OverBudget` for a release bigger than the cap on its own unless ``over_cap="send"``."""
    if over_cap not in (OVER_CAP_QUEUE, OVER_CAP_SEND):
        raise ValueError(f"over_cap is 'queue' or 'send', not {over_cap!r}")
    size = size_bytes if size_bytes and size_bytes > 0 else max(0, candidate.size_bytes)
    source = (size_source or SIZE_RELEASE) if size_bytes and size_bytes > 0 else SIZE_RELEASE
    target = check_pick(ledger, candidate, wanted_volumes, placement, save_path)
    with _HAND_OVER:
        state = budget_state(ledger)
        # A torrent that already counts (a failed download's, still in qBittorrent) adds nothing when sent again.
        verdict = state.verdict(0 if candidate.info_hash.lower() in state.hashes else size)
        if verdict == TOO_BIG and over_cap != OVER_CAP_SEND:
            _log.info("Budget: %s refused: %s is bigger than the whole cap of %s on its own (send it past the cap, or "
                      "not at all)", candidate.title, gb_text(size), gb_text(state.cap_bytes))
            raise OverBudget(f"this release is {gb_text(size)}, bigger than the download budget of "
                             f"{gb_text(state.cap_bytes)} on its own; send it past the cap or not at all")
        if verdict in (OVER, TOO_BIG) and over_cap == OVER_CAP_QUEUE:
            record = ledger.create(series_id, candidate, wanted_volumes, target, status=DownloadStatus.QUEUED,
                                   only_missing=only_missing, size_bytes=size, size_source=source)
            record = ledger.get(record.id) or record
            _log.info("Budget: download %d (%s, %s) queued, %s: %s, %d waiting", record.id, candidate.title,
                      gb_text(size), place_text(record.queue_position), _why_waiting(state, size), len(state.queued) + 1)
            return record
        from .service import send_pick

        record = send_pick(client, ledger, series_id, candidate, wanted_volumes, placement, save_path,
                           only_missing=only_missing, wait=wait, on_pack=on_pack, size_bytes=size, size_source=source)
        if verdict != "fits":
            _log.info("Budget: download %d (%s, %s) sent PAST the cap on the owner's word: now %s", record.id,
                      candidate.title, gb_text(record.size_bytes), budget_state(ledger).usage_text())
        else:
            _log.info("Budget: download %d (%s, %s) fits: now %s", record.id, candidate.title,
                      gb_text(record.size_bytes), budget_state(ledger).usage_text())
        return record


def _why_waiting(state: BudgetState, size: int) -> str:
    if state.fits(size) and state.queued:
        return f"{len(state.queued)} queued ahead of it ({state.usage_text()})"
    return f"{gb_text(size)} does not fit ({state.usage_text()}, {gb_text(state.free_bytes)} free)"


# --- handing over (the downloads job) -------------------------------------------------------------------------------


@dataclass
class QueueReport:
    """What one queue pass did, by record id."""

    sized: List[int] = field(default_factory=list)          # sizes taken from qBittorrent's own figures
    released: List[int] = field(default_factory=list)       # FAILED records whose torrent is gone: no longer counted
    sent: List[int] = field(default_factory=list)
    failed: List[Tuple[int, str]] = field(default_factory=list)
    waiting: List[Tuple[int, str]] = field(default_factory=list)
    error: Optional[str] = None                             # the pass stopped: the client could not be reached
    usage: str = ""                                         # the budget after the pass ("using 12 GB of 50 GB")

    def summary(self) -> Dict[str, object]:
        return {"queue_sent": len(self.sent), "queue_failed": len(self.failed), "queue_waiting": len(self.waiting),
                "sizes_updated": len(self.sized), "budget": self.usage}

    def text(self) -> str:
        """``queue: 1 handed over, 2 waiting; using 31 GB of 50 GB`` ('' when the queue had nothing to do)."""
        if not (self.sent or self.failed or self.waiting or self.error):
            return ""
        bits = [f"{len(self.sent)} handed over"]
        if self.failed:
            bits.append(f"{len(self.failed)} failed")
        if self.waiting:
            bits.append(f"{len(self.waiting)} waiting")
        out = "queue: " + ", ".join(bits)
        if self.error:
            out += f" (stopped: {self.error})"
        return out + (f"; {self.usage}" if self.usage else "")


def refresh(client: TorrentClient, ledger, report: Optional[QueueReport] = None) -> QueueReport:
    """Bring the counted sizes up to date from qBittorrent: a torrent in the ``mangalist`` category that reports its
    size gives its record that size; a FAILED record whose torrent is no longer there stops counting. Raises when the
    client cannot list its torrents (nothing is changed then)."""
    report = report or QueueReport()
    torrents = {t.info_hash.lower(): t for t in client.torrents(QBITTORRENT_CATEGORY)}
    for rec in ledger.all_records():
        if rec.status not in (*COUNTED, DownloadStatus.FAILED):
            continue
        t = torrents.get(rec.info_hash.lower())
        if rec.status == DownloadStatus.FAILED:
            if rec.in_client != (t is not None) and ledger.note_in_client(rec.id, t is not None) and t is None:
                report.released.append(rec.id)
                _log.info("Budget: failed download %d (%s) is no longer in qBittorrent; its %s no longer count",
                          rec.id, rec.title, gb_text(rec.size_bytes))
        if t is not None and t.size > 0 and (t.size != rec.size_bytes or rec.size_source != SIZE_CLIENT):
            if ledger.note_size(rec.id, t.size, SIZE_CLIENT):
                report.sized.append(rec.id)
                _log.debug("Budget: download %d (%s): qBittorrent reports %s (was %s, %s)", rec.id, rec.title,
                           gb_text(t.size), gb_text(rec.size_bytes), rec.size_source or "not known")
    return report


def run_queue(client: TorrentClient, ledger, *, should_stop: Callable[[], bool] = lambda: False,
              wait: Optional[PackWait] = None) -> QueueReport:
    """One queue pass: :func:`refresh`, then hand queued items over in order while they fit (see the module docstring).
    Never raises for the client: a client that cannot be reached is ``report.error``."""
    report = QueueReport()
    with _HAND_OVER:
        try:
            refresh(client, ledger, report)
        except Exception as exc:  # noqa: BLE001 - nothing changes while qBittorrent cannot be asked
            report.error = f"qBittorrent could not list the {QBITTORRENT_CATEGORY!r} category: {type(exc).__name__}: {exc}"
            _log.warning("Budget: %s; the queue waits", report.error)
            return report
        state = budget_state(ledger)
        for rec in state.queued:                # in queue order
            if should_stop():
                break
            if state.too_big(rec.size_bytes):
                note = (f"bigger than the {gb_text(state.cap_bytes)} cap on its own ({gb_text(rec.size_bytes)}): send "
                        "it now, past the cap, or remove it from the queue")
                _note(ledger, rec, note)
                report.waiting.append((rec.id, note))
                _log.info("Budget: queued download %d (%s, %s) passed over, %s: bigger than the whole cap of %s; it "
                          "waits for the owner", rec.id, rec.title, gb_text(rec.size_bytes),
                          place_text(rec.queue_position), gb_text(state.cap_bytes))
                continue
            if not state.fits(rec.size_bytes):
                why = f"{gb_text(rec.size_bytes)} does not fit ({state.usage_text()}, {gb_text(state.free_bytes)} free)"
                _note(ledger, rec, None)
                report.waiting.extend((r.id, "queued behind it" if r.id != rec.id else why)
                                      for r in state.queued if r.queue_position >= rec.queue_position)
                _log.info("Budget: queued download %d (%s) waits, %s: %s; %d in the queue", rec.id, rec.title,
                          place_text(rec.queue_position), why, len(state.queued))
                break
            try:
                _hand_over(client, ledger, rec, wait=wait, report=report)
            except ClientDown as exc:
                report.error = str(exc)
                break
            state = budget_state(ledger)
        report.usage = budget_state(ledger).usage_text()
    return report


def _note(ledger, rec: DownloadRecord, note: Optional[str]) -> None:
    """Keep (or clear) a note on a QUEUED record, for the In progress list."""
    if (rec.error or None) == (note or None):
        return
    try:
        ledger.set_status(rec.id, DownloadStatus.QUEUED, expect=(DownloadStatus.QUEUED,), error=note)
    except Exception:  # noqa: BLE001 - StatusConflict: it moved meanwhile; nothing to note
        pass


def candidate_of(rec: DownloadRecord, request: Dict[str, object]) -> NyaaCandidate:
    """The release a QUEUED record was queued for, rebuilt from its request JSON."""

    def text(key: str) -> str:
        value = request.get(key)
        return value if isinstance(value, str) else ""

    def number(key: str) -> int:
        value = request.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def opt(key: str) -> Optional[str]:
        value = request.get(key)
        return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None

    covers = request.get("covers_missing")
    return NyaaCandidate(
        title=rec.title, view_url=text("view_url"), torrent_url=text("torrent_url"), info_hash=rec.info_hash,
        size_bytes=number("size_bytes"), seeders=number("seeders"), leechers=0, downloads=0,
        trusted=request.get("trusted") is True, remake=False, published=text("published"), category=text("category"),
        vol_from=opt("vol_from"), vol_to=opt("vol_to"), digital=request.get("digital") is True, group=opt("group"),
        is_pack=request.get("is_pack") is True,
        covers_missing=tuple(str(v) for v in covers) if isinstance(covers, list) else ())


def _hand_over(client: TorrentClient, ledger, rec: DownloadRecord, *, wait: Optional[PackWait],
               report: Optional[QueueReport] = None, past_cap: bool = False) -> DownloadRecord:
    """Send one QUEUED record now. Raises :class:`ClientDown` (the record is queued again, in its place) or, for
    a send that cannot work, records it FAILED and returns it."""
    request = ledger.request(rec.id)
    candidate = candidate_of(rec, request)
    only_missing = request.get("only_missing") is True
    try:
        _series, _root, series_dir = locate_series(ledger.store, rec.series_id)
        save_path = ledger.save_path()
        check_pick(ledger, candidate, rec.wanted_volumes, Placement(series_dir, rec.target_dir, "queued"), save_path,
                   own_record=rec.id)
        if not (candidate.torrent_url or candidate.info_hash):
            raise SendRefused("the queued record has no link to the release")
    except (LookupError, SendRefused) as exc:
        return _failed(ledger, rec, f"not sent from the queue: {exc}", report)
    claimed = ledger.set_status(rec.id, DownloadStatus.SENT, expect=(DownloadStatus.QUEUED,), error=None)
    try:
        outcome = dispatch(client, candidate, rec.wanted_volumes, save_path, only_missing=only_missing, wait=wait)
    except Exception as exc:  # noqa: BLE001 - sorted below: the client is down, or this send cannot work
        if client_down(exc):
            ledger.set_status(rec.id, DownloadStatus.QUEUED, expect=(DownloadStatus.SENT,), error=None)
            _log.warning("Budget: queued download %d (%s) not handed over: qBittorrent cannot be reached (%s: %s); "
                         "it stays queued, %s", rec.id, rec.title, type(exc).__name__, exc,
                         place_text(rec.queue_position))
            raise ClientDown(f"qBittorrent cannot be reached ({type(exc).__name__}); queued downloads wait") from exc
        return _failed(ledger, claimed, f"not sent from the queue: {exc}", report)
    if outcome is not None and outcome.partial:
        ledger.note_size(rec.id, outcome.selection.kept_bytes, SIZE_SELECTED)
    out = ledger.get(rec.id) or claimed
    if report is not None:
        report.sent.append(rec.id)
    _log.info("Budget: queued download %d (%s, %s) handed to qBittorrent%s (was %s); now %s", rec.id, rec.title,
              gb_text(out.size_bytes), " PAST the cap, on the owner's word" if past_cap else "",
              place_text(rec.queue_position), budget_state(ledger).usage_text())
    return out


def _failed(ledger, rec: DownloadRecord, why: str, report: Optional[QueueReport]) -> DownloadRecord:
    out = ledger.set_status(rec.id, DownloadStatus.FAILED, expect=(rec.status,), error=why)
    ledger.note_in_client(rec.id, False)                    # nothing was added (or the add was taken back)
    if report is not None:
        report.failed.append((rec.id, why))
    _log.warning("Budget: queued download %d (%s) FAILED: %s; the queue goes on", rec.id, rec.title, why)
    return ledger.get(rec.id) or out


def send_now(client: TorrentClient, ledger, record_id: int, *, wait: Optional[PackWait] = None) -> DownloadRecord:
    """The owner's override: hand one QUEUED record to qBittorrent now, past the cap. Returns the record (SENT, or
    FAILED with the reason); raises :class:`ClientDown` when qBittorrent cannot be reached (it stays queued) and
    :class:`SendRefused` when the record is not queued (any more)."""
    with _HAND_OVER:
        rec = ledger.get(record_id)
        if rec is None or rec.status != DownloadStatus.QUEUED:
            raise SendRefused("only a queued download can be sent from the queue")
        return _hand_over(client, ledger, rec, wait=wait, past_cap=True)


def move_to_front(ledger, record_id: int) -> DownloadRecord:
    """The owner's override: this queued record is handed over next."""
    rec = ledger.move_to_front(record_id)
    _log.info("Budget: queued download %d (%s) moved to the front of the queue by the owner", rec.id, rec.title)
    return rec


def remove_from_queue(ledger, record_id: int) -> DownloadRecord:
    """Take a queued record out of the queue (CANCELLED; nothing was ever sent)."""
    rec = ledger.set_status(record_id, DownloadStatus.CANCELLED, expect=(DownloadStatus.QUEUED,),
                            error="removed from the queue by the owner")
    _log.info("Budget: queued download %d (%s) removed from the queue by the owner", rec.id, rec.title)
    return rec
