"""State / Gaps / Official source columns, the state filter, the Wanted panel and the detail panel
(offscreen Qt, synthetic entries - no files, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mangalist.gui.table_model import (  # noqa: E402
    COL_BEHIND,
    COL_COMPLETED,
    COL_LICENSED,
    COL_MU_TITLE,
    COL_GAPS,
    COL_OFFICIAL,
    COL_STATE,
    COLUMNS,
    STATE_FILTERS,
    MangaTableModel,
    state_matches,
)
from mangalist.knowledge import from_mangapixer_item  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402
from mangalist.states import State  # noqa: E402

from .helpers import TODAY, item  # noqa: E402

ROOT = Path("/library/Manga")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _entry(name, files=(), **mu) -> MangaEntry:
    folder = ROOT / name
    e = MangaEntry(folder=folder, title=name, english_title=None)
    e.files = [FileHit(path=folder / f, size=1, depth=0, has_volume=v, has_chapter=c) for f, v, c in files]
    for key, value in mu.items():
        setattr(e, key, value)
    return e


def _matched(**extra):
    base = dict(mu_id=1, mu_title="x", mu_band="auto", licensed=False)
    base.update(extra)
    return base


def entries():
    return [
        _entry("Alpha Empty", **_matched(mu_title="Alpha Empty")),
        _entry("Bravo Chapters", [(f"Bravo Ch.{n}.cbz", False, True) for n in (1, 2, 4)],
               **_matched(mu_title="Bravo Chapters", scan_latest_chapter=5.0)),
        _entry("Charlie Unknown", [("Charlie v01.cbz", True, False)]),
        _entry("Delta Volumes", [(f"Delta v0{n}.cbz", True, False) for n in (1, 2, 3)],
               **_matched(mu_title="Delta Volumes", licensed=True, publisher_name="Example Press",
                          publisher_volumes=3.0, publisher_status="Completed", completed_in_origin=True)),
        _entry("Echo Upgrade", [(f"Echo Ch.{n}.cbz", False, True) for n in range(1, 42)] + [
            ("Echo Ch.24.5.cbz", False, True)], **_matched(mu_title="Echo Upgrade")),
    ]


@pytest.fixture
def model(qapp):
    m = MangaTableModel(entries())
    m.set_state_providers(today=TODAY)
    return m


def _cell(model, row, col, role=Qt.DisplayRole):
    return model.data(model.index(row, col), role)


def test_new_columns_are_appended_and_keep_the_old_ones(model):
    assert COLUMNS[:15] == ["✓", "Dupe", "Title", "Alternative Title", "Files", "Subfolders", "Vol %", "Ch %",
                            "Both %", "Verdict", "Last Modified", "MU Title", "Licensed", "Behind", "Completed"]
    assert COLUMNS[COL_STATE] == "State" and COLUMNS[COL_GAPS] == "Gaps" and COLUMNS[COL_OFFICIAL] == "Official source"
    assert model.columnCount() == 18


def test_state_gaps_and_official_cells_from_the_fallback(model):
    assert _cell(model, 0, COL_STATE) == "Wanted - scanlation only"
    assert _cell(model, 1, COL_STATE) == "Missing chapters"
    assert _cell(model, 1, COL_GAPS) == "Ch. 3, 5"
    assert "Missing chapters:\n  Ch. 3\n  Ch. 5" == _cell(model, 1, COL_GAPS, Qt.ToolTipRole)
    assert _cell(model, 2, COL_STATE) == "Can't tell"   # not looked up yet: no attention flag
    assert _cell(model, 3, COL_STATE) == "Complete"
    assert _cell(model, 3, COL_OFFICIAL) == "Example Press"
    assert _cell(model, 0, COL_OFFICIAL) == "Search only"
    tip = _cell(model, 0, COL_OFFICIAL, Qt.ToolTipRole)
    assert "Amazon (search) [search, search]: https://www.amazon.com/s?k=Alpha+Empty" in tip


def test_state_sort_and_gap_sort(model):
    ranks = [_cell(model, r, COL_STATE, Qt.UserRole) for r in range(5)]
    assert ranks[0] < ranks[1] < ranks[3]                 # wanted < missing < complete
    assert _cell(model, 1, COL_GAPS, Qt.UserRole) == 2


def test_mangapixer_knowledge_and_the_disagreement_tooltip(model):
    k = from_mangapixer_item(item())                         # MissingSome; vol. 1-3 out, ch. 1-24.5 in them
    model.set_state_providers(knowledge_for=lambda e: k if e.title == "Echo Upgrade" else None)
    assert _cell(model, 4, COL_STATE) == "Upgrade available  ·  Upcoming"
    tip = _cell(model, 4, COL_STATE, Qt.ToolTipRole)
    assert "MangaPixer: MissingSome (Running) - differs; MangaList's own count is shown" in tip
    assert _cell(model, 4, COL_GAPS) == "Upgrade vol. 1-3"
    assert _cell(model, 4, COL_OFFICIAL) == "Official (original language) (+3)"


def test_a_changed_row_is_recomputed(model):
    assert _cell(model, 1, COL_STATE) == "Missing chapters"
    model.entry_at(1).behind_override = "done"
    model.dataChanged.emit(model.index(1, COL_BEHIND), model.index(1, COL_BEHIND))
    assert _cell(model, 1, COL_STATE) == "Up to date"
    model.entry_at(1).behind_override = None
    assert model.clear_mu_match(1)
    assert _cell(model, 1, COL_STATE).startswith("Can't tell")


def test_the_inventory_provider_replaces_the_fallback(model):
    from mangalist.states import InventorySnapshot

    model.set_state_providers(inventory_for=lambda e: InventorySnapshot(held_chapters=["1", "2", "3", "4", "5"])
                              if e.title == "Bravo Chapters" else None)
    assert _cell(model, 1, COL_STATE) == "Up to date"
    assert _cell(model, 0, COL_STATE) == "Wanted - scanlation only"   # None -> the fallback


def test_state_filters(model):
    keys = [k for k, _ in STATE_FILTERS]
    assert keys[:3] == [None, "wanted", "missing"] and State.UPGRADE.value in keys and "attention" in keys
    states = [model.state_at(r) for r in range(5)]
    assert [state_matches(s, "wanted") for s in states] == [True, False, False, False, False]
    assert [state_matches(s, "missing") for s in states] == [False, True, False, False, False]
    assert [state_matches(s, "Complete") for s in states] == [False, False, False, True, False]
    assert state_matches(None, None) and not state_matches(None, "wanted")


@pytest.fixture
def window(qapp):
    from mangalist import store
    from mangalist.gui.main_window import MainWindow

    store.reset_stores()
    opened = []
    win = MainWindow()
    win._open_url = opened.append
    win._wanted._open_url = opened.append
    win._model.set_state_providers(today=TODAY)
    win._on_scan_finished(entries())
    try:
        yield win, opened
    finally:
        win.close()
        win.deleteLater()


def test_main_window_state_filter(window):
    win, _ = window
    assert win._proxy.rowCount() == 5
    win._state_combo.setCurrentIndex(win._state_combo.findData("wanted"))
    assert win._proxy.rowCount() == 1
    assert win._model.entry_at(win._proxy.mapToSource(win._proxy.index(0, 0)).row()).title == "Alpha Empty"
    win._state_combo.setCurrentIndex(win._state_combo.findData("Missing chapters"))
    assert win._proxy.rowCount() == 1 and "Missing chapters" in win._status_label.text()
    win._state_combo.setCurrentIndex(0)
    assert win._proxy.rowCount() == 5


def test_wanted_panel_groups_links_and_selection(window):
    win, opened = window
    assert not win._wanted_dock.isVisible() or win._wanted_dock.isHidden() is False
    win._wanted.rebuild(win._model)
    tree = win._wanted.tree
    assert [tree.topLevelItem(i).text(0) for i in range(3)] == [
        "Wanted  (1)", "Missing  (1)", "Upgrade available  (0)"]
    assert win._wanted.series_titles(0) == ["Alpha Empty"] and win._wanted.series_titles(1) == ["Bravo Chapters"]
    series = tree.topLevelItem(0).child(0)
    assert series.text(1) == "Wanted - scanlation only"
    link = series.child(0)
    assert link.text(0) == "Amazon (search)"
    win._wanted._on_double_clicked(link, 0)
    assert opened == ["https://www.amazon.com/s?k=Alpha+Empty&i=stripbooks"]
    # A series opens its first page; "Show in table" selects it, clearing a filter that hid it.
    tree.setCurrentItem(series)
    win._wanted.open_selected()
    assert len(opened) == 2
    win._state_combo.setCurrentIndex(win._state_combo.findData("missing"))
    win._wanted.show_selected()
    assert win._state_combo.currentIndex() == 0
    sel = win._table.selectionModel().selectedRows()
    assert [win._model.entry_at(win._proxy.mapToSource(i).row()).title for i in sel] == ["Alpha Empty"]


def test_wanted_panel_toggle_is_remembered(window):
    from mangalist import config

    win, _ = window
    assert win._btn_wanted.text() == "Wanted panel" and win._btn_wanted.isCheckable()   # a button, not flat text
    win.show()
    win._btn_wanted.click()
    assert win._wanted_dock.isVisible() and config.load().get("wanted_panel") is True
    assert win._btn_wanted.isChecked()
    assert win._wanted.tree.topLevelItemCount() == 3      # built when shown
    win._wanted_dock.close()                              # the dock's own close button: the button follows
    assert not win._btn_wanted.isChecked() and config.load().get("wanted_panel") is False
    win._wanted_toggle.trigger()
    assert win._btn_wanted.isChecked()


def test_detail_panel_shows_state_gaps_and_links(window):
    win, _ = window
    win._table.selectRow(win._proxy.mapFromSource(win._model.index(1, 0)).row())
    d = win._detail
    assert d._lbl_state.text() == "Missing chapters"
    assert d._lbl_gaps.text() == "Ch. 3, 5" and "Missing chapters:" in d._lbl_gaps.toolTip()
    assert d._lbl_mangapixer.text() == "-"
    assert '<a href="https://www.amazon.com/s?k=Bravo+Chapters&amp;i=stripbooks">Amazon (search)</a>' in \
        d._links_label.text()
    assert d._links_label.openExternalLinks()
    d.show_entry(None)
    assert d._lbl_state.text() == "-" and d._links_label.text() == "-"


def test_default_column_order_puts_state_next_to_the_match(window):
    win, _ = window
    header = win._table.horizontalHeader()
    order = sorted(range(len(COLUMNS)), key=header.visualIndex)
    names = [COLUMNS[i] for i in order]
    assert names[:7] == ["✓", "Dupe", "Title", "MU Title", "State", "Gaps", "Behind"]
    assert names.index("Official source") == names.index("Completed") + 1


def test_main_window_uses_mangapixer_for_known_folders_and_skips_their_mu_lookup(window):
    # A folder MangaPixer knows: its state knowledge comes from the export item, and "Check MU" fetches
    # nothing for it (owner decision, MangaPixer Data Source 2026-10-02).
    win, _ = window
    entry = win._model.entry_at(0)
    win._mp_items[str(entry.folder)] = item()
    win._model.refresh_states()
    assert win._knowledge_for(entry).source == "mangapixer"
    win._mu_thread = None
    win._start_mu_lookup([entry])
    assert win._mu_thread is None  # no worker started: nothing to fetch
    assert "MangaPixer knows them" in win._status_label.text()


def test_old_columns_follow_mangapixer_and_stay_empty_when_not_a_series(model):
    # "Bravo Chapters" was matched by the own matcher earlier (cached: licensed No, Behind from ch. 5).
    assert _cell(model, 1, COL_MU_TITLE) == "Bravo Chapters" and _cell(model, 1, COL_LICENSED) == "No"
    about = from_mangapixer_item(item(link={"state": "CollectionAbout"}))
    model.set_state_providers(knowledge_for=lambda e: about if e.title == "Bravo Chapters" else None)
    # MangaPixer: a collection about a series -> no stale own-matcher numbers, the State cell says why.
    for col in (COL_MU_TITLE, COL_LICENSED, COL_BEHIND, COL_COMPLETED, COL_GAPS):
        assert _cell(model, 1, col) == ""
    assert _cell(model, 1, COL_STATE) == "Collection about Example Quest"
    # MangaPixer knows the series: the old columns show MangaPixer's record, not the cached own match.
    known = from_mangapixer_item(item())
    model.set_state_providers(knowledge_for=lambda e: known if e.title == "Bravo Chapters" else None)
    assert _cell(model, 1, COL_MU_TITLE) == "✔ Example Quest"
    assert _cell(model, 1, COL_LICENSED) == "Yes"
