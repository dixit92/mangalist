"""Cross-root journal plans (Move to library): one rename per folder from one library root into another, both roots
locked in a fixed order, no-replace, the rename tried and EXDEV failing the step (never copy + delete), crash recovery and undo. Made-up names only."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from mangalist.store import LOCK_NAME, Journal, LockBusy, PlanStateError, RootLock, StepRefused
from mangalist.store import journal as journal_mod
from mangalist.store.journal import ACROSS_FILESYSTEMS, PLAN_ROOTS_META, MoveFollower

from .conftest import make_archive


def _tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def journal(db):
    return Journal(db)


@pytest.fixture
def roots(library, tmp_path):
    """Two library roots: "Ongoing" holding two series, "Concluded" holding one."""
    ongoing = library
    concluded = tmp_path / "library" / "Concluded"
    make_archive(ongoing / "Quiet Harbor" / "Quiet Harbor v01.cbz", 11)
    make_archive(ongoing / "Quiet Harbor" / "Season 2" / "Quiet Harbor v05.cbz", 15)
    make_archive(ongoing / "Lantern Road" / "c001.cbz", 13)
    make_archive(concluded / "Paper Moths" / "Paper Moths v01.cbz", 17)
    return ongoing, concluded


def _all(*roots) -> dict:
    return {str(r): _tree(r) for r in roots}


def test_a_folder_moves_to_another_root_in_one_rename_and_back(journal, roots, db):
    ongoing, concluded = roots
    before = _all(ongoing, concluded)
    plan = journal.plan_across_roots("move to library", [(ongoing / "Quiet Harbor", concluded / "Quiet Harbor")],
                                     [ongoing, concluded])
    assert [(s.op, s.is_dir, s.state) for s in plan.steps] == [("move", True, "planned")]   # ONE step, no per-file ones
    assert journal.plan_roots(plan) == sorted([str(ongoing), str(concluded)], key=os.path.normcase)
    assert json.loads(db.get_meta(f"{PLAN_ROOTS_META}{plan.id}")) == journal.plan_roots(plan)
    assert _all(ongoing, concluded) == before                                     # planning touches nothing

    plan = journal.apply(plan.id)
    assert plan.status == "applied" and plan.steps[0].created_dirs == []
    assert not (ongoing / "Quiet Harbor").exists()
    assert _tree(concluded / "Quiet Harbor") == {"Quiet Harbor v01.cbz": b"x" * 11,
                                                  "Season 2/Quiet Harbor v05.cbz": b"x" * 15}
    assert not (ongoing / LOCK_NAME).exists() and not (concluded / LOCK_NAME).exists()   # held only while applying

    plan = journal.undo(plan.id)
    assert plan.status == "undone" and _all(ongoing, concluded) == before


def test_both_roots_are_locked_in_a_fixed_order(db, roots):
    ongoing, concluded = roots
    order = []

    class Recording(RootLock):
        def acquire(self, force=False):
            order.append(os.path.basename(self.root))
            return super().acquire(force)

    journal = Journal(db, lock_factory=Recording)
    plan = journal.plan_across_roots("m", [(concluded / "Paper Moths", ongoing / "Paper Moths"),
                                           (ongoing / "Lantern Road", concluded / "Lantern Road")], [concluded, ongoing])
    journal.apply(plan.id)
    journal.undo(plan.id)
    expected = [os.path.basename(p) for p in sorted([str(ongoing), str(concluded)], key=os.path.normcase)]
    assert order == expected * 2                          # the same order for apply and undo, whatever the steps' order


def test_a_busy_root_refuses_the_whole_plan_and_releases_the_other(journal, roots):
    ongoing, concluded = roots
    plan = journal.plan_across_roots("m", [(ongoing / "Lantern Road", concluded / "Lantern Road")], [ongoing, concluded])
    other = RootLock(concluded, host="another-instance")
    other.acquire()
    with pytest.raises(LockBusy):
        journal.apply(plan.id)
    assert journal.get_plan(plan.id).status == "planned" and (ongoing / "Lantern Road").is_dir()
    assert not (ongoing / LOCK_NAME).exists()                   # the lock taken first was released again
    other.release()
    with journal.lock_roots([concluded, ongoing]) as locks:     # held locks can be handed in (both of them)
        assert journal.apply(plan.id, lock=locks).status == "applied"
    with pytest.raises(PlanStateError):
        with RootLock(ongoing) as only_one:
            journal.undo(plan.id, lock=[only_one, RootLock(concluded)])     # not held


def test_refused_cross_root_plans(journal, roots, tmp_path):
    ongoing, concluded = roots
    make_archive(concluded / "Lantern Road" / "other.cbz")                       # the target name exists
    with pytest.raises(StepRefused, match="destination exists"):
        journal.plan_across_roots("m", [(ongoing / "Lantern Road", concluded / "Lantern Road")], [ongoing, concluded])
    with pytest.raises(StepRefused, match="not a folder"):
        journal.plan_across_roots("m", [(ongoing / "Lantern Road" / "c001.cbz", concluded / "c001.cbz")],
                                  [ongoing, concluded])
    with pytest.raises(StepRefused, match="not a folder"):
        journal.plan_across_roots("m", [(ongoing / "Gone Series", concluded / "Gone Series")], [ongoing, concluded])
    with pytest.raises(StepRefused, match="does not exist"):
        journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "New" / "Quiet Harbor")],
                                  [ongoing, concluded])
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(StepRefused, match="not inside one of the plan's roots"):
        journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", outside / "Quiet Harbor")], [ongoing, concluded])
    with pytest.raises(StepRefused, match="two library roots"):
        journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", ongoing / "Other")], [ongoing])
    with pytest.raises(StepRefused, match="overlap"):
        journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "Quiet Harbor")],
                                  [ongoing, ongoing / "Lantern Road"])
    with pytest.raises(StepRefused, match="listed twice"):
        journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "A"),
                                        (ongoing / "Quiet Harbor" / "Season 2", concluded / "B")], [ongoing, concluded])
    with pytest.raises(StepRefused):
        journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "Bad name.")], [ongoing, concluded])
    assert journal.list_plans() == []                                           # nothing recorded


def test_the_two_sides_devices_are_not_compared_when_planning_the_rename_is_tried(journal, roots):
    """Owner, 2026-10-15: try the move; only the system's refusal stops it (no up-front "different devices" refusal)."""
    ongoing, concluded = roots
    assert not hasattr(journal_mod, "_device")
    plan = journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "Quiet Harbor")], [ongoing, concluded])
    plan = journal.apply(plan.id)
    assert plan.status == "applied" and (concluded / "Quiet Harbor").is_dir()


