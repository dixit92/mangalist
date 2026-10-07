"""Find volumes on nyaa: the missing volumes of one series, the folder they will be filed into, and the releases
nyaa has, best first. The owner picks one and sends it to qBittorrent.

Everything slow (the placement lookup, the nyaa search, the send) runs off the UI thread through
:mod:`.background`. The backend's rank order is kept (sorting is off); each row's ``reasons`` are its tooltip.
Light novels (``not_comic``) are hidden until "Show light novels" is ticked. When the series' folder layout is
ambiguous the owner picks one of the offered folders first: Send stays disabled until a folder is chosen.

Nothing is sent without a confirmation that names the series, the volumes and the target folder.
"""

from __future__ import annotations

import html
import webbrowser
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..classifier import _human
from ..downloads.contracts import DownloadRecord, NyaaCandidate, Placement
from .background import BackgroundCall, start_call
from .downloads_backend import DownloadsBackend
from .volumes_target import VolumeTarget, numbers_text, volume_label

ConfirmFn = Callable[[QWidget, str], bool]

COLUMNS = ("Title", "Volumes", "Covers missing", "Already held", "Digital", "Group", "Size", "Seeders", "Trusted",
           "Published")
COL_TITLE, COL_VOLUMES, COL_COVERS, COL_HELD, COL_DIGITAL, COL_GROUP, COL_SIZE, COL_SEEDERS, COL_TRUSTED, \
    COL_PUBLISHED = range(len(COLUMNS))

NO_FOLDER_CHOSEN = "Choose a folder..."


