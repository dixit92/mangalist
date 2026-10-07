"""The journal's ``link`` step: a new library name for a download's file - hard link, verified copy fallback,
write-ahead, crash recovery, and an undo that removes only its own name and never the last copy."""

from __future__ import annotations

import errno
import logging
import os
import shutil
from pathlib import Path

import pytest

from mangalist.store import LOCK_NAME, Journal, Link, StepRefused
from mangalist.store import journal as journal_mod

from .conftest import make_archive


@pytest.fixture
def journal(db):
    return Journal(db)


@pytest.fixture
def lib(library):
    make_archive(library / "Series A" / "Series A v01.cbz", 11)
    return library


@pytest.fixture
def torrent(tmp_path):
    """A finished download outside the library (made-up names and bytes)."""
    folder = tmp_path / "torrents" / "mangalist" / "Series A v02-03 (Digital)"
    folder.mkdir(parents=True)
    (folder / "Series A v02 (Digital).cbz").write_bytes(os.urandom(70_000))
    (folder / "Series A v03 (Digital).cbz").write_bytes(os.urandom(200_000))
    return folder


def _exdev(src, dst):
    raise OSError(errno.EXDEV, "Invalid cross-device link")


def _files(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_link_creates_a_hard_link_inside_the_root(journal, lib, torrent):
    src = torrent / "Series A v02 (Digital).cbz"
    before = _files(torrent)
    dst = lib / "Series A" / "Volumes" / src.name
    plan = journal.plan_links("arrival", [Link(src, dst)], root_path=lib)
    assert plan.steps[0].op == "link" and plan.steps[0].src_signature.startswith("v1:")
    assert not dst.exists()                                           # planning touches nothing
    plan = journal.apply(plan.id)
    assert plan.status == "applied"
    step = plan.steps[0]
    assert step.state == "done" and step.how == "link" and step.dst_ident
    assert os.path.samefile(src, dst) and dst.read_bytes() == src.read_bytes()
    assert step.created_dirs == [str(lib / "Series A" / "Volumes")]
    assert _files(torrent) == before                                  # the source is never touched
    assert not (lib / LOCK_NAME).exists()


@pytest.mark.parametrize("make, message", [
    (lambda lib, src: (src, lib.parent / "elsewhere" / "x.cbz"), "outside the plan's root"),
    (lambda lib, src: (src, lib / "Series A" / "Series A v01.cbz"), "destination exists"),
    (lambda lib, src: (src.parent, lib / "Series A" / "folder"), "not a file"),
    (lambda lib, src: (src.parent / "missing.cbz", lib / "Series A" / "m.cbz"), "not a file"),
    (lambda lib, src: (src, lib / "Series A" / "Bad: name.cbz"), "not allowed on Windows"),
])
def test_refused_links(journal, lib, torrent, make, message):
    with pytest.raises(StepRefused, match=message):
        journal.plan_links("arrival", [make(lib, torrent / "Series A v02 (Digital).cbz")], root_path=lib)
    assert journal.list_plans() == []


def test_a_link_plan_needs_a_root_and_unique_destinations(journal, lib, torrent):
    a, b = torrent / "Series A v02 (Digital).cbz", torrent / "Series A v03 (Digital).cbz"
    with pytest.raises(StepRefused, match="needs its root"):
        journal.plan_links("arrival", [(a, lib / "Series A" / "x.cbz")], root_path=None)
    with pytest.raises(StepRefused, match="destination exists"):
        journal.plan_links("arrival", [(a, lib / "Series A" / "x.cbz"), (b, lib / "Series A" / "x.cbz")],
                           root_path=lib)
    with pytest.raises(StepRefused, match="at least one"):
        journal.plan_links("arrival", [], root_path=lib)


def test_exdev_falls_back_to_a_verified_copy(db, lib, torrent, caplog):
    journal = Journal(db, link=_exdev)
    src = torrent / "Series A v03 (Digital).cbz"
    dst = lib / "Series A" / src.name
    with caplog.at_level(logging.WARNING, logger="mangalist.store.journal"):
        plan = journal.apply(journal.plan_links("arrival", [(src, dst)], root_path=lib).id)
    step = plan.steps[0]
    assert plan.status == "applied" and step.how == "copy"
    assert dst.read_bytes() == src.read_bytes() and not os.path.samefile(src, dst)
    assert os.stat(dst).st_mtime_ns == os.stat(src).st_mtime_ns     # the mtime a link would share
    assert "COPIED" in caplog.text
    assert sorted(p.name for p in dst.parent.iterdir()) == ["Series A v01.cbz", src.name]   # no temp left


@pytest.mark.parametrize("code", [errno.EPERM, errno.ENOTSUP, errno.EMLINK])
def test_other_no_link_errors_also_copy(db, lib, torrent, code):
    def refuse(src, dst):
        raise OSError(code, os.strerror(code))

    journal = Journal(db, link=refuse)
    plan = journal.apply(journal.plan_links("arrival", [(torrent / "Series A v02 (Digital).cbz",
                                                          lib / "Series A" / "v02.cbz")], root_path=lib).id)
    assert plan.status == "applied" and plan.steps[0].how == "copy"


def test_a_copy_that_does_not_verify_is_discarded(db, lib, torrent, monkeypatch):
    journal = Journal(db, link=_exdev)
    src = torrent / "Series A v02 (Digital).cbz"
    dst = lib / "Series A" / "Volumes" / "v02.cbz"
    plan = journal.plan_links("arrival", [(src, dst)], root_path=lib)

    def bad_copy(fin, fout, length=0):
        fout.write(b"truncated")

    monkeypatch.setattr(shutil, "copyfileobj", bad_copy)
    plan = journal.apply(plan.id)
    assert plan.status == "failed" and "does not match" in plan.steps[0].error
    assert not dst.exists() and not (lib / "Series A" / "Volumes").exists()   # nothing left behind


def test_an_unrelated_link_error_is_not_copied(db, lib, torrent):
    def full(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    journal = Journal(db, link=full)
    dst = lib / "Series A" / "v02.cbz"
    plan = journal.apply(journal.plan_links("arrival", [(torrent / "Series A v02 (Digital).cbz", dst)],
                                            root_path=lib).id)
    assert plan.status == "failed" and "link failed" in plan.steps[0].error and not dst.exists()


def test_a_changed_source_or_a_taken_destination_fails_the_step(journal, lib, torrent):
    src = torrent / "Series A v02 (Digital).cbz"
    dst = lib / "Series A" / "v02.cbz"
    plan = journal.plan_links("arrival", [(src, dst)], root_path=lib)
    original = src.read_bytes()
    src.write_bytes(b"different")
    plan = journal.apply(plan.id)
    assert plan.status == "failed" and "changed" in plan.steps[0].error and not dst.exists()

    src.write_bytes(original)
    dst.write_bytes(b"someone else's")
    plan = journal.apply(plan.id)
    assert plan.status == "failed" and "exists now" in plan.steps[0].error
    assert dst.read_bytes() == b"someone else's"                      # nothing replaced


# --- crash recovery ------------------------------------------------------------------------------------


def _crash_on(monkeypatch, crash_state):
    real = journal_mod.Journal._set_step

    def crashing(self, step, state, created_dirs=None, error=...):
        if state == crash_state:
            raise KeyboardInterrupt("power cut")
        return real(self, step, state, created_dirs, error)

    monkeypatch.setattr(journal_mod.Journal, "_set_step", crashing)
    return real


def test_recovery_after_a_crash_right_after_the_link(journal, lib, torrent, monkeypatch):
    src, dst = torrent / "Series A v02 (Digital).cbz", lib / "Series A" / "v02.cbz"
    plan = journal.plan_links("arrival", [(src, dst)], root_path=lib)
    real = _crash_on(monkeypatch, "done")
    monkeypatch.setattr(journal_mod.Journal, "_set_link_made", lambda *a, **k: None)   # crash before that too
    with pytest.raises(KeyboardInterrupt):
        journal.apply(plan.id)
    monkeypatch.undo()
    assert journal.get_plan(plan.id).steps[0].state == "intent" and os.path.samefile(src, dst)
    (lib / LOCK_NAME).unlink(missing_ok=True)                          # the crashed process's lock

    (recovered,) = journal.recover()
    step = recovered.steps[0]
    assert recovered.status == "interrupted" and step.state == "done" and step.how == "link" and step.dst_ident
    assert journal.apply(plan.id).status == "applied"


def test_recovery_removes_a_crashed_copy_and_resumes(db, lib, torrent, monkeypatch):
    journal = Journal(db, link=_exdev)
    src, dst = torrent / "Series A v03 (Digital).cbz", lib / "Series A" / "New" / "v03.cbz"
    plan = journal.plan_links("arrival", [(src, dst)], root_path=lib)

    def power_cut(a, b):
        raise KeyboardInterrupt("power cut before the rename")

    monkeypatch.setattr(journal_mod, "rename_noreplace", power_cut)
    with pytest.raises(KeyboardInterrupt):
        journal.apply(plan.id)
    monkeypatch.undo()
    leftovers = [p.name for p in (lib / "Series A" / "New").iterdir()]
    assert len(leftovers) == 1 and leftovers[0].endswith(".part")     # the half-done copy
    (lib / LOCK_NAME).unlink(missing_ok=True)

    journal.recover()
    plan = journal.get_plan(plan.id)
    assert plan.status == "interrupted" and plan.steps[0].state == "planned"
    assert not (lib / "Series A" / "New").exists()                    # temp and its folder gone
    plan = journal.apply(plan.id)
    assert plan.status == "applied" and plan.steps[0].how == "copy" and dst.read_bytes() == src.read_bytes()


def test_recovery_leaves_a_foreign_destination_alone(journal, lib, torrent):
    src, dst = torrent / "Series A v02 (Digital).cbz", lib / "Series A" / "v02.cbz"
    plan = journal.plan_links("arrival", [(src, dst)], root_path=lib)
    with journal.store.connect() as con:
        con.execute("UPDATE journal_plans SET status='applying', pid=-1 WHERE id=?", (plan.id,))
        con.execute("UPDATE journal_steps SET state='intent' WHERE plan_id=?", (plan.id,))
    dst.write_bytes(b"another tool's file")
    journal.recover()
    step = journal.get_plan(plan.id).steps[0]
    assert step.state == "failed" and "not the planned file" in step.error
    assert dst.read_bytes() == b"another tool's file"


def test_recover_can_be_limited_to_some_plans(journal, lib, torrent):
    a = journal.plan_links("arrival", [(torrent / "Series A v02 (Digital).cbz", lib / "Series A" / "a.cbz")],
                           root_path=lib)
    b = journal.plan_links("arrival", [(torrent / "Series A v03 (Digital).cbz", lib / "Series A" / "b.cbz")],
                           root_path=lib)
    with journal.store.connect() as con:
        con.execute("UPDATE journal_plans SET status='applying', pid=-1")
    assert [p.id for p in journal.recover(only=[b.id])] == [b.id]
    assert journal.get_plan(a.id).status == "applying"


# --- undo ------------------------------------------------------------------------------------------------


def test_undo_removes_only_the_new_name(journal, lib, torrent):
    src = torrent / "Series A v02 (Digital).cbz"
    dst = lib / "Series A" / "Volumes" / src.name
    before_lib, before_torrent = _files(lib), _files(torrent)
    plan = journal.apply(journal.plan_links("arrival", [(src, dst)], root_path=lib).id)
    plan = journal.undo(plan.id)
    assert plan.status == "undone" and plan.steps[0].state == "undone"
    assert _files(lib) == before_lib and _files(torrent) == before_torrent
    assert not (lib / "Series A" / "Volumes").exists()


def test_undo_of_a_copy(db, lib, torrent):
    journal = Journal(db, link=_exdev)
    src = torrent / "Series A v02 (Digital).cbz"
    dst = lib / "Series A" / src.name
    plan = journal.apply(journal.plan_links("arrival", [(src, dst)], root_path=lib).id)
    assert journal.undo(plan.id).status == "undone" and not dst.exists() and src.is_file()


def test_undo_never_removes_the_last_copy(journal, lib, torrent):
    """After qBittorrent deleted the torrent's data the library link is the only name left: undo refuses."""
    src = torrent / "Series A v02 (Digital).cbz"
    dst = lib / "Series A" / src.name
    plan = journal.apply(journal.plan_links("arrival", [(src, dst)], root_path=lib).id)
    data = dst.read_bytes()
    src.unlink()                                                      # what Remove Completed does
    plan = journal.undo(plan.id)
    assert plan.status == "undo_failed" and "only one left" in plan.steps[0].error
    assert dst.read_bytes() == data


def test_undo_never_removes_a_replaced_or_edited_file(db, lib, torrent):
    src = torrent / "Series A v02 (Digital).cbz"
    dst = lib / "Series A" / src.name
    journal = Journal(db)
    plan = journal.apply(journal.plan_links("arrival", [(src, dst)], root_path=lib).id)
    dst.unlink()
    dst.write_bytes(b"the owner's own file now")                      # another file under that name
    plan = journal.undo(plan.id)
    assert plan.status == "undo_failed" and "not the one this step made" in plan.steps[0].error
    assert dst.read_bytes() == b"the owner's own file now"

    copier = Journal(db, link=_exdev)
    src3, dst3 = torrent / "Series A v03 (Digital).cbz", lib / "Series A" / "v03.cbz"
    plan = copier.apply(copier.plan_links("arrival", [(src3, dst3)], root_path=lib).id)
    with open(dst3, "r+b") as f:                                      # edited in place: same inode
        f.write(b"edited")
    plan = copier.undo(plan.id)
    assert plan.status == "undo_failed" and "changed" in plan.steps[0].error and dst3.is_file()


def test_recovery_while_undoing_a_link(journal, lib, torrent, monkeypatch):
    src, dst = torrent / "Series A v02 (Digital).cbz", lib / "Series A" / "v02.cbz"
    plan = journal.apply(journal.plan_links("arrival", [(src, dst)], root_path=lib).id)
    _crash_on(monkeypatch, "undone")
    with pytest.raises(KeyboardInterrupt):
        journal.undo(plan.id)
    monkeypatch.undo()
    (lib / LOCK_NAME).unlink(missing_ok=True)
    journal.recover()
    plan = journal.get_plan(plan.id)
    assert plan.status == "interrupted" and plan.steps[0].state == "undone" and not dst.exists() and src.is_file()
