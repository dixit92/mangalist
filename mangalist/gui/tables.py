"""Table columns the owner can resize: every column is dragged wider or narrower, the last one fills the rest.

A ``Stretch`` column cannot be dragged at all, and it squeezes the other columns until their headers are cut off,
so no table uses one; a wide column gets a generous starting width instead.
"""

from __future__ import annotations

from typing import Mapping, Optional

from PySide6.QtWidgets import QHeaderView, QTableView, QTableWidgetItem


def resizable_columns(table: QTableView, widths: Optional[Mapping[int, int]] = None) -> None:
    """Every column interactive, the last one stretching; *widths*: starting widths by column."""
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(True)
    for col, width in (widths or {}).items():
        table.setColumnWidth(col, width)


def cell(text: str) -> QTableWidgetItem:
    """A read-only table cell whose tooltip shows the whole text (also when its column is narrow)."""
    item = QTableWidgetItem(text)
    item.setToolTip(text)
    return item
