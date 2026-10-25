"""Merge into...: one series held in two folders (:mod:`mangalist.same_series`) brought together - every file and
sub-folder of the folder merged away moved into the folder kept, through the journal (owner, 2026-10-17, on the
Duplicates view's "Same series in two folders": "Flag + merge the others"). No Qt: the Duplicates view
(:mod:`mangalist.gui.duplicates_view`) asks which folder is kept and confirms; everything that reads or writes is here.
It reuses Move to library's machinery (:mod:`mangalist.library_move`): the same refusals, locks, journal plans, ``EXDEV``
handling and undo.

**What moves.** Each direct child of the merged-away folder - a file, or a sub-folder as a whole (``Season 2``,
``Extras``, a subseries) - into the kept folder: ONE journal plan (moves only, write-ahead, crash recovery, undo) -
within one library folder a plan of that root (:meth:`~mangalist.store.journal.Journal.plan`), across two a cross-root
plan with files allowed (:meth:`~mangalist.store.journal.Journal.plan_across_roots` ``files=True``). Nothing is ever
replaced. A FILE whose name the kept folder already has (compared without case, as Windows and macOS shares do) is
compared by content (owner, 2026-10-17: "Both, by content"):

- **byte-identical** (same size and the same SHA-256 of the whole file): the merged-away copy is offered for DELETION in
  the confirmation, listed by name, and deleted after the owner's yes through :func:`mangalist.duplicates.delete_checked`
  (size and time as listed, under the roots' locks) - only while the kept copy is still there as compared. A refused
  delete is reported and the file stays (and so does its folder). A deleted copy cannot come back with Undo; the
  confirmation says so;
- **different content**: moved in, in the same plan, under a suffixed name that names the source library and keeps the
  parsed chapter / volume number - ``Name v01 (from Other-Manga-Ongoing).cbz`` (or the tag inside the name's last
  ``[...]`` / as ``{from ...}`` when the parser would otherwise read the name differently, :func:`suffixed_name`), with a
  counter when that name is taken too - so the per-file duplicate review lists both and the owner picks. Undo puts it
  back under its own name.

A SUB-FOLDER whose name the kept folder has is left where it is and reported (owner: "leave and report"), and so is a
symbolic link, a MangaList lock file and a name Windows cannot hold. A nested copy (``Name/Name``) only goes into the
folder it lies in (:meth:`SamePair.merge_choices`).

**Refused up front, with the reason** (:func:`check_merge`; nothing moves): two folders not matched to the same record,
or a side whose match is not Confirmed (owner, 2026-10-17: "Confirmed only" - an Auto match, MangaPixer's or the own
matcher's, may be wrong: "confirm the match of <folder> first"); what Move to library refuses for the folder
that moves - a download in progress for the series (or a subseries inside it), replaced chapter files waiting for the
owner's confirmation, replaced chapters held in the holding folder (:func:`mangalist.library_move._busy_reason`); a folder
that is missing, outside every library folder or not scanned yet; the kept folder lying inside the merged one; nothing
that could move. At merge time, under the locks: a library scan running (the scan lock) or another MangaList instance
writing one of the two library folders (their ``.mangalist.lock``); the busy check is made again.

**Afterwards.** The emptied folder is removed only when it is truly empty (``rmdir``, which cannot remove anything that
has content - a removal outside the journal, of a folder the merge emptied; undo makes it again); otherwise it is left
and reported. When the system refuses a rename (``EXDEV``: another drive or share, or any other refusal) that step
fails with nothing more moved and the owner is told to move the files with a file manager, as Move to library does.

**Identity.** The kept series keeps its id and everything on it (history, own mode, Unbundled setting, link). The
archive rows follow directly (the journal's ``follow_journal_move``: they now belong to the kept series). Only when the
merged-away folder was removed, its series row - which no longer has a folder - is folded into the kept one
(``series_carries``, ``how='merge'``), as the identity carry-over does for a folder whose archives moved into one live
series (:mod:`mangalist.identity.carry`), except that the kept row is never replaced: its MangaUpdates link moves to the
kept folder when that has none (else it is dropped: the kept folder's own link wins - it is the same series), its
"volumes or chapters?" answer likewise, its examined mark moves, its download history (ledger rows) joins the kept
series, and the row is retired (its units and waiting findings go with it; the next scan and find pass rebuild them for
the kept folder). A folder left in place (names it could not give up) stays its own series. MangaPixer sees the moved
archives at its next scan of the kept library.

**Undo** (:func:`undo_merge`): the last merge's plan undone - the merged-away folder made again when the merge removed
it, its series row restored as it was (same id, link, answer, examined mark, history) BEFORE the files move back (so
their archive rows return to it), then every step renamed back (no-replace), under the same locks. Refused while a
download files into the kept folder.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .library_move import (
    SCAN_RUNNING,
    SCAN_WAIT,
    _busy_reason,
    _locks_text,
    _roots_by_id,
    _series_ids_under,
    _under,
)
from .duplicates import DiscardRefused, delete_checked, file_as_listed, human_size, other_numbers, stated_unit
from .store.discards import record_discard
from .store.journal import ACROSS_FILESYSTEMS, Journal, PlanStateError, StepRefused
from .store.lock import LOCK_NAME, LockError
from .store.names import windows_name_problem

_log = logging.getLogger(__name__)

MERGE_REASON = "merge same series"
META_PREFIX = "series_merge."           # + plan id: what the merge did besides its steps (JSON)
UNDO_HINT = "Undo: Duplicates > Same series in two folders > Undo last merge."
EXDEV_TEXT = ("The system refused to move the files between these folders (they are on different drives or shares). "
              "MangaList never copies + deletes: please move them yourself with a file manager - MangaList recognises "
              "the move at the next scan.")
REFUSED_TEXT = ("The system refused to move {name} ({error}). Please move the files yourself with a file manager - "
                "MangaList recognises the move at the next scan.")
_RENAME_FAILED = "rename failed: "      # the journal's error for a rename the system refused (not EXDEV)


def _items(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


@dataclass(frozen=True)
class Identical:
    """A file of the merged-away folder byte-identical to the kept folder's file of the same name, as compared."""

    path: str                           # the copy to delete (merged-away folder)
    size: int
    modified: str                       # ISO, as delete_checked compares it
    kept_path: str                      # the copy that stays
    kept_size: int
    kept_modified: str

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class MergeCheck:
    """What merging *merged_folder* into *kept_folder* would do: the moves, the names left where they are, or why not."""

    merged_folder: str
    kept_folder: str
    merged_where: str = ""              # "Library / folder" as the owner reads it
    kept_where: str = ""
    merged_series_id: Optional[int] = None
    kept_series_id: Optional[int] = None
    merged_root_id: Optional[int] = None
    kept_root_id: Optional[int] = None
    moves: List[Tuple[str, str]] = field(default_factory=list)      # (src, dst) absolute
    suffixed: List[Tuple[str, str]] = field(default_factory=list)   # (name, the new name it moves in under)
    identical: List["Identical"] = field(default_factory=list)      # byte-identical copies offered for deletion
    left: List[Tuple[str, str]] = field(default_factory=list)       # (name, why it stays)
    refused: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.refused is None and bool(self.moves or self.identical)

    @property
    def across(self) -> bool:
        return self.merged_root_id != self.kept_root_id


