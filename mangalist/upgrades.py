"""Chapter-to-volume upgrades: after a volume is filed, the chapter files it replaces (owner decisions, 2026-10-09).

**Which chapters a filed volume replaces** (:func:`chapters_replaced`) comes from MangaPixer's volume list only (the
export's ``volumes.items``, each volume with the chapters it collects). A chapter file is replaced only when EVERY
chapter it holds lies in a FILED volume's range - a range file (``Ch. 1-5``) across two volumes needs both. Numbers stay
exact decimals: a chapter is in a volume when ``from <= n <= to`` (the inventory's rule: ``45.5`` is in ``38-46``), and
the parts of a split chapter (:func:`mangalist.split_chapters.splits_of`: ``10.1``, ``10.2`` are chapter 10) belong to
the volume that holds their chapter - so ``10.1`` is in a volume ending at ``10``, while an extra ``10.5`` is not.
Never replaced: a chapter of a volume that was not filed, a file the volume list does not fully cover, a volume / extra
/ unknown file, a chapter whose own name says another volume (``Vol. 2 Ch. 10`` against a list that puts 10 in volume
1), a chapter number held in more than one folder (``Season 1`` / ``Season 2`` restarting), and everything of a
series MangaPixer has no volume list for. Only the series' own files count (a nested series folder is another series).

**What happens to them** is the owner's setting (Settings > Automation, ``upgrades.replaced_mode``):

- ``holding`` (default): moved through the journal (``hold`` steps, no-replace, write-ahead, the root's lock) into a
  holding folder that lies OUTSIDE every root, every MangaPixer library and the qBittorrent download folder, keeping
  their root-relative layout - "Restore" undoes the plan. The downloads job does this on its own after filing (it is
  reversible). After ``upgrades.holding_days`` days a batch is purged: deleted from the holding folder only.
- ``delete``: nothing happens until the owner confirms the full list in the GUI (Cancel is the default); then every
  file goes through :func:`mangalist.duplicates.delete_checked`, the one guarded delete, under the root's lock. The
  headless job only records the batch as pending - it never deletes.

Before acting on a batch (hold, delete) the filed volume archives must still be in the library at their filed size,
and each chapter file must still be the plain file listed (size + time). Every action is logged. No Qt here.
"""

from __future__ import annotations

import logging
import os
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .downloads.contracts import DownloadStatus
from .knowledge import VolumeInfo, fmt_num, to_decimal
from .split_chapters import splits_of
from .store.replacements import Batch, NewBatch, ReplacedFile, ReplacementConflict, ReplacementStore

_log = logging.getLogger(__name__)

KEY_MODE = "upgrades.replaced_mode"
KEY_HOLDING_FOLDER = "upgrades.holding_folder"
KEY_HOLDING_DAYS = "upgrades.holding_days"
MODE_HOLDING = "holding"
MODE_DELETE = "delete"
DEFAULT_HOLDING_FOLDER = "/data/appdata/mangalist/replaced"   # the container's appdata, on the library's /data mount
DEFAULT_HOLDING_DAYS = 30
MAX_HOLDING_DAYS = 3650
PLAN_REASON = "replaced chapters"
_MAX_RANGE = 1000           # a chapter range file wider than this is never replaced


# --- settings ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplacedSettings:
    mode: str = MODE_HOLDING
    holding_folder: str = DEFAULT_HOLDING_FOLDER
    holding_days: int = DEFAULT_HOLDING_DAYS


def load_settings(db) -> ReplacedSettings:
    mode = db.get_setting(KEY_MODE, MODE_HOLDING)
    folder = db.get_setting(KEY_HOLDING_FOLDER, DEFAULT_HOLDING_FOLDER)
    days = db.get_setting(KEY_HOLDING_DAYS, DEFAULT_HOLDING_DAYS)
    return ReplacedSettings(
        mode=mode if mode in (MODE_HOLDING, MODE_DELETE) else MODE_HOLDING,
        holding_folder=folder.strip() if isinstance(folder, str) and folder.strip() else DEFAULT_HOLDING_FOLDER,
        holding_days=days if isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= MAX_HOLDING_DAYS
        else DEFAULT_HOLDING_DAYS)


def set_mode(db, mode: str) -> None:
    if mode not in (MODE_HOLDING, MODE_DELETE):
        raise ValueError(f"unknown mode {mode!r}")
    db.set_setting(KEY_MODE, mode)
    _log.info("Upgrades: replaced chapters are now %s", "moved to the holding folder" if mode == MODE_HOLDING
              else "deleted after the owner confirms the list")


def set_holding_folder(db, path: str) -> None:
    """Store the holding folder; :class:`ValueError` with the reason when it may not be used (nothing stored)."""
    path = (path or "").strip()
    problem = holding_problem(db, path)
    if problem:
        raise ValueError(problem)
    db.set_setting(KEY_HOLDING_FOLDER, os.path.normpath(path))
    _log.info("Upgrades: holding folder set to %s", os.path.normpath(path))


