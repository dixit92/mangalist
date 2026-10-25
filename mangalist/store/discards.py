"""The log of library files the owner deliberately deleted (Duplicates > Discard, the merge's identical copies).

Why (owner, 2026-10-24, backlog bug): a volume a torrent filed and the owner then discarded in the Duplicates view was
deleted directly - not through the journal, so unlike an upgrade's ``hold`` nothing recorded that its absence was on
purpose. The removal check of a finished download (:meth:`~mangalist.downloads.arrivals.Arrivals._filed_files_problem`)
then took it for a lost file, kept the torrent in qBittorrent and logged "... is no longer in the library" every hour.

A discard is recorded as one row of the ``meta`` table, ``discard:<ISO time>:<hash of the path>`` -> JSON
``{"path", "plan", "at"}`` (a log is not worth a schema migration of its own, and rows are only ever inserted, so two
processes never overwrite each other). *plan* is the newest journal plan at that moment: the path is the file's place
at the time, so the check follows a filed path through the journal's moves **up to that plan** only
(:meth:`~mangalist.store.journal.MoveFollower.discarded`), and a folder renamed since does not hide the discard.
Rows older than :data:`RETENTION_DAYS` are dropped when a new one is written (a torrent is long gone by then). A file
that is missing and was NOT discarded still keeps its torrent. No Qt here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, NamedTuple, Optional

from .db import utcnow

_log = logging.getLogger(__name__)

PREFIX = "discard:"
RETENTION_DAYS = 400


class Discard(NamedTuple):
    path: str               # the file's place when it was deleted (absolute)
    plan: int               # the newest journal plan then (0: none yet)
    at: str                 # ISO time


def latest_plan_id(store) -> int:
    """The newest journal plan's id (0 when there is none, or the journal cannot be read)."""
    try:
        with store.connect() as con:
            return int(con.execute("SELECT COALESCE(MAX(id), 0) FROM journal_plans").fetchone()[0])
    except Exception:  # noqa: BLE001 - a missing journal table must never stop a discard
        _log.debug("Discards: the journal's newest plan could not be read", exc_info=True)
        return 0


def record_discard(store, path: str) -> None:
    """Remember that the owner deleted *path* on purpose. Never raises: the file is already gone, and a failed note
    only means its torrent is kept as before."""
    try:
        at = utcnow()
        norm = os.path.normpath(os.fspath(path))
        key = f"{PREFIX}{at}:{hashlib.sha1(os.path.normcase(norm).encode('utf-8', 'surrogatepass')).hexdigest()[:16]}"
        value = json.dumps({"path": norm, "plan": latest_plan_id(store), "at": at})
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).replace(microsecond=0).isoformat()
        with store.connect() as con:
            con.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
            con.execute("DELETE FROM meta WHERE key LIKE ? AND key < ?", (PREFIX + "%", PREFIX + cutoff))
    except Exception:  # noqa: BLE001
        _log.warning("Discards: could not note the deletion of %s", path, exc_info=True)


def list_discards(store) -> List[Discard]:
    """Every recorded discard, oldest first (rows that cannot be read are skipped)."""
    out: List[Discard] = []
    try:
        with store.connect() as con:
            rows = con.execute("SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key", (PREFIX + "%",)).fetchall()
    except Exception:  # noqa: BLE001
        _log.debug("Discards: the log could not be read", exc_info=True)
        return out
    for row in rows:
        try:
            data = json.loads(row["value"])
            out.append(Discard(str(data["path"]), int(data.get("plan") or 0), str(data.get("at") or "")))
        except (ValueError, KeyError, TypeError):
            continue
    return out


__all__ = ["Discard", "RETENTION_DAYS", "latest_plan_id", "record_discard", "list_discards"]
