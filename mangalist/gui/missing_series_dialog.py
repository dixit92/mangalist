"""Missing series: series rows whose folder vanished and that no rule could carry to a live folder (an empty
folder renamed, a split folder, fewer than 80% of the archives recognised, a target with its own link, ...).

The owner decides here (MangaPixer's "Missing folders"): **Re-attach to...** a live series folder without data
of its own (its link, kind answer, Behind override and examined mark follow - never over the target's own data),
or **Forget** the row (confirmed; its own link and examined mark go with it). Nothing on disk is changed.
Missing rows are otherwise kept, so a later scan or MangaPixer sync can still re-attach them.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..identity import carry
from .tables import resizable_columns

ChooseFn = Callable[[QWidget, carry.MissingSeries, List[carry.LiveSeries]], Optional[int]]
ConfirmFn = Callable[[QWidget, str], bool]

COLUMNS = ("Series", "Root", "Last seen", "Link", "Kind answer", "Examined")


def _yes(flag: bool) -> str:
    return "yes" if flag else ""


class TargetPicker(QDialog):
    """Pick a live series folder (only those without a link or kind answer of their own are offered)."""

    def __init__(self, missing: carry.MissingSeries, targets: List[carry.LiveSeries], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Re-attach to...")
        self.resize(560, 460)
        self._targets = targets
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Re-attach <b>{missing.name}</b> ({missing.root_name}) to the live folder:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter")
        self.filter_edit.textChanged.connect(self._fill)
        lay.addWidget(self.filter_edit)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list, 1)
        note = QLabel("Folders with a MangaUpdates link or a kind answer of their own are not listed.")
        note.setWordWrap(True)
        lay.addWidget(note)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._fill()

    def _fill(self) -> None:
        text = self.filter_edit.text().strip().casefold()
        self.list.clear()
        for t in self._targets:
            label = f"{t.rel_path}    -    {t.root_name}"
            if text and text not in label.casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, t.id)
            item.setToolTip(t.folder)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def selected_id(self) -> Optional[int]:
        item = self.list.currentItem()
        return None if item is None else int(item.data(Qt.UserRole))


def _default_choose(parent: QWidget, missing: carry.MissingSeries, targets: List[carry.LiveSeries]) -> Optional[int]:
    dlg = TargetPicker(missing, targets, parent)
    return dlg.selected_id() if dlg.exec() == QDialog.Accepted else None


def _default_confirm(parent: QWidget, text: str) -> bool:
    return QMessageBox.question(parent, "Forget missing series", text,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


class MissingSeriesDialog(QDialog):
    def __init__(self, db, parent: Optional[QWidget] = None, choose: Optional[ChooseFn] = None,
                 confirm: Optional[ConfirmFn] = None, button_style: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Missing series")
        self.resize(920, 480)
        self._db = db
        self._choose = choose or _default_choose
        self._confirm = confirm or _default_confirm
        self._rows: List[carry.MissingSeries] = []
        self.renamed: List[Tuple[str, str]] = []     # (old folder, new folder) of every re-attach
        self.forgotten: List[str] = []               # folders of forgotten rows
        self.changed = False

        lay = QVBoxLayout(self)
        intro = QLabel("Series whose folder vanished and that could not be recognised in another folder "
                       "(for example an empty folder renamed, or fewer than 80% of its archives found together). "
                       "Re-attach one to its new folder, or forget it.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        resizable_columns(self.table, {0: 320})
        self.table.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.table, 1)
        self.status_label = QLabel("")
        lay.addWidget(self.status_label)

        row = QHBoxLayout()
        self.btn_reattach = QPushButton("Re-attach to")
        self.btn_reattach.clicked.connect(self.reattach_selected)
        self.btn_forget = QPushButton("Forget")
        self.btn_forget.clicked.connect(self.forget_selected)
        for b in (self.btn_reattach, self.btn_forget):
            if button_style:
                b.setStyleSheet(button_style)
            row.addWidget(b)
        row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        row.addWidget(box)
        lay.addLayout(row)
        self.refresh()

    # --- data ------------------------------------------------------------------------------------------

    def refresh(self) -> None:
        self._rows = carry.missing_series(self._db)
        self.table.setRowCount(len(self._rows))
        for i, m in enumerate(self._rows):
            values = (m.name, m.root_name, (m.last_seen_at or "")[:16].replace("T", " "),
                      f"MU {m.mu_id}" if m.has_link and m.mu_id else _yes(m.has_link),
                      m.kind_hint or "", _yes(m.examined))
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c == 0:
                    item.setToolTip(m.folder)
                    item.setData(Qt.UserRole, m.id)
                self.table.setItem(i, c, item)
        if self._rows and not self.table.selectedItems():
            self.table.selectRow(0)
        self._update_buttons()

    def selected(self) -> Optional[carry.MissingSeries]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        r = rows[0].row()
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def _update_buttons(self) -> None:
        has = self.selected() is not None
        self.btn_reattach.setEnabled(has)
        self.btn_forget.setEnabled(has)
        if not self._rows:
            self.status_label.setText("No missing series.")

    # --- actions ---------------------------------------------------------------------------------------

    def reattach_selected(self) -> bool:
        m = self.selected()
        if m is None:
            return False
        targets = carry.live_series(self._db, without_own_data=True)
        if not targets:
            self.status_label.setText("No live series folder without data of its own to re-attach to.")
            return False
        target = self._choose(self, m, targets)
        if target is None:
            return False
        try:
            res = carry.reattach(self._db, m.id, target)
        except carry.ReattachError as exc:
            self.status_label.setText(str(exc))
            return False
        self.changed = True
        if res.carried:
            self.renamed.append((res.old_folder, res.new_folder))
        moved = ", ".join(res.moved) or "nothing"
        kept = f"; kept on the missing row: {', '.join(res.kept)}" if res.kept else ""
        self.refresh()
        self.status_label.setText(f"{m.name}: re-attached ({moved}{kept}).")
        return True

    def forget_selected(self) -> bool:
        m = self.selected()
        if m is None:
            return False
        what = []
        if m.has_link:
            what.append("its MangaUpdates link")
        if m.kind_hint:
            what.append("its kind answer")
        if m.examined:
            what.append("its examined mark")
        text = (f"Forget the missing series \"{m.name}\" ({m.root_name})?" +
                (f"\n\nThis also forgets {', '.join(what)}." if what else "") +
                "\n\nNothing on disk is changed.")
        if not self._confirm(self, text):
            return False
        if carry.forget(self._db, m.id):
            self.changed = True
            self.forgotten.append(m.folder)
        self.refresh()
        self.status_label.setText(f"{m.name}: forgotten.")
        return True


def open_missing_series_dialog(parent, db, button_style: str = "") -> MissingSeriesDialog:
    dlg = MissingSeriesDialog(db, parent, button_style=button_style)
    dlg.exec()
    return dlg


__all__ = ["MissingSeriesDialog", "TargetPicker", "open_missing_series_dialog"]
