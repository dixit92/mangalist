"""The confirmed discard: delete_checked (every guard on one file) and discard_duplicates (locks, "keep one")."""

from __future__ import annotations

import os
import time

import pytest

from mangalist.duplicates import (DiscardRefused, delete_checked, discard_duplicates, find_duplicate_files,
                                  iso_from_ns)
from mangalist.store.lock import LOCK_NAME, RootLock

S = "Example Series"
T0 = 1_700_000_000_000_000_000


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "root"
    (folder / S).mkdir(parents=True)
    return folder


def make(root, rel, size=10, mtime_ns=T0):
    path = root / S / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


# --- delete_checked ----------------------------------------------------------------------------------------------

def test_deletes_a_file_inside_a_root_that_is_as_listed(root):
    f = make(root, "a.cbz", size=7)
    delete_checked(f, 7, iso_from_ns(T0), [str(root)])
    assert not f.exists()


def test_refuses_a_path_outside_every_root(root, tmp_path):
    outside = tmp_path / "elsewhere.cbz"
    outside.write_bytes(b"x")
    with pytest.raises(DiscardRefused, match="outside"):
        delete_checked(outside, 1, iso_from_ns(os.lstat(outside).st_mtime_ns), [str(root)])
    assert outside.exists()


def test_refuses_a_sibling_folder_that_only_shares_the_roots_name_prefix(root, tmp_path):
    sibling = tmp_path / "root-other" / "a.cbz"
    sibling.parent.mkdir()
    sibling.write_bytes(b"x")
    with pytest.raises(DiscardRefused, match="outside"):
        delete_checked(sibling, 1, iso_from_ns(os.lstat(sibling).st_mtime_ns), [str(root)])


def test_refuses_the_root_itself_and_folders(root):
    folder = root / S / "Season 1"
    folder.mkdir()
    with pytest.raises(DiscardRefused, match="outside"):
        delete_checked(root, 0, "", [str(root)])
    with pytest.raises(DiscardRefused, match="not a plain file"):
        delete_checked(folder, os.lstat(folder).st_size, iso_from_ns(os.lstat(folder).st_mtime_ns), [str(root)])
    assert folder.is_dir()


def test_refuses_a_dotdot_path_that_climbs_out_of_the_root(root, tmp_path):
    victim = tmp_path / "victim.cbz"
    victim.write_bytes(b"x")
    sneaky = os.path.join(str(root), S, "..", "..", "victim.cbz")
    with pytest.raises(DiscardRefused, match=r"\.\."):
        delete_checked(sneaky, 1, iso_from_ns(os.lstat(victim).st_mtime_ns), [str(root)])
    assert victim.exists()


def test_refuses_a_relative_path(root):
    with pytest.raises(DiscardRefused, match="absolute"):
        delete_checked("a.cbz", 1, "", [str(root)])


def test_refuses_a_symlink_and_leaves_both_the_link_and_its_target(root, tmp_path):
    target = tmp_path / "target.cbz"
    target.write_bytes(b"x" * 5)
    link = root / S / "link.cbz"
    link.symlink_to(target)
    with pytest.raises(DiscardRefused, match="symbolic link"):
        delete_checked(link, 5, iso_from_ns(os.stat(target).st_mtime_ns), [str(root)])
    assert link.is_symlink() and target.exists()


