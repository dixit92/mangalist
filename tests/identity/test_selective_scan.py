"""Selective scanning (Stage 1): a scan of only the touched root folders records exactly what a full scan records.

Two identical synthetic libraries get the same change; one database is scanned in full, the other only in the named
folders (both sides of a rename or move); every table must come out the same (ids, times and scan numbers left out).
Plus: unchanged rows are not rewritten, ``last_seen_scan`` is exact once a row goes missing, the scope helpers."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from mangalist import store
from mangalist.identity.backfill import backfill_signatures
from mangalist.scanner import (
    folder_scopes,
    journal_folders_since,
    journal_mark,
    merge_scopes,
    scan_and_record_folders,
    scan_and_record_library,
    series_scopes,
    widen_scopes,
)

from .conftest import make_archive


def build(base: Path) -> None:
    for i in (1, 2, 3):
        make_archive(base / "Manga" / "Alpha" / f"Alpha v{i:02d}.cbz", seed=f"a{i}", size=5000 + i)
    for i in (1, 2, 3, 4, 5):
        make_archive(base / "Manga" / "Beta" / f"Beta ch{i:03d}.cbz", seed=f"b{i}", size=6000 + i)
    for i in (1, 2):
        make_archive(base / "Manga" / "Gamma" / f"Gamma v{i:02d}.cbz", seed=f"g{i}", size=7000 + i)
    for i in (1, 2):
        make_archive(base / "Manga" / "Gamma" / "Gamma Spinoff" / f"Gamma Spinoff v{i:02d}.cbz", seed=f"gs{i}",
                     size=7100 + i)
    for s in (1, 2):
        make_archive(base / "Manga" / "Delta" / f"Season {s}" / f"Delta ch{s:03d}.cbz", seed=f"d{s}", size=8000 + s)
    for i in (1, 2, 3):
        make_archive(base / "Completed" / "Omega" / f"Omega v{i:02d}.cbz", seed=f"o{i}", size=9000 + i)
    make_archive(base / "Completed" / "Sigma" / "Sigma v01.cbz", seed="s1", size=9500)


def dump(store_: store.Store) -> dict:
    con = sqlite3.connect(store_.path)
    con.row_factory = sqlite3.Row
    sid = {r["id"]: r["rel_path"] for r in con.execute("SELECT id, rel_path FROM series")}
    root = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM roots")}
    out = {
        "series": sorted(json.dumps([root[r["root_id"]], r["rel_path"], r["fingerprint"], r["n_archives"], r["status"],
                                     r["kind_hint"], r["mu_id"]]) for r in con.execute("SELECT * FROM series")),
        "archives": sorted(json.dumps([root[r["root_id"]], r["rel_path"], sid.get(r["series_id"]), r["size"],
                                       r["status"], r["signature"]]) for r in con.execute("SELECT * FROM archives")),
        "units": sorted(json.dumps([sid.get(r["series_id"]), r["rel_path"], r["seq"], r["kind"], r["vol_from"],
                                    r["ch_from"]]) for r in con.execute("SELECT * FROM units")),
        "moves": sorted(json.dumps([root.get(r["from_root_id"]), r["from_path"], root.get(r["to_root_id"]),
                                    r["to_path"], r["how"]]) for r in con.execute("SELECT * FROM archive_moves")),
        "carries": sorted(json.dumps([r["from_path"], r["to_path"], r["how"], r["moved"]])
                          for r in con.execute("SELECT * FROM series_carries")),
        "links": sorted(json.dumps([Path(r["folder"]).name, r["mu_id"]]) for r in
                        con.execute("SELECT folder, mu_id FROM links_cache")),
    }
    con.close()
    return out


class Lib:
    def __init__(self, tmp: Path, name: str):
        self.base = tmp / name
        build(self.base)
        self.db = store.Store(tmp / f"{name}.db", import_legacy=False)
        self.manga = self.db.add_root(str(self.base / "Manga"), "Manga")
        self.done = self.db.add_root(str(self.base / "Completed"), "Completed")
        scan_and_record_library(self.db.list_roots(), self.db)
        backfill_signatures(self.db, per_file_delay=0)

    def root(self, name):
        return next(r for r in self.db.list_roots() if r.name == name)

    def p(self, root_name, *parts):
        return self.base.joinpath(root_name, *parts)


def _add_file(lib):
    make_archive(lib.p("Manga", "Alpha", "Alpha v04.cbz"), seed="a4", size=5004)
    return {"Manga": ["Alpha"]}


def _delete_file(lib):
    lib.p("Manga", "Beta", "Beta ch003.cbz").unlink()
    return {"Manga": ["Beta"]}


def _rename_file(lib):
    lib.p("Manga", "Alpha", "Alpha v01.cbz").rename(lib.p("Manga", "Alpha", "Alpha - Volume 1.cbz"))
    return {"Manga": ["Alpha"]}


def _change_in_place(lib):
    make_archive(lib.p("Manga", "Beta", "Beta ch002.cbz"), seed="b2-new", size=6102)
    return {"Manga": ["Beta"]}


def _new_series(lib):
    make_archive(lib.p("Manga", "Epsilon", "Epsilon v01.cbz"), seed="e1", size=5500)
    return {"Manga": ["Epsilon"]}


def _delete_series(lib):
    shutil.rmtree(lib.p("Manga", "Beta"))
    return {"Manga": ["Beta"]}


def _rename_series(lib):
    lib.p("Manga", "Alpha").rename(lib.p("Manga", "Alpha Renamed"))
    return {"Manga": ["Alpha", "Alpha Renamed"]}


def _move_between_roots(lib):
    lib.p("Completed", "Omega").rename(lib.p("Manga", "Omega"))
    return {"Manga": ["Omega"], "Completed": ["Omega"]}


def _season_added(lib):
    make_archive(lib.p("Manga", "Delta", "Season 3", "Delta ch003.cbz"), seed="d3", size=8003)
    return {"Manga": ["Delta"]}


def _subseries_changed(lib):
    make_archive(lib.p("Manga", "Gamma", "Gamma Spinoff", "Gamma Spinoff v03.cbz"), seed="gs3", size=7103)
    lib.p("Manga", "Gamma", "Gamma v01.cbz").unlink()
    return {"Manga": ["Gamma"]}


def _subseries_folder_renamed(lib):
    lib.p("Manga", "Gamma", "Gamma Spinoff").rename(lib.p("Manga", "Gamma", "Gamma Side Story"))
    return {"Manga": ["Gamma"]}


def _franchise_to_top_level(lib):
    lib.p("Manga", "Gamma", "Gamma Spinoff").rename(lib.p("Manga", "Gamma Spinoff"))
    return {"Manga": ["Gamma", "Gamma Spinoff"]}


def _file_moved_between_series(lib):
    lib.p("Manga", "Beta", "Beta ch005.cbz").rename(lib.p("Manga", "Alpha", "Beta ch005.cbz"))
    return {"Manga": ["Alpha", "Beta"]}


def _excluded(lib):
    lib.db.set_exclusions(lib.manga.id, ["Beta"])
    return {"Manga": ["Beta"]}


def _kind_answered(lib):
    lib.db.set_series_kind(lib.manga.id, "Beta", "volumes")
    return {"Manga": ["Beta"]}


CASES = [_add_file, _delete_file, _rename_file, _change_in_place, _new_series, _delete_series, _rename_series,
         _move_between_roots, _season_added, _subseries_changed, _subseries_folder_renamed, _franchise_to_top_level,
         _file_moved_between_series, _excluded, _kind_answered]


@pytest.mark.parametrize("change", CASES, ids=lambda f: f.__name__.lstrip("_"))
def test_a_folder_scan_records_what_a_full_scan_records(tmp_path, change):
    full, part = Lib(tmp_path, "full"), Lib(tmp_path, "part")
    assert dump(full.db) == dump(part.db)
    change(full)
    names = change(part)
    scan_and_record_library(full.db.list_roots(), full.db)
    for root_name, tops in names.items():
        scan_and_record_folders(part.root(root_name), tops, part.db)
    assert dump(full.db) == dump(part.db)


@pytest.mark.parametrize("change", CASES, ids=lambda f: f.__name__.lstrip("_"))
def test_a_scope_over_several_roots_in_one_call_equals_a_full_scan(tmp_path, change):
    full, part = Lib(tmp_path, "full"), Lib(tmp_path, "part")
    change(full)
    names = change(part)
    scan_and_record_library(full.db.list_roots(), full.db)
    scopes = {part.root(n).id: tops for n, tops in names.items()}
    roots = [r for r in part.db.list_roots() if r.id in scopes]
    result = scan_and_record_library(roots, part.db, scopes=scopes)
    assert result.folders_only
    assert dump(full.db) == dump(part.db)


def test_the_rename_source_must_be_named(tmp_path):
    """Naming only the new folder name leaves the old rows present until a full scan (the known risk: a delay, never a
    loss)."""
    full, part = Lib(tmp_path, "full"), Lib(tmp_path, "part")
    _rename_series(full)
    _rename_series(part)
    scan_and_record_library(full.db.list_roots(), full.db)
    scan_and_record_folders(part.root("Manga"), ["Alpha Renamed"], part.db)
    assert dump(full.db) != dump(part.db)
    assert part.db.get_series(part.manga.id, "Alpha").status == "present"      # stale: still believed to be there
    scan_and_record_library(part.db.list_roots(), part.db)
    a, b = dump(full.db), dump(part.db)
    for d in (a, b):                    # recognised later it is "pairing", not "scan": the same move, the same rows
        d["moves"] = sorted(json.dumps(json.loads(m)[:4]) for m in d["moves"])
    assert a == b                                                                 # the next full scan puts it right


def test_a_folder_scan_touches_nothing_outside_its_folders(tmp_path):
    lib = Lib(tmp_path, "lib")
    shutil.rmtree(lib.p("Manga", "Beta"))                                       # gone, but not in the scope
    make_archive(lib.p("Manga", "Alpha", "Alpha v04.cbz"), seed="a4", size=5004)
    result = scan_and_record_folders(lib.root("Manga"), ["Alpha"], lib.db)
    assert [Path(e.folder).name for e in result.entries] == ["Alpha"] and result.loose == []
    assert lib.db.get_series(lib.manga.id, "Beta").status == "present"
    assert all(a.status == "present" for a in lib.db.list_archives(root_id=lib.manga.id) if a.rel_path.startswith("Beta/"))
    assert lib.db.archive_at(lib.manga.id, "Alpha/Alpha v04.cbz").status == "present"


def test_a_name_that_is_not_one_folder_is_ignored(tmp_path):
    lib = Lib(tmp_path, "lib")
    before = dump(lib.db)
    result = scan_and_record_folders(lib.root("Manga"), ["..", "Alpha/..", ""], lib.db)
    assert result.roots[0].scope == frozenset() and dump(lib.db) == before


def test_an_empty_root_is_not_read_as_every_named_folder_gone(tmp_path):
    """An offline share leaves its mountpoint empty: a folder scan must not mark the named series missing."""
    lib = Lib(tmp_path, "lib")
    shutil.rmtree(lib.p("Completed"))
    lib.p("Completed").mkdir()                          # (a root whose every folder left looks the same: a delay)
    result = scan_and_record_folders(lib.root("Completed"), ["Omega"], lib.db)
    assert result.errors and lib.db.get_series(lib.done.id, "Omega").status == "present"


# --- Stage 0: only changed rows are written; last_seen_scan stays exact -------------------------------------------

def test_recording_an_unchanged_library_writes_no_archive_row(tmp_path):
    lib = Lib(tmp_path, "lib")
    con = sqlite3.connect(lib.db.path)
    before = con.execute("SELECT id, last_seen_scan, last_seen_at FROM archives ORDER BY id").fetchall()
    con.close()
    changes = []

    real = lib.db.connect

    def counting(*a, **kw):
        cm = real(*a, **kw)

        class Wrap:
            def __enter__(self_inner):
                self_inner.con = cm.__enter__()
                self_inner.start = self_inner.con.total_changes
                return self_inner.con

            def __exit__(self_inner, *exc):
                changes.append(self_inner.con.total_changes - self_inner.start)
                return cm.__exit__(*exc)

        return Wrap()

    from mangalist.identity.moves import record_archives
    from mangalist.scanner import scan_library

    result = scan_library(lib.db.list_roots(), db=lib.db)
    lib.db.connect = counting
    try:
        rep = record_archives(lib.db, [(rs.root_id, rs.folder, rs.entries) for rs in result.roots])
    finally:
        lib.db.connect = real
    assert rep.archives_seen == 18 and rep.archives_new == 0 and rep.archives_missing == 0
    # the scan revision and the two "covered" marks; not one archive row
    assert sum(changes) == 3
    con = sqlite3.connect(lib.db.path)
    assert con.execute("SELECT id, last_seen_scan, last_seen_at FROM archives ORDER BY id").fetchall() == before
    con.close()


def _row(db, root_id, rel):
    con = sqlite3.connect(db.path)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM archives WHERE root_id = ? AND rel_path = ?", (root_id, rel)).fetchone()
    con.close()
    return r


def test_a_row_that_goes_missing_is_stamped_with_the_last_scan_that_saw_it(tmp_path):
    lib = Lib(tmp_path, "lib")
    first = _row(lib.db, lib.manga.id, "Beta/Beta ch003.cbz")["last_seen_scan"]
    for _ in range(3):                                  # unchanged: the row is not written, its stamp stays
        scan_and_record_library(lib.db.list_roots(), lib.db)
    scan_and_record_folders(lib.root("Manga"), ["Alpha"], lib.db)               # covers another folder only
    assert _row(lib.db, lib.manga.id, "Beta/Beta ch003.cbz")["last_seen_scan"] == first
    con = sqlite3.connect(lib.db.path)
    last_whole = max(int(v.split()[0]) for (k, v) in con.execute("SELECT key, value FROM meta WHERE key = ?",
                                                                  (f"identity.covered.{lib.manga.id}",)))
    con.close()
    lib.p("Manga", "Beta", "Beta ch003.cbz").unlink()
    scan_and_record_folders(lib.root("Manga"), ["Beta"], lib.db)
    gone = _row(lib.db, lib.manga.id, "Beta/Beta ch003.cbz")
    assert gone["status"] == "missing" and gone["last_seen_scan"] == last_whole > first


def test_a_folder_scan_covers_its_folder_for_the_last_seen_stamp(tmp_path):
    lib = Lib(tmp_path, "lib")
    scan_and_record_folders(lib.root("Manga"), ["Beta"], lib.db)
    con = sqlite3.connect(lib.db.path)
    covered = int(con.execute("SELECT value FROM meta WHERE key = ?",
                              (f"identity.covered.{lib.manga.id}:Beta",)).fetchone()[0].split()[0])
    con.close()
    lib.p("Manga", "Beta", "Beta ch001.cbz").unlink()
    scan_and_record_library(lib.db.list_roots(), lib.db)
    assert _row(lib.db, lib.manga.id, "Beta/Beta ch001.cbz")["last_seen_scan"] == covered
    con = sqlite3.connect(lib.db.path)                  # the whole-root record supersedes the folder marks
    assert con.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'identity.covered.%:%'").fetchone()[0] == 0
    con.close()


def test_a_copy_that_lived_beside_a_deleted_archive_is_not_taken_for_its_move(tmp_path):
    """The copy-elsewhere rule needs an exact last_seen_scan: an identical copy filed while the original was still
    there, then the original deleted, is a deletion - not "the original moved to the copy's path" - even after many
    scans that did not rewrite the original's row."""
    lib = Lib(tmp_path, "lib")
    make_archive(lib.p("Completed", "Omega", "Alpha v02 copy.cbz"), seed="a2", size=5002)     # same bytes as Alpha v02
    scan_and_record_library(lib.db.list_roots(), lib.db)
    backfill_signatures(lib.db, per_file_delay=0)
    for _ in range(3):
        scan_and_record_library(lib.db.list_roots(), lib.db)
    lib.p("Manga", "Alpha", "Alpha v02.cbz").unlink()
    scan_and_record_library(lib.db.list_roots(), lib.db)
    backfill_signatures(lib.db, per_file_delay=0)
    gone = _row(lib.db, lib.manga.id, "Alpha/Alpha v02.cbz")
    copy = _row(lib.db, lib.done.id, "Omega/Alpha v02 copy.cbz")
    assert gone["status"] == "missing" and copy["status"] == "present" and gone["id"] != copy["id"]
    con = sqlite3.connect(lib.db.path)
    assert con.execute("SELECT COUNT(*) FROM archive_moves").fetchone()[0] == 0
    con.close()


