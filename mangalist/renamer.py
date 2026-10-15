"""The library renamer: bring every series' file names to the naming scheme, safely (owner, 2026-10-10: "Let's do the
bulk rename!" - a guided one-time conversion of every root, then ongoing enforcement). No Qt here.

**What a file is called** is the naming lane's answer (:mod:`mangalist.naming`, ``target_name``): this module never
builds a name itself. The namer is INJECTED (:class:`Namer`); the default (:class:`DefaultNamer`) imports
``mangalist.naming`` lazily at call time, so this module works (and is tested with a fake) before that module exists.
Per file the answer is one of:

- **unchanged** - the name already is the scheme's;
- **rename** - old -> new, in the same folder (a file's folder never changes: only the SERIES folder itself can be
  renamed, see below);
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

**Series folders** (owner, 2026-10-15, answer 6: "Always use "<romaji> [<English>]", similar to animefetch. Folders not in
this scheme are rename candidates."): a top-level series folder whose name is not :mod:`mangalist.folder_scheme`'s
``<romaji> [<English>]`` is a rename candidate too - in the preview (its own row), in Rename pending (it counts as one),
in the window's batches (ONE journal step per folder, after the series' file renames; the series row, its link and its
data follow through the journal's folder-move follow, :meth:`~mangalist.store.series.SeriesMixin.relink_folder`).
The romaji is the series' MangaUpdates title (MangaPixer's record, else MangaList's own confirmed match); without one
the folder is left alone. Subseries folders (``Parent/Side 1 - ...``) are never renamed. **A folder is renamed only by
the owner's explicit Rename in the window**: the automatic pass (and filing) never renames a folder, whatever the
library's setting (:meth:`Renamer.apply` takes ``folders=True`` only from the window). Answer 12 ("Titles should
follow the scheme in answer 6 and should change"): the series title volume names use is the same scheme name, so a
changed match (e.g. MangaPixer's Switch to MangaUpdates) makes the volumes Rename pending as well.

**Rename pending** (:meth:`Renamer.pending_counts`): per series folder, how many files the scheme would rename, plus one
when the folder itself would be renamed (roots set to Off never count). The List shows it as a flag, a column and a
filter.

Logging: INFO for every dry run / plan / apply / undo with counts; WARNING for refusals and collisions.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple

_log = logging.getLogger(__name__)

KEY_WINDOWS_SERVER = "naming.windows_server"     # Settings > Library: the server name of the Windows path (optional)
KEY_GUIDED_ROOTS = "renamer.guided_roots"         # root ids the owner started converting from the Renamer window
KEY_AUTOMATIC_OFFERED = "renamer.automatic_offered"  # root ids already asked "Rename automatically from now on?"
META_AUTOMATIC_SEEN = "renamer.automatic_seen"    # (meta) per root: the naming inputs + the series found unchanged
PLAN_REASON = "rename to the scheme"
BATCH_SIZE = 200            # files per journal plan (whole series only: a bigger series is a batch of its own)
AUTO_LIMIT = 500            # files the automatic pass renames per run at most (the rest waits for the next run)

ENFORCE_OFF, ENFORCE_ASK, ENFORCE_AUTOMATIC = "off", "ask", "automatic"
ENFORCE_LABELS = {ENFORCE_OFF: "Off", ENFORCE_ASK: "Ask before renaming", ENFORCE_AUTOMATIC: "Rename automatically"}

UNCHANGED, RENAME, LEFT_ALONE, COLLISION = "unchanged", "rename", "left alone", "collision"

FOLDER_NOTE = ("Series folders are renamed to \"<romaji> [<English>]\" only when you press Rename here - never "
               "automatically. MangaList asks MangaPixer to scan afterwards so it sees the new folder names.")
TITLE_NOTE = ("Some volume names change only because of the series title \"<romaji> [<English>]\": like folder names, "
              "they are renamed only when you press Rename here - never automatically.")
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
    """The Windows server name of the length rule (``MYSERVER``), or None when none is set."""
    try:
        value = db.get_setting(KEY_WINDOWS_SERVER, None)
    except Exception:  # noqa: BLE001 - no setting: no Windows path rule
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_server(text: Optional[str]) -> Optional[str]:
    """``\\\\MYSERVER\\`` / ``myserver`` -> the server name; None for empty; :class:`ValueError` when it is not a
    plain server name (letters, digits, ``-``, ``_``, ``.``)."""
    name = (text or "").strip().strip("\\/").strip()
    if not name:
        return None
    if not _HOSTNAME.match(name):
        raise ValueError("a server name has letters, digits, '-', '_' or '.' only (e.g. MYSERVER)")
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
    needs_kind: bool = False        # left alone: a bare number whose kind only the series' "volumes or chapters?" settles
    title_held: bool = False        # renamed: the scheme's series title changed the name (waits for the owner's Rename)

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
    # the series folder itself (top-level series only; None: not looked at - a subseries, or no folder scheme)
    folder_target: Optional[str] = None          # the scheme's folder name ``<romaji> [<English>]``
    folder_status: Optional[str] = None          # UNCHANGED | RENAME | LEFT_ALONE | COLLISION
    folder_reason: str = ""                      # left alone / collision: why

    def of(self, status: str) -> List[FilePlan]:
        return [f for f in self.files if f.status == status]

    @property
    def renames(self) -> List[FilePlan]:
        return self.of(RENAME)

    @property
    def collisions(self) -> List[FilePlan]:
        return self.of(COLLISION)

    @property
    def needs_kind(self) -> List[FilePlan]:
        """Files left alone until the series' "volumes or chapters?" is answered (the Renamer window asks)."""
        return [f for f in self.files if f.needs_kind]

    @property
    def left_alone(self) -> List[FilePlan]:
        return self.of(LEFT_ALONE)

    @property
    def unchanged(self) -> List[FilePlan]:
        return self.of(UNCHANGED)

    @property
    def moves(self) -> List[Tuple[str, str]]:
        return [(f.path, f.new_path) for f in self.renames if f.new_path]

    @property
    def auto_renames(self) -> List[FilePlan]:
        """The renames the automatic pass may make: not the ones a new series title causes (they wait for the owner)."""
        return [f for f in self.renames if not f.title_held]

    @property
    def held_renames(self) -> List[FilePlan]:
        return [f for f in self.renames if f.title_held]

    @property
    def folder_rename(self) -> bool:
        """The series folder would be renamed to the scheme's name."""
        return self.folder_status == RENAME and bool(self.folder_target)

    @property
    def new_folder(self) -> Optional[str]:
        return os.path.join(os.path.dirname(self.folder), self.folder_target) if self.folder_rename else None

    @property
    def changes(self) -> int:
        """What renaming this series would change: its files to rename, plus one for its folder."""
        return len(self.renames) + (1 if self.folder_rename else 0)

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
        return [s for s in self.series if s.renames or s.folder_rename]

    @property
    def folders_to_rename(self) -> List[SeriesPreview]:
        return [s for s in self.series if s.folder_rename]


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

    @property
    def folders_to_rename(self) -> List[SeriesPreview]:
        """Series whose folder would be renamed to the scheme's ``<romaji> [<English>]``."""
        return [s for s in self.series if s.folder_rename]

    def folders_where(self, *statuses: str) -> List[SeriesPreview]:
        return [s for s in self.series if s.folder_status in statuses]

    def summary(self) -> str:
        """One line of counts (the log and the window's headline)."""
        series = sum(1 for s in self.series if s.renames)
        line = (f"{self.count(RENAME)} file(s) to rename in {series} series, {self.count(UNCHANGED)} already named by "
                f"the scheme, {self.count(LEFT_ALONE)} left alone, {self.count(COLLISION)} name collision(s)")
        folders = len(self.folders_to_rename)
        return line + (f", {folders} series folder(s) to rename" if folders else "")


