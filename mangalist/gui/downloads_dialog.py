"""The Downloads list: every download MangaList has sent to qBittorrent, with where it stands (Sent, Downloaded,
Filed v03-v05, Failed: <reason>, Removed). Refreshable; the records are read off the UI thread.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..downloads.contracts import DownloadRecord
from .background import BackgroundCall, start_call
from .downloads_backend import DownloadsBackend
from .volumes_target import numbers_text, status_text, status_tooltip

COLUMNS = ("Series", "Release", "Volumes", "Status", "Target folder", "Updated")
NameFn = Callable[[int], str]


class DownloadsDialog(QDialog):
    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None,
                 series_name: Optional[NameFn] = None, autostart: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Downloads")
        self.resize(1150, 460)
        self._backend = backend
        self._series_name = series_name or (lambda series_id: f"Series #{series_id}")
        self._call: Optional[BackgroundCall] = None
        self.records: List[DownloadRecord] = []

        outer = QVBoxLayout(self)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for col, width in ((0, 170), (2, 100), (3, 210), (4, 230), (5, 120)):
            self.table.setColumnWidth(col, width)
        outer.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.status_label)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(140)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.progress)
        row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        row.addWidget(box)
        outer.addLayout(row)
        if autostart:
            self.refresh()

    def refresh(self) -> bool:
        if self._call is not None:
            return False
        self.btn_refresh.setEnabled(False)
        self.progress.setVisible(True)
        self.status_label.setStyleSheet("")
        self.status_label.setText("Loading...")
        backend = self._backend
        self._call = start_call(lambda: list(backend.records()), self._on_records, self._on_error)
        self._call.finished.connect(self._on_call_finished)
        return True

    def _on_records(self, records: Sequence[DownloadRecord]) -> None:
        self.records = sorted(records, key=lambda r: r.id, reverse=True)       # newest first
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            values = (self._series_name(record.series_id), record.title,
                      numbers_text(record.wanted_volumes, pad=True), status_text(record), record.target_dir,
                      (record.updated_at or "")[:16].replace("T", " "))
            tip = status_tooltip(record)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(tip)
                self.table.setItem(row, col, item)
        self.status_label.setText("" if self.records else "Nothing has been sent to qBittorrent yet.")

    def _on_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #b71c1c;")
        self.status_label.setText(f"Could not load the downloads: {message}")

    def _on_call_finished(self) -> None:
        self._call = None
        self.btn_refresh.setEnabled(True)
        self.progress.setVisible(False)

    def done(self, result: int) -> None:
        if self._call is not None:
            self._call.abandon()
        super().done(result)


def open_downloads_dialog(parent: Optional[QWidget], backend: DownloadsBackend,
                          series_name: Optional[NameFn] = None) -> None:
    DownloadsDialog(backend, parent, series_name).exec()