@dataclass
class MergeResult:
    status: str                         # merged | partly | refused | failed | undone
    message: str
    plan_id: Optional[int] = None
    moved: List[Tuple[str, str]] = field(default_factory=list)      # (src, dst) of each item that moved
    left: List[Tuple[str, str]] = field(default_factory=list)
    merged_folder: str = ""
    kept_folder: str = ""
    removed: bool = False               # the emptied folder was removed (undo: made again)
    suffixed: List[Tuple[str, str]] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)                # identical copies deleted (names)
    not_deleted: List[Tuple[str, str]] = field(default_factory=list)  # (name, why the delete was refused)

    @property
    def ok(self) -> bool:
        return self.status in ("merged", "undone")

    @property
    def paths(self) -> List[str]:
        """The two folders: the scope of the scan after it."""
        return [p for p in (self.merged_folder, self.kept_folder) if p]


@dataclass
class MergeRecord:
    """The last merge that can be undone."""

    plan_id: int
    count: int                          # items it moved (still moved)
    merged_folder: str
    kept_folder: str
    created_at: str

    @property
    def label(self) -> str:
        return f"Undo last merge ({_items(self.count)} back to {Path(self.merged_folder).name})"


# --- checking ----------------------------------------------------------------------------------------------------


def _where(root, rel: str) -> str:
    return f"{root.name} / {rel}"