def set_holding_days(db, days: int) -> None:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_HOLDING_DAYS:
        raise ValueError(f"the holding period must be 1 to {MAX_HOLDING_DAYS} days")
    db.set_setting(KEY_HOLDING_DAYS, days)
    _log.info("Upgrades: held chapters are emptied after %d days", days)


# --- the holding folder ----------------------------------------------------------------------------------------


def _overlap(a: str, b: str) -> bool:
    """*a* and *b* are the same folder or one lies inside the other - lexically or after resolving symlinks."""
    def inside(x: str, y: str) -> bool:
        x, y = os.path.normcase(os.path.normpath(x)), os.path.normcase(os.path.normpath(y))
        return x == y or x.startswith(y.rstrip(os.sep) + os.sep)

    ra, rb = os.path.realpath(a), os.path.realpath(b)
    return inside(a, b) or inside(b, a) or inside(ra, rb) or inside(rb, ra)


def library_folders(db) -> List[Tuple[str, str]]:
    """``(folder, what)`` for every MangaPixer library MangaList can place on disk: a root mapped to a library with a
    trail prefix lies that many folders below the library's own folder (MangaPixer never sends paths)."""
    out: List[Tuple[str, str]] = []
    try:
        from .services.mangapixer import open_cache

        cache = open_cache(db)
        mappings = cache.mappings()
        names = {lib.id: lib.display_name for lib in cache.libraries()}
    except Exception:  # noqa: BLE001 - no MangaPixer data: only the roots are known
        _log.debug("Upgrades: MangaPixer mappings unavailable", exc_info=True)
        return out
    for root in db.list_roots():
        m = mappings.get(root.id)
        if m is None or not m.library_id:
            continue
        folder = os.path.normpath(root.path)
        parts = [p for p in m.prefix if p]
        tail = folder.replace("\\", "/").split("/")[-len(parts):] if parts else []
        if parts and [t.casefold() for t in tail] == [p.casefold() for p in parts]:
            for _ in parts:
                folder = os.path.dirname(folder)
        out.append((folder, f"the MangaPixer library {names.get(m.library_id) or m.library_id!r}"))
    return out


def holding_problem(db, path: Optional[str]) -> Optional[str]:
    """Why *path* may not be the holding folder (None: it may). It must be absolute and lie outside every root,
    every MangaPixer library and the qBittorrent download folder - the same rule as for the download folder."""
    path = (path or "").strip()
    if not path:
        return "no holding folder is set"
    if not os.path.isabs(path):
        return f"the holding folder must be an absolute path ({path})"
    if ".." in path.replace("\\", "/").split("/"):
        return "the holding folder path contains '..'"
    for root in db.list_roots():
        if _overlap(path, root.path):
            return f"the holding folder overlaps the library root {root.path}; it must lie outside every root"
    for folder, what in library_folders(db):
        if _overlap(path, folder):
            return f"the holding folder overlaps {what} ({folder}); it must lie outside every MangaPixer library"
    save_path = db.get_setting("downloads.save_path", None)
    if isinstance(save_path, str) and save_path.strip() and _overlap(path, save_path.strip()):
        return f"the holding folder overlaps the qBittorrent download folder {save_path.strip()}"
    return None


# --- which chapters a filed volume replaces --------------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    replaced: Tuple[Tuple[str, str, str], ...] = ()     # (rel path, chapters text, volume(s) text)
    kept: Tuple[Tuple[str, str], ...] = ()              # (rel path, why it stays)
    notes: Tuple[str, ...] = ()                         # about the volumes (no chapter list for one, ...)


def volume_spans(volumes: Sequence[VolumeInfo], filed: Iterable[Decimal]) -> Tuple[Dict[Decimal, Tuple[Decimal, Decimal]],
                                                                                    List[str]]:
    """The chapter range of each FILED volume, from the volume list; notes for filed volumes it does not map."""
    by_number: Dict[Decimal, VolumeInfo] = {}
    for v in volumes:
        d = to_decimal(v.volume)
        if d is not None and d not in by_number:
            by_number[d] = v
    spans: Dict[Decimal, Tuple[Decimal, Decimal]] = {}
    notes: List[str] = []
    for vol in sorted(set(filed)):
        info = by_number.get(vol)
        lo = to_decimal(info.chapters_from) if info is not None else None
        hi = to_decimal(info.chapters_to) if info is not None else None
        if info is None:
            notes.append(f"volume {fmt_num(vol)} is not in MangaPixer's volume list")
        elif lo is None or hi is None or lo > hi:
            notes.append(f"MangaPixer's volume list does not say which chapters volume {fmt_num(vol)} holds")
        else:
            spans[vol] = (lo, hi)
    return spans, notes


def _numbers(lo: Decimal, hi: Decimal) -> Optional[List[Decimal]]:
    """Every chapter a unit ``lo``-``hi`` holds: both ends and the whole numbers between; None when absurdly wide."""
    if hi < lo:
        lo, hi = hi, lo
    if hi - lo > _MAX_RANGE:
        return None
    out = {lo, hi}
    n = lo.to_integral_value() if lo == lo.to_integral_value() else int(lo) + 1
    while Decimal(n) <= hi:
        out.add(Decimal(n))
        n += 1
    return sorted(out)


