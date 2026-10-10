"""The volumes MVP's download records, qBittorrent connection and download settings (schema migration 5).

- Download records are rows of the ``ledger`` table (created in schema 1 for exactly this, unused until now):
  ``tool`` = ``'qbittorrent'``, ``external_ref`` = the torrent's info hash (lowercase hex), ``destination`` =
  the target folder (absolute: downloads run only in the Unraid container, which sees one path per folder),
  ``request`` = JSON (the wanted volumes and the release's title / urls / parsed volumes), ``status`` =
  :class:`~mangalist.downloads.contracts.DownloadStatus`. Schema 5 adds ``filed_files``, ``copied`` and
  ``plan_id`` (the journal plan that filed the release). Status changes are compare-and-set (``expect``), so
  two arrivals passes can never move one record twice.
- ``qbittorrent_connection`` (id = 1): URL, user name, password, TLS choice. The password is kept here and
  not in ``settings`` (``config.load()`` / ``config.save()`` copy every settings key around), as
  ``mangapixer_connection.token`` is; it is never logged and never in a ``repr``
  (:class:`~mangalist.downloads.contracts.QbtConnection` hides it).
- Settings (plain ``settings`` keys): ``downloads.save_path`` (where qBittorrent saves the ``mangalist``
  category) and ``downloads.remove_completed`` (default on).
- **The download budget** (2026-10-09, no migration): a QUEUED record (the ledger's own schema-1 ``'queued'``) is a send
  waiting for room under the cap. Everything a later hand-over needs lives in its ``request`` JSON, written at queueing
  time: the release (title, ``.torrent`` URL, page, parsed volumes, size, ...), the wanted volumes, ``only_missing``
  (the partial-or-whole choice) and ``queue_order`` (the queue sorts by it, then by id: oldest first; "move to the
  front" gives a record an order below every other). The target folder is ``destination``, as for any record. Every
  record also carries what it counts against the cap: ``budget_bytes`` / ``budget_source`` (the release's size, the
  selected files' total of a partial send, then qBittorrent's own figure once it reports one) and, for a FAILED record,
  ``in_client`` (False once the torrent is seen gone from qBittorrent). Older records without these keys count their
  ``size_bytes`` (the release's size).

:class:`DownloadLedger` wraps a :class:`~mangalist.store.Store` (it uses ``store.connect()`` and the
settings), so the store class itself is unchanged. It implements
:class:`~mangalist.downloads.contracts.DownloadStore`. No Qt here.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

from ..downloads.budget import SIZE_RELEASE
from ..downloads.contracts import DownloadRecord, DownloadStatus, NyaaCandidate, QbtConnection
from .db import utcnow
from .units import exact_number

TOOL = "qbittorrent"

SETTING_SAVE_PATH = "downloads.save_path"
SETTING_REMOVE_COMPLETED = "downloads.remove_completed"
DEFAULT_SAVE_PATH = "/data/appdata/torrents/mangalist"
DEFAULT_REMOVE_COMPLETED = True

#: Statuses an arrivals pass still works on.
ACTIVE = (DownloadStatus.SENT, DownloadStatus.DOWNLOADED, DownloadStatus.FILED)
#: ... and every status that means "MangaList has this release in hand": the same torrent is never queued or sent twice.
TRACKED = (DownloadStatus.QUEUED, *ACTIVE)


class DownloadError(ValueError):
    pass


class StatusConflict(DownloadError):
    """The record was not in the expected status (another pass moved it, or a wrong transition)."""


def _loads(text: Optional[str], default):
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _size(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _record(r: sqlite3.Row) -> DownloadRecord:
    req = _loads(r["request"], {})
    size = _size(req.get("budget_bytes")) or _size(req.get("size_bytes"))
    source = str(req.get("budget_source") or "") if _size(req.get("budget_bytes")) else (SIZE_RELEASE if size else "")
    return DownloadRecord(id=r["id"], series_id=r["series_id"], info_hash=r["external_ref"] or "",
                          title=str(req.get("title") or ""),
                          wanted_volumes=tuple(str(v) for v in req.get("wanted_volumes") or ()),
                          target_dir=r["destination"] or "", status=r["status"], created_at=r["created_at"],
                          updated_at=r["updated_at"],
                          filed_files=tuple(str(f) for f in _loads(r["filed_files"], [])),
                          copied=bool(r["copied"]), error=r["error"], size_bytes=size, size_source=source,
                          in_client=req.get("in_client") is not False)


def _queue_key(r: sqlite3.Row):
    order = _loads(r["request"], {}).get("queue_order")
    order = order if isinstance(order, (int, float)) and not isinstance(order, bool) else r["id"]
    return (order, r["id"])


def normalize_volumes(volumes: Sequence[str]) -> List[str]:
    """Exact decimal strings (``'03'`` -> ``'3'``), in the given order, without duplicates; floats refused."""
    out: List[str] = []
    for v in volumes:
        n = exact_number(v)
        if n is not None and n not in out:
            out.append(n)
    return out


class DownloadLedger:
    """Download records, the qBittorrent connection and the download settings of *store*."""

    def __init__(self, store):
        self.store = store

    def connect(self, durable: bool = False):
        return self.store.connect(durable=durable)

    # --- records (contracts.DownloadStore) ----------------------------------------------------------

    def create(self, series_id: int, candidate: NyaaCandidate, wanted_volumes: Sequence[str],
               target_dir: str, *, status: str = DownloadStatus.SENT, only_missing: bool = False,
               size_bytes: Optional[int] = None, size_source: Optional[str] = None) -> DownloadRecord:
        """A new SENT record (or QUEUED: waiting for room under the download budget, at the end of the queue). Refused
        while another record tracks the same torrent (queued or in qBittorrent). *size_bytes* / *size_source*: what it
        counts against the budget (default: the release's size)."""
        info_hash = (candidate.info_hash or "").strip().lower()
        if not info_hash:
            raise DownloadError("a download needs the torrent's info hash")
        wanted = normalize_volumes(wanted_volumes)
        if not wanted:
            raise DownloadError("a download needs at least one wanted volume")
        if not target_dir:
            raise DownloadError("a download needs its target folder")
        if status not in (DownloadStatus.SENT, DownloadStatus.QUEUED):
            raise DownloadError(f"a new download is sent or queued, not {status!r}")
        # Everything a queued record needs to be sent later, also kept for a sent one (the same shape for both).
        request = {"title": candidate.title, "wanted_volumes": wanted, "torrent_url": candidate.torrent_url,
                   "view_url": candidate.view_url, "vol_from": candidate.vol_from, "vol_to": candidate.vol_to,
                   "is_pack": candidate.is_pack, "size_bytes": candidate.size_bytes,
                   "published": candidate.published, "category": candidate.category, "seeders": candidate.seeders,
                   "trusted": candidate.trusted, "digital": candidate.digital, "group": candidate.group,
                   "covers_missing": list(candidate.covers_missing), "only_missing": bool(only_missing),
                   "budget_bytes": _size(size_bytes) or _size(candidate.size_bytes),
                   "budget_source": (size_source or SIZE_RELEASE) if _size(size_bytes) else SIZE_RELEASE}
        now = utcnow()
        with self.connect(durable=True) as con:
            if self._tracked_ids(con, info_hash):
                raise DownloadError(f"the torrent {info_hash} is already being tracked")
            if status == DownloadStatus.QUEUED:     # at the end of the queue, whatever was moved to its front
                orders = [_queue_key(r)[0] for r in self._queued_rows(con)]
                request["queue_order"] = max(orders) + 1 if orders else 0
            cur = con.execute(
                "INSERT INTO ledger (created_at, updated_at, series_id, request, tool, destination, status,"
                " external_ref) VALUES (?,?,?,?,?,?,?,?)",
                (now, now, int(series_id), json.dumps(request, ensure_ascii=False), TOOL, str(target_dir),
                 status, info_hash))
            record_id = int(cur.lastrowid)
        return self.get(record_id)  # type: ignore[return-value]

    def get(self, record_id: int) -> Optional[DownloadRecord]:
        with self.connect() as con:
            r = con.execute("SELECT * FROM ledger WHERE id = ? AND tool = ?", (record_id, TOOL)).fetchone()
            return self._records(con, [r])[0] if r is not None else None

    def for_series(self, series_id: int) -> Sequence[DownloadRecord]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM ledger WHERE tool = ? AND series_id = ? ORDER BY id",
                               (TOOL, series_id)).fetchall()
            return self._records(con, rows)

    def all_records(self) -> Sequence[DownloadRecord]:
        """Every download record, oldest first (the GUI's Downloads list)."""
        with self.connect() as con:
            rows = con.execute("SELECT * FROM ledger WHERE tool = ? ORDER BY id", (TOOL,)).fetchall()
            return self._records(con, rows)

    def _records(self, con, rows) -> List[DownloadRecord]:
        """The rows as records, a QUEUED one with its place in the queue (read in the same transaction)."""
        out = [_record(r) for r in rows]
        if not any(rec.status == DownloadStatus.QUEUED for rec in out):
            return out
        places = {r["id"]: n for n, r in enumerate(self._queued_rows(con), start=1)}
        return [replace(rec, queue_position=places.get(rec.id, 0)) if rec.status == DownloadStatus.QUEUED else rec
                for rec in out]

    # --- the queue (the download budget) ---------------------------------------------------------------

    def _queued_rows(self, con) -> List[sqlite3.Row]:
        rows = con.execute("SELECT * FROM ledger WHERE tool = ? AND status = ?",
                           (TOOL, DownloadStatus.QUEUED)).fetchall()
        return sorted(rows, key=_queue_key)

    def queued(self) -> List[DownloadRecord]:
        """The queue, in the order it is handed over (``queue_position`` 1, 2, ...)."""
        with self.connect() as con:
            return [replace(_record(r), queue_position=n) for n, r in enumerate(self._queued_rows(con), start=1)]

    def move_to_front(self, record_id: int) -> DownloadRecord:
        """Put a QUEUED record first in the queue (the owner's override). :class:`StatusConflict` when it is not
        queued (any more)."""
        with self.connect(durable=True) as con:
            con.execute("BEGIN IMMEDIATE")          # read the queue and write the new order as one step
            rows = self._queued_rows(con)
            if not any(r["id"] == record_id for r in rows):
                raise StatusConflict(f"download {record_id} is not queued")
            first = _queue_key(rows[0])[0]
            if rows[0]["id"] != record_id:
                self._set_request(con, record_id, queue_order=first - 1)
        return self.get(record_id)  # type: ignore[return-value]

    def _set_request(self, con, record_id: int, **changes: Any) -> bool:
        """Merge *changes* into a record's request JSON (inside the caller's transaction). True when it changed."""
        r = con.execute("SELECT request FROM ledger WHERE id = ? AND tool = ?", (record_id, TOOL)).fetchone()
        if r is None:
            return False
        req = _loads(r["request"], {})
        new = {**req, **changes}
        if new == req:
            return False
        con.execute("UPDATE ledger SET request = ? WHERE id = ? AND tool = ?",
                    (json.dumps(new, ensure_ascii=False), record_id, TOOL))
        return True

    def note_size(self, record_id: int, size_bytes: int, source: str) -> bool:
        """Remember what a record counts against the budget (e.g. qBittorrent's own figure, once it reports one).
        True when it changed. The record's ``updated_at`` is left alone: nothing happened to the download itself."""
        if _size(size_bytes) == 0:
            return False
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            return self._set_request(con, record_id, budget_bytes=int(size_bytes), budget_source=source)

    def note_in_client(self, record_id: int, in_client: bool) -> bool:
        """Remember whether a (FAILED) record's torrent is still in the client - it counts against the budget only
        while it is. True when it changed."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            return self._set_request(con, record_id, in_client=bool(in_client))

    def active(self) -> Sequence[DownloadRecord]:
        """Every record not yet REMOVED / FAILED / CANCELLED, oldest first."""
        with self.connect() as con:
            rows = con.execute(f"SELECT * FROM ledger WHERE tool = ? AND status IN ({','.join('?' * len(ACTIVE))})"
                               " ORDER BY id", (TOOL, *ACTIVE)).fetchall()
        return [_record(r) for r in rows]

    def active_for_hash(self, info_hash: str) -> Optional[DownloadRecord]:
        """The record that has this torrent in hand - queued, or in qBittorrent (None: it may be sent)."""
        with self.connect() as con:
            ids = self._tracked_ids(con, info_hash.strip().lower())
        return self.get(ids[0]) if ids else None

    def _tracked_ids(self, con, info_hash: str) -> List[int]:
        return [r[0] for r in con.execute(
            f"SELECT id FROM ledger WHERE tool = ? AND external_ref = ? AND status IN ({','.join('?' * len(TRACKED))})"
            " ORDER BY id", (TOOL, info_hash, *TRACKED))]

    # --- record details the contract does not carry -----------------------------------------------

    def request(self, record_id: int) -> Dict[str, Any]:
        """The record's request JSON (wanted volumes, the release's title / urls / parsed volumes)."""
        with self.connect() as con:
            r = con.execute("SELECT request FROM ledger WHERE id = ? AND tool = ?", (record_id, TOOL)).fetchone()
        return _loads(r["request"], {}) if r is not None else {}

    def plan_id(self, record_id: int) -> Optional[int]:
        with self.connect() as con:
            r = con.execute("SELECT plan_id FROM ledger WHERE id = ? AND tool = ?", (record_id, TOOL)).fetchone()
        return None if r is None or r["plan_id"] is None else int(r["plan_id"])

    def attach_plan(self, record_id: int, plan_id: int) -> None:
        """Remember the journal plan that files *record_id* (once; written ahead of applying the plan)."""
        with self.connect(durable=True) as con:
            cur = con.execute("UPDATE ledger SET plan_id = ?, updated_at = ? WHERE id = ? AND tool = ?"
                              " AND plan_id IS NULL AND status = ?",
                              (int(plan_id), utcnow(), record_id, TOOL, DownloadStatus.DOWNLOADED))
        if cur.rowcount != 1:
            raise StatusConflict(f"download {record_id} is not a DOWNLOADED record without a plan")

    # --- transitions -------------------------------------------------------------------------------

    _KEEP = object()

    def set_status(self, record_id: int, status: str, *, expect: Sequence[str], error: Any = _KEEP,
                   filed_files: Optional[Sequence[str]] = None, copied: Optional[bool] = None) -> DownloadRecord:
        """Move *record_id* to *status*, only from one of *expect* (else :class:`StatusConflict`)."""
        if status not in DownloadStatus.ALL:
            raise DownloadError(f"unknown download status {status!r}")
        sets, args = ["status = ?", "updated_at = ?"], [status, utcnow()]
        if error is not self._KEEP:
            sets.append("error = ?")
            args.append(error)
        if filed_files is not None:
            sets.append("filed_files = ?")
            args.append(json.dumps(list(filed_files), ensure_ascii=False))
        if copied is not None:
            sets.append("copied = ?")
            args.append(1 if copied else 0)
        expect = tuple(expect)
        with self.connect(durable=True) as con:
            cur = con.execute(f"UPDATE ledger SET {', '.join(sets)} WHERE id = ? AND tool = ?"
                              f" AND status IN ({','.join('?' * len(expect))})", (*args, record_id, TOOL, *expect))
        if cur.rowcount != 1:
            now = self.get(record_id)
            raise StatusConflict(f"download {record_id} is {now.status if now else 'gone'}, not "
                                 f"{' / '.join(expect)}; not moved to {status}")
        return self.get(record_id)  # type: ignore[return-value]

    def cancel(self, record_id: int) -> DownloadRecord:
        """Stop tracking a release that is not filed yet (the torrent itself is left alone). A QUEUED one is simply
        taken out of the queue: nothing was sent."""
        return self.set_status(record_id, DownloadStatus.CANCELLED,
                               expect=(DownloadStatus.QUEUED, DownloadStatus.SENT, DownloadStatus.DOWNLOADED),
                               error="cancelled by the owner")

    # --- the qBittorrent connection ------------------------------------------------------------------

    def connection(self) -> Optional[QbtConnection]:
        """The stored connection (with its password, for the client only), or None when none is set up."""
        with self.connect() as con:
            r = con.execute("SELECT * FROM qbittorrent_connection WHERE id = 1").fetchone()
        if r is None or not (r["base_url"] or "").strip():
            return None
        return QbtConnection(base_url=r["base_url"], username=r["username"] or "", password=r["password"] or "",
                             verify_tls=bool(r["verify_tls"]))

    def save_connection(self, conn: QbtConnection) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO qbittorrent_connection (id, base_url, username, password, verify_tls, updated_at)"
                " VALUES (1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url,"
                " username=excluded.username, password=excluded.password, verify_tls=excluded.verify_tls,"
                " updated_at=excluded.updated_at",
                ((conn.base_url or "").strip(), conn.username or "", conn.password or "",
                 1 if conn.verify_tls else 0, utcnow()))

    def forget_connection(self) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM qbittorrent_connection WHERE id = 1")

    # --- settings ----------------------------------------------------------------------------------

    def save_path(self) -> str:
        value = self.store.get_setting(SETTING_SAVE_PATH, None)
        return str(value).strip() if isinstance(value, str) and value.strip() else DEFAULT_SAVE_PATH

    def set_save_path(self, path: Optional[str]) -> None:
        self.store.set_setting(SETTING_SAVE_PATH, (path or "").strip() or DEFAULT_SAVE_PATH)

    def remove_completed(self) -> bool:
        value = self.store.get_setting(SETTING_REMOVE_COMPLETED, None)
        return value if isinstance(value, bool) else DEFAULT_REMOVE_COMPLETED

    def set_remove_completed(self, on: bool) -> None:
        self.store.set_setting(SETTING_REMOVE_COMPLETED, bool(on))