@dataclass(frozen=True)
class RenameBatch:
    """Whole series of one root, renamed as one journal plan."""

    root_id: Optional[int]
    root_path: str
    series: Tuple[SeriesPreview, ...]

    folders: bool = False                        # the series folders are renamed too (the owner's Rename only)
    titles: bool = True                          # renames a new series title causes too (False: the automatic pass)

    def files_of(self, s: SeriesPreview) -> List[FilePlan]:
        return s.renames if self.titles else s.auto_renames

    @property
    def n_files(self) -> int:
        return sum(len(self.files_of(s)) for s in self.series)

    @property
    def n_folders(self) -> int:
        return sum(1 for s in self.series if s.folder_rename) if self.folders else 0


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
    folders: int = 0                             # series folders renamed (or put back) - not counted in ``renamed``


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
    unchanged: int = 0                  # series not looked at again: nothing changed since a pass found nothing to do
    held: int = 0                       # renames a new series title causes: they wait for the owner's Rename

    @property
    def renamed(self) -> int:
        return sum(b.renamed for b in self.batches)

    def summary(self) -> str:
        if self.skipped:
            return f"renames: {self.skipped}"
        parts = [f"{self.renamed} file(s) renamed in {len(self.batches)} batch(es)"] if self.batches else []
        if self.waiting:
            parts.append(f"{len(self.waiting)} root(s) wait for the first guided rename")
        if self.held:
            parts.append(f"{self.held} file(s) with a new series title wait for your Rename")
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


def _has_new_title(name: str, title: Optional[str]) -> bool:
    """*name* already starts with the new series title (``<new title> - Vol. 1.cbz`` -> ``<new title> - Vol. 001.cbz`` is
    a padding fix, not a title change)."""
    return bool(title) and name.casefold().startswith(title.casefold().rstrip() + " ")


def plan_files(files: Sequence[Tuple[str, Any]], *, namer: Namer, series_title: Optional[str],
               volume_of: Optional[Callable[[Decimal], Optional[Decimal]]] = None, limits: Any = None,
               listdir: Callable[[str], Iterable[str]] = os.listdir,
               exists: Callable[[str], bool] = os.path.lexists, legacy_title: Optional[str] = None) -> List[FilePlan]:
    """What the scheme makes of each ``(absolute path, ParsedName)``: see the module docstring. Collisions are found per
    folder, comparing names case-insensitively, against the other files of the series AND everything else in the
    folder (a file that is not part of the series is never displaced).

    *legacy_title*: the series title volume names used before the folder scheme (the record's title, else the folder's
    name). A rename whose target differs from the target that title gives - the scheme's ``<romaji> [<English>]``
    changed the name, alone or with another fix (a file is never renamed twice) - is marked
    :attr:`FilePlan.title_held` unless the file already carries the new title: the automatic pass leaves it for the
    owner's Rename (integrator, 2026-10-15: 2615 such renames in the owner's library, not previewed yet)."""
    first: List[FilePlan] = []
    for path, parsed in files:
        path = os.path.normpath(str(path))
        name = os.path.basename(path)
        layer = _layer(parsed)
        if not exists(path):
            first.append(FilePlan(path, None, LEFT_ALONE, layer, "the file is gone since the last scan"))
            continue
        if os.path.islink(path):
            first.append(FilePlan(path, None, LEFT_ALONE, layer, "a link to another file (left as it is)"))
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
            bare = getattr(parsed, "number", None) is not None and (
                str(getattr(getattr(parsed, "kind", None), "value", "")) == "unknown" or getattr(parsed, "guessed", False))
            why = (("a comics extra: not counted as chapter "
                    f"{parsed.chapter}, so it keeps its name" if getattr(parsed, "chapter", None) is not None
                    else "an extra (no chapter number)") if getattr(parsed, "is_extra", False)
                   else "a bare number: chapters or volumes? (answer above)" if bare
                   else "the name says no volume or chapter number" if not getattr(parsed, "has_units", True)
                   else "the name could not be read with certainty")
            first.append(FilePlan(path, None, LEFT_ALONE, layer, why, needs_kind=bare))
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
        if status == RENAME and legacy_title and legacy_title != series_title:
            try:
                before = namer.target_name(parsed, ext=ext, series_title=legacy_title, volume_of=volume_of,
                                           folder=os.path.dirname(path), limits=limits)
            except Exception:  # noqa: BLE001 - cannot tell: hold it for the owner
                before = None
            flags["title_held"] = before != target and (before == name or not _has_new_title(name, series_title))
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
                    out[i] = replace(p, status=COLLISION, reason=why)
                changed = True
                # An unchanged file that the others wanted keeps its name; mark the same-name case on it too, so the
                # listing shows both halves of the collision.
                for i in holders:
                    p = out[i]
                    if p.status == UNCHANGED and len(holders) > 1:
                        out[i] = replace(p, status=COLLISION, reason=why)
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


