"""Merge into...: one series held in two folders (:mod:`mangalist.same_series`) brought together - every file and
sub-folder of the folder merged away moved into the folder kept, through the journal (owner, 2026-10-17, on the
Duplicates view's "Same series in two folders": "Flag + merge the others"). No Qt: the Duplicates view
(:mod:`mangalist.gui.duplicates_view`) asks which folder is kept and confirms; everything that reads or writes is here.
It reuses Move to library's machinery (:mod:`mangalist.library_move`): the same refusals, locks, journal plans, ``EXDEV``
handling and undo.

**What moves.** Each direct child of the merged-away folder - a file, or a sub-folder as a whole (``Season 2``,
``Extras``, a subseries) - under its own name into the kept folder: ONE journal plan (moves only, never deletes,
write-ahead, crash recovery, undo) - within one library folder a plan of that root
(:meth:`~mangalist.store.journal.Journal.plan`), across two a cross-root plan with files allowed
(:meth:`~mangalist.store.journal.Journal.plan_across_roots` ``files=True``). Nothing is ever replaced or merged
deeper: a name the kept folder already has (compared without case, as Windows and macOS shares do) is LEFT where it is
and reported, so the owner compares the two himself; so is a symbolic link, a MangaList lock file and a name Windows
cannot hold. A nested copy (``Name/Name``) only goes into the folder it lies in (:meth:`SamePair.merge_choices`).

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

import json
import logging
import os
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
    left: List[Tuple[str, str]] = field(default_factory=list)       # (name, why it stays)
    refused: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.refused is None and bool(self.moves)

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
            there = {e.name.casefold() for e in it}
        with os.scandir(src_dir) as it:
            children = sorted(it, key=lambda e: (e.name.casefold(), e.name))
    except OSError as exc:
        check.refused = f"a folder cannot be read ({exc.strerror or exc})"
        return check
    for e in children:
        name = e.name
        if name.startswith(LOCK_NAME):
            check.left.append((name, "a MangaList lock file"))
        elif e.is_symlink():
            check.left.append((name, "a symbolic link - move it by hand"))
        elif not (e.is_file(follow_symlinks=False) or e.is_dir(follow_symlinks=False)):
            check.left.append((name, "not a plain file or folder"))
        elif name.casefold() in there:
            check.left.append((name, "the folder kept already has this name (nothing is replaced)"))
        elif windows_name_problem(name):
            check.left.append((name, f"the name cannot be kept on Windows ({windows_name_problem(name)})"))
        else:
            check.moves.append((os.path.join(src_dir, name), os.path.join(dst_dir, name)))
    if not check.moves:
        check.refused = ("the folder is empty" if not check.left
                         else "nothing could move - every name in it is left where it is (see below)")
    return check


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


def confirm_text(check: MergeCheck) -> Tuple[str, str, str]:
    """(window title, text, the Merge button's label) of the confirmation."""
    n = len(check.moves)
    lines = [f"Move {_items(n)} from", f"    {check.merged_where}", "into", f"    {check.kept_where}?", ""]
    lines += [f"    {os.path.basename(src)}" for src, _dst in check.moves[:12]]
    if n > 12:
        lines.append(f"    ... and {n - 12} more")
    lines += ["", "Each file and sub-folder moves under its own name (nothing is copied, replaced or deleted). "
                  "The folder kept keeps its MangaUpdates link, settings and download history; once the other folder "
                  "is empty it is removed and its series joins the one kept. Then the duplicate files of the kept "
                  f"folder are listed for you to review. {UNDO_HINT}"]
    if check.across:
        lines += ["", "The two folders are in different library folders: if the system refuses to move files between "
                      "them, MangaList says so and you move them by hand."]
    if check.left:
        lines += ["", "Left where they are:"] + [f"    {name} - {why}" for name, why in check.left[:12]]
        if len(check.left) > 12:
            lines.append(f"    ... and {len(check.left) - 12} more")
    return "Merge into this folder?", "\n".join(lines), f"Merge {_items(n)}"


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
                      left=list(check.left))
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
                try:
                    if check.across:
                        plan = journal.plan_across_roots(MERGE_REASON, check.moves, plan_roots, note=note, files=True)
                    else:
                        plan = journal.plan(MERGE_REASON, check.moves, root_path=root_k.path, note=note)
                except StepRefused as exc:
                    return _refusal(f"{exc}", check)
                plan = journal.apply(plan.id, lock=locks)
                res.plan_id = plan.id
                res.moved = [(s.src, s.dst) for s in plan.steps if s.state == "done"]
                identity = None
                if plan.status == "applied":
                    res.removed = _remove_if_empty(check.merged_folder)
                    if res.removed:
                        identity = _fold_series(db, check.merged_series_id, check.kept_series_id)
                _remember(db, plan.id, check, res.removed, identity)
        except LockError as exc:
            return _refusal(_locks_text(exc, db), check)
    if plan.status == "applied":
        res.message = f"Merged {_items(len(res.moved))} of {check.merged_where} into {check.kept_where}"
        if res.removed:
            res.message += "; the emptied folder was removed."
        elif res.left:
            res.message += (f"; left in {Path(check.merged_folder).name}: "
                            + ", ".join(name for name, _why in res.left[:6]) + (" ..." if len(res.left) > 6 else "")
                            + " (the folder kept has the same names - compare them yourself).")
        else:
            res.message += "; the folder is not empty, so it was left."
        res.message += f" {UNDO_HINT}"
        _log.info("Merge: plan %d moved %d item(s) of %s into %s (folder removed: %s, left: %d)", plan.id,
                  len(res.moved), check.merged_folder, check.kept_folder, res.removed, len(res.left))
        return res
    failed = next((s for s in plan.steps if s.state == "failed"), None)
    refused = refusal_text(failed)
    why = refused or (f"{os.path.basename(failed.src)}: {failed.error}" if failed is not None
                      else f"the plan is {plan.status}")
    _log.warning("Merge: plan %d %s after %d of %d items: %s", plan.id, plan.status, len(res.moved), len(plan.steps), why)
    if res.moved:
        res.status = "partly"
        res.message = f"Merged {len(res.moved)} of {_items(len(plan.steps))}; then: {why} {UNDO_HINT}"
    else:
        res.status = "failed"
        res.message = why if refused else f"Merge: nothing moved - {why}"
    return res


