"""Filesystem scanner: walk the roots -> MangaEntry list.

A root's direct child folders are its series folders (franchise parents split into their subseries, as
before). A root's exclusions (root-relative patterns, :mod:`mangalist.store.exclusions`) are applied
while walking: an excluded folder is never entered, an excluded file never read. An archive lying
directly in a root is not a series: it is reported as "not in a series folder" (``loose``) and never
matched. Scans only read; they never take the root lock, and files other tools add are simply seen.

Every archive is parsed by the layered parser (:mod:`mangalist.parsing`) with its series' context: the
folder title, the root's naming scheme (when set) and the series' stored "volumes or chapters?" answer
(Design Decisions C12). :func:`record_library_scan` stores each archive's units per series in the
database (``units`` table) and applies stored answers; :func:`answer_series_kind` records a new answer.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from .classifier import annotate_file, classify, parse_folder_name
from .models import FileHit, MangaEntry
from .store.exclusions import ExclusionSet, rel_posix

_log = logging.getLogger(__name__)

ARCHIVE_EXTS = {".cbz", ".zip", ".cbr", ".rar", ".7z", ".cb7"}

MAX_DEPTH = 3  # 0 = manga folder itself; 3 = files three levels deep


def _is_archive(p: Path) -> bool:
    return p.suffix.lower() in ARCHIVE_EXTS


class _Excl:
    """A root's exclusions, with the root-relative prefix of the folder being walked."""

    def __init__(self, exclusions: Optional[ExclusionSet], prefix: str):
        self.ex = exclusions if exclusions else None
        self.prefix = prefix

    def child(self, name: str) -> "_Excl":
        return _Excl(self.ex, f"{self.prefix}{name}/" if self.prefix or name else "")

    def hides(self, rel_inside: str, is_dir: bool) -> bool:
        return self.ex is not None and self.ex.excludes(self.prefix + rel_inside, is_dir)


_NO_EXCL = _Excl(None, "")


def _parse_context(title: Optional[str], kind_hint: Optional[str] = None, schemes: Sequence = ()):
    """The series' :class:`~mangalist.parsing.ParseContext`; a bad hint or scheme is left out (logged)."""
    from .parsing import ParseContext

    try:
        return ParseContext(schemes=tuple(schemes or ()), kind_hint=kind_hint, series_title=title)
    except Exception:  # noqa: BLE001 - a broken stored scheme / hint must not stop the scan
        _log.warning("Ignoring an unusable naming scheme / kind hint for %r", title, exc_info=True)
        try:
            return ParseContext(kind_hint=kind_hint, series_title=title)
        except Exception:  # noqa: BLE001
            return ParseContext(series_title=title)


def _walk_manga_folder(folder: Path, max_depth: int = MAX_DEPTH, _ex: _Excl = _NO_EXCL,
                       context=None, annotate: bool = True) -> List[FileHit]:
    """Return archive FileHits inside ``folder`` up to ``max_depth`` levels (excluded paths skipped),
    each parsed with *context* (the series' ParseContext) unless *annotate* is False."""
    hits: List[FileHit] = []
    folder = folder.resolve()

    for dirpath, dirnames, filenames in os.walk(folder):
        try:
            rel_parts = Path(dirpath).resolve().relative_to(folder).parts
        except ValueError:
            rel_parts = ()
        rel_depth = len(rel_parts)
        rel_dir = "".join(f"{part}/" for part in rel_parts)
        if rel_depth > max_depth:
            # Don't descend any further.
            dirnames[:] = []
            continue
        if _ex.ex is not None:
            dirnames[:] = [d for d in dirnames if not _ex.hides(rel_dir + d, True)]
        for name in filenames:
            p = Path(dirpath) / name
            if not _is_archive(p):
                continue
            if _ex.hides(rel_dir + name, False):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            hit = FileHit(path=p, size=size, depth=rel_depth)
            if annotate:
                annotate_file(hit, context)
            hits.append(hit)
    return hits


def _count_subfolders(folder: Path, _ex: _Excl = _NO_EXCL) -> int:
    try:
        return sum(1 for c in folder.iterdir() if c.is_dir() and not _ex.hides(c.name, True))
    except OSError:
        return 0


