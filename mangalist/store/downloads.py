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
- **More than one tool** (the Suwayomi MVP, 2026-10-10; no migration): a ledger is bound to one ``tool`` -
  ``DownloadLedger(store)`` is qBittorrent's, exactly as before (every query filters on ``tool = 'qbittorrent'``);
  ``DownloadLedger(store, tool='suwayomi')`` holds the chapter downloads; ``DownloadLedger(store, tool=None)`` reads
  every tool's records (the GUI's In progress list) and refuses to create one. A chapter record is ONE chapter:
  ``external_ref`` = Suwayomi's chapter id, ``request`` = JSON (the chapter's number, name, scanlator, the source's
  id and name, the manga's id and title, the batch it was sent in), the same statuses (SENT = in Suwayomi's queue,
  DOWNLOADED = Suwayomi has the CBZ, FILED = in the library, REMOVED = Suwayomi's copy deleted). Chapter downloads
  are outside the download budget (:mod:`mangalist.downloads.budget`: torrents only).
- **Suwayomi's connection** (:class:`SuwayomiSettings`): address, optional basic-auth login and the folder MangaList
  reads Suwayomi's downloads from, in the ``meta`` table (key ``suwayomi.connection``) - not in ``settings``, which
  ``config.load()`` / ``config.save()`` copy around - and the per-series choices (the matched Suwayomi manga, the
  scanlation group; ``suwayomi.series.<id>``). The password is never logged and never in a ``repr``.

:class:`DownloadLedger` wraps a :class:`~mangalist.store.Store` (it uses ``store.connect()`` and the
settings), so the store class itself is unchanged. It implements
:class:`~mangalist.downloads.contracts.DownloadStore`. No Qt here.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..downloads.budget import SIZE_RELEASE
from ..downloads.contracts import (
    TOOL_QBITTORRENT,
    TOOL_SUWAYOMI,
    TOOLS,
    DownloadRecord,
    DownloadStatus,
    NyaaCandidate,
    QbtConnection,
    SuwayomiChapter,
    SuwayomiConnection,
    SuwayomiManga,
    SuwayomiSource,
)
from .db import utcnow
from .units import exact_number

