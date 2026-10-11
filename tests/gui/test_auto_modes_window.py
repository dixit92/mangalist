"""Automatic downloads in the main window and Settings (offscreen Qt, the fake finding backend, made-up series): the row
menu's "Automatic downloads" submenu (ticks, a multi-selection), the details panel's line, the mode chip on the rows that differ from the default,
the bulk find from the List, and Settings > Automation's default for new series."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from mangalist import store  # noqa: E402
from mangalist.gui.settings_dialog import SettingsDialog  # noqa: E402
from mangalist.gui.shell import SECTION_AUTOMATION  # noqa: E402
from mangalist.gui.table_model import COL_TITLE, MODE_CHIP_ROLE  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402
from mangalist.states import InventorySnapshot  # noqa: E402

from ..states.helpers import TODAY  # noqa: E402
from .conftest import FakeBackend, qapp, wait_until  # noqa: E402,F401
from .finding_fakes import FakeFindingBackend  # noqa: E402

TITLES = ("Alpha Saga", "Beta Days", "Gamma Tales", "Unscanned Story")


def _entry(title: str) -> MangaEntry:
    folder = Path("/lib") / title
    return MangaEntry(folder=folder, title=title, english_title=None,
                      files=[FileHit(folder / f"{title} v01.cbz", 1000, 0, has_volume=True)])


class _Done:
    def abandon(self):
        pass


def _sync(fn, on_done=None, on_error=None, on_finished=None):
    """The window's background runner, run at once (no thread order to depend on)."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        if on_error:
            on_error(str(exc))
    else:
        if on_done:
            on_done(result)
    if on_finished:
        on_finished()
    return _Done()


@pytest.fixture
def make_window(qapp, monkeypatch):
    from mangalist.gui import main_window as mw

    made = []

    def make(backend):
        monkeypatch.setenv("MANGALIST_DOWNLOADS", "1")
        monkeypatch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: backend)
        from mangalist.gui import background as bg

        with monkeypatch.context() as patch:            # the window's own runner, at once from the start
            patch.setattr(bg, "start_call", _sync)
            win = mw.MainWindow()
        made.append(win)
        assert win._background is _sync
        win._model.set_entries([_entry(t) for t in TITLES])
        win._model.set_state_providers(knowledge_for=lambda e: None,
                                       inventory_for=lambda e: InventorySnapshot(held_volumes=["1"]),
                                       needs_kind_for=lambda e: False, today=TODAY)
        win._refresh_derived()
        return win

    yield make
    for win in made:
        win.close()
        win.deleteLater()


def _backend(**kw):
    ids = {str(Path("/lib") / t): i for i, t in enumerate(TITLES[:3], start=1)}      # the last one is not scanned
    return FakeFindingBackend(series_ids=ids, **kw)


