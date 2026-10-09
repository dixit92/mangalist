"""Recording a scan root by root (``scan_and_record_library``) and a rescan of one root: a series that moved from one
root to another is still carried - its row, link, kind answer and examined mark - whichever root is read first,
and a one-root rescan touches only that root. Real database, synthetic trees, no threads."""

from __future__ import annotations

from mangalist import scanner
from mangalist.identity import carry as carry_mod
from mangalist.scanner import scan_and_record_library

from .conftest import make_archive, sign
from .test_carry import _assert_kept, _decorate, _series


def _roots(db):
    return {r.name: r for r in db.list_roots()}


def _setup_two_roots(db, library, library2):
    """"Old Title" in the first root with a link / kind answer / examined mark, signed; a second, empty root."""
    _series(library, "Old Title")
    root = db.add_root(str(library))
    root2 = db.add_root(str(library2))
    first = scan_and_record_library(db.list_roots(), db)
    folder = next(e.folder for e in first.entries if e.folder.name == "Old Title")
    _decorate(db, root, folder)
    old_id = db.get_series(root.id, "Old Title").id
    sign(db)
    return root, root2, folder, old_id


def test_each_root_is_recorded_before_the_next_is_read(db, library, library2):
    _series(library, "Series A", 2)
    _series(library2, "Series K", 2)
    r1, r2 = db.add_root(str(library)), db.add_root(str(library2))
    seen = []

    def on_root(rs, renamed):
        seen.append((rs.root_name, rs.root_id, [e.title for e in rs.entries],
                     sorted(s.rel_path for s in db.list_series(r1.id)), sorted(s.rel_path for s in db.list_series(r2.id)),
                     sorted(a.rel_path for a in db.list_archives(root_id=r2.id)), renamed))

    progress = []
    result = scan_and_record_library(db.list_roots(), db, progress=lambda d, t, n: progress.append(n), on_root=on_root)
    assert [(s[0], s[2]) for s in seen] == [("Manga", ["Series A"]), ("Completed", ["Series K"])]
    assert seen[0][3:6] == (["Series A"], [], [])            # the first root is in the database when it is handed over...
    assert seen[1][3:6] == (["Series A"], ["Series K"], ["Series K/Series K v01.cbz", "Series K/Series K v02.cbz"])
    assert [r.root_id for r in result.roots] == [r1.id, r2.id] and result.renamed == []
    assert any(n.startswith("Manga: Series A") for n in progress) and any(n.startswith("Completed: ") for n in progress)


def test_a_move_between_roots_is_carried_when_the_source_is_read_first(db, library, library2):
    root, root2, folder, old_id = _setup_two_roots(db, library, library2)
    (library / "Old Title").rename(library2 / "Old Title")
    result = scan_and_record_library(db.list_roots(), db)
    new_folder = library2.resolve() / "Old Title"
    assert result.renamed == [(folder, new_folder)]
    _assert_kept(db, root2, new_folder, old_id)
    assert db.get_series(root.id, "Old Title") is None
    assert carry_mod.missing_series(db) == []
    assert {a.root_id for a in db.list_archives(series_id=old_id)} == {root2.id}


def test_a_move_between_roots_is_carried_when_the_destination_is_read_first(db, library, library2):
    root, root2, folder, old_id = _setup_two_roots(db, library, library2)
    (library / "Old Title").rename(library2 / "Old Title")
    both = db.list_roots()
    result = scan_and_record_library([r for r in both if r.id == root2.id] + [r for r in both if r.id == root.id], db)
    new_folder = library2.resolve() / "Old Title"
    assert result.renamed == [(folder, new_folder)]         # recognised after the fact, when the source vanished
    _assert_kept(db, root2, new_folder, old_id)
    assert db.get_series(root.id, "Old Title") is None
    assert carry_mod.missing_series(db) == []               # not left missing + new
    assert {a.root_id for a in db.list_archives(series_id=old_id)} == {root2.id}


def test_one_root_rescans_of_the_destination_then_the_source_carry_the_series(db, library, library2):
    root, root2, folder, old_id = _setup_two_roots(db, library, library2)
    (library / "Old Title").rename(library2 / "Old Title")
    new_folder = library2.resolve() / "Old Title"

    only_destination = scan_and_record_library([root2], db)
    assert only_destination.renamed == []                   # the source was not read: it is still "present" there
    assert db.get_series(root.id, "Old Title").status == "present"
    assert db.get_series(root2.id, "Old Title").id != old_id        # a new row for now
    assert carry_mod.missing_series(db) == []

    only_source = scan_and_record_library([root], db)
    assert only_source.renamed == [(folder, new_folder)]
    _assert_kept(db, root2, new_folder, old_id)
    assert carry_mod.missing_series(db) == [] and db.get_series(root.id, "Old Title") is None


