"""The find pass does not search a series nested in another series' folder by the names of that folder's series (owner,
2026-10-18: a gaiden nested in its parent's folder was searched by the parent's names, and the parent's pack
"Parent v01-20" was sent into the gaiden's folder). Made-up names, a fake nyaa, no network."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from mangalist import mu_cache
from mangalist.downloads import spinoffs
from mangalist.downloads.finder import run_find
from mangalist.downloads.finding import TRIGGER_NOW
from mangalist.downloads.options import KEY_PARTIAL_DOWNLOADS, set_flag
from mangalist.gui.shell import GROUP_VOLUMES, WantedSeries
from mangalist.services.nyaa import NyaaSearch
from mangalist.store.downloads import DownloadLedger
from mangalist.store.findings import FindingStore
from mangalist.store.mangapixer import MangaPixerCache, Mapping

from ..services.nyaa.conftest import FakeClient, feed, item
from ..states.helpers import item as mp_item
from .fakes import data, scan
from .finder_fakes import wire

PARENT = "Parent Saga [Parent Saga EN]"
GAIDEN = f"{PARENT}/Parent Saga Gaiden - Side Buddy"
GAIDEN_TITLE = "Parent Saga Gaiden - Side Buddy"


def _write(root: Path, rel: str, name: str) -> None:
    folder = root.joinpath(*rel.split("/"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data(name))


@pytest.fixture
def nested(db, tmp_path):
    """(root, {rel path: series id}) of a parent series holding a nested spin-off, plus a series of its own."""
    root_dir = tmp_path / "library" / "Manga"
    _write(root_dir, PARENT, "Parent Saga v01 (Digital).cbz")
    _write(root_dir, GAIDEN, "Parent Saga Gaiden - Side Buddy v01 (Digital).cbz")
    _write(root_dir, "Re Example", "Re Example v01 (Digital).cbz")
    root = db.add_root(str(root_dir), "Manga")
    scan(db)
    ids = {s.rel_path: s.id for s in db.list_series(root.id)}
    assert set(ids) == {PARENT, GAIDEN, "Re Example"}
    return root, ids


def _entry(sid: int, folder: str, titles, missing=("2", "3")) -> WantedSeries:
    return WantedSeries(series_id=sid, folder=folder, title=titles[0], group=GROUP_VOLUMES, gaps="",
                        missing=tuple(missing), held=("1",), titles=tuple(titles), findable=True)


def _folder(root, rel: str) -> str:
    return os.path.join(root.path, *rel.split("/"))


# --- the titles asked ----------------------------------------------------------------------------------------------

def test_a_nested_series_is_not_searched_by_its_parents_names(db, nested, tmp_path, caplog):
    root, ids = nested
    w = wire(db, (ids[GAIDEN], Path(_folder(root, GAIDEN))), tmp_path)
    # what the inherited MangaPixer item gave the spin-off: its folder name, then the parent's names
    w.entries = [_entry(ids[GAIDEN], _folder(root, GAIDEN), (GAIDEN_TITLE, "Parent Saga", "Parent Saga EN"))]
    with caplog.at_level(logging.INFO, logger="mangalist.downloads.spinoffs"):
        run_find(db, trigger=TRIGGER_NOW, wanted=w.wanted, backend=w.backend)
    assert [c[0] for c in w.search.calls] == [(GAIDEN_TITLE,)]
    assert "not searched by 2 names of the series its folder is in" in caplog.text


def test_the_parents_pack_is_not_taken_for_the_spin_off(db, nested, tmp_path):
    root, ids = nested
    w = wire(db, (ids[GAIDEN], Path(_folder(root, GAIDEN))), tmp_path)
    w.entries = [_entry(ids[GAIDEN], _folder(root, GAIDEN), (GAIDEN_TITLE, "Parent Saga", "Parent Saga EN"),
                        missing=[str(n) for n in range(2, 21)])]
    client = FakeClient({"parent saga": feed(item("Parent Saga v01-20 (2011-2026) (Digital) (Group)", seeders=40)),
                         "parent saga en": feed(item("Parent Saga EN v01-20 (Digital) (Group)", seeders=40))})
    w.search.search = lambda titles, missing, held: NyaaSearch(client).search(titles, missing, held)
    FindingStore(db).set_mode([ids[GAIDEN]], "auto")
    set_flag(db, KEY_PARTIAL_DOWNLOADS, False)          # a pack then waits as a finding without its file list read
    report = run_find(db, trigger=TRIGGER_NOW, wanted=w.wanted, backend=w.backend)
    assert [q for q, _ in client.calls] == ["Parent Saga Gaiden Side Buddy"]
    assert report.sent == 0 and report.found == 0
    assert FindingStore(db).list(None, ids[GAIDEN]) == [] and DownloadLedger(db).all_records() == []


def test_a_series_not_nested_keeps_every_title(db, nested):
    # an English title that is a prefix of the longer romaji one is the same series: nothing is dropped
    root, ids = nested
    titles = ("Re Example", "Re Example kara Hajimeru Example Seikatsu", "Parent Saga")
    assert spinoffs.search_titles(db, ids["Re Example"], titles) == titles
    assert spinoffs.search_titles(db, ids[PARENT], ("Parent Saga EN", "Parent Saga")) == ("Parent Saga EN", "Parent Saga")


def test_the_spin_offs_own_names_are_kept(db, nested):
    root, ids = nested
    titles = ("Parent Saga EN: Side Buddy", GAIDEN_TITLE, "Parent Saga Gaiden: Side Body", "PARENT SAGA!", "Parent-Saga")
    assert spinoffs.search_titles(db, ids[GAIDEN], titles) == titles[:3]


def test_the_parents_names_come_from_its_folder_its_mangaupdates_link_and_mangapixer(db, nested):
    root, ids = nested
    parent = Path(root.path).resolve().joinpath(PARENT)
    mu_cache.save_entry(parent, 7001, "Parent Saga Romaji", "https://mu.example/s/7001", licensed=True,
                        mu_confirmed=True, mu_associated=["Parent Saga Associated"])
    cache = MangaPixerCache(db)

    class _Lib:
        id, display_name, kind = "lib0", "Manga", "manga"
        folder_count = item_count = last_scan_at = None

    cache.save_libraries([_Lib()])
    cache.apply_page("lib0", [], [mp_item(nodeId="p1", trail=[PARENT], record__title="Parent Saga MP",
                                          record__altTitles=["Parent Saga MP Alt"])])
    cache.save_mapping(Mapping(root.id, "lib0", [], manual=True))
    names = set(spinoffs.enclosing_names(db, ids[GAIDEN]))
    assert {"Parent Saga", "Parent Saga EN", "Parent Saga Romaji", "Parent Saga Associated", "Parent Saga MP",
            "Parent Saga MP Alt"} <= names
    assert GAIDEN_TITLE not in names and spinoffs.enclosing_names(db, ids[PARENT]) == ()


def test_a_folder_named_like_the_one_it_is_in_keeps_its_name(db, tmp_path):
    root_dir = tmp_path / "library" / "Manga"
    _write(root_dir, "Twin Saga", "Twin Saga v01 (Digital).cbz")
    _write(root_dir, "Twin Saga/Twin Saga", "Twin Saga v02 (Digital).cbz")
    root = db.add_root(str(root_dir), "Manga")
    scan(db)
    inner = db.get_series(root.id, "Twin Saga/Twin Saga")
    assert spinoffs.search_titles(db, inner.id, ("Twin Saga", "Twin Saga EN")) == ("Twin Saga", "Twin Saga EN")


def test_an_unknown_series_keeps_its_titles(db):
    assert spinoffs.search_titles(db, 999, ("A", "B")) == ("A", "B")
