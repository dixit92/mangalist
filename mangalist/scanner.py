"""Filesystem scanner: walk the roots -> MangaEntry list.

A root's direct child folders are its series folders (franchise parents split into their subseries, as
before). A root's exclusions (root-relative patterns, :mod:`mangalist.store.exclusions`) are applied
while walking: an excluded folder is never entered, an excluded file never read. An archive lying
directly in a root is not a series: it is reported as "not in a series folder" (``loose``) and never
matched. Scans only read; they never take the root lock, and files other tools add are simply seen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Union

from .classifier import annotate_file, classify, parse_folder_name
from .models import FileHit, MangaEntry
from .store.exclusions import ExclusionSet

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


def _walk_manga_folder(folder: Path, max_depth: int = MAX_DEPTH, _ex: _Excl = _NO_EXCL) -> List[FileHit]:
    """Return archive FileHits inside ``folder`` up to ``max_depth`` levels (excluded paths skipped)."""
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
            annotate_file(hit)
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
            # Check if this subdir has any archives (using existing walk)
            if _walk_manga_folder(subdir, _ex=_ex.child(subdir.name)):
                result.append(subdir)
    except OSError:
        pass
    return result


# Subdirectory names to skip when extracting franchise subseries
_SKIP_SUBDIR_NAMES = {"chapters", "extras", "bonus", "specials", "omake"}


def _extract_subseries(
    parent: Path, parent_title: str, parent_mtime: float, _ex: _Excl = _NO_EXCL
) -> List[MangaEntry]:
    """Create MangaEntry objects for each subseries in a franchise parent.

    Skips subdirectories named exactly 'Chapters' or other non-series folders.
    Each subseries gets parent_folder set to the parent Path.
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
        entry.files = _walk_manga_folder(subdir, _ex=_ex.child(subdir.name))
        classify(entry)
        entries.append(entry)

    return entries


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
) -> List[MangaEntry]:
    """Scan a Manga Root and return classified MangaEntry objects.

    ``progress(done, total, current_name)`` is called as folders are processed. *exclusions* (patterns
    relative to the root) are never scanned. Archives lying directly in the root are appended to
    *loose* when given ("not in a series folder"); they never become entries. *root_id* is copied onto
    every entry.
    """
    root = Path(root).resolve()
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
        subseries = _extract_subseries(folder, title, mtime, ex)

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
            parent_entry.files = _walk_manga_folder(folder, _ex=ex)
            classify(parent_entry)
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
            entry.files = _walk_manga_folder(folder, _ex=ex)
            classify(entry)
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


def scan_library(
    roots: Sequence,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> LibraryScan:
    """Scan every root (``store.Root``-like: ``id``, ``name``, ``path``, ``exclusions``). A root that
    cannot be read is reported in :attr:`LibraryScan.errors`; the others are still scanned."""
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
        try:
            rs.entries = scan_root(folder, prog, exclusions=list(getattr(root, "exclusions", []) or []),
                                   loose=loose, root_id=rs.root_id)
        except (OSError, NotADirectoryError) as exc:
            rs.error = str(exc)
        result.roots.append(rs)
        result.loose.extend(LooseArchive(rs.root_id, name, p) for p in loose)
    return result


def record_library_scan(db, result: LibraryScan) -> List[tuple]:
    """Write a :class:`LibraryScan` into the database's series rows (see ``Store.record_scan``).

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
    return renamed
