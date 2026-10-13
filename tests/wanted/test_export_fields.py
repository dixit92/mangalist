"""The wanted list with MangaPixer 1.42's export fields: a declared edition's volumes are listed but never searched,
a folder with "Track completion: off" leaves To get, and MangaDex's chapters settle a series MangaList cannot tell.
A made-up library (titles, ids and paths invented)."""

from __future__ import annotations

import pytest

from mangalist import store, wanted
from mangalist.states import State
from mangalist.store.mangapixer import Mapping, MangaPixerCache

from ..states.helpers import TODAY, item, vol
from .library import LIB_ID, _Lib, _write, scan

SERIES = {
    "Edition Folder": [f"Edition Folder v0{n}.cbz" for n in (1, 2, 4)],         # master edition of 6
    "Untracked": ["Untracked v01.cbz"],                                         # would miss vol. 2-3
    "Untracked Empty": [],
    "Quiet Scanlation": [f"Quiet Scanlation c{n:03d}.cbz" for n in range(1, 4)],  # no latest chapter; MangaDex: 6
}


def _items():
    master = {"answer": "MissingSome", "reason": "None", "upgradeAvailable": False, "upgradeVolumes": [],
              "computedAt": "2026-10-12T12:00:00.000Z", "volumeTotalOverride": 6, "edition": "master"}
    # The regular edition's list maps volume 1 to chapters 1-8: an edition folder must not inherit that coverage.
    regular = {"items": [vol(str(n), str(8 * n - 7), str(8 * n), "2025-01-01", "released") for n in range(1, 11)]}
    quiet = item(nodeId="n04", trail=["Quiet Scanlation"], record__licensedEn=False, record__englishPublishers=[],
                 record__latestChapter=None, volumes=None, completion=None)
    quiet["companions"]["mangadexReleased"] = {"language": "en", "latestChapter": "6.5", "chapters": 6,
                                               "fetchedAt": "2026-10-12T12:00:00.000Z"}
    return [
        item(nodeId="n01", trail=["Edition Folder"], completion=master, volumes=regular),
        item(nodeId="n02", trail=["Untracked"], tracking="off", completion=None),
        item(nodeId="n03", trail=["Untracked Empty"], tracking="off", completion=None),
        quiet,
    ]


@pytest.fixture
def lib(tmp_path):
    store.reset_stores()
    db = store.get_store()
    for rel, names in SERIES.items():
        _write(tmp_path / "Manga" / rel, names)
    root = db.add_root(str(tmp_path / "Manga"), "Manga")
    cache = MangaPixerCache(db)
    cache.save_libraries([_Lib(LIB_ID, "Manga")])
    cache.apply_page(LIB_ID, [], _items())
    cache.save_mapping(Mapping(root.id, LIB_ID, [], manual=True))
    scan(db, db.list_roots())
    return db


def _views(db):
    return {v.entry.folder.name: v for v in wanted.library_states(db, today=TODAY)}


def _wanted(db, **kw):
    return {(w.title, w.group): w for w in wanted.wanted_list(db, today=TODAY, **kw)}


def test_an_edition_folder_is_counted_by_its_own_volumes(lib):
    v = _views(lib)["Edition Folder"]
    assert v.state.state == State.MISSING_VOLUMES and v.state.missing_volumes == ("3", "5", "6")
    assert v.state.edition == "Master edition" and v.knowledge.volumes == ()
    # inventory_of left the regular list out: volume 1 covers no chapter of it
    assert not tuple(wanted.inventory_of(v.entry, wanted.mangapixer_item(
        wanted.open_resolver(lib), v.row.root_id, lib.get_root(v.row.root_id).path, v.entry.folder))
        .chapters_covered_by_volumes)


def test_an_edition_folders_volumes_are_in_to_get_but_never_searched(lib):
    for downloads in (True, False):
        w = _wanted(lib, downloads=downloads)[("Edition Folder", "volumes")]
        assert w.missing == ("3", "5", "6") and not w.findable
        assert w.reason.startswith("Master edition: nyaa releases are matched by volume number")


def test_tracking_off_leaves_to_get_and_the_find_pass(lib):
    views = _views(lib)
    for name in ("Untracked", "Untracked Empty"):
        assert views[name].state.state == State.NOT_TRACKED and not views[name].state.gaps
    titles = {title for title, _ in _wanted(lib)}
    assert "Untracked" not in titles and "Untracked Empty" not in titles


def test_mangadex_settles_a_series_without_a_latest_chapter(lib):
    v = _views(lib)["Quiet Scanlation"]
    assert v.state.state == State.MISSING_CHAPTERS and v.state.missing_chapters == (("4", "6"),)
    assert _wanted(lib)[("Quiet Scanlation", "chapters")].missing == ("4", "5", "6")
