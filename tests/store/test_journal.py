"""The journal: write-ahead moves, crash recovery, undo, Windows-safe names, never deletes, never a
content change together with a rename."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from manga_list import mu_cache
from manga_list.store import (LOCK_NAME, ContentAndPathChange, Journal, LockBusy, Move, PlanStateError,
                              RootLock, SeriesSeen, StepRefused)
from manga_list.store import journal as journal_mod

from .conftest import make_archive


def _tree(root: Path) -> dict:
    """Every file under *root* with its bytes (what must survive any plan)."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def journal(db):
    return Journal(db)


@pytest.fixture
def lib(library):
    make_archive(library / "Series A" / "Series A v01.cbz", 11)
    make_archive(library / "Series A" / "Series A v02.cbz", 12)
    make_archive(library / "Series B" / "c001.cbz", 13)
    return library


def test_plan_apply_undo_round_trip(journal, lib):
    before = _tree(lib)
    plan = journal.plan("enforcement", [
        (lib / "Series A" / "Series A v01.cbz", lib / "Series A" / "Series A v01 (Digital).cbz"),
        (lib / "Series B" / "c001.cbz", lib / "Series B" / "Chapters" / "Series B c001.cbz"),
    ], root_path=lib)
    assert plan.status == "planned" and [s.state for s in plan.steps] == ["planned", "planned"]
    assert _tree(lib) == before                                      # planning touches nothing

    plan = journal.apply(plan.id)
    assert plan.status == "applied" and [s.state for s in plan.steps] == ["done", "done"]
    assert (lib / "Series B" / "Chapters" / "Series B c001.cbz").read_bytes() == before["Series B/c001.cbz"]
    assert plan.steps[1].created_dirs == [str(lib / "Series B" / "Chapters")]
    assert not (lib / LOCK_NAME).exists()                            # lock held only while applying

    plan = journal.undo(plan.id)
    assert plan.status == "undone" and [s.state for s in plan.steps] == ["undone", "undone"]
    assert _tree(lib) == before
    assert not (lib / "Series B" / "Chapters").exists()              # the empty folder it made is gone


def test_steps_see_earlier_steps(journal, lib):
    """Rename a series folder, then a file inside it by its new path."""
    plan = journal.plan("import", [
        (lib / "Series A", lib / "Series A (2020)"),
        (lib / "Series A (2020)" / "Series A v01.cbz", lib / "Series A (2020)" / "Series A (2020) v01.cbz"),
    ], root_path=lib)
    journal.apply(plan.id)
    assert (lib / "Series A (2020)" / "Series A (2020) v01.cbz").is_file()
    journal.undo(plan.id)
    assert (lib / "Series A" / "Series A v01.cbz").is_file() and not (lib / "Series A (2020)").exists()
    with pytest.raises(StepRefused, match="does not exist"):
        journal.plan("x", [(lib / "Series A", lib / "S"), (lib / "Series A" / "Series A v01.cbz", lib / "y.cbz")])


@pytest.mark.parametrize("dst, message", [
    ("Series A/Bad: name.cbz", "not allowed on Windows"),
    ("Series A/CON.cbz", "reserved"),
    ("Series A/trailing dot./x.cbz", "dot or a space"),
    ("Series B/c001.cbz", "destination exists"),
])
def test_refused_destinations(journal, lib, dst, message):
    with pytest.raises(StepRefused, match=message):
        journal.plan("x", [(lib / "Series A" / "Series A v01.cbz", lib / dst)], root_path=lib)
    assert journal.list_plans() == []


def test_refused_shapes(journal, lib, tmp_path):
    src = lib / "Series A" / "Series A v01.cbz"
    with pytest.raises(ContentAndPathChange):
        journal.plan("x", [Move(src, lib / "Series A" / "new.cbz", changes_content=True)])
    with pytest.raises(StepRefused):
        journal.plan("x", [Move(src, src, changes_content=True)])
    with pytest.raises(StepRefused, match="outside"):
        journal.plan("x", [(src, tmp_path / "elsewhere.cbz")], root_path=lib)
    with pytest.raises(StepRefused, match="into itself"):
        journal.plan("x", [(lib / "Series A", lib / "Series A" / "inner")])
    with pytest.raises(StepRefused, match="same"):
        journal.plan("x", [(src, src)])
    with pytest.raises(StepRefused, match="at least one"):
        journal.plan("x", [])


def test_a_file_changed_after_planning_is_not_renamed(journal, lib):
    src = lib / "Series A" / "Series A v01.cbz"
    plan = journal.plan("enforcement", [(src, lib / "Series A" / "renamed.cbz")], root_path=lib)
    src.write_bytes(b"ComicInfo written by another tool" * 3)        # content (size) changed
    plan = journal.apply(plan.id)
    assert plan.status == "failed" and "changed since the plan" in plan.steps[0].error
    assert src.is_file() and not (lib / "Series A" / "renamed.cbz").exists()


def test_hash_verification_catches_a_same_size_change(journal, lib):
    src = lib / "Series A" / "Series A v01.cbz"
    plan = journal.plan("x", [(src, lib / "Series A" / "r.cbz")], verify="hash")
    st = os.stat(src)
    src.write_bytes(b"y" * st.st_size)
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))               # same size, same mtime
    assert journal.apply(plan.id).status == "failed"


