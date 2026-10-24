"""Chapter downloads the program gave up on: retried by MangaList a few times, and by the owner (Retry / Cancel). No Qt.

Suwayomi (and MangaList's built-in extension host, which behaves the same) tries a chapter 3 times within about a
minute, then leaves it in its download queue in the ``ERROR`` state and never tries again by itself. A site that is
down for longer (Weeb Central's HTTP 522s on 2026-10-13) left chapters waiting for days, and MangaList offered no way to
retry them (owner, 2026-10-20: "I can't manually retry this").

- **Stuck**: a SENT chapter record whose chapter is in its program's queue in the ``ERROR`` state. The arrivals pass
  marks the record (:meth:`~mangalist.store.downloads.DownloadLedger.set_stuck`: ``request.stuck``) and keeps why on it
  (:func:`stuck_note`, worded for the program). The downloads list offers Retry by that mark - never by the note's
  words, which name the program (transition audit B1, 2026-10-24: the host's reworded note hid Retry and Cancel).
- **Automatically** (:func:`auto_retry`, from the arrivals pass): a stuck chapter is put back in the queue once every
  :data:`AUTO_EVERY` (6 h), at most :data:`AUTO_RETRIES` (3) times; after that it waits for the owner. Re-queuing a
  chapter in the ``ERROR`` state gives it one more try (Suwayomi 2.4, checked 2026-10-20; the host the same).
- **By the owner** (:func:`retry`, :func:`cancel`; In progress' Retry / Cancel): Retry re-queues the stuck chapters now
  and starts the automatic retries over. Cancel works for ANY chapter download still on its way (SENT), stuck or not:
  it takes the chapters out of the program's queue and marks the records cancelled - also when the program cannot be
  asked (it is stopped, or another program is chosen now), so the switch guard's advice "cancel them" is always
  possible.

Each call goes to the program the record belongs to (``record.backend``; transition audit B2) - the caller passes that
program's client. The automatic retries are counted in the database's ``meta`` table, one key per record
(``chapter_retry.<record id>``, a small JSON object), forgotten once the chapter is no longer stuck.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..store.downloads import DownloadLedger, StatusConflict
from .chapter_backend import backend_of, name_of_backend, wording
from .contracts import CHAPTER_BACKEND_EXTENSIONS, ChapterClient, DownloadRecord, DownloadStatus

_log = logging.getLogger(__name__)

META_PREFIX = "chapter_retry."          # + the record id
#: How often, and how many times, MangaList puts a stuck chapter back in Suwayomi's queue by itself (owner, 2026-10-20).
AUTO_EVERY = timedelta(hours=6)
AUTO_RETRIES = 3
#: Every note of a stuck chapter starts with this (worded for the program: :func:`stuck_note`).
STUCK = "Suwayomi could not download it"
ERROR_STATE = "ERROR"


class RetryRefused(Exception):
    """Retry / Cancel refused: the record is not a stuck chapter download (any more)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def is_stuck(record: DownloadRecord) -> bool:
    """A chapter download its program gave up on, as the last arrivals pass saw it: marked so (``record.stuck``), or -
    a record an older build noted - its note says so in either program's words."""
    if not (record.is_chapters and record.status == DownloadStatus.SENT):
        return False
    note = record.error or ""
    return record.stuck or note.startswith(STUCK) or note.startswith(wording(STUCK, True) or STUCK)


def can_cancel(record: DownloadRecord) -> bool:
    """A chapter download still on its way (SENT): the owner may cancel it, stuck or not (transition audit B1)."""
    return record.is_chapters and record.status == DownloadStatus.SENT


def _builtin(backend: str) -> bool:
    return backend == CHAPTER_BACKEND_EXTENSIONS


