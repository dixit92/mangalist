"""Series rows: fingerprints (stored, never used to pair since schema 4), missing rows, MangaUpdates identity,
the journal re-link. Recognising a folder renamed by hand is tests/identity."""

from __future__ import annotations

from pathlib import Path

from mangalist import mu_cache
from mangalist.store import SeriesSeen, folder_fingerprint
from mangalist.store.series import link_key


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


def test_a_same_fingerprint_never_relinks_sizes_alone_are_no_evidence(db, library):
    """Schema 4: the exact size-multiset re-link is gone; the archive identity decides (tests/identity)."""
    root = db.add_root(str(library))
    _scan(db, root, library, ("Old Name", "fp-1", 4), ("Other", "fp-2", 1))
    old_id = db.get_series(root.id, "Old Name").id
    mu_cache.save_entry(Path(link_key(library, "Old Name")), 42, "Linked Title", "", None, mu_confirmed=True)
    assert db.get_series(root.id, "Old Name").mu_id == 42           # identity copied on save

    rec = _scan(db, root, library, ("New Name", "fp-1", 4), ("Other", "fp-2", 1))
    assert rec.relinked == [] and rec.added == ["New Name"] and rec.missing == ["Old Name"]
    old = db.get_series(root.id, "Old Name")
    assert old.id == old_id and old.status == "missing" and old.missing_since and old.mu_id == 42
    assert mu_cache.load_entry(Path(link_key(library, "Old Name")))["mu_id"] == 42   # kept on the missing row
    assert mu_cache.load_entry(Path(link_key(library, "New Name"))) is None


def test_a_missing_row_that_comes_back_is_present_again(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("A", "fp", 1))
    _scan(db, root, library)
    assert db.get_series(root.id, "A").status == "missing"
    _scan(db, root, library, ("A", "fp", 1))
    a = db.get_series(root.id, "A")
    assert a.status == "present" and a.missing_since is None


def test_empty_folders_have_no_fingerprint(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("Wanted A", None, 0))
    rec = _scan(db, root, library, ("Wanted B", None, 0))
    assert rec.relinked == [] and rec.added == ["Wanted B"]
    assert db.get_series(root.id, "Wanted B").fingerprint is None


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


def test_relink_folder_into_another_root(db, library, tmp_path):
    other = tmp_path / "library" / "Other"
    other.mkdir(parents=True)
    root = db.add_root(str(library))
    root2 = db.add_root(str(other))
    _scan(db, root, library, ("Moving", "fp", 1))
    sid = db.get_series(root.id, "Moving").id
    assert db.relink_folder(library / "Moving", other / "Moving")
    moved = db.get_series(root2.id, "Moving")
    assert moved is not None and moved.id == sid


def test_a_folder_the_root_now_excludes_is_let_go_not_missing(db, library):
    # owner 2026-10-09: excluding "@Oneshots" asked "missing - forget it?"; excluding is the owner's choice
    root = db.add_root(str(library))
    _scan(db, root, library, ("Series A", "fp-a", 2), ("@Oneshots", "fp-o", 5), ("Extras", "fp-e", 1))
    mu_cache.save_entry(Path(link_key(library, "@Oneshots")), 42, "Linked Title", "", None, mu_confirmed=True)
    db.set_exclusions(root.id, ["@Oneshots/**", "Extras"])
    rec = _scan(db, root, library, ("Series A", "fp-a", 2))
    assert sorted(rec.excluded) == ["@Oneshots", "Extras"] and rec.missing == []
    assert [s.rel_path for s in db.list_series(root.id)] == ["Series A"]       # nothing left to ask about
    db.set_exclusions(root.id, [])                                                 # lifted: back as it was
    _scan(db, root, library, ("Series A", "fp-a", 2), ("@Oneshots", "fp-o", 5))
    back = db.get_series(root.id, "@Oneshots")
    assert back.status == "present" and back.mu_id == 42


def test_an_already_missing_folder_that_is_then_excluded_is_let_go(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("Series A", "fp-a", 2), ("@Oneshots", "fp-o", 5))
    _scan(db, root, library, ("Series A", "fp-a", 2))
    assert db.get_series(root.id, "@Oneshots").status == "missing"
    db.set_exclusions(root.id, ["@Oneshots"])
    rec = _scan(db, root, library, ("Series A", "fp-a", 2))
    assert rec.excluded == ["@Oneshots"] and db.get_series(root.id, "@Oneshots") is None


def test_an_excluded_folder_with_download_records_stays_missing(db, library):
    root = db.add_root(str(library))
    _scan(db, root, library, ("Series A", "fp-a", 2))
    sid = db.get_series(root.id, "Series A").id
    with db.connect() as con:
        con.execute("INSERT INTO ledger (created_at, updated_at, series_id, tool) VALUES ('t', 't', ?, 'qbittorrent')",
                    (sid,))
    db.set_exclusions(root.id, ["Series A"])
    rec = _scan(db, root, library)
    assert rec.missing == ["Series A"] and rec.excluded == []                       # a download still points at it
