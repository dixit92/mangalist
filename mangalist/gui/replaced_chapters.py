"""Replaced chapters in the Download tab (upgrades, owner decisions 2026-10-09): what the filed volumes replaced, and
the owner's answers.

- :class:`ReplacedBar` - one line above "In progress": "Replace 24 chapter files of 2 series with the volumes filed?"
  (delete mode, or a move that has to wait) or "40 chapter files replaced by volumes are in the holding folder";
  hidden when there is nothing. "Review" opens the dialog.
- :class:`ReplacedDialog` - every open batch (series, volumes, files, state) and the selected batch's files: **Restore**
  (held), **Move to holding folder** (pending, holding mode), **Delete...** (pending, delete mode: the confirmation
  first) and **Keep them** (pending: never asked again).
- :class:`ConfirmReplaceDialog` - lists every file about to be deleted; Delete is explicit, Cancel is the default
  (the duplicates view's confirmation).

The actions run off the UI thread (:mod:`.background`); the service is :class:`mangalist.upgrades.ReplacedChapters`.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..classifier import _human
from ..store.replacements import Batch, ReplacementConflict
from .background import BackgroundCall, start_call
from .download_rules import batch_status_text, replaced_bar_text
from .download_style import apply_style, set_tone
from .download_widgets import button, flat_table, hbox, label
from .downloads_backend import BackendError
from .volumes_target import numbers_text

ConfirmDeleteFn = Callable[[QWidget, Batch], bool]
COLUMNS = ("Series", "Volumes", "Files", "State")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _guarded(fn: Callable[[], object]) -> Callable[[], object]:
    """*fn* with the upgrades' own refusals shown in words (their messages carry paths, never a secret)."""
    def run():
        try:
            return fn()
        except (ReplacementConflict, ValueError) as exc:
            raise BackendError(str(exc)) from None
    return run