def test_a_move_found_after_the_fact_still_pairs_when_the_destination_was_read_first(tmp_path):
    """Root by root, the destination root is recorded before the source root: the archives that vanish later pair with
    the live rows after the fact (their last-seen stamp is the source root's previous record, not this pass's)."""
    lib = Lib(tmp_path, "lib")
    for _ in range(2):
        scan_and_record_library(lib.db.list_roots(), lib.db)
    sid = lib.db.get_series(lib.done.id, "Omega").id
    lib.p("Completed", "Omega").rename(lib.p("Manga", "Omega"))
    scan_and_record_library([lib.root("Manga"), lib.root("Completed")], lib.db)
    assert lib.db.get_series(lib.manga.id, "Omega").id == sid
    assert lib.db.get_series(lib.done.id, "Omega") is None


# --- the MangaPixer pairing refresh -----------------------------------------------------------------------------

def test_a_folder_scan_refreshes_the_pairing_only_when_a_series_row_changed(tmp_path, monkeypatch):
    from mangalist import scanner

    lib = Lib(tmp_path, "lib")
    calls = []
    monkeypatch.setattr(scanner, "refresh_mangapixer_pairing", lambda db: calls.append(1) or True)
    make_archive(lib.p("Manga", "Alpha", "Alpha v04.cbz"), seed="a4", size=5004)
    scan_and_record_folders(lib.root("Manga"), ["Alpha"], lib.db)
    assert calls == []                                                          # a file more: the counts stand
    make_archive(lib.p("Manga", "Epsilon", "Epsilon v01.cbz"), seed="e1", size=5500)
    scan_and_record_folders(lib.root("Manga"), ["Epsilon"], lib.db)
    assert calls == [1]                                                         # a new series row
    scan_and_record_library([lib.root("Manga")], lib.db)
    assert calls == [1, 1]                                                      # a whole root: always


