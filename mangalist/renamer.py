"""The library renamer: bring every series' file names to the naming scheme, safely (owner, 2026-10-10: "Let's do the
bulk rename!" - a guided one-time conversion of every root, then ongoing enforcement). No Qt here.

**What a file is called** is the naming lane's answer (:mod:`mangalist.naming`, ``target_name``): this module never
builds a name itself. The namer is INJECTED (:class:`Namer`); the default (:class:`DefaultNamer`) imports
``mangalist.naming`` lazily at call time, so this module works (and is tested with a fake) before that module exists.
Per file the answer is one of:

- **unchanged** - the name already is the scheme's;
- **rename** - old -> new, in the same folder (files only; folders are never renamed);
- **left alone** - the namer returns None (unreadable, a true extra, ambiguous), the new name would not be valid, the
  file is gone since the last scan, or naming failed;
- **collision** - two files would get one name (two groups' copies of a chapter normally differ by their group; when they
  still do not, both keep their names and are listed for the duplicates review), or the name is taken by another file
  in the folder. Nothing is ever overwritten.

**Safeguards** (Next Cycle Design, section 4):

1. *Dry run* per root (:meth:`Renamer.dry_run`): counts per detected pattern (the parser's layer), the files left
   alone, the collisions, the titles / groups the length rule would cut, the titles that contain a unit word
   (``Vol 2``) - nothing is changed.
2. *Preview* per series (:meth:`Renamer.preview_series`): old -> new for every file.
3. *Batches through the journal* (:meth:`Renamer.apply`): one write-ahead journal plan per batch (whole series only,
   never split), no-replace renames, the root's lock while it applies, undo per batch (:meth:`Renamer.undo`).
   Swaps and chains of names are ordered so no step needs a name that is still taken (a cycle goes through a
   temporary name inside the same plan).
4. *Renames only*: a rename never changes a file's contents (MangaPixer keeps read state by content signature; the
   journal refuses a file that changed since the plan). Whether MangaPixer has analysed the archives is asked of an
   injected check; when MangaList cannot tell (the default: MangaPixer's export says nothing about analysis), the dry
   run says so plainly (:data:`ANALYSIS_WARNING`).
5. *After each batch* (and each undo): the rescan is recorded (the injected ``rescan``; the GUI rescans itself) and
   MangaPixer is asked to scan the libraries touched (as after filing; the same Settings > Automation switch).
6. *Pilot*: the window makes "one series" the first step; :meth:`Renamer.mark_guided` records a root the owner started
   converting from the window. The headless **automatic** pass (:meth:`Renamer.automatic_pass`, roots set to
   "Rename automatically") acts only on such roots, at most :data:`AUTO_LIMIT` files per pass, and logs every batch.

**Rename pending** (:meth:`Renamer.pending_counts`): per series folder, how many files the scheme would rename (roots set
to Off never count). The List shows it as a flag, a column and a filter.

Logging: INFO for every dry run / plan / apply / undo with counts; WARNING for refusals and collisions.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple

_log = logging.getLogger(__name__)

KEY_WINDOWS_SERVER = "naming.windows_server"     # Settings > Library: the server name of the Windows path (optional)
KEY_GUIDED_ROOTS = "renamer.guided_roots"         # root ids the owner started converting from the Renamer window
PLAN_REASON = "rename to the scheme"
BATCH_SIZE = 200            # files per journal plan (whole series only: a bigger series is a batch of its own)
AUTO_LIMIT = 500            # files the automatic pass renames per run at most (the rest waits for the next run)

ENFORCE_OFF, ENFORCE_ASK, ENFORCE_AUTOMATIC = "off", "ask", "automatic"
ENFORCE_LABELS = {ENFORCE_OFF: "Off", ENFORCE_ASK: "Ask before renaming", ENFORCE_AUTOMATIC: "Rename automatically"}

UNCHANGED, RENAME, LEFT_ALONE, COLLISION = "unchanged", "rename", "left alone", "collision"

ANALYSIS_WARNING = ("Files MangaPixer has not analysed yet lose their read state when renamed - let MangaPixer finish "
                    "its scan first.")
NOT_ANALYSED_WARNING = ("MangaPixer has not analysed every archive of {n} series yet: renaming them now loses their "
                        "read state - let MangaPixer finish its scan first.")

_HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62})$")
# A unit word in a chapter title ("The Vol 2 Begins", "Chapter 5 of the Ch. 3 Arc"): a parser may read it as a number.
_UNIT_WORDS = re.compile(r"(?i)(?<![a-z])(?:vol(?:ume)?s?|ch(?:apter|ap|p)?s?|ep(?:isode)?s?)\.?\s*#?\d"
                         r"|(?<![a-z0-9])[vc]\d")


# --- the namer (lane A's naming module, injected) ----------------------------------------------------------------


class Namer(Protocol):
    """What the renamer needs from :mod:`mangalist.naming` (the naming contract, Next Cycle Design section 13)."""

    def target_name(self, parsed: Any, *, ext: str, series_title: Optional[str],
                    volume_of: Optional[Callable[[Decimal], Optional[Decimal]]] = None, folder: Optional[str] = None,
                    limits: Any = None) -> Optional[str]:
        """The scheme's file name for *parsed* (None: leave the file alone)."""

    def volume_lookup(self, db: Any, series_id: int) -> Callable[[Decimal], Optional[Decimal]]:
        """Chapter -> volume from MangaPixer's volume list of the series."""

    def limits(self, windows_server: Optional[str]) -> Any:
        """The length rule's limits (``NameLimits``) for the Windows server name (None: no Windows path)."""


class NamingUnavailable(RuntimeError):
    """The naming module is not in this build."""