#: The tool of the volumes MVP's records (kept: other modules import it).
TOOL = TOOL_QBITTORRENT

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
    tool = r["tool"] or TOOL_QBITTORRENT
    if tool != TOOL_QBITTORRENT:            # a chapter download: never counted against the budget, no size
        return DownloadRecord(id=r["id"], series_id=r["series_id"], info_hash=r["external_ref"] or "",
                              title=str(req.get("title") or ""), wanted_volumes=(), target_dir=r["destination"] or "",
                              status=r["status"], created_at=r["created_at"], updated_at=r["updated_at"],
                              filed_files=tuple(str(f) for f in _loads(r["filed_files"], [])),
                              copied=bool(r["copied"]), error=r["error"], tool=tool,
                              wanted_chapters=tuple(str(c) for c in req.get("wanted_chapters") or ()),
                              batch=str(req.get("batch") or ""), source=str(req.get("source_name") or ""),
                              group=str(req.get("scanlator") or ""))
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
    """Download records, the qBittorrent connection and the download settings of *store*. *tool*: whose records this
    ledger sees (default qBittorrent's, as before; None: every tool's, read only)."""

    def __init__(self, store, tool: Optional[str] = TOOL_QBITTORRENT):
        if tool is not None and tool not in TOOLS:
            raise DownloadError(f"unknown download tool {tool!r}")
        self.store = store
        self.tool = tool

    def for_tool(self, tool: Optional[str]) -> "DownloadLedger":
        """The same store's ledger for another tool (None: every tool)."""
        return DownloadLedger(self.store, tool)

    def _where(self) -> str:
        """The tool condition every query carries (``1 = 1`` for a ledger of every tool), with :meth:`_args`."""
        return "tool = ?" if self.tool is not None else "1 = 1"

    def _args(self) -> tuple:
        return (self.tool,) if self.tool is not None else ()

    def _writable(self, tool: str) -> None:
        if self.tool != tool:
            raise DownloadError(f"this ledger is {self.tool or 'every tool'}'s; it does not record {tool} downloads")

    def connect(self, durable: bool = False):
        return self.store.connect(durable=durable)

    # --- records (contracts.DownloadStore) ----------------------------------------------------------

    def create(self, series_id: int, candidate: NyaaCandidate, wanted_volumes: Sequence[str],
               target_dir: str, *, status: str = DownloadStatus.SENT, only_missing: bool = False,
               size_bytes: Optional[int] = None, size_source: Optional[str] = None) -> DownloadRecord:
        """A new SENT record (or QUEUED: waiting for room under the download budget, at the end of the queue). Refused
        while another record tracks the same torrent (queued or in qBittorrent). *size_bytes* / *size_source*: what it
        counts against the budget (default: the release's size)."""
        self._writable(TOOL_QBITTORRENT)
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

    def create_chapters(self, series_id: int, chapters: Sequence[SuwayomiChapter], *, source: SuwayomiSource,
                        manga: SuwayomiManga, target_dir: str, batch: str) -> Tuple[List[DownloadRecord], List[str]]:
        """New SENT chapter records (a Suwayomi ledger only), one per chapter, in one transaction - written BEFORE the
        chapters are enqueued in Suwayomi (the caller marks them FAILED when the enqueue fails). A chapter already in
        hand - the same Suwayomi chapter, or the same chapter number of this series, sent and not failed or cancelled -
        is skipped. Returns (records made, why each skipped chapter was skipped)."""
        self._writable(TOOL_SUWAYOMI)
        if not target_dir:
            raise DownloadError("a chapter download needs its target folder")
        if not batch:
            raise DownloadError("a chapter download needs its batch")
        now = utcnow()
        made: List[int] = []
        skipped: List[str] = []
        with self.connect(durable=True) as con:
            con.execute("BEGIN IMMEDIATE")          # the in-hand check and the inserts as one step
            held = self._chapters_in_hand(con, series_id)
            refs = {r[0] for r in con.execute(
                f"SELECT external_ref FROM ledger WHERE tool = ? AND status IN ({','.join('?' * len(TRACKED))})",
                (TOOL_SUWAYOMI, *TRACKED))}
            for ch in chapters:
                number = exact_number(ch.number) if ch.number and not ch.number.startswith("-") else None
                if number is None:
                    skipped.append(f"{ch.name}: no chapter number")
                    continue
                if str(ch.id) in refs or number in held:
                    skipped.append(f"ch {number}: already sent")
                    continue
                request = {"title": ch.name, "wanted_chapters": [number], "chapter_id": int(ch.id),
                           "chapter_name": ch.name, "scanlator": ch.scanlator or "", "chapter_url": ch.real_url or ch.url,
                           "upload_date": ch.upload_date, "source_id": str(source.id),
                           "source_name": source.display_name, "manga_id": int(manga.id), "manga_title": manga.title,
                           "batch": batch}
                cur = con.execute(
                    "INSERT INTO ledger (created_at, updated_at, series_id, request, tool, destination, status,"
                    " external_ref) VALUES (?,?,?,?,?,?,?,?)",
                    (now, now, int(series_id), json.dumps(request, ensure_ascii=False), TOOL_SUWAYOMI,
                     str(target_dir), DownloadStatus.SENT, str(int(ch.id))))
                made.append(int(cur.lastrowid))
                held.add(number)
                refs.add(str(ch.id))
        return [self.get(i) for i in made], skipped  # type: ignore[misc]

    def chapters_in_hand(self, series_id: int) -> Set[str]:
        """The chapter numbers of *series_id* that a chapter download has in hand (sent, downloaded, filed - also
        removed: filed, Suwayomi's copy gone), exact strings: never sent again."""
        with self.connect() as con:
            return self._chapters_in_hand(con, series_id)

    @staticmethod
    def _chapters_in_hand(con, series_id: int) -> Set[str]:
        keep = (*TRACKED, DownloadStatus.REMOVED)
        out: Set[str] = set()
        for r in con.execute(f"SELECT request FROM ledger WHERE tool = ? AND series_id = ? AND status IN "
                             f"({','.join('?' * len(keep))})", (TOOL_SUWAYOMI, int(series_id), *keep)):
            out.update(str(c) for c in _loads(r["request"], {}).get("wanted_chapters") or ())
        return out

    def get(self, record_id: int) -> Optional[DownloadRecord]:
        with self.connect() as con:
            r = con.execute(f"SELECT * FROM ledger WHERE id = ? AND {self._where()}",
                            (record_id, *self._args())).fetchone()
            return self._records(con, [r])[0] if r is not None else None

    def for_series(self, series_id: int) -> Sequence[DownloadRecord]:
        with self.connect() as con:
            rows = con.execute(f"SELECT * FROM ledger WHERE {self._where()} AND series_id = ? ORDER BY id",
                               (*self._args(), series_id)).fetchall()
            return self._records(con, rows)

    def all_records(self) -> Sequence[DownloadRecord]:
        """Every download record, oldest first (the GUI's Downloads list)."""
        with self.connect() as con:
            rows = con.execute(f"SELECT * FROM ledger WHERE {self._where()} ORDER BY id", self._args()).fetchall()
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
        rows = con.execute(f"SELECT * FROM ledger WHERE {self._where()} AND status = ?",
                           (*self._args(), DownloadStatus.QUEUED)).fetchall()
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
        r = con.execute(f"SELECT request FROM ledger WHERE id = ? AND {self._where()}",
                        (record_id, *self._args())).fetchone()
        if r is None:
            return False
        req = _loads(r["request"], {})
        new = {**req, **changes}
        if new == req:
            return False
        con.execute(f"UPDATE ledger SET request = ? WHERE id = ? AND {self._where()}",
                    (json.dumps(new, ensure_ascii=False), record_id, *self._args()))
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
            rows = con.execute(f"SELECT * FROM ledger WHERE {self._where()} AND status IN ({','.join('?' * len(ACTIVE))})"
                               " ORDER BY id", (*self._args(), *ACTIVE)).fetchall()
        return [_record(r) for r in rows]

    def active_for_hash(self, info_hash: str) -> Optional[DownloadRecord]:
        """The record that has this torrent in hand - queued, or in qBittorrent (None: it may be sent)."""
        with self.connect() as con:
            ids = self._tracked_ids(con, info_hash.strip().lower())
        return self.get(ids[0]) if ids else None

    def _tracked_ids(self, con, info_hash: str) -> List[int]:
        return [r[0] for r in con.execute(
            f"SELECT id FROM ledger WHERE {self._where()} AND external_ref = ? AND status IN "
            f"({','.join('?' * len(TRACKED))}) ORDER BY id", (*self._args(), info_hash, *TRACKED))]

    # --- record details the contract does not carry -----------------------------------------------

    def request(self, record_id: int) -> Dict[str, Any]:
        """The record's request JSON (wanted volumes, the release's title / urls / parsed volumes)."""
        with self.connect() as con:
            r = con.execute(f"SELECT request FROM ledger WHERE id = ? AND {self._where()}",
                            (record_id, *self._args())).fetchone()
        return _loads(r["request"], {}) if r is not None else {}

    def plan_id(self, record_id: int) -> Optional[int]:
        with self.connect() as con:
            r = con.execute(f"SELECT plan_id FROM ledger WHERE id = ? AND {self._where()}",
                            (record_id, *self._args())).fetchone()
        return None if r is None or r["plan_id"] is None else int(r["plan_id"])

    def attach_plan(self, record_id: int, plan_id: int) -> None:
        """Remember the journal plan that files *record_id* (once; written ahead of applying the plan)."""
        with self.connect(durable=True) as con:
            cur = con.execute(f"UPDATE ledger SET plan_id = ?, updated_at = ? WHERE id = ? AND {self._where()}"
                              " AND plan_id IS NULL AND status = ?",
                              (int(plan_id), utcnow(), record_id, *self._args(), DownloadStatus.DOWNLOADED))
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
            cur = con.execute(f"UPDATE ledger SET {', '.join(sets)} WHERE id = ? AND {self._where()}"
                              f" AND status IN ({','.join('?' * len(expect))})",
                              (*args, record_id, *self._args(), *expect))
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


