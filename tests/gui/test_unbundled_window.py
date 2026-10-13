"""Unbundled Chapters in the main window (offscreen Qt, made-up series in a temporary library): the row menu's tick (also
on a multi-selection), the states and the Download tab's groups that follow it, and the details panel's flag."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist.gui.list_text import UNBUNDLED_MENU, unbundled_detail  # noqa: E402
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_VOLUMES  # noqa: E402
from mangalist.gui.table_model import COL_TITLE  # noqa: E402
from mangalist.knowledge import from_mangapixer_item  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402
from mangalist.states import GAP_CHAPTER, UNBUNDLED_FLAG, InventorySnapshot, State  # noqa: E402
from mangalist.unbundled import UnbundledChapters  # noqa: E402

from ..states.helpers import TODAY, item  # noqa: E402
from .conftest import FakeBackend, qapp  # noqa: E402,F401
from .test_auto_modes_window import _sync  # noqa: E402

TITLES = ("Alpha Saga", "Beta Days", "Unscanned Story")


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    from mangalist.gui import main_window as mw
    from mangalist.gui import background as bg

    base = tmp_path / "lib"
    folders = {t: base / t for t in TITLES}
    for f in folders.values():
        f.mkdir(parents=True)
    monkeypatch.setenv("MANGALIST_DOWNLOADS", "1")
    backend = FakeBackend(series_ids={})
    monkeypatch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: backend)
    import importlib
    import pkgutil

    import mangalist.gui as gui

    for module in pkgutil.iter_modules(gui.__path__):                  # see test_auto_modes_window's make_window
        importlib.import_module(f"mangalist.gui.{module.name}")
    with monkeypatch.context() as patch:
        patch.setattr(bg, "start_call", _sync)
        w = mw.MainWindow()
    root = w._db.add_root(str(base), "Manga")
    for t in TITLES[:2]:                                                # the last one is not scanned
        backend.series_ids[str(folders[t])] = w._db.ensure_series(root.id, t).id
    entries = [MangaEntry(folder=f, title=t, english_title=None,
                          files=[FileHit(f / f"{t} v0{n}.cbz", 1000, 0, has_volume=True) for n in (1, 2)])
               for t, f in folders.items()]
    w._model.set_entries(entries)
    # volumes 1-3 out (ch. 1-24.5), latest chapter 41; the folders hold volumes 1-2
    w._model.set_state_providers(knowledge_for=lambda e: from_mangapixer_item(item(trail=[e.title])),
                                 inventory_for=lambda e: InventorySnapshot(held_volumes=["1", "2"]),
                                 needs_kind_for=lambda e: False, today=TODAY)
    w._refresh_derived()
    yield w
    w.close()
    w.deleteLater()


def _row(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def _menu(win, *titles, pick=False):
    """Select *titles* and open the row menu; with *pick*, choose the Unbundled chapters entry. Returns that entry."""
    from PySide6.QtCore import QItemSelectionModel

    sel = win._table.selectionModel()
    sel.clearSelection()
    for title in titles:
        idx = win._proxy.mapFromSource(win._model.index(_row(win, title), COL_TITLE))
        sel.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    seen = []

    def answer(menu, pos):
        act = next(a for a in menu.actions() if a.text() == UNBUNDLED_MENU)
        seen.append((act.isCheckable(), act.isChecked()))
        return act if pick else None

    win._exec_menu = answer
    idx = win._proxy.mapFromSource(win._model.index(_row(win, titles[0]), COL_TITLE))
    win._on_context_menu(win._table.visualRect(idx).center())
    return seen[-1]


def _groups(win, title):
    win._refresh_derived()
    return set(win._wanted_groups(str(win._model.entry_at(_row(win, title)).folder)))


def test_the_tick_switches_the_series_and_its_state_follows(win):
    alpha = _row(win, "Alpha Saga")
    assert _menu(win, "Alpha Saga") == (True, False)
    assert win._model.state_at(alpha).state == State.MISSING_VOLUMES and _groups(win, "Alpha Saga") == {GROUP_VOLUMES}
    _menu(win, "Alpha Saga", pick=True)
    st = win._model.state_at(alpha)
    assert st.unbundled and UNBUNDLED_FLAG in st.flags
    assert [(g.start, g.end) for g in st.gaps if g.kind == GAP_CHAPTER] == [("25", "41")]
    assert _groups(win, "Alpha Saga") == {GROUP_VOLUMES, GROUP_CHAPTERS}         # both groups of the Download tab
    assert win._status_label.text().startswith("Unbundled chapters on for 1 series")
    assert UnbundledChapters(win._db).series_ids() == {win._db.series_for_folder(win._model.entry_at(alpha).folder).id}
    assert _menu(win, "Alpha Saga") == (True, True)
    win._select_source_row(_row(win, "Beta Days"))
    win._select_source_row(alpha)
    assert unbundled_detail(True) in win._detail._lbl_flags.text()
    _menu(win, "Alpha Saga", pick=True)                                           # and off again
    assert not win._model.state_at(alpha).unbundled and _groups(win, "Alpha Saga") == {GROUP_VOLUMES}
    assert win._status_label.text() == "Unbundled chapters off for 1 series: volumes only"
    assert unbundled_detail(True) not in win._detail._lbl_flags.text()


def test_a_multi_selection_is_ticked_only_when_every_series_has_it(win):
    _menu(win, "Alpha Saga", pick=True)
    assert _menu(win, "Alpha Saga", "Beta Days") == (True, False)
    _menu(win, "Alpha Saga", "Beta Days", pick=True)                               # on for both
    assert all(win._model.state_at(_row(win, t)).unbundled for t in ("Alpha Saga", "Beta Days"))
    assert win._status_label.text().startswith("Unbundled chapters on for 2 series")
    assert _menu(win, "Alpha Saga", "Beta Days") == (True, True)


def test_a_folder_not_scanned_yet_says_so(win):
    _menu(win, "Unscanned Story", pick=True)
    assert win._status_label.text() == "Unbundled chapters: these folders are not scanned yet."
    assert not win._model.state_at(_row(win, "Unscanned Story")).unbundled
