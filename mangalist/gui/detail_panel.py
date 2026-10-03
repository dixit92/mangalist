"""Right-hand detail panel showing per-entry breakdown."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..classifier import _human  # internal helper, fine to reuse
from ..models import MangaEntry

_KIND_COLORS = {
    "volume": QColor("#1565c0"),
    "chapter": QColor("#2e7d32"),
    "ambiguous": QColor("#9e9e9e"),
}

MAX_SAMPLE_FILES = 30


class DetailPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        self._title_label = QLabel("Select a row to see details")
        self._title_label.setWordWrap(True)
        f = self._title_label.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        self._title_label.setFont(f)
        root.addWidget(self._title_label)

        self._eng_label = QLabel("")
        self._eng_label.setWordWrap(True)
        self._eng_label.setStyleSheet("color: #666;")
        root.addWidget(self._eng_label)

        self._path_label = QLabel("")
        self._path_label.setWordWrap(True)
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._path_label.setStyleSheet("color: #555; font-family: Consolas, monospace;")
        root.addWidget(self._path_label)

        # Stats group
        self._stats_box = QGroupBox("Stats")
        self._stats_form = QFormLayout(self._stats_box)
        self._stats_form.setLabelAlignment(Qt.AlignRight)
        self._lbl_files = QLabel("-")
        self._lbl_subs = QLabel("-")
        self._lbl_vols = QLabel("-")
        self._lbl_chs = QLabel("-")
        self._lbl_amb = QLabel("-")
        self._lbl_median = QLabel("-")
        self._lbl_verdict = QLabel("-")
        self._stats_form.addRow("Files:", self._lbl_files)
        self._stats_form.addRow("Subfolders:", self._lbl_subs)
        self._stats_form.addRow("Volume files:", self._lbl_vols)
        self._stats_form.addRow("Chapter files:", self._lbl_chs)
        self._stats_form.addRow("Ambiguous files:", self._lbl_amb)
        self._stats_form.addRow("Median size:", self._lbl_median)
        self._stats_form.addRow("Verdict:", self._lbl_verdict)
        root.addWidget(self._stats_box)

        # Reasons
        self._reasons_box = QGroupBox("Heuristic hits")
        rb_layout = QVBoxLayout(self._reasons_box)
        self._reasons_list = QListWidget()
        self._reasons_list.setSelectionMode(QListWidget.NoSelection)
        rb_layout.addWidget(self._reasons_list)
        root.addWidget(self._reasons_box)

        # Sample files
        self._files_box = QGroupBox(f"Sample files (up to {MAX_SAMPLE_FILES})")
        fb_layout = QVBoxLayout(self._files_box)
        self._files_list = QListWidget()
        self._files_list.setSelectionMode(QListWidget.NoSelection)
        self._files_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        fb_layout.addWidget(self._files_list)
        root.addWidget(self._files_box, stretch=1)

        self.show_entry(None)

    # ------------------------------------------------------------------

    def show_entry(self, entry: Optional[MangaEntry]) -> None:
        if entry is None:
            self._title_label.setText("Select a row to see details")
            self._eng_label.setText("")
            self._path_label.setText("")
            for lbl in (
                self._lbl_files,
                self._lbl_subs,
                self._lbl_vols,
                self._lbl_chs,
                self._lbl_amb,
                self._lbl_median,
                self._lbl_verdict,
            ):
                lbl.setText("-")
            self._reasons_list.clear()
            self._files_list.clear()
            return

        self._title_label.setText(entry.title)
        self._eng_label.setText(entry.english_title or "")
        self._eng_label.setVisible(bool(entry.english_title))
        self._path_label.setText(str(entry.folder))

        self._lbl_files.setText(str(entry.n_files))
        self._lbl_subs.setText(str(entry.n_subfolders))
        self._lbl_vols.setText(str(entry.n_volume_files))
        self._lbl_chs.setText(str(entry.n_chapter_files))
        self._lbl_amb.setText(str(entry.n_ambiguous))
        self._lbl_median.setText(_human(entry.median_size) if entry.n_files else "-")
        self._lbl_verdict.setText(
            f"{entry.verdict.value}  "
            f"(V {entry.vol_pct:.0f}% / C {entry.ch_pct:.0f}% / B {entry.both_pct:.0f}%)"
        )

        self._reasons_list.clear()
        for r in entry.reasons:
            self._reasons_list.addItem(QListWidgetItem("• " + r))

        self._files_list.clear()
        for hit in entry.files[:MAX_SAMPLE_FILES]:
            indent = "  " * hit.depth
            label = f"{indent}{hit.path.name}    [{_human(hit.size)}]"
            item = QListWidgetItem(label)
            item.setForeground(_KIND_COLORS.get(hit.kind, QColor("#000")))
            item.setToolTip(str(hit.path))
            self._files_list.addItem(item)
        if len(entry.files) > MAX_SAMPLE_FILES:
            extra = len(entry.files) - MAX_SAMPLE_FILES
            self._files_list.addItem(QListWidgetItem(f"… and {extra} more"))
