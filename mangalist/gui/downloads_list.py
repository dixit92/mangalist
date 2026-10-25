"""The "In progress" list: every download MangaList has sent to qBittorrent or queued, and where it stands (Queued -
2nd in line, Downloading, Downloaded, Filed v03-v05 - seeding, Failed: <reason>, ...), with Check downloads now, the
time of the next automatic check and the download budget ("Using 31.2 GB of 50 GB; 2 downloads queued"). Chapter
downloads (Suwayomi) show here too, one row per Send ("Ch. 101-104 · <group> · MangaDex (EN)": Downloading, Filed ch
101-104 - done, ...); the check files them as well.

The records are read off the UI thread (``refresh``); the Download tab shows this list at the bottom and learns the
records from :attr:`records_loaded`, the downloads dialog shows it alone.

Row menu (right-click): a filed download offers "Remove now..."; a queued one (owner, 2026-10-09: "the user can override
the queue") "Send now (past the cap)...", "Move to the front of the queue" and "Remove from the queue...". Sending past
the cap and removing ask first, as Remove now does; moving to the front does not (it changes no download).

**Dismiss** (owner, 2026-10-15): a FAILED row - a torrent or a chapter download ("already in the folder", ...) - offers
"Dismiss": the record is marked cancelled and leaves In progress (it stays under Show finished, its failure readable in
the tooltip). Nothing else changes: no file is touched, a torrent still in qBittorrent is left there and stops counting
against the download budget. A merged chapter row dismisses every chapter record it shows. It does not ask first (it
changes no download); the status line says what was dismissed.

**Stuck chapter downloads** (owner, 2026-10-20: "I can't manually retry this"): a chapter download its program gave up
on (:mod:`mangalist.downloads.chapter_retry`; MangaList also retries it by itself every 6 h, up to 3 times) offers "Retry
in <program>" and "Cancel download..." in its row menu, and a line under the list names every such row with Retry and
Cancel... buttons - reachable by tap too (the iPad, touch UX backlog). Retry puts the chapters back in their program's
queue; Cancel (asked first) takes them out of it and marks the records cancelled. Since the transition fixes
(2026-10-24, audit B1 / A3): a row is stuck by the state the arrivals pass saw (never by its note's words), every chapter
download still on its way offers Cancel (the switch guard tells the owner to cancel them), and every text names the
row's own program (Suwayomi or the extension host) when it is shown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QMenu, QMessageBox, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from ..downloads.budget import BudgetState, gb_text
from ..downloads.chapter_retry import can_cancel, is_stuck
from ..downloads.contracts import DownloadRecord, DownloadStatus
from .background import BackgroundCall, start_call
from .download_rules import badge_kind, in_progress, merge_batches, next_check_text, when_text
from .download_style import set_tone
from .download_widgets import ElidedLabel, button, flat_table, hbox, label, pill
from .downloads_backend import DownloadsBackend
from .tables import cell, resizable_columns
from .volumes_target import chapter_words, status_parts, status_tooltip

_log = logging.getLogger(__name__)

COLUMNS = ("Series", "Release", "Status", "Updated")
COL_SERIES, COL_RELEASE, COL_STATUS, COL_UPDATED = range(len(COLUMNS))
MAX_ROWS = 200          # a pill widget per row: older records stay in the database, out of the list


@dataclass
class DownloadsSnapshot:
    records: List[DownloadRecord] = field(default_factory=list)
    titles: Dict[int, str] = field(default_factory=dict)       # series id -> the name the backend gave
    next_check: Optional[str] = None                           # ISO 8601 UTC
    budget: Optional[BudgetState] = None                       # None: the backend keeps no download budget


def load_snapshot(backend: DownloadsBackend) -> DownloadsSnapshot:
    """Everything the list needs, in one background call (the optional backend extras are used when present)."""
    records = list(backend.records())
    titles_fn = getattr(backend, "series_titles", None)
    ids = {r.series_id for r in records if r.series_id is not None}     # a forgotten series' records have none
    titles = dict(titles_fn(ids)) if titles_fn and ids else {}
    next_fn = getattr(backend, "next_check", None)
    budget_fn = getattr(backend, "budget_status", None)
    return DownloadsSnapshot(records, titles, next_fn() if next_fn else None, budget_fn() if budget_fn else None)


#: The queue actions of the row menu (the backend method each calls).
ACT_SEND_NOW = "Send now (past the cap)…"
ACT_TO_FRONT = "Move to the front of the queue"
ACT_DEQUEUE = "Remove from the queue…"
QUEUE_ACTIONS = {ACT_SEND_NOW: "send_queued_now", ACT_TO_FRONT: "move_to_front", ACT_DEQUEUE: "remove_from_queue"}
ACT_DISMISS = "Dismiss"
ACT_REMOVE_FAILED = "Remove from qBittorrent…"
REMOVE_FAILED_TIP = ("Delete this failed download's torrent from qBittorrent - with its downloaded files or keeping them "
                     "- and take it out of In progress. Nothing in the library is touched: a failed download filed "
                     "nothing.")
ACT_RETRY = "Retry in Suwayomi"
ACT_CANCEL = "Cancel download…"
#: Written for Suwayomi; shown worded for each row's program (:func:`~.volumes_target.chapter_words`).
RETRY_TIP = ("Put the chapters back in Suwayomi's download queue now (Suwayomi gave up after its own tries; MangaList "
             "also retries them by itself every 6 hours, up to 3 times)")
CANCEL_TIP = ("Take the chapters out of Suwayomi's download queue and out of In progress (marked cancelled, under Show "
              "finished). A later check may find them missing again.")
DISMISS_TIP = ("Take this failed download out of In progress (it is marked cancelled and stays under Show finished). "
               "No file is touched; a torrent still in qBittorrent is left there and no longer counts against the "
               "download budget.")


def members_of(row: DownloadRecord, records: Sequence[DownloadRecord]) -> List[int]:
    """The record ids a shown row stands for: a torrent row its own; a merged chapter row every chapter record of its
    Send in the same status and with the same error (:func:`~.download_rules.merge_batches`' grouping)."""
    if not row.is_chapters:
        return [row.id]
    key = (row.batch or f"#{row.id}", row.status, row.error or "")
    ids = [r.id for r in records if r.is_chapters and (r.batch or f"#{r.id}", r.status, r.error or "") == key]
    return ids or [row.id]


class DownloadsList(QWidget):
    records_loaded = Signal(object)         # the records (a list of DownloadRecord), newest first
    check_finished = Signal(bool)           # a "Check downloads now" ended (True: it worked)
    find_now_requested = Signal()           # "Find now" (automatic downloads): the host runs the find pass

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None, autostart: bool = True,
                 heading: str = "In progress", series_name: Optional[Callable[[int], str]] = None,
                 confirm_remove: Optional[Callable[[DownloadRecord], bool]] = None,
                 confirm_queue: Optional[Callable[[str, DownloadRecord], bool]] = None,
                 confirm_cancel: Optional[Callable[[Sequence[DownloadRecord]], bool]] = None,
                 confirm_remove_failed: Optional[Callable[[DownloadRecord], Optional[bool]]] = None):
        super().__init__(parent)
        self._confirm_cancel = confirm_cancel or self._ask_cancel
        self._confirm_remove_failed = confirm_remove_failed or self._ask_remove_failed
        self._confirm_remove = confirm_remove or self._ask_remove
        self._confirm_queue = confirm_queue or self._ask_queue
        self.setObjectName("downloadsList")
        self._backend = backend
        self._series_name = series_name
        self._call: Optional[BackgroundCall] = None
        self._note: Optional[str] = None            # the last "Check downloads now" summary, kept across the reload
        self._check_failed = False
        self._host_note, self._host_tone = "", ""  # the host's line (Find now's summary), kept across the reload
        self._again = False                         # a refresh was asked for while one ran
        self._known_titles: Mapping[int, str] = {}  # names the host knows (the wanted series)
        self._titles: Dict[int, str] = {}
        self._next_check: Optional[str] = None
        self.budget: Optional[BudgetState] = None
        self.records: List[DownloadRecord] = []
        self.show_finished = False                  # done / cancelled downloads are hidden unless asked

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.heading_label = label(heading, "h3")
        self.next_label = label(next_check_text(None), "muted")
        self.budget_label = label("", "muted")
        self.budget_label.setToolTip("The download budget (Settings > Download sources): what MangaList has "
                                     "downloading or seeding, against its cap. Sends past it wait in the queue.")
        self.budget_label.setVisible(False)
        # Owner, 2026-10-09: "These labels need to be more clear" - one re-reads MangaList's own list, the other asks
        # qBittorrent; neither talks to MangaPixer.
        self.btn_refresh = button("Reload list", link=True)
        self.btn_refresh.setToolTip("Show the latest saved state of these downloads (asks neither qBittorrent, the "
                                    "chapter downloads' program nor MangaPixer)")
        self.btn_refresh.clicked.connect(self.refresh)
        # owner, 2026-10-10: "Check downloads now is misleading since we have another provider" - it asks Suwayomi too
        self.btn_check = button("Check downloads now", tip="Ask qBittorrent and the chapter downloads' program (Suwayomi "
                                                            "or the built-in extensions) now: file finished downloads, "
                                                            "remove completed torrents and hand queued torrents over "
                                                            "while they fit the download budget - the same check that "
                                                            "runs every hour on its own")
        self.btn_check.clicked.connect(self.check_now)
        # Automatic downloads (2026-10-10): the find pass on request, next to the check - the host runs it and says
        # what it did (set_find_available / set_find_busy / set_note); hidden without a backend that can find.
        self.btn_find_now = button("Find now", tip="Look for every series with gaps now - the same find pass as the "
                                                   "schedule in Settings > Automation: series on Auto get what is "
                                                   "found sent, the rest waits under Found")
        self.btn_find_now.clicked.connect(self.find_now_requested)
        self.btn_find_now.setVisible(False)
        self.btn_finished = button("", link=True, tip="Downloads whose torrent left qBittorrent at its seed goal, "
                                                       "or that were cancelled - their volumes stay in the library")
        self.btn_finished.clicked.connect(self.toggle_finished)
        self.btn_finished.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(90)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        outer.addLayout(hbox(self.heading_label, self.next_label, self.budget_label, self.progress, None,
                             self.btn_finished, self.btn_refresh, self.btn_find_now, self.btn_check, spacing=12))

        self.table = flat_table("progressTable", COLUMNS, select_rows=False)
        # the status column starts wide enough for a reason beside its chip (owner, 2026-10-18); every column stays
        # the owner's to drag (no Stretch column - tests/gui/test_tables.py)
        resizable_columns(self.table, {COL_SERIES: 200, COL_RELEASE: 360, COL_STATUS: 420, COL_UPDATED: 110})
        self.table.horizontalHeaderItem(COL_UPDATED).setTextAlignment(Qt.AlignmentFlag.AlignRight
                                                                     | Qt.AlignmentFlag.AlignVCenter)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        outer.addWidget(self.table, 1)

        # Chapter downloads Suwayomi gave up on, with buttons (a tap works where the row menu needs a right-click)
        self.stuck_label = label("", wrap=True)
        set_tone(self.stuck_label, "warn")
        self.btn_retry = button("Retry", tip=RETRY_TIP)
        self.btn_retry.clicked.connect(lambda: self.retry_stuck())
        self.btn_cancel_stuck = button("Cancel…", tip=CANCEL_TIP)
        self.btn_cancel_stuck.clicked.connect(lambda: self.cancel_stuck())
        self.stuck_bar = QWidget()
        self.stuck_bar.setLayout(hbox(self.stuck_label, None, self.btn_retry, self.btn_cancel_stuck, spacing=10))
        self.stuck_bar.setVisible(False)
        self.stuck_rows: List[DownloadRecord] = []
        outer.addWidget(self.stuck_bar)

        self.status_label = label("", wrap=True, selectable=True)
        outer.addWidget(self.status_label)
        if autostart:
            self.refresh()

    # --- loading ---------------------------------------------------------------------------------------

    def set_known_titles(self, titles: Mapping[int, str]) -> None:
        """Names for series ids (the host's wanted series); the backend's own names win. Unchanged names rebuild
        nothing (a library switch sends the same names again; the rebuild cost 40-60 ms - UI performance stage 2)."""
        titles = dict(titles)
        if titles == self._known_titles:
            return
        self._known_titles = titles
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
        self.budget = snap.budget
        self.budget_label.setText(f"· {snap.budget.summary()}" if snap.budget is not None else "")
        self.budget_label.setVisible(snap.budget is not None)
        self._show(self.records)
        if not self._check_failed:
            empty = "" if self.records else "Nothing has been sent to qBittorrent yet."
            if self.records and not self.shown_records():
                empty = "Nothing is in progress."
            set_tone(self.status_label, self._host_tone if self._host_note and not self._note else "")
            self.status_label.setText(" ".join(t for t in (self._note or "", self._host_note, empty) if t))
        self.records_loaded.emit(list(self.records))

    def name_of(self, record: DownloadRecord) -> str:
        if record.series_id is None:
            return "(series no longer in the library)"
        return (self._titles.get(record.series_id) or self._known_titles.get(record.series_id)
                or (self._series_name(record.series_id) if self._series_name else f"Series #{record.series_id}"))

    def shown_records(self) -> List[DownloadRecord]:
        """The rows, top to bottom: everything not finished, or everything with Show finished on. The chapter downloads
        of one Send (Suwayomi) are one row (:func:`~.download_rules.merge_batches`)."""
        return in_progress(merge_batches(self.records), self.show_finished)[:MAX_ROWS]

    def toggle_finished(self) -> None:
        self.show_finished = not self.show_finished
        self._show(self.records)

    def _show_finished_button(self) -> None:
        merged = merge_batches(self.records)
        n = len(merged) - len(in_progress(merged))
        self.btn_finished.setVisible(n > 0)
        self.btn_finished.setText(f"Hide finished ({n})" if self.show_finished else f"Show finished ({n})")

    def _show(self, records: Sequence[DownloadRecord]) -> None:
        shown = in_progress(merge_batches(records), self.show_finished)[:MAX_ROWS]
        self._show_finished_button()
        self.table.setRowCount(len(shown))
        for row, record in enumerate(shown):
            tip = status_tooltip(record)
            self.table.setItem(row, COL_SERIES, cell(self.name_of(record)))
            self.table.setItem(row, COL_RELEASE, cell(record.title))
            self.table.setItem(row, COL_STATUS, cell(""))
            self.table.setCellWidget(row, COL_STATUS, status_cell(record, tip))
            updated = cell(when_text(record.updated_at))
            updated.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, COL_UPDATED, updated)
            for col in (COL_SERIES, COL_RELEASE, COL_UPDATED):
                self.table.item(row, col).setToolTip(tip)
            font = self.table.item(row, COL_SERIES).font()
            font.setWeight(font.Weight.Medium)
            self.table.item(row, COL_SERIES).setFont(font)
        self._show_stuck([r for r in shown if is_stuck(r)])

    def _show_stuck(self, rows: List[DownloadRecord]) -> None:
        self.stuck_rows = rows
        self.stuck_bar.setVisible(bool(rows))
        if not rows:
            return
        names = ", ".join(f"{self.name_of(r)} ({r.title})" for r in rows[:3]) + (", ..." if len(rows) > 3 else "")
        what = "a chapter download" if len(rows) == 1 else f"{len(rows)} chapter downloads"
        programs = {r.backend for r in rows}
        who = chapter_words(rows[0], "Suwayomi") if len(programs) == 1 else "Their program"
        self.stuck_label.setText(f"{who} gave up on {what}: {names}")
        self.stuck_label.setToolTip("\n".join(chapter_words(r, r.error or "") for r in rows))

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
        self.btn_retry.setEnabled(not busy)
        self.btn_cancel_stuck.setEnabled(not busy)
        self.progress.setVisible(busy)

    # --- Find now (the host's find pass) ------------------------------------------------------------------------

    def set_find_available(self, available: bool) -> None:
        self.btn_find_now.setVisible(available)

    def set_find_busy(self, busy: bool) -> None:
        """A find pass runs: the button waits (its text says so) until it ends."""
        self.btn_find_now.setEnabled(not busy)
        self.btn_find_now.setText("Finding…" if busy else "Find now")

    def set_note(self, text: str, tone: str = "") -> None:
        """A line under the list from the host (the find pass's summary); a reload keeps it."""
        self._host_note, self._host_tone = text, tone
        self._note = None
        self._check_failed = False
        set_tone(self.status_label, tone if text else "")
        self.status_label.setText(text)

    # --- Check downloads now -------------------------------------------------------------------------------------

    def check_now(self) -> bool:
        """Run the downloads check once (off the UI thread), then reload the list."""
        if self._call is not None:
            return False
        self._busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText("Checking the downloads...")
        self._check_failed = False
        self._host_note = ""
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
        shown = self.shown_records()
        if row < 0 or row >= len(shown):
            return
        record = shown[row]
        if record.status == DownloadStatus.FAILED:
            self._failed_menu(record, pos)
            return
        if record.is_chapters:              # a chapter download: no torrent to remove, nothing queued
            if can_cancel(record):          # on its way: Cancel always, Retry when its program gave up (audit B1)
                self._stuck_menu(record, pos)
            return
        if record.status == DownloadStatus.QUEUED:
            self._queue_menu(record, pos)
            return
        if not hasattr(self._backend, "remove_now"):
            return
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

    # --- Dismiss (a failed download, torrent or chapters) -----------------------------------------------------------

    def _failed_menu(self, record: DownloadRecord, pos: QPoint) -> None:
        if not callable(getattr(self._backend, "dismiss_downloads", None)):
            return
        menu = QMenu(self.table)
        remove = None
        if (not record.is_chapters and record.in_client
                and callable(getattr(self._backend, "remove_failed_download", None))):
            remove = menu.addAction(ACT_REMOVE_FAILED)
            remove.setToolTip(REMOVE_FAILED_TIP)
            remove.setEnabled(self._call is None)
        act = menu.addAction(ACT_DISMISS)
        act.setToolTip(DISMISS_TIP)
        act.setEnabled(self._call is None)
        menu.setToolTipsVisible(True)
        chosen = self._exec_menu(menu, self.table.viewport().mapToGlobal(pos))
        if chosen is act:
            self.dismiss(record)
        elif remove is not None and chosen is remove:
            files = self._confirm_remove_failed(record)
            if files is not None:
                self.remove_failed(record, delete_files=files)

    def _ask_remove_failed(self, record: DownloadRecord) -> Optional[bool]:
        """True: remove with its files; False: remove, keep its files; None: not removed."""
        box = QMessageBox(QMessageBox.Icon.Question, "Remove from qBittorrent?",
                          f"Remove the failed download \"{record.title}\" from qBittorrent?\n\n"
                          "MangaList filed nothing from it, so nothing in the library changes. With its files, the "
                          "downloaded data is deleted too.", parent=self)
        with_files = box.addButton("Remove with its files", QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Remove, keep its files", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        clicked = box.clickedButton()
        return True if clicked is with_files else False if clicked is keep else None

    def remove_failed(self, record: DownloadRecord, *, delete_files: bool) -> bool:
        """Remove a failed torrent download from qBittorrent (off the UI thread); the list reloads after."""
        fn = getattr(self._backend, "remove_failed_download", None)
        if self._call is not None or record.status != DownloadStatus.FAILED or not callable(fn):
            return False
        self._busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText("Removing from qBittorrent...")
        self._check_failed = False
        _log.info("In progress: removing failed download %d from qBittorrent (files: %s)", record.id, delete_files)
        self._call = start_call(lambda: fn(record.id, delete_files=delete_files),
                                lambda _r: self._on_removed_failed(record, delete_files), self._on_remove_failed_error,
                                self._after_check)
        return True

    def _on_removed_failed(self, record: DownloadRecord, delete_files: bool) -> None:
        self._note = (f"Removed from qBittorrent{' with its files' if delete_files else ''}: {record.title}. "
                      "It is under Show finished.")

    def dismiss(self, record: DownloadRecord) -> bool:
        """Dismiss a failed row (off the UI thread): its records are marked cancelled; the list reloads after."""
        fn = getattr(self._backend, "dismiss_downloads", None)
        if self._call is not None or record.status != DownloadStatus.FAILED or not callable(fn):
            return False
        ids = members_of(record, self.records)
        self._busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText("Dismissing...")
        self._check_failed = False
        _log.info("In progress: dismissing failed download(s) %s", ids)
        self._call = start_call(lambda: fn(ids), lambda n: self._on_dismissed(record, n), self._on_dismiss_error,
                                self._after_check)
        return True

    def _on_dismissed(self, record: DownloadRecord, n: int) -> None:
        self._note = (f"Dismissed: {record.title} ({self.name_of(record)}). It is under Show finished." if n
                      else f"Not dismissed: {record.title} is no longer failed.")

    def _on_remove_failed_error(self, message: str) -> None:
        self._note = None
        set_tone(self.status_label, "bad")
        self.status_label.setText(f"Could not remove it: {message}")
        self._check_failed = True

    def _on_dismiss_error(self, message: str) -> None:
        self._note = None
        set_tone(self.status_label, "bad")
        self.status_label.setText(f"Could not dismiss it: {message}")
        self._check_failed = True

    # --- chapter downloads Suwayomi gave up on: Retry / Cancel ----------------------------------------------------

    def _stuck_menu(self, record: DownloadRecord, pos: QPoint) -> None:
        menu = QMenu(self.table)
        retry = menu.addAction(chapter_words(record, ACT_RETRY))
        retry.setToolTip(chapter_words(record, RETRY_TIP) if is_stuck(record) else
                         chapter_words(record, "Only a chapter Suwayomi gave up on can be retried; this one is still "
                                               "queued or downloading there"))
        cancel = menu.addAction(ACT_CANCEL)
        cancel.setToolTip(chapter_words(record, CANCEL_TIP))
        for act, name in ((retry, "retry_chapter_downloads"), (cancel, "cancel_chapter_downloads")):
            act.setEnabled(self._call is None and callable(getattr(self._backend, name, None)))
        retry.setEnabled(retry.isEnabled() and is_stuck(record))
        menu.setToolTipsVisible(True)
        chosen = self._exec_menu(menu, self.table.viewport().mapToGlobal(pos))
        if chosen is retry:
            self.retry_stuck([record])
        elif chosen is cancel:
            self.cancel_stuck([record])

    def retry_stuck(self, rows: Optional[Sequence[DownloadRecord]] = None) -> bool:
        """Retry stuck chapter downloads in Suwayomi (*rows*: shown rows; default every stuck row), off the UI thread;
        the list reloads after."""
        rows = [r for r in (rows if rows is not None else self.stuck_rows) if is_stuck(r)]
        busy = chapter_words(rows[0], "Retrying in Suwayomi...") if rows else "Retrying..."
        return self._stuck_call("retry_chapter_downloads", rows, busy, "Retried", "Could not retry it")

    def cancel_stuck(self, rows: Optional[Sequence[DownloadRecord]] = None) -> bool:
        """Cancel chapter downloads still on their way - stuck or not (asked first), off the UI thread; the list reloads
        after (transition audit B1: the switch guard's "cancel them" must always be possible)."""
        rows = [r for r in (rows if rows is not None else self.stuck_rows) if can_cancel(r)]
        if not rows or self._call is not None or not self._confirm_cancel(rows):
            return False
        return self._stuck_call("cancel_chapter_downloads", rows, "Cancelling...", "Cancelled",
                                "Could not cancel it")

    def _stuck_call(self, name: str, rows: List[DownloadRecord], busy: str, done: str, failed: str) -> bool:
        fn = getattr(self._backend, name, None)
        rows = [r for r in rows if can_cancel(r)]
        if self._call is not None or not rows or not callable(fn):
            return False
        ids = [i for r in rows for i in members_of(r, self.records)]
        what = ", ".join(f"{self.name_of(r)} {r.title}" for r in rows)
        self._busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText(busy)
        self._check_failed = False
        _log.info("In progress: %s for chapter download(s) %s", name, ids)

        def on_done(n: int) -> None:
            self._note = f"{done}: {what} ({n} chapter{'s' if n != 1 else ''})."

        def on_error(message: str) -> None:
            self._note = None
            set_tone(self.status_label, "bad")
            self.status_label.setText(f"{failed}: {message}")
            self._check_failed = True

        self._call = start_call(lambda: fn(ids), on_done, on_error, self._after_check)
        return True

    def _ask_cancel(self, rows: Sequence[DownloadRecord]) -> bool:
        names = "\n".join(f"- {self.name_of(r)}: {r.title}" for r in rows)
        box = QMessageBox(QMessageBox.Icon.Question, "Cancel these downloads?",
                          chapter_words(rows[0], "Take these chapters out of Suwayomi's download queue and out of In "
                                                 "progress?") + f"\n\n{names}"
                          "\n\nNothing is deleted. A later check may find the chapters missing again.", parent=self)
        yes = box.addButton("Cancel downloads", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("Keep", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    # --- the queue (the download budget): the owner's overrides -------------------------------------------------

    def _queue_menu(self, record: DownloadRecord, pos: QPoint) -> None:
        if not any(callable(getattr(self._backend, name, None)) for name in QUEUE_ACTIONS.values()):
            return
        menu = QMenu(self.table)
        tips = {ACT_SEND_NOW: "Hand it to qBittorrent now, even though it takes MangaList past its download budget",
                ACT_TO_FRONT: "Make it the next download handed to qBittorrent when there is room",
                ACT_DEQUEUE: "Take it out of the queue; nothing was sent to qBittorrent"}
        acts = {}
        for text, name in QUEUE_ACTIONS.items():
            act = menu.addAction(text)
            act.setToolTip(tips[text])
            act.setEnabled(self._call is None and callable(getattr(self._backend, name, None))
                           and not (text == ACT_TO_FRONT and record.queue_position == 1))
            acts[act] = text
        menu.setToolTipsVisible(True)
        chosen = self._exec_menu(menu, self.table.viewport().mapToGlobal(pos))
        if chosen in acts:
            self.queue_action(acts[chosen], record)

    def _ask_queue(self, action: str, record: DownloadRecord) -> bool:
        size = gb_text(record.size_bytes) if record.size_bytes else "of a size not known yet"
        if action == ACT_SEND_NOW:
            title, verb = "Send now?", "Send now"
            usage = f" MangaList is {self.budget.usage_text()}." if self.budget is not None and self.budget.limited \
                else ""
            text = (f"Send \"{record.title}\" to qBittorrent now, past the download budget?\n\nIt is {size}.{usage} "
                    "It then counts like any download; the rest of the queue waits until there is room again.")
        else:
            title, verb = "Remove from the queue?", "Remove"
            text = (f"Remove \"{record.title}\" from the queue?\n\nNothing was sent to qBittorrent. To get it later, "
                    "send it again from the releases.")
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self)
        yes = box.addButton(verb, QMessageBox.ButtonRole.AcceptRole if action == ACT_SEND_NOW
                            else QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def queue_action(self, action: str, record: DownloadRecord) -> bool:
        """One of the queue overrides for a queued *record* (asking first where the action needs it), off the UI
        thread; the list reloads after."""
        method = getattr(self._backend, QUEUE_ACTIONS.get(action, ""), None)
        if self._call is not None or record.status != DownloadStatus.QUEUED or not callable(method):
            return False
        if action != ACT_TO_FRONT and not self._confirm_queue(action, record):
            return False
        self._busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText("Sending to qBittorrent..." if action == ACT_SEND_NOW else "Changing the queue...")
        self._check_failed = False
        self._call = start_call(lambda: method(record.id), lambda out: self._on_queue_done(action, out),
                                self._on_queue_error, self._after_check)
        return True

    def _on_queue_done(self, action: str, record: DownloadRecord) -> None:
        if action == ACT_SEND_NOW and record.status == DownloadStatus.FAILED:
            self._note = None
            set_tone(self.status_label, "bad")
            self.status_label.setText(f"Could not send it: {record.error or 'it failed'}")
            self._check_failed = True
            return
        self._note = {ACT_SEND_NOW: f"Sent to qBittorrent past the download budget: {record.title}.",
                      ACT_TO_FRONT: f"Moved to the front of the queue: {record.title}.",
                      ACT_DEQUEUE: f"Removed from the queue: {record.title}."}[action]

    def _on_queue_error(self, message: str) -> None:
        self._note = None
        set_tone(self.status_label, "bad")
        self.status_label.setText(f"Could not change the queue: {message}")
        self._check_failed = True

    # --- closing ---------------------------------------------------------------------------------------

    def stop(self) -> None:
        if self._call is not None:
            self._call.abandon()


def status_cell(record: DownloadRecord, tip: str) -> QWidget:
    """The status column of a row: the chip (the state, never squeezed - owner, 2026-10-18: "the chip should not be
    cut off") and the reason beside it, ended with "..." when the column is narrow; both carry the full tooltip."""
    chip_text, reason = status_parts(record)
    chip = pill(chip_text, badge_kind(record))
    chip.setObjectName("statusChip")
    chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    chip.setToolTip(tip)
    holder = QWidget()
    lay = hbox(chip, spacing=8, margins=(0, 0, 8, 0))
    lay.setAlignment(chip, Qt.AlignmentFlag.AlignVCenter)
    if reason:
        detail = ElidedLabel(reason)
        detail.setObjectName("statusDetail")
        detail.setProperty("role", "muted")
        detail.setToolTip(tip)
        lay.addWidget(detail, 1)
    else:
        lay.addStretch(1)
    holder.setLayout(lay)
    return holder


def hbox_widget(inner: QWidget) -> QWidget:
    """A table cell holding *inner* left-aligned (a pill must not stretch to the column)."""
    holder = QWidget()
    lay = hbox(inner, None, margins=(0, 0, 8, 0))
    lay.setAlignment(inner, Qt.AlignmentFlag.AlignVCenter)
    holder.setLayout(lay)
    return holder