def _row(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def _menu_for(win, *titles):
    """Select *titles* and open the row menu (answered by nobody); returns the menu."""
    from PySide6.QtCore import QItemSelectionModel

    sel = win._table.selectionModel()
    sel.clearSelection()
    for title in titles:
        idx = win._proxy.mapFromSource(win._model.index(_row(win, title), COL_TITLE))
        sel.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    menus = []
    win._exec_menu = lambda menu, pos: menus.append(menu) or None
    idx = win._proxy.mapFromSource(win._model.index(_row(win, titles[0]), COL_TITLE))
    win._on_context_menu(win._table.visualRect(idx).center())
    return menus[-1]


def _submenu(menu):
    return next(a.menu() for a in menu.actions() if a.menu() is not None and a.text() == "Automatic downloads")


def _choose(win, titles, text):
    """Open the row menu on *titles* and pick *text* in the Automatic downloads submenu."""
    _menu_for(win, *titles)                                             # selects them
    win._exec_menu = lambda m, pos: next(a for a in _submenu(m).actions() if a.text() == text)
    idx = win._proxy.mapFromSource(win._model.index(_row(win, titles[0]), COL_TITLE))
    win._on_context_menu(win._table.visualRect(idx).center())


def test_the_submenu_ticks_the_mode_and_works_on_a_multi_selection(make_window):
    backend = _backend(modes={1: "auto"})
    win = make_window(backend)
    sub = _submenu(_menu_for(win, "Alpha Saga"))
    assert [(a.text(), a.isChecked()) for a in sub.actions() if not a.isSeparator()] == [
        ("Off", False), ("Notify", False), ("Auto", True), ("Use the default (Notify)", False)]
    sub = _submenu(_menu_for(win, "Alpha Saga", "Beta Days"))
    assert not any(a.isChecked() for a in sub.actions())               # they differ: nothing ticked
    _choose(win, ["Alpha Saga", "Beta Days"], "Off")
    assert backend.mode_calls[-1] == ((1, 2), "off")
    assert win._status_label.text() == "Automatic downloads: Off for 2 series."
    _choose(win, ["Beta Days"], "Use the default (Notify)")
    assert backend.mode_calls[-1] == ((2,), None) and backend.own_modes[2] is None


def test_a_series_not_scanned_yet_has_the_submenu_off(make_window):
    win = make_window(_backend())
    sub = _submenu(_menu_for(win, "Unscanned Story"))
    assert not sub.isEnabled()
    menu = _menu_for(win, "Unscanned Story")
    bulk = next(a for a in menu.actions() if a.text().startswith("Find releases for"))
    assert bulk.text() == "Find releases for 0 series" and not bulk.isEnabled()


def _chips(win):
    model = win._model
    return {t: model.data(model.index(_row(win, t), COL_TITLE), MODE_CHIP_ROLE) for t in TITLES
            if model.data(model.index(_row(win, t), COL_TITLE), MODE_CHIP_ROLE)}


def test_the_mode_chip_shows_only_where_the_series_differs_from_the_default(make_window):
    # owner, 2026-10-11: with an Auto default every row would carry the chip - only a series' own, different mode does
    backend = _backend(modes={1: "auto"})
    win = make_window(backend)
    assert _chips(win) == {"Alpha Saga": "Auto"}
    win._select_source_row(_row(win, "Alpha Saga"))
    assert win._detail.auto_mode_text() == "Auto" and win._detail._lbl_auto.isVisibleTo(win._detail)
    win._select_source_row(_row(win, "Beta Days"))
    assert win._detail.auto_mode_text() == "Notify (the default)"
    _choose(win, ["Beta Days"], "Auto")
    assert _chips(win) == {"Alpha Saga": "Auto", "Beta Days": "Auto"}
    assert win._detail.auto_mode_text() == "Auto"
    _choose(win, ["Gamma Tales"], "Off")
    assert _chips(win)["Gamma Tales"] == "Off"
    backend.default = "auto"                                            # the default changed in Settings
    win._refresh_auto_modes(force=True)
    assert _chips(win) == {"Gamma Tales": "Off"}                         # Auto is the default now: no Auto chips


def test_bulk_find_from_the_list_opens_found(make_window, qapp):
    backend = _backend()
    win = make_window(backend)
    menu = _menu_for(win, "Alpha Saga", "Gamma Tales", "Unscanned Story")
    bulk = next(a for a in menu.actions() if a.text().startswith("Find releases for"))
    assert bulk.text() == "Find releases for 2 series" and bulk.isEnabled()
    win._exec_menu = lambda m, pos: next(a for a in m.actions() if a.text() == "Find releases for 2 series")
    idx = win._proxy.mapFromSource(win._model.index(_row(win, "Alpha Saga"), COL_TITLE))
    win._on_context_menu(win._table.visualRect(idx).center())
    tab = win._download_tab
    assert win._top.current() == 1 and tab.current_group() == "found"
    wait_until(qapp, lambda: not tab.find_running())
    assert backend.find_calls == [(1, 3)]


def test_nothing_of_it_without_the_backend_methods(make_window):
    win = make_window(FakeBackend(series_ids={str(Path("/lib") / "Alpha Saga"): 1}))
    assert win._auto_modes is None and not win._detail._lbl_auto.isVisibleTo(win._detail)
    menu = _menu_for(win, "Alpha Saga")
    assert not any(a.text() == "Automatic downloads" for a in menu.actions())
    assert not any(a.text().startswith("Find releases for") for a in menu.actions())


# --- Settings > Automation ---------------------------------------------------------------------------------------

@pytest.fixture
def settings(qapp):
    made = []

    def open_page(backend):
        store.reset_stores()
        dlg = SettingsDialog(None, store.get_store(), backend, SECTION_AUTOMATION, env={})
        made.append(dlg)
        return dlg, dlg.pages[SECTION_AUTOMATION]

    yield open_page
    for dlg in made:
        dlg.reject()
        dlg.deleteLater()


def test_settings_sets_the_default_for_new_series(settings):
    backend = FakeFindingBackend()
    dlg, page = settings(backend)
    radios = page.default_mode_radios
    assert [r.text() for r in radios.values()] == ["Off", "Notify", "Auto"]
    assert radios["notify"].isChecked() and all(r.isEnabled() for r in radios.values())
    radios["auto"].setChecked(True)
    assert backend.default == "auto" and backend.mode_calls == [("default", "auto")]
    assert page.auto_status.text() == "Saved: series you have not set are now Auto."
    assert dlg.result_data().downloads_changed


def test_settings_without_automation_shows_the_choice_off(settings):
    _dlg, page = settings(FakeBackend())
    assert not any(r.isEnabled() for r in page.default_mode_radios.values())
    assert "switched off" in page.auto_note.text()