def _span_text(numbers: Sequence[Decimal]) -> str:
    return fmt_num(numbers[0]) if len(numbers) == 1 else f"{fmt_num(numbers[0])}-{fmt_num(numbers[-1])}"


def chapters_replaced(units: Sequence, volumes: Sequence[VolumeInfo], filed_volumes: Iterable) -> Coverage:
    """Which of the series' chapter files the *filed_volumes* fully replace (see the module docstring).

    *units*: the series' own unit rows (``rel_path``, ``kind``, ``ch_from`` / ``ch_to``, ``vol_from`` / ``vol_to``);
    *volumes*: MangaPixer's volume list (:class:`~mangalist.knowledge.VolumeInfo`); *filed_volumes*: exact numbers."""
    filed = {d for d in (to_decimal(v) for v in filed_volumes) if d is not None}
    spans, notes = volume_spans(volumes, filed)
    if not spans:
        return Coverage(notes=tuple(notes or ["no volume was filed"]))
    by_file: Dict[str, List] = defaultdict(list)
    for u in units:
        by_file[(u.rel_path or "").replace("\\", "/")].append(u)
    singles: List[Decimal] = []
    ranges: List[Tuple[Decimal, Decimal]] = []
    folders_of: Dict[Decimal, Set[str]] = defaultdict(set)
    numbers_of: Dict[str, Optional[List[Decimal]]] = {}
    for rel, rows in by_file.items():
        if not rows or any(r.kind != "chapter" for r in rows):
            continue
        nums: List[Decimal] = []
        for r in rows:
            lo, hi = to_decimal(r.ch_from), to_decimal(r.ch_to if r.ch_to is not None else r.ch_from)
            if lo is None or hi is None:
                nums = None
                break
            expanded = _numbers(lo, hi)
            if expanded is None:
                nums = None
                break
            nums.extend(expanded)
            if lo == hi:
                singles.append(lo)
            else:
                ranges.append((min(lo, hi), max(lo, hi)))
        numbers_of[rel] = sorted(set(nums)) if nums is not None else None
        for n in nums or ():
            folders_of[n].add(os.path.dirname(rel))
    parts = splits_of(singles, ranges).parts

    def covering(n: Decimal) -> Optional[Decimal]:
        for vol, (lo, hi) in sorted(spans.items()):
            if lo <= n <= hi:
                return vol
            if n in parts and lo <= Decimal(int(n)) <= hi:
                return vol
        return None

    replaced: List[Tuple[str, str, str]] = []
    kept: List[Tuple[str, str]] = []
    for rel in sorted(numbers_of):
        nums = numbers_of[rel]
        if nums is None:
            continue
        vols = {n: covering(n) for n in nums}
        if not any(v is not None for v in vols.values()):
            continue                            # nowhere near a filed volume: not part of this upgrade
        outside = [n for n, v in vols.items() if v is None]
        if outside:
            kept.append((rel, f"chapter {_span_text(outside)} is not in the filed volumes"))
            continue
        in_vols = set(vols.values())
        tags: Set[Decimal] = set()
        for r in by_file[rel]:
            tag_lo, tag_hi = to_decimal(r.vol_from), to_decimal(r.vol_to if r.vol_to is not None else r.vol_from)
            if tag_lo is not None:
                tags.update(_numbers(tag_lo, tag_hi if tag_hi is not None else tag_lo) or [tag_lo])
        if tags and not tags <= in_vols:
            kept.append((rel, f"its name says volume {', '.join(fmt_num(t) for t in sorted(tags))}, the volume list "
                              f"puts it in volume {', '.join(fmt_num(v) for v in sorted(in_vols))}"))
            continue
        elsewhere = [n for n in nums if len(folders_of[n]) > 1]
        if elsewhere:
            kept.append((rel, f"chapter {fmt_num(elsewhere[0])} is in more than one folder (numbering that restarts?)"))
            continue
        replaced.append((rel, _span_text(nums), ", ".join(fmt_num(v) for v in sorted(in_vols))))
    return Coverage(tuple(replaced), tuple(kept), tuple(notes))


# --- files on disk ---------------------------------------------------------------------------------------------


def _iso_from_ns(mtime_ns: int) -> str:
    from .duplicates import iso_from_ns

    return iso_from_ns(mtime_ns)


def _plain_below(path: str, top: str) -> Optional[os.stat_result]:
    """The lstat of *path* when it is a plain file strictly inside *top*, reached without any symlink; else None."""
    top = os.path.normpath(top)
    path = os.path.normpath(path)
    if not os.path.normcase(path).startswith(os.path.normcase(top.rstrip(os.sep)) + os.sep):
        return None
    here, st = top, None
    for part in path[len(top.rstrip(os.sep)) + 1:].split(os.sep):
        here = os.path.join(here, part)
        try:
            st = os.lstat(here)
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode):
            return None
    return st if st is not None and stat.S_ISREG(st.st_mode) else None


