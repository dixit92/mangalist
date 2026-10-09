"""Where a series' new volume archives go: the place its EXISTING layout uses (the owner's rule, D6).

Read from the scan's ``units`` rows (``rel_path`` relative to the series folder) and, for what the units cannot
say, one listing of the series folder (and of its direct subfolders). The scanner records a subfolder with
archives (other than ``Chapters`` / ``Extras`` / ...) as its own, nested series row - e.g. ``Series/Volumes`` -
so :func:`series_units` merges the units of the series' nested rows in (paths made relative to the series
folder): the layout on disk is what counts. Rules, in order:

1. The series folder is not there (moved, root not mounted) -> ambiguous, no options (rescan first).
2. A direct subfolder that holds images and no archives, named like a volume (``Vol. 01``, ``Series v02``):
   the series keeps volumes as folders of images -> ambiguous (MangaList files archives only).
3. Volume archives (units of kind ``volume``) all in ONE folder -> that folder (the series folder itself or a
   subfolder such as ``Volumes``).
4. Volume archives spread over several folders -> ambiguous; the options are those folders, most volumes first.
5. No volumes held - the usual case of an upgrade from chapters to volumes (owner, 2026-10-09: "the series
   folder - BUT when the chapters being replaced are NOT in the series folder's root, ask"):
   a. chapter archives in a subfolder (``Chapters``, ``Season 1``, ...) -> ambiguous; the options are the series
      folder first, then the folders holding chapters (most chapters first), then the other subfolders;
   b. otherwise the series folder itself, when it holds archives directly or has no subfolders;
   c. when it holds only subfolders the place is unclear -> ambiguous; the options are the series folder and
      its subfolders.

Never a folder outside the series folder: every target and option is checked (also through symlinks) to be the
series folder or inside it. Hidden / system folders (``.x``, ``@eaDir``, ``#recycle``) are never options.
No Qt; reads the disk, never writes.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Iterable, List, Optional, Sequence

from ..scanner import ARCHIVE_EXTS
from .contracts import Placement

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".jxl", ".tif", ".tiff"})


def same_or_inside(path: str, folder: str) -> bool:
    """True when *path* is *folder* or lies inside it - lexically AND after resolving symlinks."""
    def check(a: str, b: str) -> bool:
        a, b = os.path.normcase(os.path.normpath(a)), os.path.normcase(os.path.normpath(b))
        return a == b or a.startswith(b.rstrip(os.sep) + os.sep)

    return check(os.path.abspath(path), os.path.abspath(folder)) and check(os.path.realpath(path),
                                                                            os.path.realpath(folder))


def _hidden(name: str) -> bool:
    return name.startswith((".", "@", "#"))


def _is_archive(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in ARCHIVE_EXTS


def _is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def _entries(folder: str):
    try:
        with os.scandir(folder) as it:
            return [(e.name, e.is_dir(follow_symlinks=False), e.is_file(follow_symlinks=False)) for e in it]
    except OSError:
        return []


def _volume_named(name: str) -> bool:
    from ..parsing import Kind, parse_name

    parsed = parse_name(name)
    return parsed.kind == Kind.VOLUME or (parsed.kind == Kind.UNKNOWN and parsed.number is not None)


def _image_volume_folders(series_dir: str, subdirs: Iterable[str]) -> List[str]:
    out = []
    for name in subdirs:
        files = [n for n, is_dir, is_file in _entries(os.path.join(series_dir, name)) if is_file]
        if any(_is_image(n) for n in files) and not any(_is_archive(n) for n in files) and _volume_named(name):
            out.append(name)
    return out


def infer_placement(series_dir: str, units: Sequence) -> Placement:
    """The :class:`~mangalist.downloads.contracts.Placement` for the series at *series_dir* (absolute) whose
    stored units are *units* (``store.Unit`` rows: ``rel_path``, ``kind``)."""
    series_dir = os.path.normpath(os.path.abspath(series_dir))
    if not os.path.isdir(series_dir):
        return Placement(series_dir, None, "the series folder is not there (moved, or its root is not mounted); "
                                           "rescan first")
    listing = _entries(series_dir)
    subdirs = sorted(n for n, is_dir, _ in listing if is_dir and not _hidden(n))
    image_vols = _image_volume_folders(series_dir, subdirs)
    if image_vols:
        return Placement(series_dir, None, f"volumes are kept as folders of images ({len(image_vols)} found); "
                                           "MangaList files archives - choose a folder",
                         _options(series_dir, [""] + subdirs))
    archives_by_folder: dict = {}
    for u in units:
        if getattr(u, "kind", None) == "volume":
            rel = (u.rel_path or "").replace("\\", "/").strip("/")
            if rel and _is_archive(rel):
                archives_by_folder.setdefault(os.path.dirname(rel), set()).add(rel)
    counts = Counter({folder: len(files) for folder, files in archives_by_folder.items()})
    if len(counts) == 1:
        (folder,) = counts
        target = _folder(series_dir, folder)
        if target is None:
            return Placement(series_dir, None, f"the volumes' folder {folder!r} is not inside the series folder")
        if not os.path.isdir(target):
            return Placement(series_dir, None, f"the volumes' folder {folder or '(series folder)'!r} is gone; "
                                               "rescan first")
        reason = "volumes live in the series folder" if not folder else f'volumes live in "{folder}"'
        return Placement(series_dir, target, reason)
    if len(counts) > 1:
        ordered = [f for f, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        return Placement(series_dir, None, f"volumes are spread over {len(counts)} folders; choose one",
                         _options(series_dir, ordered))
    chapter_folders = Counter()
    for u in units:
        if getattr(u, "kind", None) == "chapter":
            rel = (u.rel_path or "").replace("\\", "/").strip("/")
            if rel and _is_archive(rel) and "/" in rel:
                chapter_folders[os.path.dirname(rel)] += 1
    if chapter_folders:
        ordered = [f for f, _ in sorted(chapter_folders.items(), key=lambda kv: (-kv[1], kv[0]))]
        names = ", ".join(f'"{f}"' for f in ordered[:3]) + (" ..." if len(ordered) > 3 else "")
        return Placement(series_dir, None, f"no volumes held yet and the chapters sit in subfolders ({names}); "
                                           "choose where the volumes go",
                         _options(series_dir, [""] + ordered + subdirs))
    loose = any(is_file and _is_archive(n) for n, _, is_file in listing)
    if loose or not subdirs:
        return Placement(series_dir, series_dir, "no volumes held yet; the series folder itself")
    return Placement(series_dir, None, "no volumes held yet and the series folder holds only subfolders; "
                                       "choose one", _options(series_dir, [""] + subdirs))


def _folder(series_dir: str, rel: str) -> Optional[str]:
    """*rel* (``/``-separated, series-relative) as an absolute folder inside *series_dir*, else None."""
    parts = [p for p in rel.split("/") if p]
    if any(p in (".", "..") for p in parts):
        return None
    path = os.path.join(series_dir, *parts) if parts else series_dir
    return path if same_or_inside(path, series_dir) else None


def _options(series_dir: str, rels: Iterable[str]) -> tuple:
    out: List[str] = []
    for rel in rels:
        path = _folder(series_dir, rel)
        if path is not None and os.path.isdir(path) and path not in out:
            out.append(path)
    return tuple(out)


def locate_series(db, series_id: int):
    """``(series row, root, absolute series folder)`` of library series *series_id*; LookupError when the
    database no longer knows it or its root."""
    from ..store.series import _row

    if series_id is None:           # the ledger's series_id is SET NULL when the series row goes (root removed)
        raise LookupError("the series is no longer in the library database")
    with db.connect() as con:
        r = con.execute("SELECT * FROM series WHERE id = ?", (int(series_id),)).fetchone()
    if r is None:
        raise LookupError(f"series {series_id} is no longer in the library database")
    series = _row(r)
    root = db.get_root(series.root_id)
    if root is None:
        raise LookupError(f"series {series_id}'s root is no longer configured")
    return series, root, os.path.join(root.path, *series.rel_path.split("/"))


def series_units(db, series) -> List:
    """The units of *series* and of every present series row nested inside its folder (the scanner's subseries,
    e.g. ``Series/Volumes``), each ``rel_path`` relative to *series*' folder."""
    from dataclasses import replace

    units = list(db.list_units(series.id))
    prefix = series.rel_path.rstrip("/") + "/"
    for other in db.list_series(series.root_id):
        if other.id == series.id or other.status != "present" or not other.rel_path.startswith(prefix):
            continue
        sub = other.rel_path[len(prefix):]
        units.extend(replace(u, rel_path=f"{sub}/{u.rel_path}") for u in db.list_units(other.id))
    return units


def placement_for(db, series_id: int) -> Placement:
    """The placement of library series *series_id* from the library database (series row, root, the units of
    the series and of its nested rows)."""
    series, _, series_dir = locate_series(db, series_id)
    return infer_placement(series_dir, series_units(db, series))
