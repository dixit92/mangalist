"""A ticked List column is never invisible (owner, 2026-10-26: "I think the columns are buggy. I can't see the completed
column"): eight columns had no default width, Qt reports a hidden column's width as 0, so a column shown from the menu
or restored from a layout saved that way was 0 px wide. Made-up names only."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist import config  # noqa: E402
from mangalist.gui.main_window import MainWindow  # noqa: E402
from mangalist.gui.table_model import COL_COMPLETED, COL_ENG, COLUMNS  # noqa: E402

from .conftest import qapp  # noqa: E402,F401
from .test_shell_window import make_window  # noqa: E402,F401


def _shown(make_window, qapp):
    win = make_window()
    win.resize(1600, 760)
    win.show()
    qapp.processEvents()
    return win


def test_every_list_column_has_a_default_width():
    assert set(MainWindow._DEFAULT_WIDTHS) == set(range(len(COLUMNS)))
    assert all(w > 0 for w in MainWindow._DEFAULT_WIDTHS.values())


def test_ticking_a_hidden_column_shows_it_at_a_real_width(make_window, qapp):
    win = _shown(make_window, qapp)
    header = win._table.horizontalHeader()
    if not header.isSectionHidden(COL_COMPLETED):
        win._toggle_column(COL_COMPLETED)                       # hide it first
    assert header.isSectionHidden(COL_COMPLETED) and header.sectionSize(COL_COMPLETED) == 0
    win._toggle_column(COL_COMPLETED)
    qapp.processEvents()
    assert not header.isSectionHidden(COL_COMPLETED)
    assert header.sectionSize(COL_COMPLETED) >= max(header.sectionSizeHint(COL_COMPLETED), 24)


def test_a_layout_saved_with_a_shown_column_at_zero_width_gets_it_back(make_window, qapp):
    """The owner's saved layout: Completed and Alternative Title shown (not in the hidden list) but 0 px wide."""
    win = _shown(make_window, qapp)
    header = win._table.horizontalHeader()
    cfg = config.load()
    hidden = [n for n in (cfg.get("list_hidden_columns") or []) if n not in ("Completed", "Alternative Title")]
    cfg["list_hidden_columns"] = hidden
    cfg["list_columns_version"] = MainWindow._COLUMNS_VERSION
    config.save(cfg)
    for col in (COL_COMPLETED, COL_ENG):
        header.setSectionHidden(col, False)
    header.setMinimumSectionSize(0)
    for col in (COL_COMPLETED, COL_ENG):
        header.resizeSection(col, 0)
    assert header.sectionSize(COL_COMPLETED) == 0
    win._save_column_state()
    again = _shown(make_window, qapp)
    header = again._table.horizontalHeader()
    for col in (COL_COMPLETED, COL_ENG):
        assert not header.isSectionHidden(col)
        assert header.sectionSize(col) >= max(header.sectionSizeHint(col), 24)