def _remove_if_empty(folder: str) -> bool:
    """Remove *folder* when it is truly empty (``rmdir`` cannot remove anything with content); False when it stays."""
    try:
        os.rmdir(folder)
    except OSError:
        return False
    _log.info("Merge: removed the emptied folder %s", folder)
    return True


def _remember(db, plan_id: int, check: MergeCheck, removed: bool, identity: Optional[dict]) -> None:
    value = {"merged_folder": check.merged_folder, "kept_folder": check.kept_folder, "merged_where": check.merged_where,
             "kept_where": check.kept_where, "kept_series_id": check.kept_series_id, "removed": removed,
             "identity": identity}
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
        _log.info("Merge: plan %d undone (%d item(s) back)", plan_id, len(back))
    else:
        stuck = next((s for s in after.steps if s.state == "done" and s.error), None)
        res.status = "partly"
        res.message = (f"Moved {len(back)} of {_items(len(done))} back; "
                       f"{os.path.basename(stuck.dst) if stuck else 'one'}: {stuck.error if stuck else after.status}")
        _log.warning("Merge: plan %d not fully undone: %s", plan_id, res.message)
    return res


__all__ = ["MERGE_REASON", "UNDO_HINT", "EXDEV_TEXT", "REFUSED_TEXT", "MergeCheck", "MergeResult", "MergeRecord",
           "check_merge", "confirm_text", "merge_folders", "last_merge", "undo_merge", "refusal_text"]
