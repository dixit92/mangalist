"""The filesystem journal: every rename / move MangaList makes in a library goes through a plan here.

Guarantees:

- **Moves only, never deletes.** A step is one ``rename`` on the same filesystem; a move that would cross
  filesystems is refused (no copy + delete), and a step never replaces an existing path (no-replace
  rename where the OS offers it, an existence check otherwise). The only removals are of EMPTY folders
  the plan itself created, when it is undone (``rmdir`` cannot remove anything that has content).
- **Write-ahead.** Before a step touches the disk its record is set to ``intent`` (durably committed),
  after it to ``done``; undo uses ``undo_intent`` -> ``undone``.
- **Crash recovery.** :meth:`Journal.recover` (run on start) looks at every plan left ``applying`` /
  ``undoing`` and settles each ``intent`` / ``undo_intent`` step from what is on disk; the plan becomes
  ``interrupted``, ready to resume (:meth:`apply`) or roll back (:meth:`undo`).
- **Undo of a whole plan**, steps in reverse order.
- **Windows-safe names** for every name a step creates.
- **Never a content change and a path change in one step** (MangaPixer keeps an archive's identity
  across a rename only when its content is unchanged): a step that declares a content change together
  with a new path is refused, and a file whose size / modification time (or content signature, with
  ``verify="hash"``) changed since the plan was made is not moved - re-plan after MangaPixer has seen
  the change.
- **One writer.** A plan with a ``root_path`` takes that root's ``.mangalist.lock`` while it applies or
  undoes, and every step must lie inside that root. Other tools writing into the library meanwhile is
  normal: a path they took simply makes that step fail (nothing is overwritten).
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .db import utcnow
from .lock import RootLock, pid_alive, this_host
from .names import windows_name_problem

_log = logging.getLogger(__name__)

SIGNATURE_CHUNK = 64 * 1024


class JournalError(Exception):
    pass


class StepRefused(JournalError):
    """A step that the journal will not plan."""


class ContentAndPathChange(StepRefused):
    """A step would change an archive's content and its path at once."""


class PlanStateError(JournalError):
    pass


@dataclass
class Move:
    src: Union[str, os.PathLike]
    dst: Union[str, os.PathLike]
    changes_content: bool = False


@dataclass
class Step:
    id: int
    plan_id: int
    seq: int
    op: str
    src: str
    dst: str
    is_dir: bool
    src_size: Optional[int]
    src_signature: Optional[str]
    created_dirs: List[str]
    state: str
    error: Optional[str]


@dataclass
class Plan:
    id: int
    reason: str
    root_path: Optional[str]
    status: str
    created_at: str
    updated_at: str
    note: Optional[str] = None
    steps: List[Step] = field(default_factory=list)

    @property
    def done_steps(self) -> List[Step]:
        return [s for s in self.steps if s.state == "done"]


# --- filesystem helpers --------------------------------------------------------------------------------


def _lexical(p) -> str:
    """*p* made absolute without asking the OS: unlike ``abspath`` on Windows, it keeps a name's
    trailing dots and spaces."""
    s = os.fspath(p)
    if not os.path.isabs(s):
        s = os.path.join(os.getcwd(), s)
    return os.path.normpath(s)


def _norm(p) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(p)))


def _inside(path: str, folder: str) -> bool:
    a, b = os.path.normcase(path), os.path.normcase(folder)
    return a.startswith(b.rstrip(os.sep) + os.sep)


def exists_exact(path: str) -> bool:
    """True when *path* exists with exactly this name (a case-insensitive filesystem answers ``exists``
    for any spelling; a case-only rename must see the old spelling as gone)."""
    if not os.path.lexists(path):
        return False
    parent, name = os.path.split(path)
    try:
        return name in os.listdir(parent or ".")
    except OSError:
        return True


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _is_case_only(src: str, dst: str) -> bool:
    return src != dst and src.lower() == dst.lower() and _same_file(src, dst)


def content_signature(path: str, mode: str = "stat") -> str:
    """``stat:<size>:<mtime_ns>`` (cheap, the default) or ``v1:<size>:<sha256 of size + first and last
    64 KiB>`` (MangaPixer's content signature, ``mode="hash"``)."""
    st = os.stat(path)
    if mode == "stat":
        return f"stat:{st.st_size}:{st.st_mtime_ns}"
    h = hashlib.sha256(str(st.st_size).encode("ascii"))
    with open(path, "rb") as f:
        h.update(f.read(SIGNATURE_CHUNK))
        if st.st_size > SIGNATURE_CHUNK:
            f.seek(max(SIGNATURE_CHUNK, st.st_size - SIGNATURE_CHUNK))
            h.update(f.read(SIGNATURE_CHUNK))
    return f"v1:{st.st_size}:{h.hexdigest()}"


