"""Busy indicators (owner, 2026-10-16, answer 80: "keep a spinner if measured time for any UI element can go over
0.5s"): the List's State cell of a series whose state is still being worked out, the chips' counts of the long
background counts, the Download tab's counts while its list waits for states. Offscreen Qt, made-up series."""

from __future__ import annotations

import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QModelIndex, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableView, QWidget  # noqa: E402

from mangalist.gui import busy, theme  # noqa: E402
from mangalist.gui.chips import ChipButton, TabButton  # noqa: E402
from mangalist.gui.list_delegates import StateBadgeDelegate  # noqa: E402
from mangalist.gui.list_tab import DUPLICATES, ListTab  # noqa: E402
from mangalist.gui.main_window import MainWindow  # noqa: E402
from mangalist.gui.table_model import COL_STATE, COL_TITLE, PENDING_ROLE, STATE_ROLE, MangaTableModel  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402

from .conftest import qapp  # noqa: E402,F401
from .test_dark_mode import themed  # noqa: E402,F401


def _entry(title: str) -> MangaEntry:
    folder = Path("/lib") / title
    return MangaEntry(folder=folder, title=title, english_title=None,
                      files=[FileHit(folder / f"{title} v01.cbz", 1000, 0, has_volume=True)])


def _deferred(titles):
    model = MangaTableModel([_entry(t) for t in titles])
    model.deferred = True
    return model


def _push_all(model) -> None:
    model.push_states(model.compute_states(model.take_states_request()))


# --- the model: which cells are pending ----------------------------------------------------------------------------

def test_a_series_seen_for_the_first_time_is_pending_until_its_state_is_pushed(qapp):
    model = _deferred(["Made Up One", "Made Up Two"])
    state_cell = model.index(0, COL_STATE)
    assert model.data(state_cell, STATE_ROLE) is None and model.data(state_cell, PENDING_ROLE) is True
    assert model.state_pending_at(1) and model.evaluations == 0                  # asking computes nothing
    assert model.data(model.index(0, COL_TITLE), PENDING_ROLE) is None           # only the State cell is busy
    _push_all(model)
    assert model.data(state_cell, PENDING_ROLE) is False and model.data(state_cell, STATE_ROLE) is not None
    assert not model.state_pending_at(0) and not model.state_pending_at(99)


def test_a_rescanned_series_shows_its_old_state_and_is_not_pending(qapp):
    model = _deferred(["Made Up One"])
    _push_all(model)
    old = model.shown_state_at(0)
    model.set_entries([_entry("Made Up One"), _entry("Made Up Three")])        # a rescan: a new entry for the same folder
    assert model.shown_state_at(0) is old and not model.state_pending_at(0)    # the old state stands in
    assert model.state_pending_at(1)                                           # a series never seen: busy


def test_without_a_background_runner_nothing_is_pending(qapp):
    model = MangaTableModel([_entry("Made Up One")])
    assert not model.deferred and not model.state_pending_at(0)
    assert model.data(model.index(0, COL_STATE), PENDING_ROLE) is False


def test_a_state_that_could_not_be_computed_in_the_background_is_not_left_spinning(qapp):
    model = _deferred(["Made Up One"])
    model.abandon_states(model.take_states_request())
    assert not model.state_pending_at(0)


# --- the shared clock ----------------------------------------------------------------------------------------------

def test_the_clock_runs_only_while_a_client_has_something_to_animate(qapp):
    clk = busy.BusyClock()
    calls = []
    left = [2]

    def client():
        calls.append(1)
        left[0] -= 1
        return left[0] > 0

    clk.watch(client)
    clk.watch(client)                                   # registering twice is one client
    assert clk.active and clk.clients() == 1
    clk.tick()
    assert clk.active and len(calls) == 1
    clk.tick()                                          # False: let go; no client: the timer stops
    assert not clk.active and clk.clients() == 0 and len(calls) == 2
    clk.tick()
    assert len(calls) == 2


