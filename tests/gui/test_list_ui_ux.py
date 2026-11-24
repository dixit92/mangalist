"""List tab UI/UX overhaul (owner-approved mockup, 2026-11-24): slimmed context menu with submenus, interactive
Details panel action strip and contextual pills, contextual multi-select bulk action bar, and Ctrl+K Quick Actions."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from mangalist.gui.links import OPEN_FOLDER_LABEL  # noqa: E402
from mangalist.gui.quick_actions import QuickAction, QuickActionsDialog  # noqa: E402
from mangalist.gui.table_model import COL_TITLE  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402

from .conftest import qapp  # noqa: E402,F401
from .test_library_picker import _series, lib  # noqa: E402,F401
from .test_series_actions import find_action, submenu, texts  # noqa: E402


def _row(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def _select(win, *titles):
    sel = win._table.selectionModel()
    sel.clearSelection()
    first = None
    for title in titles:
        idx = win._proxy.mapFromSource(win._model.index(_row(win, title), COL_TITLE))
        if first is None:
            first = idx
        sel.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    if first is not None:
        sel.setCurrentIndex(first, QItemSelectionModel.SelectionFlag.NoUpdate)


def _open_menu(win, *titles, pick=None):
    _select(win, *titles)
    menus = []
    win._exec_menu = lambda menu, pos: menus.append(menu) or (find_action(menu, pick) if pick else None)
    idx = win._proxy.mapFromSource(win._model.index(_row(win, titles[0]), COL_TITLE))
    win._on_context_menu(win._table.visualRect(idx).center())
    return menus[-1]


@pytest.fixture
def window(lib):
    root_dir = lib.tmp / "libs" / "Manga"
    _series(root_dir, "Alpha Saga", 2)
    _series(root_dir, "Beta Days", 1)
    _series(root_dir, "Gamma Tales", 1)
    w = lib.make_window()
    w._db.add_root(str(root_dir), "Manga")
    w._show_roots()
    lib.scan_synchronously(w)
    # Give Alpha Saga a MangaUpdates match + URL so Metadata & MU and Behind actions are active.
    alpha = w._model.entry_at(_row(w, "Alpha Saga"))
    alpha.mu_id = 12345
    alpha.mu_title = "Alpha Saga"
    alpha.mu_url = "https://www.mangaupdates.com/series/alpha"
    alpha.mu_confirmed = False
    return w


def test_context_menu_is_slimmed_with_grouped_submenus_and_checkable_toggles(window):
    menu = _open_menu(window, "Alpha Saga")
    top = texts(menu)
    assert len(top) <= 10
    assert "Rename its whole library…" not in top
    assert OPEN_FOLDER_LABEL in top
    assert "Downloads" in top and "Files & library" in top and "Metadata & MU" in top
    assert "Mark Behind as up to date" in top and "Examined" in top
    # Old redundant toggles are gone on single-select
    assert find_action(menu, "Mark as not examined") is None
    assert find_action(menu, "Toggle examined") is None
    assert find_action(menu, "Un-confirm MU match") is None
    assert find_action(menu, "Clear 'up to date' override") is None

    # Grouped submenus hold their respective actions
    assert find_action(submenu(menu, "Files & library"), "Copy folder path") is not None
    assert find_action(submenu(menu, "Files & library"), "Set aside chapters its volumes hold…") is not None
    assert find_action(submenu(menu, "Metadata & MU"), "Fix MangaUpdates match…") is not None
    assert find_action(submenu(menu, "Metadata & MU"), "Confirm MU match").isCheckable()

    # Checkable toggles flip state on single click
    alpha_row = _row(window, "Alpha Saga")
    assert not window._model.entry_at(alpha_row).examined
    _open_menu(window, "Alpha Saga", pick="Examined")
    assert window._model.entry_at(alpha_row).examined
    assert find_action(_open_menu(window, "Alpha Saga"), "Examined").isChecked()

    assert not window._model.entry_at(alpha_row).mu_confirmed
    _open_menu(window, "Alpha Saga", pick="Confirm MU match")
    assert window._model.entry_at(alpha_row).mu_confirmed
    assert find_action(_open_menu(window, "Alpha Saga"), "Confirm MU match").isChecked()

    _open_menu(window, "Alpha Saga", pick="Mark Behind as up to date")
    assert window._model.entry_at(alpha_row).behind_override == "done"
    assert find_action(_open_menu(window, "Alpha Saga"), "Mark Behind as up to date").isChecked()


def test_detail_panel_action_strip_and_inline_pills_work(window):
    opened = []
    window.open_series_folder = lambda f: opened.append(("folder", f))
    window.rename_series = lambda entries: opened.append(("rename", [e.title for e in entries]))
    window.review_duplicates = lambda f: opened.append(("dupes", f))
    window.set_aside_volume_chapters = lambda f: opened.append(("aside", f))

    alpha_row = _row(window, "Alpha Saga")
    alpha = window._model.entry_at(alpha_row)
    window._model.set_rename_counts({str(alpha.folder): 3})
    window._model.set_duplicate_file_counts({str(alpha.folder): (1, 1)})
    window._select_source_row(alpha_row)

    detail = window._detail
    assert not detail._action_strip.isHidden()
    assert not detail.btn_confirm_mu.isHidden() and detail.btn_confirm_mu.text() == "Confirm"
    assert not detail.btn_mu_page.isHidden()
    assert not detail.btn_inline_rename.isHidden() and detail.btn_inline_rename.text() == "Rename (3)…"
    assert not detail.btn_inline_dupes.isHidden() and detail.btn_inline_dupes.text() == "Duplicates (2)…"
    assert not detail.btn_inline_behind.isHidden() and detail.btn_inline_behind.text() == "Mark up to date"

    detail.btn_open_folder.click()
    detail.btn_inline_rename.click()
    detail.btn_inline_dupes.click()
    detail.btn_confirm_mu.click()
    assert alpha.mu_confirmed and detail.btn_confirm_mu.text() == "Un-confirm"

    detail.btn_inline_behind.click()
    assert alpha.behind_override == "done" and detail.btn_inline_behind.text() == "✓ Up to date"

    detail.btn_copy_path.click()
    assert QGuiApplication.clipboard().text() == str(alpha.folder)

    # Actions ▾ opens the same slimmed series menu anchored to the button
    menus = []
    window._exec_menu = lambda menu, pos: menus.append(menu) or None
    detail.btn_actions.click()
    assert menus and "Files & library" in texts(menus[0])
    assert opened == [("folder", str(alpha.folder)), ("rename", ["Alpha Saga"]), ("dupes", str(alpha.folder))]


def test_bulk_action_bar_appears_on_multi_select_and_runs_actions(window):
    _select(window, "Alpha Saga")
    assert window._list.bulk_bar.isHidden()

    _select(window, "Alpha Saga", "Beta Days")
    assert not window._list.bulk_bar.isHidden()
    assert window._list.bulk_count_label.text() == "2 series selected"

    renamed = []
    window.rename_series = lambda entries: renamed.append([e.title for e in entries])
    window._list.btn_bulk_rename.click()
    assert renamed == [["Alpha Saga", "Beta Days"]]

    window._exec_menu = lambda menu, pos: find_action(menu, "Mark all 2 as examined")
    window._list.btn_bulk_examined.click()
    assert window._model.entry_at(_row(window, "Alpha Saga")).examined
    assert window._model.entry_at(_row(window, "Beta Days")).examined

    window._list.btn_bulk_clear.click()
    assert window._list.bulk_bar.isHidden()


def test_quick_actions_palette_filters_and_runs_action(window, qapp):
    ran = []
    dlg = QuickActionsDialog("Alpha Saga", [
        QuickAction("Open folder", "Files", lambda: ran.append("open")),
        QuickAction("Set aside chapters its volumes hold…", "Files", lambda: ran.append("aside")),
        QuickAction("Fix MangaUpdates match…", "Metadata", lambda: ran.append("mu")),
    ])
    assert dlg.list.count() == 3
    dlg.search.setText("aside volumes")
    assert dlg.list.count() == 1
    assert dlg.list.item(0).text().startswith("Set aside chapters")
    dlg._activate_current()
    assert dlg.chosen is not None
    dlg.chosen.run()
    assert ran == ["aside"]
    dlg.deleteLater()

    # MainWindow.open_quick_actions builds actions for the selected series and executes them
    window._select_source_row(_row(window, "Alpha Saga"))

    def fake_exec(dialog):
        dialog.search.setText("copy folder path")
        assert dialog.list.count() == 1
        dialog._activate_current()
        return QuickActionsDialog.DialogCode.Accepted

    window._exec_quick_actions = fake_exec
    window.open_quick_actions()
    assert QGuiApplication.clipboard().text() == str(window._model.entry_at(_row(window, "Alpha Saga")).folder)
