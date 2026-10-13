"""Research prototype (selective scanning): a scan of only the touched root folders records exactly what a full scan
records for them. Two identical synthetic libraries, one change each, one database scanned in full and the other
scanned folder by folder; every table must come out the same (ids, times and scan numbers left out). Not for merging."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from mangalist import store
from mangalist.identity.backfill import backfill_signatures
from mangalist.scanner import scan_and_record_folders, scan_and_record_library, scan_changed

from .conftest import make_archive


def build(base: Path) -> None:
    for i in (1, 2, 3):
        make_archive(base / "Manga" / "Alpha" / f"Alpha v{i:02d}.cbz", seed=f"a{i}", size=5000 + i)
    for i in (1, 2, 3, 4, 5):
        make_archive(base / "Manga" / "Beta" / f"Beta ch{i:03d}.cbz", seed=f"b{i}", size=6000 + i)
    for i in (1, 2):
        make_archive(base / "Manga" / "Gamma" / f"Gamma v{i:02d}.cbz", seed=f"g{i}", size=7000 + i)
    for i in (1, 2):
        make_archive(base / "Manga" / "Gamma" / "Gamma Spinoff" / f"Gamma Spinoff v{i:02d}.cbz", seed=f"gs{i}", size=7100 + i)
    for s in (1, 2):
        make_archive(base / "Manga" / "Delta" / f"Season {s}" / f"Delta ch{s:03d}.cbz", seed=f"d{s}", size=8000 + s)
    for i in (1, 2, 3):
        make_archive(base / "Completed" / "Omega" / f"Omega v{i:02d}.cbz", seed=f"o{i}", size=9000 + i)


def dump(store_: store.Store) -> dict:
    con = sqlite3.connect(store_.path)
    con.row_factory = sqlite3.Row
    sid = {r["id"]: r["rel_path"] for r in con.execute("SELECT id, rel_path FROM series")}
    root = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM roots")}
    out = {
        "series": sorted(json.dumps([root[r["root_id"]], r["rel_path"], r["fingerprint"], r["n_archives"], r["status"]])
                         for r in con.execute("SELECT * FROM series")),
        "archives": sorted(json.dumps([root[r["root_id"]], r["rel_path"], sid.get(r["series_id"]), r["size"], r["status"]])
                           for r in con.execute("SELECT * FROM archives")),
        "units": sorted(json.dumps([sid.get(r["series_id"]), r["rel_path"], r["seq"], r["kind"], r["vol_from"], r["ch_from"]])
                        for r in con.execute("SELECT * FROM units")),
        "moves": sorted(json.dumps([root.get(r["from_root_id"]), r["from_path"], root.get(r["to_root_id"]), r["to_path"],
                                    r["how"]]) for r in con.execute("SELECT * FROM archive_moves")),
        "carries": sorted(json.dumps([r["from_path"], r["to_path"], r["how"], r["moved"]])
                          for r in con.execute("SELECT * FROM series_carries")),
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

    def root(self, rid):
        return next(r for r in self.db.list_roots() if r.id == rid)

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


def _excluded(lib):
    lib.db.set_exclusions(lib.manga.id, ["Beta"])
    return {"Manga": ["Beta"]}


CASES = [_add_file, _delete_file, _rename_file, _new_series, _delete_series, _rename_series, _move_between_roots,
         _season_added, _subseries_changed, _subseries_folder_renamed, _franchise_to_top_level, _excluded]


@pytest.mark.parametrize("change", CASES, ids=lambda f: f.__name__.lstrip("_"))
@pytest.mark.parametrize("order", ["named", "diffpass"])
def test_selective_equals_full(tmp_path, change, order):
    full, part = Lib(tmp_path, "full"), Lib(tmp_path, "part")
    assert dump(full.db) == dump(part.db)
    change(full)
    names = change(part)
    scan_and_record_library(full.db.list_roots(), full.db)
    if order == "named":
        for root_name, tops in names.items():
            root = next(r for r in part.db.list_roots() if r.name == root_name)
            scan_and_record_folders(root, tops, part.db)
    else:
        scan_changed(part.db, part.db.list_roots())
    assert dump(full.db) == dump(part.db)


def test_the_rename_source_must_be_named(tmp_path):
    """Naming only the new folder name leaves the old rows present until a full scan (the demonstrated risk)."""
    full, part = Lib(tmp_path, "full"), Lib(tmp_path, "part")
    _rename_series(full)
    _rename_series(part)
    scan_and_record_library(full.db.list_roots(), full.db)
    scan_and_record_folders(part.root(part.manga.id), ["Alpha Renamed"], part.db)
    assert dump(full.db) != dump(part.db)
    assert part.db.get_series(part.manga.id, "Alpha").status == "present"      # stale: still believed to be there
    scan_and_record_library(part.db.list_roots(), part.db)
    assert dump(full.db) == dump(part.db)                                         # the next full scan puts it right
