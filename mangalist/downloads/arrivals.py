"""The arrivals pass: finished ``mangalist`` torrents -> the series folder, then "Remove Completed".

One pass (:func:`run_arrivals`) is idempotent and safe to run hourly. For each active download record
(:meth:`~mangalist.store.downloads.DownloadLedger.active`), its torrent is looked up by info hash in qBittorrent's
``mangalist`` category:

- **Missing** from the category (removed, or moved to another category) -> FAILED with the reason; nothing else.
- **SENT** + complete -> DOWNLOADED (and on to filing in the same pass).
- **DOWNLOADED**, a replacement (``replace_volumes``: an other release sent to replace the owner's copies, 2026-10-24)
  -> the owner's copies it covers to the holding folder first, then filed as below (:mod:`.replacing`).
- **DOWNLOADED** -> filing: the torrent's archive files (``scanner.ARCHIVE_EXTS``) whose parsed volume is one of
  the record's ``wanted_volumes`` AND is not held by the series right now (its stored units AND a fresh look at
  its folder) are linked into the record's ``target_dir`` through ONE journal plan - under their own names, or
  the naming scheme's when the library is set to "Rename automatically" and its conversion was started
  (:meth:`~mangalist.store.journal.Journal.plan_links`) -> FILED with ``filed_files`` / ``copied``. Nothing
  matched, the series or target folder gone, or the target no longer inside the series folder / a configured
  root -> FAILED with the reason; the torrent is left alone. The plan id is stored on the record BEFORE the plan
  is applied, so a crash in between is settled by the next pass (the plan recovered, resumed or read back).
  Another MangaList instance holding the root's lock -> the record waits for the next pass.
- **FILED** + "Remove Completed" on (``downloads.remove_completed``) + the torrent still in the ``mangalist``
  category + stopped at its seed goal (``stopped_complete``) + every filed library file present with the size
  the plan recorded + the torrent's data nowhere inside a library root -> ``client.delete(hash,
  delete_files=True)`` -> REMOVED. Any condition unmet -> stays FILED (reported as waiting). A FAILED record
  is never removed; nothing outside the category is ever touched. Deleting is qBittorrent's doing; the library
  files survive it (a hard link keeps the data, a copy is its own).

qBittorrent and MangaList see the same paths (both mount ``/mnt/user`` at ``/data`` on the box), so a
torrent's ``save_path`` + a file's ``name`` is the file on MangaList's side too. No Qt here.
"""

from __future__ import annotations

import logging
import os
import posixpath
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..scanner import ARCHIVE_EXTS
from ..store.downloads import DownloadLedger, StatusConflict
from .contracts import QBITTORRENT_CATEGORY, DownloadRecord, DownloadStatus, TorrentClient, TorrentInfo
from .placement import locate_series, same_or_inside, series_units

_log = logging.getLogger(__name__)

PLAN_REASON = "arrival"
_MAX_RANGE = 1000   # a volume range wider than this is read as its two ends only


@dataclass
class ArrivalsReport:
    """What one pass did, by record id."""

    checked: int = 0
    downloaded: List[int] = field(default_factory=list)
    filed: List[int] = field(default_factory=list)
    copied: List[int] = field(default_factory=list)
    removed: List[int] = field(default_factory=list)
    failed: List[Tuple[int, str]] = field(default_factory=list)
    waiting: List[Tuple[int, str]] = field(default_factory=list)
    errors: List[Tuple[int, str]] = field(default_factory=list)
    error: Optional[str] = None       # the pass itself could not run (e.g. qBittorrent unreachable)

    def summary(self) -> Dict[str, Any]:
        return {"checked": self.checked, "downloaded": len(self.downloaded), "filed": len(self.filed),
                "copied": len(self.copied), "removed": len(self.removed), "failed": len(self.failed),
                "waiting": len(self.waiting), "errors": len(self.errors)}


# --- volumes -------------------------------------------------------------------------------------------


def _expand(start: Optional[Decimal], end: Optional[Decimal]) -> Set[Decimal]:
    if start is None:
        return set()
    if end is None or end == start:
        return {start}
    if start == start.to_integral_value() and end == end.to_integral_value() and 0 < end - start <= _MAX_RANGE:
        return {Decimal(n) for n in range(int(start), int(end) + 1)}
    return {start, end}