def _still_listed(f: ReplacedFile, root: str) -> bool:
    st = _plain_below(f.path, root)
    return st is not None and st.st_size == f.size and _iso_from_ns(st.st_mtime_ns) == f.modified


def _root_configured(db, root: str) -> bool:
    return bool(root) and any(os.path.normcase(os.path.normpath(r.path)) == os.path.normcase(os.path.normpath(root))
                              for r in db.list_roots())


def _volumes_problem(batch: Batch) -> Optional[str]:
    """Why the filed volumes no longer stand in for the chapters (None: every one is in the library at its size)."""
    if not batch.volume_files:
        return "no filed volume is recorded"
    for path, size in batch.volume_files:
        try:
            st = os.lstat(path)
        except OSError:
            return f"the volume {os.path.basename(path)} is no longer in the library"
        if not stat.S_ISREG(st.st_mode) or st.st_size != size:
            return f"the volume {os.path.basename(path)} in the library is not the filed file any more"
    return None


# --- examining a filed download ----------------------------------------------------------------------------------


def _knowledge_volumes(db, series) -> Tuple[Optional[Sequence[VolumeInfo]], str]:
    """MangaPixer's volume list of *series* (its own item or the nearest ancestor's), or (None, why not)."""
    from .knowledge import MANGAPIXER_SERIES_STATES, LINK_NEEDS_REVIEW, from_mangapixer_item
    from .services.mangapixer import open_cache
    from .services.mangapixer.resolve import resolve

    try:
        res = resolve(open_cache(db), series.root_id, series.rel_path)
    except Exception as exc:  # noqa: BLE001 - a broken cache means "not known", never a guess
        return None, f"MangaPixer's data could not be read ({type(exc).__name__})"
    if res is None:
        return None, "MangaPixer does not know this series"
    if res.link_state not in MANGAPIXER_SERIES_STATES or res.link_state == LINK_NEEDS_REVIEW:
        return None, f"MangaPixer's link is {res.link_state or 'unknown'}, not matched"
    volumes = from_mangapixer_item(res.item).volumes
    if not volumes:
        return None, "MangaPixer has no volume list for this series"
    return volumes, ""


def examine(db, ledger, record) -> NewBatch:
    """The batch for one filed download: the chapter files its filed volumes replace, as they are on disk now."""
    from .downloads.arrivals import volumes_of
    from .downloads.placement import locate_series
    from .store.journal import Journal, PlanStateError

    def nothing(why: str, status: str = "nothing", **kw) -> NewBatch:
        return NewBatch(download_id=record.id, series_id=record.series_id, status=status, error=why, **kw)

    try:
        series, root, series_dir = locate_series(db, record.series_id)
    except LookupError as exc:
        return nothing(str(exc))
    plan_id = ledger.plan_id(record.id)
    if plan_id is None:
        return nothing("no filing plan is recorded")
    try:
        plan = Journal(db).get_plan(plan_id)
    except PlanStateError as exc:
        return nothing(str(exc))
    volume_files = [(s.dst, int(s.src_size or 0)) for s in plan.steps if s.op == "link" and s.state == "done"]
    filed: Set[Decimal] = set()
    for path, _ in volume_files:
        filed |= volumes_of(os.path.basename(path), "volumes")
    common = dict(root_path=root.path, series_dir=series_dir, volumes=[fmt_num(v) for v in sorted(filed)],
                  volume_files=volume_files)
    if not filed:
        return nothing("no volume was filed", **common)
    units = db.list_units(series.id)
    if not any(u.kind == "chapter" for u in units):
        # a series held as volumes only: missing volumes were filed, no chapter can be replaced (owner, 2026-10-09:
        # "don't both of those series only have volumes in the folder anyway?") - say that, not MangaPixer's gaps
        return nothing("the series holds no chapter files", **common)
    volumes, why = _knowledge_volumes(db, series)
    if volumes is None:
        return nothing(f"{why}; no chapter is replaced", **common)
    coverage = chapters_replaced(units, volumes, filed)
    files: List[ReplacedFile] = []
    kept = list(coverage.kept)
    for rel, chapters, volume in coverage.replaced:
        path = os.path.join(series_dir, *rel.split("/"))
        st = _plain_below(path, root.path)
        if st is None:
            kept.append((rel, "not a plain file in the library now (gone since the last scan?)"))
            continue
        files.append(ReplacedFile(path=path, rel=rel, size=st.st_size, modified=_iso_from_ns(st.st_mtime_ns),
                                  chapters=chapters, volume=volume))
    note = "; ".join(coverage.notes) or None
    if not files:
        return nothing(note or "no chapter file is fully covered by the filed volumes", kept=kept, **common)
    return NewBatch(download_id=record.id, series_id=record.series_id, status="pending", files=files, kept=kept,
                    mode=load_settings(db).mode, error=note, **common)


