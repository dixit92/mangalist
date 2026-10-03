"""Series rows: fingerprints, a renamed series folder keeps its row and its MangaUpdates link."""

from __future__ import annotations

from pathlib import Path

from manga_list import mu_cache
from manga_list.store import SeriesSeen, folder_fingerprint
from manga_list.store.series import link_key


def test_fingerprint_ignores_names_and_order():
    assert folder_fingerprint([3, 1, 2]) == folder_fingerprint([1, 2, 3])
    assert folder_fingerprint([1, 2, 3]) != folder_fingerprint([1, 2, 4])
    assert folder_fingerprint([1, 2]) != folder_fingerprint([1, 2, 2])
    assert folder_fingerprint([]) is None
    assert folder_fingerprint([5]).startswith("v1:1:")


def _scan(db, root, root_dir, *seen):
    return db.record_scan(root.id, root_dir, [SeriesSeen(rel, fp, n) for rel, fp, n in seen])


def test_new_known_and_missing(db, library):
    root = db.add_root(str(library))
    rec = _scan(db, root, library, ("Series A", "fp-a", 2), ("Series B", "fp-b", 1))
    assert sorted(rec.added) == ["Series A", "Series B"] and not rec.relinked and not rec.missing
    rec = _scan(db, root, library, ("Series A", "fp-a2", 3))
    assert rec.added == [] and rec.missing == ["Series B"]
    rows = {s.rel_path: s for s in db.list_series(root.id)}
    assert rows["Series A"].fingerprint == "fp-a2" and rows["Series A"].n_archives == 3
    assert rows["Series B"].status == "missing"          # kept, not deleted


def test_renamed_folder_keeps_row_and_link(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("Old Name", "fp-1", 4), ("Other", "fp-2", 1))
    old_id = db.get_series(root.id, "Old Name").id
    mu_cache.save_entry(Path(link_key(library, "Old Name")), 42, "Linked Title", "", None, mu_confirmed=True)
    assert db.get_series(root.id, "Old Name").mu_id == 42           # identity copied on save

    rec = _scan(db, root, library, ("New Name", "fp-1", 4), ("Other", "fp-2", 1))
    assert rec.relinked == [("Old Name", "New Name")] and not rec.added and not rec.missing
    moved = db.get_series(root.id, "New Name")
    assert moved.id == old_id and moved.mu_id == 42 and moved.mu_confirmed
    assert mu_cache.load_entry(Path(link_key(library, "New Name")))["mu_title"] == "Linked Title"
    assert mu_cache.load_entry(Path(link_key(library, "Old Name"))) is None


def test_ambiguous_fingerprints_are_not_relinked(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("A", "same", 1), ("B", "same", 1))
    rec = _scan(db, root, library, ("C", "same", 1))
    assert rec.relinked == [] and rec.added == ["C"] and sorted(rec.missing) == ["A", "B"]


def test_a_relink_never_overwrites_the_new_paths_own_link(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("Old", "fp", 1))
    mu_cache.save_entry(Path(link_key(library, "Old")), 1, "Old link", "", None, mu_confirmed=False)
    mu_cache.save_entry(Path(link_key(library, "New")), 2, "Own link", "", None, mu_confirmed=False)
    _scan(db, root, library, ("New", "fp", 1))
    assert mu_cache.load_entry(Path(link_key(library, "New")))["mu_id"] == 2
    assert mu_cache.load_entry(Path(link_key(library, "Old")))["mu_id"] == 1


def test_empty_folders_have_no_fingerprint_and_never_relink(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("Wanted A", None, 0))
    rec = _scan(db, root, library, ("Wanted B", None, 0))
    assert rec.relinked == [] and rec.added == ["Wanted B"]


def test_identity_follows_confirm_and_delete(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("S", "fp", 1))
    folder = Path(link_key(library, "S"))
    mu_cache.save_entry(folder, 7, "T", "", None, mu_confirmed=False)
    assert db.get_series(root.id, "S").mu_id == 7 and not db.get_series(root.id, "S").mu_confirmed
    mu_cache.set_mu_confirmed(folder, True)
    assert db.get_series(root.id, "S").mu_confirmed
    mu_cache.delete_entry(folder)
    assert db.get_series(root.id, "S").mu_id is None


def test_relink_folder_after_a_journal_move(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("Before", "fp", 1))
    mu_cache.save_entry(Path(link_key(library, "Before")), 9, "T", "", None, mu_confirmed=True)
    assert db.relink_folder(library / "Before", library / "After")
    assert db.get_series(root.id, "After").mu_id == 9
    assert mu_cache.load_entry(library / "After")["mu_id"] == 9
