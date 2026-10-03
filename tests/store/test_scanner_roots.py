"""The scanner with roots: several roots, exclusions never scanned, loose archives reported."""

from __future__ import annotations

from pathlib import Path

from mangalist import scanner
from mangalist.scanner import scan_library, scan_root

from .conftest import make_archive


def _names(entries):
    return sorted(e.folder.name for e in entries)


def test_exclusions_are_never_scanned(library, monkeypatch):
    make_archive(library / "Series A" / "Series A v01.cbz")
    make_archive(library / "Series A" / "Raw" / "raw 01.cbz")
    make_archive(library / "@Oneshots" / "One Shot.cbz")
    make_archive(library / "Series B" / "Series B c001.cbz")
    make_archive(library / "Series B" / "Series B c002.zip")

    walked = []
    real_walk = scanner.os.walk

    def spy(top, *a, **k):
        for dirpath, dirnames, filenames in real_walk(top, *a, **k):
            walked.append(Path(dirpath).name)
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(scanner.os, "walk", spy)
    entries = scan_root(library, exclusions=["@Oneshots/**", "Series A/Raw", "*.zip"])
    assert _names(entries) == ["Series A", "Series B"]
    files = {e.folder.name: sorted(f.path.name for f in e.files) for e in entries}
    assert files == {"Series A": ["Series A v01.cbz"], "Series B": ["Series B c001.cbz"]}
    assert "@Oneshots" not in walked and "Raw" not in walked          # never entered
    assert {e.folder.name: e.n_subfolders for e in entries}["Series A"] == 0


def test_without_exclusions_behaviour_is_unchanged(library):
    make_archive(library / "Series A" / "Raw" / "raw 01.cbz")
    make_archive(library / "Series A" / "Series A v01.cbz")
    entries = scan_root(library)  # a parent with its own files and a subseries folder: both, as before
    assert [(e.folder.name, len(e.files)) for e in entries] == [("Series A", 2), ("Raw", 1)]
    assert entries[0].root_id is None


def test_loose_archives_are_reported_not_matched(library):
    make_archive(library / "Lost Volume v01.cbz")
    make_archive(library / "notes.txt")
    make_archive(library / "ignored.cbr")
    make_archive(library / "Series A" / "a.cbz")
    loose = []
    entries = scan_root(library, loose=loose, exclusions=["ignored.cbr"])
    assert _names(entries) == ["Series A"]
    assert [p.name for p in loose] == ["Lost Volume v01.cbz"]


def test_franchise_subseries_respect_exclusions(library):
    make_archive(library / "Franchise" / "Part One" / "p1 v01.cbz")
    make_archive(library / "Franchise" / "Part Two" / "p2 v01.cbz")
    entries = scan_root(library, exclusions=["Franchise/Part Two"])
    assert _names(entries) == ["Part One"]


def test_scan_library_over_several_roots(db, tmp_path):
    manga, manhwa = tmp_path / "lib" / "Manga", tmp_path / "lib" / "Manhwa"
    make_archive(manga / "Series A" / "a v01.cbz")
    make_archive(manga / "@Oneshots" / "x.cbz")
    make_archive(manhwa / "Series K" / "k c001.cbz")
    make_archive(manhwa / "Stray c001.cbz")
    r1 = db.add_root(str(manga), exclusions=["@Oneshots"])
    r2 = db.add_root(str(manhwa), origin_hint="manhwa")
    r3 = db.add_root(str(tmp_path / "lib" / "Offline"), "Offline share")
    seen = []
    result = scan_library(db.list_roots(), progress=lambda d, t, n: seen.append(n))
    assert _names(result.entries) == ["Series A", "Series K"]
    assert {e.folder.name: e.root_id for e in result.entries} == {"Series A": r1.id, "Series K": r2.id}
    assert [(la.root_id, la.path.name) for la in result.loose] == [(r2.id, "Stray c001.cbz")]
    assert len(result.errors) == 1 and result.errors[0].startswith("Offline share:")
    assert [r.root_id for r in result.roots] == [r1.id, r2.id, r3.id]
    assert "Manga: Series A" in seen


def test_record_library_scan_relinks_a_folder_renamed_by_hand(db, library):
    from mangalist import mu_cache
    from mangalist.scanner import record_library_scan

    make_archive(library / "Old Title" / "v01.cbz", 101)
    make_archive(library / "Old Title" / "v02.cbz", 102)
    root = db.add_root(str(library))
    first = scan_library(db.list_roots())
    assert record_library_scan(db, first) == []
    folder = first.entries[0].folder
    mu_cache.save_entry(folder, 11, "Linked", "", None, mu_confirmed=True)

    (library / "Old Title").rename(library / "New Title")             # the owner renames it in Explorer
    second = scan_library(db.list_roots())
    renamed = record_library_scan(db, second)
    assert renamed == [(folder, second.entries[0].folder)]
    assert mu_cache.load_entry(second.entries[0].folder)["mu_id"] == 11
    assert db.get_series(root.id, "New Title").mu_confirmed


def test_an_offline_root_keeps_its_series(db, library):
    from mangalist.scanner import record_library_scan

    make_archive(library / "Series A" / "a.cbz")
    root = db.add_root(str(library))
    record_library_scan(db, scan_library(db.list_roots()))
    (library / "Series A" / "a.cbz").unlink()
    (library / "Series A").rmdir()
    library.rmdir()
    record_library_scan(db, scan_library(db.list_roots()))
    assert db.get_series(root.id, "Series A").status == "present"
