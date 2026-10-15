"""Move to library: a series folder moved from one library folder (root) into another, through the journal (owner,
2026-10-15, with a Complete series in "Manga-Ongoing": "how do i move this between roots?" - then "Can you add a move to
library feature now"). No Qt: the List's row menu (:mod:`mangalist.gui.main_window`, the shared series menu) asks and
shows; everything that reads or writes is here.

**What moves.** Each series folder as a whole, under its own name, into the TOP of the target library - or, chosen in
the confirmation, into a folder at the top of it (owner, 2026-10-15: "OK default, user settable location"; e.g. a
franchise parent, where the series becomes one of its subseries): ONE rename per series, all of them in one cross-root
journal plan (:meth:`mangalist.store.journal.Journal.plan_across_roots`) - moves
only, never deletes, write-ahead, crash recovery, no-replace, undo. Before that hand-made moves were recognised at the next
scan by the archives' content signatures (:mod:`mangalist.identity.moves`); now the identity follows directly
(:func:`mangalist.identity.carry.follow_folder_move` and the archive rows' re-point): the series keeps its id - and so
its download history, its own automatic-download mode, Unbundled chapters, findings and units - and gets the new root
and path; its MangaUpdates link and examined mark are re-keyed to the new folder; ``series_carries`` records it
(``how='journal'``). The caller then scans both folders (selective scanning) so the List shows it at once.

**Refused, with the reason** (:func:`check_move`; nothing moves for that series, the others still can):

- a series with a download in progress - queued, sent or downloaded but not filed yet - since filing targets its current
  folder; one whose replaced chapter files wait for the owner's confirmation (they are listed by the current path); and
  one whose replaced chapters are HELD in the holding folder (owner, 2026-10-15: "Refuse until emptied" - Restore puts
  them back by the old path, which the move would leave behind): empty or restore them first;
- a folder that is missing, not in a library folder, or not scanned into the library yet;
- a target library that already has a folder of that name (no-replace), still remembers a missing series there (the
  Missing series list decides that one first), or excludes that name (the series would leave the list);
- into a folder of the target (not its top): a folder that holds subseries of its own (they would lie too deep for a
  scan), or whose name reads as a part of the folder it would go into (``Season 2``, ``Volumes``, ``Extras``: the scan
  would take it for a unit of that series, not a series);
- the whole move while a library scan runs (here or in the container: the scan lock) or another MangaList instance
  writes one of the two libraries (their ``.mangalist.lock``); the headless runner's filing, renaming and scanning wait
  for the move the same way (it takes the scan lock and both roots' locks before anything moves).

**Different drives / shares** (owner, 2026-10-15: "MangaList should attempt to move it and raise an error to the user to
make them move it themselves if refused by the system"). Nothing is refused up front for the two sides' devices: the
rename is tried. When the system refuses it - ``EXDEV`` (another filesystem, or one of Unraid's user shares that will not
rename into another under the one ``/mnt/user`` mount) or any other refusal of the rename (permissions, a read-only
share) - that step fails with nothing moved and the owner is told plainly to move the folder with a file manager; the
next scan recognises the move by the archives. MangaList never copies + deletes.

**MangaPixer** resolves its item by root + folder: after the move the series shows as a fresh folder of the target
library until MangaPixer has rescanned that library (MangaPixer has its own carry-over); MangaPixer's ``carriedFrom``
pair finds nothing left at the old path and is settled as ``nothing``.

**Undo** (:func:`undo_move`): the last move's plan undone (each folder renamed back, no-replace, under the same locks);
the series rows follow back the same way.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .store.journal import ACROSS_FILESYSTEMS, AcrossFilesystems, Journal, PlanStateError, StepRefused

#: The journal's error for a rename the system refused for another reason than ``EXDEV`` (``Journal._apply_step``).
_RENAME_FAILED = "rename failed: "
from .store.lock import LockBusy, LockError

_log = logging.getLogger(__name__)

MOVE_REASON = "move to library"
SCAN_WAIT = 2.0             # seconds the move waits for the scan lock before it says a scan is running

EXDEV_TEXT = ("The system refused to move the folder between these library folders (they are on different drives or "
              "shares). MangaList never copies + deletes: please move it yourself with a file manager - MangaList "
              "recognises it at the next scan.")
#: Any other refusal of the rename by the system ({name}, {error}).
REFUSED_TEXT = ("The system refused to move {name} ({error}). Nothing was moved. Please move it yourself with a file "
                "manager - MangaList recognises it at the next scan.")
HELD_REASON = ("its replaced chapters are held in the holding folder (Restore puts them back by the current folder) - "
               "empty or restore them first (Download tab > Replaced chapters)")
SCAN_RUNNING = "A library scan is running (here or in the container) - try again when it has finished."
UNDO_HINT = "Undo: Move to library > Undo last move."

#: Download statuses that still file into the series' current folder (the ledger; all tools).
IN_PROGRESS = ("queued", "dispatched", "sent", "downloaded")
_MODE_LABELS = {"off": "Off", "notify": "Notify", "auto": "Auto"}


@dataclass
class MoveItem:
    title: str
    folder: str                         # the series folder, under its root as configured
    target: str                         # where it goes: the target root + the folder's name
    series_id: int
    root_id: int
    rel_path: str
    own_mode: Optional[str] = None      # its own automatic-download mode (None: it follows a default)
    new_rel: str = ""                   # its folder inside the target library afterwards ("Name" or "Parent/Name")


@dataclass
class MoveCheck:
    """What a move to *target* would do: the series that move, those refused (title, why) and those already there."""

    target: object                                              # store.Root
    into: str = ""                                              # a folder at the top of the target ('' = its top)
    asked: List[Tuple[str, str]] = field(default_factory=list)  # the (folder, title) pairs asked about (to re-check)
    items: List[MoveItem] = field(default_factory=list)
    refused: List[Tuple[str, str]] = field(default_factory=list)
    already: List[str] = field(default_factory=list)            # titles already in the target library
    source_names: List[str] = field(default_factory=list)
    defaults_now: List[Tuple[str, bool]] = field(default_factory=list)   # (mode, library's) of each series on a default
    target_default: Tuple[str, bool] = ("notify", False)       # the target's default: (mode, True when the library's)
    renames_automatically: bool = False

    @property
    def follows_default(self) -> int:
        return sum(1 for it in self.items if it.own_mode is None)

    @property
    def own_modes(self) -> int:
        return sum(1 for it in self.items if it.own_mode is not None)

    @property
    def destination(self) -> str:
        """The folder the series go into: the target library's, or the chosen folder inside it."""
        return str(Path(self.target.path) / self.into) if self.into else str(self.target.path)

    @property
    def destination_name(self) -> str:
        return f"{self.target.name} / {self.into}" if self.into else str(self.target.name)