def _has_direct_archives(folder: Path, _ex: _Excl = _NO_EXCL) -> bool:
    """Return True if folder contains archive files directly (not in subdirs)."""
    try:
        for p in folder.iterdir():
            if p.is_file() and _is_archive(p) and not _ex.hides(p.name, False):
                return True
    except OSError:
        pass
    return False


def _get_subdirs_with_archives(folder: Path, _ex: _Excl = _NO_EXCL) -> List[Path]:
    """Return immediate subdirectories that contain archive files (at any depth)."""
    result: List[Path] = []
    try:
        for subdir in folder.iterdir():
            if not subdir.is_dir() or _ex.hides(subdir.name, True):
                continue
            # Check if this subdir has any archives (using existing walk; nothing parsed)
            if _walk_manga_folder(subdir, _ex=_ex.child(subdir.name), annotate=False):
                result.append(subdir)
    except OSError:
        pass
    return result


# Subdirectory names to skip when extracting franchise subseries
_SKIP_SUBDIR_NAMES = {"chapters", "extras", "bonus", "specials", "omake"}


def _extract_subseries(
    parent: Path, parent_title: str, parent_mtime: float, _ex: _Excl = _NO_EXCL,
    hint_for: Optional[Callable[[Path], Optional[str]]] = None, schemes: Sequence = (),
) -> List[MangaEntry]:
    """Create MangaEntry objects for each subseries in a franchise parent.

    Skips subdirectories named exactly 'Chapters' or other non-series folders.
    Each subseries gets parent_folder set to the parent Path. *hint_for(folder)* gives a subseries'
    stored kind hint.
    """
    entries: List[MangaEntry] = []
    subdirs = _get_subdirs_with_archives(parent, _ex)

    for subdir in subdirs:
        # Skip non-series subdirectories
        if subdir.name.lower() in _SKIP_SUBDIR_NAMES:
            continue

        # Parse subdir name using same logic as parent
        sub_title, sub_eng = parse_folder_name(subdir.name)

        # Use subdir's own mtime if available, fall back to parent
        try:
            mtime = subdir.stat().st_mtime
        except OSError:
            mtime = parent_mtime

        entry = MangaEntry(
            folder=subdir,
            title=sub_title,
            english_title=sub_eng,
            n_subfolders=_count_subfolders(subdir, _ex.child(subdir.name)),
            last_modified=mtime,
            parent_folder=parent,  # Mark as subseries
        )
        _fill(entry, _ex.child(subdir.name), hint_for(subdir) if hint_for else None, schemes)
        entries.append(entry)

    return entries


def _fill(entry: MangaEntry, ex: _Excl, kind_hint: Optional[str], schemes: Sequence) -> None:
    """Walk, parse and classify one series entry."""
    entry.kind_hint = kind_hint
    ctx = _parse_context(entry.title, kind_hint, schemes)
    entry.files = _walk_manga_folder(entry.folder, _ex=ex, context=ctx)
    classify(entry)


def apply_kind_hint(entry: MangaEntry, kind_hint: Optional[str], schemes: Sequence = ()) -> MangaEntry:
    """Re-parse *entry*'s files with a new "volumes or chapters?" answer (None = no answer) and
    re-classify it, in place (no disk access)."""
    ctx = _parse_context(entry.title, kind_hint, schemes)
    entry.kind_hint = kind_hint
    for hit in entry.files:
        annotate_file(hit, ctx)
    classify(entry)
    return entry


Exclusions = Union[ExclusionSet, Iterable[str], None]


def _as_exclusion_set(exclusions: Exclusions) -> Optional[ExclusionSet]:
    if exclusions is None or isinstance(exclusions, ExclusionSet):
        return exclusions or None
    return ExclusionSet(exclusions) or None