def check_merge(db, merged_folder, kept_folder) -> MergeCheck:
    """What merging the series folder *merged_folder* into *kept_folder* would do (each an absolute folder, as the
    List or :mod:`mangalist.same_series` gives it). Reads only."""
    check = MergeCheck(str(merged_folder), str(kept_folder))
    roots = _roots_by_id(db)
    loc_m, loc_k = db._locate(merged_folder), db._locate(kept_folder)
    if loc_m is None or loc_k is None:
        check.refused = "a folder is not inside a library folder"
        return check
    (rid_m, rel_m), (rid_k, rel_k) = loc_m, loc_k
    root_m, root_k = roots.get(rid_m), roots.get(rid_k)
    if root_m is None or root_k is None:
        check.refused = "that library folder is no longer configured"
        return check
    src_dir, dst_dir = _under(db, root_m, rel_m), _under(db, root_k, rel_k)
    check.merged_folder, check.kept_folder = src_dir, dst_dir
    check.merged_where, check.kept_where = _where(root_m, rel_m), _where(root_k, rel_k)
    check.merged_root_id, check.kept_root_id = rid_m, rid_k
    if loc_m == loc_k:
        check.refused = "the two folders are the same"
        return check
    if rid_m == rid_k and rel_k.startswith(rel_m.rstrip("/") + "/"):
        check.refused = "the folder kept lies inside the one merged away - merge the inner folder into its parent"
        return check
    with db.connect() as con:
        row_m = con.execute("SELECT id FROM series WHERE root_id = ? AND rel_path = ?", (rid_m, rel_m)).fetchone()
        row_k = con.execute("SELECT id FROM series WHERE root_id = ? AND rel_path = ?", (rid_k, rel_k)).fetchone()
        if row_m is None or row_k is None:
            check.refused = "a folder is not scanned into the library yet - rescan first"
            return check
        check.merged_series_id, check.kept_series_id = int(row_m["id"]), int(row_k["id"])
        for path in (src_dir, dst_dir):
            if os.path.islink(path) or not os.path.isdir(path):
                check.refused = f"the folder is missing ({path})"
                return check
        why = _busy_reason(con, _series_ids_under(con, rid_m, rel_m))
        if why:
            check.refused = why
            return check
    why = _match_refusal(db, check.merged_series_id, check.kept_series_id)
    if why:
        check.refused = why
        return check
    try:
        with os.scandir(dst_dir) as it:
            there = {e.name.casefold(): e.name for e in it}
        with os.scandir(src_dir) as it:
            children = sorted(it, key=lambda e: (e.name.casefold(), e.name))
    except OSError as exc:
        check.refused = f"a folder cannot be read ({exc.strerror or exc})"
        return check
    taken = set(there)                                  # names (case-folded) the kept folder will hold
    same_named = []
    for e in children:
        name = e.name
        if name.startswith(LOCK_NAME):
            check.left.append((name, "a MangaList lock file"))
        elif e.is_symlink():
            check.left.append((name, "a symbolic link - move it by hand"))
        elif not (e.is_file(follow_symlinks=False) or e.is_dir(follow_symlinks=False)):
            check.left.append((name, "not a plain file or folder"))
        elif name.casefold() in there:
            same_named.append(e)
        elif windows_name_problem(name):
            check.left.append((name, f"the name cannot be kept on Windows ({windows_name_problem(name)})"))
        else:
            check.moves.append((os.path.join(src_dir, name), os.path.join(dst_dir, name)))
            taken.add(name.casefold())
    from .classifier import parse_folder_name

    title = parse_folder_name(os.path.basename(dst_dir))[0]
    library = roots[rid_m].name
    for e in same_named:                                # after the plain moves: a new name never takes one of theirs
        name, src = e.name, os.path.join(src_dir, e.name)
        other = os.path.join(dst_dir, there[name.casefold()])
        if e.is_dir(follow_symlinks=False):
            check.left.append((name, "the folder kept already has a folder of this name (sub-folders are not merged)"))
            continue
        if os.path.islink(other) or not os.path.isfile(other):
            check.left.append((name, "the folder kept has a folder or link of this name (nothing is replaced)"))
            continue
        try:
            same = same_content(src, other)
            if same:
                (size, modified), (ksize, kmodified) = file_as_listed(src), file_as_listed(other)
        except (OSError, TypeError) as exc:
            check.left.append((name, f"it cannot be read to compare ({getattr(exc, 'strerror', None) or exc})"))
            continue
        if same:
            check.identical.append(Identical(src, size, modified, other, ksize, kmodified))
            continue
        new = suffixed_name(name, library, taken, title)
        problem = windows_name_problem(new)
        if problem:
            check.left.append((name, f"the new name cannot be kept on Windows ({problem})"))
            continue
        taken.add(new.casefold())
        check.moves.append((src, os.path.join(dst_dir, new)))
        check.suffixed.append((name, new))
    if not check.moves and not check.identical:
        check.refused = ("the folder is empty" if not check.left
                         else "nothing could move - every name in it is left where it is (see below)")
    return check


# --- same-named files ----------------------------------------------------------------------------------------------

_CHUNK = 1024 * 1024


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def same_content(a: str, b: str) -> bool:
    """Two plain files are byte-identical: the same size and the same SHA-256 of their whole content."""
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    return _sha256(a) == _sha256(b)