# --- the downloads job's hook -----------------------------------------------------------------------------------


@dataclass
class UpgradeReport:
    examined: int = 0
    held: List[int] = field(default_factory=list)        # batch ids moved to the holding folder this pass
    pending: List[int] = field(default_factory=list)     # batch ids waiting (the owner's confirmation, or a retry)
    purged: List[int] = field(default_factory=list)
    failed: List[Tuple[int, str]] = field(default_factory=list)
    files_moved: int = 0

    def summary(self) -> str:
        parts = []
        if self.files_moved:
            parts.append(f"{self.files_moved} replaced chapter file(s) moved to the holding folder")
        if self.pending:
            parts.append(f"{len(self.pending)} replacement(s) waiting for you")
        if self.purged:
            parts.append(f"{len(self.purged)} held batch(es) emptied")
        if self.failed:
            parts.append(f"{len(self.failed)} replacement(s) failed")
        return "; ".join(parts)


def after_filing(db, ledger, *, journal=None, now: Optional[datetime] = None) -> UpgradeReport:
    """The downloads job's step after each pass: look at every download filed since upgrades exist that has no
    batch yet; in holding mode move its replaced chapters now (and retry batches that had to wait); in delete mode
    only record them as pending for the owner. Then purge held batches whose time is up."""
    from .store.journal import Journal

    journal = journal if journal is not None else Journal(db)
    store = ReplacementStore(db)
    report = UpgradeReport()
    done = [r for r in ledger.all_records() if r.status in (DownloadStatus.FILED, DownloadStatus.REMOVED)]
    for download_id in store.unexamined([r.id for r in done]):
        record = ledger.get(download_id)
        try:
            new = examine(db, ledger, record)
            batch = store.create(new)
        except Exception as exc:  # noqa: BLE001 - one download must not stop the others; looked at again next pass
            _log.exception("Upgrades: looking at download %d failed", download_id)
            report.failed.append((download_id, f"{type(exc).__name__}: {exc}"))
            continue
        report.examined += 1
        if batch.status == "nothing":
            _log.info("Upgrades: download %d (%s): no chapter replaced (%s)", record.id, record.title, batch.error)
            continue
        _log.info("Upgrades: download %d (%s): %d chapter file(s) replaced by volume(s) %s: %s", record.id,
                  record.title, len(batch.files), ", ".join(batch.volumes), ", ".join(f.rel for f in batch.files))
        for rel, why in batch.kept:
            _log.info("Upgrades: download %d: kept %s (%s)", record.id, rel, why)
    settings = load_settings(db)
    for batch in store.with_status("pending"):
        if settings.mode != MODE_HOLDING:
            report.pending.append(batch.id)
            continue
        after = hold_batch(db, batch.id, journal=journal, now=now)
        if after.status == "held":
            report.held.append(after.id)
            report.files_moved += sum(1 for s in journal.get_plan(after.plan_id).steps if s.state == "done")
        elif after.status == "pending":
            report.pending.append(after.id)
        elif after.status == "failed":
            report.failed.append((after.id, after.error or "failed"))
    report.purged = purge_expired(db, journal=journal, now=now)
    return report