def test_any_other_refusal_of_the_rename_fails_the_step_and_moves_nothing(journal, roots, monkeypatch):
    ongoing, concluded = roots
    before = _all(ongoing, concluded)

    def refuse(src, dst):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(journal_mod, "rename_noreplace", refuse)
    plan = journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "Quiet Harbor")], [ongoing, concluded])
    plan = journal.apply(plan.id)
    assert plan.status == "failed" and plan.steps[0].error.startswith("rename failed: ")
    assert _all(ongoing, concluded) == before


def test_exdev_from_the_rename_fails_the_step_and_copies_nothing(journal, roots, monkeypatch):
    """One FUSE mount (one device) whose shares still cannot rename between them (Unraid's /mnt/user)."""
    ongoing, concluded = roots
    before = _all(ongoing, concluded)

    def exdev(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(journal_mod, "rename_noreplace", exdev)
    plan = journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "Quiet Harbor")], [ongoing, concluded])
    plan = journal.apply(plan.id)
    assert plan.status == "failed" and plan.steps[0].state == "failed" and plan.steps[0].error == ACROSS_FILESYSTEMS
    assert _all(ongoing, concluded) == before                                     # nothing copied, nothing deleted
    assert not (ongoing / LOCK_NAME).exists() and not (concluded / LOCK_NAME).exists()


def test_recovery_of_an_interrupted_cross_root_step(journal, roots, monkeypatch):
    ongoing, concluded = roots
    plan = journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "Quiet Harbor"),
                                           (ongoing / "Lantern Road", concluded / "Lantern Road")], [ongoing, concluded])
    real = journal_mod.Journal._set_step

    def crashing(self, step, state, created_dirs=None, error=...):
        if state == "done":
            raise KeyboardInterrupt("power cut right after the rename")
        return real(self, step, state, created_dirs, error)

    monkeypatch.setattr(journal_mod.Journal, "_set_step", crashing)
    with pytest.raises(KeyboardInterrupt):
        journal.apply(plan.id)
    monkeypatch.setattr(journal_mod.Journal, "_set_step", real)
    assert [s.state for s in journal.get_plan(plan.id).steps] == ["intent", "planned"]

    assert [p.id for p in journal.recover()] == [plan.id]
    plan = journal.get_plan(plan.id)
    assert plan.status == "interrupted" and [s.state for s in plan.steps] == ["done", "planned"]
    plan = journal.apply(plan.id)                                                  # resume
    assert plan.status == "applied" and (concluded / "Lantern Road" / "c001.cbz").is_file()
    plan = journal.undo(plan.id)                                                   # and roll the whole move back
    assert plan.status == "undone" and (ongoing / "Quiet Harbor").is_dir() and (ongoing / "Lantern Road").is_dir()


def test_files_filed_before_a_folder_move_are_followed_into_the_new_folder(journal, roots):
    """The arrivals' checks that a filed volume is still in the library follow a moved folder too."""
    ongoing, concluded = roots
    filed = ongoing / "Quiet Harbor" / "Season 2" / "Quiet Harbor v05.cbz"
    before = journal.plan_across_roots("m", [(ongoing / "Lantern Road", concluded / "Lantern Road")],
                                       [ongoing, concluded])
    journal.apply(before.id)
    plan = journal.plan_across_roots("m", [(ongoing / "Quiet Harbor", concluded / "Quiet Harbor")], [ongoing, concluded])
    journal.apply(plan.id)
    follower = MoveFollower(journal)
    assert follower.current_path(str(filed), before.id) == str(concluded / "Quiet Harbor" / "Season 2" /
                                                                "Quiet Harbor v05.cbz")
    assert follower.current_path(str(filed), plan.id) == str(filed)               # only moves after the given plan
    journal.undo(plan.id)
    assert MoveFollower(journal).current_path(str(filed), before.id) == str(filed)   # an undone move is not followed


def test_a_file_renamed_then_its_folder_renamed_in_the_same_plan_is_followed_through_both(journal, roots):
    """The renamer's plan: the files first (inside the old folder), then the series folder - a later step of the same
    plan is after the file's rename, so the follower carries on into the new folder."""
    ongoing, _ = roots
    filed = ongoing / "Quiet Harbor" / "Quiet Harbor v01.cbz"
    plan = journal.plan("rename to the scheme", [(filed, ongoing / "Quiet Harbor" / "Vol. 001.cbz"),
                                                 (ongoing / "Quiet Harbor", ongoing / "Quiet Harbor [Renamed]")],
                        root_path=ongoing)
    journal.apply(plan.id)
    renamed = ongoing / "Quiet Harbor [Renamed]" / "Vol. 001.cbz"
    assert renamed.is_file()
    assert MoveFollower(journal).current_path(str(filed), plan.id - 1) == str(renamed)
    assert MoveFollower(journal).current_path(str(filed), plan.id) == str(filed)   # the plan itself: not after it