def _reads_as(name: str, title: str):
    """What the duplicate review reads from a file name: (kind, number) and the other numbers it states."""
    from decimal import Decimal
    from types import SimpleNamespace

    from .inventory import units_from_parsed
    from .parsing import ParseContext, parse_name

    units = units_from_parsed(name, parse_name(name, ParseContext(series_title=title)), 0)
    stated = stated_unit([SimpleNamespace(**{k: getattr(u, k, None) for k in
                                             ("kind", "vol_from", "vol_to", "ch_from", "ch_to", "group_name")})
                          for u in units])
    number = stated.number if stated is not None else Decimal(0)
    return (stated.kind, stated.number) if stated else None, other_numbers(name, number, title)


def suffixed_name(name: str, library: str, taken, title: str = "") -> str:
    """A new name for a file of the library *library* whose name the kept folder has: a ``from <library>`` tag that
    keeps what the duplicate review reads from the name (its chapter / volume number and the other numbers in it), so
    the two copies are grouped there - ``(from X)`` after the name, else inside its last ``[...]``, else ``{from X}`` -
    with a counter (``from X 2``) when that name is taken (*taken*: case-folded names). Never one in *taken*."""
    stem, ext = os.path.splitext(name)
    tag_name = re.sub(r"[\[\](){}]", "-", library).strip() or "another library"
    want = _reads_as(name, title)
    forms = [lambda t: f"{stem} ({t}){ext}", lambda t: f"{stem} {{{t}}}{ext}"]
    if stem.endswith("]"):
        forms.insert(1, lambda t: f"{stem[:-1]} ({t})]{ext}")
    tag = f"from {tag_name}"
    form = next((f for f in forms if _reads_as(f(tag), title) == want), forms[0])
    counter = 1
    while True:
        candidate = form(tag + (f" {counter}" if counter > 1 else ""))
        if candidate.casefold() not in taken:
            return candidate
        counter += 1


def _match_refusal(db, merged_id: int, kept_id: int) -> Optional[str]:
    """Why the two series may not be merged by their matches (None: they may): both must be matched to the SAME record,
    and both matches Confirmed (owner, 2026-10-17: "Confirmed only")."""
    from .same_series import matches_of, merge_refusal

    found = matches_of(db, [merged_id, kept_id])
    sides = [found.get(merged_id), found.get(kept_id)]
    unmatched = [c for c in (s[1] for s in sides if s is not None and s[0] is None)]
    if None in sides or unmatched:
        names = " and ".join(f"{c.root_name} / {c.name}" for c in unmatched) or "a folder"
        return f"{names} has no confirmed MangaUpdates match - match it first"
    if sides[0][0] != sides[1][0]:
        return "the two folders are matched to different series"
    return merge_refusal([sides[0][1], sides[1][1]])


@dataclass(frozen=True)
class ConfirmParts:
    """The confirmation in two parts: *summary* is short and its length does not depend on how many files there are
    (counts, the deletion, what happens to the folders); *details* lists every file and may be long, so the window
    shows it in a scrollable area."""

    title: str
    summary: str
    details: str
    label: str                          # the Merge button's label


def confirm_parts(check: MergeCheck) -> ConfirmParts:
    """The confirmation of the merge: a short summary that always fits a screen (the deletions are stated in it) and
    the full lists of files for a scroll box (owner, 2026-10-24: with 27 files the dialog grew taller than the screen
    and its buttons were off-screen)."""
    n, d = len(check.moves), len(check.identical)
    renamed = dict((new, old) for old, new in check.suffixed)
    plain = [src for src, dst in check.moves if os.path.basename(dst) not in renamed]
    lines = [f"Merge {check.merged_where}", f"into {check.kept_where}?", ""]
    if plain:
        lines.append(f"{_items(len(plain))} move in under their own names.")
    if check.suffixed:
        lines.append(f"{_items(len(check.suffixed))} move in under a new name - the folder kept has a different file of "
                     "that name; the duplicate review then lists both.")
    if d:
        lines.append(f"{d} identical cop{'y' if d == 1 else 'ies'} will be DELETED - byte for byte the same as the "
                     "file of that name in the folder kept. Deleted for good: Undo cannot bring "
                     f"{'it' if d == 1 else 'them'} back.")
    if check.left:
        lines.append(f"{_items(len(check.left))} stay where they are.")
    lines += ["", "Nothing is copied or replaced. The folder kept keeps its MangaUpdates link, settings and download "
                  "history; once the other folder is empty it is removed and its series joins the one kept. Then the "
                  "duplicate files of the kept folder are listed for you to review."
                  + (f" {UNDO_HINT} (the moves only)" if n else " Nothing moves, so there is nothing to undo.")]
    if check.across and n:
        lines += ["", "The two folders are in different library folders: if the system refuses to move files between "
                      "them, MangaList says so and you move them by hand."]
    sections: List[List[str]] = []
    if plain:
        sections.append([f"Moved in under their own names ({_items(len(plain))}):"]
                        + [f"    {os.path.basename(src)}" for src in plain])
    if check.suffixed:
        sections.append([f"Moved in under a new name ({_items(len(check.suffixed))}):"]
                        + [f"    {old}  ->  {new}" for old, new in check.suffixed])
    if d:
        sections.append([f"Identical copies that will be DELETED, for good ({d}):"]
                        + [f"    {c.name}  ({human_size(c.size)})" for c in check.identical])
    if check.left:
        sections.append([f"Left where they are ({_items(len(check.left))}):"]
                        + [f"    {name} - {why}" for name, why in check.left])
    if n:
        label = f"Merge {_items(n)}" + (f" and delete {d}" if d else "")
    else:
        label = f"Delete {d} identical cop{'y' if d == 1 else 'ies'}"
    details = "\n\n".join("\n".join(sec) for sec in sections)
    return ConfirmParts("Merge into this folder?", "\n".join(lines), details, label)