# --- holding ---------------------------------------------------------------------------------------------------


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def hold_batch(db, batch_id: int, *, journal=None, now: Optional[datetime] = None) -> Batch:
    """Move a pending batch's chapter files into the holding folder (one journal plan). Waits (stays pending, with
    the reason) while the holding folder is unusable or the root is busy; fails when its volumes are gone."""
    from .store.journal import Journal, PlanStateError, StepRefused
    from .store.lock import LockError

    journal = journal if journal is not None else Journal(db)
    store = ReplacementStore(db)
    batch = store.get(batch_id)
    if batch is None or batch.status != "pending":
        raise ReplacementConflict(f"replaced-chapters batch {batch_id} is not pending")
    settings = load_settings(db)

    def wait(why: str) -> Batch:
        _log.warning("Upgrades: batch %d not moved yet: %s", batch.id, why)
        return store.update(batch.id, expect=("pending",), mode=MODE_HOLDING, error=why)

    def fail(why: str) -> Batch:
        _log.warning("Upgrades: batch %d: %s; nothing moved", batch.id, why)
        return store.update(batch.id, expect=("pending",), status="failed", error=why)

    plan = journal.get_plan(batch.plan_id) if batch.plan_id is not None else None
    if plan is None:
        problem = holding_problem(db, settings.holding_folder)
        if problem:
            return wait(problem)
        problem = _volumes_problem(batch)
        if problem:
            return fail(problem)
        root = batch.root_path or ""
        if not _root_configured(db, root):
            return fail("its library root is no longer configured")
        files = [f for f in batch.files if _still_listed(f, root)]
        for f in batch.files:
            if f not in files:
                _log.warning("Upgrades: batch %d: %s changed or went since it was listed; left alone", batch.id, f.rel)
        if not files:
            return fail("every chapter file changed or went since it was listed")
        stamp = _now(now).astimezone(timezone.utc).strftime("%Y-%m-%d")
        holding_dir = os.path.join(os.path.normpath(settings.holding_folder), f"{stamp} batch {batch.id}",
                                   os.path.basename(os.path.normpath(root)) or "root")
        try:
            plan = journal.plan_holding(PLAN_REASON, [f.path for f in files], root_path=root, holding_dir=holding_dir,
                                        note=f"replaced by volume(s) {', '.join(batch.volumes)} (download "
                                             f"{batch.download_id})")
        except StepRefused as exc:
            return fail(f"the journal refused the move: {exc}")
        batch = store.update(batch.id, expect=("pending",), mode=MODE_HOLDING, plan_id=plan.id,
                             holding_dir=os.path.dirname(holding_dir))
    try:
        if plan.status in ("applying", "undoing"):
            journal.recover(only=[plan.id])
            plan = journal.get_plan(plan.id)
        if plan.status in ("planned", "interrupted", "failed"):
            plan = journal.apply(plan.id)
    except LockError as exc:
        return wait(f"the library root is busy ({exc}); tried again on the next check")
    except PlanStateError as exc:
        return fail(f"its journal plan {plan.id}: {exc}")
    moved = [s for s in plan.steps if s.state == "done"]
    failed_step = next((s for s in plan.steps if s.state == "failed"), None)
    if not moved:
        return fail(f"nothing moved: {failed_step.error if failed_step else plan.status} - the chapters stay in the "
                    "library; check the holding folder (Settings > Automation) and try again")
    error = None
    if failed_step is not None:
        error = (f"{len(moved)} of {len(plan.steps)} moved; stopped at {os.path.basename(failed_step.src)}: "
                 f"{failed_step.error} (the rest stay in the library)")
    purge_after = _iso(_now(now) + timedelta(days=settings.holding_days))
    batch = store.update(batch.id, expect=("pending",), status="held", purge_after=purge_after, error=error)
    for s in moved:
        _log.info("Upgrades: batch %d: moved %s -> %s (journal plan %d)", batch.id, s.src, s.dst, plan.id)
    _log.info("Upgrades: batch %d: %d chapter file(s) held in %s until %s", batch.id, len(moved), batch.holding_dir,
              purge_after)
    return batch


def restore_batch(db, batch_id: int, *, journal=None) -> Batch:
    """Move a held batch's files back where they were (the journal plan undone, no-replace). Partly blocked (a file
    of that name is back in the library, a folder is gone): stays held, with the reason; Restore can be tried again."""
    from .store.journal import Journal, PlanStateError
    from .store.lock import LockError

    journal = journal if journal is not None else Journal(db)
    store = ReplacementStore(db)
    batch = store.get(batch_id)
    if batch is None or batch.status != "held" or batch.plan_id is None:
        raise ReplacementConflict(f"replaced-chapters batch {batch_id} is not held")
    try:
        plan = journal.undo(batch.plan_id)
    except LockError as exc:
        return store.update(batch.id, expect=("held",), error=f"the library root is busy ({exc}); try again")
    except PlanStateError as exc:
        return store.update(batch.id, expect=("held",), error=f"cannot restore: {exc}")
    if plan.status == "undone":
        _log.info("Upgrades: batch %d restored: %d file(s) moved back into %s", batch.id,
                  sum(1 for s in plan.steps if s.state == "undone"), batch.series_dir)
        return store.update(batch.id, expect=("held",), status="restored", error=None)
    stuck = next((s for s in plan.steps if s.state == "done" and s.error), None)
    why = f"{os.path.basename(stuck.src)}: {stuck.error}" if stuck is not None else plan.status
    _log.warning("Upgrades: batch %d only partly restored: %s", batch.id, why)
    return store.update(batch.id, expect=("held",), error=f"not everything was restored - {why}")


def purge_batch(db, batch_id: int, *, journal=None, reason: str = "holding period over") -> Batch:
    """Delete a held batch's files from the holding folder - the only delete there. Each file must be a done
    ``hold`` step of the batch's plan, a plain file strictly inside the batch's own holding folder (no symlink on the
    way) with the size it had when it was moved, and the holding folder must still lie outside every root and
    library. Anything else is left where it is. Never touches a library file. ``reason`` is what the log says for
    each deletion: the holding period ending, or the owner emptying it early."""
    from .store.journal import Journal

    journal = journal if journal is not None else Journal(db)
    store = ReplacementStore(db)
    batch = store.get(batch_id)
    if batch is None or batch.status != "held" or batch.plan_id is None or not batch.holding_dir:
        raise ReplacementConflict(f"replaced-chapters batch {batch_id} is not held")
    top = os.path.normpath(batch.holding_dir)
    problem = holding_problem(db, top) if os.path.isabs(top) else "its holding folder is not an absolute path"
    if problem:
        _log.warning("Upgrades: batch %d not emptied: %s", batch.id, problem)
        return store.update(batch.id, expect=("held",), error=f"not emptied: {problem}")
    plan = journal.get_plan(batch.plan_id)
    left: List[str] = []
    removed = 0
    for s in plan.steps:
        if s.op != "hold" or s.state != "done":
            continue
        st = _plain_below(s.dst, top)
        if st is None or st.st_size != s.src_size:
            left.append(os.path.basename(s.dst))
            _log.warning("Upgrades: batch %d: %s is not the held file (gone, moved or changed); left alone",
                         batch.id, s.dst)
            continue
        try:
            os.remove(s.dst)
        except OSError as exc:
            left.append(os.path.basename(s.dst))
            _log.warning("Upgrades: batch %d: could not delete %s (%s)", batch.id, s.dst, exc.strerror or exc)
            continue
        removed += 1
        _log.info("Upgrades: batch %d: %s, deleted %s", batch.id, reason, s.dst)
    for folder, _dirs, _files in sorted(os.walk(top), key=lambda t: -len(t[0])):
        try:
            os.rmdir(folder)                # only empty folders: never content
        except OSError:
            pass
    error = f"left in the holding folder: {', '.join(left)}" if left else None
    _log.info("Upgrades: batch %d emptied (%d file(s) deleted from the holding folder)", batch.id, removed)
    return store.update(batch.id, expect=("held",), status="purged", error=error)


