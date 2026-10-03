"""Dialog for manually selecting the correct MangaUpdates series match."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..mu_client import REQUEST_DELAY, search_series


class MuPickerDialog(QDialog):
    """Shows candidate MangaUpdates matches and lets the user pick one.

    After ``exec()``, read ``.selected`` for the chosen record dict, or None.
    """

    def __init__(self, initial_query: str, candidates: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select MangaUpdates Match")
        self.setMinimumWidth(560)
        self.setMinimumHeight(400)
        self.selected: Optional[Dict[str, Any]] = None
        self._candidates = list(candidates)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Search MangaUpdates:"))
        search_row = QHBoxLayout()
        self._search_edit = QLineEdit(initial_query)
        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self._do_search)
        self._search_edit.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_edit, 1)
        search_row.addWidget(self._search_btn)
        layout.addLayout(search_row)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #666;")
        layout.addWidget(self._status)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.itemDoubleClicked.connect(self._accept_selection)
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate(self._candidates)

    # ------------------------------------------------------------------

    def _populate(self, records: List[Dict[str, Any]]) -> None:
        self._list.clear()
        # search_series returns {"record": ..., "hit_title": ...} wrappers;
        # unwrap to the inner record dict for display and selection.
        unwrapped = [
            r["record"] if "record" in r else r
            for r in records
        ]
        self._candidates = unwrapped
        for r in unwrapped:
            sid = r.get("series_id", "?")
            title = r.get("title", "?")
            year = r.get("year") or ""
            rtype = r.get("type") or ""
            label = f"{title}  [{rtype}  {year}]  — ID {sid}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, r)
            self._list.addItem(item)
        if unwrapped:
            self._list.setCurrentRow(0)
        self._status.setText(f"{len(unwrapped)} result(s)")

    def _do_search(self) -> None:
        query = self._search_edit.text().strip()
        if not query:
            return
        self._status.setText("Searching…")
        self._search_btn.setEnabled(False)
        self.repaint()
        try:
            results = search_series(query, page_size=15)
            self._populate(results)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Search failed", str(exc))
            self._status.setText("Search failed")
        finally:
            self._search_btn.setEnabled(True)

    def _accept_selection(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self.selected = item.data(Qt.UserRole)
        self.accept()
