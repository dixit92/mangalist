"""Move to library in the List's row menu (offscreen Qt, a real database with real folders of made-up series in two
library folders): the submenu lists the other libraries, the confirmation names what moves where and what changes,
Cancel moves nothing, one row and several rows move, a download in progress refuses, Undo last move puts them back; the
List shows the series in its new library after the folder scan."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel  # noqa: E402

from mangalist.gui.series_actions import MOVE_MENU, NO_OTHER_LIBRARY  # noqa: E402
from mangalist.gui.table_model import COL_TITLE  # noqa: E402
from mangalist.scanner import folder_scopes  # noqa: E402

from .conftest import qapp  # noqa: E402,F401
from .test_library_picker import Lib, _series, lib  # noqa: E402,F401
from .test_series_actions import find_action, submenu, texts  # noqa: E402

ONGOING, CONCLUDED = "Manga-Ongoing", "Manga-Concluded"


@pytest.fixture
def win(lib):
    ongoing, concluded = lib.tmp / "libs" / ONGOING, lib.tmp / "libs" / CONCLUDED
    for name in ("Amber Tide", "Cinder Vale", "Moss Lantern"):
        _series(ongoing, name, 2)
    _series(concluded, "Iron Petals")
    w = lib.make_window()
    w._db.add_root(str(ongoing), ONGOING)
    w._db.add_root(str(concluded), CONCLUDED, auto_mode="off", enforce_naming="automatic")
    w._show_roots()
    lib.scan_synchronously(w)
    w.test_scans = []

    def scan_paths(paths):                      # the folder scan, run on this thread
        scopes = folder_scopes(w._roots(), paths)
        w.test_scans.append(scopes)
        lib.scan_synchronously(w, [r for r in w._roots() if r.id in scopes], scopes)
        return True

    w._scan_paths = scan_paths
    w.test_asked, w.test_told = [], []
    w.test_answer = True

    def ask(title, text, label):
        w.test_asked.append((title, text, label))
        return w.test_answer

    w._ask = ask
    w.test_into = ""

    def answer_move(dialog):                    # the move confirmation (move_dialog.MoveDialog)
        if w.test_into:
            assert dialog.choose(w.test_into)
        w.test_asked.append((dialog.windowTitle(), dialog.text(), dialog.move_label()))
        return w.test_answer

    w.answer_move = answer_move
    w.tell_move = lambda title, text: w.test_told.append((title, text))
    w.test_dirs = {ONGOING: ongoing, CONCLUDED: concluded}
    return w


def _row(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def _menu(win, *titles, pick=None):
    """Select *titles*, open the row menu and choose the entry *pick* (None: close it). Returns the menu."""
    sel = win._table.selectionModel()
    sel.clearSelection()
    for title in titles:
        idx = win._proxy.mapFromSource(win._model.index(_row(win, title), COL_TITLE))
        sel.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    menus = []
    win._exec_menu = lambda menu, pos: menus.append(menu) or (find_action(menu, pick) if pick else None)
    idx = win._proxy.mapFromSource(win._model.index(_row(win, titles[0]), COL_TITLE))
    win._on_context_menu(win._table.visualRect(idx).center())
    return menus[-1]


def _library_of(win, title):
    root_id = win._model.entry_at(_row(win, title)).root_id
    return next(r.name for r in win._roots() if r.id == root_id)


def test_the_submenu_lists_the_other_libraries(win):
    sub = submenu(_menu(win, "Amber Tide"), MOVE_MENU)
    assert texts(sub) == [CONCLUDED]
    assert find_action(sub, CONCLUDED).toolTip() == str(win.test_dirs[CONCLUDED])
    assert texts(submenu(_menu(win, "Iron Petals"), MOVE_MENU)) == [ONGOING]
    assert texts(submenu(_menu(win, "Amber Tide", "Iron Petals"), MOVE_MENU)) == [ONGOING, CONCLUDED]   # mixed: both


def test_one_row_moves_after_the_confirmation_and_the_list_follows(win):
    sid = win._db.series_for_folder(win._model.entry_at(_row(win, "Amber Tide")).folder).id
    win.test_answer = False                                                         # Cancel: nothing moves
    _menu(win, "Amber Tide", pick=CONCLUDED)
    title, text, label = win.test_asked[-1]
    assert (title, label) == (f"Move to {CONCLUDED}?", "Move 1 series")
    assert text.startswith(f"Move 1 series from {ONGOING} to {CONCLUDED}?\n\n    Amber Tide\n")
    assert f"Into the folder: {win.test_dirs[CONCLUDED]} (the top of the library)" in text
    assert "Files not named by the scheme: Rename automatically" in text
    assert "Automatic downloads" not in text                                        # downloads are off here
    assert (win.test_dirs[ONGOING] / "Amber Tide").is_dir() and win.test_scans == []

    win.test_answer = True
    _menu(win, "Amber Tide", pick=CONCLUDED)
    assert (win.test_dirs[CONCLUDED] / "Amber Tide" / "Amber Tide v01.cbz").is_file()
    assert not (win.test_dirs[ONGOING] / "Amber Tide").exists()
    assert _library_of(win, "Amber Tide") == CONCLUDED                              # the folder scan showed it there
    assert win._db.series_for_folder(win.test_dirs[CONCLUDED] / "Amber Tide").id == sid
    assert [sorted(s.values(), key=str) for s in win.test_scans] == [[{"Amber Tide"}, {"Amber Tide"}]]  # both sides
    assert win._status_label.text().startswith(f"Moved 1 series to {CONCLUDED}. Undo: Move to library > Undo last move.")
    assert sorted(e.title for e in win._model.entries()) == ["Amber Tide", "Cinder Vale", "Iron Petals", "Moss Lantern"]


def test_several_rows_move_at_once_and_undo_puts_them_back(win):
    _menu(win, "Cinder Vale", "Moss Lantern", pick=CONCLUDED)
    assert win.test_asked[-1][2] == "Move 2 series"
    assert {_library_of(win, t) for t in ("Cinder Vale", "Moss Lantern")} == {CONCLUDED}
    undo = f"Undo last move (2 series back to {ONGOING})"
    assert texts(submenu(_menu(win, "Amber Tide"), MOVE_MENU))[-1] == undo
    _menu(win, "Amber Tide", pick=undo)
    assert win.test_asked[-1][0] == "Undo the last move?" and win.test_asked[-1][2] == "Move back"
    assert {_library_of(win, t) for t in ("Cinder Vale", "Moss Lantern")} == {ONGOING}
    assert (win.test_dirs[ONGOING] / "Cinder Vale").is_dir() and not (win.test_dirs[CONCLUDED] / "Cinder Vale").exists()
    assert win._status_label.text().startswith("Moved 2 series back.")
    assert undo not in texts(submenu(_menu(win, "Amber Tide"), MOVE_MENU))


def test_a_download_in_progress_refuses_with_the_reason(win):
    sid = win._db.series_for_folder(win._model.entry_at(_row(win, "Amber Tide")).folder).id
    with win._db.connect() as con:
        con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status) VALUES"
                    " ('2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z', ?, '{}', 'qbittorrent', 'downloaded')", (sid,))
    _menu(win, "Amber Tide", pick=CONCLUDED)
    assert win.test_asked == []                                                     # nothing to confirm
    title, text = win.test_told[-1]
    assert title == f"Not moved to {CONCLUDED}" and "Amber Tide - a download is in progress" in text
    assert (win.test_dirs[ONGOING] / "Amber Tide").is_dir()
    _menu(win, "Amber Tide", "Cinder Vale", pick=CONCLUDED)                          # the others still move
    text = win.test_asked[-1][1]
    assert "Not moved:\n    Amber Tide - a download is in progress" in text and win.test_asked[-1][2] == "Move 1 series"
    assert _library_of(win, "Cinder Vale") == CONCLUDED and _library_of(win, "Amber Tide") == ONGOING


def test_one_library_only_says_so(lib):
    _series(lib.tmp / "libs" / "Solo", "Lone Reed")
    w = lib.make_window()
    w._db.add_root(str(lib.tmp / "libs" / "Solo"), "Solo")
    lib.scan_synchronously(w)
    sub = submenu(_menu(w, "Lone Reed"), MOVE_MENU)
    assert texts(sub) == [NO_OTHER_LIBRARY] and not sub.actions()[0].isEnabled()


def test_the_confirmation_lets_the_owner_choose_a_folder_inside_the_target(win):
    """Owner, 2026-10-15: "OK default, user settable location"."""
    shelf = win.test_dirs[CONCLUDED] / "Tide Shelf"
    shelf.mkdir()
    win.test_into = "Tide Shelf"
    _menu(win, "Amber Tide", pick=CONCLUDED)
    title, text, label = win.test_asked[-1]
    assert label == "Move 1 series" and text.startswith(f"Move 1 series from {ONGOING} to {CONCLUDED} / Tide Shelf?")
    assert f"Into the folder: {shelf}" in text
    assert (shelf / "Amber Tide" / "Amber Tide v01.cbz").is_file() and not (win.test_dirs[ONGOING] / "Amber Tide").exists()
    assert _library_of(win, "Amber Tide") == CONCLUDED                              # a subseries of "Tide Shelf"


def test_the_dialog_rechecks_each_folder_and_offers_only_what_can_move(win, qapp):
    from mangalist import library_move
    from mangalist.gui.move_dialog import MoveDialog, top_text

    (win.test_dirs[CONCLUDED] / "Tide Shelf" / "Amber Tide").mkdir(parents=True)      # taken inside that folder
    root = next(r for r in win._roots() if r.name == CONCLUDED)
    folder = str(win.test_dirs[ONGOING] / "Amber Tide")
    check = library_move.check_move(win._db, [(folder, "Amber Tide")], root.id)
    dialog = MoveDialog(win._db, check, show_modes=False)
    combo = dialog.into_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [top_text(root), "Iron Petals", "Tide Shelf"]
    assert dialog.btn_cancel.isDefault() and dialog.btn_move.isEnabled() and dialog.move_label() == "Move 1 series"
    assert dialog.choose("Tide Shelf") and not dialog.btn_move.isEnabled()
    assert "already has a folder named 'Amber Tide'" in dialog.text()
    assert dialog.ask(lambda d: True) is None                                        # nothing can move there
    assert dialog.choose("") and dialog.btn_move.isEnabled()
    assert dialog.ask(lambda d: True).into == "" and dialog.ask(lambda d: False) is None
    assert not dialog.choose("Nowhere")