def purge_expired(db, *, journal=None, now: Optional[datetime] = None) -> List[int]:
    """Empty every held batch whose holding period is over. Returns their ids."""
    out = []
    when = _now(now)
    for batch in ReplacementStore(db).with_status("held"):
        try:
            due = datetime.fromisoformat(batch.purge_after) if batch.purge_after else None
        except ValueError:
            due = None
        if due is None or due > when:
            continue
        try:
            after = purge_batch(db, batch.id, journal=journal)
        except Exception:  # noqa: BLE001 - one batch must not stop the others
            _log.exception("Upgrades: emptying batch %d failed", batch.id)
            continue
        if after.status == "purged":
            out.append(after.id)
    return out


def volumes_in_library_problem(db, batch: Batch) -> Optional[str]:
    """Why a held batch may not be emptied early (None: it may): every volume archive that replaced its chapters must
    still be in the library - a plain file of the size it was filed with, inside a root that is still configured
    (owner, 2026-10-09: empty the holding folder "as long as the contents are filled in the real roots")."""
    if not batch.volume_files:
        return "it does not record which volume files replaced the chapters"
    roots = [os.path.normpath(r.path) for r in db.list_roots()]
    for path, size in batch.volume_files:
        top = next((r for r in roots if os.path.normcase(os.path.normpath(path)).startswith(
            os.path.normcase(r.rstrip(os.sep)) + os.sep)), None)
        name = os.path.basename(path)
        if top is None:
            return f"{name} is not inside a configured library root"
        st = _plain_below(path, top)
        if st is None:
            return f"{name} is no longer in the library"
        if st.st_size != size:
            return f"{name} has changed since it was filed"
    return None


def empty_now(db, batch_id: int, *, journal=None) -> Batch:
    """Empty one held batch from the holding folder before its period is over - only while the volumes that replaced
    its chapters are still in the library; then the same guarded :func:`purge_batch`. Raises
    :class:`ReplacementConflict` with the reason otherwise (nothing is deleted)."""
    batch = ReplacementStore(db).get(batch_id)
    if batch is None or batch.status != "held":
        raise ReplacementConflict(f"replaced-chapters batch {batch_id} is not held")
    problem = volumes_in_library_problem(db, batch)
    if problem:
        _log.warning("Upgrades: batch %d not emptied early: %s", batch.id, problem)
        raise ReplacementConflict(f"not emptied: {problem}")
    _log.info("Upgrades: batch %d: emptying the holding folder now, on the owner's word", batch.id)
    return purge_batch(db, batch.id, journal=journal, reason="emptied early, on the owner's word")


