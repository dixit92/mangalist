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
- **Two chapter programs** (the transition fixes, 2026-10-24, schema 10): Suwayomi and MangaList's built-in extension
  host both number their chapters from 1, so a chapter record says which program it belongs to (column ``backend``:
  ``suwayomi`` | ``extensions``; NULL for a torrent, or a record an older build wrote - read as either program). The
  "already sent" checks compare chapter ids within one program only (chapter numbers across both), each record is
  finished with its own program, and a stuck chapter is marked by state (``request.stuck``), not by its note's words.
- **Clearing finished records** (2026-10-15, owner: "Setting, user clearable, scheduled task to clear [Daily, Weekly,
  Monthly]"; :meth:`DownloadLedger.clear_finished`). Only the records the Download tab calls finished - REMOVED
  (the torrent left at its seed goal / Suwayomi's copy deleted) and CANCELLED - and only those not touched for the
  chosen period. Never cleared: a record that is still in progress (queued, sent, downloaded), a FILED one (its
  torrent still seeds and counts against the download budget; Remove Completed turns it REMOVED first), a FAILED one
  (it wants a look, and may still count against the budget), the newest record (SQLite reuses the largest id once its
  row is gone; ``replacements.download_id`` and ``upgrades.first_download`` go by id), a REMOVED torrent record the
  replaced-chapters pass has not looked at yet (chapter records are never looked at), and a record whose replaced-chapters batch still waits for the owner
  (pending / held). What a cleared record used to tell other features is kept where it matters: a cleared torrent's
  info hash stays in ``meta`` (``downloads.cleared_releases``), so the find pass still never picks the same release
  twice. Findings keep their ``record_id`` (a plain number, never joined) and a settled replaced-chapters batch keeps
  its ``download_id``; the series' "Filed v03 - done" chip reads the newest record, so it goes when that record does.
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
import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..downloads.budget import SIZE_RELEASE
from ..downloads.contracts import (
    CHAPTER_BACKEND_EXTENSIONS,
    CHAPTER_BACKEND_SUWAYOMI,
    CHAPTER_BACKENDS,
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


#: The records the Download tab's "Show finished" lists (``gui.download_rules.FINISHED``) - the only ones that may be cleared.
FINISHED = (DownloadStatus.REMOVED, DownloadStatus.CANCELLED)
#: Replaced-chapters batch states that still point at their download (the owner has something to confirm or restore).
HOLDING_BATCH = ("pending", "held")
META_CLEARED_RELEASES = "downloads.cleared_releases"


@dataclass
class ClearReport:
    """What :meth:`DownloadLedger.clear_finished` did (or, for a dry run, would do), and what it kept and why."""

    finished: int = 0                   # finished records looked at
    cleared: int = 0
    kept_recent: int = 0                # changed within the period
    kept_newest: int = 0                # the newest record of the ledger (its id must not be reused)
    kept_unexamined: int = 0            # removed, but the replaced-chapters pass has not looked at it yet
    kept_waiting_chapters: int = 0      # a replaced-chapters batch of it waits for the owner

    @property
    def kept(self) -> int:
        return self.finished - self.cleared

    def summary(self) -> str:
        if not self.finished:
            return "no finished records"
        out = f"cleared {self.cleared} of {self.finished} finished records"
        why = [f"{n} {text}" for n, text in ((self.kept_recent, "newer than the period"),
                                             (self.kept_newest + self.kept_unexamined + self.kept_waiting_chapters,
                                              "still needed")) if n]
        return out + (f" (kept {', '.join(why)})" if why else "")


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
        backend = r["backend"] if "backend" in r.keys() else None
        return DownloadRecord(id=r["id"], series_id=r["series_id"], info_hash=r["external_ref"] or "",
                              title=str(req.get("title") or ""), wanted_volumes=(), target_dir=r["destination"] or "",
                              status=r["status"], created_at=r["created_at"], updated_at=r["updated_at"],
                              filed_files=tuple(str(f) for f in _loads(r["filed_files"], [])),
                              copied=bool(r["copied"]), error=r["error"], tool=tool,
                              wanted_chapters=tuple(str(c) for c in req.get("wanted_chapters") or ()),
                              batch=str(req.get("batch") or ""), source=str(req.get("source_name") or ""),
                              group=str(req.get("scanlator") or ""),
                              backend=backend if backend in CHAPTER_BACKENDS else "",
                              stuck=req.get("stuck") is True and r["status"] == DownloadStatus.SENT)
    size = _size(req.get("budget_bytes")) or _size(req.get("size_bytes"))
    source = str(req.get("budget_source") or "") if _size(req.get("budget_bytes")) else (SIZE_RELEASE if size else "")
    return DownloadRecord(id=r["id"], series_id=r["series_id"], info_hash=r["external_ref"] or "",
                          title=str(req.get("title") or ""),
                          wanted_volumes=tuple(str(v) for v in req.get("wanted_volumes") or ()),
                          target_dir=r["destination"] or "", status=r["status"], created_at=r["created_at"],
                          updated_at=r["updated_at"],
                          filed_files=tuple(str(f) for f in _loads(r["filed_files"], [])),
                          copied=bool(r["copied"]), error=r["error"], size_bytes=size, size_source=source,
                          in_client=req.get("in_client") is not False,
                          replace_volumes=tuple(str(v) for v in req.get("replace_volumes") or ()))


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
               size_bytes: Optional[int] = None, size_source: Optional[str] = None,
               replace_volumes: Sequence[str] = ()) -> DownloadRecord:
        """A new SENT record (or QUEUED: waiting for room under the download budget, at the end of the queue). Refused
        while another record tracks the same torrent (queued or in qBittorrent). *size_bytes* / *size_source*: what it
        counts against the budget (default: the release's size). *replace_volumes*: a replacement's held volumes (of
        the wanted ones) whose files it replaces when filed (owner, 2026-10-24)."""
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
        replacing = [v for v in normalize_volumes(replace_volumes) if v in wanted]
        if replacing:
            request["replace_volumes"] = replacing
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
                        manga: SuwayomiManga, target_dir: str, batch: str, backend: str = CHAPTER_BACKEND_SUWAYOMI,
                        extra: Optional[Dict[str, Any]] = None) -> Tuple[List[DownloadRecord], List[str]]:
        """New SENT chapter records (a Suwayomi ledger only), one per chapter, in one transaction - written BEFORE the
        chapters are enqueued in the program (the caller marks them FAILED when the enqueue fails). *backend*: the
        program the chapters' ids belong to (the client that listed them; transition audit B2/B5). A chapter already in
        hand - the same chapter of the same program, or the same chapter number of this series, sent and not failed or
        cancelled - is skipped. *extra*: more request keys (the extension host's database id). Returns (records made,
        why each skipped chapter was skipped)."""
        self._writable(TOOL_SUWAYOMI)
        if backend not in CHAPTER_BACKENDS:
            raise DownloadError(f"unknown chapter backend {backend!r}")
        if not target_dir:
            raise DownloadError("a chapter download needs its target folder")
        if not batch:
            raise DownloadError("a chapter download needs its batch")
        now = utcnow()
        made: List[int] = []
        skipped: List[str] = []
        with self.connect(durable=True) as con:
            con.execute("BEGIN IMMEDIATE")          # the in-hand check and the inserts as one step
            held, refs = self._chapters_in_hand(con, series_id, backend)
            elsewhere = self._chapters_elsewhere(con, series_id, backend)
            for ch in chapters:
                number = exact_number(ch.number) if ch.number and not ch.number.startswith("-") else None
                if number is None:
                    skipped.append(f"{ch.name}: no chapter number")
                    continue
                if str(ch.id) in refs or number in held:
                    skipped.append(f"ch {number}: already sent")
                    continue
                if str(ch.id) in elsewhere:
                    skipped.append(f"ch {number}: already on its way for another folder")
                    continue
                request = {"title": ch.name, "wanted_chapters": [number], "chapter_id": int(ch.id),
                           "chapter_name": ch.name, "scanlator": ch.scanlator or "", "chapter_url": ch.real_url or ch.url,
                           "upload_date": ch.upload_date, "source_id": str(source.id),
                           "source_name": source.display_name, "manga_id": int(manga.id), "manga_title": manga.title,
                           "batch": batch, **(extra or {})}
                cur = con.execute(
                    "INSERT INTO ledger (created_at, updated_at, series_id, request, tool, destination, status,"
                    " external_ref, backend) VALUES (?,?,?,?,?,?,?,?,?)",
                    (now, now, int(series_id), json.dumps(request, ensure_ascii=False), TOOL_SUWAYOMI,
                     str(target_dir), DownloadStatus.SENT, str(int(ch.id)), backend))
                made.append(int(cur.lastrowid))
                held.add(number)
                refs.add(str(ch.id))
        return [self.get(i) for i in made], skipped  # type: ignore[misc]

    def chapters_in_hand(self, series_id: int) -> Set[str]:
        """The chapter numbers of *series_id* that a chapter download has in hand (sent, downloaded, filed - also
        removed: filed, Suwayomi's copy gone - while a filed file is still in the series folder), exact strings: not
        sent again - whichever program downloads them."""
        with self.connect() as con:
            return self._chapters_in_hand(con, series_id, CHAPTER_BACKEND_SUWAYOMI)[0]

    @staticmethod
    def _chapters_in_hand(con, series_id: int, backend: str) -> Tuple[Set[str], Set[str]]:
        """The series' chapter numbers in hand (any program's) and the chapter ids of *backend* already used: on their
        way (queued, sent, downloaded), or filed while a file it filed is still in the series folder. A filed chapter
        whose file was moved away (to be replaced by another source's copy) can be sent again (owner, 2026-10-11:
        "skipped 9 ... already sent" for chapters moved to the holding folder). The ids are compared within one program
        only: Suwayomi's and the extension host's both start at 1 (transition audit B2); a record of unknown program (an
        older build's) counts for both."""
        filed = (DownloadStatus.FILED, DownloadStatus.REMOVED)
        keep = (*TRACKED, DownloadStatus.REMOVED)
        row = con.execute("SELECT r.path AS root, s.rel_path AS rel FROM series s JOIN roots r ON r.id = s.root_id"
                          " WHERE s.id = ?", (int(series_id),)).fetchone()
        series_dir = os.path.join(row["root"], *row["rel"].split("/")) if row is not None and row["rel"] else None

        def still_there(files) -> bool:
            if series_dir is None:
                return True                         # cannot look: counted, as before
            return any(os.path.isfile(os.path.join(series_dir, *str(f).replace("\\", "/").split("/"))) for f in files)

        numbers: Set[str] = set()
        refs: Set[str] = set()
        for r in con.execute(f"SELECT request, status, filed_files, external_ref, backend FROM ledger WHERE tool = ?"
                             f" AND series_id = ? AND status IN ({','.join('?' * len(keep))})",
                             (TOOL_SUWAYOMI, int(series_id), *keep)):
            if r["status"] in filed and not still_there(_loads(r["filed_files"], [])):
                continue
            numbers.update(str(c) for c in _loads(r["request"], {}).get("wanted_chapters") or ())
            if r["backend"] in (None, backend):
                refs.add(r["external_ref"])
        return numbers, refs

    @staticmethod
    def _chapters_elsewhere(con, series_id: int, backend: str) -> Set[str]:
        """The chapter ids of *backend* another series has in hand (queued, sent, downloaded, or filed while the
        program still has its copy). Suwayomi keeps ONE copy of a chapter: the same series in two folders (owner,
        2026-10-18, "Kanojo Shoukan Shimashita!" in two libraries) sent chapters 19-25 twice; the first send filed them
        and Suwayomi deleted its copy, so the second failed ("the chapter's CBZ is not in Suwayomi's download folder").
        The other folder can have the chapter once this download is done. Another program's ids are other chapters."""
        return {r[0] for r in con.execute(
            f"SELECT external_ref FROM ledger WHERE tool = ? AND series_id != ? AND (backend IS NULL OR backend = ?)"
            f" AND status IN ({','.join('?' * len(TRACKED))})",
            (TOOL_SUWAYOMI, int(series_id), backend, *TRACKED)) if r[0]}

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

    def note_replacement(self, record_id: int, batch_id: int) -> bool:
        """Remember the replaced-files batch (``mangalist.store.replacements``) that took a replacement's old copies to
        the holding folder before its filing (owner, 2026-10-24), so a later pass resumes it. True when it changed."""
        with self.connect(durable=True) as con:
            con.execute("BEGIN IMMEDIATE")
            return self._set_request(con, record_id, replace_batch=int(batch_id))

    def active(self, backends: Optional[Sequence[str]] = None) -> Sequence[DownloadRecord]:
        """Every record not yet REMOVED / FAILED / CANCELLED, oldest first. *backends*: only the chapter records of
        these programs (``''`` in it: also the records of unknown program, an older build's)."""
        with self.connect() as con:
            rows = con.execute(f"SELECT * FROM ledger WHERE {self._where()} AND status IN ({','.join('?' * len(ACTIVE))})"
                               " ORDER BY id", (*self._args(), *ACTIVE)).fetchall()
        out = [_record(r) for r in rows]
        return out if backends is None else [r for r in out if r.backend in tuple(backends)]

    def set_stuck(self, record_id: int, stuck: bool) -> bool:
        """Remember whether a SENT chapter download is one its program gave up on (the arrivals pass saw the chapter in
        the ``ERROR`` state): the In progress list offers Retry for it - whatever the note says (transition audit B1).
        True when it changed. ``updated_at`` is left alone."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if stuck:
                return self._set_request(con, record_id, stuck=True)
            return self._drop_request_key(con, record_id, "stuck")

    def _request_of(self, con, record_id: int) -> Optional[Dict[str, Any]]:
        r = con.execute(f"SELECT request FROM ledger WHERE id = ? AND {self._where()}",
                        (record_id, *self._args())).fetchone()
        return None if r is None else _loads(r["request"], {})

    def _drop_request_key(self, con, record_id: int, key: str) -> bool:
        req = self._request_of(con, record_id)
        if req is None or key not in req:
            return False
        req.pop(key)
        con.execute(f"UPDATE ledger SET request = ? WHERE id = ? AND {self._where()}",
                    (json.dumps(req, ensure_ascii=False), record_id, *self._args()))
        return True

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

    def dismiss(self, record_id: int) -> DownloadRecord:
        """The owner dismissed a FAILED download (the In progress list's "Dismiss", owner 2026-10-15): it is marked
        cancelled - out of In progress, under Show finished - and nothing else changes (a torrent still in the client
        is left alone and, as for any cancelled record, is the owner's from then on: it no longer counts against the
        download budget). Only a FAILED record can be dismissed (:meth:`cancel` keeps refusing one); the failure stays
        readable in ``error``."""
        record = self.get(record_id)
        why = (record.error or "failed") if record is not None else "failed"
        return self.set_status(record_id, DownloadStatus.CANCELLED, expect=(DownloadStatus.FAILED,),
                               error=f"dismissed by the owner (it had failed: {why})")

    # --- clearing finished records -------------------------------------------------------------------

    def clear_finished(self, older_than_days: float, *, now: Optional[datetime] = None,
                       dry_run: bool = False) -> "ClearReport":
        """Delete the finished records (REMOVED, CANCELLED) not changed for *older_than_days* days, except the ones a
        feature still relies on (see the module docstring); the info hashes of the torrents among them are remembered.
        ``dry_run`` only counts. Works on every tool's records whatever this ledger's tool (it is housekeeping of the
        one table). *older_than_days* of 0 or less clears every finished record that is safe to clear."""
        when = now or datetime.now(timezone.utc)
        cutoff = (when - timedelta(days=max(0.0, float(older_than_days)))).replace(microsecond=0).isoformat()
        report = ClearReport()
        with self.connect(durable=True) as con:
            con.execute("BEGIN IMMEDIATE")
            newest = con.execute("SELECT COALESCE(MAX(id), 0) FROM ledger").fetchone()[0]
            first = int(self._meta(con, "upgrades.first_download") or 0)
            rows = con.execute("SELECT id, tool, series_id, external_ref, status, updated_at FROM ledger"
                               f" WHERE status IN ({','.join('?' * len(FINISHED))}) ORDER BY id", FINISHED).fetchall()
            batches = {}
            try:
                batches = {r[0]: r[1] for r in con.execute("SELECT download_id, status FROM replacements")}
            except sqlite3.OperationalError:        # a database from before the upgrades schema
                pass
            clear: List[sqlite3.Row] = []
            for r in rows:
                report.finished += 1
                if r["updated_at"] > cutoff:
                    report.kept_recent += 1
                elif r["id"] == newest:
                    report.kept_newest += 1
                elif batches.get(r["id"]) in HOLDING_BATCH:
                    report.kept_waiting_chapters += 1
                elif (r["tool"] == TOOL_QBITTORRENT and r["status"] == DownloadStatus.REMOVED and r["id"] > first
                      and r["id"] not in batches):        # (chapter downloads are never looked at: no batch is due)
                    report.kept_unexamined += 1
                else:
                    clear.append(r)
            report.cleared = len(clear)
            if clear and not dry_run:
                remembered = _loads(self._meta(con, META_CLEARED_RELEASES), {})
                for r in clear:
                    if r["tool"] == TOOL_QBITTORRENT and r["series_id"] is not None and r["external_ref"]:
                        mine = remembered.setdefault(str(r["series_id"]), [])
                        if r["external_ref"].lower() not in mine:
                            mine.append(r["external_ref"].lower())
                con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (META_CLEARED_RELEASES, json.dumps(remembered)))
                ids = [r["id"] for r in clear]
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i + 500]
                    con.execute(f"DELETE FROM ledger WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        return report

    @staticmethod
    def _meta(con, key: str) -> Optional[str]:
        row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def cleared_releases(self) -> Dict[int, Set[str]]:
        """series id -> info hashes of the torrents whose records were cleared (lowercase): releases already sent once,
        which the find pass must not pick again."""
        with self.connect() as con:
            raw = _loads(self._meta(con, META_CLEARED_RELEASES), {})
        out: Dict[int, Set[str]] = {}
        for key, hashes in raw.items():
            try:
                out[int(key)] = {str(h).lower() for h in hashes}
            except (TypeError, ValueError):
                continue
        return out

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


# The built-in extensions backend (prototype/mihon-mangalist): which program downloads chapters, and MangaList's own
# extension host. Plain settings (no secret: the host has no login).
SETTING_CHAPTER_BACKEND = "downloads.chapter_backend"      # 'suwayomi' (default) | 'extensions'
SETTING_EXTENSION_HOST = "downloads.extension_host"        # {url, download_dir, downloads_path, repos, ...}
SETTING_EXTENSION_AUTO_UPDATE = "downloads.extension_auto_update"   # bool: the update job installs what it finds
META_EXTENSIONS_SERIES = "extensions.series."              # + the series id: the per-series choices on the host
#: The id of the extension host's database the stored host ids belong to (transition audit B4; mangalist.downloads.
#: host_identity): learned from the host's /compat/about.
META_HOST_INSTANCE = "extensions.host_instance"
DEFAULT_EXTENSION_HOST_URL = "http://127.0.0.1:4570"
#: keiyoushi's repository (the one MangaList's Suwayomi uses; the host's own default too).
DEFAULT_EXTENSION_REPO = "https://raw.githubusercontent.com/keiyoushi/extensions/repo/index.json"


@dataclass(frozen=True)
class ExtensionHostConfig:
    """MangaList's extension host as Settings edits it, and the options the container passes it when it starts it.

    ``download_dir`` is the folder MangaList files finished chapters from, as MangaList sees it (empty: the host's own
    ``downloadsPath`` - right whenever the host runs next to MangaList, as in the container); ``downloads_path`` is the
    host's own answer, learned at the last connection test or host start. ``flaresolverr_url`` empty: the environment's
    (``MANGALIST_FLARESOLVERR_URL``), else none.

    ``repos``: the extension repositories as the owner last left the host's list (Settings > Built-in extensions; each
    added one checked by the host first) - the container's host is started with them (``--repo`` each), so a new data
    folder gets the same list. None: never edited - the single ``repo_url`` of an older version, else
    ``MANGALIST_EXTENSION_REPO``, else the host's default (keiyoushi's)."""

    url: str = DEFAULT_EXTENSION_HOST_URL
    download_dir: str = ""
    downloads_path: str = ""
    repo_url: str = ""
    flaresolverr_url: str = ""
    webview: bool = True
    repos: Optional[Tuple[str, ...]] = None

    @property
    def files_from(self) -> str:
        """The folder the chapter arrivals pass reads (the one holding ``mangas/``)."""
        return self.download_dir or self.downloads_path


class SuwayomiSettings:
    """Suwayomi's connection (``meta``: the password stays out of ``settings``), the sources MangaList may use (a plain
    setting, like nyaa's switches) and each series' choices (``meta``): the Suwayomi manga it was matched to - by its
    MangaDex id, or by a title search the owner confirmed - and the scanlation group the owner picked.

    **The chapter backend** (prototype/mihon-mangalist): with "Built-in extensions" chosen (:meth:`backend`) the
    connection is MangaList's own extension host (:meth:`connection` then describes it, with ``backend='extensions'``),
    and each series' choices are kept apart from Suwayomi's (``extensions.series.<id>``): the host's manga ids are its
    own. The scanlation group the owner picked under Suwayomi carries over until one is picked on the host. The allowed
    sources are shared: a source's id is the extension's, the same in both programs."""

    def __init__(self, store):
        self.store = store

    # --- the backend ---------------------------------------------------------------------------------------------

    def backend(self) -> str:
        value = self.store.get_setting(SETTING_CHAPTER_BACKEND, None)
        return CHAPTER_BACKEND_EXTENSIONS if value == CHAPTER_BACKEND_EXTENSIONS else CHAPTER_BACKEND_SUWAYOMI

    def set_backend(self, backend: str) -> None:
        if backend not in CHAPTER_BACKENDS:
            raise ValueError(f"unknown chapter backend {backend!r}")
        self.store.set_setting(SETTING_CHAPTER_BACKEND, backend)

    @property
    def builtin(self) -> bool:
        return self.backend() == CHAPTER_BACKEND_EXTENSIONS

    def extension_host(self) -> ExtensionHostConfig:
        data = self.store.get_setting(SETTING_EXTENSION_HOST, None)
        data = data if isinstance(data, dict) else {}

        def text(key: str) -> str:
            value = data.get(key)
            return value.strip() if isinstance(value, str) else ""

        webview = data.get("webview")
        repos = data.get("repos")
        return ExtensionHostConfig(url=text("url") or DEFAULT_EXTENSION_HOST_URL, download_dir=text("download_dir"),
                                   downloads_path=text("downloads_path"), repo_url=text("repo_url"),
                                   flaresolverr_url=text("flaresolverr_url"),
                                   webview=webview if isinstance(webview, bool) else True,
                                   repos=tuple(r.strip() for r in repos if isinstance(r, str) and r.strip())
                                   if isinstance(repos, list) else None)

    def save_extension_host(self, config: ExtensionHostConfig) -> None:
        data = {
            "url": (config.url or "").strip() or DEFAULT_EXTENSION_HOST_URL,
            "download_dir": (config.download_dir or "").strip(), "downloads_path": (config.downloads_path or "").strip(),
            "repo_url": (config.repo_url or "").strip(), "flaresolverr_url": (config.flaresolverr_url or "").strip(),
            "webview": bool(config.webview)}
        if config.repos is not None:
            data["repos"] = [r.strip() for r in config.repos if r and r.strip()]
        self.store.set_setting(SETTING_EXTENSION_HOST, data)

    def extension_auto_update(self) -> bool:
        """Whether the scheduled extension update check installs the updates it finds (off unless the owner turns it
        on)."""
        return self.store.get_setting(SETTING_EXTENSION_AUTO_UPDATE, False) is True

    def set_extension_auto_update(self, on: bool) -> None:
        self.store.set_setting(SETTING_EXTENSION_AUTO_UPDATE, bool(on))

    def learn_downloads_path(self, path: str) -> None:
        """Keep the host's own downloads folder (its ``/compat/settings`` answer, or what the container started it with)."""
        path = (path or "").strip()
        config = self.extension_host()
        if path and path != config.downloads_path:
            self.save_extension_host(replace(config, downloads_path=path))

    def connection(self) -> Optional[SuwayomiConnection]:
        """The stored connection (with its password, for the client only), or None when none is set up. With the
        built-in extensions backend: the extension host's (always set up: its default address)."""
        if self.builtin:
            host = self.extension_host()
            return SuwayomiConnection(base_url=host.url, download_dir=host.files_from,
                                      backend=CHAPTER_BACKEND_EXTENSIONS)
        return self.suwayomi_connection()

    def connection_for(self, backend: str) -> Optional[SuwayomiConnection]:
        """The connection of *backend* whichever is chosen: a record or a choice of the other program is finished with
        its own program (transition audit B2 / C2)."""
        if backend == CHAPTER_BACKEND_EXTENSIONS:
            host = self.extension_host()
            return SuwayomiConnection(base_url=host.url, download_dir=host.files_from,
                                      backend=CHAPTER_BACKEND_EXTENSIONS)
        return self.suwayomi_connection()

    def suwayomi_connection(self) -> Optional[SuwayomiConnection]:
        """Suwayomi's own stored connection, whichever backend is chosen."""
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

    def reset_sources(self) -> None:
        """Forget the choice: :meth:`sources` is None again (MangaDex in the owner's languages)."""
        self.store.delete_setting(SETTING_SUWAYOMI_SOURCES)

    # --- per series ------------------------------------------------------------------------------------------

    def _series_key(self, series_id: int, backend: Optional[str] = None) -> str:
        builtin = self.builtin if backend is None else backend == CHAPTER_BACKEND_EXTENSIONS
        return f"{META_EXTENSIONS_SERIES if builtin else META_SUWAYOMI_SERIES}{int(series_id)}"

    def series_choice(self, series_id: int, backend: Optional[str] = None) -> Dict[str, Any]:
        """``{"source_id", "manga_id", "manga_title", "manga_url", "how", "group"}`` as far as known ({} when nothing
        is stored). ``how``: ``mangadex-id`` (found by the MangaDex id MangaPixer links) or ``confirmed`` (a title match
        the owner confirmed). *backend*: whose (None: the chosen backend's; see the class docstring)."""
        builtin = self.builtin if backend is None else backend == CHAPTER_BACKEND_EXTENSIONS
        data = _loads(self.store.get_meta(self._series_key(series_id, backend)), {})
        if builtin and "group" not in data and "group_cleared" not in data:
            group = _loads(self.store.get_meta(f"{META_SUWAYOMI_SERIES}{int(series_id)}"), {}).get("group")
            if group:
                data["group"] = group
        data.pop("group_cleared", None)
        return data

    # --- the carry-over from Suwayomi to the built-in extensions (mangalist.services.extensions.carry_over) -------

    def suwayomi_series_choices(self) -> Dict[int, Dict[str, Any]]:
        """Every series' stored Suwayomi choice (``suwayomi.series.<id>``), whichever backend is chosen: series id ->
        the stored dict (forgotten / empty ones left out)."""
        return self._choices(META_SUWAYOMI_SERIES)

    def extensions_series_choices(self) -> Dict[int, Dict[str, Any]]:
        """Every series' stored choice on the extension host (``extensions.series.<id>``), as stored."""
        return self._choices(META_EXTENSIONS_SERIES)

    def _choices(self, prefix: str) -> Dict[int, Dict[str, Any]]:
        out: Dict[int, Dict[str, Any]] = {}
        with self.store.connect() as con:
            rows = con.execute("SELECT key, value FROM meta WHERE substr(key, 1, ?) = ? AND value IS NOT NULL",
                               (len(prefix), prefix)).fetchall()
        for key, value in rows:
            tail = key[len(prefix):]
            data = _loads(value, {})
            if tail.isdigit() and data:
                out[int(tail)] = data
        return out

    def carry_series_choice(self, series_id: int, choice: Dict[str, Any]) -> bool:
        """Write *choice* as the series' choice on the extension host - only when it has no manga there yet (never
        overwrites: keys already stored, e.g. a scanlation group picked on the host, are kept). One statement, so a
        choice made at the same moment elsewhere wins. True when written."""
        key = f"{META_EXTENSIONS_SERIES}{int(series_id)}"
        value = json.dumps(dict(choice), ensure_ascii=False)
        with self.store.connect() as con:
            cur = con.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = "
                "CASE WHEN meta.value IS NULL OR NOT json_valid(meta.value) THEN excluded.value "
                "WHEN json_type(meta.value) = 'object' THEN json_patch(excluded.value, meta.value) "
                "ELSE excluded.value END "
                "WHERE CASE WHEN meta.value IS NULL OR NOT json_valid(meta.value) THEN 1 "
                "WHEN json_type(meta.value) != 'object' THEN 1 "
                "ELSE json_extract(meta.value, '$.manga_id') IS NULL END", (key, value))
            return cur.rowcount > 0

    def set_series_choice(self, series_id: int, *, backend: Optional[str] = None, **changes: Any) -> Dict[str, Any]:
        """Merge *changes* into the series' choices (a value of None forgets that key); the stored result. *backend*:
        whose choice - the program that answered the lookup (``chapter_backend.backend_of(client)``), so a switch made
        while a pass or a dialog held the old program's client never files its manga id under the new program's key
        (transition audit B5); None: the chosen backend's. A choice on the extension host is stamped with the host's
        database id MangaList knows (``host_instance``, transition audit B4)."""
        builtin = self.builtin if backend is None else backend == CHAPTER_BACKEND_EXTENSIONS
        key = self._series_key(series_id, backend)
        data = _loads(self.store.get_meta(key), {})
        for name, value in changes.items():
            if value is None:
                data.pop(name, None)
                if name == "group" and builtin:
                    data["group_cleared"] = True        # the owner cleared it here: Suwayomi's no longer carries over
            else:
                data[name] = value
                if name == "group":
                    data.pop("group_cleared", None)
        if "manga_id" in changes:
            # chosen here now: no longer the other program's choice carried over / back (the carry-back - audit B8 -
            # takes only what was decided in the program it carries from)
            for mark in ("carried_from", "carried_back"):
                if mark not in changes:
                    data.pop(mark, None)
        if builtin and "manga_id" in changes:
            instance = self.store.get_meta(META_HOST_INSTANCE)
            if changes["manga_id"] is not None and instance:
                data["host_instance"] = instance
            elif changes["manga_id"] is None:
                data.pop("host_instance", None)
        self.store.set_meta(key, json.dumps(data, ensure_ascii=False) if data else None)
        return self.series_choice(series_id, CHAPTER_BACKEND_EXTENSIONS if builtin else CHAPTER_BACKEND_SUWAYOMI)