def confirm_text(check: MergeCheck) -> Tuple[str, str, str]:
    """(window title, the whole text, the Merge button's label) of the confirmation - summary and details in one."""
    parts = confirm_parts(check)
    return parts.title, parts.summary + (f"\n\n{parts.details}" if parts.details else ""), parts.label


# --- merging -----------------------------------------------------------------------------------------------------


def _refusal(why: str, check: Optional[MergeCheck] = None) -> MergeResult:
    res = MergeResult(status="refused", message=f"Merge: {why}")
    if check is not None:
        res.merged_folder, res.kept_folder, res.left = check.merged_folder, check.kept_folder, list(check.left)
    return res


def refusal_text(step) -> Optional[str]:
    """What to tell the owner when the system refused a step's rename (None: it did not fail that way)."""
    if step is None or not step.error:
        return None
    if step.error == ACROSS_FILESYSTEMS:
        return EXDEV_TEXT
    if step.error.startswith(_RENAME_FAILED):
        return REFUSED_TEXT.format(name=os.path.basename(step.src), error=step.error[len(_RENAME_FAILED):])
    return None


def merge_folders(db, check: MergeCheck, *, journal: Optional[Journal] = None,
                  scan_wait: float = SCAN_WAIT) -> MergeResult:
    """Merge as *check* says (:func:`check_merge`; the busy check is made again here, under the locks): the scan lock,
    then both library folders' locks (sorted), ONE journal plan applied, the emptied folder removed and its series
    folded into the kept one (see the module docstring)."""
    from .scan_lock import scan_lock

    if not check.ok:
        return _refusal(check.refused or "nothing to move.", check)
    journal = journal or Journal(db)
    roots = _roots_by_id(db)
    root_m, root_k = roots.get(check.merged_root_id), roots.get(check.kept_root_id)
    if root_m is None or root_k is None:
        return _refusal("that library folder is no longer configured.", check)
    res = MergeResult(status="merged", message="", merged_folder=check.merged_folder, kept_folder=check.kept_folder,
                      left=list(check.left), suffixed=list(check.suffixed))
    plan = None
    with scan_lock(db, wait=scan_wait, what="Merge same series") as held:
        if not held:
            return _refusal(SCAN_RUNNING, check)
        loc = db._locate(check.merged_folder)
        with db.connect() as con:
            why = _busy_reason(con, _series_ids_under(con, *loc)) if loc is not None else "its folder is gone"
        if why:
            return _refusal(f"{why}.", check)
        plan_roots = sorted({root_m.path, root_k.path})
        note = f"{check.merged_where} into {check.kept_where}"
        try:
            with journal.lock_roots(plan_roots) as locks:
                if check.moves:
                    try:
                        if check.across:
                            plan = journal.plan_across_roots(MERGE_REASON, check.moves, plan_roots, note=note,
                                                             files=True)
                        else:
                            plan = journal.plan(MERGE_REASON, check.moves, root_path=root_k.path, note=note)
                    except StepRefused as exc:
                        return _refusal(f"{exc}", check)
                    plan = journal.apply(plan.id, lock=locks)
                    res.plan_id = plan.id
                    res.moved = [(s.src, s.dst) for s in plan.steps if s.state == "done"]
                identity = None
                if plan is None or plan.status == "applied":
                    _delete_identical(check, [r.path for r in roots.values()], res, db)
                    res.removed = _remove_if_empty(check.merged_folder)
                    if res.removed:
                        identity = _fold_series(db, check.merged_series_id, check.kept_series_id)
                if plan is not None:
                    _remember(db, plan.id, check, res.removed, identity, res.deleted)
        except LockError as exc:
            return _refusal(_locks_text(exc, db), check)
    if plan is None or plan.status == "applied":
        res.message = (f"Merged {_items(len(res.moved))} of {check.merged_where} into {check.kept_where}"
                       + (f" ({len(res.suffixed)} under a new name)" if res.suffixed else ""))
        if res.deleted:
            res.message += f"; deleted {len(res.deleted)} identical cop{'y' if len(res.deleted) == 1 else 'ies'}"
        if res.not_deleted:
            res.message += "; NOT deleted: " + "; ".join(f"{name} ({why})" for name, why in res.not_deleted[:6])
        if res.removed:
            res.message += "; the emptied folder was removed."
        elif res.left or res.not_deleted:
            names = [name for name, _why in res.left] + [name for name, _why in res.not_deleted]
            res.message += (f"; left in {Path(check.merged_folder).name}: " + ", ".join(names[:6])
                            + (" ..." if len(names) > 6 else "") + " - so the folder stays.")
        else:
            res.message += "; the folder is not empty, so it was left."
        res.message += f" {UNDO_HINT}" if plan is not None else " (Nothing moved: there is nothing to undo.)"
        _log.info("Merge: %s moved %d item(s) of %s into %s (suffixed %d, deleted %d, not deleted %d, folder removed: "
                  "%s, left: %d)", f"plan {plan.id}" if plan else "no plan", len(res.moved), check.merged_folder,
                  check.kept_folder, len(res.suffixed), len(res.deleted), len(res.not_deleted), res.removed,
                  len(res.left))
        return res
    failed = next((s for s in plan.steps if s.state == "failed"), None)
    refused = refusal_text(failed)
    why = refused or (f"{os.path.basename(failed.src)}: {failed.error}" if failed is not None
                      else f"the plan is {plan.status}")
    _log.warning("Merge: plan %d %s after %d of %d items: %s", plan.id, plan.status, len(res.moved), len(plan.steps), why)
    if res.moved:
        res.status = "partly"
        res.message = (f"Merged {len(res.moved)} of {_items(len(plan.steps))}; then: {why} Nothing was deleted. "
                       f"{UNDO_HINT}")
    else:
        res.status = "failed"
        res.message = why if refused else f"Merge: nothing moved - {why}"
    return res