# --- Suwayomi's connection and the per-series choices (the Suwayomi MVP) --------------------------------------

META_SUWAYOMI_CONNECTION = "suwayomi.connection"
META_SUWAYOMI_SERIES = "suwayomi.series."          # + the series id
SETTING_SUWAYOMI_SOURCES = "downloads.suwayomi_sources"     # the source ids MangaList may use, in order


class SuwayomiSettings:
    """Suwayomi's connection (``meta``: the password stays out of ``settings``), the sources MangaList may use (a plain
    setting, like nyaa's switches) and each series' choices (``meta``): the Suwayomi manga it was matched to - by its
    MangaDex id, or by a title search the owner confirmed - and the scanlation group the owner picked."""

    def __init__(self, store):
        self.store = store

    def connection(self) -> Optional[SuwayomiConnection]:
        """The stored connection (with its password, for the client only), or None when none is set up."""
        data = _loads(self.store.get_meta(META_SUWAYOMI_CONNECTION), {})
        url = str(data.get("base_url") or "").strip()
        if not url:
            return None
        return SuwayomiConnection(base_url=url, username=str(data.get("username") or ""),
                                  password=str(data.get("password") or ""),
                                  download_dir=str(data.get("download_dir") or "").strip())

    def save_connection(self, conn: SuwayomiConnection) -> None:
        self.store.set_meta(META_SUWAYOMI_CONNECTION, json.dumps(
            {"base_url": (conn.base_url or "").strip(), "username": conn.username or "",
             "password": conn.password or "", "download_dir": (conn.download_dir or "").strip(),
             "updated_at": utcnow()}, ensure_ascii=False))

    def forget_connection(self) -> None:
        self.store.set_meta(META_SUWAYOMI_CONNECTION, None)

    # --- sources ---------------------------------------------------------------------------------------------

    def sources(self) -> Optional[List[str]]:
        """The source ids MangaList may use, in the order it tries them; None: never chosen (MangaList then uses
        MangaDex alone - the owner's first source)."""
        value = self.store.get_setting(SETTING_SUWAYOMI_SOURCES, None)
        if not isinstance(value, list):
            return None
        return [str(v) for v in value if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip()]

    def set_sources(self, source_ids: Sequence[str]) -> None:
        out: List[str] = []
        for v in source_ids:
            text = str(v).strip()
            if text and text not in out:
                out.append(text)
        self.store.set_setting(SETTING_SUWAYOMI_SOURCES, out)

    # --- per series ------------------------------------------------------------------------------------------

    def series_choice(self, series_id: int) -> Dict[str, Any]:
        """``{"source_id", "manga_id", "manga_title", "how", "group"}`` as far as known ({} when nothing is stored).
        ``how``: ``mangadex-id`` (found by the MangaDex id MangaPixer links) or ``confirmed`` (a title match the owner
        confirmed)."""
        return _loads(self.store.get_meta(f"{META_SUWAYOMI_SERIES}{int(series_id)}"), {})

    def set_series_choice(self, series_id: int, **changes: Any) -> Dict[str, Any]:
        """Merge *changes* into the series' choices (a value of None forgets that key); the stored result."""
        data = self.series_choice(series_id)
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        self.store.set_meta(f"{META_SUWAYOMI_SERIES}{int(series_id)}",
                            json.dumps(data, ensure_ascii=False) if data else None)
        return data
