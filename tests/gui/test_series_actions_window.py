"""Series actions everywhere in the main window (offscreen Qt, the fake finding backend, made-up series in a temporary
library): the window is the series host of the Download tab's menus, so Unbundled, Behind, Fix MangaUpdates match and
Show in List chosen there act on the List; a mode set on one screen reaches the others (the List's chips and details,
Found's picker); the details panel's picker and Unbundled switch; a library's default named in the List."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist.gui.download_tab import ROLE_FOLDER  # noqa: E402
from mangalist.gui.list_text import UNBUNDLED_MENU  # noqa: E402
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_VOLUMES  # noqa: E402
from mangalist.gui.table_model import COL_TITLE, MODE_CHIP_ROLE  # noqa: E402
from mangalist.gui.top_bar import TAB_DOWNLOAD, TAB_LIST  # noqa: E402
from mangalist.knowledge import from_mangapixer_item  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402
from mangalist.states import InventorySnapshot, State  # noqa: E402

from ..states.helpers import TODAY, item  # noqa: E402
from .conftest import qapp  # noqa: E402,F401
from .finding_fakes import FakeFindingBackend, finding  # noqa: E402
from .test_auto_modes_window import _sync  # noqa: E402
from .test_series_actions import find_action, texts  # noqa: E402

TITLES = ("Alpha Saga", "Beta Days", "Unscanned Story")


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    from mangalist.gui import background as bg
    from mangalist.gui import main_window as mw

    base = tmp_path / "lib"
    folders = {t: base / t for t in TITLES}
    for f in folders.values():
        f.mkdir(parents=True)
    monkeypatch.setenv("MANGALIST_DOWNLOADS", "1")
    backend = FakeFindingBackend(series_ids={})
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
    ids = {}
    for t in TITLES[:2]:                                                # the last one is not scanned
        ids[t] = backend.series_ids[str(folders[t])] = w._db.ensure_series(root.id, t).id
    backend.found = [finding(1, series_id=ids["Alpha Saga"], fills=("3",), title="Alpha Saga v03 (Digital)")]
    entries = [MangaEntry(folder=f, title=t, english_title=None,
                          files=[FileHit(f / f"{t} v0{n}.cbz", 1000, 0, has_volume=True) for n in (1, 2)])
               for t, f in folders.items()]
    w._model.set_entries(entries)
    # volumes 1-3 out (ch. 1-24.5), latest chapter 41; the folders hold volumes 1-2: every series misses volume 3
    w._model.set_state_providers(knowledge_for=lambda e: from_mangapixer_item(item(trail=[e.title])),
                                 inventory_for=lambda e: InventorySnapshot(held_volumes=["1", "2"]),
                                 needs_kind_for=lambda e: False, today=TODAY)
    w._refresh_derived()
    w._download_tab.reload_found()
    w.test_ids, w.test_folders, w.test_backend = ids, {t: str(f) for t, f in folders.items()}, backend
    yield w
    w.close()
    w.deleteLater()


def _row(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def _to_get_menu(win, title, pick=None):
    """Right-click *title* in the To get list (Missing volumes) and answer with the entry *pick*."""
    tab = win._download_tab
    win._refresh_derived()
    tab.show_group(GROUP_VOLUMES)
    folder = win.test_folders[title]
    row = next(tab.tree.topLevelItem(i) for i in range(tab.tree.topLevelItemCount())
               if tab.tree.topLevelItem(i).data(0, ROLE_FOLDER) == folder)
    menus = []
    tab._exec_menu = lambda menu, pos: menus.append(menu) or (find_action(menu, pick) if pick else None)
    tab._on_tree_menu(tab.tree.visualItemRect(row).center())
    return menus[-1]


def _chips(win):
    m = win._model
    return {t: m.data(m.index(_row(win, t), COL_TITLE), MODE_CHIP_ROLE) for t in TITLES
            if m.data(m.index(_row(win, t), COL_TITLE), MODE_CHIP_ROLE)}


def test_the_to_get_menu_acts_on_the_list_through_the_window(win, monkeypatch):
    menu = _to_get_menu(win, "Alpha Saga")
    assert texts(menu)[:4] == ["Automatic downloads", UNBUNDLED_MENU, "Mark Behind as up to date",
                               "Clear 'up to date' override"]
    assert not find_action(menu, "Mark Behind as up to date").isEnabled()     # no MangaUpdates match yet
    _to_get_menu(win, "Alpha Saga", UNBUNDLED_MENU)
    alpha = _row(win, "Alpha Saga")
    assert win._model.state_at(alpha).unbundled
    win._refresh_derived()
    assert set(win._wanted_groups(win.test_folders["Alpha Saga"])) == {GROUP_VOLUMES, GROUP_CHAPTERS}
    assert win._download_tab.bulk_label.text().startswith("Unbundled chapters on for 1 series")
    # Behind: a series with a MangaUpdates match can be marked up to date from To get
    win._model.entry_at(alpha).mu_id = 77
    _to_get_menu(win, "Alpha Saga", "Mark Behind as up to date")
    assert win._model.entry_at(alpha).behind_override == "done"
    assert win._model.state_at(alpha).state == State.UP_TO_DATE
    assert find_action(_to_get_menu(win, "Alpha Saga"), "Clear 'up to date' override").isEnabled()
    _to_get_menu(win, "Alpha Saga", "Clear 'up to date' override")
    assert win._model.entry_at(alpha).behind_override is None
    # Fix MangaUpdates match: the List's own dialog, for that row
    fixed = []
    monkeypatch.setattr(win, "_on_fix_mu_match", lambda row, entry: fixed.append((row, entry.title)))
    _to_get_menu(win, "Beta Days", "Fix MangaUpdates match…")
    assert fixed == [(_row(win, "Beta Days"), "Beta Days")]


def test_show_in_list_from_to_get_and_from_found(win):
    win.show_tab(TAB_DOWNLOAD)
    _to_get_menu(win, "Beta Days", "Show in List")
    assert win._top.current() == TAB_LIST and win._current_entry().title == "Beta Days"
    win.show_tab(TAB_DOWNLOAD)
    tab = win._download_tab
    tab.show_group("found")
    menus = []
    tab._exec_menu = lambda menu, pos: menus.append(menu) or find_action(menu, "Show in List")
    tab.found.click(next(iter(tab.found.rows)), "more")
    assert win._top.current() == TAB_LIST and win._current_entry().title == "Alpha Saga"


def test_a_mode_set_on_one_screen_reaches_the_others(win):
    tab, backend = win._download_tab, win.test_backend
    alpha = win.test_ids["Alpha Saga"]
    win._select_source_row(_row(win, "Alpha Saga"))
    tab.show_group("found")
    tab.found.set_series_filter(alpha)
    assert tab.found.mode_combo.currentText() == "Use the default (Notify)"
    _to_get_menu(win, "Alpha Saga", "Auto")                     # in the Download tab
    assert backend.own_modes[alpha] == "auto"
    assert _chips(win) == {"Alpha Saga": "Auto"}                 # the List's chip
    assert win._detail.auto_mode_text() == "Auto" and win._detail.auto_choice() == "Auto"
    tab.show_group("found")
    tab.found.set_series_filter(alpha)
    assert tab.found.mode_combo.currentText() == "Auto"
    # ... and the List's details picker back to the default: Found's picker and the chip follow
    combo = win._detail.auto_combo
    combo.activated.emit(combo.findData(None))
    assert backend.own_modes[alpha] is None and _chips(win) == {}
    assert win._status_label.text() == "Automatic downloads: 1 series follow the default again (Notify)."
    assert tab.found.mode_combo.currentText() == "Use the default (Notify)"


def test_the_details_unbundled_switch(win):
    win._select_source_row(_row(win, "Alpha Saga"))
    check = win._detail.unbundled_check
    assert check.isEnabled() and not check.isChecked()
    check.click()
    assert win._model.state_at(_row(win, "Alpha Saga")).unbundled and check.isChecked()
    win._select_source_row(_row(win, "Unscanned Story"))
    assert not check.isEnabled()                                # not scanned: nothing to switch


def test_a_librarys_default_is_named_in_the_list(win):
    backend = win.test_backend
    beta = win.test_ids["Beta Days"]
    backend.library_modes[beta] = "off"                         # its library set to Off (Settings > Library)
    win._refresh_auto_modes(force=True)
    assert _chips(win) == {}                                    # on its library's default: no chip
    win._select_source_row(_row(win, "Beta Days"))
    assert win._detail.auto_mode_text() == "Off (the library's default)"
    assert win._detail.auto_choice() == "Use the library's default (Off)"
    backend.own_modes[beta] = "notify"
    win._refresh_auto_modes(force=True)
    assert _chips(win) == {"Beta Days": "Notify"}               # its own mode differs from its library's