def make_batches(previews: Iterable[SeriesPreview], size: int = BATCH_SIZE, *, folders: bool = False,
                 titles: Optional[bool] = None) -> List[RenameBatch]:
    """Series with renames, grouped per root into batches of about *size* files (a series is never split). With
    *folders* (the owner's Rename in the window, never the automatic pass) a series whose folder is renamed counts too,
    its folder as one more step. *titles* (default: as *folders*): the renames a new series title causes are included;
    without it (the automatic pass) they wait for the owner's Rename."""
    titles = folders if titles is None else titles

    def weight(s: SeriesPreview) -> int:
        files = len(s.renames if titles else s.auto_renames)
        return files + (1 if folders and s.folder_rename else 0)

    per_root: Dict[Tuple[Optional[int], str], List[SeriesPreview]] = defaultdict(list)
    for s in previews:
        if weight(s):
            per_root[(s.root_id, s.root_path)].append(s)
    out: List[RenameBatch] = []
    for (root_id, root_path), series in per_root.items():
        current: List[SeriesPreview] = []
        count = 0
        for s in series:
            if current and count + weight(s) > size:
                out.append(RenameBatch(root_id, root_path, tuple(current), folders, titles))
                current, count = [], 0
            current.append(s)
            count += weight(s)
        if current:
            out.append(RenameBatch(root_id, root_path, tuple(current), folders, titles))
    return out


# --- the series folder (lane A, 2026-10-15) -------------------------------------------------------------------------

FOLDER_NO_TITLE = "no MangaUpdates title yet (MangaPixer has not matched the series): the folder keeps its name"


def _name_key(name: str) -> str:
    """A folder name as the library compares names: NFC, case-insensitive (the library is shared over SMB)."""
    import unicodedata

    return unicodedata.normalize("NFC", name).casefold()


def folder_names(root_path: str) -> Set[str]:
    """The names in a library folder, as :func:`_name_key` compares them (empty when it cannot be read)."""
    try:
        return {_name_key(n) for n in os.listdir(root_path)}
    except OSError:
        return set()


def plan_folder(current: str, target: Optional[str], *, names: Set[str],
                excluded: Callable[[str], bool] = lambda _name: False, no_title: str = FOLDER_NO_TITLE
                ) -> Tuple[str, str]:
    """``(status, reason)`` of renaming the series folder *current* to *target* (the scheme's name; None: no title) in
    a library folder holding *names* (:func:`folder_names`): UNCHANGED, RENAME, LEFT_ALONE (no title, an invalid name,
    a name the library excludes) or COLLISION (another folder has that name, in any case). Changing only the case of
    the folder's own name is a rename."""
    from .store.names import windows_name_problem

    if not target:
        return LEFT_ALONE, no_title
    if target == current:
        return UNCHANGED, ""
    problem = windows_name_problem(target)
    if problem or target.startswith("."):
        return LEFT_ALONE, f"the scheme's name {target!r} cannot be used ({problem or 'a hidden folder'})"
    if excluded(target):
        return LEFT_ALONE, f"the library excludes a folder called {target!r} (it would vanish from the List)"
    if _name_key(target) in names and _name_key(target) != _name_key(current):
        return COLLISION, f"a folder named {target!r} is already in the library"
    return RENAME, ""


def _root_excludes(root) -> Callable[[str], bool]:
    try:
        rules = root.exclusion_set()
    except Exception:  # noqa: BLE001 - unreadable exclusions: nothing excluded
        return lambda _name: False

    def excluded(name: str) -> bool:
        try:
            return bool(rules.excludes(name, True))
        except Exception:  # noqa: BLE001
            return False

    return excluded


def mark_folder_collisions(previews: Iterable[SeriesPreview]) -> None:
    """Two series of one library that would get the same folder name (two folders matched to one MangaUpdates series):
    neither is renamed - both are listed as a collision."""
    by_target: Dict[Tuple[Optional[int], str], List[SeriesPreview]] = defaultdict(list)
    for s in previews:
        if s.folder_status in (RENAME, UNCHANGED) and s.folder_target:
            by_target[(s.root_id, _name_key(s.folder_target))].append(s)
    for holders in by_target.values():
        if len(holders) < 2:
            continue
        names = ", ".join(sorted(s.title for s in holders))
        for s in holders:
            if s.folder_status == RENAME:
                s.folder_status = COLLISION
                s.folder_reason = (f"{len(holders)} series folders would be named {s.folder_target!r} ({names}); "
                                   "they keep their names - see the duplicates review")


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


def _default_title_for(db) -> Callable[[Any, Any], Optional[Tuple[str, Tuple[str, ...]]]]:
    """The series' record title (the romaji of the folder scheme, :mod:`mangalist.folder_scheme`) and its alternative
    titles: MangaPixer's record (its own matched item, not a parent's; owner, 2026-10-10: "the series title =
    MangaPixer's title"), else MangaList's own MangaUpdates match when it is confirmed or automatic (a series added
    with Add series before MangaPixer has matched it), else None (no title: the folder keeps its name and volume names
    use it)."""
    resolver = None
    links: Optional[Dict[str, Any]] = None

    def mangapixer(root, series):
        nonlocal resolver
        try:
            if resolver is None:
                from .services.mangapixer import open_cache
                from .services.mangapixer.resolve import Resolver

                resolver = Resolver(open_cache(db))
            res = resolver.resolve(root.id, series.rel_path)
        except Exception:  # noqa: BLE001 - no MangaPixer data
            return None
        if res is None or res.inherited or res.link_state not in ("Confirmed", "Auto"):
            return None
        record = res.record if isinstance(res.record, dict) else {}
        title = record.get("title")
        if not isinstance(title, str) or not title.strip():
            return None
        alts = tuple(a.strip() for a in record.get("altTitles") or () if isinstance(a, str) and a.strip())
        return title.strip(), alts

    def own_match(root, series):
        nonlocal links
        import json

        if links is None:
            links = {}
            try:
                with db.connect() as con:
                    for r in con.execute("SELECT folder, mu_title, mu_associated, mu_confirmed, mu_band"
                                         " FROM links_cache"):
                        links[os.path.normcase(r["folder"])] = r
            except Exception:  # noqa: BLE001 - no links cache: no title from it
                links = {}
        from pathlib import Path

        from .store.series import link_key

        row = None
        for base in dict.fromkeys((root.path, str(Path(root.path).resolve()))):
            row = links.get(os.path.normcase(link_key(Path(base), series.rel_path)))
            if row is not None:
                break
        if row is None or not (row["mu_confirmed"] or row["mu_band"] == "auto"):
            return None
        title = row["mu_title"]
        if not isinstance(title, str) or not title.strip():
            return None
        try:
            alts = tuple(a.strip() for a in json.loads(row["mu_associated"] or "[]") if isinstance(a, str) and a.strip())
        except (TypeError, ValueError):
            alts = ()
        return title.strip(), alts

    def title_for(root, series) -> Optional[Tuple[str, Tuple[str, ...]]]:
        return mangapixer(root, series) or own_match(root, series)

    return title_for


