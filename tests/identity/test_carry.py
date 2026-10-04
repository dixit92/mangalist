"""Series carry-over across all roots (port of MetadataCarryOverService, MinMovedShare 0.8) and the
integrator's probe cases (a)-(h): each synthetic series has an own MangaUpdates link, a Behind override, a kind
answer and an examined mark before the change."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangalist import mu_cache
from mangalist.identity import carry as carry_mod

from .conftest import make_archive, scan, sign


def _series(library: Path, name: str, n: int = 4, size0: int = 2000):
    for i in range(n):
        make_archive(library / name / f"{name} v{i + 1:02}.cbz", seed=f"{name}-{i}", size=size0 + i)


def _decorate(db, root, folder: Path, mu_id: int = 77):
    """An own link (confirmed) + Behind override, a kind answer and an examined mark."""
    mu_cache.save_entry(folder, mu_id, "Linked Title", "https://example.invalid/s", None, mu_confirmed=True)
    mu_cache.set_behind_override(folder, "done")
    rel = folder.relative_to(Path(root.path).resolve()).as_posix()
    assert db.set_series_kind(root.id, rel, "volumes")
    db.set_setting("examined", [str(folder)])


def _assert_kept(db, root, new_folder: Path, old_id: int, mu_id: int = 77):
    rel = new_folder.relative_to(Path(root.path).resolve()).as_posix()
    row = db.get_series(root.id, rel)
    assert row is not None and row.id == old_id and row.status == "present"
    assert row.kind_hint == "volumes" and row.mu_id == mu_id and row.mu_confirmed
    link = mu_cache.load_entry(new_folder)
    assert link["mu_id"] == mu_id and link["mu_confirmed"] and link["behind_override"] == "done"
    assert db.get_setting("examined") == [str(new_folder)]


def _setup(db, library, name="Old Title", n=4):
    _series(library, name, n)
    root = db.add_root(str(library))
    first = scan(db)
    folder = next(e.folder for e in first.entries if e.folder.name == name)
    _decorate(db, root, folder)
    old_id = db.get_series(root.id, name).id
    sign(db)                                                          # the background backfill ran
    return root, folder, old_id


def test_a_rename_only_keeps_everything(db, library):
    root, folder, old_id = _setup(db, library)
    (library / "Old Title").rename(library / "New Title")
    result = scan(db)
    new_folder = folder.with_name("New Title")
    assert result.renamed == [(folder, new_folder)]
    _assert_kept(db, root, new_folder, old_id)
    assert mu_cache.load_entry(folder) is None
    assert db.get_series(root.id, "Old Title") is None                # the row moved, nothing left missing
    (c,) = result.identity.carries
    assert c.merged and set(c.moved) == {"row", "link", "kind", "examined"} and c.kept == []
    units = db.list_units(old_id)
    assert {u.rel_path for u in units} == {f"Old Title v{i:02}.cbz" for i in range(1, 5)}   # files kept their names
    assert {u.kind for u in units} == {"volume"}


def test_b_a_rename_plus_one_new_chapter_is_kept(db, library):
    root, folder, old_id = _setup(db, library)
    (library / "Old Title").rename(library / "New Title")
    make_archive(library / "New Title" / "New Title v05.cbz", seed="new chapter", size=2004)
    result = scan(db)
    _assert_kept(db, root, folder.with_name("New Title"), old_id)
    assert len(db.list_archives(series_id=old_id)) == 5


def test_c_an_empty_folder_renamed_is_missing_and_can_be_reattached_by_hand(db, library):
    (library / "Wanted Series").mkdir()
    root = db.add_root(str(library))
    first = scan(db)
    folder = first.entries[0].folder
    _decorate(db, root, folder, mu_id=5)
    old_id = db.get_series(root.id, "Wanted Series").id
    (library / "Wanted Series").rename(library / "Wanted Series (2024)")
    result = scan(db)
    assert result.renamed == []
    missing = carry_mod.missing_series(db)
    assert [(m.id, m.name, m.has_link, m.kind_hint, m.examined) for m in missing] == [
        (old_id, "Wanted Series", True, "volumes", True)]
    target = db.get_series(root.id, "Wanted Series (2024)")
    assert [s.id for s in carry_mod.live_series(db, without_own_data=True)] == [target.id]
    res = carry_mod.reattach(db, old_id, target.id)
    assert res.merged and res.how == "manual"
    _assert_kept(db, root, folder.with_name("Wanted Series (2024)"), old_id, mu_id=5)
    assert carry_mod.missing_series(db) == []


def test_d_a_move_to_another_root_is_kept(db, library, library2):
    root, folder, old_id = _setup(db, library)
    root2 = db.add_root(str(library2))
    (library / "Old Title").rename(library2 / "Old Title")            # same filesystem: a rename
    result = scan(db)
    new_folder = library2.resolve() / "Old Title"
    assert result.renamed == [(folder, new_folder)]
    _assert_kept(db, root2, new_folder, old_id)
    assert db.get_series(root.id, "Old Title") is None
    assert {a.root_id for a in db.list_archives(series_id=old_id)} == {root2.id}


def test_g_a_target_with_its_own_link_is_never_overwritten(db, library):
    root, folder, old_id = _setup(db, library)
    (library / "Old Title").rename(library / "New Title")
    new_folder = folder.with_name("New Title")
    mu_cache.save_entry(new_folder, 99, "Own link", "", None, mu_confirmed=False)   # T matched on its own first
    result = scan(db)
    assert mu_cache.load_entry(new_folder)["mu_id"] == 99            # T keeps its own link
    old = db.get_series(root.id, "Old Title")
    assert old.id == old_id and old.status == "missing"               # the old row stays missing with its link
    assert mu_cache.load_entry(folder)["mu_id"] == 77
    new = db.get_series(root.id, "New Title")
    assert new.kind_hint == "volumes" and old.kind_hint is None        # what T lacked moved
    (c,) = result.identity.carries
    assert c.kept == ["link"] and set(c.moved) == {"kind", "examined"}
    assert [m.id for m in carry_mod.missing_series(db)] == [old_id]


def test_h_less_than_80_percent_moved_stays_missing(db, library):
    root, folder, old_id = _setup(db, library, n=5)
    (library / "Old Title").rename(library / "New Title")
    for i in (4, 5):                                                 # 2 of 5 archives gone: 60% moved
        (library / "New Title" / f"Old Title v{i:02}.cbz").unlink()
    result = scan(db)
    assert result.renamed == [] and len(result.identity.moves) == 3
    old = db.get_series(root.id, "Old Title")
    assert old.status == "missing" and old.kind_hint == "volumes"
    assert mu_cache.load_entry(folder)["mu_id"] == 77
    assert db.get_series(root.id, "New Title").kind_hint is None


def test_exactly_80_percent_is_enough(db, library):
    root, folder, old_id = _setup(db, library, n=5)
    (library / "Old Title").rename(library / "New Title")
    (library / "New Title" / "Old Title v05.cbz").unlink()           # 4 of 5 = 80%
    scan(db)
    _assert_kept(db, root, folder.with_name("New Title"), old_id)


def test_a_split_folder_has_no_single_target(db, library):
    root, folder, old_id = _setup(db, library, n=4)
    (library / "Half One").mkdir()
    (library / "Half Two").mkdir()
    for i in (1, 2):
        (library / "Old Title" / f"Old Title v{i:02}.cbz").rename(library / "Half One" / f"v{i}.cbz")
    for i in (3, 4):
        (library / "Old Title" / f"Old Title v{i:02}.cbz").rename(library / "Half Two" / f"v{i}.cbz")
    (library / "Old Title").rmdir()
    result = scan(db)
    assert len(result.identity.moves) == 4 and result.renamed == []
    assert db.get_series(root.id, "Old Title").status == "missing"
    half = db.get_series(root.id, "Half One")
    res = carry_mod.reattach(db, old_id, half.id)                    # the owner decides
    assert res.merged
    assert db.get_series(root.id, "Half One").id == old_id


def test_the_old_rule_without_signatures_leaves_it_missing(db, library):
    """No signature before the move: nothing to recognise it by (the backfill had not run yet)."""
    _series(library, "Old Title", 3)
    root = db.add_root(str(library))
    scan(db)
    (library / "Old Title").rename(library / "New Title")
    assert scan(db).renamed == []
    assert db.get_series(root.id, "Old Title").status == "missing"


def test_forget_needs_a_missing_row_and_drops_its_data(db, library):
    root, folder, old_id = _setup(db, library)
    assert not carry_mod.forget(db, old_id)                          # present: refused
    for p in (library / "Old Title").iterdir():
        p.unlink()
    (library / "Old Title").rmdir()
    scan(db)
    assert carry_mod.missing_count(db) == 1
    archive_ids = {a.id for a in db.list_archives(series_id=old_id)}
    assert carry_mod.forget(db, old_id)
    assert db.get_series(root.id, "Old Title") is None and mu_cache.load_entry(folder) is None
    assert db.get_setting("examined") == []
    assert {db.get_archive(i).series_id for i in archive_ids} == {None}   # archive rows kept, unattached
    assert carry_mod.missing_count(db) == 0


def test_reattach_refuses_a_live_source_or_a_missing_target(db, library):
    root, folder, old_id = _setup(db, library)
    with pytest.raises(carry_mod.ReattachError):
        carry_mod.reattach(db, old_id, old_id)


def test_carries_are_recorded(db, library):
    root, folder, old_id = _setup(db, library)
    (library / "Old Title").rename(library / "New Title")
    scan(db)
    with db.connect() as con:
        rows = con.execute("SELECT from_series_id, from_path, to_path, how FROM series_carries").fetchall()
    assert [tuple(r) for r in rows] == [(old_id, "Old Title", "New Title", "archives")]


def test_two_series_merged_into_one_folder(db, library):
    """Both vanished folders land in one existing live folder: the first fills it, the second keeps what T has."""
    _series(library, "Part One", 2)
    _series(library, "Part Two", 2, size0=3000)
    root = db.add_root(str(library))
    first = scan(db)
    f1 = next(e.folder for e in first.entries if e.folder.name == "Part One")
    f2 = next(e.folder for e in first.entries if e.folder.name == "Part Two")
    mu_cache.save_entry(f1, 1, "One", "", None, mu_confirmed=True)
    mu_cache.save_entry(f2, 2, "Two", "", None, mu_confirmed=True)
    sign(db)
    (library / "Together").mkdir()
    for f in (f1, f2):
        for p in sorted(f.iterdir()):
            p.rename(library / "Together" / p.name)
        f.rmdir()
    result = scan(db)
    together = db.get_series(root.id, "Together")
    assert together.mu_id == 1                                       # the first (lowest id) carried
    assert db.get_series(root.id, "Part Two").status == "missing"    # its link waits for the owner
    assert [c.kept for c in result.identity.carries] == [[], ["link"]]
