"""Quick actions command palette (``Ctrl+K``) for the currently selected series in the List tab.

A small modal search dialog: type to filter actions by name or category, navigate with Up/Down, press Enter to run the
highlighted action (or Esc to cancel). Pure Qt widget over ``(label, category, callback)`` tuples supplied by the main
window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from . import theme


@dataclass(frozen=True)
class QuickAction:
    label: str
    category: str
    run: Callable[[], object]
    enabled: bool = True


class QuickActionsDialog(QDialog):
    """Filterable action launcher for one selected series."""

    def __init__(self, title: str, actions: Sequence[QuickAction], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("QuickActionsDialog")
        self.setWindowTitle("Quick actions")
        self.setMinimumWidth(420)
        self.resize(460, 360)
        self._all: List[QuickAction] = [a for a in actions if a.enabled]
        self._shown: List[QuickAction] = []
        self.chosen: Optional[QuickAction] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setProperty("role", "muted")
        lbl.setWordWrap(False)
        head.addWidget(lbl, 1)
        hint = QLabel("Enter to run · Esc to close")
        hint.setProperty("role", "muted")
        head.addWidget(hint)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type an action (e.g. rename, chapters, folder, MU)…")
        self.search.setAccessibleName("Filter actions")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refilter)
        self.search.returnPressed.connect(self._activate_current)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _item: self._activate_current())
        self.list.itemClicked.connect(lambda _item: self._activate_current())
        lay.addWidget(self.list, 1)

        self.setStyleSheet(theme.stylesheet())
        self._refilter("")
        self.search.setFocus()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down and self.list.count():
                self.list.setCurrentRow((self.list.currentRow() + 1) % self.list.count())
                return True
            if key == Qt.Key.Key_Up and self.list.count():
                self.list.setCurrentRow((self.list.currentRow() - 1) % self.list.count())
                return True
        return super().eventFilter(obj, event)

    def _refilter(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        tokens = [t for t in needle.split() if t]
        self._shown = [
            a for a in self._all
            if all(t in f"{a.label} {a.category}".casefold() for t in tokens)
        ]
        self.list.clear()
        for act in self._shown:
            item = QListWidgetItem(f"{act.label}   ·   {act.category}")
            self.list.addItem(item)
        if self._shown:
            self.list.setCurrentRow(0)

    def _activate_current(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._shown):
            self.chosen = self._shown[row]
            self.accept()


__all__ = ["QuickAction", "QuickActionsDialog"]