def volumes_of(name: str, kind_hint: Optional[str] = None, series_title: Optional[str] = None) -> Set[Decimal]:
    """The volumes an archive *name* holds (empty when the name names no whole volume)."""
    from ..parsing import Kind, ParseContext, parse_name

    parsed = parse_name(name, ParseContext(kind_hint=kind_hint, series_title=series_title))
    if parsed.kind not in (Kind.VOLUME, Kind.BOTH) or parsed.volume is None:
        return set()
    return _expand(parsed.volume.start, parsed.volume.end)


def release_hint(title: Optional[str], vol_from: Optional[str]) -> Optional[str]:
    """The kind hint a release's file names are read with: ``"volumes"`` when its title states volumes with a volume
    mark (``v01-03``, ``Vol. 3``), else None (the names alone decide). Bare numbers (``001-051``) state nothing: read
    with that hint, chapter 3 of a chapter pack passed for volume 3 (owner, 2026-10-13).

    The title is read again here instead of trusting a stored flag: a finding recorded before ``numbers_only`` existed,
    a queued record and the ledger's request all come back without it (owner's report, 2026-10-17: a finding of
    2026-10-13 for "<Series> 001-010 (2018-2019) (Digital)" was sent for "volumes" 1 and 2 of a side story the owner
    had answered holds chapters; its files "... 001 ...cbz" and "... 002 ...cbz" were kept as those volumes)."""
    if not vol_from:
        return None
    from ..services.nyaa.titles import parse_title

    return None if parse_title(title or "").numbers_only else "volumes"


def held_volumes(db, series, series_dir: str) -> Set[Decimal]:
    """The volumes the series holds right now: its stored ``volume`` units (nested rows included) plus every
    archive in its folder, read again now."""
    held: Set[Decimal] = set()
    for u in series_units(db, series):
        if u.kind == "volume" and u.vol_from is not None:
            held |= _expand(Decimal(u.vol_from), Decimal(u.vol_to) if u.vol_to is not None else None)
    title = os.path.basename(series_dir)
    for folder, dirs, files in os.walk(series_dir):
        dirs[:] = [d for d in dirs if not d.startswith((".", "@", "#"))]
        for name in files:
            if os.path.splitext(name)[1].lower() in ARCHIVE_EXTS:
                held |= volumes_of(name, series.kind_hint, title)
    return held