def scan_root(
    root: Path,
    progress: Optional[Callable[[int, int, str], None]] = None,
    *,
    exclusions: Exclusions = None,
    loose: Optional[List[Path]] = None,
    root_id: Optional[int] = None,
    kind_hints: Optional[Mapping[str, str]] = None,
    schemes: Sequence = (),
) -> List[MangaEntry]:
    """Scan a Manga Root and return classified MangaEntry objects.

    ``progress(done, total, current_name)`` is called as folders are processed. *exclusions* (patterns
    relative to the root) are never scanned. Archives lying directly in the root are appended to
    *loose* when given ("not in a series folder"); they never become entries. *root_id* is copied onto
    every entry. Every archive is parsed by the layered parser; *kind_hints* (``{root-relative series
    path: "volumes" | "chapters"}``, the stored answers) decide bare numbers, *schemes* are the root's
    naming template(s).
    """
    root = Path(root).resolve()
    hints = dict(kind_hints or {})

    def hint_for(folder: Path) -> Optional[str]:
        return hints.get(rel_posix(folder, root)) if hints else None

    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")
    top = _Excl(_as_exclusion_set(exclusions), "")

    manga_dirs = []
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise NotADirectoryError(f"Cannot read {root}: {exc}") from exc
    for p in children:
        try:
            is_dir = p.is_dir()
        except OSError:
            continue
        if is_dir:
            if not p.name.startswith(".") and not top.hides(p.name, True):
                manga_dirs.append(p)
        elif loose is not None and _is_archive(p) and not top.hides(p.name, False):
            loose.append(p)
    total = len(manga_dirs)
    entries: List[MangaEntry] = []

    for i, folder in enumerate(manga_dirs, start=1):
        if progress:
            progress(i - 1, total, folder.name)
        ex = top.child(folder.name)

        title, eng = parse_folder_name(folder.name)
        try:
            mtime = folder.stat().st_mtime
        except OSError:
            mtime = 0.0

        has_direct = _has_direct_archives(folder, ex)
        subseries = _extract_subseries(folder, title, mtime, ex, hint_for, schemes)

        if not has_direct and subseries:
            # Parent is a franchise container - only add subseries, not parent
            entries.extend(subseries)
        elif has_direct and subseries:
            # Parent has both direct files AND subseries - add both
            # Create parent entry normally
            parent_entry = MangaEntry(
                folder=folder,
                title=title,
                english_title=eng,
                n_subfolders=_count_subfolders(folder, ex),
                last_modified=mtime,
            )
            # The subseries' archives stay in its file counts (as before) but not in its inventory.
            parent_entry.nested_series = [s.folder for s in subseries]
            _fill(parent_entry, ex, hint_for(folder), schemes)
            entries.append(parent_entry)
            # Also add subseries
            entries.extend(subseries)
        else:
            # Normal case: no subseries, just the folder itself
            entry = MangaEntry(
                folder=folder,
                title=title,
                english_title=eng,
                n_subfolders=_count_subfolders(folder, ex),
                last_modified=mtime,
            )
            _fill(entry, ex, hint_for(folder), schemes)
            entries.append(entry)

    if root_id is not None:
        for e in entries:
            e.root_id = root_id
    if progress:
        progress(total, total, "")
    return entries


@dataclass
class LooseArchive:
    """An archive directly in a root: "not in a series folder" (never matched)."""

    root_id: Optional[int]
    root_name: str
    path: Path


@dataclass
class RootScan:
    root_id: Optional[int]
    root_name: str
    folder: Path                                  # the resolved folder that was walked
    entries: List[MangaEntry] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class LibraryScan:
    roots: List[RootScan] = field(default_factory=list)
    loose: List[LooseArchive] = field(default_factory=list)

    @property
    def entries(self) -> List[MangaEntry]:
        return [e for r in self.roots for e in r.entries]

    @property
    def errors(self) -> List[str]:
        return [f"{r.root_name}: {r.error}" for r in self.roots if r.error]


def _root_schemes(root) -> Tuple[str, ...]:
    scheme = getattr(root, "naming_scheme", None)
    return (scheme,) if isinstance(scheme, str) and scheme.strip() else ()