# --- the scope helpers ------------------------------------------------------------------------------------------

def test_folder_scopes_name_each_paths_top_folder_and_never_every_root(tmp_path):
    lib = Lib(tmp_path, "lib")
    roots = lib.db.list_roots()
    scopes = folder_scopes(roots, [lib.p("Manga", "Gamma", "Gamma Spinoff", "x.cbz"), lib.p("Manga", "Alpha"),
                                   lib.p("Completed", "Omega", "Omega v01.cbz"), tmp_path / "elsewhere" / "y.cbz"])
    assert scopes == {lib.manga.id: {"Gamma", "Alpha"}, lib.done.id: {"Omega"}}
    assert folder_scopes(roots, []) == {}
    assert folder_scopes(roots, [tmp_path / "elsewhere"]) == {}
    assert folder_scopes(roots, [lib.p("Manga"), lib.p("Manga", "Alpha")]) == {lib.manga.id: None}


def test_series_scopes_merge_and_widen(tmp_path):
    lib = Lib(tmp_path, "lib")
    spin = lib.db.get_series(lib.manga.id, "Gamma/Gamma Spinoff").id
    omega = lib.db.get_series(lib.done.id, "Omega").id
    assert series_scopes(lib.db, [spin, omega, 99999, None]) == {lib.manga.id: {"Gamma"}, lib.done.id: {"Omega"}}
    assert merge_scopes({1: {"A"}}, {1: {"B"}, 2: None}, {2: {"C"}}, None) == {1: {"A", "B"}, 2: None}
    assert widen_scopes(lib.db, {lib.manga.id: {"Alpha", "Beta"}}) == {lib.manga.id: {"Alpha", "Beta"}}
    assert widen_scopes(lib.db, {lib.manga.id: {"Alpha", "Beta"}}, minimum=1) == {lib.manga.id: None}
    assert widen_scopes(lib.db, {lib.manga.id: None, lib.done.id: {"Omega"}}) == {lib.manga.id: None,
                                                                                  lib.done.id: {"Omega"}}


def test_the_journal_names_both_sides_of_what_mangalist_moved(tmp_path):
    from mangalist.store.journal import Journal

    lib = Lib(tmp_path, "lib")
    paths, mark = journal_folders_since(lib.db, None)
    assert paths == [] and mark == journal_mark(lib.db)
    journal = Journal(lib.db)
    src = lib.p("Manga", "Beta", "Beta ch005.cbz")
    dst = lib.p("Manga", "Alpha", "Beta ch005.cbz")
    plan = journal.plan("manual", [(str(src), str(dst))], root_path=str(lib.p("Manga")))
    journal.apply(plan.id)
    paths, mark2 = journal_folders_since(lib.db, mark)
    assert folder_scopes(lib.db.list_roots(), paths) == {lib.manga.id: {"Alpha", "Beta"}}
    assert journal_folders_since(lib.db, mark2)[0] == []                         # nothing new since
