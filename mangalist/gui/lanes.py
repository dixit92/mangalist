"""Where the shell finds the other lanes' parts (UI cycle, ``gui/shell.py``): lane B's Download tab and Settings dialog,
lane C's duplicates view and finder. Each loader returns None while that lane is not merged - only when the module
itself is missing; a module that exists but fails to import is a bug and raises.

Until lane B merges, :class:`PlaceholderDownloadTab` stands in for the Download tab with the same interface (the
"To get" list, ``focus``, ``count_changed``, ``show_in_list``), and the existing nyaa dialog behind "Find volumes on
nyaa...". The integrator removes the placeholders once every lane is in."""

from __future__ import annotations

import importlib
import logging
from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, WantedSeries

_log = logging.getLogger(__name__)

DOWNLOAD_TAB = "mangalist.gui.download_tab"
SETTINGS_DIALOG = "mangalist.gui.settings_dialog"
DUPLICATES_VIEW = "mangalist.gui.duplicates_view"
DUPLICATES = "mangalist.duplicates"


def _load(module: str, name: str):
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name != module:
            raise                       # the module exists but one of its own imports is broken
        return None
    return getattr(mod, name, None)


def download_tab_class():
    """Lane B's ``DownloadTab`` class, or None."""
    return _load(DOWNLOAD_TAB, "DownloadTab")


def open_settings_function() -> Optional[Callable]:
    """Lane B's ``open_settings(parent, db, backend, section=None) -> SettingsResult``, or None."""
    return _load(SETTINGS_DIALOG, "open_settings")


def duplicates_view_class():
    """Lane C's ``DuplicatesView`` class, or None."""
    return _load(DUPLICATES_VIEW, "DuplicatesView")


def find_duplicate_files_function() -> Optional[Callable]:
    """Lane C's ``find_duplicate_files(db, root_ids=None)``, or None."""
    return _load(DUPLICATES, "find_duplicate_files")


GROUP_LABELS = {GROUP_VOLUMES: "Missing volumes", GROUP_CHAPTERS: "Missing chapters", GROUP_UPGRADES: "Upgrades"}
ROLE_FOLDER = Qt.ItemDataRole.UserRole


class PlaceholderDownloadTab(QWidget):
    """The Download tab's stand-in until lane B merges: the "To get" list in its three groups. *find_volumes(folder)*
    opens the existing nyaa dialog for a findable series."""

    count_changed = Signal(int)
    show_in_list = Signal(str)

    def __init__(self, backend=None, parent=None, find_volumes: Optional[Callable[[str], object]] = None):
        super().__init__(parent)
        self._backend = backend
        self._find_volumes = find_volumes
        self._series: List[WantedSeries] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        head = QLabel("TO GET")
        head.setProperty("role", "section")
        layout.addWidget(head)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Series", "Gaps", "Search"])
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(1, 220)
        self.tree.currentItemChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(lambda *_: self._show_selected())
        layout.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        self.btn_find = QPushButton("Find volumes on nyaa…")
        self.btn_find.setProperty("variant", "primary")
        self.btn_find.clicked.connect(self._find_selected)
        self.btn_show = QPushButton("Show in List")
        self.btn_show.clicked.connect(self._show_selected)
        buttons.addWidget(self.btn_find)
        buttons.addWidget(self.btn_show)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._update_buttons()

    # --- lane B's interface ----------------------------------------------------------------------------

    def set_wanted(self, series: Sequence[WantedSeries]) -> None:
        current = self._selected()
        self._series = list(series)
        self.tree.clear()
        groups: Dict[str, QTreeWidgetItem] = {}
        for group in (GROUP_VOLUMES, GROUP_CHAPTERS, GROUP_UPGRADES):
            members = sorted((s for s in self._series if s.group == group), key=lambda s: s.title.lower())
            top = QTreeWidgetItem([f"{GROUP_LABELS[group]}  ({len(members)})", "", ""])
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            self.tree.addTopLevelItem(top)
            groups[group] = top
            for s in members:
                child = QTreeWidgetItem([s.title, s.gaps, "Ready" if s.findable else s.reason])
                child.setData(0, ROLE_FOLDER, s.folder)
                child.setData(1, ROLE_FOLDER, s.group)
                child.setToolTip(2, s.reason)
                top.addChild(child)
            top.setExpanded(True)
        if current is not None:
            self.focus(current.folder, current.group)
        self.count_changed.emit(len(self._series))
        self._update_buttons()

    def focus(self, folder: str, group: Optional[str] = None) -> None:
        for g in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(g)
            for i in range(top.childCount()):
                child = top.child(i)
                if child.data(0, ROLE_FOLDER) == folder and (group is None or child.data(1, ROLE_FOLDER) == group):
                    self.tree.setCurrentItem(child)
                    self.tree.scrollToItem(child)
                    return

    def stop(self) -> None:
        pass

    # ------------------------------------------------------------------------------------------------------

    def series(self) -> List[WantedSeries]:
        return list(self._series)

    def _selected(self) -> Optional[WantedSeries]:
        item = self.tree.currentItem()
        if item is None or item.data(0, ROLE_FOLDER) is None:
            return None
        folder, group = item.data(0, ROLE_FOLDER), item.data(1, ROLE_FOLDER)
        return next((s for s in self._series if s.folder == folder and s.group == group), None)

    def _update_buttons(self, *_args) -> None:
        sel = self._selected()
        findable = sel is not None and sel.findable and sel.group == GROUP_VOLUMES and self._find_volumes is not None
        self.btn_find.setEnabled(findable)
        self.btn_find.setToolTip("Search nyaa for this series' missing volumes" if findable
                                 else (sel.reason if sel is not None and sel.reason else "Select a series that can be "
                                                                                          "searched"))
        self.btn_show.setEnabled(sel is not None)

    def _find_selected(self) -> None:
        sel = self._selected()
        if sel is not None and sel.findable and self._find_volumes is not None:
            self._find_volumes(sel.folder)

    def _show_selected(self) -> None:
        sel = self._selected()
        if sel is not None:
            self.show_in_list.emit(sel.folder)