class DefaultNamer:
    """:mod:`mangalist.naming`, imported when first used (the naming lane's module; the integrator merges it first)."""

    def _module(self):
        try:
            from . import naming  # type: ignore[attr-defined]
        except ImportError as exc:
            raise NamingUnavailable("the naming scheme is not in this build") from exc
        return naming

    def target_name(self, parsed, *, ext, series_title, volume_of=None, folder=None, limits=None):
        mod = self._module()
        kw = {} if limits is None else {"limits": limits}
        return mod.target_name(parsed, ext=ext, series_title=series_title, volume_of=volume_of, folder=folder, **kw)

    def volume_lookup(self, db, series_id):
        return self._module().volume_lookup(db, series_id)

    def limits(self, windows_server):
        return self._module().NameLimits(windows_server=windows_server or None)


def naming_available(namer: Optional[Namer] = None) -> bool:
    """True when *namer* (default: :class:`DefaultNamer`) can name files in this build."""
    namer = namer if namer is not None else DefaultNamer()
    if isinstance(namer, DefaultNamer):
        try:
            namer._module()
        except NamingUnavailable:
            return False
    return True


# --- settings -----------------------------------------------------------------------------------------------------


def windows_server(db) -> Optional[str]:
    """The Windows server name of the length rule (``SMIT-SERVER``), or None when none is set."""
    try:
        value = db.get_setting(KEY_WINDOWS_SERVER, None)
    except Exception:  # noqa: BLE001 - no setting: no Windows path rule
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_server(text: Optional[str]) -> Optional[str]:
    """``\\\\SMIT-SERVER\\`` / ``smit-server`` -> the server name; None for empty; :class:`ValueError` when it is not a
    plain server name (letters, digits, ``-``, ``_``, ``.``)."""
    name = (text or "").strip().strip("\\/").strip()
    if not name:
        return None
    if not _HOSTNAME.match(name):
        raise ValueError("a server name has letters, digits, '-', '_' or '.' only (e.g. SMIT-SERVER)")
    return name


def set_windows_server(db, text: Optional[str]) -> Optional[str]:
    """Store the Windows server name (None / empty: clear it). Returns what was stored."""
    name = normalize_server(text)
    db.set_setting(KEY_WINDOWS_SERVER, name)
    _log.info("Renamer: Windows server for the path-length rule %s", f"set to {name}" if name else "cleared")
    return name


def guided_roots(db) -> Set[int]:
    try:
        value = db.get_setting(KEY_GUIDED_ROOTS, [])
    except Exception:  # noqa: BLE001
        return set()
    return {int(v) for v in value if isinstance(v, int) and not isinstance(v, bool)} if isinstance(value, list) else set()


# --- results ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FilePlan:
    """One file of a series: what the scheme makes of it."""

    path: str                       # absolute path as on disk now
    target: Optional[str]           # the scheme's file name (None: left alone)
    status: str                     # UNCHANGED | RENAME | LEFT_ALONE | COLLISION
    layer: str = "none"             # the parser layer that read the name (the dry run's "pattern")
    reason: str = ""                # left alone / collision: why
    title_cut: bool = False         # the length rule shortened the chapter title
    group_cut: bool = False         # ... or the group
    unit_words: bool = False        # the chapter title holds a unit word (a parser may read a number from it)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def new_path(self) -> Optional[str]:
        return os.path.join(self.folder, self.target) if self.target else None


@dataclass
class SeriesPreview:
    """Old -> new for every file of one series folder."""

    series_id: Optional[int]
    root_id: Optional[int]
    root_path: str
    folder: str
    title: str                                   # the folder's name (what the window shows)
    series_title: Optional[str] = None           # the title volume names use (MangaPixer's, else the folder's)
    files: List[FilePlan] = field(default_factory=list)
    analysed: Optional[bool] = None              # MangaPixer analysed every archive (None: MangaList cannot tell)
    error: Optional[str] = None                  # why nothing could be previewed

    def of(self, status: str) -> List[FilePlan]:
        return [f for f in self.files if f.status == status]

    @property
    def renames(self) -> List[FilePlan]:
        return self.of(RENAME)

    @property
    def collisions(self) -> List[FilePlan]:
        return self.of(COLLISION)

    @property
    def left_alone(self) -> List[FilePlan]:
        return self.of(LEFT_ALONE)

    @property
    def unchanged(self) -> List[FilePlan]:
        return self.of(UNCHANGED)

    @property
    def moves(self) -> List[Tuple[str, str]]:
        return [(f.path, f.new_path) for f in self.renames if f.new_path]

    def counts(self) -> Dict[str, int]:
        c = Counter(f.status for f in self.files)
        return {s: c.get(s, 0) for s in (RENAME, UNCHANGED, LEFT_ALONE, COLLISION)}


@dataclass
class RootDryRun:
    root_id: Optional[int]
    name: str
    path: str
    enforce: str
    series: List[SeriesPreview] = field(default_factory=list)

    @property
    def patterns(self) -> Counter:
        return Counter(f.layer for s in self.series for f in s.files)

    def count(self, status: str) -> int:
        return sum(len(s.of(status)) for s in self.series)

    @property
    def series_to_rename(self) -> List[SeriesPreview]:
        return [s for s in self.series if s.renames]


@dataclass
class DryRun:
    roots: List[RootDryRun] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def series(self) -> List[SeriesPreview]:
        return [s for r in self.roots for s in r.series]

    def count(self, status: str) -> int:
        return sum(r.count(status) for r in self.roots)

    @property
    def patterns(self) -> Counter:
        out: Counter = Counter()
        for r in self.roots:
            out.update(r.patterns)
        return out

    def files_where(self, predicate: Callable[[FilePlan], bool]) -> List[Tuple[SeriesPreview, FilePlan]]:
        return [(s, f) for s in self.series for f in s.files if predicate(f)]

    def summary(self) -> str:
        """One line of counts (the log and the window's headline)."""
        series = sum(1 for s in self.series if s.renames)
        return (f"{self.count(RENAME)} file(s) to rename in {series} series, {self.count(UNCHANGED)} already named by "
                f"the scheme, {self.count(LEFT_ALONE)} left alone, {self.count(COLLISION)} name collision(s)")