def test_refuses_a_file_behind_a_symlinked_folder(root, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    inside = real / "a.cbz"
    inside.write_bytes(b"x")
    (root / S / "Season 1").symlink_to(real, target_is_directory=True)
    with pytest.raises(DiscardRefused, match="symbolic link"):
        delete_checked(root / S / "Season 1" / "a.cbz", 1, iso_from_ns(os.lstat(inside).st_mtime_ns), [str(root)])
    assert inside.exists()


def test_a_root_that_is_itself_a_symlink_is_fine(tmp_path):
    real = tmp_path / "real"
    (real / S).mkdir(parents=True)
    root = tmp_path / "mount"
    root.symlink_to(real, target_is_directory=True)
    f = root / S / "a.cbz"
    f.write_bytes(b"x")
    delete_checked(f, 1, iso_from_ns(os.lstat(f).st_mtime_ns), [str(root)])
    assert not (real / S / "a.cbz").exists()


def test_refuses_a_file_whose_size_or_time_changed_since_it_was_listed(root):
    f = make(root, "a.cbz", size=7)
    with pytest.raises(DiscardRefused, match="changed"):
        delete_checked(f, 8, iso_from_ns(T0), [str(root)])
    with pytest.raises(DiscardRefused, match="changed"):
        delete_checked(f, 7, iso_from_ns(T0 + 1_000_000_000), [str(root)])
    assert f.exists()


def test_refuses_a_file_that_is_already_gone(root):
    with pytest.raises(DiscardRefused, match="gone"):
        delete_checked(root / S / "nope.cbz", 1, "", [str(root)])


def test_refuses_the_lock_file_and_its_artefacts(root):
    lock = root / LOCK_NAME
    lock.write_text("{}")
    with pytest.raises(DiscardRefused, match="lock"):
        delete_checked(lock, lock.stat().st_size, iso_from_ns(os.lstat(lock).st_mtime_ns), [str(root)])
    assert lock.exists()


def test_a_filename_with_a_backslash_is_an_ordinary_name_on_posix(root):
    if os.sep != "/":
        pytest.skip("POSIX only")
    f = make(root, "a\\b.cbz")
    delete_checked(f, 10, iso_from_ns(T0), [str(root)])
    assert not f.exists()


# --- discard_duplicates ------------------------------------------------------------------------------------------

@pytest.fixture
def two(db, lib):
    older = lib.add(S, "Series c001.cbz", size=10, mtime_ns=T0)
    newer = lib.add(S, "Series c001 [2].cbz", size=20, mtime_ns=T0 + 5_000_000_000)
    lib.scan()
    (group,) = find_duplicate_files(db)
    return lib, group, older, newer


def test_discards_the_chosen_file_and_logs_it(db, two, caplog):
    lib, group, older, newer = two
    caplog.set_level("INFO", logger="mangalist.duplicates")
    out = discard_duplicates(db, [(group, [str(older)])])
    assert [(o.path, o.deleted) for o in out] == [(str(older), True)]
    assert not older.exists() and newer.exists()
    assert str(older) in caplog.text and "Discarded" in caplog.text
    assert not (lib.dir / LOCK_NAME).exists()                    # the lock is released
    assert find_duplicate_files(db) == []                        # and the list is right without a rescan


def test_never_discards_every_file_of_a_number(db, two):
    _lib, group, older, newer = two
    out = discard_duplicates(db, [(group, [str(older), str(newer)])])
    assert [o.deleted for o in out] == [False, False]
    assert all("no copy" in o.reason for o in out)
    assert older.exists() and newer.exists()


def test_refuses_a_path_that_is_not_in_the_group(db, two, tmp_path):
    _lib, group, older, newer = two
    stranger = tmp_path / "stranger.cbz"
    stranger.write_bytes(b"x")
    out = discard_duplicates(db, [(group, [str(stranger)])])
    assert [(o.deleted, "not one of" in o.reason) for o in out] == [(False, True)]
    assert stranger.exists()


def test_skips_a_file_that_changed_and_reports_it(db, two):
    _lib, group, older, newer = two
    older.write_bytes(b"y" * 99)
    out = discard_duplicates(db, [(group, [str(older)])])
    assert [(o.deleted, o.reason) for o in out] == [(False, "it changed since the list was made")]
    assert older.exists()


def test_refuses_when_the_copy_to_keep_has_vanished_or_changed(db, two):
    _lib, group, older, newer = two
    newer.unlink()
    out = discard_duplicates(db, [(group, [str(older)])])
    assert not out[0].deleted and "refresh" in out[0].reason
    assert older.exists()


def test_refuses_while_another_instance_holds_the_roots_lock(db, two):
    lib, group, older, newer = two
    with RootLock(lib.dir, host="another-host", pid=1) as _held:
        out = discard_duplicates(db, [(group, [str(older)])])
        assert [o.deleted for o in out] == [False]
        assert "busy" in out[0].reason
        assert older.exists()
    # released: the same call now goes through
    assert discard_duplicates(db, [(group, [str(older)])])[0].deleted


def test_one_lock_serves_several_groups_of_a_root_and_is_released(db, lib):
    paths = [lib.add(S, n, size=s, mtime_ns=T0) for n, s in
             (("Series c001.cbz", 5), ("Series c001 [2].cbz", 6), ("Series c002.cbz", 5), ("Series c002 [2].cbz", 6))]
    lib.scan()
    groups = find_duplicate_files(db)
    out = discard_duplicates(db, [(groups[0], [str(paths[0])]), (groups[1], [str(paths[2])])])
    assert [o.deleted for o in out] == [True, True]
    assert not (lib.dir / LOCK_NAME).exists()
    assert [p.exists() for p in paths] == [False, True, False, True]


def test_a_stale_lock_from_a_dead_process_does_not_block(db, two):
    lib, group, older, _newer = two
    (lib.dir / LOCK_NAME).write_text(
        '{"app":"MangaList","token":"t","host":"%s","pid":999999,"acquired_at":1,"heartbeat":%s}'
        % (RootLock(lib.dir).host, time.time() - 10_000))
    assert discard_duplicates(db, [(group, [str(older)])])[0].deleted


def test_nothing_is_deleted_for_an_empty_selection(db, two):
    _lib, group, older, newer = two
    assert discard_duplicates(db, []) == []
    assert discard_duplicates(db, [(group, [])]) == []
    assert older.exists() and newer.exists()