def stuck_note(tries: int, auto: int, builtin: bool = False) -> str:
    """Why a stuck record waits (*tries*: the program's count, *auto*: MangaList's automatic retries so far), worded for
    the program (*builtin*: the extension host)."""
    if auto >= AUTO_RETRIES:
        return wording(f"{STUCK} ({tries} tries; MangaList retried it {auto} times) - Retry or Cancel", builtin)
    hours = int(AUTO_EVERY.total_seconds() // 3600)
    done = f"; MangaList retried it {auto} of {AUTO_RETRIES} times" if auto else ""
    return wording(f"{STUCK} ({tries} tries{done}); MangaList retries it within {hours} h - or Retry / Cancel now",
                   builtin)


# --- the automatic retries -------------------------------------------------------------------------------------------

@dataclass
class Tally:
    """MangaList's automatic retries of one stuck record: how many, and since when the current wait runs."""

    since: datetime                     # first seen stuck, or the last automatic retry
    auto: int = 0


def _key(record_id: int) -> str:
    return f"{META_PREFIX}{int(record_id)}"


def load(db, record_id: int) -> Optional[Tally]:
    try:
        data = json.loads(db.get_meta(_key(record_id)) or "null")
        since = datetime.fromisoformat(str(data["since"]).replace("Z", "+00:00"))
    except (TypeError, ValueError, KeyError):
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    auto = data.get("auto")
    return Tally(since, auto if isinstance(auto, int) and auto >= 0 else 0)


def save(db, record_id: int, tally: Tally) -> None:
    db.set_meta(_key(record_id), json.dumps({"since": tally.since.isoformat(), "auto": tally.auto}, sort_keys=True))


def forget(db, record_id: int) -> None:
    if db.get_meta(_key(record_id)) is not None:
        db.set_meta(_key(record_id), None)


def auto_retry(db, client: ChapterClient, record: DownloadRecord, tries: int,
               now: Optional[datetime] = None) -> str:
    """The arrivals pass found *record*'s chapter in Suwayomi's ``ERROR`` state: put it back in the queue when its
    automatic retry is due (and count it), else nothing. Returns the note to keep on the record. Suwayomi not
    answering leaves the tally as it was (the next pass tries again)."""
    now = now or utcnow()
    tally = load(db, record.id)
    if tally is None:
        tally = Tally(since=now)
        save(db, record.id, tally)
    builtin = _builtin(backend_of(client))
    if tally.auto < AUTO_RETRIES and now - tally.since >= AUTO_EVERY:
        client.enqueue([int(record.info_hash)])
        tally = Tally(since=now, auto=tally.auto + 1)
        save(db, record.id, tally)
        _log.info("Chapter arrivals: download %d (%s) retried in %s automatically (%d of %d)", record.id,
                  record.title, name_of_backend(backend_of(client)), tally.auto, AUTO_RETRIES)
        return wording(f"MangaList retried it in Suwayomi ({tally.auto} of {AUTO_RETRIES}) after Suwayomi could not "
                       f"download it ({tries} tries); waiting for Suwayomi", builtin)
    return stuck_note(tries, tally.auto, builtin)


# --- the owner's Retry / Cancel ----------------------------------------------------------------------------------

def _mine(ledger: DownloadLedger, client: ChapterClient, record_ids: Sequence[int]) -> List[DownloadRecord]:
    """The SENT chapter records of *record_ids* that belong to *client*'s program (or to no known program)."""
    backend = backend_of(client)
    records = [r for r in (ledger.get(int(i)) for i in record_ids) if r is not None]
    return [r for r in records if r.is_chapters and r.status == DownloadStatus.SENT and r.backend in ("", backend)]


def _ref(rec: DownloadRecord) -> int:
    return int(rec.info_hash) if str(rec.info_hash).isdigit() else -1


def _stuck_records(ledger: DownloadLedger, client: ChapterClient, record_ids: Sequence[int]) -> List[DownloadRecord]:
    """The records of *record_ids* that are chapter downloads in their program's ``ERROR`` state now (asked live)."""
    records = _mine(ledger, client, record_ids)
    if not records:
        return []
    states: Dict[int, str] = {q.chapter_id: q.state for q in client.queue()}
    return [rec for rec in records if states.get(_ref(rec)) == ERROR_STATE]


def retry(db, client: ChapterClient, record_ids: Sequence[int]) -> int:
    """The owner's Retry: put the stuck chapters of *record_ids* back in their program's queue now; their automatic
    retries start over. Returns how many were retried; raises :class:`RetryRefused` when none is stuck any more."""
    ledger = DownloadLedger(db, tool=None)
    name = name_of_backend(backend_of(client))
    stuck = _stuck_records(ledger, client, record_ids)
    if not stuck:
        for rec in _mine(ledger, client, record_ids):
            DownloadLedger(db, tool=rec.tool).set_stuck(rec.id, False)      # the mark was old: the list stops offering it
        raise RetryRefused(wording("Suwayomi is no longer stuck on it (it is downloading, done, or gone)",
                                   _builtin(backend_of(client))))
    client.enqueue([int(r.info_hash) for r in stuck])
    for rec in stuck:
        forget(db, rec.id)
        chapters = DownloadLedger(db, tool=rec.tool)
        chapters.set_stuck(rec.id, False)
        try:
            ledger.set_status(rec.id, DownloadStatus.SENT, expect=(DownloadStatus.SENT,),
                              error=f"retried by you; waiting for {name}")
        except StatusConflict as exc:
            _log.info("Retry: %s", exc)
    _log.info("Retry: %d stuck chapter download(s) put back in %s's queue: %s", len(stuck),
              name, [r.id for r in stuck])
    return len(stuck)


def cancel(db, client: Optional[ChapterClient], record_ids: Sequence[int], *, backend: str = "") -> int:
    """The owner's Cancel: take the chapters of *record_ids* - any chapter download still on its way (SENT), stuck or
    not - out of their program's queue and mark their records cancelled (out of In progress, under Show finished; a
    check may find the chapter missing again later). *client*: the records' program (None, or a program that does not
    answer: the records are cancelled all the same, the note says the program may still download the chapter; *backend*
    names it then). Returns how many were cancelled; raises :class:`RetryRefused` when none is on its way any more."""
    ledger = DownloadLedger(db, tool=None)
    backend = backend_of(client) if client is not None else backend
    name = name_of_backend(backend)
    records = [r for r in (ledger.get(int(i)) for i in record_ids) if r is not None]
    records = [r for r in records if can_cancel(r) and r.backend in ("", backend)]
    if not records:
        raise RetryRefused("it is no longer on its way (it was downloaded, filed, or cancelled meanwhile)")
    unasked = ""
    if client is None:
        unasked = f"{name} was not asked"
    else:
        try:
            queued = {q.chapter_id for q in client.queue()}
            ids = [_ref(r) for r in records if _ref(r) in queued]
            if ids:
                client.dequeue(ids)
        except Exception as exc:  # noqa: BLE001 - the program is down: the owner's cancel still stands
            unasked = f"{name} could not be asked ({type(exc).__name__})"
            _log.info("Cancel: %s: %s", unasked, exc)
    done = 0
    for rec in records:
        forget(db, rec.id)
        why = rec.error or ("waiting" if not is_stuck(rec) else wording(STUCK, _builtin(backend)))
        note = f"cancelled by you ({why})"
        if unasked:
            note += f"; {unasked}: it may still download the chapter"
        try:
            ledger.set_status(rec.id, DownloadStatus.CANCELLED, expect=(DownloadStatus.SENT,), error=note)
            done += 1
        except StatusConflict as exc:
            _log.info("Cancel: %s", exc)
    _log.info("Cancel: %d chapter download(s) cancelled%s: %s", done,
              f" ({unasked})" if unasked else f", taken out of {name}'s queue", [r.id for r in records])
    return done


__all__ = ["AUTO_EVERY", "AUTO_RETRIES", "RetryRefused", "STUCK", "Tally", "auto_retry", "can_cancel", "cancel",
           "forget", "is_stuck", "load", "retry", "save", "stuck_note"]