@dataclass(frozen=True)
class RenameBatch:
    """Whole series of one root, renamed as one journal plan."""

    root_id: Optional[int]
    root_path: str
    series: Tuple[SeriesPreview, ...]

    @property
    def n_files(self) -> int:
        return sum(len(s.renames) for s in self.series)


@dataclass
class BatchResult:
    status: str                                  # applied | partly | failed | busy | nothing
    plan_id: Optional[int] = None
    renamed: int = 0
    failed: List[Tuple[str, str]] = field(default_factory=list)       # (file name, why)
    skipped: List[Tuple[str, str]] = field(default_factory=list)      # changed since the preview: (file name, why)
    series_ids: List[int] = field(default_factory=list)
    root_ids: List[int] = field(default_factory=list)
    message: str = ""
    after: str = ""                              # what the rescan / MangaPixer scan request said


@dataclass(frozen=True)
class BatchRecord:
    """A renamer plan in the journal (the window's "Undo last batch")."""

    plan_id: int
    status: str
    created_at: str
    files: int
    done: int
    note: str

    @property
    def can_undo(self) -> bool:
        return self.status in ("applied", "failed", "interrupted", "undo_failed") and self.done > 0


@dataclass
class AutoReport:
    roots: List[str] = field(default_factory=list)
    batches: List[BatchResult] = field(default_factory=list)
    waiting: List[str] = field(default_factory=list)      # roots set to automatic that wait for the guided start
    skipped: Optional[str] = None
    after: str = ""

    @property
    def renamed(self) -> int:
        return sum(b.renamed for b in self.batches)

    def summary(self) -> str:
        if self.skipped:
            return f"renames: {self.skipped}"
        parts = [f"{self.renamed} file(s) renamed in {len(self.batches)} batch(es)"] if self.batches else []
        if self.waiting:
            parts.append(f"{len(self.waiting)} root(s) wait for the first guided rename")
        if self.after:
            parts.append(self.after)
        return "renames: " + ("; ".join(parts) if parts else "nothing to rename")


# --- the per-series preview (pure: no database, only the folder listing) -------------------------------------------


def _layer(parsed: Any) -> str:
    layer = getattr(parsed, "layer", None)
    return str(getattr(layer, "value", layer) or "none")


def _fold(name: str) -> str:
    """The comparison key of a name: SMB / Windows compare names case-insensitively."""
    return name.casefold()


def _name_problem(name: Optional[str]) -> Optional[str]:
    from .store.names import windows_name_problem

    if not name or os.sep in name or "/" in name:
        return "the new name is not a plain file name"
    if len(name.encode("utf-8")) > 255:
        return "the new name is longer than 255 bytes"
    return windows_name_problem(name)


def _cut(part: Optional[str], target: str, swap: Dict[str, str]) -> bool:
    """The name *part* (a title or a group, as the parser read it) is not whole in *target* (the length rule cut it)."""
    if not part or not part.strip():
        return False
    text = "".join(swap.get(c, c) for c in part.strip())
    return text not in target and part.strip() not in target


_TITLE_SWAP = {"(": "[", ")": "]"}
_GROUP_SWAP = {"[": "(", "]": ")"}


def plan_files(files: Sequence[Tuple[str, Any]], *, namer: Namer, series_title: Optional[str],
               volume_of: Optional[Callable[[Decimal], Optional[Decimal]]] = None, limits: Any = None,
               listdir: Callable[[str], Iterable[str]] = os.listdir,
               exists: Callable[[str], bool] = os.path.lexists) -> List[FilePlan]:
    """What the scheme makes of each ``(absolute path, ParsedName)``: see the module docstring. Collisions are found per
    folder, comparing names case-insensitively, against the other files of the series AND everything else in the
    folder (a file that is not part of the series is never displaced)."""
    first: List[FilePlan] = []
    for path, parsed in files:
        path = os.path.normpath(str(path))
        name = os.path.basename(path)
        layer = _layer(parsed)
        if not exists(path):
            first.append(FilePlan(path, None, LEFT_ALONE, layer, "the file is gone since the last scan"))
            continue
        if parsed is None:
            first.append(FilePlan(path, None, LEFT_ALONE, layer, "the name could not be read"))
            continue
        ext = os.path.splitext(name)[1]
        try:
            target = namer.target_name(parsed, ext=ext, series_title=series_title, volume_of=volume_of,
                                       folder=os.path.dirname(path), limits=limits)
        except NamingUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - one odd name must not stop the series
            _log.warning("Renamer: naming %s failed (%s); left alone", path, type(exc).__name__, exc_info=True)
            first.append(FilePlan(path, None, LEFT_ALONE, layer, f"naming failed ({type(exc).__name__})"))
            continue
        if target is None:
            why = ("an extra (no chapter number)" if getattr(parsed, "is_extra", False)
                   else "the name says no volume or chapter number" if not getattr(parsed, "has_units", True)
                   else "the name could not be read with certainty")
            first.append(FilePlan(path, None, LEFT_ALONE, layer, why))
            continue
        problem = _name_problem(target)
        if problem:
            first.append(FilePlan(path, None, LEFT_ALONE, layer, f"{problem} ({target!r})"))
            continue
        title = getattr(parsed, "title", None)
        flags = dict(title_cut=_cut(title, target, _TITLE_SWAP), group_cut=_cut(getattr(parsed, "group", None),
                                                                                target, _GROUP_SWAP),
                     unit_words=bool(title and _UNIT_WORDS.search(title)))
        status = UNCHANGED if target == name else RENAME
        first.append(FilePlan(path, target, status, layer, "", **flags))
    return _collisions(first, listdir)