def _ident(path: str) -> Optional[str]:
    """``'<dev>:<ino>'`` of *path* (the journal's record of a hard link), None when it cannot be read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{st.st_dev}:{st.st_ino}"


def _plain(values: Iterable[Decimal]) -> str:
    from ..parsing.model import plain

    return ", ".join(plain(v) for v in sorted(values)) or "none"


# --- the pass ------------------------------------------------------------------------------------------


class RemoveRefused(RuntimeError):
    """Remove now was refused; the message says why (nothing was deleted)."""


def remove_now(client: TorrentClient, ledger: DownloadLedger, record_id: int, *, journal=None) -> DownloadRecord:
    """Remove one filed download's torrent now, on the owner's word (see :meth:`_Pass.remove_now`)."""
    return _Pass(client, ledger, journal, True, lambda: False).remove_now(record_id)


def run_arrivals(client: TorrentClient, ledger: DownloadLedger, *, journal=None,
                 remove_completed: Optional[bool] = None,
                 should_stop: Callable[[], bool] = lambda: False) -> ArrivalsReport:
    """One arrivals pass over every active record (see the module docstring)."""
    return _Pass(client, ledger, journal, remove_completed, should_stop).run()


class _Pass:
    def __init__(self, client, ledger: DownloadLedger, journal, remove_completed, should_stop):
        from ..store.journal import Journal

        self.client = client
        self.ledger = ledger
        self.db = ledger.store
        self.journal = journal if journal is not None else Journal(self.db)
        self.remove = ledger.remove_completed() if remove_completed is None else bool(remove_completed)
        self._moves = None                  # the journal's applied moves, read once per pass (see _current_path)
        self._discards = None               # the deliberate deletions, read once per pass (see _discarded)
        self.should_stop = should_stop
        self.report = ArrivalsReport()

    def run(self) -> ArrivalsReport:
        records = list(self.ledger.active())
        if not records:
            return self.report
        try:
            torrents = {t.info_hash.lower(): t for t in self.client.torrents(QBITTORRENT_CATEGORY)}
        except Exception as exc:  # noqa: BLE001 - nothing changes while qBittorrent cannot be asked
            self.report.error = f"qBittorrent could not list the {QBITTORRENT_CATEGORY!r} category: " \
                                f"{type(exc).__name__}: {exc}"
            _log.warning("Arrivals: %s; nothing changed", self.report.error)
            return self.report
        for rec in records:
            if self.should_stop():
                break
            self.report.checked += 1
            try:
                self._one(rec, torrents.get(rec.info_hash.lower()))
            except StatusConflict as exc:   # another pass moved it meanwhile
                _log.info("Arrivals: %s", exc)
            except Exception as exc:  # noqa: BLE001 - one record must not stop the others; it stays as it was
                _log.exception("Arrivals: download %d (%s) failed this pass", rec.id, rec.title)
                self.report.errors.append((rec.id, f"{type(exc).__name__}: {exc}"))
        return self.report

    # --- one record --------------------------------------------------------------------------------

    def _one(self, rec: DownloadRecord, t: Optional[TorrentInfo]) -> None:
        if t is None or t.category != QBITTORRENT_CATEGORY:
            gone = (f"the torrent is no longer in qBittorrent's {QBITTORRENT_CATEGORY!r} category (removed there, or "
                    "moved to another category)")
            if rec.status == DownloadStatus.FILED:
                # The owner removed it in qBittorrent after filing: done, not failed - the volumes are in the library.
                problem = self._filed_files_problem(rec)
                if problem is None:
                    self.ledger.set_status(rec.id, DownloadStatus.REMOVED, expect=(DownloadStatus.FILED,),
                                           error="removed in qBittorrent, not by MangaList")
                    self.report.removed.append(rec.id)
                    _log.info("Arrivals: download %d (%s): %s after filing; done", rec.id, rec.title, gone)
                    return
                self._fail(rec, f"{gone}, and {problem}")
                return
            self._fail(rec, gone)
            return
        if rec.status == DownloadStatus.SENT:
            if not t.complete:
                self._wait(rec, f"downloading ({t.progress:.0%})")
                return
            rec = self.ledger.set_status(rec.id, DownloadStatus.DOWNLOADED, expect=(DownloadStatus.SENT,))
            self.report.downloaded.append(rec.id)
            _log.info("Arrivals: download %d (%s) finished", rec.id, rec.title)
        if rec.status == DownloadStatus.DOWNLOADED:
            rec = self._file(rec, t)
            if rec is None or rec.status != DownloadStatus.FILED:
                return
        if rec.status == DownloadStatus.FILED:
            if self.remove:
                self._remove_completed(rec, t)
            else:
                self._wait(rec, "filed; Remove Completed is off")

    # --- filing ------------------------------------------------------------------------------------

    def _file(self, rec: DownloadRecord, t: TorrentInfo) -> Optional[DownloadRecord]:
        try:
            series, root, series_dir = locate_series(self.db, rec.series_id)
        except LookupError as exc:
            return self._fail(rec, f"{exc}; nothing filed")
        problem = self._target_problem(rec, series, series_dir)
        plan_id = self.ledger.plan_id(rec.id)
        if plan_id is not None:     # a plan from an earlier pass: read it back, or resume it while still safe
            return self._settle(rec, plan_id, series_dir, blocked=problem)
        if problem:
            return self._fail(rec, problem + "; nothing filed, the torrent is left alone")
        if rec.replace_volumes:         # an other release sent to replace the owner's copies (owner, 2026-10-24)
            return self._file_replacement(rec, t, series, root, series_dir)
        links, notes = self._pick(rec, t, series, series_dir, root)
        if not links:
            return self._fail(rec, f"none of the torrent's archives is a wanted volume still missing from the series "
                                   f"(wanted: {', '.join(rec.wanted_volumes)}; {'; '.join(notes) or 'no archives'})"
                                   "; nothing filed, the torrent is left alone")
        from ..store.journal import StepRefused

        try:
            plan = self.journal.plan_links(PLAN_REASON, links, root_path=root.path,
                                           note=f"download {rec.id}: {rec.title}")
        except StepRefused as exc:
            return self._fail(rec, f"the journal refused the filing: {exc}; nothing filed")
        self.ledger.attach_plan(rec.id, plan.id)
        if notes:
            _log.info("Arrivals: download %d: not filed: %s", rec.id, "; ".join(notes))
        return self._settle(rec, plan.id, series_dir)

    def _target_problem(self, rec: DownloadRecord, series, series_dir: str) -> Optional[str]:
        target = rec.target_dir
        if series.status != "present":
            return "the series folder was missing at the last scan"
        if not os.path.isdir(series_dir):
            return "the series folder is gone"
        if not target or not os.path.isabs(target) or not os.path.isdir(target):
            return f"the target folder {target!r} is gone"
        if not same_or_inside(target, series_dir):
            return "the target folder is no longer inside the series folder"
        root = self.db.root_for_path(target)
        if root is None or not same_or_inside(target, root.path):
            return "the target folder is no longer inside a configured library root"
        return None

    def _scheme_namer(self, root):
        """The Renamer when *root* keeps its files in the scheme (filed volumes are named by it at once), else None."""
        try:
            from ..renamer import Renamer

            renamer = Renamer(self.db)
            return renamer if renamer.keeps_in_scheme(root) else None
        except Exception:  # noqa: BLE001 - files keep their release names
            _log.debug("Arrivals: no scheme naming", exc_info=True)
            return None

    def _pick(self, rec: DownloadRecord, t: TorrentInfo, series, series_dir: str, root=None, *,
              held: Optional[Set[Decimal]] = None, free: Iterable[str] = ()):
        """(links, notes): the torrent's files to file and why the others are not. *held*: the volumes counted as held
        (default: :func:`held_volumes`); *free*: library paths (normcase'd) whose name a link may take - a
        replacement's old copies, moved to the holding folder first."""
        request = self.ledger.request(rec.id)
        hint = release_hint(request.get("title") or rec.title, request.get("vol_from"))   # the title states volumes
        wanted = {Decimal(v) for v in rec.wanted_volumes}
        held = held_volumes(self.db, series, series_dir) if held is None else held
        free = set(free)
        save_path = os.path.normpath(t.save_path)
        links: List[Tuple[str, str]] = []
        taken_names: Set[str] = set()
        taken_vols: Set[Decimal] = set()
        notes: List[str] = []
        namer = self._scheme_namer(root) if root is not None else None
        for f in self.client.files(t.info_hash):
            name = f.name.replace("\\", "/")
            base = posixpath.basename(name)
            if os.path.splitext(base)[1].lower() not in ARCHIVE_EXTS:
                continue
            if f.progress < 1.0:
                notes.append(f"{base}: not downloaded")
                continue
            vols = volumes_of(base, hint)
            if not vols:
                notes.append(f"{base}: no volume number")
                continue
            if not vols <= wanted:
                notes.append(f"{base}: volume {_plain(vols)} not wanted")
                continue
            if vols & held:
                notes.append(f"{base}: volume {_plain(vols & held)} already held")
                continue
            if vols & taken_vols:
                notes.append(f"{base}: volume {_plain(vols & taken_vols)} twice in the torrent")
                continue
            parts = [p for p in name.split("/") if p]
            if any(p in (".", "..") for p in parts):
                notes.append(f"{base}: unsafe path in the torrent")
                continue
            src = os.path.join(save_path, *parts)
            if not same_or_inside(src, save_path) or not os.path.isfile(src):
                notes.append(f"{base}: not found under the torrent's save path")
                continue
            named = (namer.scheme_name(root, series, base, size=int(getattr(f, "size", 0) or 0))
                     if namer is not None else None) or base
            dst = os.path.join(rec.target_dir, named)
            if (os.path.lexists(dst) and os.path.normcase(os.path.normpath(dst)) not in free) \
                    or os.path.normcase(named) in taken_names:
                notes.append(f"{base}: a file of that name is already in the target folder")
                continue
            taken_names.add(os.path.normcase(named))
            taken_vols |= vols
            links.append((src, dst))
        return links, notes

    # --- filing a replacement (other releases; mangalist.downloads.replacing) ---------------------------------------

    def _file_replacement(self, rec: DownloadRecord, t: TorrentInfo, series, root,
                          series_dir: str) -> Optional[DownloadRecord]:
        """File a replacement: the owner's copies it covers to the holding folder first (one replaced-files batch,
        journaled like an upgrade's), then the release's files as any arrival's; the batch is put back when nothing
        could be filed. A pass that stops in between resumes from the batch kept on the record."""
        from .. import upgrades
        from ..store.replacements import ReplacementConflict, ReplacementStore
        from .replacing import held_files, held_volumes_except, plan_replacement

        request = self.ledger.request(rec.id)
        hint = release_hint(request.get("title") or rec.title, request.get("vol_from"))
        store = ReplacementStore(self.db)
        batch_id = request.get("replace_batch")
        batch = store.get(int(batch_id)) if isinstance(batch_id, int) and not isinstance(batch_id, bool) else None
        if batch is not None and batch.status == "pending":         # its move was interrupted or had to wait
            try:
                batch = upgrades.hold_batch(self.db, batch.id, journal=self.journal)
            except ReplacementConflict as exc:
                _log.info("Arrivals: download %d: %s", rec.id, exc)
                batch = store.get(batch.id)
        if batch is not None and batch.status == "pending":
            self._wait(rec, f"moving your old copies to the holding folder: {batch.error or 'waiting'}")
            return None
        if batch is not None and batch.status not in ("held", "purged"):
            why = (f"your old copies could not be moved to the holding folder ({batch.error or batch.status})"
                   if batch.status == "failed" else
                   f"its replaced files were {batch.status} before the release was filed")
            return self._fail(rec, f"{why}; nothing filed, the torrent is left alone")
        if batch is None:
            files = held_files(self.db, series, series_dir)
            plan = plan_replacement(
                files, {Decimal(v) for v in rec.replace_volumes},
                lambda held, free: self._pick(rec, t, series, series_dir, root, held=held, free=free),
                lambda src: volumes_of(os.path.basename(src), hint),
                lambda away: held_volumes_except(self.db, series, series_dir, away),
                exists=lambda p: upgrades._plain_below(p, root.path) is not None)
            if not plan.links:
                return self._fail(rec, "none of the torrent's archives is a wanted volume that can be filed (wanted: "
                                       f"{', '.join(rec.wanted_volumes)}; replacing: {', '.join(rec.replace_volumes)}; "
                                       f"{'; '.join(plan.notes) or 'no archives'}); nothing filed, the torrent is "
                                       "left alone")
            if plan.away:
                sizes = {src: os.stat(src).st_size for src, _ in plan.replacing}
                replaced = []
                for f in plan.away:
                    st = upgrades._plain_below(f.path, root.path)
                    replaced.append(upgrades.ReplacedFile(
                        path=f.path, rel=f.rel, size=st.st_size, modified=upgrades._iso_from_ns(st.st_mtime_ns),
                        chapters="", volume=", ".join(_plain([v]) for v in sorted(f.volumes))))
                try:
                    batch = upgrades.hold_replaced_volumes(
                        self.db, download_id=rec.id, title=rec.title, series_id=rec.series_id, root_path=root.path,
                        series_dir=series_dir, files=replaced, volumes=[_plain([v]) for v in plan.volumes],
                        volume_files=[(dst, sizes[src]) for src, dst in plan.replacing], journal=self.journal)
                except ReplacementConflict as exc:
                    self._wait(rec, f"your old copies wait for the holding folder: {exc}")
                    _log.warning("Arrivals: download %d (%s): not filed yet - the holding folder cannot be used: %s",
                                 rec.id, rec.title, exc)
                    return None
                self.ledger.note_replacement(rec.id, batch.id)
                _log.info("Arrivals: download %d (%s) replaces %s: %s", rec.id, rec.title,
                          _plain(plan.volumes), ", ".join(f.rel for f in plan.away))
                return self._file_replacement(rec, t, series, root, series_dir)   # on from the batch's state
        away = []
        if batch is not None and batch.plan_id is not None:
            away = [s.src for s in self.journal.get_plan(batch.plan_id).steps if s.op == "hold" and s.state == "done"]
        links, notes = self._pick(rec, t, series, series_dir, root,
                                  held=held_volumes_except(self.db, series, series_dir, away))
        if not links:
            return self._replacement_failed(rec, batch, "none of the torrent's archives can be filed now ("
                                            f"{'; '.join(notes) or 'no archives'})")
        from ..store.journal import StepRefused

        try:
            plan = self.journal.plan_links(PLAN_REASON, links, root_path=root.path,
                                           note=f"download {rec.id}: {rec.title} (replacing {len(away)} file(s))")
        except StepRefused as exc:
            return self._replacement_failed(rec, batch, f"the journal refused the filing: {exc}")
        self.ledger.attach_plan(rec.id, plan.id)
        if notes:
            _log.info("Arrivals: download %d: not filed: %s", rec.id, "; ".join(notes))
        out = self._settle(rec, plan.id, series_dir)
        if out is not None and out.status == DownloadStatus.FAILED and not out.filed_files and batch is not None:
            self._put_back(rec, batch)
        return out

    def _replacement_failed(self, rec: DownloadRecord, batch, why: str) -> DownloadRecord:
        if batch is not None:
            self._put_back(rec, batch)
        return self._fail(rec, f"{why}; nothing filed, the torrent is left alone"
                          + ("; your old copies were put back" if batch is not None else ""))

    def _put_back(self, rec: DownloadRecord, batch) -> None:
        """Nothing of the replacement was filed: its old copies go back from the holding folder (Restore)."""
        from .. import upgrades

        try:
            after = upgrades.restore_batch(self.db, batch.id, journal=self.journal)
        except Exception as exc:  # noqa: BLE001 - the batch stays held, restorable from the Download tab
            _log.warning("Arrivals: download %d: the old copies of batch %d were not put back: %s", rec.id, batch.id,
                         exc)
            return
        _log.info("Arrivals: download %d: nothing filed - batch %d %s", rec.id, batch.id,
                  "restored (the old copies are back)" if after.status == "restored" else
                  f"not fully restored: {after.error}")

    def _settle(self, rec: DownloadRecord, plan_id: int, series_dir: str,
                blocked: Optional[str] = None) -> Optional[DownloadRecord]:
        """Bring the record in line with its journal plan (apply / resume it first when needed, unless
        *blocked* says why the target may no longer be written)."""
        from ..store.journal import PlanStateError
        from ..store.lock import LockError

        try:
            plan = self.journal.get_plan(plan_id)
            if plan.status in ("applying", "undoing"):
                self.journal.recover(only=[plan_id])
                plan = self.journal.get_plan(plan_id)
            if plan.status in ("applying", "undoing"):
                self._wait(rec, f"its journal plan {plan_id} is busy in another live process")
                return None
            if plan.status in ("planned", "interrupted"):
                if blocked:
                    return self._fail(rec, blocked + "; filing not resumed, the torrent is left alone",
                                      filed_files=self._filed(plan, series_dir),
                                      copied=any(s.how == "copy" for s in plan.done_steps))
                plan = self.journal.apply(plan_id)
        except LockError as exc:
            self._wait(rec, f"the library root is locked by another MangaList instance ({exc}); next pass")
            return None
        except PlanStateError as exc:
            return self._fail(rec, f"its journal plan {plan_id}: {exc}")
        filed = self._filed(plan, series_dir)
        copied = any(s.how == "copy" for s in plan.done_steps)
        if plan.status == "applied":
            rec = self.ledger.set_status(rec.id, DownloadStatus.FILED, expect=(DownloadStatus.DOWNLOADED,),
                                         error=None, filed_files=filed, copied=copied)
            self.report.filed.append(rec.id)
            if copied:
                self.report.copied.append(rec.id)
                _log.warning("Arrivals: download %d (%s): filed by COPY (no hard link possible): %s",
                             rec.id, rec.title, ", ".join(filed))
            _log.info("Arrivals: download %d (%s) filed: %s", rec.id, rec.title, ", ".join(filed))
            return rec
        failed = next((s for s in plan.steps if s.state == "failed"), None)
        why = (f"filing stopped at {posixpath.basename(failed.dst.replace(os.sep, '/'))}: {failed.error}"
               if failed is not None else f"its journal plan {plan_id} is {plan.status}")
        if filed:
            why += f" (filed before that: {', '.join(filed)})"
        return self._fail(rec, why, filed_files=filed, copied=copied)

    @staticmethod
    def _filed(plan, series_dir: str) -> List[str]:
        return [os.path.relpath(s.dst, series_dir).replace(os.sep, "/") for s in plan.done_steps]

    # --- Remove Completed --------------------------------------------------------------------------

    def _remove_completed(self, rec: DownloadRecord, t: TorrentInfo) -> None:
        if rec.status != DownloadStatus.FILED:          # never anything but a filed record
            return
        if t.category != QBITTORRENT_CATEGORY:
            self._wait(rec, f"not removed: the torrent is in category {t.category!r}")
            self._note(rec, f"not removed: it is in qBittorrent category {t.category!r}")
            return
        if not t.stopped_complete:
            self._wait(rec, f"seeding (qBittorrent state {t.state}); removed once stopped at its seed goal")
            self._note(rec, None)                           # "seeding" is the plain FILED wording
            return
        if not t.seed_goal_reached:     # stopped by hand (or no goal set): the owner's pause is not a seed goal
            goal = f" of {t.max_ratio:g}" if t.max_ratio is not None and t.max_ratio >= 0 else ""
            self._wait(rec, f"stopped before its seed goal (ratio {t.ratio:.2f}{goal}); not removed - resume it in "
                            "qBittorrent, remove it there yourself, or use Remove now")
            # kept on the record, so the downloads list says it (owner, 2026-10-09: the list kept saying "seeding")
            self._note(rec, f"stopped before its seed goal (ratio {t.ratio:.2f}{goal})")
            return
        problem = self._library_problem(rec, t)
        if problem:
            self._wait(rec, f"not removed: {problem}")
            self._note(rec, f"not removed: {problem}")
            _log.warning("Arrivals: download %d (%s) not removed: %s", rec.id, rec.title, problem)
            return
        self.client.delete(t.info_hash, delete_files=True)
        self.ledger.set_status(rec.id, DownloadStatus.REMOVED, expect=(DownloadStatus.FILED,), error=None)
        self.report.removed.append(rec.id)
        _log.info("Arrivals: download %d (%s) stopped at its seed goal; qBittorrent removed the torrent and its "
                  "data", rec.id, rec.title)

    def _filed_files_problem(self, rec: DownloadRecord) -> Optional[str]:
        """Why the filed files are not all in the library at their filed size (None: they are)."""
        plan_id = self.ledger.plan_id(rec.id)
        if plan_id is None or not rec.filed_files:
            return "no filed files recorded"
        try:
            plan = self.journal.get_plan(plan_id)
        except Exception:  # noqa: BLE001 - PlanStateError
            return f"its journal plan {plan_id} is gone"
        for s in (s for s in plan.steps if s.state == "done"):
            name = posixpath.basename(s.dst.replace(os.sep, "/"))
            path = self._current_path(s.dst, plan_id)
            try:
                size = os.stat(path).st_size if os.path.isfile(path) else None
            except OSError:
                size = None
            if size is None:
                if self._held(s.dst, plan_id):
                    continue        # an upgrade replaced it (the owner's choice): the torrent's copy is not needed
                if self._discarded(s.dst, plan_id):
                    continue        # the owner discarded it in Duplicates (or the merge's identical copy): same
                return f"{name} is no longer in the library"
            if size != s.src_size:
                return f"{name} in the library is not the filed size"
            if s.how == "link" and s.dst_ident and _ident(path) not in (None, s.dst_ident):
                return f"{name} in the library is no longer the filed file"
        return None

    def _current_path(self, path: str, after_plan: int) -> str:
        """Where a file filed at *path* is now: followed through the moves the journal applied after plan *after_plan*
        (the renamer's renames to the scheme, their undos) - see :class:`~mangalist.store.journal.MoveFollower`."""
        return self._follower().current_path(path, after_plan)

    def _held(self, path: str, after_plan: int) -> bool:
        """Whether the file filed at *path* was later replaced by an upgrade (held, perhaps since emptied)."""
        return self._follower().held(path, after_plan)

    def _discarded(self, path: str, after_plan: int) -> bool:
        """Whether the file filed at *path* was deleted on purpose by the owner (Duplicates > Discard, a merge's
        identical copy) - recorded in :mod:`mangalist.store.discards`, since a delete is not a journal step."""
        if self._discards is None:
            from ..store.discards import list_discards

            self._discards = list_discards(self.db)
        return bool(self._discards) and self._follower().discarded(path, after_plan, self._discards)

    def _follower(self):
        if self._moves is None:
            from ..store.journal import MoveFollower

            self._moves = MoveFollower(self.journal)
        return self._moves

    def _library_problem(self, rec: DownloadRecord, t: TorrentInfo) -> Optional[str]:
        """Why the torrent may not be deleted yet (None: every filed file verified, its data outside the library)."""
        problem = self._filed_files_problem(rec)
        if problem:
            return problem
        plan = self.journal.get_plan(self.ledger.plan_id(rec.id))
        done = [s for s in plan.steps if s.state == "done"]
        if len(done) != len(rec.filed_files):
            return "the filed files and the journal disagree"
        data = [p for p in (t.content_path, t.save_path) if p]
        for s in done:
            if any(same_or_inside(s.dst, p) for p in data):
                return f"{posixpath.basename(s.dst.replace(os.sep, '/'))} lies inside the torrent's own data"
        for root in self.db.list_roots():
            for p in data:
                if same_or_inside(p, root.path) or same_or_inside(root.path, p):
                    return f"the torrent's data ({p}) overlaps the library root {root.path}; never deleted"
        return None

    # --- outcomes ----------------------------------------------------------------------------------

    def _fail(self, rec: DownloadRecord, why: str, *, filed_files: Optional[Sequence[str]] = None,
              copied: Optional[bool] = None) -> DownloadRecord:
        out = self.ledger.set_status(rec.id, DownloadStatus.FAILED, expect=(rec.status,), error=why,
                                     filed_files=filed_files, copied=copied)
        self.report.failed.append((rec.id, why))
        _log.warning("Arrivals: download %d (%s) FAILED: %s", rec.id, rec.title, why)
        return out

    def _wait(self, rec: DownloadRecord, why: str) -> None:
        self.report.waiting.append((rec.id, why))

    def _note(self, rec: DownloadRecord, note: Optional[str]) -> None:
        """Keep why a FILED record is not removed (or None: plainly seeding) on the record, for the downloads list."""
        if rec.status != DownloadStatus.FILED or (rec.error or None) == (note or None):
            return
        try:
            self.ledger.set_status(rec.id, DownloadStatus.FILED, expect=(DownloadStatus.FILED,), error=note)
        except StatusConflict:      # changed meanwhile (e.g. removed by a pass at the same moment): nothing to note
            pass

    # --- Remove now (the owner's own decision for one filed download) ---------------------------------

    def remove_now(self, record_id: int) -> DownloadRecord:
        """The owner chose to remove a filed download's torrent now (e.g. one stopped by hand before its seed goal):
        the same library check as Remove Completed, then qBittorrent deletes the torrent and its downloaded copy.
        The library files stay. Raises :class:`RemoveRefused` with the reason when it cannot."""
        rec = self.ledger.get(record_id)
        if rec is None or rec.status != DownloadStatus.FILED:
            raise RemoveRefused("only a filed download can be removed")
        t = next((x for x in self.client.torrents(QBITTORRENT_CATEGORY)
                  if x.info_hash.lower() == rec.info_hash.lower()), None)
        if t is None:
            out = self.ledger.set_status(rec.id, DownloadStatus.REMOVED, expect=(DownloadStatus.FILED,),
                                         error="removed in qBittorrent, not by MangaList")
            _log.info("Arrivals: download %d (%s): Remove now - already gone from qBittorrent", rec.id, rec.title)
            return out
        problem = self._library_problem(rec, t)
        if problem:
            _log.warning("Arrivals: download %d (%s): Remove now refused: %s", rec.id, rec.title, problem)
            raise RemoveRefused(problem)
        self.client.delete(t.info_hash, delete_files=True)
        out = self.ledger.set_status(rec.id, DownloadStatus.REMOVED, expect=(DownloadStatus.FILED,),
                                     error="removed by you (Remove now)")
        _log.info("Arrivals: download %d (%s): Remove now - qBittorrent removed the torrent and its downloaded copy; "
                  "the library files stay", rec.id, rec.title)
        return out