def test_the_clock_drops_a_client_whose_owner_is_gone_or_deleted(qapp):
    clk = busy.BusyClock()

    class Owner:
        def tick(self):
            return True

    owner = Owner()
    clk.watch(owner.tick)
    ref = weakref.ref(owner)
    del owner
    assert ref() is None
    clk.tick()
    assert not clk.active                               # held by a weak reference: nothing keeps it going

    class Wid(QWidget):
        def tick(self):
            self.update()
            return True

    widget = Wid()
    clk.watch(widget.tick)
    widget.deleteLater()
    QApplication.sendPostedEvents(None, 52)             # QEvent.DeferredDelete
    clk.tick()
    assert not clk.active                               # a deleted widget under a client: dropped, not raised


def test_the_spinner_turns_with_the_wall_clock():
    assert busy.angle(0.0) == 0.0 and busy.angle(busy.TURN_MS / 2000) == pytest.approx(180.0)
    assert busy.angle(busy.TURN_MS / 1000) == pytest.approx(0.0)


# --- the State cell ------------------------------------------------------------------------------------------------

def _table(model):
    view = QTableView()
    view.setModel(model)
    delegate = StateBadgeDelegate(view)
    view.setItemDelegateForColumn(COL_STATE, delegate)
    for col in range(model.columnCount()):
        view.setColumnHidden(col, col != COL_STATE)     # the State column alone, in view
    view.setColumnWidth(COL_STATE, 220)
    view.verticalHeader().setDefaultSectionSize(50)
    view.resize(400, 300)
    view.show()
    QApplication.processEvents()
    return view, delegate


def _cell_image(view, model, row=0) -> QImage:
    rect = view.visualRect(model.index(row, COL_STATE))
    return view.viewport().grab(rect).toImage()


def _ink(img: QImage) -> int:
    """How many pixels differ from the cell's own background (the corner pixel)."""
    bg = img.pixel(0, 0)
    return sum(1 for y in range(img.height()) for x in range(img.width()) if img.pixel(x, y) != bg)


def test_a_pending_state_cell_paints_a_spinner_that_turns_and_stops_when_the_state_arrives(themed, monkeypatch):
    model = _deferred(["Made Up One"])
    view, delegate = _table(model)
    clk = busy.clock()
    monkeypatch.setattr(busy, "angle", lambda now=None: 0.0)
    first = _cell_image(view, model)
    assert _ink(first) > 40 and clk.active              # a ring and "Checking…" where the cell was empty
    monkeypatch.setattr(busy, "angle", lambda now=None: 180.0)
    second = _cell_image(view, model)
    assert first != second                              # the arc moved
    assert delegate._painted                            # the cells painted since the last frame
    clk.tick()                                          # a frame: repaints them (queued) and starts counting afresh
    assert delegate._painted == []
    _push_all(model)
    QApplication.processEvents()
    img = _cell_image(view, model)
    assert _ink(img) > 40                               # now the state's badge
    delegate._painted.clear()
    assert delegate._tick() is False                    # nothing busy was painted since: the clock lets it go
    clk.tick()
    assert delegate not in [r() for r in clk._clients] and not clk.active
    view.close()
    view.deleteLater()


def test_the_spinner_repaints_only_the_busy_cells(themed):
    model = _deferred(["Made Up One", "Made Up Two"])
    _push_all(model)
    model.set_entries([_entry("Made Up One"), _entry("Made Up Two"), _entry("Made Up Three")])
    view, delegate = _table(model)
    delegate._painted.clear()                           # (showing the table painted every cell in view)
    _cell_image(view, model, 0)
    _cell_image(view, model, 1)
    assert delegate._painted == []                      # rows with a state to show are not busy
    _cell_image(view, model, 2)
    assert len(delegate._painted) == 1
    assert delegate._painted[0][1] == view.visualRect(model.index(2, COL_STATE))
    view.close()
    view.deleteLater()