def test_a_destination_taken_by_another_tool_fails_without_replacing(journal, lib):
    src = lib / "Series A" / "Series A v01.cbz"
    dst = lib / "Series A" / "new name.cbz"
    plan = journal.plan("x", [(lib / "Series B" / "c001.cbz", lib / "Series B" / "c1.cbz"), (src, dst)])
    dst.write_bytes(b"arrived from FFS")
    plan = journal.apply(plan.id)
    assert plan.status == "failed" and [s.state for s in plan.steps] == ["done", "failed"]
    assert dst.read_bytes() == b"arrived from FFS" and src.is_file()
    # Undo puts back the step that was done.
    assert journal.undo(plan.id).status == "undone"
    assert (lib / "Series B" / "c001.cbz").is_file()


def test_rename_noreplace_never_overwrites(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    with pytest.raises(FileExistsError):
        journal_mod.rename_noreplace(str(a), str(b))
    assert a.read_bytes() == b"a" and b.read_bytes() == b"b"


def test_a_cross_filesystem_move_is_refused_not_copied(journal, lib, monkeypatch):
    import errno as _errno

    def exdev(src, dst):
        raise OSError(_errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(journal_mod, "rename_noreplace", exdev)
    src = lib / "Series A" / "Series A v01.cbz"
    plan = journal.plan("x", [(src, lib / "Other" / "x.cbz")])
    plan = journal.apply(plan.id)
    assert plan.status == "failed" and "another filesystem" in plan.steps[0].error
    assert src.is_file() and not (lib / "Other").exists()            # the folder it made is removed again


def _crash_after_rename(monkeypatch):
    """Simulate a crash right after the rename, before 'done' is recorded."""
    real = journal_mod.Journal._set_step

    def crashing(self, step, state, created_dirs=None, error=...):
        if state == "done":
            raise KeyboardInterrupt("power cut")
        return real(self, step, state, created_dirs, error)

    monkeypatch.setattr(journal_mod.Journal, "_set_step", crashing)
    return real


def test_recovery_after_a_crash_mid_step(journal, lib, monkeypatch):
    src, dst = lib / "Series A" / "Series A v01.cbz", lib / "Series A" / "moved.cbz"
    src2, dst2 = lib / "Series B" / "c001.cbz", lib / "Series B" / "moved.cbz"
    plan = journal.plan("x", [(src, dst), (src2, dst2)])
    real = _crash_after_rename(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        journal.apply(plan.id)
    monkeypatch.setattr(journal_mod.Journal, "_set_step", real)
    assert journal.get_plan(plan.id).status == "applying"
    assert [s.state for s in journal.get_plan(plan.id).steps] == ["intent", "planned"]

    recovered = journal.recover()
    assert [p.id for p in recovered] == [plan.id]
    plan = journal.get_plan(plan.id)
    assert plan.status == "interrupted" and [s.state for s in plan.steps] == ["done", "planned"]
    plan = journal.apply(plan.id)                                   # resume
    assert plan.status == "applied" and dst.is_file() and dst2.is_file()


def test_recovery_of_an_intent_that_never_reached_the_disk(journal, lib, monkeypatch):
    src = lib / "Series A" / "Series A v01.cbz"
    plan = journal.plan("x", [(src, lib / "Series A" / "New" / "moved.cbz")])

    def crash(*a, **k):
        raise KeyboardInterrupt("power cut before rename")

    monkeypatch.setattr(journal_mod, "rename_noreplace", crash)
    with pytest.raises(KeyboardInterrupt):
        journal.apply(plan.id)
    monkeypatch.undo()
    journal.recover()
    plan = journal.get_plan(plan.id)
    assert plan.status == "interrupted" and plan.steps[0].state == "planned"
    assert src.is_file() and not (lib / "Series A" / "New").exists()  # the folder made before the crash
    assert journal.undo(plan.id).status == "undone"                  # nothing to put back


def test_recovery_while_undoing_and_ambiguous_states(journal, lib, monkeypatch):
    src, dst = lib / "Series A" / "Series A v01.cbz", lib / "Series A" / "moved.cbz"
    plan = journal.apply(journal.plan("x", [(src, dst)]).id)
    real = journal_mod.Journal._set_step

    def crash_on_undone(self, step, state, created_dirs=None, error=...):
        if state == "undone":
            raise KeyboardInterrupt
        return real(self, step, state, created_dirs, error)

    monkeypatch.setattr(journal_mod.Journal, "_set_step", crash_on_undone)
    with pytest.raises(KeyboardInterrupt):
        journal.undo(plan.id)
    monkeypatch.setattr(journal_mod.Journal, "_set_step", real)
    journal.recover()
    plan = journal.get_plan(plan.id)
    assert plan.status == "interrupted" and plan.steps[0].state == "undone" and src.is_file()

    # Both paths present after a crash: left as is, reported.
    src2, dst2 = lib / "Series B" / "c001.cbz", lib / "Series B" / "c1.cbz"
    plan2 = journal.plan("y", [(src2, dst2)])
    monkeypatch.setattr(journal_mod.Journal, "_set_step", lambda *a, **k: real(*a, **k))
    with journal.store.connect() as con:
        con.execute("UPDATE journal_plans SET status='applying', pid=-1 WHERE id=?", (plan2.id,))
        con.execute("UPDATE journal_steps SET state='intent' WHERE plan_id=?", (plan2.id,))
    dst2.write_bytes(b"other")
    journal.recover()
    step = journal.get_plan(plan2.id).steps[0]
    assert step.state == "failed" and "both exist" in step.error
    assert src2.is_file() and dst2.read_bytes() == b"other"


def test_recovery_skips_a_plan_of_a_live_process(journal, lib, live_pid):
    plan = journal.plan("x", [(lib / "Series B" / "c001.cbz", lib / "Series B" / "c1.cbz")])
    with journal.store.connect() as con:
        con.execute("UPDATE journal_plans SET status='applying', host=?, pid=? WHERE id=?",
                    (journal_mod.this_host(), live_pid, plan.id))
    assert journal.recover() == []


def test_apply_needs_the_root_lock(journal, lib):
    plan = journal.plan("x", [(lib / "Series B" / "c001.cbz", lib / "Series B" / "c1.cbz")], root_path=lib)
    other = RootLock(lib, host="another-instance")
    other.acquire()
    with pytest.raises(LockBusy):
        journal.apply(plan.id)
    assert journal.get_plan(plan.id).status == "planned" and (lib / "Series B" / "c001.cbz").is_file()
    other.release()
    # A held lock can be handed in (e.g. the headless runner holds it for a whole batch).
    with RootLock(lib) as mine:
        assert journal.apply(plan.id, lock=mine).status == "applied"
        with pytest.raises(PlanStateError):
            journal.apply(plan.id, lock=mine)                         # already applied
    with pytest.raises(PlanStateError):
        journal.undo(plan.id, lock=RootLock(lib))                     # not held


def test_plan_state_rules(journal, lib):
    plan = journal.plan("x", [(lib / "Series B" / "c001.cbz", lib / "Series B" / "c1.cbz")])
    with pytest.raises(PlanStateError):
        journal.undo(plan.id)
    journal.apply(plan.id)
    journal.undo(plan.id)
    with pytest.raises(PlanStateError):
        journal.undo(plan.id)
    with pytest.raises(PlanStateError):
        journal.get_plan(9999)
    assert [p.status for p in journal.list_plans(["undone"])] == ["undone"]


def test_undo_stops_when_the_original_path_was_taken(journal, lib):
    src, dst = lib / "Series B" / "c001.cbz", lib / "Series B" / "c1.cbz"
    plan = journal.apply(journal.plan("x", [(src, dst)]).id)
    src.write_bytes(b"a new download with the old name")
    plan = journal.undo(plan.id)
    assert plan.status == "undo_failed" and plan.steps[0].state == "done" and "original path" in plan.steps[0].error
    assert src.read_bytes() == b"a new download with the old name" and dst.is_file()


def test_case_only_rename(journal, lib):
    src = lib / "Series A" / "Series A v01.cbz"
    dst = lib / "Series A" / "SERIES A v01.cbz"
    plan = journal.apply(journal.plan("x", [(src, dst)]).id)
    assert plan.status == "applied"
    assert "SERIES A v01.cbz" in os.listdir(lib / "Series A") and "Series A v01.cbz" not in os.listdir(lib / "Series A")
    assert journal.undo(plan.id).status == "undone"
    assert "Series A v01.cbz" in os.listdir(lib / "Series A")


def test_a_series_folder_move_keeps_its_link(db, journal, lib):
    root = db.add_root(str(lib))
    db.record_scan(root.id, lib, [SeriesSeen("Series A", "fp", 2)])
    mu_cache.save_entry(lib / "Series A", 5, "Linked", "", None, mu_confirmed=True)
    plan = journal.apply(journal.plan("x", [(lib / "Series A", lib / "Series A (Renamed)")], root_path=lib).id)
    assert plan.status == "applied"
    assert db.get_series(root.id, "Series A (Renamed)").mu_id == 5
    assert mu_cache.load_entry(lib / "Series A (Renamed)")["mu_title"] == "Linked"
    journal.undo(plan.id)
    assert mu_cache.load_entry(lib / "Series A")["mu_title"] == "Linked"


def test_nothing_is_ever_deleted(journal, lib):
    """Across apply, a failed step and undo, every byte that was there is still there."""
    before = _tree(lib)
    plan = journal.plan("x", [(lib / "Series A", lib / "Renamed"), (lib / "Series B" / "c001.cbz", lib / "x" / "y.cbz")])
    (lib / "x").mkdir()
    (lib / "x" / "y.cbz").write_bytes(b"someone else's")
    plan = journal.apply(plan.id)
    assert plan.status == "failed"
    journal.undo(plan.id)
    after = _tree(lib)
    assert {k: v for k, v in after.items() if k != "x/y.cbz"} == before
    assert after["x/y.cbz"] == b"someone else's"