_RENAME_NOREPLACE = 1
_AT_FDCWD = -100
_renameat2 = None
if hasattr(os, "uname") and os.uname().sysname == "Linux":  # pragma: no branch
    try:
        _libc = ctypes.CDLL(None, use_errno=True)
        _renameat2 = getattr(_libc, "renameat2", None)
    except OSError:  # pragma: no cover
        _renameat2 = None


def rename_noreplace(src: str, dst: str) -> None:
    """Rename *src* to *dst*, never replacing an existing *dst* (``FileExistsError``); refuses to cross
    filesystems (``OSError(EXDEV)``) - the caller never falls back to copy + delete."""
    if _is_case_only(src, dst) or os.name == "nt":
        os.rename(src, dst)  # Windows' rename never replaces
        return
    if _renameat2 is not None:
        rc = _renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE)
        if rc == 0:
            return
        err = ctypes.get_errno()
        if err not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise OSError(err, os.strerror(err), src, None, dst)
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "destination exists", dst)
    os.rename(src, dst)


# --- the journal ---------------------------------------------------------------------------------------


def _default_on_moved(store) -> Callable[[str, str, bool], None]:
    def on_moved(src: str, dst: str, is_dir: bool) -> None:
        if is_dir:
            store.relink_folder(src, dst)
    return on_moved