def _no_analysis_info(_db, _series) -> Optional[bool]:
    """MangaPixer's export says nothing about which archives it has analysed: MangaList cannot tell."""
    return None


def record_rescan(db, root_ids: Sequence[int], series_ids: Optional[Sequence[int]] = None) -> str:
    """Scan and record what a batch renamed (the series' units follow the new names now, not at the next scheduled
    rescan): the folders of *series_ids* (renames stay inside their folder, so a series' folder holds both sides),
    or - without series - the given roots whole (selective scanning, 2026-10-13). Returns a short summary."""
    from .scanner import scan_and_record_library, series_scopes, widen_scopes

    scopes = series_scopes(db, series_ids) if series_ids else {}
    for rid in root_ids:
        if rid not in scopes:
            scopes[rid] = None              # a root without a known series: read it whole
    scopes = widen_scopes(db, scopes)
    roots = [r for r in db.list_roots() if r.id in scopes]
    if not roots:
        return ""
    scan_and_record_library(roots, db, scopes=scopes)
    folders = sum(len(v) for v in scopes.values() if v is not None)
    whole = sum(1 for r in roots if scopes.get(r.id) is None)
    parts = ([f"{folders} folder(s)"] if folders else []) + ([f"{whole} root(s)"] if whole else [])
    return f"rescan of {' and '.join(parts)} recorded"


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


# --- what the owner says a file holds ---------------------------------------------------------------------------

UNIT_CHAPTER, UNIT_VOLUME = "chapter", "volume"
MANUAL_REASON = "number set by you"
_UNIT_NUMBER = r"\d+(?:\.\d+)?"
_UNIT_RANGE = re.compile(rf"^\s*({_UNIT_NUMBER})\s*(?:-\s*({_UNIT_NUMBER}))?\s*$")
# what an unreadable name leaves at the start of its "title": "Ch. 0017.1.5 - Short notice" -> "Short notice"
_LEADING_UNITS = re.compile(r"^\s*(?:(?:vol(?:ume)?|ch(?:apter)?|v|c)\.?\s*\d[\d.]*[\s,]*)+(?:[-:\u2013]\s*)?",
                            re.IGNORECASE)


@dataclass(frozen=True)
class FileUnits:
    """What one file holds, as the owner says in the Renamer ("Set chapter / volume..."; owner, 2026-10-11: "I still
    can't set volume or chapter in the rename window") - for a name the parser cannot read, e.g. one cut off after
    ``Ch``. *volume*: a chapter's volume (None: the one the name states, else the volume list's)."""

    kind: str                       # UNIT_CHAPTER | UNIT_VOLUME
    start: Decimal
    end: Decimal
    volume: Optional[Decimal] = None


def file_units(kind: str, numbers: str, volume: str = "") -> FileUnits:
    """``("chapter", "106-110", "")`` -> :class:`FileUnits`; a ValueError says what is wrong."""
    if kind not in (UNIT_CHAPTER, UNIT_VOLUME):
        raise ValueError("choose chapter or volume")
    m = _UNIT_RANGE.match(numbers or "")
    if m is None:
        raise ValueError("write a number (12, 12.5) or a range (106-110)")
    start = Decimal(m.group(1))
    end = Decimal(m.group(2)) if m.group(2) else start
    if end < start:
        raise ValueError("a range goes from the smaller number to the larger")
    vol: Optional[Decimal] = None
    if kind == UNIT_CHAPTER and (volume or "").strip():
        vm = _UNIT_RANGE.match(volume)
        if vm is None or vm.group(2):
            raise ValueError("the volume is one number")
        vol = Decimal(vm.group(1))
    return FileUnits(kind, start, end, vol)


def with_units(parsed: Any, units: FileUnits, name: str) -> Any:
    """*parsed* (the parser's reading of *name*, or None) with the units the owner set; its title and group stay."""
    from .parsing.model import Kind, ParsedName, UnitRange

    base = parsed if parsed is not None else ParsedName(name=name)
    rng = UnitRange(units.start, units.end)
    title = _LEADING_UNITS.sub("", base.title).strip() if base.title else ""
    common = dict(number=None, is_extra=False, guessed=False, ambiguous=False, title=title or None)
    if units.kind == UNIT_VOLUME:
        return replace(base, kind=Kind.VOLUME, volume=rng, chapter=None, **common)
    if units.volume is not None:
        vol = UnitRange(units.volume, units.volume)
    else:                                   # blank: the volume the name states (one), else the volume list's
        vol = base.volume if base.volume is not None and not base.volume.is_range else None
    return replace(base, kind=Kind.CHAPTER, chapter=rng, volume=vol, **common)


def _listing_cache() -> Callable[[str], Set[str]]:
    """:func:`folder_names` read once per library folder (for one dry run, count or apply)."""
    seen: Dict[str, Set[str]] = {}

    def names_of(root_path: str) -> Set[str]:
        if root_path not in seen:
            seen[root_path] = folder_names(root_path)
        return seen[root_path]

    return names_of