@dataclass
class MoveResult:
    status: str                         # moved | partly | refused | failed | undone
    message: str
    plan_id: Optional[int] = None
    moved: List[Tuple[str, str]] = field(default_factory=list)  # (old folder, new folder) of each folder that moved
    target_name: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("moved", "undone")

    @property
    def paths(self) -> List[str]:
        """Both sides of every folder that moved: the scope of the scan after it."""
        return [p for pair in self.moved for p in pair]


@dataclass
class MoveRecord:
    """The last move that can be undone."""

    plan_id: int
    count: int                          # folders it moved (still moved)
    target_name: str
    source_names: List[str]
    created_at: str

    @property
    def label(self) -> str:
        back = " / ".join(self.source_names) or "where they were"
        return f"Undo last move ({_series(self.count)} back to {back})"


def _series(n: int) -> str:
    return f"{n} series"


def _mode_label(mode: Optional[str]) -> str:
    return _MODE_LABELS.get(mode or "", str(mode or ""))


def _roots_by_id(db) -> Dict[int, object]:
    return {r.id: r for r in db.list_roots()}


def _under(db, root, rel_path: str) -> str:
    return str(Path(root.path).joinpath(*rel_path.split("/")))


def _series_ids_under(con, root_id: int, rel_path: str) -> List[int]:
    prefix = rel_path.rstrip("/") + "/"
    return [r[0] for r in con.execute("SELECT id FROM series WHERE root_id = ? AND (rel_path = ? OR substr(rel_path, 1, ?)"
                                      " = ?)", (root_id, rel_path, len(prefix), prefix))]