class Journal:
    """Plans of moves recorded in the library database (``journal_plans`` / ``journal_steps``)."""

    def __init__(self, store, *, on_moved: Optional[Callable[[str, str, bool], None]] = None,
                 lock_factory: Callable[[str], RootLock] = RootLock):
        self.store = store
        self.on_moved = on_moved if on_moved is not None else _default_on_moved(store)
        self.lock_factory = lock_factory

    # --- reading -----------------------------------------------------------------------------------

    def get_plan(self, plan_id: int) -> Plan:
        with self.store.connect() as con:
            p = con.execute("SELECT * FROM journal_plans WHERE id = ?", (plan_id,)).fetchone()
            if p is None:
                raise PlanStateError(f"no plan {plan_id}")
            steps = con.execute("SELECT * FROM journal_steps WHERE plan_id = ? ORDER BY seq", (plan_id,)).fetchall()
        return Plan(id=p["id"], reason=p["reason"], root_path=p["root_path"], status=p["status"],
                    created_at=p["created_at"], updated_at=p["updated_at"], note=p["note"],
                    steps=[Step(id=s["id"], plan_id=s["plan_id"], seq=s["seq"], op=s["op"], src=s["src"],
                                dst=s["dst"], is_dir=bool(s["is_dir"]), src_size=s["src_size"],
                                src_signature=s["src_signature"], created_dirs=json.loads(s["created_dirs"] or "[]"),
                                state=s["state"], error=s["error"]) for s in steps])

    def list_plans(self, status: Optional[Sequence[str]] = None) -> List[Plan]:
        with self.store.connect() as con:
            if status:
                ids = [r[0] for r in con.execute(
                    f"SELECT id FROM journal_plans WHERE status IN ({','.join('?' * len(status))}) ORDER BY id",
                    list(status))]
            else:
                ids = [r[0] for r in con.execute("SELECT id FROM journal_plans ORDER BY id")]
        return [self.get_plan(i) for i in ids]

    # --- planning ----------------------------------------------------------------------------------

    def plan(self, reason: str, moves: Iterable[Union[Move, Tuple]], root_path=None, note: Optional[str] = None,
             verify: str = "stat") -> Plan:
        """Check *moves* (in order, each seeing the effect of the ones before it) and record them as a
        plan. Raises :class:`StepRefused` (nothing recorded) for the first step that is not allowed."""
        if verify not in ("stat", "hash"):
            raise ValueError("verify must be 'stat' or 'hash'")
        root = _norm(root_path) if root_path is not None else None
        sim = _Simulation()
        rows = []
        for i, mv in enumerate(moves):
            if not isinstance(mv, Move):
                mv = Move(*mv)
            src, dst = _norm(mv.src), _norm(mv.dst)
            where = f"step {i + 1} ({src} -> {dst})"
            if mv.changes_content:
                if src != dst:
                    raise ContentAndPathChange(f"{where}: a content change and a path change in one step "
                                               "(MangaPixer would lose the archive's identity); split them")
                raise StepRefused(f"{where}: content changes are not journal moves")
            if src == dst:
                raise StepRefused(f"{where}: source and destination are the same")
            if root is not None:
                for p in (src, dst):
                    if not _inside(p, root):
                        raise StepRefused(f"{where}: {p} is outside the plan's root {root}")
            if _inside(dst, src):
                raise StepRefused(f"{where}: cannot move a folder into itself")
            on_disk_src = sim.disk_path(src)
            if on_disk_src is None or not exists_exact(on_disk_src):
                raise StepRefused(f"{where}: the source does not exist")
            case_only = _is_case_only(on_disk_src, sim.disk_path(dst) or dst)
            if sim.exists(dst) and not case_only:
                raise StepRefused(f"{where}: the destination exists (a move never replaces anything)")
            # Every name this step creates must be valid on Windows. Count the new components on the
            # normalized path, but read their names from the lexical one: Windows' abspath strips trailing
            # dots and spaces, which would hide exactly the names this check must refuse.
            count = 1
            parent = os.path.dirname(dst)
            while parent and not sim.exists(parent) and os.path.dirname(parent) != parent:
                count += 1
                parent = os.path.dirname(parent)
            new_parts = []
            lex = _lexical(mv.dst)
            for _ in range(count):
                new_parts.append(os.path.basename(lex))
                lex = os.path.dirname(lex)
            for part in new_parts:
                problem = windows_name_problem(part)
                if problem:
                    raise StepRefused(f"{where}: {part!r}: {problem}")
            is_dir = os.path.isdir(on_disk_src) and not os.path.islink(on_disk_src)
            size = sig = None
            if not is_dir:
                size = os.stat(on_disk_src).st_size
                sig = content_signature(on_disk_src, verify)
            sim.move(src, dst)
            rows.append((i, src, dst, is_dir, size, sig))
        if not rows:
            raise StepRefused("a plan needs at least one move")
        now = utcnow()
        with self.store.connect(durable=True) as con:
            cur = con.execute("INSERT INTO journal_plans (created_at, updated_at, reason, root_path, status, note, host,"
                              " pid) VALUES (?,?,?,?, 'planned', ?,?,?)",
                              (now, now, reason, root, note, this_host(), os.getpid()))
            plan_id = int(cur.lastrowid)
            con.executemany("INSERT INTO journal_steps (plan_id, seq, op, src, dst, is_dir, src_size, src_signature,"
                            " state, updated_at) VALUES (?,?, 'move', ?,?,?,?,?, 'planned', ?)",
                            [(plan_id, i, s, d, 1 if isd else 0, sz, sg, now) for i, s, d, isd, sz, sg in rows])
        return self.get_plan(plan_id)

    # --- applying ----------------------------------------------------------------------------------

    def apply(self, plan_id: int, lock: Optional[RootLock] = None) -> Plan:
        """Apply (or resume) a plan. Stops at the first step that cannot be done; the plan is then
        ``failed`` and can be undone, or applied again once the cause is fixed."""
        plan = self.get_plan(plan_id)
        if plan.status not in ("planned", "failed", "interrupted"):
            raise PlanStateError(f"plan {plan_id} is {plan.status}; only a planned, failed or interrupted plan applies")
        with self._locked(plan, lock) as held:
            self._set_plan(plan_id, "applying")
            for step in plan.steps:
                if step.state == "done":
                    continue
                if step.state not in ("planned", "failed"):
                    self._set_plan(plan_id, "failed")
                    raise PlanStateError(f"step {step.seq + 1} is {step.state}; run recover() first")
                error = self._apply_step(step, held)
                if error:
                    _log.warning("Plan %d step %d not applied: %s", plan_id, step.seq + 1, error)
                    self._set_plan(plan_id, "failed")
                    return self.get_plan(plan_id)
            self._set_plan(plan_id, "applied")
        return self.get_plan(plan_id)

    def _apply_step(self, step: Step, held: Optional[RootLock]) -> Optional[str]:
        src, dst = step.src, step.dst
        if held is not None:
            try:
                held.check()
            except Exception as exc:  # noqa: BLE001 - LockLost
                return self._fail(step, f"root lock lost: {exc}")
        if not exists_exact(src):
            return self._fail(step, "the source is gone")
        case_only = _is_case_only(src, dst)
        if exists_exact(dst) and not case_only:
            return self._fail(step, "the destination exists now (another tool?); nothing replaced")
        if not step.is_dir:
            try:
                sig = content_signature(src, "hash" if (step.src_signature or "").startswith("v1:") else "stat")
            except OSError as exc:
                return self._fail(step, f"cannot read the source: {exc}")
            if sig != step.src_signature:
                return self._fail(step, "the file changed since the plan was made; a content change and a "
                                        "rename must not reach MangaPixer as one step - re-plan later")
        elif not os.path.isdir(src):
            return self._fail(step, "the source is no longer a folder")
        missing: List[str] = []
        parent = os.path.dirname(dst)
        while parent and not os.path.isdir(parent):
            missing.append(parent)
            parent = os.path.dirname(parent)
        missing.reverse()
        self._set_step(step, "intent", created_dirs=missing)
        made: List[str] = []
        try:
            for d in missing:
                os.mkdir(d)
                made.append(d)
            rename_noreplace(src, dst)
        except OSError as exc:
            for d in reversed(made):
                _rmdir_if_empty(d)
            if exc.errno == errno.EXDEV:
                return self._fail(step, "the destination is on another filesystem; MangaList never copies + deletes")
            if isinstance(exc, FileExistsError):
                return self._fail(step, "the destination appeared meanwhile; nothing replaced")
            return self._fail(step, f"rename failed: {exc}")
        self._set_step(step, "done", created_dirs=missing)
        self._notify(src, dst, step.is_dir)
        return None

    # --- undo --------------------------------------------------------------------------------------

    def undo(self, plan_id: int, lock: Optional[RootLock] = None) -> Plan:
        """Move every done step back, last first, and remove the (empty) folders the plan created."""
        plan = self.get_plan(plan_id)
        if plan.status not in ("applied", "failed", "interrupted", "undo_failed"):
            raise PlanStateError(f"plan {plan_id} is {plan.status}; nothing to undo")
        with self._locked(plan, lock) as held:
            self._set_plan(plan_id, "undoing")
            for step in reversed(plan.steps):
                if step.state != "done":
                    continue
                error = self._undo_step(step, held)
                if error:
                    _log.warning("Plan %d step %d not undone: %s", plan_id, step.seq + 1, error)
                    self._set_plan(plan_id, "undo_failed")
                    return self.get_plan(plan_id)
            self._set_plan(plan_id, "undone")
        return self.get_plan(plan_id)

    def _undo_step(self, step: Step, held: Optional[RootLock]) -> Optional[str]:
        src, dst = step.src, step.dst
        if held is not None:
            try:
                held.check()
            except Exception as exc:  # noqa: BLE001
                return self._note(step, f"root lock lost: {exc}")
        if not exists_exact(dst):
            return self._note(step, "the moved item is gone from its new path")
        case_only = _is_case_only(dst, src)
        if exists_exact(src) and not case_only:
            return self._note(step, "something now exists at the original path; nothing replaced")
        self._set_step(step, "undo_intent")
        try:
            rename_noreplace(dst, src)
        except OSError as exc:
            self._set_step(step, "done", error=f"undo failed: {exc}")
            return f"undo failed: {exc}"
        self._set_step(step, "undone")
        for d in reversed(step.created_dirs):
            _rmdir_if_empty(d)
        self._notify(dst, src, step.is_dir)
        return None

    # --- recovery ----------------------------------------------------------------------------------

    def recover(self) -> List[Plan]:
        """Settle plans a crash left ``applying`` / ``undoing`` (not those of a live process on this host)."""
        recovered = []
        host = this_host()
        with self.store.connect() as con:
            rows = con.execute("SELECT id, host, pid FROM journal_plans WHERE status IN ('applying', 'undoing')"
                               " ORDER BY id").fetchall()
        for r in rows:
            if (r["host"] or "").lower() == host.lower() and r["pid"] != os.getpid() and pid_alive(r["pid"] or 0):
                continue  # another live process on this machine is working on it
            plan = self.get_plan(r["id"])
            for step in plan.steps:
                src_there, dst_there = exists_exact(step.src), exists_exact(step.dst)
                if step.state == "intent":
                    if src_there and not dst_there:
                        for d in reversed(step.created_dirs):
                            _rmdir_if_empty(d)
                        self._set_step(step, "planned", error=None)
                    elif dst_there and not src_there:
                        self._set_step(step, "done", error=None)
                        self._notify(step.src, step.dst, step.is_dir)
                    else:
                        self._set_step(step, "failed", error="after a crash: source and destination both "
                                       f"{'exist' if src_there else 'missing'}; left as is")
                elif step.state == "undo_intent":
                    if dst_there and not src_there:
                        self._set_step(step, "done", error=None)
                    elif src_there and not dst_there:
                        self._set_step(step, "undone", error=None)
                        for d in reversed(step.created_dirs):
                            _rmdir_if_empty(d)
                        self._notify(step.dst, step.src, step.is_dir)
                    else:
                        self._set_step(step, "done", error="after a crash while undoing: source and destination "
                                       f"both {'exist' if src_there else 'missing'}; left as is")
            self._set_plan(plan.id, "interrupted")
            _log.warning("Journal plan %d (%s) was interrupted; it can be resumed or undone", plan.id, plan.reason)
            recovered.append(self.get_plan(plan.id))
        return recovered

    # --- internals ---------------------------------------------------------------------------------

    def _locked(self, plan: Plan, lock: Optional[RootLock]):
        journal = self

        class _Ctx:
            def __enter__(self_inner):
                self_inner.own = None
                if plan.root_path is None:
                    return lock if lock is not None and lock.held else None
                if lock is not None:
                    if not lock.held or _norm(lock.root) != plan.root_path:
                        raise PlanStateError(f"the lock given is not a held lock of {plan.root_path}")
                    return lock
                self_inner.own = journal.lock_factory(plan.root_path)
                self_inner.own.acquire()
                self_inner.own.start_heartbeat()
                return self_inner.own

            def __exit__(self_inner, *exc):
                if self_inner.own is not None:
                    self_inner.own.release()

        return _Ctx()

    def _notify(self, src: str, dst: str, is_dir: bool) -> None:
        try:
            self.on_moved(src, dst, is_dir)
        except Exception:  # noqa: BLE001 - the move itself is done and recorded
            _log.warning("After moving %s -> %s: updating the database failed", src, dst, exc_info=True)

    def _fail(self, step: Step, error: str) -> str:
        self._set_step(step, "failed", error=error)
        return error

    def _note(self, step: Step, error: str) -> str:
        self._set_step(step, step.state, error=error)
        return error

    def _set_step(self, step: Step, state: str, created_dirs: Optional[List[str]] = None, error=...) -> None:
        sets, args = ["state = ?", "updated_at = ?"], [state, utcnow()]
        if created_dirs is not None:
            sets.append("created_dirs = ?")
            args.append(json.dumps(created_dirs))
            step.created_dirs = list(created_dirs)
        if error is not ...:
            sets.append("error = ?")
            args.append(error)
            step.error = error
        elif state in ("done", "undone", "intent", "undo_intent"):
            sets.append("error = NULL")
            step.error = None
        with self.store.connect(durable=True) as con:
            con.execute(f"UPDATE journal_steps SET {', '.join(sets)} WHERE id = ?", (*args, step.id))
        step.state = state

    def _set_plan(self, plan_id: int, status: str) -> None:
        with self.store.connect(durable=True) as con:
            con.execute("UPDATE journal_plans SET status = ?, updated_at = ?, host = ?, pid = ? WHERE id = ?",
                        (status, utcnow(), this_host(), os.getpid(), plan_id))