def _delete_identical(check: MergeCheck, root_dirs: List[str], res: MergeResult, db=None) -> None:
    """Delete the byte-identical copies the owner confirmed - each through :func:`delete_checked` (as listed), and only
    while the kept copy is still there as compared. The caller holds the roots' locks."""
    for c in check.identical:
        if file_as_listed(c.kept_path) != (c.kept_size, c.kept_modified):
            res.not_deleted.append((c.name, "the copy in the folder kept changed or is gone since the comparison"))
            continue
        try:
            delete_checked(c.path, c.size, c.modified, root_dirs)
        except DiscardRefused as exc:
            res.not_deleted.append((c.name, str(exc)))
            _log.warning("Merge: identical copy not deleted: %s (%s)", c.path, exc)
            continue
        res.deleted.append(c.name)
        if db is not None:
            record_discard(db, c.path)      # the owner confirmed it: a torrent that filed it is not kept for it
        _log.info("Merge: deleted %s (%d bytes, modified %s) - byte-identical to %s", c.path, c.size, c.modified,
                  c.kept_path)


def _remove_if_empty(folder: str) -> bool:
    """Remove *folder* when it is truly empty (``rmdir`` cannot remove anything with content); False when it stays."""
    try:
        os.rmdir(folder)
    except OSError:
        return False
    _log.info("Merge: removed the emptied folder %s", folder)
    return True


def _remember(db, plan_id: int, check: MergeCheck, removed: bool, identity: Optional[dict],
              deleted: Optional[List[str]] = None) -> None:
    value = {"merged_folder": check.merged_folder, "kept_folder": check.kept_folder, "merged_where": check.merged_where,
             "kept_where": check.kept_where, "kept_series_id": check.kept_series_id, "removed": removed,
             "identity": identity, "deleted": list(deleted or [])}
    with db.connect() as con:
        con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (f"{META_PREFIX}{plan_id}", json.dumps(value, ensure_ascii=False)))