def _default_confirm(parent: QWidget, text: str) -> bool:
    return QMessageBox.question(parent, "Send to qBittorrent", text,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def _yes(flag: bool) -> str:
    return "yes" if flag else ""


def volumes_text(candidate: NyaaCandidate) -> str:
    """The volume range the release's title states (``v03-v05``, ``v03``), or ``?`` when it does not say."""
    if candidate.vol_from is None:
        return "?"
    if candidate.vol_to is None or candidate.vol_to == candidate.vol_from:
        return volume_label(candidate.vol_from)
    return f"{volume_label(candidate.vol_from)}-{volume_label(candidate.vol_to)}"


def wanted_volumes_for(candidate: NyaaCandidate, missing: Sequence[str]) -> Sequence[str]:
    """The missing volumes this release is picked for: the ones it holds; a release whose title names no volumes
    (a numberless pack) is picked for all of them - the filing step links whichever the files turn out to be."""
    if candidate.vol_from is None:
        return tuple(missing)
    return tuple(candidate.covers_missing)


class NyaaDialog(QDialog):
    def __init__(self, backend: DownloadsBackend, target: VolumeTarget, parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmFn] = None, open_url: Optional[Callable[[str], object]] = None,
                 autostart: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Find volumes on nyaa")
        self.resize(1100, 600)
        self._backend = backend
        self.target = target
        self._confirm = confirm or _default_confirm
        self._open_url = open_url or webbrowser.open
        self._candidates: List[NyaaCandidate] = []
        self._placement: Optional[Placement] = None
        self._placement_error: Optional[str] = None
        self._search_state = "idle"          # idle | searching | done | error
        self._calls: List[BackgroundCall] = []
        self._sending = False
        self._sent_hashes = set()
        self.sent_records: List[DownloadRecord] = []
        self._build_ui()
        if autostart:
            self.start()

    # --- UI ------------------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        self.title_label = QLabel(f"<b>{html.escape(self.target.title)}</b>")
        self.title_label.setTextFormat(Qt.RichText)
        outer.addWidget(self.title_label)

        form = QFormLayout()
        self.missing_label = QLabel(numbers_text(self.target.missing, pad=True) or "-")
        self.missing_label.setWordWrap(True)
        self.missing_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Missing volumes:", self.missing_label)

        folder_row = QVBoxLayout()
        self.folder_label = QLabel("Looking up where new volumes go...")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.folder_combo = QComboBox()
        self.folder_combo.setVisible(False)
        self.folder_combo.currentIndexChanged.connect(self._update_send)
        self.folder_reason = QLabel("")
        self.folder_reason.setWordWrap(True)
        self.folder_reason.setStyleSheet("color: #666;")
        folder_row.addWidget(self.folder_label)
        folder_row.addWidget(self.folder_combo)
        folder_row.addWidget(self.folder_reason)
        form.addRow("Target folder:", folder_row)
        outer.addLayout(form)

        bar = QHBoxLayout()
        self.search_label = QLabel("")
        self.search_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(140)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.btn_retry = QPushButton("Search again")
        self.btn_retry.clicked.connect(self.start_search)
        self.novels_check = QCheckBox("Show light novels")
        self.novels_check.setToolTip("Releases that are light novels / EPUBs rather than manga volumes")
        self.novels_check.toggled.connect(self._apply_filter)
        bar.addWidget(self.search_label, 1)
        bar.addWidget(self.progress)
        bar.addWidget(self.btn_retry)
        bar.addWidget(self.novels_check)
        outer.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)               # the backend's rank order is the point
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        for col, width in ((COL_VOLUMES, 70), (COL_COVERS, 110), (COL_HELD, 100), (COL_DIGITAL, 60), (COL_GROUP, 120),
                           (COL_SIZE, 80), (COL_SEEDERS, 70), (COL_TRUSTED, 60), (COL_PUBLISHED, 90)):
            self.table.setColumnWidth(col, width)
        self.table.itemSelectionChanged.connect(self._update_send)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected_page())
        outer.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.btn_page = QPushButton("Open nyaa page")
        self.btn_page.setToolTip("Open the selected release's page on nyaa in the browser")
        self.btn_page.clicked.connect(self.open_selected_page)
        self.btn_send = QPushButton("Send to qBittorrent...")
        self.btn_send.clicked.connect(self.send_selected)
        buttons.addWidget(self.btn_page)
        buttons.addWidget(self.btn_send)
        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        outer.addLayout(buttons)
        self._update_send()

    # --- loading -------------------------------------------------------------------------------------

    def start(self) -> None:
        """Look up the target folder and run the search (both off the UI thread)."""
        self._spawn(lambda: self._backend.placement(self.target.series_id), self._on_placement,
                    self._on_placement_error)
        self.start_search()

    def start_search(self) -> None:
        if self._search_state == "searching":
            return
        self._search_state = "searching"
        self.btn_retry.setEnabled(False)
        self.progress.setVisible(True)
        self.search_label.setStyleSheet("")
        self.search_label.setText("Searching nyaa...")
        target = self.target
        self._spawn(lambda: list(self._backend.search(target.titles, target.missing, target.held)),
                    self._on_results, self._on_search_error)

    def _spawn(self, fn, on_done, on_error) -> None:
        call = start_call(fn, on_done, on_error)
        self._calls.append(call)
        call.finished.connect(lambda c=call: self._calls.remove(c) if c in self._calls else None)

    def _on_placement(self, placement: Placement) -> None:
        self._placement = placement
        self._placement_error = None
        self.folder_reason.setText(placement.reason or "")
        if placement.ambiguous:
            self.folder_label.setText("More than one folder could hold new volumes. Choose one:")
            self.folder_combo.blockSignals(True)
            self.folder_combo.clear()
            self.folder_combo.addItem(NO_FOLDER_CHOSEN, None)
            for option in placement.options:
                self.folder_combo.addItem(option, option)
            self.folder_combo.blockSignals(False)
            self.folder_combo.setVisible(True)
        else:
            self.folder_combo.setVisible(False)
            self.folder_label.setText(placement.target_dir or "")
        self._update_send()

    def _on_placement_error(self, message: str) -> None:
        self._placement = None
        self._placement_error = message
        self.folder_label.setText(f"Could not work out the target folder: {message}")
        self._update_send()

    def _on_results(self, candidates: Sequence[NyaaCandidate]) -> None:
        self._search_state = "done"
        self.progress.setVisible(False)
        self.btn_retry.setEnabled(True)
        self._candidates = list(candidates)
        self.table.setRowCount(len(self._candidates))
        for row, candidate in enumerate(self._candidates):
            values = (candidate.title, volumes_text(candidate), numbers_text(candidate.covers_missing, pad=True),
                      numbers_text(candidate.covers_held, pad=True), _yes(candidate.digital), candidate.group or "",
                      _human(candidate.size_bytes), str(candidate.seeders), _yes(candidate.trusted),
                      (candidate.published or "")[:10])
            tip = self._tooltip(candidate)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(tip)
                if col in (COL_SEEDERS, COL_SIZE):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)
        self._apply_filter()
        first = self._first_visible_row()
        if first is not None:
            self.table.selectRow(first)

    def _on_search_error(self, message: str) -> None:
        self._search_state = "error"
        self.progress.setVisible(False)
        self.btn_retry.setEnabled(True)
        self._candidates = []
        self.table.setRowCount(0)
        self.search_label.setStyleSheet("color: #b71c1c;")
        self.search_label.setText(f"The nyaa search failed: {message}")
        self._update_send()

    @staticmethod
    def _tooltip(candidate: NyaaCandidate) -> str:
        lines = list(candidate.reasons) or ["No ranking reasons given"]
        if candidate.not_comic:
            lines.append("Light novel / not a comic release")
        if candidate.remake:
            lines.append("Marked as a remake on nyaa")
        return "\n".join(lines)

    # --- filter and selection ------------------------------------------------------------------------

    def _apply_filter(self, *_args) -> None:
        show_novels = self.novels_check.isChecked()
        hidden = 0
        for row, candidate in enumerate(self._candidates):
            hide = candidate.not_comic and not show_novels
            self.table.setRowHidden(row, hide)
            hidden += hide
        shown = len(self._candidates) - hidden
        if self._search_state == "done":
            if not self._candidates:
                text = "No releases found on nyaa for this series (with seeders, English)."
            elif shown == 0:
                text = f"Only light novels found ({hidden}); tick \"Show light novels\" to see them."
            else:
                text = f"{shown} release(s), best first." + (f" {hidden} light novel(s) hidden." if hidden else "")
            self.search_label.setText(text)
        sel = self.selected_candidate()
        if sel is None or self.table.isRowHidden(self._candidates.index(sel)):
            self.table.clearSelection()
        self._update_send()

    def _first_visible_row(self) -> Optional[int]:
        for row in range(len(self._candidates)):
            if not self.table.isRowHidden(row):
                return row
        return None

    def selected_candidate(self) -> Optional[NyaaCandidate]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        row = rows[0].row()
        return self._candidates[row] if 0 <= row < len(self._candidates) else None

    def chosen_target_dir(self) -> Optional[str]:
        """The folder the volumes will be filed into: the placement's, or the owner's pick when ambiguous."""
        if self._placement is None:
            return None
        if self._placement.ambiguous:
            return self.folder_combo.currentData()
        return self._placement.target_dir

    def send_blocker(self) -> Optional[str]:
        """Why Send is disabled right now (None when it can go)."""
        if self._sending:
            return "Sending..."
        if self._placement is None:
            return ("The target folder could not be worked out." if self._placement_error
                    else "Still looking up the target folder.")
        if self._placement.ambiguous and self.chosen_target_dir() is None:
            return "Choose the target folder first."
        candidate = self.selected_candidate()
        if candidate is None:
            return "Select a release."
        if candidate.info_hash in self._sent_hashes:
            return "This release was already sent."
        if not wanted_volumes_for(candidate, self.target.missing):
            return "This release holds none of the missing volumes."
        return None

    def _update_send(self, *_args) -> None:
        blocker = self.send_blocker()
        self.btn_send.setEnabled(blocker is None)
        self.btn_send.setToolTip(blocker or "Add the selected release to qBittorrent")
        self.btn_page.setEnabled(self.selected_candidate() is not None)

    # --- actions -------------------------------------------------------------------------------------

    def open_selected_page(self) -> bool:
        candidate = self.selected_candidate()
        if candidate is None or not candidate.view_url.lower().startswith(("http://", "https://")):
            return False
        self._open_url(candidate.view_url)
        return True

    def confirmation_text(self, candidate: NyaaCandidate, wanted: Sequence[str], target_dir: str) -> str:
        if candidate.vol_from is None:
            vols = (f"{numbers_text(wanted, pad=True)} (the release's title does not say which volumes it holds; "
                    "MangaList files whichever of these it contains)")
        else:
            vols = numbers_text(wanted, pad=True)
        return (f"Send this release to qBittorrent?\n\n{candidate.title}\n\n"
                f"Series: {self.target.title}\nVolumes: {vols}\nTarget folder: {target_dir}\n\n"
                "qBittorrent downloads it; MangaList files these volumes into the target folder when it has finished.")

    def send_selected(self) -> bool:
        if self.send_blocker() is not None:
            return False
        candidate = self.selected_candidate()
        target_dir = self.chosen_target_dir()
        if candidate is None or target_dir is None:
            return False
        wanted = tuple(wanted_volumes_for(candidate, self.target.missing))
        if not self._confirm(self, self.confirmation_text(candidate, wanted, target_dir)):
            return False
        self._sending = True
        self._update_send()
        self.status_label.setStyleSheet("")
        self.status_label.setText("Sending to qBittorrent...")
        series_id = self.target.series_id
        self._spawn(lambda: self._backend.send(series_id, candidate, wanted, target_dir),
                    lambda record, c=candidate: self._on_sent(c, record), self._on_send_error)
        return True

    def _on_sent(self, candidate: NyaaCandidate, record: DownloadRecord) -> None:
        self._sending = False
        self._sent_hashes.add(candidate.info_hash)
        self.sent_records.append(record)
        self.status_label.setStyleSheet("color: #2e7d32;")
        self.status_label.setText(f"Sent to qBittorrent: {candidate.title}. MangaList files the volumes when the "
                                  "download has finished.")
        self._update_send()

    def _on_send_error(self, message: str) -> None:
        self._sending = False
        self.status_label.setStyleSheet("color: #b71c1c;")
        self.status_label.setText(f"Could not send it: {message}")
        self._update_send()

    # --- closing -------------------------------------------------------------------------------------

    def done(self, result: int) -> None:
        for call in list(self._calls):
            call.abandon()
        super().done(result)


def open_nyaa_dialog(parent: Optional[QWidget], backend: DownloadsBackend, target: VolumeTarget) -> NyaaDialog:
    """Open the find-volumes dialog (modal); returns it so the caller can read ``sent_records``."""
    dlg = NyaaDialog(backend, target, parent)
    dlg.exec()
    return dlg


__all__ = ["NyaaDialog", "open_nyaa_dialog", "volumes_text", "wanted_volumes_for"]
