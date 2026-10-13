"""Automatic downloads (schema migration 7): a series' Off / Notify / Auto mode, what a find pass found, and its runs.

- **Modes.** ``series.auto_mode`` is ``off`` (never searched), ``notify`` (searched; what is found waits for the owner) or
  ``auto`` (searched; what is found is sent within the guards); NULL follows the default setting
  (:data:`SETTING_DEFAULT_MODE`, ``notify`` unless the owner changed it - owner, 2026-10-10).
- **Findings.** One row per pick a pass found for a series: a release (``volumes`` / ``upgrades``) or a set of chapters
  (``chapters``). ``pick_key`` names the pick (``nyaa:<info hash>``, ``chapters:<source id>:<chapter ids>``); with the
  series and the kind it is unique, so the same pick is never listed twice and a pick the owner skipped is never offered
  again (steady state stays quiet). ``payload`` is the finder's own JSON - what sending the pick later needs.

  ===========  =====================================================================================================
  ``waiting``  found; waits for the owner (Notify, or a pick Auto must not send by itself - the reason says why)
  ``sent``     sent, by the owner or automatically (``record_id``: the ledger row)
  ``skipped``  the owner skipped it: not offered again
  ``gone``     no longer wanted (the gap was filled another way, the release vanished): dropped from the list
  ===========  =====================================================================================================

  Status changes are compare-and-set (``expect``), like the ledger's and the replacements', so a pass and the GUI never
  act on one finding twice.
- **Runs.** One row per find pass (scheduled, Find now, bulk find) with its counts and summary.

:class:`FindingStore` wraps a :class:`~mangalist.store.Store`. No Qt here.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .db import utcnow
from .schema import AUTO_MODE_DEFAULT, AUTO_MODES, FIND_TRIGGERS, FINDING_KINDS, FINDING_STATUS

SETTING_DEFAULT_MODE = "automation.default_mode"

WAITING, SENT, SKIPPED, GONE = "waiting", "sent", "skipped", "gone"
MODE_OFF, MODE_NOTIFY, MODE_AUTO = AUTO_MODES


class FindingConflict(ValueError):
    """The finding was not in the expected status (another pass or window moved it meanwhile)."""


@dataclass(frozen=True)
class Finding:
    id: int
    series_id: int
    kind: str                           # FINDING_KINDS
    pick_key: str
    title: str
    fills: Tuple[str, ...]              # exact numbers
    size_bytes: Optional[int]
    reason: str
    payload: Dict[str, Any]
    status: str                         # FINDING_STATUS
    run_id: Optional[int]
    record_id: Optional[int]
    found_at: str
    updated_at: str


@dataclass
class NewFinding:
    """What :meth:`FindingStore.offer` records."""

    series_id: int
    kind: str
    pick_key: str
    title: str
    fills: Sequence[str] = ()
    size_bytes: Optional[int] = None
    reason: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FindRun:
    id: int
    trigger: str                        # FIND_TRIGGERS
    started_at: str
    finished_at: Optional[str]
    series_searched: int
    found: int
    sent: int
    errors: int
    summary: str


def _loads(text: Optional[str], default):
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _finding(r: sqlite3.Row) -> Finding:
    return Finding(id=r["id"], series_id=r["series_id"], kind=r["kind"], pick_key=r["pick_key"], title=r["title"],
                   fills=tuple(str(x) for x in _loads(r["fills"], [])), size_bytes=r["size_bytes"], reason=r["reason"],
                   payload=_loads(r["payload"], {}), status=r["status"], run_id=r["run_id"], record_id=r["record_id"],
                   found_at=r["found_at"], updated_at=r["updated_at"])


def _run(r: sqlite3.Row) -> FindRun:
    return FindRun(id=r["id"], trigger=r["trigger"], started_at=r["started_at"], finished_at=r["finished_at"],
                   series_searched=r["series_searched"], found=r["found"], sent=r["sent"], errors=r["errors"],
                   summary=r["summary"])


class FindingStore:
    def __init__(self, store):
        self.store = store

    # --- modes ---------------------------------------------------------------------------------------

    def default_mode(self) -> str:
        value = self.store.get_setting(SETTING_DEFAULT_MODE)
        return value if value in AUTO_MODES else AUTO_MODE_DEFAULT

    def set_default_mode(self, mode: str) -> None:
        if mode not in AUTO_MODES:
            raise ValueError(f"not a mode: {mode!r}")
        self.store.set_setting(SETTING_DEFAULT_MODE, mode)

    def own_mode(self, series_id: int) -> Optional[str]:
        """The series' own mode, or None when it follows the default."""
        with self.store.connect() as con:
            row = con.execute("SELECT auto_mode FROM series WHERE id = ?", (series_id,)).fetchone()
        value = row["auto_mode"] if row is not None else None
        return value if value in AUTO_MODES else None

    def mode(self, series_id: int) -> str:
        """The mode in force for the series: its own, else the default."""
        return self.own_mode(series_id) or self.default_mode()

    _MODES_SQL = ("SELECT s.id, s.auto_mode, r.auto_mode AS root_mode FROM series s"
                  " LEFT JOIN roots r ON r.id = s.root_id")

    def modes(self, series_ids: Optional[Iterable[int]] = None) -> Dict[int, str]:
        """series id -> the mode in force (every series when *series_ids* is None): the series' own mode, else its
        library's (schema 8), else the default setting."""
        default = self.default_mode()
        with self.store.connect() as con:
            if series_ids is None:
                rows = con.execute(self._MODES_SQL).fetchall()
            else:
                ids = sorted(set(int(i) for i in series_ids))
                rows = [r for chunk in _chunks(ids) for r in con.execute(
                    f"{self._MODES_SQL} WHERE s.id IN ({','.join('?' * len(chunk))})", chunk).fetchall()]
        return {r["id"]: (r["auto_mode"] if r["auto_mode"] in AUTO_MODES
                          else r["root_mode"] if r["root_mode"] in AUTO_MODES else default) for r in rows}

    def default_for(self, series_id: int) -> Tuple[str, bool]:
        """(the mode the series follows when it has none of its own, True when that is its library's - else the
        default setting)."""
        with self.store.connect() as con:
            row = con.execute("SELECT r.auto_mode FROM series s JOIN roots r ON r.id = s.root_id WHERE s.id = ?",
                              (int(series_id),)).fetchone()
        if row is not None and row[0] in AUTO_MODES:
            return row[0], True
        return self.default_mode(), False

    def set_mode(self, series_ids: Iterable[int], mode: Optional[str]) -> int:
        """Set the series' own mode (None: follow the default). Returns how many series rows changed."""
        if mode is not None and mode not in AUTO_MODES:
            raise ValueError(f"not a mode: {mode!r}")
        ids = sorted(set(int(i) for i in series_ids))
        n = 0
        with self.store.connect() as con:
            for chunk in _chunks(ids):
                n += con.execute(f"UPDATE series SET auto_mode = ? WHERE id IN ({','.join('?' * len(chunk))})",
                                 (mode, *chunk)).rowcount
        return n

    # --- findings ------------------------------------------------------------------------------------

    def get(self, finding_id: int) -> Optional[Finding]:
        with self.store.connect() as con:
            row = con.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
        return _finding(row) if row is not None else None

    def list(self, status: Optional[str] = WAITING, series_id: Optional[int] = None) -> List[Finding]:
        """Findings, newest first (*status* None: every status)."""
        where, args = [], []
        if status is not None:
            where.append("status = ?")
            args.append(status)
        if series_id is not None:
            where.append("series_id = ?")
            args.append(series_id)
        sql = "SELECT * FROM findings" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY found_at DESC, id DESC"
        with self.store.connect() as con:
            return [_finding(r) for r in con.execute(sql, args).fetchall()]

    def count_waiting(self) -> int:
        with self.store.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM findings WHERE status = 'waiting'").fetchone()[0])

    def offer(self, new: NewFinding, run_id: Optional[int] = None) -> Tuple[Optional[Finding], bool]:
        """Record a pick as ``waiting``. Returns (the finding, True) when it is now waiting; (the existing finding, False)
        when this pick was already offered: a waiting one is refreshed (title, fills, size, reason, payload, run), a
        skipped / sent one is left alone and NOT offered again, a gone one comes back as waiting."""
        if new.kind not in FINDING_KINDS:
            raise ValueError(f"not a finding kind: {new.kind!r}")
        now = utcnow()
        fields = (new.title, json.dumps([str(x) for x in new.fills]), new.size_bytes, new.reason,
                  json.dumps(new.payload, sort_keys=True))
        with self.store.connect() as con:
            row = con.execute("SELECT * FROM findings WHERE series_id = ? AND kind = ? AND pick_key = ?",
                              (new.series_id, new.kind, new.pick_key)).fetchone()
            if row is None:
                cur = con.execute(
                    "INSERT INTO findings (series_id, kind, pick_key, title, fills, size_bytes, reason, payload, status,"
                    " run_id, found_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?)",
                    (new.series_id, new.kind, new.pick_key, *fields, run_id, now, now))
                fid, fresh = cur.lastrowid, True
            elif row["status"] in (SKIPPED, SENT):
                return _finding(row), False
            else:
                con.execute("UPDATE findings SET title = ?, fills = ?, size_bytes = ?, reason = ?, payload = ?,"
                            " status = 'waiting', run_id = ?, updated_at = ? WHERE id = ?", (*fields, run_id, now, row["id"]))
                fid, fresh = row["id"], row["status"] == GONE
            out = con.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone()
        return _finding(out), fresh

    def set_status(self, finding_id: int, status: str, *, expect: Optional[Sequence[str]] = (WAITING,),
                   record_id: Optional[int] = None) -> Finding:
        """Move a finding to *status* when it is in one of *expect* (None: any); raises :class:`FindingConflict`."""
        if status not in FINDING_STATUS:
            raise ValueError(f"not a finding status: {status!r}")
        with self.store.connect() as con:
            row = con.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
            if row is None:
                raise FindingConflict(f"finding {finding_id} does not exist")
            if expect is not None and row["status"] not in expect:
                raise FindingConflict(f"finding {finding_id} is {row['status']}, not {'/'.join(expect)}")
            con.execute("UPDATE findings SET status = ?, record_id = COALESCE(?, record_id), updated_at = ? WHERE id = ?",
                        (status, record_id, utcnow(), finding_id))
            out = con.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
        return _finding(out)

    def retire(self, series_id: int, kind: str, keep: Iterable[str] = ()) -> int:
        """Mark the series' waiting findings of *kind* ``gone`` except the picks in *keep* (what this pass found again).
        Returns how many were retired."""
        keep = set(keep)
        with self.store.connect() as con:
            rows = con.execute("SELECT id, pick_key FROM findings WHERE series_id = ? AND kind = ? AND status = 'waiting'",
                               (series_id, kind)).fetchall()
            gone = [r["id"] for r in rows if r["pick_key"] not in keep]
            for chunk in _chunks(gone):
                con.execute(f"UPDATE findings SET status = 'gone', updated_at = ? WHERE id IN ({','.join('?' * len(chunk))})",
                            (utcnow(), *chunk))
        return len(gone)

    # --- runs ----------------------------------------------------------------------------------------

    def start_run(self, trigger: str) -> int:
        if trigger not in FIND_TRIGGERS:
            raise ValueError(f"not a find trigger: {trigger!r}")
        with self.store.connect() as con:
            return int(con.execute("INSERT INTO find_runs (trigger, started_at) VALUES (?, ?)",
                                   (trigger, utcnow())).lastrowid)

    def finish_run(self, run_id: int, *, series_searched: int, found: int, sent: int, errors: int, summary: str) -> FindRun:
        with self.store.connect() as con:
            con.execute("UPDATE find_runs SET finished_at = ?, series_searched = ?, found = ?, sent = ?, errors = ?,"
                        " summary = ? WHERE id = ?", (utcnow(), series_searched, found, sent, errors, summary, run_id))
            return _run(con.execute("SELECT * FROM find_runs WHERE id = ?", (run_id,)).fetchone())

    def last_run(self) -> Optional[FindRun]:
        with self.store.connect() as con:
            row = con.execute("SELECT * FROM find_runs ORDER BY id DESC LIMIT 1").fetchone()
        return _run(row) if row is not None else None


def _chunks(ids: Sequence[int], size: int = 500) -> Iterable[List[int]]:
    ids = list(ids)
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


__all__ = ["FindingStore", "Finding", "NewFinding", "FindRun", "FindingConflict", "SETTING_DEFAULT_MODE",
           "WAITING", "SENT", "SKIPPED", "GONE", "MODE_OFF", "MODE_NOTIFY", "MODE_AUTO"]