def _recalled(db, plan_id: int) -> dict:
    with db.connect() as con:
        r = con.execute("SELECT value FROM meta WHERE key = ?", (f"{META_PREFIX}{plan_id}",)).fetchone()
    try:
        value = json.loads(r["value"]) if r is not None and r["value"] else {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


# --- the series row of the folder merged away --------------------------------------------------------------------


def _fold_series(db, old_id: Optional[int], kept_id: Optional[int]) -> Optional[dict]:
    """Fold the series *old_id* (its folder is gone: everything moved into the kept folder) into *kept_id*, never over
    the kept series' own data (see the module docstring). Returns what undo needs to restore it, or None."""
    from .identity.carry import _Ctx, _has_link, _rekey, _sync_mu

    if old_id is None or kept_id is None or old_id == kept_id:
        return None
    with db.connect() as con:
        ctx = _Ctx(con)
        o = con.execute("SELECT * FROM series WHERE id = ?", (old_id,)).fetchone()
        t = con.execute("SELECT * FROM series WHERE id = ?", (kept_id,)).fetchone()
        if o is None or t is None:
            return None
        key_o, key_t = ctx.key(o), ctx.key(t)
        snap = {"series": {k: o[k] for k in o.keys()}, "key_o": key_o, "key_t": key_t, "link": None,
                "link_moved": False, "link_dropped": False, "kind_moved": False, "kept_kind": t["kind_hint"],
                "examined_moved": False, "examined_t": key_t in ctx.examined, "ledger": []}
        moved, dropped = ["row"], []
        if _has_link(con, key_o):
            link = con.execute("SELECT * FROM links_cache WHERE folder = ?", (key_o,)).fetchone()
            snap["link"] = {k: link[k] for k in link.keys()}
            if not _has_link(con, key_t) and _rekey(con, key_o, key_t):
                snap["link_moved"] = True
                moved.append("link")
            else:
                con.execute("DELETE FROM links_cache WHERE folder = ?", (key_o,))
                snap["link_dropped"] = True
                dropped.append("link")
        if o["kind_hint"] and not t["kind_hint"]:
            con.execute("UPDATE series SET kind_hint = ? WHERE id = ?", (o["kind_hint"], kept_id))
            snap["kind_moved"] = True
            moved.append("kind")
        if ctx.move_examined(key_o, key_t):
            snap["examined_moved"] = True
            moved.append("examined")
        snap["ledger"] = [r[0] for r in con.execute("SELECT id FROM ledger WHERE series_id = ?", (old_id,))]
        con.execute("UPDATE ledger SET series_id = ? WHERE series_id = ?", (kept_id, old_id))
        con.execute("DELETE FROM series WHERE id = ?", (old_id,))
        _sync_mu(con, kept_id, key_t)
        con.execute("INSERT INTO series_carries (from_series_id, to_series_id, from_root_id, from_path, to_root_id,"
                    " to_path, how, moved, kept, at) VALUES (?,?,?,?,?,?, 'merge', ?, ?, ?)",
                    (old_id, kept_id, o["root_id"], o["rel_path"], t["root_id"], t["rel_path"], json.dumps(moved),
                     json.dumps(dropped), ctx.now))
        ctx.flush()
    _log.info("Merge: series %d folded into %d (moved %s, dropped %s)", old_id, kept_id, ",".join(moved),
              ",".join(dropped) or "-")
    return snap


def _restore_series(db, snap: dict, kept_id: Optional[int]) -> Optional[str]:
    """Put the folded series back as it was (undo, before the files move back); why not, or None."""
    from .identity.carry import _Ctx, _sync_mu

    row = snap.get("series") or {}
    if not row:
        return None
    with db.connect() as con:
        if con.execute("SELECT 1 FROM series WHERE id = ? OR (root_id = ? AND rel_path = ?)",
                       (row["id"], row["root_id"], row["rel_path"])).fetchone():
            return "a series is recorded at the merged-away folder again - rescan, then move the files back by hand"
        ctx = _Ctx(con)
        cols = list(row.keys())
        values = dict(row, status="present", missing_since=None) if "missing_since" in row else dict(row, status="present")
        con.execute(f"INSERT INTO series ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [values[c] for c in cols])
        key_o, key_t = snap["key_o"], snap["key_t"]
        link = snap.get("link")
        if snap.get("link_moved"):
            if not con.execute("SELECT 1 FROM links_cache WHERE folder = ?", (key_o,)).fetchone():
                con.execute("UPDATE links_cache SET folder = ? WHERE folder = ?", (key_o, key_t))
        elif snap.get("link_dropped") and link:
            names = list(link.keys())
            con.execute(f"INSERT OR IGNORE INTO links_cache ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                        [link[k] for k in names])
        if snap.get("kind_moved") and kept_id is not None:
            con.execute("UPDATE series SET kind_hint = ? WHERE id = ?", (snap.get("kept_kind"), kept_id))
        if snap.get("examined_moved"):
            if snap.get("examined_t"):
                if key_o not in ctx.examined:
                    ctx._examined = ctx.examined + [key_o]
                    ctx._examined_dirty = True
            else:
                ctx.move_examined(key_t, key_o)
        ids = [int(i) for i in snap.get("ledger") or []]
        if ids:
            con.execute(f"UPDATE ledger SET series_id = ? WHERE id IN ({','.join('?' * len(ids))})", (row["id"], *ids))
        _sync_mu(con, row["id"], key_o)
        if kept_id is not None:
            _sync_mu(con, kept_id, key_t)
        ctx.flush()
    _log.info("Undo merge: series %d restored at %s", row["id"], row["rel_path"])
    return None


# --- undo --------------------------------------------------------------------------------------------------------


def last_merge(db) -> Optional[MergeRecord]:
    """The newest merge that still has items moved (applied, or failed / interrupted part way)."""
    journal = Journal(db)
    with db.connect() as con:
        rows = con.execute("SELECT id FROM journal_plans WHERE reason = ? AND status IN ('applied', 'failed',"
                           " 'interrupted', 'undo_failed') ORDER BY id DESC LIMIT 5", (MERGE_REASON,)).fetchall()
    for r in rows:
        plan = journal.get_plan(r["id"])
        done = [s for s in plan.steps if s.state == "done"]
        if not done:
            continue
        meta = _recalled(db, plan.id)
        merged = meta.get("merged_folder") or os.path.dirname(done[0].src)
        kept = meta.get("kept_folder") or os.path.dirname(done[0].dst)
        return MergeRecord(plan.id, len(done), merged, kept, plan.created_at)
    return None


def undo_merge(db, plan_id: int, *, journal: Optional[Journal] = None, scan_wait: float = SCAN_WAIT) -> MergeResult:
    """Undo the merge *plan_id* (see the module docstring)."""
    from .scan_lock import scan_lock

    journal = journal or Journal(db)
    try:
        plan = journal.get_plan(plan_id)
    except PlanStateError as exc:
        return _refusal(str(exc))
    if plan.reason != MERGE_REASON:
        return _refusal(f"plan {plan_id} is not a merge.")
    meta = _recalled(db, plan_id)
    merged, kept = meta.get("merged_folder", ""), meta.get("kept_folder", "")
    done = [s for s in plan.steps if s.state == "done"]
    with scan_lock(db, wait=scan_wait, what="Undo merge") as held:
        if not held:
            return _refusal(SCAN_RUNNING)
        loc = db._locate(kept) if kept else None
        if loc is not None:
            with db.connect() as con:
                why = _busy_reason(con, _series_ids_under(con, *loc))
            if why:
                return _refusal(f"{os.path.basename(kept)}: {why}.")
        try:
            with journal.lock_roots(journal.plan_roots(plan)) as locks:
                if meta.get("removed") and merged:
                    snap = meta.get("identity")
                    if snap:
                        why = _restore_series(db, snap, meta.get("kept_series_id"))
                        if why:
                            return _refusal(why)
                    if not os.path.lexists(merged):
                        os.mkdir(merged)
                after = journal.undo(plan_id, lock=locks)
        except LockError as exc:
            return _refusal(_locks_text(exc, db))
        except PlanStateError as exc:
            return _refusal(str(exc))
        except OSError as exc:
            return _refusal(f"the merged-away folder cannot be made again ({exc.strerror or exc}).")
    done_ids = {s.id for s in done}
    back = [(s.dst, s.src) for s in after.steps if s.state == "undone" and s.id in done_ids]
    res = MergeResult(status="undone", message="", plan_id=plan_id, moved=back, merged_folder=merged, kept_folder=kept)
    if after.status == "undone":
        res.message = f"Moved {_items(len(back))} back to {meta.get('merged_where') or merged}."
        gone = len(meta.get("deleted") or [])
        if gone:
            res.message += (f" The {gone} identical cop{'y' if gone == 1 else 'ies'} deleted at the merge cannot come "
                            "back (the kept folder still has the same file).")
        _log.info("Merge: plan %d undone (%d item(s) back)", plan_id, len(back))
    else:
        stuck = next((s for s in after.steps if s.state == "done" and s.error), None)
        res.status = "partly"
        res.message = (f"Moved {len(back)} of {_items(len(done))} back; "
                       f"{os.path.basename(stuck.dst) if stuck else 'one'}: {stuck.error if stuck else after.status}")
        _log.warning("Merge: plan %d not fully undone: %s", plan_id, res.message)
    return res


__all__ = ["MERGE_REASON", "UNDO_HINT", "EXDEV_TEXT", "REFUSED_TEXT", "Identical", "MergeCheck", "MergeResult",
           "MergeRecord", "ConfirmParts", "same_content", "suffixed_name", "check_merge", "confirm_parts", "confirm_text",
           "merge_folders", "last_merge", "undo_merge", "refusal_text"]