def test_one_root_rescans_of_the_source_then_the_destination_carry_the_series(db, library, library2):
    root, root2, folder, old_id = _setup_two_roots(db, library, library2)
    (library / "Old Title").rename(library2 / "Old Title")
    new_folder = library2.resolve() / "Old Title"

    only_source = scan_and_record_library([root], db)
    assert only_source.renamed == []
    assert [m.rel_path for m in carry_mod.missing_series(db)] == ["Old Title"]     # waiting for the other root

    only_destination = scan_and_record_library([root2], db)
    assert only_destination.renamed == [(folder, new_folder)]
    _assert_kept(db, root2, new_folder, old_id)
    assert carry_mod.missing_series(db) == []


def test_a_one_root_rescan_leaves_the_other_roots_untouched(db, library, library2):
    _series(library, "Series A", 2)
    _series(library2, "Series K", 2)
    r1, r2 = db.add_root(str(library)), db.add_root(str(library2))
    scan_and_record_library(db.list_roots(), db)
    before = {a.rel_path: (a.status, a.last_seen_scan) for a in db.list_archives(root_id=r2.id)}
    gone = library2 / "Series K" / "Series K v01.cbz"
    gone.unlink()
    make_archive(library / "Series A" / "Series A v03.cbz", seed="a3", size=2003)

    result = scan_and_record_library([r1], db)
    assert [r.root_id for r in result.roots] == [r1.id]
    assert {a.rel_path: (a.status, a.last_seen_scan) for a in db.list_archives(root_id=r2.id)} == before
    assert db.get_series(r2.id, "Series K").status == "present"
    assert db.archive_at(r2.id, "Series K/Series K v01.cbz").status == "present"        # not read: still as last seen
    assert db.archive_at(r1.id, "Series A/Series A v03.cbz").status == "present"

    scan_and_record_library([r2], db)                        # now its own turn
    assert db.archive_at(r2.id, "Series K/Series K v01.cbz").status == "missing"


def test_a_root_that_cannot_be_read_is_reported_and_left_alone(db, library, library2, tmp_path):
    _series(library, "Series A", 2)
    r1 = db.add_root(str(library))
    r2 = db.add_root(str(tmp_path / "offline"), "Offline share")
    handed = []
    result = scan_and_record_library(db.list_roots(), db, on_root=lambda rs, renamed: handed.append((rs.root_name, rs.error)))
    assert [h[0] for h in handed] == ["Manga", "Offline share"] and handed[0][1] is None and handed[1][1]
    assert result.errors and result.errors[0].startswith("Offline share:")
    assert [s.rel_path for s in db.list_series(r1.id)] == ["Series A"] and db.list_series(r2.id) == []


def test_the_mangapixer_pairing_is_refreshed_once_after_the_last_root(db, library, library2, monkeypatch):
    _series(library, "Series A", 1)
    _series(library2, "Series K", 1)
    db.add_root(str(library))
    db.add_root(str(library2))
    calls = []
    monkeypatch.setattr(scanner, "refresh_mangapixer_pairing", lambda d: calls.append(d) or True)
    order = []
    scan_and_record_library(db.list_roots(), db, on_root=lambda rs, renamed: order.append(len(calls)))
    assert order == [0, 0] and calls == [db]                  # not per root, once at the end

    calls.clear()
    scan_and_record_library(db.list_roots(), None)            # nothing to record into: no pairing either
    assert calls == []


def test_record_library_scan_still_pairs_by_default(db, library, monkeypatch):
    _series(library, "Series A", 1)
    db.add_root(str(library))
    calls = []
    monkeypatch.setattr(scanner, "refresh_mangapixer_pairing", lambda d: calls.append(d) or True)
    scanner.record_library_scan(db, scanner.scan_library(db.list_roots()))
    assert calls == [db]
    scanner.record_library_scan(db, scanner.scan_library(db.list_roots()), pair=False)
    assert calls == [db]


def test_stopping_between_folders_keeps_the_roots_already_recorded(db, library, library2):
    _series(library, "Series A", 1)
    _series(library2, "Series K", 1)
    r1, r2 = db.add_root(str(library)), db.add_root(str(library2))

    class Stop(Exception):
        pass

    def progress(done, total, name):
        if name.startswith("Completed:"):
            raise Stop()

    handed = []
    try:
        scan_and_record_library(db.list_roots(), db, progress=progress, on_root=lambda rs, renamed: handed.append(rs.root_name))
    except Stop:
        pass
    assert handed == ["Manga"]
    assert [s.rel_path for s in db.list_series(r1.id)] == ["Series A"] and db.list_series(r2.id) == []