def scan_library(
    roots: Sequence,
    progress: Optional[Callable[[int, int, str], None]] = None,
    *,
    db=None,
) -> LibraryScan:
    """Scan every root (``store.Root``-like: ``id``, ``name``, ``path``, ``exclusions``,
    ``naming_scheme``). A root that cannot be read is reported in :attr:`LibraryScan.errors`; the others
    are still scanned. With *db*, the series' stored "volumes or chapters?" answers are used while
    parsing (without it, :func:`record_library_scan` applies them afterwards)."""
    result = LibraryScan()
    many = len(roots) > 1
    for root in roots:
        name = getattr(root, "name", "") or str(root.path)
        folder = Path(root.path)
        try:
            folder = folder.resolve()
        except OSError:
            pass
        rs = RootScan(root_id=getattr(root, "id", None), root_name=name, folder=folder)
        loose: List[Path] = []
        prog = progress
        if progress and many:
            def prog(d, t, n, _name=name):  # noqa: E306 - label the folder with its root
                progress(d, t, f"{_name}: {n}" if n else "")
        hints: Dict[str, str] = {}
        if db is not None and rs.root_id is not None:
            try:
                hints = db.series_kind_hints(rs.root_id)
            except Exception:  # noqa: BLE001 - the scan works without the answers
                _log.warning("Reading the stored series kinds failed", exc_info=True)
        try:
            rs.entries = scan_root(folder, prog, exclusions=list(getattr(root, "exclusions", []) or []),
                                   loose=loose, root_id=rs.root_id, kind_hints=hints,
                                   schemes=_root_schemes(root))
        except (OSError, NotADirectoryError) as exc:
            rs.error = str(exc)
        result.roots.append(rs)
        result.loose.extend(LooseArchive(rs.root_id, name, p) for p in loose)
    return result


def record_library_scan(db, result: LibraryScan) -> List[tuple]:
    """Write a :class:`LibraryScan` into the database: series rows (see ``Store.record_scan``) and every
    archive's units (``units`` table, see ``Store.sync_units``).

    Each series' stored "volumes or chapters?" answer is applied to its entry first (its files are
    re-parsed in place when the scan used another answer, e.g. a folder renamed since the answer was
    given), so the entries shown and the units stored agree. Units of archives that are gone are
    deleted, as are the units of series folders that disappeared.

    Returns ``(old folder, new folder)`` for every series folder recognised as renamed (its row and
    MangaUpdates link moved with it). Roots that failed to scan are left untouched (their series are
    not marked missing just because a share was offline).
    """
    from .store.series import link_key, seen_from_entries

    renamed: List[tuple] = []
    for rs in result.roots:
        if rs.error or rs.root_id is None:
            continue
        rec = db.record_scan(rs.root_id, rs.folder, seen_from_entries(rs.folder, rs.entries))
        renamed.extend((Path(link_key(rs.folder, old)), Path(link_key(rs.folder, new))) for old, new in rec.relinked)
        try:
            _record_units(db, rs, getattr(_root_of(db, rs.root_id), "naming_scheme", None))
        except Exception:  # noqa: BLE001 - the series rows and re-links are already recorded
            _log.warning("Recording the units of %s failed", rs.root_name, exc_info=True)
    return renamed


def _root_of(db, root_id: int):
    try:
        return db.get_root(root_id)
    except Exception:  # noqa: BLE001
        return None


def _record_units(db, rs: RootScan, scheme: Optional[str] = None) -> None:
    from .inventory import units_of_entry

    schemes = (scheme,) if isinstance(scheme, str) and scheme.strip() else ()
    rows = {s.rel_path: s for s in db.list_series(rs.root_id)}
    by_series: Dict[int, Dict[str, list]] = {}
    for entry in rs.entries:
        row = rows.get(rel_posix(Path(entry.folder), rs.folder))
        if row is None:
            continue
        if (row.kind_hint or None) != (entry.kind_hint or None):
            apply_kind_hint(entry, row.kind_hint, schemes)
        by_series[row.id] = units_of_entry(entry)
    for row in rows.values():
        if row.status == "missing" and row.id not in by_series:
            by_series[row.id] = {}
    db.sync_units(by_series)


def answer_series_kind(db, entry: MangaEntry, kind: Optional[str]) -> bool:
    """Record the owner's "volumes or chapters?" answer for *entry*'s series (C12): store it on the
    series row, re-parse the entry in place and store its units. ``kind`` is ``"volumes"`` /
    ``"chapters"`` (None forgets the answer). False when the folder is not a known series of a root
    (the entry is still re-parsed)."""
    from .inventory import units_of_entry
    from .store.series import normalize_kind_hint

    k = normalize_kind_hint(kind)
    located = db._locate(entry.folder)
    root = _root_of(db, located[0]) if located else None
    apply_kind_hint(entry, k, _root_schemes(root) if root is not None else ())
    if located is None or not db.set_series_kind(located[0], located[1], k):
        return False
    row = db.get_series(*located)
    db.sync_units({row.id: units_of_entry(entry)})
    return True
