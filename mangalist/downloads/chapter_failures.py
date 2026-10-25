"""Series whose chapter source keeps failing in the scheduled chapter check (owner-approved 2026-10-24).

The check (:mod:`mangalist.downloads.chapter_check`) logs "<series>: <source> manga N could not be read (<reason>)" at
INFO and its summary says only "2 could not be read"; a source taken down, or a Cloudflare wall that never lifts, stayed
invisible for weeks. This remembers, per series, how many checks IN A ROW could not read it (a successful read - or a
MangaDex series unchanged since its last good read - starts the count again, and so does picking another source) and,
from :data:`WARN_AFTER` checks on, the Download tab says so on the series: a chip on its Missing chapters row and a line
in its chapters panel with the reason and "Change source…" as the way on. Modest by design: one meta row per failing
series (``chapter_check.failures.<series id>`` -> JSON), no schema change; a series that recovers loses it. No Qt here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, Optional

from ..store.db import utcnow

_log = logging.getLogger(__name__)

PREFIX = "chapter_check.failures."
WARN_AFTER = 3                          # consecutive checks that could not read the series before the warning shows
MAX_REASON = 200


@dataclass(frozen=True)
class Failure:
    series_id: int
    count: int                          # checks in a row that could not read the series
    reason: str                         # the latest one's
    source: str                         # the source asked ("" when unknown)
    since: str                          # when the first of them failed
    last: str                           # when the latest did

    @property
    def warn(self) -> bool:
        return self.count >= WARN_AFTER


def _key(series_id: int) -> str:
    return f"{PREFIX}{int(series_id)}"


def _short(reason: str) -> str:
    reason = " ".join(str(reason).split())
    return reason if len(reason) <= MAX_REASON else reason[:MAX_REASON - 1] + "…"


def get(db, series_id: int) -> Optional[Failure]:
    """The series' failure record (any count), None when its last check read it."""
    try:
        return _parse(series_id, db.get_meta(_key(series_id)))
    except Exception:  # noqa: BLE001 - a note must never break a screen or a check
        _log.debug("Chapter check: the failure note of series %s could not be read", series_id, exc_info=True)
        return None


def warnings(db) -> Dict[int, Failure]:
    """series id -> its failure, for every series that failed :data:`WARN_AFTER` checks or more in a row."""
    out: Dict[int, Failure] = {}
    try:
        with db.connect() as con:
            rows = con.execute("SELECT key, value FROM meta WHERE key LIKE ?", (PREFIX + "%",)).fetchall()
    except Exception:  # noqa: BLE001
        _log.debug("Chapter check: the failure notes could not be read", exc_info=True)
        return out
    for row in rows:
        try:
            sid = int(row["key"][len(PREFIX):])
        except ValueError:
            continue
        failure = _parse(sid, row["value"])
        if failure is not None and failure.warn:
            out[sid] = failure
    return out


def _parse(series_id: int, raw: Optional[str]) -> Optional[Failure]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Failure(int(series_id), int(data["count"]), str(data.get("reason") or ""), str(data.get("source") or ""),
                       str(data.get("since") or ""), str(data.get("last") or ""))
    except (ValueError, KeyError, TypeError):
        return None


def record_failure(db, series_id: int, reason: str, source: str = "") -> Optional[Failure]:
    """One more check in a row could not read the series. Returns the updated record (None if it could not be kept)."""
    try:
        now = utcnow()
        previous = get(db, series_id)
        count = (previous.count if previous is not None else 0) + 1
        failure = Failure(int(series_id), count, _short(reason), source,
                          previous.since if previous is not None else now, now)
        db.set_meta(_key(series_id), json.dumps({"count": failure.count, "reason": failure.reason,
                                                 "source": failure.source, "since": failure.since,
                                                 "last": failure.last}))
        return failure
    except Exception:  # noqa: BLE001
        _log.warning("Chapter check: could not keep the failure note of series %s", series_id, exc_info=True)
        return None


def clear(db, series_id: int) -> None:
    """The series was read (or its source changed): the count starts again."""
    try:
        with db.connect() as con:
            con.execute("DELETE FROM meta WHERE key = ?", (_key(series_id),))
    except Exception:  # noqa: BLE001
        _log.debug("Chapter check: could not clear the failure note of series %s", series_id, exc_info=True)


def warning_text(failure: Failure) -> str:
    """The line the Download tab shows (chapters panel); the row's chip is :data:`CHIP_TEXT`."""
    source = failure.source or "its source"
    return (f"The scheduled chapter check could not read {source} for this series in {failure.count} checks in a row "
            f"({failure.reason or 'no reason given'}). Chapters will not be found automatically until it can. "
            "Change source… picks another.")


CHIP_TEXT = "Source not readable"          # the Download tab's chip (gui/download_tab.py: CHECK_FAILING_CHIP)

class FailuresBackendMixin:
    """The Download tab's reads of the failure notes (mixed into the downloads backend, :class:`~mangalist.downloads.
    adapter.Backend`; needs ``self.db``)."""

    def chapter_check_warnings(self) -> Dict[int, str]:
        """series id -> the warning to show, for every series the scheduled check could not read for WARN_AFTER checks
        in a row (a quick read: one query)."""
        return {sid: warning_text(f) for sid, f in warnings(self.db).items()}

    def forget_chapter_check_failures(self, series_id: int) -> None:
        """The owner picked another source for the series: its failures in a row start again."""
        clear(self.db, series_id)


__all__ = ["FailuresBackendMixin", "CHIP_TEXT", "Failure", "WARN_AFTER", "clear", "get", "record_failure", "warning_text", "warnings"]