def _count(con, sql: str, ids: Sequence[int], extra: Sequence = ()) -> int:
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    return int(con.execute(sql.format(ids=marks), (*ids, *extra)).fetchone()[0])


def _busy_reason(con, ids: Sequence[int]) -> Optional[str]:
    """Why these series (a folder and its subseries) cannot move now: a download that still files into the folder,
    replaced chapters waiting for the owner's confirmation, or replaced chapters held in the holding folder."""
    marks = ",".join("?" * len(IN_PROGRESS))
    if _count(con, "SELECT COUNT(*) FROM ledger WHERE series_id IN ({ids}) AND status IN (" + marks + ")", ids,
              IN_PROGRESS):
        return ("a download is in progress (queued, sent or downloaded, not filed yet) and filing targets its current "
                "folder - move it once it is filed")
    try:
        if _count(con, "SELECT COUNT(*) FROM replacements WHERE series_id IN ({ids}) AND status = 'pending'", ids):
            return "replaced chapter files wait for your confirmation (Download tab) - confirm or decline them first"
        if _count(con, "SELECT COUNT(*) FROM replacements WHERE series_id IN ({ids}) AND status = 'held'", ids):
            return HELD_REASON
    except Exception:  # noqa: BLE001 - an older database without the table
        pass
    return None


