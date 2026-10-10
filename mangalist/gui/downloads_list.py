"""The "In progress" list: every download MangaList has sent to qBittorrent or queued, and where it stands (Queued -
2nd in line, Downloading, Downloaded, Filed v03-v05 - seeding, Failed: <reason>, ...), with Check downloads now, the
time of the next automatic check and the download budget ("Using 31.2 GB of 50 GB; 2 downloads queued"). Chapter
downloads (Suwayomi) show here too, one row per Send ("Ch. 101-104 · <group> · MangaDex (EN)": Downloading, Filed ch
101-104 - done, ...); they have no row menu (no torrent, no queue) and the check files them as well.

The records are read off the UI thread (``refresh``); the Download tab shows this list at the bottom and learns the
records from :attr:`records_loaded`, the downloads dialog shows it alone.

Row menu (right-click): a filed download offers "Remove now..."; a queued one (owner, 2026-10-09: "the user can override
the queue") "Send now (past the cap)...", "Move to the front of the queue" and "Remove from the queue...". Sending past
the cap and removing ask first, as Remove now does; moving to the front does not (it changes no download).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QMenu, QMessageBox, QProgressBar, QVBoxLayout, QWidget

from ..downloads.budget import BudgetState, gb_text
from ..downloads.contracts import DownloadRecord, DownloadStatus
from .background import BackgroundCall, start_call
from .download_rules import badge_kind, in_progress, merge_batches, next_check_text, when_text
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
    budget: Optional[BudgetState] = None                       # None: the backend keeps no download budget


def load_snapshot(backend: DownloadsBackend) -> DownloadsSnapshot:
    """Everything the list needs, in one background call (the optional backend extras are used when present)."""
    records = list(backend.records())
    titles_fn = getattr(backend, "series_titles", None)
    titles = dict(titles_fn({r.series_id for r in records})) if titles_fn and records else {}
    next_fn = getattr(backend, "next_check", None)
    budget_fn = getattr(backend, "budget_status", None)
    return DownloadsSnapshot(records, titles, next_fn() if next_fn else None, budget_fn() if budget_fn else None)


#: The queue actions of the row menu (the backend method each calls).
ACT_SEND_NOW = "Send now (past the cap)…"
ACT_TO_FRONT = "Move to the front of the queue"
ACT_DEQUEUE = "Remove from the queue…"
QUEUE_ACTIONS = {ACT_SEND_NOW: "send_queued_now", ACT_TO_FRONT: "move_to_front", ACT_DEQUEUE: "remove_from_queue"}


class DownloadsList(QWidget):
    records_loaded = Signal(object)         # the records (a list of DownloadRecord), newest first
    check_finished = Signal(bool)           # a "Check downloads now" ended (True: it worked)

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None, autostart: bool = True,
                 heading: str = "In progress", series_name: Optional[Callable[[int], str]] = None,
                 confirm_remove: Optional[Callable[[DownloadRecord], bool]] = None,
                 confirm_queue: Optional[Callable[[str, DownloadRecord], bool]] = None):
        super().__init__(parent)
        self._confirm_remove = confirm_remove or self._ask_remove
        self._confirm_queue = confirm_queue or self._ask_queue
        self.setObjectName("downloadsList")
        self._backend = backend
        self._series_name = series_name
        self._call: Optional[BackgroundCall] = None
        self._note: Optional[str] = None            # the last "Check downloads now" summary, kept across the reload
        self._check_failed = False
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
        self.btn_refresh.setToolTip("Show the latest saved state of these downloads (asks neither qBittorrent, Suwayomi "
                                    "nor MangaPixer)")
        self.btn_refresh.clicked.connect(self.refresh)
        # owner, 2026-10-10: "Check downloads now is misleading since we have another provider" - it asks Suwayomi too
        self.btn_check = button("Check downloads now", tip="Ask qBittorrent and Suwayomi now: file finished downloads, "
                                                            "remove completed torrents and hand queued torrents over "
                                                            "while they fit the download budget - the same check that "
                                                            "runs every hour on its own")
        self.btn_check.clicked.connect(self.check_now)
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
                             self.btn_finished, self.btn_refresh, self.btn_check, spacing=12))

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
        self.budget = snap.budget
        self.budget_label.setText(f"· {snap.budget.summary()}" if snap.budget is not None else "")
        self.budget_label.setVisible(snap.budget is not None)
        self._show(self.records)
        if not self._check_failed:
            empty = "" if self.records else "Nothing has been sent to qBittorrent yet."
            if self.records and not self.shown_records():
                empty = "Nothing is in progress."
            set_tone(self.status_label, "")
            self.status_label.setText(" ".join(t for t in (self._note or "", empty) if t))
        self.records_loaded.emit(list(self.records))

    def name_of(self, record: DownloadRecord) -> str:
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

    # --- Check downloads now -------------------------------------------------------------------------------------

    def check_now(self) -> bool:
        """Run the downloads check once (off the UI thread), then reload the list."""
        if self._call is not None:
            return False
        self._busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText("Checking the downloads...")
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
        shown = self.shown_records()
        if row < 0 or row >= len(shown):
            return
        record = shown[row]
        if record.is_chapters:              # a chapter download (Suwayomi): no torrent to remove, nothing queued
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


def hbox_widget(inner: QWidget) -> QWidget:
    """A table cell holding *inner* left-aligned (a pill must not stretch to the column)."""
    holder = QWidget()
    lay = hbox(inner, None, margins=(0, 0, 8, 0))
    lay.setAlignment(inner, Qt.AlignmentFlag.AlignVCenter)
    holder.setLayout(lay)
    return holder