def empty_all_now(db, *, journal=None) -> Tuple[List[int], List[Tuple[int, str]]]:
    """:func:`empty_now` for every held batch. Returns (emptied ids, [(id, why not)])."""
    emptied: List[int] = []
    refused: List[Tuple[int, str]] = []
    for batch in ReplacementStore(db).with_status("held"):
        try:
            after = empty_now(db, batch.id, journal=journal)
        except ReplacementConflict as exc:
            refused.append((batch.id, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - one batch must not stop the others
            _log.exception("Upgrades: emptying batch %d failed", batch.id)
            refused.append((batch.id, f"{type(exc).__name__}: {exc}"))
            continue
        (emptied.append(after.id) if after.status == "purged" else refused.append((after.id, after.error or "")))
    return emptied, refused


# --- the owner's answers (GUI) ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class DeleteOutcome:
    path: str
    deleted: bool
    reason: str = ""


def delete_confirmed(db, batch_id: int, confirmed: Sequence[str]) -> List[DeleteOutcome]:
    """Delete the files of a pending batch after the owner confirmed the list (*confirmed*: the paths shown). Refused
    as a whole when the list is not exactly the batch's files or a filed volume is no longer in the library; each
    file then goes through :func:`mangalist.duplicates.delete_checked` under the root's lock."""
    from .duplicates import DiscardRefused, delete_checked
    from .store.lock import LockError, RootLock

    store = ReplacementStore(db)
    batch = store.get(batch_id)
    if batch is None or batch.status != "pending":
        raise ReplacementConflict(f"replaced-chapters batch {batch_id} is not waiting for a confirmation")

    def refuse_all(why: str) -> List[DeleteOutcome]:
        _log.warning("Upgrades: batch %d not deleted: %s", batch.id, why)
        store.update(batch.id, expect=("pending",), error=why)
        return [DeleteOutcome(f.path, False, why) for f in batch.files]

    listed = {os.path.normpath(f.path) for f in batch.files}
    if {os.path.normpath(p) for p in confirmed} != listed or len(confirmed) != len(listed):
        return refuse_all("the confirmed list is not the batch's list of files")
    problem = _volumes_problem(batch)
    if problem:
        return refuse_all(problem)
    root = batch.root_path or ""
    if not _root_configured(db, root):
        return refuse_all("its library root is no longer configured")
    lock = RootLock(root)
    try:
        lock.acquire()
    except LockError as exc:
        return refuse_all(f"the library root is busy ({exc}); try again")
    out: List[DeleteOutcome] = []
    try:
        for f in batch.files:
            try:
                delete_checked(f.path, f.size, f.modified, [root])
            except DiscardRefused as exc:
                _log.warning("Upgrades: batch %d: not deleted %s (%s)", batch.id, f.path, exc)
                out.append(DeleteOutcome(f.path, False, str(exc)))
                continue
            _log.info("Upgrades: batch %d: deleted %s (%d bytes, chapter %s, replaced by volume %s) - confirmed by "
                      "the owner", batch.id, f.path, f.size, f.chapters, f.volume)
            out.append(DeleteOutcome(f.path, True))
    finally:
        lock.release()
    refused = [o for o in out if not o.deleted]
    error = (f"{len(refused)} not deleted: " + "; ".join(f"{os.path.basename(o.path)} ({o.reason})"
                                                          for o in refused[:5])) if refused else None
    store.update(batch.id, expect=("pending",), status="deleted", error=error)
    return out


def retry_batch(db, batch_id: int, *, journal=None, now: Optional[datetime] = None) -> Batch:
    """A failed batch, asked again: pending with a fresh plan (the files are listed again as they are now when moved);
    in holding mode it is moved right away."""
    store = ReplacementStore(db)
    batch = store.update(batch_id, expect=("failed",), status="pending", plan_id=None, holding_dir=None, error=None)
    _log.info("Upgrades: batch %d asked again by the owner", batch.id)
    if load_settings(db).mode == MODE_HOLDING:
        return hold_batch(db, batch.id, journal=journal, now=now)
    return batch


def keep_batch(db, batch_id: int) -> Batch:
    """The owner keeps a batch's chapter files (pending, or failed): nothing is moved or deleted, and it is not asked
    again."""
    batch = ReplacementStore(db).update(batch_id, expect=("pending", "failed"), status="declined", error=None)
    _log.info("Upgrades: batch %d: the owner keeps the %d chapter file(s)", batch.id, len(batch.files))
    return batch


class ReplacedChapters:
    """The replaced-chapters actions for the GUI, over one library database (every method may block: the GUI calls
    them off the UI thread, except :meth:`settings`)."""

    def __init__(self, db, journal_factory: Optional[Callable[[object], object]] = None):
        self.db = db
        self._journal_factory = journal_factory

    def _journal(self):
        from .store.journal import Journal

        return (self._journal_factory or Journal)(self.db)

    def settings(self) -> ReplacedSettings:
        return load_settings(self.db)

    def open_batches(self) -> List[Batch]:
        """Pending, failed and held batches, oldest first (what the owner may still act on)."""
        return ReplacementStore(self.db).with_status("pending", "failed", "held")

    def series_title(self, batch: Batch) -> str:
        return os.path.basename(os.path.normpath(batch.series_dir)) if batch.series_dir else f"Series #{batch.series_id}"

    def hold(self, batch_id: int) -> Batch:
        return hold_batch(self.db, batch_id, journal=self._journal())

    def restore(self, batch_id: int) -> Batch:
        return restore_batch(self.db, batch_id, journal=self._journal())

    def delete(self, batch_id: int, confirmed: Sequence[str]) -> List[DeleteOutcome]:
        return delete_confirmed(self.db, batch_id, confirmed)

    def keep(self, batch_id: int) -> Batch:
        return keep_batch(self.db, batch_id)

    def retry(self, batch_id: int) -> Batch:
        return retry_batch(self.db, batch_id, journal=self._journal())

    def empty_now(self, batch_id: int) -> Batch:
        return empty_now(self.db, batch_id, journal=self._journal())

    def empty_all_now(self) -> Tuple[List[int], List[Tuple[int, str]]]:
        return empty_all_now(self.db, journal=self._journal())
