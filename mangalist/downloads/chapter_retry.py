"""Chapter downloads Suwayomi gave up on: retried by MangaList a few times, and by the owner (Retry / Cancel). No Qt.

Suwayomi tries a chapter 3 times within about a minute, then leaves it in its download queue in the ``ERROR`` state and
never tries again by itself. A site that is down for longer (Weeb Central's HTTP 522s on 2026-10-13) left chapters
waiting for days, and MangaList offered no way to retry them (owner, 2026-10-20: "I can't manually retry this").

- **Stuck**: a SENT chapter record whose chapter is in Suwayomi's queue in the ``ERROR`` state. The arrivals pass keeps
  why on the record (:func:`stuck_note`, always starting with :data:`STUCK`), which is how the downloads list knows.
- **Automatically** (:func:`auto_retry`, from the arrivals pass): a stuck chapter is put back in Suwayomi's queue once
  every :data:`AUTO_EVERY` (6 h), at most :data:`AUTO_RETRIES` (3) times; after that it waits for the owner. Re-queuing a
  chapter in the ``ERROR`` state gives it one more try in Suwayomi (Suwayomi 2.4, checked 2026-10-20).
- **By the owner** (:func:`retry`, :func:`cancel`; In progress' Retry / Cancel): Retry re-queues the stuck chapters now
  and starts the automatic retries over; Cancel takes them out of Suwayomi's queue and marks the records cancelled.

The automatic retries are counted in the database's ``meta`` table, one key per record (``chapter_retry.<record id>``,
a small JSON object), forgotten once the chapter is no longer stuck - no schema change.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..store.downloads import DownloadLedger, StatusConflict
from .contracts import ChapterClient, DownloadRecord, DownloadStatus

_log = logging.getLogger(__name__)

META_PREFIX = "chapter_retry."          # + the record id
#: How often, and how many times, MangaList puts a stuck chapter back in Suwayomi's queue by itself (owner, 2026-10-20).
AUTO_EVERY = timedelta(hours=6)
AUTO_RETRIES = 3
#: Every note of a stuck chapter starts with this (the downloads list offers Retry / Cancel for it).
STUCK = "Suwayomi could not download it"
ERROR_STATE = "ERROR"


class RetryRefused(Exception):
    """Retry / Cancel refused: the record is not a stuck chapter download (any more)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def is_stuck(record: DownloadRecord) -> bool:
    """A chapter download Suwayomi gave up on, as the last arrivals pass saw it."""
    return record.is_chapters and record.status == DownloadStatus.SENT and (record.error or "").startswith(STUCK)


def stuck_note(tries: int, auto: int) -> str:
    """Why a stuck record waits (*tries*: Suwayomi's count, *auto*: MangaList's automatic retries so far)."""
    if auto >= AUTO_RETRIES:
        return (f"{STUCK} ({tries} tries; MangaList retried it {auto} times) - Retry or Cancel")
    hours = int(AUTO_EVERY.total_seconds() // 3600)
    done = f"; MangaList retried it {auto} of {AUTO_RETRIES} times" if auto else ""
    return f"{STUCK} ({tries} tries{done}); MangaList retries it within {hours} h - or Retry / Cancel now"


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
    if tally.auto < AUTO_RETRIES and now - tally.since >= AUTO_EVERY:
        client.enqueue([int(record.info_hash)])
        tally = Tally(since=now, auto=tally.auto + 1)
        save(db, record.id, tally)
        _log.info("Chapter arrivals: download %d (%s) retried in Suwayomi automatically (%d of %d)", record.id,
                  record.title, tally.auto, AUTO_RETRIES)
        return (f"MangaList retried it in Suwayomi ({tally.auto} of {AUTO_RETRIES}) after Suwayomi could not download it "
                f"({tries} tries); waiting for Suwayomi")
    return stuck_note(tries, tally.auto)


# --- the owner's Retry / Cancel ----------------------------------------------------------------------------------

def _stuck_records(ledger: DownloadLedger, client: ChapterClient, record_ids: Sequence[int]) -> List[DownloadRecord]:
    """The records of *record_ids* that are chapter downloads in Suwayomi's ``ERROR`` state now (asked live)."""
    records = [r for r in (ledger.get(int(i)) for i in record_ids) if r is not None]
    states: Dict[int, str] = {q.chapter_id: q.state for q in client.queue()}
    out = []
    for rec in records:
        ref = int(rec.info_hash) if str(rec.info_hash).isdigit() else -1
        if rec.is_chapters and rec.status == DownloadStatus.SENT and states.get(ref) == ERROR_STATE:
            out.append(rec)
    return out


def retry(db, client: ChapterClient, record_ids: Sequence[int]) -> int:
    """The owner's Retry: put the stuck chapters of *record_ids* back in Suwayomi's queue now; their automatic retries
    start over. Returns how many were retried; raises :class:`RetryRefused` when none is stuck any more."""
    ledger = DownloadLedger(db, tool=None)
    stuck = _stuck_records(ledger, client, record_ids)
    if not stuck:
        raise RetryRefused("Suwayomi is no longer stuck on it (it is downloading, done, or gone)")
    client.enqueue([int(r.info_hash) for r in stuck])
    for rec in stuck:
        forget(db, rec.id)
        try:
            ledger.set_status(rec.id, DownloadStatus.SENT, expect=(DownloadStatus.SENT,),
                              error="retried by you; waiting for Suwayomi")
        except StatusConflict as exc:
            _log.info("Retry: %s", exc)
    _log.info("Retry: %d stuck chapter download(s) put back in Suwayomi's queue: %s", len(stuck),
              [r.id for r in stuck])
    return len(stuck)


def cancel(db, client: ChapterClient, record_ids: Sequence[int]) -> int:
    """The owner's Cancel: take the stuck chapters of *record_ids* out of Suwayomi's queue and mark their records
    cancelled (out of In progress, under Show finished; a check may find the chapter missing again later). Returns how
    many were cancelled; raises :class:`RetryRefused` when none is stuck any more."""
    ledger = DownloadLedger(db, tool=None)
    stuck = _stuck_records(ledger, client, record_ids)
    if not stuck:
        raise RetryRefused("Suwayomi is no longer stuck on it (it is downloading, done, or gone)")
    client.dequeue([int(r.info_hash) for r in stuck])
    done = 0
    for rec in stuck:
        forget(db, rec.id)
        try:
            ledger.set_status(rec.id, DownloadStatus.CANCELLED, expect=(DownloadStatus.SENT,),
                              error=f"cancelled by you ({rec.error or STUCK})")
            done += 1
        except StatusConflict as exc:
            _log.info("Cancel: %s", exc)
    _log.info("Cancel: %d stuck chapter download(s) taken out of Suwayomi's queue: %s", done, [r.id for r in stuck])
    return done


__all__ = ["AUTO_EVERY", "AUTO_RETRIES", "RetryRefused", "STUCK", "Tally", "auto_retry", "cancel", "forget",
           "is_stuck", "load", "retry", "save", "stuck_note"]