def test_a_dark_spinner_is_drawn_in_the_palettes_ink(themed):
    theme.apply_theme(themed, "dark")
    model = _deferred(["Made Up One"])
    view, _ = _table(model)
    img = _cell_image(view, model)
    pal = theme.current()
    colors = {QColor(img.pixel(x, y)).name() for y in range(img.height()) for x in range(img.width())}
    assert pal.muted_2 in colors or pal.muted in colors
    view.close()
    view.deleteLater()


# --- chips and the tab ---------------------------------------------------------------------------------------------

def test_a_busy_chip_swaps_its_number_for_a_spinner_and_back(qapp):
    chip = ChipButton("Rename pending", "rename")
    chip.set_count(7)
    plain = chip.sizeHint().width()
    chip.set_busy(True)
    assert chip.busy and busy.clock().active
    assert chip.sizeHint().width() != plain
    img = QImage(chip.sizeHint(), QImage.Format.Format_ARGB32)
    img.fill(QColor("white"))
    chip.render(img)
    busy.clock().tick()
    assert chip.busy and busy.clock().active
    chip.set_busy(False)
    assert not chip.busy and chip.sizeHint().width() == plain
    assert chip.count == 7                              # the count itself was never touched
    chip.deleteLater()


def test_a_busy_tab_badge_shows_a_spinner_pill(qapp):
    tab = TabButton("Download")
    base = tab.sizeHint().width()
    tab.set_busy(True)
    assert tab.sizeHint().width() > base                # the pill is there with no number
    tab.set_badge(4)
    tab.set_busy(False)
    assert tab.badge == 4 and not tab.busy
    tab.deleteLater()


# --- the window's hooks --------------------------------------------------------------------------------------------

def _list_tab(qapp):
    tab = ListTab()
    tab.set_counts({None: 10, "rename": 3, DUPLICATES: 2})
    return tab


def test_the_list_marks_the_filters_whose_count_is_being_worked_out(qapp):
    tab = _list_tab(qapp)
    rename_action = tab._more_actions["rename"]
    assert rename_action.text().endswith("3")
    tab.set_busy_counts({"rename"})
    assert rename_action.text().endswith("…") and "3" not in rename_action.text()
    tab.set_filter("rename")
    assert tab.chip_more.busy                           # the More chip names the filter and shows its spinner
    tab.set_busy_counts(set())
    assert rename_action.text().endswith("3") and not tab.chip_more.busy
    tab.deleteLater()


def test_the_window_asks_for_spinners_while_the_long_counts_run(qapp):
    busy_keys = []
    win = SimpleNamespace(_rename_call=None, _rename_again=False, _dupe_call=None,
                          _list=SimpleNamespace(set_busy_counts=lambda keys: busy_keys.append(set(keys))))
    MainWindow._update_busy_counts(win)
    win._rename_call = object()
    MainWindow._update_busy_counts(win)
    win._dupe_call = object()
    MainWindow._update_busy_counts(win)
    win._rename_call = None
    win._rename_again = True                            # a recount is queued behind the running one
    MainWindow._update_busy_counts(win)
    assert busy_keys[0] == set() and busy_keys[1] == {"rename"} and busy_keys[2] == {"rename", DUPLICATES}
    assert busy_keys[3] == {"rename", DUPLICATES}


def test_the_download_tab_and_its_badge_wait_with_spinners_for_the_states(qapp):
    seen = []
    tab = SimpleNamespace(set_waiting=lambda flag: seen.append(("tab", flag)))
    top = SimpleNamespace(set_download_busy=lambda flag: seen.append(("top", flag)))
    win = SimpleNamespace(_wanted_waiting=False, _download_tab=tab, _top=top)
    MainWindow._set_wanted_waiting(win, True)
    MainWindow._set_wanted_waiting(win, True)           # unchanged: nothing more is sent
    MainWindow._set_wanted_waiting(win, False)
    assert seen == [("tab", True), ("top", True), ("tab", False), ("top", False)]
