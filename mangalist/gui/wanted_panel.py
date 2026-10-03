"""The Wanted panel: series that are wanted, missing units or have an upgrade, with their official
sources (open in the browser)."""

from __future__ import annotations

import webbrowser
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..states import MISSING_STATES, WANTED_STATES, SeriesState, State

GROUPS: Tuple[Tuple[str, frozenset], ...] = (
    ("Wanted", WANTED_STATES),
    ("Missing", MISSING_STATES),
    ("Upgrade available", frozenset({State.UPGRADE})),
)

ROLE_ROW = Qt.UserRole          # a series item: the source-model row
ROLE_URL = Qt.UserRole + 1      # a link item: its URL


class WantedPanel(QWidget):
    """A tree: group -> series (state, gaps) -> official links."""

    series_activated = Signal(int)   # source-model row

    def __init__(self, parent=None, open_url: Optional[Callable[[str], object]] = None):
        super().__init__(parent)
        self._open_url = open_url or webbrowser.open
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.summary = QLabel("")
        layout.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Series / source", "State", "Gaps / link"])
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 190)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.currentItemChanged.connect(self._update_buttons)
        self.tree.setMinimumHeight(140)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.btn_open = QPushButton("Open link")
        self.btn_open.setToolTip("Open the selected official source in the browser")
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_show = QPushButton("Show in table")
        self.btn_show.clicked.connect(self.show_selected)
        buttons.addWidget(self.btn_open)
        buttons.addWidget(self.btn_show)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._update_buttons()

    # ------------------------------------------------------------------

    def rebuild(self, model) -> None:
        """Refill from a MangaTableModel (``rowCount``, ``entry_at``, ``state_at``, ``links_at``)."""
        expanded = {self.tree.topLevelItem(i).text(0).split("  (")[0]
                    for i in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(i).isExpanded()}
        first_build = self.tree.topLevelItemCount() == 0
        self.tree.clear()
        buckets: List[List[Tuple[int, SeriesState]]] = [[] for _ in GROUPS]
        for row in range(model.rowCount()):
            st = model.state_at(row)
            if st is None:
                continue
            for i, (name, states) in enumerate(GROUPS):
                if st.state in states or (name == "Upgrade available" and st.complete_with_upgrade):
                    buckets[i].append((row, st))
                    break
        counts = []
        for (name, _), rows in zip(GROUPS, buckets):
            group = QTreeWidgetItem([f"{name}  ({len(rows)})", "", ""])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.tree.addTopLevelItem(group)
            counts.append(f"{name}: {len(rows)}")
            rows.sort(key=lambda rs: (rs[1].sort_key, _title(model, rs[0]).lower()))
            for row, st in rows:
                item = QTreeWidgetItem([_title(model, row), st.state.value, st.gaps_text])
                item.setData(0, ROLE_ROW, row)
                item.setToolTip(1, st.tooltip())
                item.setToolTip(2, st.gaps_tooltip())
                group.addChild(item)
                for link in model.links_at(row):
                    child = QTreeWidgetItem([link.label, link.kind, link.url or "(no page known)"])
                    child.setData(0, ROLE_URL, link.url)
                    child.setToolTip(2, link.url or "")
                    item.addChild(child)
            group.setExpanded(first_build or name in expanded)
        self.summary.setText("   ".join(counts))
        self._update_buttons()

    def series_titles(self, group: int) -> List[str]:
        top = self.tree.topLevelItem(group)
        return [top.child(i).text(0) for i in range(top.childCount())] if top else []

    # ------------------------------------------------------------------

    def _on_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(0, ROLE_URL):
            self._open_url(item.data(0, ROLE_URL))
        elif item.data(0, ROLE_ROW) is not None:
            self.series_activated.emit(int(item.data(0, ROLE_ROW)))

    def _selected(self) -> Optional[QTreeWidgetItem]:
        return self.tree.currentItem()

    def open_selected(self) -> None:
        item = self._selected()
        if item is None:
            return
        url = item.data(0, ROLE_URL)
        if not url and item.data(0, ROLE_ROW) is not None:
            # A series: its first official source with a page.
            for i in range(item.childCount()):
                url = item.child(i).data(0, ROLE_URL)
                if url:
                    break
        if url:
            self._open_url(url)

    def show_selected(self) -> None:
        item = self._selected()
        while item is not None and item.data(0, ROLE_ROW) is None:
            item = item.parent()
        if item is not None:
            self.series_activated.emit(int(item.data(0, ROLE_ROW)))

    def _update_buttons(self, *_args) -> None:
        item = self._selected()
        self.btn_open.setEnabled(item is not None and (bool(item.data(0, ROLE_URL)) or item.data(0, ROLE_ROW) is not None))
        self.btn_show.setEnabled(item is not None and (item.data(0, ROLE_ROW) is not None or item.parent() is not None))


def _title(model, row: int) -> str:
    e = model.entry_at(row)
    if e is None:
        return ""
    if getattr(e, "parent_folder", None) is not None:
        return f"{e.parent_folder.name} / {e.title}"
    return e.title
