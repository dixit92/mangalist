"""Duplicate files: the same chapter (or volume) number in more than one file of one folder - and the confirmed
discard of the ones the owner does not keep.

Finding is MangaPixer's "duplicate numbers" rule (``MissingUnits`` / ``DuplicateUnits``, MangaPixer 1.31.0) applied to the
library database's units, so MangaList and MangaPixer agree on what a folder holds:

- Per FOLDER: only files in the same folder are compared (``Season 1`` and ``Season 2`` both holding a chapter 1 are
  numbering that restarts, not duplicates). The folder is the one that holds the file: the series folder or a sub-folder.
- A file that states a chapter is that chapter, even if it also names a volume (``v03 c012`` is chapter 12); otherwise a
  file that states a volume is that volume. Chapters and volumes are counted apart. Two volume files with the same number
  (two editions) ARE duplicates.
- NOT duplicates: a range file (``Ch. 1-5``) next to a single chapter, a name that states no number (an extra, an
  unknown), and the parts of a split chapter (``2.1`` and ``2.2``, or a file ``2`` next to its parts,
  :func:`mangalist.split_chapters.splits_of`): they are different numbers, so they never meet. A part that is itself in two
  files (``2.1`` twice) is a duplicate of that part.
- A number is compared as an exact decimal, never a float: ``12`` and ``12.0`` are the same chapter, ``12`` and ``12.5`` are not.
- MangaList's own safety rule on top (owner, 2026-10-09): files are copies only when their names differ by tags alone
  (``[group]``, ``(Digital)``, a year, a copy marker). When anything else differs by a number - ``009 Vol 01`` next to
  ``008 Vol 01`` (chapters the parser does not read), ``Season 1 v01`` next to ``Season 2 v01`` - they are different
  units and never listed. FMD2's download index (``0002 [Vol. 0001 Ch. 1]``) is not such a number.
- The usual group: when the copies come from different scanlation groups, the group of the neighbouring numbers (else
  the folder's most used group) - a series keeps its translation style. The default Keep prefers it.

The listing is the database as of the last scan, with each file's size and time read from the disk now (a file that has
gone, or is not a plain file, is left out) - so a list built after a discard is already right without a rescan.

Deleting is an owner-approved exception to the journal's "moves only" rule (2026-10-08: no holding folder, only an explicit
confirmation) for this action - and, since the volumes cycle, for the confirmed delete of chapters a filed volume replaced
(``mangalist.upgrades``, which calls :func:`delete_checked` the same way). It lives in :func:`delete_checked` (one file, every guard) and :func:`discard_duplicates`
(the root's ``.mangalist.lock`` and "keep at least one of each number" around it). Nothing else in MangaList deletes
a library file. No Qt.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Collection, Dict, Iterable, List, Optional, Sequence, Tuple

from .gui.shell import DuplicateFile, DuplicateGroup
from .inventory import to_number
from .parsing.model import Layer
from .parsing.parser import parse_name
from .store.lock import LOCK_NAME, LockError, RootLock

_log = logging.getLogger(__name__)

KIND_CHAPTER = "chapter"
KIND_VOLUME = "volume"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# --- numbers and times -----------------------------------------------------------------------------------

def canonical_number(value: Decimal) -> str:
    """``Decimal("12.50")`` -> ``"12.5"``, ``Decimal("3")`` -> ``"3"`` (exact; the text the lists show)."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def iso_from_ns(mtime_ns: int) -> str:
    """A file time as ISO 8601 UTC with microseconds, from integer nanoseconds (so the same stat always gives the same text)."""
    return (_EPOCH + timedelta(microseconds=mtime_ns // 1000)).isoformat(timespec="microseconds")


def _single(lo: Optional[str], hi: Optional[str]) -> Optional[Decimal]:
    """The number a unit states when it is ONE number (``hi`` equal to ``lo`` or absent); None for a range or no number."""
    start = to_number(lo)
    if start is None:
        return None
    end = to_number(hi)
    return start if end is None or end == start else None


@dataclass(frozen=True)
class _Stated:
    kind: str
    number: Decimal
    group: Optional[str]


def stated_unit(rows: Iterable) -> Optional[_Stated]:
    """The one chapter / volume number an archive states, from its unit rows (anything with ``kind``, ``vol_from``,
    ``vol_to``, ``ch_from``, ``ch_to``, ``group_name``), or None: no number, or a range (MangaPixer's ``DuplicateUnits.Find``)."""
    rows = list(rows)
    chapters = [r for r in rows if r.kind == "chapter" and to_number(r.ch_from) is not None]
    if chapters:                         # a file that states a chapter is that chapter, whatever volume it names
        r = chapters[0]
        n = _single(r.ch_from, r.ch_to)
        return None if n is None else _Stated(KIND_CHAPTER, n, _clean_group(r.group_name))
    volumes = [r for r in rows if r.kind == "volume" and to_number(r.vol_from) is not None]
    if volumes:
        r = volumes[0]
        n = _single(r.vol_from, r.vol_to)
        return None if n is None else _Stated(KIND_VOLUME, n, _clean_group(r.group_name))
    return None


def _clean_group(name: Optional[str]) -> Optional[str]:
    """The group from the name; a bare number (``[2]``, MangaPixer's copy marker) is not a group."""
    text = (name or "").strip()
    return None if not text or text.isdigit() else text


# --- finding ---------------------------------------------------------------------------------------------

_QUERY = ("SELECT u.series_id, u.rel_path, u.kind, u.vol_from, u.vol_to, u.ch_from, u.ch_to, u.group_name,"
          " s.rel_path AS series_rel, s.root_id AS root_id"
          " FROM units u JOIN series s ON s.id = u.series_id"
          " WHERE s.status = 'present'{where} ORDER BY u.series_id, u.rel_path, u.seq")


def _slash_parts(rel: str) -> List[str]:
    return [p for p in str(rel).replace("\\", "/").split("/") if p and p != "."]


def find_duplicate_files(db, root_ids: Optional[Collection[int]] = None) -> List[DuplicateGroup]:
    """Every number held by more than one file of one folder, in the roots *root_ids* (None: all roots).

    Groups come sorted by series title, folder, volumes before chapters, number ascending; the files of a group by path.
    """
    roots = {r.id: r for r in db.list_roots() if root_ids is None or r.id in set(root_ids)}
    if not roots:
        return []
    ids = sorted(roots)
    where = f" AND s.root_id IN ({','.join('?' * len(ids))})"
    # (series id, container folder rel to the series, archive rel path) -> its unit rows
    series_info: Dict[int, Tuple[int, str]] = {}
    per_archive: Dict[Tuple[int, str], List] = {}
    with db.connect() as con:
        for row in con.execute(_QUERY.format(where=where), ids):
            series_info[row["series_id"]] = (row["root_id"], row["series_rel"])
            per_archive.setdefault((row["series_id"], row["rel_path"]), []).append(SimpleNamespace(**dict(row)))

    # (series id, container, kind, number) -> [(archive rel, group)]
    buckets: Dict[Tuple[int, str, str, Decimal], List[Tuple[str, Optional[str]]]] = {}
    for (sid, rel), rows in per_archive.items():
        stated = stated_unit(rows)
        if stated is None:
            continue
        container = "/".join(_slash_parts(rel)[:-1])
        buckets.setdefault((sid, container, stated.kind, stated.number), []).append((rel, stated.group))

    # (series id, container, kind) -> {number: [groups]} - for the usual group of a duplicate number
    groups_by_number: Dict[Tuple[int, str, str], Dict[Decimal, List[str]]] = {}
    for (sid, container, kind, number), members in buckets.items():
        groups_by_number.setdefault((sid, container, kind), {})[number] = [g for _rel, g in members if g]

    out: List[DuplicateGroup] = []
    for (sid, container, kind, number), members in buckets.items():
        if len(members) < 2:
            continue
        root_id, series_rel = series_info[sid]
        series_folder = Path(roots[root_id].path, *_slash_parts(series_rel))
        for same in _alike(members, number).values():
            if len(same) < 2:
                continue
            files = []
            for rel, group in sorted(same):
                seen = _stat_plain_file(Path(series_folder, *_slash_parts(rel)))
                if seen is not None:
                    files.append(DuplicateFile(path=str(seen[0]), size=seen[1], modified=iso_from_ns(seen[2]),
                                               group=group))
            if len(files) < 2:
                continue
            usual = usual_group(groups_by_number[(sid, container, kind)], number, [f.group for f in files])
            out.append(DuplicateGroup(series_id=sid, folder=str(series_folder),
                                      title=series_folder.name or str(series_folder), kind=kind,
                                      number=canonical_number(number), files=tuple(files), usual_group=usual))
    out.sort(key=lambda g: (g.title.casefold(), g.folder, _directory(g), 0 if g.kind == KIND_VOLUME else 1,
                            Decimal(g.number)))
    return out


_TAGS = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}")
_DIGITS = re.compile(r"\d+(?:\.\d+)?")


def _number_pattern(number: Decimal) -> str:
    """A regex for *number* as names write it: ``01``, ``1``, ``12.5``, ``012.50``."""
    whole, _, frac = canonical_number(number).partition(".")
    return r"0*" + re.escape(whole) + (r"\.(?:" + re.escape(frac) + r")0*" if frac else r"(?:\.0+)?")


def other_numbers(name: str, number: Decimal) -> Tuple[str, ...]:
    """The numbers a file name states outside tags and outside its volume / chapter labels: ``009`` in
    ``Sea 009 Vol 01 Title``, ``1`` in ``Series Season 1 v01``. FMD2's download index and a bare unit *number* are not
    among them. Leading zeros dropped."""
    stem = os.path.splitext(os.path.basename(name))[0]
    text, before = stem, None
    while text != before:                                   # nested tags: "[Vol. 1 Ch. 2 - Title [group]]"
        before, text = text, _TAGS.sub(" ", text)
    num = _number_pattern(number)
    # every volume / chapter label, whatever its number: "v03 c012" and "v04 c012" are the same chapter 12 (one of them
    # mislabelled - MangaPixer's rule counts them), while "009 Vol 01" keeps its 009 and "Season 1 v01" its 1
    text = re.sub(r"(?i)(?<![a-z0-9])(?:v|vol|volume|c|ch|chap|chapter|ep|episode|#)\.?\s*\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?"
                  r"(?![0-9])", " ", text)
    parsed = parse_name(os.path.basename(name))
    if parsed is not None and parsed.layer == Layer.FMD2 and parsed.index is not None:
        text = re.sub(r"(?<![0-9.])0*" + str(int(parsed.index)) + r"(?![0-9.])", " ", text, count=1)
    if not re.search(r"(?i)(?<![a-z0-9])(?:v|vol|volume|c|ch|chap|chapter)\.?\s*\d", stem):
        text = re.sub(r"(?<![0-9.])" + num + r"(?![0-9])", " ", text, count=1)   # a bare number: the unit itself
    return tuple(str(Decimal(d).normalize()) if "." in d else str(int(d)) for d in _DIGITS.findall(text))


def _alike(members: List[Tuple[str, Optional[str]]], number: Decimal) -> Dict[Tuple[str, ...], List[Tuple[str, Optional[str]]]]:
    """*members* split by the other numbers their names state: only files that agree on them are copies."""
    out: Dict[Tuple[str, ...], List[Tuple[str, Optional[str]]]] = {}
    for rel, group in members:
        out.setdefault(other_numbers(rel, number), []).append((rel, group))
    return out


def usual_group(groups_by_number: Dict[Decimal, List[str]], number: Decimal, candidates: Sequence[Optional[str]]) -> Optional[str]:
    """The group a series uses around *number*: the nearest single-file numbers below and above (a series can change
    groups), else the folder's most used group. None unless it tells the copies apart (some have it, some do not)."""
    def key(g: Optional[str]) -> str:
        return (g or "").strip().casefold()

    have = {key(g) for g in candidates if g}
    if not have or all(key(g) in have and g for g in candidates) and len(have) == 1:
        return None
    single = sorted(n for n, gs in groups_by_number.items() if n != number and len(gs) == 1)
    below = [n for n in single if n < number][-1:]
    above = [n for n in single if n > number][:1]
    near = Counter(key(groups_by_number[n][0]) for n in below + above)
    pick = None
    if near:
        best, count = near.most_common(1)[0]
        if count == sum(near.values()) or count > 1:            # the neighbours agree
            pick = best
    if pick is None:
        # the folder's favourite from the OTHER numbers only: the copies themselves are no evidence for their own group
        every = Counter(key(g) for n, gs in groups_by_number.items() if n != number for g in gs)
        if every:
            best, count = every.most_common(1)[0]
            if list(every.values()).count(count) == 1:          # a clear favourite, not a tie
                pick = best
    if pick is None or pick not in have:
        return None
    if all(key(g) == pick for g in candidates):
        return None
    return next(g for g in candidates if g and key(g) == pick)


def _directory(group: DuplicateGroup) -> str:
    return os.path.dirname(group.files[0].path)


def _stat_plain_file(path: Path) -> Optional[Tuple[Path, int, int]]:
    """(path, size, mtime_ns) of a plain file; None when it is gone, a symlink, a folder or unreadable."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return path, st.st_size, st.st_mtime_ns


# --- showing -----------------------------------------------------------------------------------------------

def human_size(n: int) -> str:
    """``1536`` -> ``"1.5 KB"`` (binary units, one decimal from KB up)."""
    size = float(max(0, int(n)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(n)} B"     # unreachable


def format_time(iso: str) -> str:
    """An ISO 8601 time as the owner's local ``2026-09-30 14:02``; the text itself when it does not parse."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


# --- the default choice ----------------------------------------------------------------------------------

def default_keep(group: DuplicateGroup) -> DuplicateFile:
    """The file to keep by default: one from the series' usual group when the copies differ by group (the newest of
    those), else the newest of the number, the largest of equally new ones (then by path, so it is stable)."""
    usual = (group.usual_group or "").strip().casefold()
    if usual:
        same = [f for f in group.files if (f.group or "").strip().casefold() == usual]
        if same:
            return max(same, key=lambda f: (f.modified, f.size, f.path))
    return newest(group)


def newest(group: DuplicateGroup) -> DuplicateFile:
    return max(group.files, key=lambda f: (f.modified, f.size, f.path))


def largest(group: DuplicateGroup) -> DuplicateFile:
    return max(group.files, key=lambda f: (f.size, f.modified, f.path))


# --- deleting --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscardOutcome:
    path: str
    deleted: bool
    reason: str = ""                    # why not, in words for the owner; "" when deleted


class DiscardRefused(Exception):
    """:func:`delete_checked` refused: the message says why."""


def delete_checked(path, size: int, modified: str, roots: Sequence) -> None:
    """Delete ONE file the owner chose to discard - the only place MangaList deletes a library file.

    Raises :class:`DiscardRefused` (nothing deleted) unless ALL of this holds:

    - *path* is absolute, and lies strictly inside one of *roots* (folder paths; the root itself is never a target);
    - no part of it below the root is a symlink (the file itself included), and it contains no ``..``;
    - it is a plain file - never a folder - and not a ``.mangalist`` lock artefact;
    - its size is *size* and its modification time is *modified* (what the owner was shown): otherwise it changed since.
    """
    raw = os.fspath(path)
    if not os.path.isabs(raw):
        raise DiscardRefused("the path is not absolute")
    if ".." in raw.replace("\\", "/").split("/"):
        raise DiscardRefused("the path contains '..'")
    target = os.path.normpath(raw)
    root_dir = _root_holding(target, roots)
    if root_dir is None:
        raise DiscardRefused("it is outside every configured root")
    if os.path.basename(target).startswith(LOCK_NAME):
        raise DiscardRefused("it is a lock file")
    here = Path(root_dir)
    for part in Path(target[len(root_dir.rstrip(os.sep)) + 1:]).parts:
        here = here / part
        try:
            st = os.lstat(here)
        except FileNotFoundError:
            raise DiscardRefused("it is already gone") from None
        except OSError as exc:
            raise DiscardRefused(f"it cannot be read ({exc.strerror or type(exc).__name__})") from None
        if stat.S_ISLNK(st.st_mode):
            raise DiscardRefused("it is, or lies behind, a symbolic link")
    if not stat.S_ISREG(st.st_mode):
        raise DiscardRefused("it is not a plain file")
    if st.st_size != size or iso_from_ns(st.st_mtime_ns) != modified:
        raise DiscardRefused("it changed since the list was made")
    try:
        os.remove(target)       # unlink never follows a link: even a file swapped for one now loses only the link
    except OSError as exc:
        raise DiscardRefused(f"it could not be deleted ({exc.strerror or type(exc).__name__})") from None


def _root_holding(target: str, roots: Sequence) -> Optional[str]:
    """The (normalised) root folder that holds *target* strictly inside it, else None."""
    nt = os.path.normcase(target)
    for root in roots:
        base = os.path.normpath(os.fspath(getattr(root, "path", root)))
        nb = os.path.normcase(base)
        if nt.startswith(nb.rstrip(os.sep) + os.sep):
            return base
    return None


def discard_duplicates(db, selections: Sequence[Tuple[DuplicateGroup, Collection[str]]]) -> List[DiscardOutcome]:
    """Delete the chosen files, group by group, after the caller has had the owner's confirmation.

    *selections*: ``(group as listed, paths of its files to discard)``. For each group:

    - at least one file must stay: discarding every file of a number is refused, and so is a group whose kept files have
      meanwhile vanished or changed (the listing no longer holds);
    - a path that is not one of the group's files is refused;
    - each root is locked (``.mangalist.lock``, the writers' lock) for the whole run: while a scan's filing or a plan
      holds it, its files are refused with that reason and nothing is deleted there.

    Every deletion and every refusal is logged. Returns one outcome per requested path.
    """
    roots = db.list_roots()
    root_dirs = [r.path for r in roots]
    outcomes: List[DiscardOutcome] = []
    locks: Dict[str, Optional[RootLock]] = {}
    busy: Dict[str, str] = {}
    try:
        for group, wanted in selections:
            wanted_set = {os.path.normpath(p) for p in wanted}
            by_path = {os.path.normpath(f.path): f for f in group.files}
            keepers = [f for p, f in by_path.items() if p not in wanted_set]
            for p in sorted(wanted_set - by_path.keys()):
                outcomes.append(_refused(p, "it is not one of this number's files"))
            targets = [by_path[p] for p in sorted(wanted_set & by_path.keys())]
            if not targets:
                continue
            if not keepers:
                outcomes.extend(_refused(f.path, "that would leave no copy of this number") for f in targets)
                continue
            if not any(_still_as_listed(f) for f in keepers):
                outcomes.extend(_refused(f.path, "the copy you keep is no longer as listed - refresh the list") for f in targets)
                continue
            for f in targets:
                root_dir = _root_holding(os.path.normpath(f.path), root_dirs)
                if root_dir is not None and root_dir not in locks:
                    locks[root_dir], busy[root_dir] = _take(root_dir)
                if root_dir is not None and locks[root_dir] is None:
                    outcomes.append(_refused(f.path, busy[root_dir]))
                    continue
                try:
                    delete_checked(f.path, f.size, f.modified, root_dirs)
                except DiscardRefused as exc:
                    outcomes.append(_refused(f.path, str(exc)))
                    continue
                _log.info("Discarded duplicate %s %s: deleted %s (%d bytes, modified %s)", group.kind, group.number,
                          f.path, f.size, f.modified)
                outcomes.append(DiscardOutcome(f.path, True))
    finally:
        for lock in locks.values():
            if lock is not None:
                lock.release()
    return outcomes


def busy_reason(db, paths: Iterable[str]) -> Optional[str]:
    """Why a discard of *paths* cannot start now - another instance (a scan's filing, a plan) holds the lock of a root
    they lie in - or None. A look only: :func:`discard_duplicates` takes the lock itself."""
    root_dirs = [r.path for r in db.list_roots()]
    for root_dir in sorted({_root_holding(os.path.normpath(p), root_dirs) for p in paths} - {None}):
        lock = RootLock(root_dir)
        info = lock.read()
        if info is not None and not lock.is_stale(info):
            return f"{Path(root_dir).name or root_dir} is being changed by {info.host} (pid {info.pid})"
    return None


def _refused(path: str, reason: str) -> DiscardOutcome:
    _log.warning("Duplicate not discarded: %s (%s)", path, reason)
    return DiscardOutcome(path, False, reason)


def _still_as_listed(file: DuplicateFile) -> bool:
    seen = _stat_plain_file(Path(file.path))
    return seen is not None and seen[1] == file.size and iso_from_ns(seen[2]) == file.modified


def _take(root_dir: str) -> Tuple[Optional[RootLock], str]:
    """The root's lock, or (None, why not). Taken without a heartbeat: a discard takes seconds."""
    lock = RootLock(root_dir)
    try:
        lock.acquire()
    except LockError as exc:
        return None, f"the root is busy (a scan or a filing holds its lock): {exc}"
    return lock, ""