def _collisions(plans: List[FilePlan], listdir: Callable[[str], Iterable[str]]) -> List[FilePlan]:
    by_folder: Dict[str, List[int]] = defaultdict(list)
    for i, p in enumerate(plans):
        by_folder[os.path.normcase(p.folder)].append(i)
    out = list(plans)
    for indices in by_folder.values():
        folder = plans[indices[0]].folder
        ours = {_fold(plans[i].name) for i in indices}
        try:
            others = {_fold(n) for n in listdir(folder)} - ours
        except OSError:
            others = set()
        while True:                                   # until no two files end with one name (renames that lose fall back)
            final: Dict[str, List[int]] = defaultdict(list)
            for i in indices:
                p = out[i]
                final[_fold(p.target if p.status == RENAME else p.name)].append(i)
            changed = False
            for key, holders in final.items():
                renamers = [i for i in holders if out[i].status == RENAME]
                if not renamers:
                    continue
                if key in others:
                    why = f"a file named {out[renamers[0]].target!r} is already in the folder"
                elif len(holders) > 1:
                    names = ", ".join(sorted(out[i].name for i in holders))
                    why = (f"{len(holders)} files would be named {out[renamers[0]].target!r} ({names}); both keep their "
                           "names - see the duplicates review")
                else:
                    continue
                for i in renamers:
                    p = out[i]
                    out[i] = FilePlan(p.path, p.target, COLLISION, p.layer, why, p.title_cut, p.group_cut, p.unit_words)
                changed = True
                # An unchanged file that the others wanted keeps its name; mark the same-name case on it too, so the
                # listing shows both halves of the collision.
                for i in holders:
                    p = out[i]
                    if p.status == UNCHANGED and len(holders) > 1:
                        out[i] = FilePlan(p.path, p.target, COLLISION, p.layer, why, p.title_cut, p.group_cut,
                                          p.unit_words)
            if not changed:
                break
    return out


def order_moves(moves: Sequence[Tuple[str, str]], *, exists: Callable[[str], bool] = os.path.lexists
                ) -> List[Tuple[str, str]]:
    """*moves* (same-folder renames, no two to one name) in an order where each step's new name is free when it runs:
    a chain ``a -> b, b -> c`` runs ``b -> c`` first; a cycle (``a -> b, b -> a``) goes through a temporary name. A
    rename that changes only the case of the name is its own step."""
    pending: Dict[str, Tuple[str, str]] = {}
    for src, dst in moves:
        pending[_fold(src)] = (src, dst)
    out: List[Tuple[str, str]] = []
    used_temp: Set[str] = set()
    n = 0
    while pending:
        ready = [k for k, (src, dst) in pending.items()
                 if _fold(dst) not in pending or _fold(dst) == k]
        if ready:
            for k in sorted(ready):
                out.append(pending.pop(k))
            continue
        key = sorted(pending)[0]                     # a cycle: park one file under a temporary name
        src, dst = pending.pop(key)
        folder, name = os.path.split(src)
        ext = os.path.splitext(name)[1]
        while True:
            n += 1
            temp = os.path.join(folder, f"~mangalist-rename-{os.getpid()}-{n}{ext}")
            if not exists(temp) and _fold(temp) not in used_temp and _fold(temp) not in pending:
                break
        used_temp.add(_fold(temp))
        out.append((src, temp))
        pending[_fold(temp)] = (temp, dst)
    return out


def make_batches(previews: Iterable[SeriesPreview], size: int = BATCH_SIZE) -> List[RenameBatch]:
    """Series with renames, grouped per root into batches of about *size* files (a series is never split)."""
    per_root: Dict[Tuple[Optional[int], str], List[SeriesPreview]] = defaultdict(list)
    for s in previews:
        if s.renames:
            per_root[(s.root_id, s.root_path)].append(s)
    out: List[RenameBatch] = []
    for (root_id, root_path), series in per_root.items():
        current: List[SeriesPreview] = []
        count = 0
        for s in series:
            if current and count + len(s.renames) > size:
                out.append(RenameBatch(root_id, root_path, tuple(current)))
                current, count = [], 0
            current.append(s)
            count += len(s.renames)
        if current:
            out.append(RenameBatch(root_id, root_path, tuple(current)))
    return out


# --- the renamer over the library database --------------------------------------------------------------------------


def _parse_context(title: Optional[str], kind_hint: Optional[str], scheme: Optional[str]):
    from .parsing import ParseContext

    schemes = (scheme,) if isinstance(scheme, str) and scheme.strip() else ()
    for kw in (dict(schemes=schemes, kind_hint=kind_hint, series_title=title), dict(kind_hint=kind_hint,
                                                                                   series_title=title),
               dict(series_title=title)):
        try:
            return ParseContext(**kw)
        except Exception:  # noqa: BLE001 - a broken stored scheme / hint must not stop the preview
            continue
    return None


def _default_title_for(db) -> Callable[[Any, Any], Optional[str]]:
    """The series title volume names use: MangaPixer's (its own matched item, not a parent's), else None (the caller
    falls back to the folder's name). Owner, 2026-10-10: "the series title = MangaPixer's title"."""
    resolver = None

    def title_for(root, series) -> Optional[str]:
        nonlocal resolver
        try:
            if resolver is None:
                from .services.mangapixer import open_cache
                from .services.mangapixer.resolve import Resolver

                resolver = Resolver(open_cache(db))
            res = resolver.resolve(root.id, series.rel_path)
        except Exception:  # noqa: BLE001 - no MangaPixer data: the folder's name
            return None
        if res is None or res.inherited or res.link_state not in ("Confirmed", "Auto"):
            return None
        title = (res.record or {}).get("title") if isinstance(res.record, dict) else None
        return title.strip() if isinstance(title, str) and title.strip() else None

    return title_for


def _no_analysis_info(_db, _series) -> Optional[bool]:
    """MangaPixer's export says nothing about which archives it has analysed: MangaList cannot tell."""
    return None


