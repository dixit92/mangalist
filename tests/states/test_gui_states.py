"""State / Gaps / Official source / English columns, the state chips and the details panel (offscreen Qt, synthetic
entries - no files, no network)."""

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
    COL_ENGLISH,
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
    assert COLUMNS[COL_ENGLISH] == "English" and model.columnCount() == 19
    assert model.headerData(9, Qt.Horizontal) == "Kind"          # "Verdict" keeps its name in the settings


def test_state_gaps_and_official_cells_from_the_fallback(model):
    assert _cell(model, 0, COL_STATE) == "Wanted - scanlation only"
    assert _cell(model, 1, COL_STATE) == "Missing chapters"
    assert _cell(model, 1, COL_GAPS) == "Ch. 3, 5"
    assert "Missing chapters:\n  Ch. 3\n  Ch. 5" == _cell(model, 1, COL_GAPS, Qt.ToolTipRole)
    assert _cell(model, 2, COL_STATE) == "Can't tell"   # not looked up yet: no attention flag
    assert _cell(model, 3, COL_STATE) == "Complete"
    assert _cell(model, 3, COL_OFFICIAL) == "Example Press"
    assert _cell(model, 0, COL_OFFICIAL) == "Search only"
    assert _cell(model, 3, COL_ENGLISH) == "Example Press" and _cell(model, 1, COL_ENGLISH) == "Not licensed"
    assert _cell(model, 2, COL_ENGLISH) == ""                # nothing known
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
    win._model.set_state_providers(today=TODAY)
    win._on_scan_finished(entries())
    try:
        yield win, opened
    finally:
        win.close()
        win.deleteLater()


def _shown(win):
    return [win._model.entry_at(win._proxy.mapToSource(win._proxy.index(r, 0)).row()).title
            for r in range(win._proxy.rowCount())]


def test_main_window_state_chips_filter_and_count(window):
    win, _ = window
    lst = win._list
    assert win._proxy.rowCount() == 5
    win._refresh_derived()
    assert lst.chips[None].count == 5 and lst.chips["Missing chapters"].count == 1
    assert lst.chips["Complete"].count == 1 and lst.chips["Can't tell"].count == 2     # Charlie; Echo: no numbers
    lst.chips["Missing chapters"].click()
    assert _shown(win) == ["Bravo Chapters"] and "Missing chapters" in win._status_label.text()
    assert lst.chips["Missing chapters"].isChecked() and not lst.chips[None].isChecked()
    # The other filters sit behind "More": the chip names the one picked, with its count.
    wanted = next(a for k, a in lst._more_actions.items() if k == "wanted")
    assert wanted.text() == "Wanted (any)   1"
    wanted.trigger()
    assert _shown(win) == ["Alpha Empty"] and lst.chip_more.isChecked() and lst.chip_more.text() == "Wanted (any) ▾"
    assert lst.chip_more.count == 1
    lst.chips[None].click()
    assert win._proxy.rowCount() == 5 and lst.chip_more.text() == "More ▾"


def test_selecting_from_elsewhere_clears_the_filters_that_hide_the_series(window):
    win, _ = window
    win._list.chips["Complete"].click()
    win._filter_edit.setText("Delta")
    win.show_folder_in_list(str(ROOT / "Alpha Empty"))
    assert win._list.current_filter() is None and win._filter_edit.text() == ""
    sel = win._table.selectionModel().selectedRows()
    assert [win._model.entry_at(win._proxy.mapToSource(i).row()).title for i in sel] == ["Alpha Empty"]


def test_detail_panel_shows_state_gaps_holds_and_links(window):
    win, _ = window
    win._table.selectRow(win._proxy.mapFromSource(win._model.index(1, 0)).row())
    d = win._detail
    assert d._lbl_state.text() == "Missing chapters" and "#fbeaea" in d._lbl_state.styleSheet()
    assert d._lbl_gaps.text() == "Ch. 3, 5" and "Missing chapters:" in d._lbl_gaps.toolTip()
    assert d._lbl_holds.text() == "Chapters 1-2, 4" and d._lbl_english.text() == "Not licensed"
    assert d._lbl_mangapixer.text() == "-" and not d._lbl_mangapixer.isVisibleTo(d)    # no MangaPixer: no row
    assert d._eng_label.text() == "Bravo Chapters · MangaUpdates: matched"
    assert '<a href="https://www.amazon.com/s?k=Bravo+Chapters&amp;i=stripbooks">Amazon (search)</a>' in \
        d._links_label.text()
    assert d._links_label.openExternalLinks()
    assert not d.btn_get.isVisibleTo(d)             # downloads are off: no Download tab to go to
    d.show_entry(None)
    assert d._lbl_state.text() == "-" and d._links_label.text() == "-"
    assert not d._links_box.isVisibleTo(d)


def test_default_columns_are_the_mockups_and_the_header_menu_remembers(window):
    from mangalist import config

    win, _ = window
    header = win._table.horizontalHeader()
    shown = [COLUMNS[i] for i in sorted(range(len(COLUMNS)), key=header.visualIndex) if not header.isSectionHidden(i)]
    assert shown == ["Title", "State", "Gaps", "English", "Verdict", "Files"]
    assert win._proxy.headerData(9, Qt.Horizontal) == "KIND"
    win._toggle_column(COLUMNS.index("MU Title"))
    assert not header.isSectionHidden(COLUMNS.index("MU Title"))
    assert "MU Title" not in config.load()["list_hidden_columns"]
    menus = []
    win._exec_menu = lambda menu, pos: menus.append(menu) or None
    win._on_header_context_menu(header.rect().center())
    texts = {a.text(): a for a in menus[0].actions()}
    assert not texts["Title"].isEnabled() and texts["Kind"].isChecked() and not texts["Dupe"].isChecked()


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