class ReplacedBar(QFrame):
    review_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("replacedBar")
        self.setStyleSheet("QFrame#replacedBar { background: #ffffff; border: none; border-top: 1px solid #dcdcd8; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(10)
        self.text_label = label("", wrap=True)
        self.review_button = button("Review")
        self.review_button.setAccessibleName("Review replaced chapters")
        self.review_button.clicked.connect(self.review_requested)
        lay.addWidget(self.text_label, 1)
        lay.addWidget(self.review_button)
        self.batches: List[Batch] = []
        self.setVisible(False)

    def set_batches(self, batches: Sequence[Batch]) -> None:
        self.batches = list(batches)
        text, tone = replaced_bar_text(self.batches)
        self.text_label.setText(text)
        set_tone(self.text_label, "warn" if tone == "warn" else "")
        self.review_button.setProperty("primary", tone == "warn")
        self.review_button.style().unpolish(self.review_button)
        self.review_button.style().polish(self.review_button)
        self.setVisible(bool(text))


class ConfirmReplaceDialog(QDialog):
    """Lists every chapter file about to be deleted; Delete is explicit, Cancel is the default."""

    def __init__(self, batch: Batch, series: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Delete these chapter files?")
        self.setObjectName("settingsDialog")            # the download widgets' look
        apply_style(self)
        self.setModal(True)
        self.resize(760, 440)
        n = len(batch.files)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(8)
        lay.addWidget(label(f"Delete {_plural(n, 'chapter file')} ({_human(batch.total_size)}) of {series} for good?",
                            "h2", wrap=True))
        lay.addWidget(label(f"Volume{'s' if len(batch.volumes) != 1 else ''} "
                            f"{numbers_text(batch.volumes, pad=True)}, filed into the series, hold{'' if len(batch.volumes) != 1 else 's'} "
                            "these chapters. Deleted files are gone, not moved to the holding folder.", "lead",
                            wrap=True))
        self.list = QListWidget()
        for f in batch.files:
            self.list.addItem(f"{f.path}    ch. {f.chapters} -> v{f.volume}    {_human(f.size)}")
        lay.addWidget(self.list, 1)
        buttons = QDialogButtonBox()
        self.delete_button = buttons.addButton(f"Delete {_plural(n, 'file')}", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.delete_button.setAutoDefault(False)
        self.delete_button.setProperty("role", "danger")
        self.cancel_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


def ask_delete(parent: QWidget, batch: Batch, series: str = "") -> bool:
    dialog = ConfirmReplaceDialog(batch, series or "the series", parent)
    try:
        return dialog.exec() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


class ReplacedDialog(QDialog):
    """The open batches and the owner's answers. ``changed`` carries the series folders whose files moved."""

    changed = Signal(list)

    def __init__(self, service, batches: Sequence[Batch], parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmDeleteFn] = None):
        super().__init__(parent)
        self.setWindowTitle("Replaced chapters")
        self.setObjectName("settingsDialog")
        apply_style(self)
        self.resize(860, 560)
        self._service = service
        self._confirm = confirm or (lambda parent, batch: ask_delete(parent, batch, self._title(batch)))
        self._batches: List[Batch] = list(batches)
        self._call: Optional[BackgroundCall] = None
        self._closed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Replaced chapters", "h2"))
        self.lead_label = label("", "lead", wrap=True)
        lay.addWidget(self.lead_label)
        self.table = flat_table("progressTable", COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._show_selected)
        lay.addWidget(self.table, 2)
        self.where_label = label("", "muted", wrap=True, selectable=True)
        lay.addWidget(self.where_label)
        self.files = QListWidget()
        lay.addWidget(self.files, 3)
        self.kept_label = label("", "muted", wrap=True)
        lay.addWidget(self.kept_label)
        self.message_label = label("", wrap=True)
        lay.addWidget(self.message_label)
        self.btn_restore = button("Restore", tip="Move the files back where they were")
        self.btn_hold = button("Move to holding folder", primary=True)
        self.btn_delete = button("Delete...", tip="Shows every file first; nothing is deleted before you confirm")
        self.btn_delete.setProperty("role", "danger")
        self.btn_keep = button("Keep them", tip="Leave the chapter files where they are and do not ask again")
        self.btn_close = button("Close")
        self.btn_restore.clicked.connect(self.restore_selected)
        self.btn_hold.clicked.connect(self.hold_selected)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_keep.clicked.connect(self.keep_selected)
        self.btn_close.clicked.connect(self.reject)
        lay.addLayout(hbox(self.btn_restore, self.btn_hold, self.btn_delete, self.btn_keep, None, self.btn_close))
        self._fill()

    # --- the list ------------------------------------------------------------------------------------------

    def _title(self, batch: Batch) -> str:
        try:
            return self._service.series_title(batch)
        except Exception:  # noqa: BLE001 - a label only
            return f"Series #{batch.series_id}"

    def _mode(self) -> str:
        try:
            return self._service.settings().mode
        except Exception:  # noqa: BLE001
            return "holding"

    def _fill(self, keep_id: Optional[int] = None) -> None:
        mode = self._mode()
        self.lead_label.setText(
            "Chapter files that volumes you filed now hold. " +
            ("Pending ones are deleted only after you confirm the list; held ones can be restored until they are "
             "emptied." if mode == "delete" else
             "They are moved to the holding folder (outside your libraries) and can be restored until it is emptied."))
        self.table.setRowCount(len(self._batches))
        for row, b in enumerate(self._batches):
            cells = (self._title(b), numbers_text(b.volumes, pad=True), str(len(b.files)), batch_status_text(b))
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, b.id)
                item.setToolTip(b.series_dir or "")
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        if self._batches:
            ids = [b.id for b in self._batches]
            self.table.selectRow(ids.index(keep_id) if keep_id in ids else 0)
        self._show_selected()

    def selected(self) -> Optional[Batch]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self._batches):
            return None
        return self._batches[rows[0].row()]

    def _show_selected(self) -> None:
        batch = self.selected()
        self.files.clear()
        busy = self._call is not None
        if batch is None:
            self.where_label.setText("Nothing to review." if not self._batches else "")
            self.kept_label.setText("")
            for b in (self.btn_restore, self.btn_hold, self.btn_delete, self.btn_keep):
                b.setEnabled(False)
            return
        for f in batch.files:
            self.files.addItem(f"{f.rel}    ch. {f.chapters} -> v{f.volume}    {_human(f.size)}")
        if batch.status == "held":
            self.where_label.setText(f"Held in {batch.holding_dir}" + (f" - {batch.error}" if batch.error else ""))
        else:
            self.where_label.setText(f"In {batch.series_dir}" + (f" - {batch.error}" if batch.error else ""))
        self.kept_label.setText(
            f"Kept in the library ({len(batch.kept)}): " + "; ".join(f"{rel} ({why})" for rel, why in batch.kept[:4])
            + (" ..." if len(batch.kept) > 4 else "") if batch.kept else "")
        mode = self._mode()
        pending = batch.status == "pending"
        self.btn_restore.setVisible(batch.status == "held")
        self.btn_hold.setVisible(pending and mode != "delete")
        self.btn_delete.setVisible(pending and mode == "delete")
        self.btn_keep.setVisible(pending)
        for b in (self.btn_restore, self.btn_hold, self.btn_delete, self.btn_keep):
            b.setEnabled(not busy)

    # --- the answers ------------------------------------------------------------------------------------------

    def restore_selected(self) -> bool:
        batch = self.selected()
        if batch is None or batch.status != "held" or self._call is not None:
            return False
        self._run(f"Restoring {_plural(len(batch.files), 'file')}...", batch,
                  lambda: self._service.restore(batch.id))
        return True

    def hold_selected(self) -> bool:
        batch = self.selected()
        if batch is None or batch.status != "pending" or self._call is not None:
            return False
        self._run("Moving to the holding folder...", batch, lambda: self._service.hold(batch.id))
        return True

    def keep_selected(self) -> bool:
        batch = self.selected()
        if batch is None or batch.status != "pending" or self._call is not None:
            return False
        self._run("Keeping them...", batch, lambda: self._service.keep(batch.id), moved=False)
        return True

    def delete_selected(self) -> bool:
        """Delete the selected pending batch's files - only after the confirmation listing every one of them."""
        batch = self.selected()
        if batch is None or batch.status != "pending" or self._call is not None:
            return False
        if not self._confirm(self, batch):
            self._say("Nothing deleted.", "")
            return False
        paths = [f.path for f in batch.files]
        self._run(f"Deleting {_plural(len(paths), 'file')}...", batch, lambda: self._service.delete(batch.id, paths))
        return True

    def _run(self, text: str, batch: Batch, fn: Callable[[], object], *, moved: bool = True) -> None:
        self._say(text, "")
        made: list = []
        self._call = start_call(_guarded(fn), lambda result: self._done(batch, result, moved), self._failed,
                                lambda: self._finished(made[0] if made else None))
        made.append(self._call)
        self._show_selected()

    def _done(self, batch: Batch, result, moved: bool) -> None:
        if isinstance(result, list):                        # a delete: one outcome per file
            gone = sum(1 for o in result if o.deleted)
            refused = [o for o in result if not o.deleted]
            text = f"Deleted {_plural(gone, 'file')}." + (f" {len(refused)} not deleted: {refused[0].reason}."
                                                          if refused else "")
            self._say(text, "warn" if refused else "ok")
        else:
            self._say(batch_status_text(result) + ".", "warn" if result.error else "ok")
        if moved and batch.series_dir:
            self.changed.emit([batch.series_dir])

    def _failed(self, message: str) -> None:
        self._say(f"Could not do it: {message}", "bad")

    def _finished(self, call) -> None:
        if call is self._call:
            self._call = None
        if self._closed:
            return
        self._reload(batch_id=self.selected().id if self.selected() else None)

    def _reload(self, batch_id: Optional[int] = None) -> None:
        try:
            self._batches = list(self._service.open_batches())
        except Exception:  # noqa: BLE001 - keep what is shown
            pass
        self._fill(keep_id=batch_id)

    def _say(self, text: str, tone: str) -> None:
        self.message_label.setText(text)
        set_tone(self.message_label, tone)

    def busy(self) -> bool:
        return self._call is not None

    def done(self, result: int) -> None:
        self._closed = True
        if self._call is not None:
            self._call.abandon()
            self._call = None
        super().done(result)