def place_choices(db, target_root_id: int) -> List[str]:
    """The folders at the top of the library *target_root_id* a move can go into (besides its top): its direct child
    folders, by name - not hidden ones, symbolic links or excluded ones. One directory read."""
    target = _roots_by_id(db).get(target_root_id)
    if target is None:
        return []
    excl = target.exclusion_set()
    try:
        with os.scandir(target.path) as it:
            names = [e.name for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    return sorted((n for n in names if not excl.excludes(n, True)), key=lambda n: (n.casefold(), n))


def _into_problem(target, into: str) -> Optional[str]:
    """Why *into* cannot take a move (None: it can): one folder directly at the top of the target library, there now."""
    if not into:
        return None
    if into in (".", "..") or "/" in into or "\\" in into:
        return f"{into!r} is not a folder at the top of {target.name}"
    path = Path(target.path) / into
    if os.path.islink(path) or not os.path.isdir(path):
        return f"{target.name} has no folder named {into!r}"
    if target.exclusion_set().excludes(into, True):
        return f"{target.name} excludes {into!r} (Settings > Library), so the series would leave the list"
    return None


def check_move(db, folders: Sequence[Tuple[str, str]], target_root_id: int, into: str = "") -> MoveCheck:
    """What moving *folders* (``(folder, title)``, as the List shows them) into the library *target_root_id* would do:
    the series that can move (each under its folder's name into the top of the target, or into the folder *into* at its
    top) and the reasons for those that cannot. Reads only."""
    roots = _roots_by_id(db)
    target = roots.get(target_root_id)
    if target is None:
        raise ValueError("that library folder is no longer configured")
    into = (into or "").strip()
    check = MoveCheck(target=target, into=into, asked=[(str(f), t) for f, t in folders])
    if not os.path.isdir(target.path):
        check.refused = [(title, f"{target.name}'s folder is not reachable ({target.path})") for _f, title in folders]
        return check
    problem = _into_problem(target, into)
    if problem:
        check.refused = [(title, problem) for _f, title in folders]
        return check
    from .scanner import _is_unit_subfolder
    from .store.findings import FindingStore

    modes = FindingStore(db)
    excl = target.exclusion_set()
    located = []
    for folder, title in folders:
        loc = db._locate(folder)
        if loc is None:
            check.refused.append((title, "it is not inside a library folder"))
            continue
        located.append((folder, title, loc))
    # A folder inside another folder of the selection moves with it (a subseries with its franchise parent).
    tops = [(f, t, loc) for f, t, loc in located
            if not any(o != loc and o[0] == loc[0] and loc[1].startswith(o[1].rstrip("/") + "/") for _f, _t, o in located)]
    taken = set()
    sources = []
    where = check.destination_name
    with db.connect() as con:
        for folder, title, (root_id, rel) in tops:
            root = roots.get(root_id)
            if root_id == target.id:
                check.already.append(title)
                continue
            row = con.execute("SELECT id, auto_mode FROM series WHERE root_id = ? AND rel_path = ?",
                              (root_id, rel)).fetchone()
            src = _under(db, root, rel)
            name = rel.rsplit("/", 1)[-1]
            new_rel = f"{into}/{name}" if into else name
            dst = str(Path(target.path).joinpath(*new_rel.split("/")))
            if row is None:
                check.refused.append((title, "it is not scanned into the library yet - rescan first"))
                continue
            if not os.path.isdir(src) or os.path.islink(src):
                check.refused.append((title, f"its folder is missing ({src})"))
                continue
            if os.path.lexists(dst) or os.path.normcase(dst) in taken:
                check.refused.append((title, f"{where} already has a folder named {name!r} (nothing is replaced)"))
                continue
            if con.execute("SELECT 1 FROM series WHERE root_id = ? AND rel_path = ?", (target.id, new_rel)).fetchone():
                check.refused.append((title, f"{where} still remembers a missing series named {name!r} - forget "
                                             "or re-attach it in Missing series first"))
                continue
            if excl.excludes(new_rel, True):
                check.refused.append((title, f"{target.name} excludes {new_rel!r} (Settings > Library), so it would "
                                             "leave the list"))
                continue
            ids = _series_ids_under(con, root_id, rel)
            if into and len(ids) > 1:
                check.refused.append((title, f"it holds subseries of its own, which inside {into!r} would lie too deep "
                                             f"for a scan - move it to the top of {target.name}"))
                continue
            if into and _is_unit_subfolder(name):
                check.refused.append((title, f"inside {into!r} a folder named {name!r} reads as a part of {into!r} "
                                             f"(not a series) - move it to the top of {target.name}"))
                continue
            why = _busy_reason(con, ids)
            if why:
                check.refused.append((title, why))
                continue
            own = row["auto_mode"] if row["auto_mode"] in _MODE_LABELS else None
            check.items.append(MoveItem(title=title, folder=src, target=dst, series_id=int(row["id"]), root_id=root_id,
                                        rel_path=rel, own_mode=own, new_rel=new_rel))
            taken.add(os.path.normcase(dst))
            if root.name not in sources:
                sources.append(root.name)
    check.source_names = sources
    following = [it.series_id for it in check.items if it.own_mode is None]
    if following:
        check.defaults_now = list(modes.defaults(following).values())
    check.target_default = ((target.auto_mode, True) if target.auto_mode in _MODE_LABELS
                            else (modes.default_mode(), False))
    check.renames_automatically = getattr(target, "enforce_naming", None) == "automatic"
    return check


def refused_lines(check: MoveCheck) -> List[str]:
    return [f"{title} - {why}" for title, why in check.refused]


def confirm_text(check: MoveCheck, *, show_modes: bool = True) -> Tuple[str, str, str]:
    """(window title, text, the Move button's label) of the confirmation: what moves where, and what changes because of
    the target library (its automatic-download default, its "Rename automatically")."""
    target = check.target
    n = len(check.items)
    listed = check.items[:12]
    lines = [f"Move {_series(n)} from {' / '.join(check.source_names)} to {check.destination_name}?", ""]
    lines += [f"    {it.title}" for it in listed]
    if n > len(listed):
        lines.append(f"    ... and {n - len(listed)} more")
    lines += ["", f"Into the folder: {check.destination}" + ("" if check.into else " (the top of the library)"), "",
              "Each series folder moves as a whole (one rename - nothing is copied or deleted) and keeps its "
              f"MangaUpdates link, settings and download history. {UNDO_HINT}"]
    changes = []
    if show_modes and check.follows_default:
        mode, library = check.target_default
        whose = f"{target.name}'s default" if library else "the default (Settings > Automation)"
        now = {m for m, _lib in check.defaults_now}
        was = f" (now {_mode_label(next(iter(now)))})" if len(now) == 1 and mode not in now else (
            " (now their libraries' defaults)" if len(now) > 1 else "")
        verb = "will follow" if was else "follow"
        changes.append(f"- Automatic downloads: {_series(check.follows_default)} on \"Use the default\" {verb} {whose}: "
                       f"{_mode_label(mode)}{was}." + (f" {_series(check.own_modes)} "
                                                      f"{'keeps its' if check.own_modes == 1 else 'keep their'} own mode."
                                                      if check.own_modes else ""))
    if check.renames_automatically:
        changes.append("- Files not named by the scheme: Rename automatically - their files may be renamed to the "
                       "naming scheme.")
    if changes:
        lines += ["", f"In {target.name}:"] + changes
    lines += ["", f"MangaPixer shows them under {target.name} once it has rescanned it."]
    if check.already:
        lines += ["", f"Already in {target.name} (left as they are): {', '.join(check.already[:6])}"
                      + (" ..." if len(check.already) > 6 else "")]
    if check.refused:
        lines += ["", "Not moved:"] + [f"    {line}" for line in refused_lines(check)[:12]]
    return f"Move to {target.name}?", "\n".join(lines), f"Move {_series(n)}"


def _refusal(why: str) -> MoveResult:
    return MoveResult(status="refused", message=f"Move to library: {why}")


def _locks_text(exc: Exception, db) -> str:
    if isinstance(exc, LockBusy):
        root = db.root_for_path(str(exc.path.parent))
        who = f"{exc.holder.host} (pid {exc.holder.pid})" if exc.holder else "another MangaList instance"
        return f"{root.name if root else exc.path.parent} is being changed by {who} - try again in a moment."
    return f"a library folder cannot be locked ({exc})."


def move_series(db, check: MoveCheck, *, journal: Optional[Journal] = None, scan_wait: float = SCAN_WAIT) -> MoveResult:
    """Move the series *check* lets move (:func:`check_move`; each one is checked again here, under the locks) into its
    target library: the scan lock, then both roots' locks (sorted), then ONE cross-root journal plan applied."""
    from .scan_lock import scan_lock

    if not check.items:
        return _refusal("nothing to move.")
    journal = journal or Journal(db)
    target = check.target
    roots = _roots_by_id(db)
    with scan_lock(db, wait=scan_wait, what="Move to library") as held:
        if not held:
            return _refusal(SCAN_RUNNING)
        with db.connect() as con:           # a download may have been sent since the confirmation
            for it in check.items:
                why = _busy_reason(con, _series_ids_under(con, it.root_id, it.rel_path))
                if why:
                    return _refusal(f"{it.title}: {why}.")
        plan_roots = sorted({roots[it.root_id].path for it in check.items if it.root_id in roots} | {target.path})
        try:
            with journal.lock_roots(plan_roots) as locks:
                try:
                    plan = journal.plan_across_roots(MOVE_REASON, [(it.folder, it.target) for it in check.items],
                                                     plan_roots, note=f"{_series(len(check.items))} to {target.name}")
                except AcrossFilesystems:   # no longer raised when planning (the rename is tried); kept for safety
                    return MoveResult(status="failed", message=EXDEV_TEXT, target_name=target.name)
                except StepRefused as exc:
                    return _refusal(f"{exc}")
                plan = journal.apply(plan.id, lock=locks)
        except LockError as exc:
            return _refusal(_locks_text(exc, db))
    moved = [(s.src, s.dst) for s in plan.steps if s.state == "done"]
    res = MoveResult(status="moved", message="", plan_id=plan.id, moved=moved, target_name=target.name)
    if plan.status == "applied":
        res.message = f"Moved {_series(len(moved))} to {target.name}. {UNDO_HINT}"
        _log.info("Move to library: plan %d moved %d series to %s", plan.id, len(moved), target.name)
        return res
    failed = next((s for s in plan.steps if s.state == "failed"), None)
    refused = refusal_text(failed)
    why = refused or (f"{os.path.basename(failed.src)}: {failed.error}" if failed is not None
                      else f"the plan is {plan.status}")
    _log.warning("Move to library: plan %d %s after %d of %d folders: %s", plan.id, plan.status, len(moved),
                 len(plan.steps), why)
    if moved:
        res.status = "partly"
        res.message = f"Moved {len(moved)} of {_series(len(plan.steps))} to {target.name}; then: {why} {UNDO_HINT}"
    else:
        res.status = "failed"
        res.message = why if refused else f"Move to library: nothing moved - {why}"
    return res


def refusal_text(step) -> Optional[str]:
    """What to tell the owner when the system refused a step's rename (None: the step did not fail that way): EXDEV
    (another drive or share) or any other refusal of the rename (permissions, a read-only share) - move it yourself."""
    if step is None or not step.error:
        return None
    if step.error == ACROSS_FILESYSTEMS:
        return EXDEV_TEXT
    if step.error.startswith(_RENAME_FAILED):
        return REFUSED_TEXT.format(name=os.path.basename(step.src), error=step.error[len(_RENAME_FAILED):])
    return None


def last_move(db) -> Optional[MoveRecord]:
    """The newest move to library that still has folders moved (applied, or failed / interrupted part way)."""
    journal = Journal(db)
    with db.connect() as con:
        rows = con.execute("SELECT id FROM journal_plans WHERE reason = ? AND status IN ('applied', 'failed',"
                           " 'interrupted', 'undo_failed') ORDER BY id DESC LIMIT 5", (MOVE_REASON,)).fetchall()
    for r in rows:
        plan = journal.get_plan(r["id"])
        done = [s for s in plan.steps if s.state == "done"]
        if not done:
            continue
        names: List[str] = []
        target = ""
        for s in done:
            src_root, dst_root = db.root_for_path(os.path.dirname(s.src)), db.root_for_path(os.path.dirname(s.dst))
            if src_root is not None and src_root.name not in names:
                names.append(src_root.name)
            if dst_root is not None:
                target = dst_root.name
        return MoveRecord(plan.id, len(done), target, names, plan.created_at)
    return None


def undo_move(db, plan_id: int, *, journal: Optional[Journal] = None, scan_wait: float = SCAN_WAIT) -> MoveResult:
    """Undo the move *plan_id*: each folder renamed back (no-replace), under the scan lock and the roots' locks; the
    series rows follow back. Refused while a download of one of them is in progress (it files into the new folder)."""
    from .scan_lock import scan_lock

    journal = journal or Journal(db)
    try:
        plan = journal.get_plan(plan_id)
    except PlanStateError as exc:
        return _refusal(str(exc))
    if plan.reason != MOVE_REASON:
        return _refusal(f"plan {plan_id} is not a move to library.")
    done = [s for s in plan.steps if s.state == "done"]
    with scan_lock(db, wait=scan_wait, what="Undo move to library") as held:
        if not held:
            return _refusal(SCAN_RUNNING)
        with db.connect() as con:
            for s in done:
                loc = db._locate(s.dst)
                if loc is None:
                    continue
                why = _busy_reason(con, _series_ids_under(con, loc[0], loc[1]))
                if why:
                    return _refusal(f"{os.path.basename(s.dst)}: {why}.")
        try:
            with journal.lock_roots(journal.plan_roots(plan)) as locks:
                after = journal.undo(plan_id, lock=locks)
        except LockError as exc:
            return _refusal(_locks_text(exc, db))
        except PlanStateError as exc:
            return _refusal(str(exc))
    done_ids = {s.id for s in done}
    back = [(s.dst, s.src) for s in after.steps if s.state == "undone" and s.id in done_ids]
    res = MoveResult(status="undone", message="", plan_id=plan_id, moved=back)
    if after.status == "undone":
        res.message = f"Moved {_series(len(back))} back."
        _log.info("Move to library: plan %d undone (%d series back)", plan_id, len(back))
    else:
        stuck = next((s for s in after.steps if s.state == "done" and s.error), None)
        res.status = "partly"
        res.message = (f"Moved {len(back)} of {_series(len(done))} back; "
                       f"{os.path.basename(stuck.dst) if stuck else 'one'}: {stuck.error if stuck else after.status}")
        _log.warning("Move to library: plan %d not fully undone: %s", plan_id, res.message)
    return res


__all__ = ["MOVE_REASON", "EXDEV_TEXT", "REFUSED_TEXT", "HELD_REASON", "SCAN_RUNNING", "IN_PROGRESS", "MoveItem", "MoveCheck", "MoveResult", "MoveRecord",
           "check_move", "place_choices", "confirm_text", "refused_lines", "move_series", "last_move", "undo_move"]