def record_rescan(db, root_ids: Sequence[int]) -> str:
    """Scan the given roots and record the scan in the database (the series' units follow the new names now, not at the
    next scheduled rescan). Returns a short summary."""
    from .scanner import record_library_scan, scan_library

    roots = [r for r in db.list_roots() if r.id in set(root_ids)]
    if not roots:
        return ""
    result = scan_library(roots, db=db)
    record_library_scan(db, result)
    return f"rescan of {len(roots)} root(s) recorded"


def request_mangapixer_scans(db, series_ids: Sequence[int]) -> str:
    """Ask MangaPixer to scan the libraries of *series_ids* (as after filing; off when the owner switched the scan
    requests off in Settings > Automation). Returns the summary ("" when nothing was asked)."""
    from .downloads.options import KEY_SCAN_AFTER_FILING, get_flag
    from .services.mangapixer import open_cache
    from .services.mangapixer.scans import libraries_for_series, request_scans

    if not series_ids or not get_flag(db, KEY_SCAN_AFTER_FILING):
        return ""
    cache = open_cache(db)
    libraries = libraries_for_series(cache, series_ids)
    if not libraries:
        return ""
    return request_scans(cache, libraries).summary()


class Renamer:
    """The renamer's actions over one library database. Every method may block (disk, database, MangaPixer): the GUI
    calls them off the UI thread.

    Injected (all optional): *namer* (:class:`Namer`; default :class:`DefaultNamer`), *journal_factory* (``db ->``
    :class:`~mangalist.store.journal.Journal`), *title_for* (``(root, series) -> title or None``), *analysed*
    (``(db, series) -> True / False / None``), *rescan* (``(db, root ids) -> summary``; None: the caller rescans, as
    the GUI does), *request_scans* (``(db, series ids) -> summary``)."""

    def __init__(self, db, *, namer: Optional[Namer] = None, journal_factory: Optional[Callable[[Any], Any]] = None,
                 title_for: Optional[Callable[[Any, Any], Optional[str]]] = None,
                 analysed: Optional[Callable[[Any, Any], Optional[bool]]] = None,
                 rescan: Optional[Callable[[Any, Sequence[int]], str]] = record_rescan,
                 request_scans: Optional[Callable[[Any, Sequence[int]], str]] = request_mangapixer_scans,
                 batch_size: int = BATCH_SIZE):
        self.db = db
        self.namer: Namer = namer if namer is not None else DefaultNamer()
        self._journal_factory = journal_factory
        self._title_for = title_for
        self._analysed = analysed or _no_analysis_info
        self._rescan = rescan
        self._request_scans = request_scans
        self.batch_size = max(1, int(batch_size))

    # --- small helpers --------------------------------------------------------------------------------------------

    def journal(self):
        from .store.journal import Journal

        return (self._journal_factory or Journal)(self.db)

    def available(self) -> bool:
        return naming_available(self.namer)

    def limits(self):
        return self.namer.limits(windows_server(self.db))

    def _title(self, root, series) -> Optional[str]:
        if self._title_for is None:
            self._title_for = _default_title_for(self.db)
        try:
            return self._title_for(root, series)
        except Exception:  # noqa: BLE001
            return None

    def _volume_of(self, series_id: Optional[int]):
        if series_id is None:
            return None
        try:
            return self.namer.volume_lookup(self.db, series_id)
        except NamingUnavailable:
            raise
        except Exception:  # noqa: BLE001 - no volume list: chapters are named without a volume
            _log.debug("Renamer: no volume lookup for series %s", series_id, exc_info=True)
            return None

    def series_by_id(self, series_id: int):
        with self.db.connect() as con:
            row = con.execute("SELECT root_id, rel_path FROM series WHERE id = ?", (int(series_id),)).fetchone()
        return self.db.get_series(row["root_id"], row["rel_path"]) if row else None

    def series_id_for_folder(self, folder) -> Optional[int]:
        s = self.db.series_for_folder(folder)
        return s.id if s is not None else None

    # --- previews -------------------------------------------------------------------------------------------------

    def files_of(self, root, series) -> List[Tuple[str, Any]]:
        """``(absolute path, ParsedName)`` of the series' own archives as the last scan recorded them, parsed again
        from their names (the root's scheme and the series' "volumes or chapters?" answer)."""
        from .parsing import parse_name

        folder = os.path.join(root.path, *series.rel_path.split("/"))
        ctx = _parse_context(os.path.basename(folder), series.kind_hint, getattr(root, "naming_scheme", None))
        rels = sorted({u.rel_path for u in self.db.list_units(series.id)}, key=lambda r: (r.casefold(), r))
        out = []
        for rel in rels:
            path = os.path.join(folder, *rel.split("/"))
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            out.append((path, parse_name(os.path.basename(path), ctx, file_size=size)))
        return out

    def _preview(self, root, series, files: Sequence[Tuple[str, Any]], limits: Any) -> SeriesPreview:
        folder = os.path.join(root.path, *series.rel_path.split("/")) if series.rel_path else root.path
        title = self._title(root, series) or os.path.basename(folder)
        preview = SeriesPreview(series_id=series.id, root_id=root.id, root_path=root.path, folder=folder,
                                title=os.path.basename(folder), series_title=title)
        try:
            preview.analysed = self._analysed(self.db, series)
        except Exception:  # noqa: BLE001 - not known
            preview.analysed = None
        if not files:
            preview.error = "no files recorded for this series (rescan first)"
            return preview
        preview.files = plan_files(files, namer=self.namer, series_title=title, volume_of=self._volume_of(series.id),
                                   limits=limits)
        return preview

    def preview_series(self, series_id: int) -> SeriesPreview:
        """Old -> new for every file of one series (nothing is changed)."""
        series = self.series_by_id(series_id)
        if series is None:
            raise LookupError(f"no series {series_id} in the library database")
        root = self.db.get_root(series.root_id)
        if root is None:
            raise LookupError(f"the library of series {series_id} is no longer configured")
        preview = self._preview(root, series, self.files_of(root, series), self.limits())
        self._log_preview([preview], f"series {preview.title!r}")
        return preview

    def dry_run(self, root_ids: Optional[Sequence[int]] = None, series_ids: Optional[Sequence[int]] = None,
                should_stop: Optional[Callable[[], bool]] = None,
                progress: Optional[Callable[[int, int, str], None]] = None) -> DryRun:
        """The dry run over the given roots (None: every root) or series: nothing is changed. Roots set to Off are
        included only when named."""
        limits = self.limits()
        roots = self.db.list_roots()
        wanted_series = {int(s) for s in series_ids} if series_ids is not None else None
        if root_ids is not None:
            roots = [r for r in roots if r.id in {int(i) for i in root_ids}]
        elif wanted_series is None:
            roots = [r for r in roots if r.enforce_naming != ENFORCE_OFF]
        dry = DryRun()
        todo = []
        for root in roots:
            for series in self.db.list_series(root.id):
                if getattr(series, "status", "present") != "present":
                    continue
                if wanted_series is not None and series.id not in wanted_series:
                    continue
                todo.append((root, series))
        per_root: Dict[int, RootDryRun] = {}
        for n, (root, series) in enumerate(todo, start=1):
            if should_stop is not None and should_stop():
                dry.warnings.append("stopped before every series was looked at")
                break
            if progress is not None:
                progress(n, len(todo), series.rel_path)
            rdr = per_root.get(root.id)
            if rdr is None:
                rdr = per_root[root.id] = RootDryRun(root.id, root.name, root.path, root.enforce_naming)
                dry.roots.append(rdr)
            rdr.series.append(self._preview(root, series, self.files_of(root, series), limits))
        dry.warnings.extend(self._warnings(dry))
        self._log_preview(dry.series, "dry run of " + (", ".join(r.name for r in dry.roots) or "nothing"))
        return dry

    def _warnings(self, dry: DryRun) -> List[str]:
        out = []
        moving = [s for s in dry.series if s.renames]
        not_analysed = [s for s in moving if s.analysed is False]
        if not_analysed:
            out.append(NOT_ANALYSED_WARNING.format(n=len(not_analysed)))
        if any(s.analysed is None for s in moving):
            out.append(ANALYSIS_WARNING)
        return out

    @staticmethod
    def _log_preview(previews: Sequence[SeriesPreview], what: str) -> None:
        c: Counter = Counter()
        for s in previews:
            c.update(s.counts())
            if s.collisions:
                _log.warning("Renamer: %s: %d name collision(s), those files keep their names: %s", s.title,
                             len(s.collisions), "; ".join(f"{f.name} ({f.reason})" for f in s.collisions[:5]))
        _log.info("Renamer: %s: %d to rename, %d already named by the scheme, %d left alone, %d collision(s) in %d "
                  "series", what, c[RENAME], c[UNCHANGED], c[LEFT_ALONE], c[COLLISION], len(previews))

    # --- rename pending ---------------------------------------------------------------------------------------------

    def pending_counts(self, entries: Iterable[Any]) -> Dict[str, int]:
        """Per series folder (``str(entry.folder)``): how many files the scheme would rename - from a scan's entries
        (their parsed names), for the List's "Rename pending". Roots set to Off, and entries outside a known root,
        never count. Empty when the naming scheme is not in this build."""
        if not self.available():
            return {}
        roots = {r.id: r for r in self.db.list_roots()}
        limits = self.limits()
        out: Dict[str, int] = {}
        for entry in entries:
            root = roots.get(getattr(entry, "root_id", None))
            if root is None or root.enforce_naming == ENFORCE_OFF:
                continue
            series = self.db.series_for_folder(entry.folder)
            if series is None:
                continue
            files = []
            for hit in getattr(entry, "inventory_files", getattr(entry, "files", ())):
                parsed = getattr(hit, "parsed", None)
                if parsed is None:
                    continue
                files.append((str(hit.path), parsed))
            if not files:
                continue
            try:
                preview = self._preview(root, series, files, limits)
            except NamingUnavailable:
                return {}
            n = len(preview.renames)
            if n:
                out[str(entry.folder)] = n
        _log.info("Renamer: %d series have files not named by the scheme (rename pending)", len(out))
        return out

    # --- applying -------------------------------------------------------------------------------------------------

    def batches(self, previews: Iterable[SeriesPreview]) -> List[RenameBatch]:
        return make_batches(previews, self.batch_size)

    def apply(self, batch: RenameBatch, *, after: bool = True) -> BatchResult:
        """Rename one batch through the journal (one plan, the root's lock). Each series is previewed again first;
        only renames that are still exactly what the preview showed are made (anything else is listed as skipped).
        With *after*: the rescan is recorded and MangaPixer is asked to scan the libraries touched."""
        from .store.journal import PlanStateError, StepRefused
        from .store.lock import LockError

        result = BatchResult(status="nothing", root_ids=[batch.root_id] if batch.root_id is not None else [])
        limits = self.limits()
        root = self.db.get_root(batch.root_id) if batch.root_id is not None else None
        if root is None or os.path.normcase(os.path.normpath(root.path)) != os.path.normcase(
                os.path.normpath(batch.root_path)):
            result.status, result.message = "failed", "its library root is no longer configured"
            _log.warning("Renamer: batch not applied: %s", result.message)
            return result
        moves: List[Tuple[str, str]] = []
        for shown in batch.series:
            series = self.series_by_id(shown.series_id) if shown.series_id is not None else None
            if series is None:
                result.skipped += [(f.name, "the series is no longer in the library") for f in shown.renames]
                continue
            now = self._preview(root, series, self.files_of(root, series), limits)
            current = {(f.path, f.new_path) for f in now.renames}
            ok = [(f.path, f.new_path) for f in shown.renames if (f.path, f.new_path) in current]
            for f in shown.renames:
                if (f.path, f.new_path) not in current:
                    result.skipped.append((f.name, "changed since the preview - preview again"))
            if ok:
                moves.extend(ok)
                result.series_ids.append(series.id)
        for name, why in result.skipped:
            _log.warning("Renamer: %s not renamed: %s", name, why)
        if not moves:
            result.message = "nothing to rename" + (" (everything changed since the preview)" if result.skipped else "")
            _log.info("Renamer: batch in %s: %s", batch.root_path, result.message)
            return result
        journal = self.journal()
        ordered = order_moves(moves)
        note = f"{len(moves)} file(s) in {len(result.series_ids)} series"
        try:
            plan = journal.plan(PLAN_REASON, ordered, root_path=root.path, note=note)
        except StepRefused as exc:
            result.status, result.message = "failed", f"the journal refused the renames: {exc}"
            _log.warning("Renamer: batch in %s refused: %s", root.path, exc)
            return result
        result.plan_id = plan.id
        _log.info("Renamer: plan %d recorded for %s: %s", plan.id, root.name, note)
        try:
            plan = journal.apply(plan.id)
        except LockError as exc:
            result.status, result.message = "busy", f"the library is busy ({exc}); try again in a moment"
            _log.warning("Renamer: plan %d not applied: %s", plan.id, result.message)
            return result
        except PlanStateError as exc:
            result.status, result.message = "failed", str(exc)
            _log.warning("Renamer: plan %d not applied: %s", plan.id, exc)
            return result
        self._settle(result, plan, ordered)
        if after and result.renamed:
            result.after = self._after(result.root_ids, result.series_ids)
        return result

    def _settle(self, result: BatchResult, plan, ordered: Sequence[Tuple[str, str]]) -> None:
        temps = {os.path.normcase(d) for _s, d in ordered if os.path.basename(d).startswith("~mangalist-rename-")}
        done = [s for s in plan.steps if s.state == "done" and os.path.normcase(s.dst) not in temps]
        result.renamed = len(done)
        for s in plan.steps:
            if s.state == "failed":
                result.failed.append((os.path.basename(s.src), s.error or "failed"))
        if plan.status == "applied":
            result.status = "applied"
            result.message = f"{result.renamed} file(s) renamed"
            _log.info("Renamer: plan %d applied: %d file(s) renamed in %d series", plan.id, result.renamed,
                      len(result.series_ids))
        else:
            result.status = "partly" if result.renamed else "failed"
            stuck = result.failed[0] if result.failed else ("", plan.status)
            result.message = (f"{result.renamed} file(s) renamed; stopped at {stuck[0]}: {stuck[1]} (Undo puts the "
                              "renamed ones back)")
            _log.warning("Renamer: plan %d stopped: %s", plan.id, result.message)

    def _after(self, root_ids: Sequence[int], series_ids: Sequence[int]) -> str:
        notes = []
        if self._rescan is not None and root_ids:
            try:
                note = self._rescan(self.db, list(root_ids))
            except Exception as exc:  # noqa: BLE001 - the renames are done and recorded
                _log.warning("Renamer: recording the rescan failed (%s)", type(exc).__name__, exc_info=True)
                note = f"rescan failed ({type(exc).__name__})"
            if note:
                notes.append(note)
        if self._request_scans is not None and series_ids:
            try:
                note = self._request_scans(self.db, list(series_ids))
            except Exception as exc:  # noqa: BLE001
                _log.warning("Renamer: asking MangaPixer to scan failed (%s)", type(exc).__name__, exc_info=True)
                note = f"MangaPixer scan request failed ({type(exc).__name__})"
            if note:
                notes.append(note)
                _log.info("Renamer: %s", note)
        return "; ".join(notes)

    def apply_all(self, batches: Sequence[RenameBatch], should_stop: Optional[Callable[[], bool]] = None,
                  progress: Optional[Callable[[int, int], None]] = None) -> List[BatchResult]:
        """Apply *batches* one after another (one plan each); stops at a batch that did not fully apply. The rescan and
        the MangaPixer scan request run once, after the last batch."""
        out: List[BatchResult] = []
        for n, batch in enumerate(batches, start=1):
            if should_stop is not None and should_stop():
                break
            if progress is not None:
                progress(n, len(batches))
            res = self.apply(batch, after=False)
            out.append(res)
            if res.status not in ("applied", "nothing"):
                break
        root_ids = sorted({r for res in out if res.renamed for r in res.root_ids})
        series_ids = sorted({s for res in out if res.renamed for s in res.series_ids})
        if out and series_ids:
            out[-1].after = self._after(root_ids, series_ids)
        return out

    # --- undo and history -----------------------------------------------------------------------------------------

    def history(self, limit: int = 10) -> List[BatchRecord]:
        """The renamer's journal plans, newest first."""
        out = []
        for plan in reversed(self.journal().list_plans()):
            if plan.reason != PLAN_REASON:
                continue
            temps = [s for s in plan.steps if os.path.basename(s.dst).startswith("~mangalist-rename-")]
            out.append(BatchRecord(plan.id, plan.status, plan.created_at, len(plan.steps) - len(temps),
                                   sum(1 for s in plan.steps if s.state == "done" and s not in temps), plan.note or ""))
            if len(out) >= limit:
                break
        return out

    def last_undoable(self) -> Optional[BatchRecord]:
        return next((b for b in self.history(50) if b.can_undo), None)

    def undo(self, plan_id: int) -> BatchResult:
        """Put one batch's files back under their old names (the journal plan undone, last step first, no-replace)."""
        from .store.journal import PlanStateError
        from .store.lock import LockError

        journal = self.journal()
        result = BatchResult(status="failed", plan_id=plan_id)
        try:
            plan = journal.get_plan(plan_id)
        except PlanStateError as exc:
            result.message = str(exc)
            return result
        if plan.reason != PLAN_REASON:
            result.message = f"plan {plan_id} is not a renamer batch"
            return result
        try:
            after = journal.undo(plan_id)
        except LockError as exc:
            result.status, result.message = "busy", f"the library is busy ({exc}); try again in a moment"
            _log.warning("Renamer: plan %d not undone: %s", plan_id, result.message)
            return result
        except PlanStateError as exc:
            result.message = str(exc)
            _log.warning("Renamer: plan %d not undone: %s", plan_id, exc)
            return result
        undone = [s for s in after.steps if s.state == "undone"
                  and not os.path.basename(s.dst).startswith("~mangalist-rename-")]
        result.renamed = len(undone)
        root = self.db.root_for_path(after.root_path) if after.root_path else None
        result.root_ids = [root.id] if root is not None else []
        series_ids = set()
        for s in undone:
            sid = self.series_id_for_folder(os.path.dirname(s.src))
            if sid is not None:
                series_ids.add(sid)
        result.series_ids = sorted(series_ids)
        if after.status == "undone":
            result.status = "undone"
            result.message = f"{len(undone)} file(s) back under their old names"
            _log.info("Renamer: plan %d undone: %d file(s) back under their old names", plan_id, len(undone))
        else:
            stuck = next((s for s in after.steps if s.state == "done" and s.error), None)
            result.status = "partly"
            result.message = (f"{len(undone)} file(s) back; not all: "
                              f"{os.path.basename(stuck.dst) + ': ' + stuck.error if stuck else after.status}")
            _log.warning("Renamer: plan %d only partly undone: %s", plan_id, result.message)
        if undone:
            result.after = self._after(result.root_ids, result.series_ids)
        return result

    # --- the guided start and the automatic pass -------------------------------------------------------------------

    def mark_guided(self, root_ids: Iterable[int]) -> None:
        """The owner started converting these roots from the Renamer window (a root or every root, not a pilot
        series): from now on a root set to "Rename automatically" is kept in the scheme by the scheduled pass."""
        have = guided_roots(self.db)
        new = {int(r) for r in root_ids if r is not None} - have
        if new:
            self.db.set_setting(KEY_GUIDED_ROOTS, sorted(have | new))
            _log.info("Renamer: guided conversion started for root(s) %s", ", ".join(str(r) for r in sorted(new)))

    def automatic_pass(self, should_stop: Optional[Callable[[], bool]] = None, limit: int = AUTO_LIMIT) -> AutoReport:
        """The scheduled pass (headless rescan): rename what is pending in roots set to "Rename automatically" that the
        owner has started converting from the window - at most *limit* files per run, in journal batches, each logged.
        Series MangaPixer has certainly not analysed yet wait. Then one rescan and one MangaPixer scan request."""
        report = AutoReport()
        roots = [r for r in self.db.list_roots() if r.enforce_naming == ENFORCE_AUTOMATIC]
        if not roots:
            report.skipped = "no library renames automatically"
            return report
        if not self.available():
            report.skipped = "the naming scheme is not in this build"
            _log.info("Renamer: automatic renames skipped: %s", report.skipped)
            return report
        guided = guided_roots(self.db)
        budget = max(0, int(limit))
        started = time.monotonic()
        for root in roots:
            if root.id not in guided:
                report.waiting.append(root.name)
                _log.info("Renamer: %s renames automatically only after you start its conversion in MangaList "
                          "(List > Rename library...); nothing renamed", root.name)
                continue
            if budget <= 0 or (should_stop is not None and should_stop()):
                break
            report.roots.append(root.name)
            dry = self.dry_run(root_ids=[root.id], should_stop=should_stop)
            ready = [s for s in dry.series if s.renames and s.analysed is not False]
            for s in dry.series:
                if s.renames and s.analysed is False:
                    _log.warning("Renamer: %s waits: MangaPixer has not analysed its archives yet", s.title)
            for batch in self.batches(ready):
                if budget <= 0 or (should_stop is not None and should_stop()):
                    break
                if batch.n_files > budget:
                    kept = []
                    count = 0
                    for s in batch.series:
                        if count + len(s.renames) > budget:
                            break
                        kept.append(s)
                        count += len(s.renames)
                    if not kept:
                        break
                    batch = RenameBatch(batch.root_id, batch.root_path, tuple(kept))
                res = self.apply(batch, after=False)
                report.batches.append(res)
                budget -= res.renamed
                _log.info("Renamer: automatic batch in %s: %s", root.name, res.message)
                if res.status not in ("applied", "nothing"):
                    break
        root_ids = sorted({r for b in report.batches if b.renamed for r in b.root_ids})
        series_ids = sorted({s for b in report.batches if b.renamed for s in b.series_ids})
        if series_ids:
            report.after = self._after(root_ids, series_ids)
        _log.info("Renamer: automatic pass: %s (%.1f s)", report.summary(), time.monotonic() - started)
        return report


__all__ = [
    "ANALYSIS_WARNING", "AUTO_LIMIT", "AutoReport", "BATCH_SIZE", "BatchRecord", "BatchResult", "COLLISION",
    "DefaultNamer", "DryRun", "ENFORCE_LABELS", "FilePlan", "KEY_GUIDED_ROOTS", "KEY_WINDOWS_SERVER", "LEFT_ALONE",
    "Namer", "NamingUnavailable", "PLAN_REASON", "RENAME", "RenameBatch", "Renamer", "RootDryRun", "SeriesPreview",
    "UNCHANGED", "guided_roots", "make_batches", "naming_available", "normalize_server", "order_moves", "plan_files",
    "record_rescan", "request_mangapixer_scans", "set_windows_server", "windows_server",
]
