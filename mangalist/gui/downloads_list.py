"""The "In progress" list: every download MangaList has sent to qBittorrent and where it stands (Sent, Downloaded,
Filed v03-v05 - seeding, Failed: <reason>, ...), with Check qBittorrent now and the time of the next automatic check.

The records are read off the UI thread (``refresh``); the Download tab shows this list at the bottom and learns the
records from :attr:`records_loaded`, the downloads dialog shows it alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QMenu, QMessageBox, QProgressBar, QVBoxLayout, QWidget

from ..downloads.contracts import DownloadRecord, DownloadStatus
from .background import BackgroundCall, start_call
from .download_rules import badge_kind, next_check_text, when_text
from .download_style import set_tone
from .download_widgets import button, flat_table, hbox, label, pill
from .downloads_backend import DownloadsBackend
from .tables import cell, resizable_columns
from .volumes_target import status_text, status_tooltip

COLUMNS = ("Series", "Release", "Status", "Updated")
COL_SERIES, COL_RELEASE, COL_STATUS, COL_UPDATED = range(len(COLUMNS))
MAX_ROWS = 200          # a pill widget per row: older records stay in the database, out of the list


@dataclass
class DownloadsSnapshot:
    records: List[DownloadRecord] = field(default_factory=list)
    titles: Dict[int, str] = field(default_factory=dict)       # series id -> the name the backend gave
    next_check: Optional[str] = None                           # ISO 8601 UTC


def load_snapshot(backend: DownloadsBackend) -> DownloadsSnapshot:
    """Everything the list needs, in one background call (the optional backend extras are used when present)."""
    records = list(backend.records())
    titles_fn = getattr(backend, "series_titles", None)
    titles = dict(titles_fn({r.series_id for r in records})) if titles_fn and records else {}
    next_fn = getattr(backend, "next_check", None)
    return DownloadsSnapshot(records, titles, next_fn() if next_fn else None)


class DownloadsList(QWidget):
    records_loaded = Signal(object)         # the records (a list of DownloadRecord), newest first
    check_finished = Signal(bool)           # a "Check qBittorrent now" ended (True: it worked)

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None, autostart: bool = True,
                 heading: str = "In progress", series_name: Optional[Callable[[int], str]] = None,
                 confirm_remove: Optional[Callable[[DownloadRecord], bool]] = None):
        super().__init__(parent)
        self._confirm_remove = confirm_remove or self._ask_remove
        self.setObjectName("downloadsList")
        self._backend = backend
        self._series_name = series_name
        self._call: Optional[BackgroundCall] = None
        self._note: Optional[str] = None            # the last "Check qBittorrent now" summary, kept across the reload
        self._check_failed = False
        self._again = False                         # a refresh was asked for while one ran
        self._known_titles: Mapping[int, str] = {}  # names the host knows (the wanted series)
        self._titles: Dict[int, str] = {}
        self._next_check: Optional[str] = None
        self.records: List[DownloadRecord] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.heading_label = label(heading, "h3")
        self.next_label = label(next_check_text(None), "muted")
        # Owner, 2026-10-09: "These labels need to be more clear" - one re-reads MangaList's own list, the other asks
        # qBittorrent; neither talks to MangaPixer.
        self.btn_refresh = button("Reload list", link=True)
        self.btn_refresh.setToolTip("Show the latest saved state of these downloads (asks neither qBittorrent nor "
                                    "MangaPixer)")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_check = button("Check qBittorrent now", tip="Ask qBittorrent now: file finished downloads and remove "
                                                              "completed torrents - the same check that runs every hour "
                                                              "on its own")
        self.btn_check.clicked.connect(self.check_now)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(90)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        outer.addLayout(hbox(self.heading_label, self.next_label, self.progress, None, self.btn_refresh,
                             self.btn_check, spacing=12))

        self.table = flat_table("progressTable", COLUMNS, select_rows=False)
        resizable_columns(self.table, {COL_SERIES: 200, COL_RELEASE: 360, COL_STATUS: 240, COL_UPDATED: 110})
        self.table.horizontalHeaderItem(COL_UPDATED).setTextAlignment(Qt.AlignmentFlag.AlignRight
                                                                     | Qt.AlignmentFlag.AlignVCenter)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        outer.addWidget(self.table, 1)

        self.status_label = label("", wrap=True, selectable=True)
        outer.addWidget(self.status_label)
        if autostart:
            self.refresh()

    # --- loading ---------------------------------------------------------------------------------------

    def set_known_titles(self, titles: Mapping[int, str]) -> None:
        """Names for series ids (the host's wanted series); the backend's own names win."""
        self._known_titles = dict(titles)
        if self.records:
            self._show(self.records)

    def refresh(self, *_args) -> bool:
        self._check_failed = False
        return self._reload()

    def _reload(self) -> bool:
        if self._call is not None:
            self._again = True
            return False
        self._busy(True)
        backend = self._backend
        self._call = start_call(lambda: load_snapshot(backend), self._on_snapshot, self._on_error,
                                self._on_call_finished)
        return True

    def _on_snapshot(self, snap: DownloadsSnapshot) -> None:
        self.records = sorted(snap.records, key=lambda r: r.id, reverse=True)       # newest first
        self._titles = snap.titles
        self._next_check = snap.next_check
        self.next_label.setText(next_check_text(snap.next_check))
        self._show(self.records)
        if not self._check_failed:
            empty = "" if self.records else "Nothing has been sent to qBittorrent yet."
            set_tone(self.status_label, "")
            self.status_label.setText(" ".join(t for t in (self._note or "", empty) if t))
        self.records_loaded.emit(list(self.records))

    def name_of(self, record: DownloadRecord) -> str:
        return (self._titles.get(record.series_id) or self._known_titles.get(record.series_id)
                or (self._series_name(record.series_id) if self._series_name else f"Series #{record.series_id}"))

    def _show(self, records: Sequence[DownloadRecord]) -> None:
        shown = list(records)[:MAX_ROWS]
        self.table.setRowCount(len(shown))
        for row, record in enumerate(shown):
            tip = status_tooltip(record)
            self.table.setItem(row, COL_SERIES, cell(self.name_of(record)))
            self.table.setItem(row, COL_RELEASE, cell(record.title))
            self.table.setItem(row, COL_STATUS, cell(""))
            badge = pill(status_text(record), badge_kind(record))
            badge.setToolTip(tip)
            self.table.setCellWidget(row, COL_STATUS, hbox_widget(badge))
            updated = cell(when_text(record.updated_at))
            updated.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, COL_UPDATED, updated)
            for col in (COL_SERIES, COL_RELEASE, COL_UPDATED):
                self.table.item(row, col).setToolTip(tip)
            font = self.table.item(row, COL_SERIES).font()
            font.setWeight(font.Weight.Medium)
            self.table.item(row, COL_SERIES).setFont(font)

    def _on_error(self, message: str) -> None:
        set_tone(self.status_label, "bad")
        self.status_label.setText(f"Could not load the downloads: {message}")

    def _on_call_finished(self) -> None:
        self._call = None
        self._busy(False)
        if self._again:
            self._again = False
            self._reload()

    def _busy(self, busy: bool) -> None:
        self.btn_refresh.setEnabled(not busy)
        self.btn_check.setEnabled(not busy)
        self.progress.setVisible(busy)

    # --- Check qBittorrent now -------------------------------------------------------------------------------------

    def check_now(self) -> bool:
        """Run the downloads check once (off the UI thread), then reload the list."""
        if self._call is not None:
            return False
        self._busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText("Checking qBittorrent...")
        self._check_failed = False
        backend = self._backend
        self._call = start_call(backend.check_now, self._on_checked, self._on_check_error, self._after_check)
        return True

    def _on_checked(self, summary: str) -> None:
        self._note = f"Checked now: {summary}."

    def _on_check_error(self, message: str) -> None:
        self._note = None
        set_tone(self.status_label, "bad")
        self.status_label.setText(f"Could not check the downloads: {message}")
        self._check_failed = True

    def _after_check(self) -> None:
        failed = self._check_failed
        self._on_call_finished()
        self._reload()                       # also after a failure: the list stays right, the message stays
        self.check_finished.emit(not failed)

    # --- Remove now (one filed download, the owner's choice) --------------------------------------------

    def _exec_menu(self, menu: QMenu, pos: QPoint):
        return menu.exec(pos)

    def _on_context_menu(self, pos: QPoint) -> None:
        row = self.table.rowAt(pos.y())
        shown = list(self.records)[:MAX_ROWS]
        if row < 0 or row >= len(shown) or not hasattr(self._backend, "remove_now"):
            return
        record = shown[row]
        menu = QMenu(self.table)
        act = menu.addAction("Remove now…")
        filed = record.status == DownloadStatus.FILED
        act.setEnabled(filed and self._call is None)
        act.setToolTip("Remove the torrent and its downloaded copy from qBittorrent now - the volumes stay in the "
                       "library" if filed else "Only a filed download can be removed")
        menu.setToolTipsVisible(True)
        if self._exec_menu(menu, self.table.viewport().mapToGlobal(pos)) is act and filed:
            self.remove_now(record)

    def _ask_remove(self, record: DownloadRecord) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, "Remove now?",
                          f"Remove \"{record.title}\" from qBittorrent, with its downloaded copy?\n\n"
                          "The volumes MangaList filed stay in the library. The torrent stops seeding.", parent=self)
        yes = box.addButton("Remove", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def remove_now(self, record: DownloadRecord) -> bool:
        """After the owner's yes: qBittorrent removes this filed download's torrent and downloaded copy (off the UI
        thread); the same library check as Remove Completed runs first."""
        if self._call is not None or record.status != DownloadStatus.FILED or not self._confirm_remove(record):
            return False
        self._busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText("Removing from qBittorrent...")
        self._check_failed = False
        backend = self._backend
        self._call = start_call(lambda: backend.remove_now(record.id), self._on_removed, self._on_remove_error,
                                self._after_check)
        return True

    def _on_removed(self, record: DownloadRecord) -> None:
        self._note = f"Removed from qBittorrent: {record.title}. The volumes stay in the library."

    def _on_remove_error(self, message: str) -> None:
        self._note = None
        set_tone(self.status_label, "bad")
        self.status_label.setText(f"Could not remove it: {message}")
        self._check_failed = True

    # --- closing ---------------------------------------------------------------------------------------

    def stop(self) -> None:
        if self._call is not None:
            self._call.abandon()


def hbox_widget(inner: QWidget) -> QWidget:
    """A table cell holding *inner* left-aligned (a pill must not stretch to the column)."""
    holder = QWidget()
    lay = hbox(inner, None, margins=(0, 0, 8, 0))
    lay.setAlignment(inner, Qt.AlignmentFlag.AlignVCenter)
    holder.setLayout(lay)
    return holder