def _rmdir_if_empty(path: str) -> None:
    try:
        os.rmdir(path)  # fails on a folder with anything in it: never removes content
    except OSError:
        pass


class _Simulation:
    """The filesystem as the steps planned so far will leave it (for checking later steps)."""

    def __init__(self) -> None:
        self.moves: List[Tuple[str, str]] = []
        self.created: Dict[str, bool] = {}

    def disk_path(self, path: str) -> Optional[str]:
        """Where *path* (as it will be) is on disk now, or None when it will not exist."""
        p = path
        for src, dst in reversed(self.moves):
            if os.path.normcase(p) == os.path.normcase(dst) or _inside(p, dst):
                p = src + p[len(dst):]
            elif os.path.normcase(p) == os.path.normcase(src) or _inside(p, src):
                return None
        return p

    def exists(self, path: str) -> bool:
        if self.created.get(os.path.normcase(path)):
            return True
        on_disk = self.disk_path(path)
        if on_disk is None:
            return False
        return exists_exact(on_disk)

    def move(self, src: str, dst: str) -> None:
        self.moves.append((src, dst))
        parent = os.path.dirname(dst)
        while parent and not self.exists(parent) and os.path.dirname(parent) != parent:
            self.created[os.path.normcase(parent)] = True
            parent = os.path.dirname(parent)