class Renamer:
    """The renamer's actions over one library database. Every method may block (disk, database, MangaPixer): the GUI
    calls them off the UI thread.

    Injected (all optional): *namer* (:class:`Namer`; default :class:`DefaultNamer`), *journal_factory* (``db ->``
    :class:`~mangalist.store.journal.Journal`), *title_for* (``(root, series) -> the record title`` - the romaji of
    the folder scheme -, or ``(title, alternative titles)``, or None), *analysed*
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
        self._manual: Dict[str, FileUnits] = {}         # path -> what the owner says it holds (this session)

    def set_file_units(self, path: str, units: Optional[FileUnits]) -> None:
        """What *path* holds, as the owner says (None forgets it): the next preview names it by these numbers. Kept for
        this Renamer only - once renamed, the file's new name says it."""
        key = os.path.normpath(str(path))
        if units is None:
            self._manual.pop(key, None)
        else:
            self._manual[key] = units

    def file_units(self, path: str) -> Optional[FileUnits]:
        return self._manual.get(os.path.normpath(str(path)))

    # --- small helpers --------------------------------------------------------------------------------------------

    def journal(self):
        from .store.journal import Journal

        return (self._journal_factory or Journal)(self.db)

    def available(self) -> bool:
        return naming_available(self.namer)

    def limits(self):
        return self.namer.limits(windows_server(self.db))

    def keeps_in_scheme(self, root) -> bool:
        """*root* is set to "Rename automatically" and the owner started its conversion: what is filed there is
        named by the scheme at once (owner, 2026-10-12: "Doesn't Mangalist automatically rename to the scheme when
        it fills?")."""
        return getattr(root, "enforce_naming", None) == ENFORCE_AUTOMATIC and root.id in guided_roots(self.db)

    def scheme_name(self, root, series, name: str, *, size: int = 0) -> Optional[str]:
        """The scheme's name for an archive called *name* about to be filed into *series* (read like the series' own
        files: the root's scheme, the "volumes or chapters?" answer, the series title, its volume list, the length
        rule) - None when the name cannot be read with certainty or the naming scheme is not in this build."""
        from .parsing import parse_name, with_series_head

        try:
            folder = os.path.join(root.path, *series.rel_path.split("/")) if series.rel_path else root.path
            ctx = _parse_context(os.path.basename(folder), series.kind_hint, getattr(root, "naming_scheme", None))
            siblings = [u.rel_path for u in self.db.list_units(series.id) if "/" not in u.rel_path]
            parsed = parse_name(name, with_series_head(ctx, siblings + [name]), file_size=size)
            title = self.series_title(root, series)
            target = self.namer.target_name(parsed, ext=os.path.splitext(name)[1], series_title=title,
                                            volume_of=self._volume_of(series.id), folder=folder,
                                            limits=self.limits())
        except Exception:  # noqa: BLE001 - NamingUnavailable or an odd name: the file keeps its own name
            _log.debug("Renamer: no scheme name for %s", name, exc_info=True)
            return None
        return target if target and not _name_problem(target) else None

    def _record_titles(self, root, series) -> Tuple[Optional[str], Tuple[str, ...]]:
        """The series' record title and alternative titles (see :func:`_default_title_for`); (None, ()) without one."""
        if self._title_for is None:
            self._title_for = _default_title_for(self.db)
        try:
            got = self._title_for(root, series)
        except Exception:  # noqa: BLE001
            return None, ()
        if isinstance(got, tuple):
            title, alts = (got + ((),))[:2]
            return (title if isinstance(title, str) and title.strip() else None), tuple(alts or ())
        return (got if isinstance(got, str) and got.strip() else None), ()

    def series_titles(self, root, series):
        """The :class:`~mangalist.folder_scheme.SeriesTitles` of a series (its romaji and English titles), or None
        when it has no record title yet."""
        from .folder_scheme import titles_for

        rel = series.rel_path or ""
        romaji, alts = self._record_titles(root, series)
        if romaji is None:
            return None
        return titles_for(rel.rsplit("/", 1)[-1], romaji, alts, top_level="/" not in rel)

    def scheme_folder_name(self, root, series, fits=None) -> Optional[str]:
        """The scheme's name ``<romaji> [<English>]`` for the series' folder (None: no record title)."""
        titles = self.series_titles(root, series)
        return titles.name(fits) if titles is not None else None

    def series_title(self, root, series) -> str:
        """The series title volume names use (owner, 2026-10-15, answer 12: "Titles should follow the scheme in answer 6
        and should change"): the folder scheme's ``<romaji> [<English>]``, else - no record title - the folder's
        name."""
        folder = os.path.join(root.path, *series.rel_path.split("/")) if series.rel_path else root.path
        try:
            name = self.scheme_folder_name(root, series)
        except Exception:  # noqa: BLE001 - the folder's name
            _log.debug("Renamer: no scheme title for %s", series.rel_path, exc_info=True)
            name = None
        return name or os.path.basename(folder)

    def legacy_title(self, root, series) -> str:
        """The series title volume names used before the folder scheme (2026.10.35): the record's title, else the
        folder's name. A rename that differs from what this title gives waits for the owner's Rename."""
        folder = os.path.join(root.path, *series.rel_path.split("/")) if series.rel_path else root.path
        romaji, _alts = self._record_titles(root, series)
        return romaji or os.path.basename(folder)

    def _plan_folder(self, root, series, preview: SeriesPreview, names_of: Callable[[str], Set[str]],
                     server: Optional[str]) -> None:
        """The series folder's status in *preview* (top-level series only)."""
        from .folder_scheme import windows_fits

        rel = series.rel_path or ""
        if not rel or "/" in rel:
            return
        try:
            target = self.scheme_folder_name(root, series, windows_fits(root.path, server))
        except Exception:  # noqa: BLE001 - one odd title must not stop the preview
            _log.warning("Renamer: naming the folder of %s failed; left alone", rel, exc_info=True)
            preview.folder_status, preview.folder_reason = LEFT_ALONE, "naming the folder failed"
            return
        preview.folder_target = target
        preview.folder_status, preview.folder_reason = plan_folder(rel, target, names=names_of(root.path),
                                                                   excluded=_root_excludes(root))

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

    def answer_kind(self, series_id: int, kind: Optional[str]) -> bool:
        """Store the series' "volumes or chapters?" answer (``"volumes"`` / ``"chapters"``, None: no answer) - the same
        answer as the List's question (owner, 2026-10-11: "You should be able to set unknown as chapters or volumes from
        within the renamer"). The next dry run reads the names with it; a rescan brings the List along."""
        series = self.series_by_id(series_id)
        if series is None:
            return False
        done = self.db.set_series_kind(series.root_id, series.rel_path, kind)
        _log.info("Renamer: %s: the numbers are %s (the owner's answer)", series.rel_path, kind or "not answered")
        return bool(done)

    def series_id_for_folder(self, folder) -> Optional[int]:
        s = self.db.series_for_folder(folder)
        return s.id if s is not None else None

    # --- previews -------------------------------------------------------------------------------------------------

    def files_of(self, root, series) -> List[Tuple[str, Any]]:
        """``(absolute path, ParsedName)`` of the series' own archives as the last scan recorded them, parsed again
        from their names (the root's scheme and the series' "volumes or chapters?" answer)."""
        from .parsing import parse_name, with_series_head

        folder = os.path.join(root.path, *series.rel_path.split("/"))
        ctx = _parse_context(os.path.basename(folder), series.kind_hint, getattr(root, "naming_scheme", None))
        rels = sorted({u.rel_path for u in self.db.list_units(series.id)}, key=lambda r: (r.casefold(), r))
        by_folder: Dict[str, List[str]] = defaultdict(list)     # the series head of each folder, as the scan reads it
        for rel in rels:
            by_folder[rel.rpartition("/")[0]].append(rel.rpartition("/")[2])
        heads = {d: with_series_head(ctx, names) for d, names in by_folder.items()}
        out = []
        for rel in rels:
            path = os.path.join(folder, *rel.split("/"))
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            parsed = parse_name(os.path.basename(path), heads[rel.rpartition("/")[0]], file_size=size)
            units = self._manual.get(os.path.normpath(path))
            if units is not None:
                parsed = with_units(parsed, units, os.path.basename(path))
            out.append((path, parsed))
        return out

    def _preview(self, root, series, files: Sequence[Tuple[str, Any]], limits: Any,
                 names_of: Optional[Callable[[str], Set[str]]] = None) -> SeriesPreview:
        folder = os.path.join(root.path, *series.rel_path.split("/")) if series.rel_path else root.path
        title = self.series_title(root, series)
        preview = SeriesPreview(series_id=series.id, root_id=root.id, root_path=root.path, folder=folder,
                                title=os.path.basename(folder), series_title=title)
        try:
            preview.analysed = self._analysed(self.db, series)
        except Exception:  # noqa: BLE001 - not known
            preview.analysed = None
        self._plan_folder(root, series, preview, names_of or _listing_cache(),
                          getattr(limits, "windows_server", None) if limits is not None else None)
        if not files:
            preview.error = "no files recorded for this series (rescan first)"
            return preview
        preview.files = plan_files(files, namer=self.namer, series_title=title, volume_of=self._volume_of(series.id),
                                   limits=limits, legacy_title=self.legacy_title(root, series))
        if self._manual:
            preview.files = [replace(f, reason=f.reason or MANUAL_REASON)
                             if f.status in (RENAME, UNCHANGED) and os.path.normpath(f.path) in self._manual else f
                             for f in preview.files]
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
        names_of = _listing_cache()
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
            rdr.series.append(self._preview(root, series, self.files_of(root, series), limits, names_of))
        mark_folder_collisions(dry.series)
        dry.warnings.extend(self._warnings(dry))
        self._log_preview(dry.series, "dry run of " + (", ".join(r.name for r in dry.roots) or "nothing"))
        return dry

    def _warnings(self, dry: DryRun) -> List[str]:
        out = []
        moving = [s for s in dry.series if s.renames]
        not_analysed = [s for s in moving if s.analysed is False]
        if dry.folders_to_rename:
            out.append(FOLDER_NOTE)
        if any(s.held_renames for s in dry.series):
            out.append(TITLE_NOTE)
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
        folders = sum(1 for s in previews if s.folder_rename)
        _log.info("Renamer: %s: %d to rename, %d already named by the scheme, %d left alone, %d collision(s) in %d "
                  "series; %d series folder(s) not named \"<romaji> [<English>]\"", what, c[RENAME], c[UNCHANGED],
                  c[LEFT_ALONE], c[COLLISION], len(previews), folders)

    # --- rename pending ---------------------------------------------------------------------------------------------

    def pending_counts(self, entries: Iterable[Any]) -> Dict[str, int]:
        """Per series folder (``str(entry.folder)``): how many files the scheme would rename - from a scan's entries
        (their parsed names), for the List's "Rename pending". Roots set to Off, and entries outside a known root,
        never count. Empty when the naming scheme is not in this build."""
        if not self.available():
            return {}
        roots = {r.id: r for r in self.db.list_roots()}
        limits = self.limits()
        names_of = _listing_cache()
        out: Dict[str, int] = {}
        folders = 0
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
            try:
                preview = self._preview(root, series, files, limits, names_of)
            except NamingUnavailable:
                return {}
            n = len(preview.renames) if files else 0
            if preview.folder_rename:           # the folder is not "<romaji> [<English>]": one more to rename
                n += 1
                folders += 1
            if n:
                out[str(entry.folder)] = n
        _log.info("Renamer: %d series have files or a folder not named by the scheme (rename pending; %d folder(s))",
                  len(out), folders)
        return out

    # --- applying -------------------------------------------------------------------------------------------------

    def batches(self, previews: Iterable[SeriesPreview], *, folders: bool = False) -> List[RenameBatch]:
        """The journal batches of *previews*; *folders*: their series folders too (the owner's Rename only)."""
        return make_batches(previews, self.batch_size, folders=folders)

    def apply(self, batch: RenameBatch, *, after: bool = True) -> BatchResult:
        """Rename one batch through the journal (one plan, the root's lock). Each series is previewed again first;
        only renames that are still exactly what the preview showed are made (anything else is listed as skipped).
        A batch made with ``folders=True`` (the window's Rename, never the automatic pass) also renames each series
        folder that is still to be renamed to the same name: ONE step, after the series' file renames, so the series
        keeps its identity (the journal's folder-move follow). With *after*: the rescan is recorded and MangaPixer is
        asked to scan the libraries touched."""
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
        folder_moves: List[Tuple[str, str]] = []
        names_of = _listing_cache()
        taken: Set[str] = set()                 # folder names this batch gives out
        for shown in batch.series:
            series = self.series_by_id(shown.series_id) if shown.series_id is not None else None
            folder_wanted = batch.folders and shown.folder_rename
            wanted = batch.files_of(shown)          # the automatic pass: not the renames a new series title causes
            if series is None:
                result.skipped += [(f.name, "the series is no longer in the library") for f in wanted]
                if folder_wanted:
                    result.skipped.append((shown.title + "/", "the series is no longer in the library"))
                continue
            now = self._preview(root, series, self.files_of(root, series), limits, names_of)
            if now.analysed is False:
                result.skipped += [(f.name, "MangaPixer has not analysed this series' files yet - let it finish its "
                                            "scan first") for f in wanted]
                if folder_wanted:
                    result.skipped.append((shown.title + "/", "MangaPixer has not analysed this series' files yet"))
                continue
            current = {(f.path, f.new_path) for f in batch.files_of(now)}
            ok = [(f.path, f.new_path) for f in wanted if (f.path, f.new_path) in current]
            for f in wanted:
                if (f.path, f.new_path) not in current:
                    result.skipped.append((f.name, "changed since the preview - preview again"))
            folder_ok = False
            if folder_wanted:
                key = _name_key(shown.folder_target)
                if not (now.folder_rename and now.folder == shown.folder and now.folder_target == shown.folder_target):
                    result.skipped.append((shown.title + "/", "the folder changed since the preview - preview again"))
                elif key in taken:
                    result.skipped.append((shown.title + "/", f"another series of this batch takes the name "
                                                              f"{shown.folder_target!r}"))
                else:
                    taken.add(key)
                    folder_ok = True
            if ok or folder_ok:
                moves.extend(ok)
                if folder_ok:
                    folder_moves.append((now.folder, now.new_folder))
                result.series_ids.append(series.id)
        for name, why in result.skipped:
            _log.warning("Renamer: %s not renamed: %s", name, why)
        if not moves and not folder_moves:
            result.message = "nothing to rename" + (" (everything changed since the preview)" if result.skipped else "")
            _log.info("Renamer: batch in %s: %s", batch.root_path, result.message)
            return result
        journal = self.journal()
        # the files first (inside the old folder), then each series folder as one step: undo puts the folder back first
        ordered = order_moves(moves) + folder_moves
        note = f"{len(moves)} file(s) in {len(result.series_ids)} series"
        if folder_moves:
            note += f", {len(folder_moves)} series folder(s) renamed to the scheme"
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
        if after and (result.renamed or result.folders):
            result.after = self._after(result.root_ids, result.series_ids)
        return result

    def _settle(self, result: BatchResult, plan, ordered: Sequence[Tuple[str, str]]) -> None:
        temps = {os.path.normcase(d) for _s, d in ordered if os.path.basename(d).startswith("~mangalist-rename-")}
        done = [s for s in plan.steps if s.state == "done" and os.path.normcase(s.dst) not in temps]
        result.folders = sum(1 for s in done if s.is_dir)
        result.renamed = len(done) - result.folders
        for s in plan.steps:
            if s.state == "failed":
                result.failed.append((os.path.basename(s.src), s.error or "failed"))
        if plan.status == "applied":
            result.status = "applied"
            result.message = f"{result.renamed} file(s) renamed" + (
                f", {result.folders} series folder(s) renamed" if result.folders else "")
            _log.info("Renamer: plan %d applied: %d file(s) and %d series folder(s) renamed in %d series", plan.id,
                      result.renamed, result.folders, len(result.series_ids))
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
                if self._rescan is record_rescan:       # the default scans the series' folders, not their roots
                    note = record_rescan(self.db, list(root_ids), list(series_ids))
                else:
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
        root_ids = sorted({r for res in out if res.renamed or res.folders for r in res.root_ids})
        series_ids = sorted({s for res in out if res.renamed or res.folders for s in res.series_ids})
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
        result.folders = sum(1 for s in undone if s.is_dir)
        result.renamed = len(undone) - result.folders
        root = self.db.root_for_path(after.root_path) if after.root_path else None
        result.root_ids = [root.id] if root is not None else []
        series_ids = set()
        for s in undone:                        # a folder step's series is at its old folder again
            sid = self.series_id_for_folder(s.src if s.is_dir else os.path.dirname(s.src))
            if sid is not None:
                series_ids.add(sid)
        result.series_ids = sorted(series_ids)
        if after.status == "undone":
            result.status = "undone"
            result.message = f"{result.renamed} file(s) back under their old names" + (
                f", {result.folders} series folder(s) back" if result.folders else "")
            _log.info("Renamer: plan %d undone: %d file(s) and %d series folder(s) back under their old names", plan_id,
                      result.renamed, result.folders)
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

    def automatic_offers(self, root_ids: Iterable[int]) -> List[Any]:
        """The roots among *root_ids* to ask "Rename automatically from now on?" after their first rename from the
        window (owner, 2026-10-12: "The user should be prompted to enable this after they have done their first library
        renaming for a given library"): not set to it already, never asked before."""
        try:
            asked = {int(r) for r in self.db.get_setting(KEY_AUTOMATIC_OFFERED, []) or []}
        except Exception:  # noqa: BLE001
            asked = set()
        wanted = {int(r) for r in root_ids if r is not None}
        return [r for r in self.db.list_roots()
                if r.id in wanted and r.id not in asked and r.enforce_naming != ENFORCE_AUTOMATIC]

    def answer_automatic(self, root_id: int, on: bool) -> None:
        """The owner's answer for *root_id*: remembered (asked once); yes sets the root to "Rename automatically"."""
        from dataclasses import replace as _replace

        try:
            asked = {int(r) for r in self.db.get_setting(KEY_AUTOMATIC_OFFERED, []) or []}
        except Exception:  # noqa: BLE001
            asked = set()
        self.db.set_setting(KEY_AUTOMATIC_OFFERED, sorted(asked | {int(root_id)}))
        if on:
            root = self.db.get_root(root_id)
            if root is not None and root.enforce_naming != ENFORCE_AUTOMATIC:
                self.db.update_root(_replace(root, enforce_naming=ENFORCE_AUTOMATIC))
                _log.info("Renamer: %s now renames automatically (the owner's answer after its first rename)",
                          root.name)

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
        seen = self._automatic_seen()
        shared: Optional[str] = None
        for root in roots:
            if root.id not in guided:
                report.waiting.append(root.name)
                _log.info("Renamer: %s renames automatically only after you start its conversion in MangaList "
                          "(List > Rename library...); nothing renamed", root.name)
                continue
            if budget <= 0 or (should_stop is not None and should_stop()):
                break
            report.roots.append(root.name)
            if shared is None:
                shared = self._shared_inputs()
            inputs, digests = self._naming_inputs(root, shared), self._series_digests(root)
            before = seen.get(str(root.id)) or {}
            quiet = dict(before.get("series") or {}) if before.get("inputs") == inputs else {}
            quiet = {sid: d for sid, d in quiet.items() if digests.get(int(sid)) == d}      # still as when looked at
            todo = sorted(sid for sid in digests if str(sid) not in quiet)
            report.unchanged += len(digests) - len(todo)
            seen[str(root.id)] = {"inputs": inputs, "series": quiet}
            if not todo:
                _log.info("Renamer: %s: nothing changed since the last automatic pass; dry run skipped", root.name)
                continue
            dry = self.dry_run(root_ids=[root.id], series_ids=todo, should_stop=should_stop)
            for s in dry.series:            # nothing to rename: not looked at again until its files or names change
                if not s.auto_renames and s.series_id in digests:       # (a new series title waits for the owner)
                    quiet[str(s.series_id)] = digests[s.series_id]
            report.held += sum(len(s.held_renames) for s in dry.series)
            ready = [s for s in dry.series if s.auto_renames and s.analysed is not False]
            for s in dry.series:
                if s.auto_renames and s.analysed is False:
                    _log.warning("Renamer: %s waits: MangaPixer has not analysed its archives yet", s.title)
            for batch in self.batches(ready):
                if budget <= 0 or (should_stop is not None and should_stop()):
                    break
                if batch.n_files > budget:
                    kept = []
                    count = 0
                    for s in batch.series:
                        if count + len(s.auto_renames) > budget:
                            break
                        kept.append(s)
                        count += len(s.auto_renames)
                    if not kept:
                        break
                    batch = RenameBatch(batch.root_id, batch.root_path, tuple(kept), titles=False)
                res = self.apply(batch, after=False)
                report.batches.append(res)
                budget -= res.renamed
                _log.info("Renamer: automatic batch in %s: %s", root.name, res.message)
                if res.status not in ("applied", "nothing"):
                    break
        root_ids = sorted({r for b in report.batches if b.renamed for r in b.root_ids})
        series_ids = sorted({s for b in report.batches if b.renamed for s in b.series_ids})
        self._save_automatic_seen(seen)
        if series_ids:
            report.after = self._after(root_ids, series_ids)
        _log.info("Renamer: automatic pass: %s (%.1f s; %d series unchanged since the last pass)", report.summary(),
                  time.monotonic() - started, report.unchanged)
        return report

    # --- what the automatic pass looked at (selective scanning, Stage 0) -------------------------------------------
    #
    # The automatic pass dry-ran every series of every automatic root after every headless scan (about 50 s, renaming
    # nothing in eight passes in a row, 2026-10-12). A series whose dry run found nothing to rename is remembered with a
    # digest of everything its names depend on that the database holds; while the digest and the root's naming inputs
    # stay the same, it is not looked at again.

    def _automatic_seen(self) -> Dict[str, Any]:
        import json

        try:
            got = json.loads(self.db.get_meta(META_AUTOMATIC_SEEN) or "{}")
        except Exception:  # noqa: BLE001 - unreadable: look at everything again
            got = {}
        return got if isinstance(got, dict) else {}

    def _save_automatic_seen(self, seen: Dict[str, Any]) -> None:
        import json

        try:
            self.db.set_meta(META_AUTOMATIC_SEEN, json.dumps(seen, sort_keys=True, separators=(",", ":")))
        except Exception:  # noqa: BLE001 - the next pass looks at everything again
            _log.warning("Renamer: could not remember what the automatic pass looked at", exc_info=True)

    def _naming_inputs(self, root, shared: str) -> str:
        """A digest of what every name in *root* depends on besides its own files: the root's folder and scheme, and
        *shared* (:meth:`_shared_inputs`)."""
        import hashlib

        text = repr((shared, root.path, getattr(root, "naming_scheme", None)))
        return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

    def _shared_inputs(self) -> str:
        """A digest of what every name in every root depends on: the Windows server (the length rule), this build (the
        parser and the namer), and MangaPixer's synced data (titles and volume lists) with the roots' pairing."""
        import hashlib

        from . import __version__

        h = hashlib.sha256()
        h.update(repr((__version__, type(self.namer).__name__, windows_server(self.db))).encode("utf-8",
                                                                                               "surrogatepass"))
        try:
            with self.db.connect() as con:
                for r in con.execute("SELECT root_id, library_id, prefix, manual FROM mangapixer_mappings"
                                     " ORDER BY root_id"):
                    h.update(repr(tuple(r)).encode("utf-8", "surrogatepass"))
                for r in con.execute("SELECT library_id, node_id, updated_at, link_state FROM mangapixer_items"
                                     " ORDER BY library_id, node_id"):
                    h.update(repr(tuple(r)).encode("utf-8", "surrogatepass"))
        except Exception:  # noqa: BLE001 - not readable: a fresh digest each time (everything is looked at)
            h.update(repr(time.time()).encode("ascii"))
        return h.hexdigest()

    def _series_digests(self, root) -> Dict[int, str]:
        """Per present series of *root*: a digest of its folder, its "volumes or chapters?" answer and its recorded
        files (the names the dry run reads - its units are read from the same files by the same parser - with their
        sizes)."""
        import hashlib

        files: Dict[int, List[Tuple[str, int]]] = defaultdict(list)
        series: Dict[int, Tuple[str, Optional[str]]] = {}
        with self.db.connect() as con:
            for r in con.execute("SELECT id, rel_path, kind_hint FROM series WHERE root_id = ? AND status = 'present'",
                                 (root.id,)):
                series[r["id"]] = (r["rel_path"], r["kind_hint"])
            # by the (root, path) index: "AND status = ..." made SQLite walk every present archive of every root
            for r in con.execute("SELECT series_id, rel_path, size, status FROM archives WHERE root_id = ?",
                                 (root.id,)):
                if r["series_id"] is not None and r["status"] == "present":
                    files[r["series_id"]].append((r["rel_path"], r["size"]))
        out: Dict[int, str] = {}
        for sid, (rel, kind) in series.items():
            text = repr((rel, kind, sorted(files.get(sid, ()))))
            out[sid] = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:32]
        return out


__all__ = [
    "ANALYSIS_WARNING", "AUTO_LIMIT", "AutoReport", "BATCH_SIZE", "BatchRecord", "BatchResult", "COLLISION",
    "DefaultNamer", "DryRun", "ENFORCE_LABELS", "FilePlan", "KEY_GUIDED_ROOTS", "KEY_WINDOWS_SERVER", "LEFT_ALONE",
    "Namer", "NamingUnavailable", "PLAN_REASON", "RENAME", "RenameBatch", "Renamer", "RootDryRun", "SeriesPreview",
    "UNCHANGED", "guided_roots", "make_batches", "naming_available", "normalize_server", "order_moves", "plan_files",
    "record_rescan", "request_mangapixer_scans", "set_windows_server", "windows_server",
]
