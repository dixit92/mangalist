"""Every table's columns can be resized by the owner (no Stretch column: it cannot be dragged and squeezes the rest)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QHeaderView, QTableWidget  # noqa: E402

from mangalist.gui.tables import cell, resizable_columns  # noqa: E402

from .conftest import qapp  # noqa: E402,F401

GUI = Path(__file__).resolve().parents[2] / "mangalist" / "gui"


def test_resizable_columns(qapp):
    table = QTableWidget(0, 3)
    resizable_columns(table, {0: 250})
    header = table.horizontalHeader()
    assert header.stretchLastSection() and table.columnWidth(0) == 250
    assert all(header.sectionResizeMode(c) == QHeaderView.ResizeMode.Interactive for c in range(3))
    assert cell("A long library name").toolTip() == "A long library name"


def test_no_gui_table_uses_a_stretch_column():
    offenders = [p.name for p in GUI.glob("*.py") if "Stretch)" in p.read_text(encoding="utf-8")]
    assert offenders == []
