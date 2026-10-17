"""The UI defects of the 2026-10-16 taste review (offscreen Qt, made-up series): the details panel never wider than
its view (the Unbundled check box's text wraps, the close button stays in sight), the matched title shown only when it
says something new, the State cell's flags whole or counted ("+1") - never cut to "Up..." -, the Title column
stretching so Kind and Files stay in view at 1200 px (remembered widths still win), and the empty state (an icon, one
sentence, the button; no filter bar or details panel until there is a library)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFontMetrics, QHelpEvent, QImage, QPainter, QStandardItem, QStandardItemModel  # noqa: E402,E501
from PySide6.QtWidgets import QScrollArea, QStyleOptionViewItem, QTableView  # noqa: E402

from mangalist import config  # noqa: E402
from mangalist.classifier import classify  # noqa: E402
from mangalist.gui import list_delegates, theme  # noqa: E402
from mangalist.gui.detail_panel import UNBUNDLED_TEXT, DetailPanel  # noqa: E402
from mangalist.gui.list_delegates import StateBadgeDelegate, fit_flags, shown_subtitle  # noqa: E402
from mangalist.gui.list_tab import PAGE_EMPTY, PAGE_TABLE, TITLE_MIN_WIDTH, ListTab  # noqa: E402
from mangalist.gui.list_text import same_title  # noqa: E402
from mangalist.gui.main_window import MainWindow  # noqa: E402
from mangalist.gui.palette import DARK, LIGHT  # noqa: E402
from mangalist.gui.table_model import COL_FILES, COL_STATE, COL_TITLE, COL_VERDICT, STATE_ROLE  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402

from ..states.helpers import own  # noqa: E402
from .conftest import qapp  # noqa: E402,F401
from .test_dark_mode import themed  # noqa: E402,F401
from .test_shell_window import PLAIN, QUEST, _entry, make_window  # noqa: E402,F401


def _long_entry() -> MangaEntry:
    """A made-up chapters series with a long, space-free folder path and long file names."""
    folder = Path("/lib/an_extremely_long_library_folder_name_without_any_spaces/Made_Up_Series_With_A_Long_Name")
    files = [FileHit(folder / f"Made_Up_Series_With_A_Long_Name_c{n:03d}_(Digital)_(Example-Group).cbz", 14_000_000, 0,
                     has_chapter=True) for n in range(1, 41)]
    entry = MangaEntry(folder=folder, title="Made Up Series", english_title="Made Up Series", files=files)
    classify(entry)
    return entry


def _shown_panel(qapp, width: int, height: int = 500) -> DetailPanel:
    panel = DetailPanel()
    panel.set_downloads_enabled(True)
    panel.set_auto_enabled(True)
    panel.resize(width, height)
    panel.show()
    panel.show_entry(_long_entry())
    panel.set_auto_choice(None, "notify", library=True)
    panel.set_download("Filed v03-v05")
    for _ in range(3):                                  # the body's re-measure is a posted event
        qapp.processEvents()
    return panel


# --- 1. the details panel ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width", [300, 380])
def test_the_details_panel_is_never_wider_than_its_view(qapp, width):
    panel = _shown_panel(qapp, width)
    scroll = panel.findChild(QScrollArea)
    body, view = scroll.widget(), scroll.viewport()
    assert body.width() == view.width()                             # no row sets the minimum width
    assert scroll.horizontalScrollBar().maximum() == 0
    close = panel.btn_close
    right = close.mapTo(body, QPoint(close.width(), 0)).x()
    assert 0 < right <= view.width()                                # the close button is in sight
    row = panel.unbundled_row
    assert row.mapTo(body, QPoint(row.width(), 0)).x() <= view.width()
    assert panel.unbundled_check.text() == "" and row.text() == UNBUNDLED_TEXT
    assert panel.unbundled_check.accessibleName() == UNBUNDLED_TEXT
    one_line = QFontMetrics(row.label.font()).height()
    if width == 300:
        assert row.label.height() >= 2 * one_line                   # wrapped, not cut
    panel.close()


def test_a_long_series_scrolls_the_details_vertically(qapp):
    """The body's height follows a new series even when its width does not change (it used to keep the old height:
    the rows squeezed into the view, no scroll bar)."""
    panel = DetailPanel()
    panel.resize(380, 400)
    panel.show()
    qapp.processEvents()
    panel.show_entry(_long_entry())
    for _ in range(3):
        qapp.processEvents()
    scroll = panel.findChild(QScrollArea)
    body = scroll.widget()
    assert body.height() >= body.heightForWidth(scroll.viewport().width()) > scroll.viewport().height()
    assert scroll.verticalScrollBar().maximum() > 0
    panel.close()


def test_a_click_on_the_unbundled_text_toggles_the_switch(qapp):
    panel = _shown_panel(qapp, 380)
    seen = []
    panel.unbundled_toggled.connect(seen.append)
    panel.set_unbundled_choice(False, available=True)
    panel.unbundled_row.label.clicked.emit()
    assert panel.unbundled_check.isChecked() and seen == [True]
    panel.set_unbundled_choice(False, available=False)              # not scanned: greyed, a click does nothing
    panel.unbundled_row.label.clicked.emit()
    assert not panel.unbundled_check.isEnabled() and not panel.unbundled_check.isChecked() and seen == [True]
    assert panel.unbundled_row.toolTip() == "Not available until the folder is scanned"
    panel.close()


# --- 2. the matched title, and the State cell's flags ----------------------------------------------------------------

def test_same_title_ignores_case_spaces_and_punctuation():
    assert same_title("Ashen Prince", "ashen prince!")
    assert same_title("Made-Up: Story", "Made Up Story")
    assert not same_title("Brine and Bone", "Brine & Bone")
    assert not same_title("Made Up Story", "")
    assert same_title("!!!", "!!!") and not same_title("!!!", "???")       # nothing but punctuation: compared as is


def test_the_matched_title_shows_only_when_it_says_something_new():
    assert shown_subtitle("Ashen Prince", "Ashen Prince", review=False) == ""
    assert shown_subtitle("Ashen Prince", "ASHEN PRINCE.", review=False) == ""
    assert shown_subtitle("Example Saga / Side Story", "Side Story", review=False) == ""     # a subseries' part
    assert shown_subtitle("Umbral Ferry", "Umbral Ferry (Light Novel)", review=False) == "Umbral Ferry (Light Novel)"
    assert shown_subtitle("Ashen Prince", "Ashen Prince", review=True) == "Ashen Prince"      # needs review: kept
    assert shown_subtitle("Ashen Prince", "", review=False) == ""


def test_the_details_header_does_not_repeat_the_title(qapp):
    panel = DetailPanel()
    entry = _long_entry()                                   # its English title is its own title
    known = own(title="made up series")
    panel.show_entry(entry, knowledge=known)
    assert panel._eng_label.text() == "MangaUpdates: matched"
    entry.english_title = "A Different English Title"
    panel.show_entry(entry, knowledge=own(title="Made Up Series (Novel)"))
    assert panel._eng_label.text() == "A Different English Title · Made Up Series (Novel) · MangaUpdates: matched"


class _Recorder(QPainter):
    """A painter that remembers the texts it was asked to draw."""

    def __init__(self, device):
        super().__init__(device)
        self.texts = []

    def drawText(self, *args):
        self.texts.append(args[-1])
        return super().drawText(*args)


def _paint_state(width: int, value: str, flags) -> list:
    delegate = StateBadgeDelegate()
    model = QStandardItemModel(1, 1)
    item = QStandardItem("x")
    item.setData(SimpleNamespace(state=SimpleNamespace(value=value), reasons=[], flags=tuple(flags)), STATE_ROLE)
    model.setItem(0, 0, item)
    img = QImage(width, 50, QImage.Format.Format_ARGB32)
    img.fill(QColor("#808080"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, 50)
    painter = _Recorder(img)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    return painter.texts


def test_flags_are_whole_or_counted_never_cut(qapp):
    fm = QFontMetrics(theme.font(12, 400))
    flags = ("Upcoming", "Needs attention")
    assert fit_flags(flags, 1000, fm) == "Upcoming  ·  Needs attention"
    assert fit_flags(flags, fm.horizontalAdvance("Upcoming  ·  +1"), fm) == "Upcoming  ·  +1"
    assert fit_flags(flags, fm.horizontalAdvance("Upcoming"), fm) == "+2"
    assert fit_flags(flags, 3, fm) == ""
    assert fit_flags((), 1000, fm) == ""
    # painted: the window's default State width fits the longest badge and one flag
    width = MainWindow._DEFAULT_WIDTHS[COL_STATE]
    assert _paint_state(width, "Upgrade available", ["Upcoming"]) == ["Upgrade available", "Upcoming"]
    assert _paint_state(width, "Missing volumes", flags) == ["Missing volumes", "+2"]               # no room for both
    assert _paint_state(400, "Missing volumes", flags) == ["Missing volumes", "Upcoming  ·  Needs attention"]
    assert _paint_state(width + 40, "Missing volumes", flags) == ["Missing volumes", "Upcoming  ·  +1"]
    assert _paint_state(170, "Missing chapters", flags) == ["Missing chapters", "+2"]       # an old remembered width
    assert not any("…" in t for t in _paint_state(150, "Missing chapters", flags)[1:])


def test_the_state_tooltip_names_every_flag(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(list_delegates.QToolTip, "showText", lambda pos, text, *a: shown.append(text))
    view = QTableView()
    model = QStandardItemModel(1, 1)
    item = QStandardItem("x")
    st = SimpleNamespace(state=SimpleNamespace(value="Complete"), reasons=[], flags=("Omnibus", "Upgrade available"))
    item.setData(st, STATE_ROLE)
    item.setData("Complete\nUpgrade available: vol. 5 in English", Qt.ItemDataRole.ToolTipRole)
    model.setItem(0, 0, item)
    view.setModel(model)
    delegate = StateBadgeDelegate(view)
    event = QHelpEvent(QEvent.Type.ToolTip, QPoint(5, 5), QPoint(5, 5))
    assert delegate.helpEvent(event, view, QStyleOptionViewItem(), model.index(0, 0))
    assert shown == ["Complete\nUpgrade available: vol. 5 in English\nOmnibus"]     # only the flag not named yet


def test_a_pending_state_cell_still_spins(qapp):
    """The State cell's spinner (UI performance stage 2) is untouched by the flags' fitting."""
    delegate = StateBadgeDelegate()
    model = QStandardItemModel(1, 1)
    item = QStandardItem("x")
    item.setData(True, list_delegates.PENDING_ROLE)
    model.setItem(0, 0, item)
    img = QImage(204, 50, QImage.Format.Format_ARGB32)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 204, 50)
    painter = _Recorder(img)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    assert painter.texts == [list_delegates.PENDING_TEXT]


# --- 4. the columns ---------------------------------------------------------------------------------------------------

def _visible_columns_fit(win) -> None:
    table = win._table
    header = table.horizontalHeader()
    for col in (COL_VERDICT, COL_FILES):
        assert not header.isSectionHidden(col)
        assert header.sectionViewportPosition(col) + header.sectionSize(col) <= table.viewport().width()
    assert table.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize("width", [1200, 1600])
def test_the_title_column_stretches_and_kind_and_files_stay_in_view(make_window, qapp, width):
    win = make_window()
    win.resize(width, 760)
    win.show()
    qapp.processEvents()
    header = win._table.horizontalHeader()
    others = sum(header.sectionSize(c) for c in range(header.count())
                 if c != COL_TITLE and not header.isSectionHidden(c))
    assert header.sectionSize(COL_TITLE) == win._table.viewport().width() - others >= TITLE_MIN_WIDTH
    assert not header.stretchLastSection()
    for col in (COL_STATE, COL_VERDICT):
        assert header.sectionSize(col) == MainWindow._DEFAULT_WIDTHS[col]
    _visible_columns_fit(win)


def test_a_narrow_table_keeps_the_title_readable_and_scrolls(make_window, qapp):
    win = make_window()
    win.resize(1400, 700)
    win.show()
    qapp.processEvents()
    header = win._table.horizontalHeader()
    header.resizeSection(COL_STATE, 600)                    # the others leave less than the Title's minimum
    qapp.processEvents()
    assert header.sectionSize(COL_TITLE) == TITLE_MIN_WIDTH and win._table.horizontalScrollBar().maximum() > 0
    header.resizeSection(COL_STATE, 150)                    # room again: it stretches again
    qapp.processEvents()
    assert header.sectionSize(COL_TITLE) > TITLE_MIN_WIDTH and win._table.horizontalScrollBar().maximum() == 0


def test_remembered_widths_still_win(make_window, qapp):
    win = make_window()
    win.resize(1600, 760)
    win.show()
    qapp.processEvents()
    win._table.horizontalHeader().resizeSection(COL_STATE, 260)
    win._save_column_state()
    again = make_window()
    again.resize(1600, 760)
    again.show()
    qapp.processEvents()
    assert again._table.horizontalHeader().sectionSize(COL_STATE) == 260


def test_a_layout_remembered_while_the_last_column_stretched_gets_files_back_to_size(make_window, qapp):
    """Before, the last column (Files) stretched: the width remembered for it was what the window left over."""
    win = make_window()
    win.resize(1600, 760)
    win.show()
    qapp.processEvents()
    header = win._table.horizontalHeader()
    header.resizeSection(COL_STATE, 240)                    # an owner's choice: kept
    header.setStretchLastSection(True)                      # the old layout
    header.setSectionResizeMode(COL_TITLE, header.ResizeMode.Interactive)
    header.resizeSection(COL_TITLE, 300)
    qapp.processEvents()
    assert header.sectionSize(COL_FILES) > 300
    win._save_column_state()
    again = make_window()
    again.resize(1200, 700)
    again.show()
    qapp.processEvents()
    header = again._table.horizontalHeader()
    assert header.sectionSize(COL_STATE) == 240
    files_default = MainWindow._DEFAULT_WIDTHS[COL_FILES]
    assert header.sectionSize(COL_FILES) == max(header.sectionSizeHint(COL_FILES), files_default)
    assert not header.stretchLastSection()
    assert config.load()["list_column_state"]               # (the old layout was read, not thrown away)


# --- 6. the empty state -----------------------------------------------------------------------------------------------

def test_the_first_run_shows_an_icon_one_sentence_and_the_button(make_window, qapp):
    win = make_window(entries=False)
    win.resize(1200, 700)
    win.show()
    assert not win.start_initial_scan()
    qapp.processEvents()
    lst = win._list
    assert lst.stack.currentIndex() == PAGE_EMPTY
    assert not lst.empty_icon.pixmap().isNull() and lst.empty_icon.isVisible()
    text = lst.empty_text.text()
    assert text.endswith(".") and text.count(". ") == 0                 # one sentence
    assert lst.btn_add_root.isVisible() and lst.btn_add_root.property("variant") == "primary"
    assert not lst.filter_bar.isVisible()                               # no chips at 0, no search over nothing
    assert not lst.details.isVisible() and not lst.btn_details.isVisible()
    assert lst.details_visible()                                        # the owner's choice is kept meanwhile
    win._on_scan_finished([_entry(QUEST), _entry(PLAIN)])
    win._stop_signatures()
    qapp.processEvents()
    assert lst.stack.currentIndex() == PAGE_TABLE
    assert lst.filter_bar.isVisible() and lst.details.isVisible() and not lst.btn_details.isVisible()


def test_a_collapsed_details_panel_stays_collapsed_when_the_library_arrives(qapp):
    lst = ListTab()
    lst.resize(1200, 600)
    lst.show()
    lst.show_page(PAGE_EMPTY)
    lst.set_details_visible(False)
    assert not lst.details.isVisible() and not lst.btn_details.isVisible()     # nothing to reopen yet
    lst.show_page(PAGE_TABLE)
    assert not lst.details.isVisible() and lst.btn_details.isVisible() and not lst.details_visible()
    lst.close()


def test_the_empty_states_icon_follows_the_theme(themed):
    lst = ListTab()
    size = lst.empty_icon.pixmap().width()

    def fill() -> str:                                      # the soft circle, left of the folder
        return lst.empty_icon.pixmap().toImage().pixelColor(int(size * 0.15), size // 2).name()

    assert fill() == LIGHT.accent_tint
    theme.apply_theme(themed, "dark")
    assert fill() == DARK.accent_tint
    lst.close()
