"""Parity: the wanted list without Qt (:mod:`mangalist.wanted`, from the database) against the window's own (the scan's
entries in the table model, ``MainWindow._rebuild_wanted``) on one library on disk - every series' state, knowledge and
held volumes, and the Download tab's "To get" entries."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from mangalist import store, wanted  # noqa: E402
from mangalist.models import MangaEntry  # noqa: E402

from ..states.helpers import TODAY  # noqa: E402
from ..wanted.library import build_library  # noqa: E402
from .conftest import FakeBackend, qapp, settle_states  # noqa: E402,F401


class _DbBackend(FakeBackend):
    """The fake backend, with the real adapter's series lookup (the library database)."""

    def __init__(self, db):
        super().__init__()
        self.db = db

    def series_id_for(self, folder):
        row = self.db.series_for_folder(folder)
        return row.id if row is not None else None


@pytest.fixture
def setup(qapp, tmp_path, monkeypatch):
    from mangalist.gui import main_window as mw

    store.reset_stores()
    lib = build_library(tmp_path / "lib")
    monkeypatch.setenv("MANGALIST_DOWNLOADS", "1")
    monkeypatch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: _DbBackend(lib.db))
    win = mw.MainWindow()
    entries = lib.scan.entries                  # what the window's scan shows: _apply_root_scan
    win._prepare_entries(entries, lib.scan.renamed)
    win._model.set_entries(entries)
    win._model.set_state_providers(today=TODAY)
    win._apply_library(None)
    settle_states(win)                          # computed in the background, as in the app
    yield lib, win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _gui_views(win):
    model = win._model
    out = {}
    for row in range(model.rowCount()):
        e = model.entry_at(row)
        out[str(e.folder)] = (e, model.state_at(row), model.knowledge_at(row), model.held_volumes_at(row))
    return out


def _files(entry: MangaEntry):
    return [(str(f.path), f.size, f.depth, f.parsed) for f in entry.inventory_files]


def _sorted(items):
    return sorted(items, key=lambda w: (w.folder, w.group))


def test_every_series_reads_the_same(setup):
    lib, win = setup
    gui = _gui_views(win)
    mine = {str(v.entry.folder): v for v in wanted.library_states(lib.db, today=TODAY)}
    assert set(mine) == set(gui) and len(gui) == 20
    for folder, (entry, state, knowledge, held) in gui.items():
        v = mine[folder]
        assert _files(v.entry) == _files(entry), folder
        assert (v.entry.n_files, v.entry.needs_kind, v.entry.restarting_units, v.entry.kind_hint) == \
            (entry.n_files, entry.needs_kind, entry.restarting_units, entry.kind_hint), folder
        assert v.knowledge == knowledge, folder
        assert v.state == state, folder
        assert v.held == held, folder


def test_the_to_get_list_is_the_same(setup):
    lib, win = setup
    win._rebuild_wanted(force=True)
    gui = list(win._wanted_series)
    assert len(gui) >= 12 and {w.group for w in gui} == {"volumes", "chapters", "upgrades"}
    assert _sorted(wanted.wanted_list(lib.db, today=TODAY)) == _sorted(gui)


def test_the_picked_library_is_the_same(setup):
    lib, win = setup
    manga = next(r for r in lib.roots if r.name == "Manga")
    win._apply_library(manga.id)
    win._rebuild_wanted(force=True)
    got = wanted.wanted_list(lib.db, root_ids=[manga.id], today=TODAY)
    assert _sorted(got) == _sorted(win._wanted_series) and all("Manga" in Path(w.folder).parts for w in got)


def test_the_links_cache_is_applied_as_the_window_does():
    from mangalist.gui.mu_worker import _apply_cache

    cached = {"mu_id": 7, "mu_title": "T", "mu_url": "u", "licensed": True, "mu_confirmed": True,
              "mu_associated": ["A"], "mu_score": 0.5, "mu_score_version": 5, "mu_band": "auto", "mu_reasons": ["r"],
              "scan_latest_chapter": 12.5, "publisher_name": "P", "publisher_chapters": None, "publisher_volumes": 3.0,
              "publisher_status": "Ongoing", "scan_latest_volume": "x", "anilist_id": 9, "anilist_chapters": 40.0,
              "anilist_volumes": None, "completed_in_origin": 0, "behind_override": "done"}
    a, b = (MangaEntry(folder=Path("/lib/T"), title="T", english_title=None) for _ in range(2))
    _apply_cache(a, cached)
    wanted.apply_links_cache(b, cached)
    assert dataclasses.asdict(a) == dataclasses.asdict(b)
    for partial in ({}, {"mu_id": 1}):           # older caches without the newer keys
        a, b = (MangaEntry(folder=Path("/lib/T"), title="T", english_title=None) for _ in range(2))
        _apply_cache(a, partial)
        wanted.apply_links_cache(b, partial)
        assert dataclasses.asdict(a) == dataclasses.asdict(b)
