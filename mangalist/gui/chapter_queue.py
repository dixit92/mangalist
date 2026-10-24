"""The chapter download queue (transition fixes E2, 2026-10-24): what Suwayomi's web UI showed and the built-in extension
host has no UI for. A thin bar under the Download tab's In progress list opens a dialog with the chosen program's queue
as it reports it now - each chapter with its state, progress and tries - joined with MangaList's own records, so a row
says which series and chapter it is.

- The rows are read from the program (``backend.chapter_queue()``; off the UI thread), the records and the series names
  from the backend: a queue entry with no record of MangaList's own (sent from the program's own UI, or an older
  record) is listed by its chapter id.
- **Retry** and **Cancel...** are the In progress list's actions (``backend.retry_chapter_downloads`` /
  ``cancel_chapter_downloads``) for the failed rows that have a MangaList record - buttons, not only a menu, because the
  owner uses an iPad. The rules (what may be retried, the live check of the queue) are the backend's and the In progress
  list's; nothing is decided here.
- Everything the dialog says names the program in force (:mod:`.chapter_words`).

The row building is Qt-free (:func:`queue_rows`); the dialog and the bar are the only widgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from ..downloads.contracts import DownloadRecord, DownloadStatus, QueuedChapter
from . import theme
from .background import BackgroundCall, start_call
from .chapter_words import label_of, reword
from .download_style import set_tone, stylesheet
from .download_widgets import button, flat_table, hbox, label
from .downloads_backend import BackendError
from .palette import current
from .tables import cell, resizable_columns
from .volumes_target import numbers_text

COLUMNS = ("Series", "Chapter", "State", "Progress", "Tries")
COL_SERIES, COL_CHAPTER, COL_STATE, COL_PROGRESS, COL_TRIES = range(len(COLUMNS))
ERROR_STATE = "ERROR"
#: How the program's states read (Suwayomi's and the host's are the same four words).
STATE_TEXT = {"QUEUED": "Waiting", "DOWNLOADING": "Downloading", "FINISHED": "Finished", ERROR_STATE: "Failed"}
#: The records a queue entry can belong to: the chapter is not yet filed (a filed record's chapter has left the queue).
_OPEN = (DownloadStatus.SENT, DownloadStatus.DOWNLOADED, DownloadStatus.QUEUED)
TITLE = "Chapter download queue"
BAR_TIP = "The download queue as Suwayomi reports it now: each chapter's state, progress and tries"
LEAD = ("The chapters Suwayomi has queued, as it reports them now. Finished chapters leave the queue when MangaList has "
        "filed them. A chapter that failed after its tries can be retried here or in In progress; MangaList also "
        "retries by itself every 6 hours.")
EMPTY = "The queue is empty."
UNKNOWN_ROW = "(not sent by MangaList)"
CANCEL_ASK = ("Take {n} chapter{s} out of {program}'s download queue and out of In progress?\n\nThey are marked "
              "cancelled (under Show finished). A later check may find them missing again.")


@dataclass(frozen=True)
class QueueRow:
    """One queue entry with what MangaList knows about it."""

    chapter_id: int
    state: str                          # the program's word: QUEUED | DOWNLOADING | FINISHED | ERROR
    progress: float = 0.0
    tries: int = 0
    series_id: Optional[int] = None     # None: no record of MangaList's own
    series: str = ""
    chapter: str = ""                   # the chapter number(s) of the record ('' when unknown)
    record_id: Optional[int] = None

    @property
    def failed(self) -> bool:
        return self.state == ERROR_STATE

    @property
    def state_text(self) -> str:
        return STATE_TEXT.get(self.state, self.state.title() or "?")

    @property
    def progress_text(self) -> str:
        return f"{round(self.progress * 100)}%" if self.state == "DOWNLOADING" else ""


def _record_ref(record: DownloadRecord) -> int:
    return int(record.info_hash) if str(record.info_hash).isdigit() else -1


def queue_rows(items: Sequence[QueuedChapter], records: Sequence[DownloadRecord],
               titles: Mapping[int, str]) -> List[QueueRow]:
    """The queue's entries with the series and chapter of MangaList's record of each (the open record of that chapter id)."""
    by_ref: Dict[int, DownloadRecord] = {}
    for rec in records:
        if rec.is_chapters and rec.status in _OPEN:
            by_ref.setdefault(_record_ref(rec), rec)
    rows = []
    for item in items:
        rec = by_ref.get(int(item.chapter_id))
        rows.append(QueueRow(chapter_id=int(item.chapter_id), state=str(item.state or ""), progress=float(item.progress or 0),
                             tries=int(item.tries or 0), series_id=rec.series_id if rec else None,
                             series=(titles.get(rec.series_id) or "") if rec and rec.series_id is not None else "",
                             chapter=numbers_text(rec.wanted_chapters) if rec and rec.wanted_chapters else "",
                             record_id=rec.id if rec else None))
    return rows


def summary_text(rows: Sequence[QueueRow]) -> str:
    """"2 downloading, 3 waiting, 1 failed" (the words of the states, in this order)."""
    if not rows:
        return EMPTY
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row.state_text.lower()] = counts.get(row.state_text.lower(), 0) + 1
    order = [s.lower() for s in ("Downloading", "Waiting", "Failed", "Finished")]
    parts = [f"{counts[k]} {k}" for k in order if k in counts] + [f"{n} {k}" for k, n in counts.items() if k not in order]
    return ", ".join(parts)


def queue_available(backend) -> bool:
    """The backend can read the program's queue (a fake without it hides the bar)."""
    return callable(getattr(backend, "chapter_queue", None))


def fetch_queue(backend) -> List[QueuedChapter]:
    """The chosen program's download queue (blocking; a :class:`BackendError` with a readable reason), through
    ``backend.chapter_queue()``."""
    try:
        return list(backend.chapter_queue())
    except BackendError:
        raise
    except Exception as exc:  # noqa: BLE001 - the clients' own errors carry a readable reason and no secret
        raise BackendError(str(exc) or "the program did not answer") from None


class QueueView:
    """What one load found: the rows and the program's name (the backend can switch between loads)."""

    def __init__(self, rows: List[QueueRow]):
        self.rows = rows


class ChapterQueueDialog(QDialog):
    """The queue, read-only, with Retry / Cancel... for the failed rows MangaList has a record of."""

    changed = Signal()                  # a retry or a cancel went through: the In progress list reloads

    def __init__(self, backend, parent: Optional[QWidget] = None,
                 confirm: Optional[Callable[[QWidget, str], bool]] = None):
        super().__init__(parent)
        self._backend = backend
        self._confirm = confirm or self._ask
        self._calls: List[BackgroundCall] = []
        self.rows: List[QueueRow] = []
        self.setObjectName("chapterQueueDialog")
        self.setWindowTitle(TITLE)
        theme.bind_sheet(self, lambda: stylesheet(current()))
        self.resize(820, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        self.title_label = label(TITLE, "h2")
        self.lead_label = label("", "lead", wrap=True)
        self.summary_label = label("", "muted", wrap=True)
        self.table = flat_table("chapterQueueTable", COLUMNS)
        resizable_columns(self.table, {COL_SERIES: 260, COL_CHAPTER: 110, COL_STATE: 120, COL_PROGRESS: 90,
                                       COL_TRIES: 70})
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.status_label = label("", wrap=True, selectable=True)
        self.btn_reload = button("Reload", tip="Read the queue again now")
        self.btn_reload.clicked.connect(self.load)
        self.btn_retry = button("Retry", tip="Put the selected failed chapters back in the queue now")
        self.btn_retry.clicked.connect(self.retry_selected)
        self.btn_cancel = button("Cancel…", tip="Take the selected failed chapters out of the queue and out of In "
                                                "progress")
        self.btn_cancel.clicked.connect(self.cancel_selected)
        self.btn_close = button("Close")
        self.btn_close.clicked.connect(self.reject)
        lay.addWidget(self.title_label)
        lay.addWidget(self.lead_label)
        lay.addWidget(self.summary_label)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.status_label)
        lay.addLayout(hbox(self.btn_reload, None, self.btn_retry, self.btn_cancel, self.btn_close, spacing=8))
        self._apply_wording()
        self._update_buttons()

    # --- wording ----------------------------------------------------------------------------------------------

    def _w(self, text: str) -> str:
        return reword(self._backend, text) or ""

    def _apply_wording(self) -> None:
        self.title_label.setText(f"{TITLE} · {label_of(self._backend)}")
        self.lead_label.setText(self._w(LEAD))

    def _ask(self, parent: QWidget, text: str) -> bool:
        return QMessageBox.question(parent, "Cancel downloads", text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    # --- loading ----------------------------------------------------------------------------------------------

    def _spawn(self, fn, on_done, on_error) -> BackgroundCall:
        made: list = []
        call = start_call(fn, on_done, on_error, lambda: self._calls.remove(made[0]) if made and made[0] in self._calls
                          else None)
        made.append(call)
        self._calls.append(call)
        return call

    def load(self, keep_status: bool = False) -> bool:
        """Read the queue, MangaList's records and the series names (off the UI thread). *keep_status*: after a retry or
        a cancel, whose result stays on the status line."""
        backend = self._backend
        self._apply_wording()
        self.btn_reload.setEnabled(False)
        if not keep_status:
            set_tone(self.status_label, "")
            self.status_label.setText(self._w("Asking Suwayomi for its queue..."))

        def run() -> QueueView:
            items = fetch_queue(backend)
            records = [r for r in backend.records() if r.is_chapters]
            ids = {r.series_id for r in records if r.series_id is not None}
            titles_fn = getattr(backend, "series_titles", None)
            titles = dict(titles_fn(ids)) if callable(titles_fn) and ids else {}
            return QueueView(queue_rows(items, records, titles))

        self._spawn(run, self._show, self._failed)
        return True

    def _failed(self, message: str) -> None:
        self.btn_reload.setEnabled(True)
        self.rows = []
        self.table.setRowCount(0)
        self.summary_label.setText("")
        set_tone(self.status_label, "bad")
        self.status_label.setText(self._w(message))
        self._update_buttons()

    def _show(self, view: QueueView) -> None:
        self.btn_reload.setEnabled(True)
        self.rows = list(view.rows)
        if self.status_label.property("tone") != "ok":          # the result of a retry / cancel stays until the next action
            self.status_label.setText("")
        self.summary_label.setText(summary_text(self.rows))
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            self.table.setItem(r, COL_SERIES, cell(row.series or UNKNOWN_ROW))
            self.table.setItem(r, COL_CHAPTER, cell(f"ch {row.chapter}" if row.chapter else f"id {row.chapter_id}"))
            state = cell(row.state_text)
            if row.failed:
                state.setToolTip(self._w("Suwayomi gave up on this chapter after its tries. Retry puts it back in the "
                                         "queue."))
            self.table.setItem(r, COL_STATE, state)
            self.table.setItem(r, COL_PROGRESS, cell(row.progress_text))
            self.table.setItem(r, COL_TRIES, cell(str(row.tries) if row.tries else ""))
        self._update_buttons()

    # --- Retry / Cancel for the failed rows --------------------------------------------------------------------

    def selected_rows(self) -> List[QueueRow]:
        picked = {i.row() for i in self.table.selectionModel().selectedRows()} if self.table.selectionModel() else set()
        return [self.rows[r] for r in sorted(picked) if r < len(self.rows)]

    def _actionable(self) -> List[QueueRow]:
        return [r for r in self.selected_rows() if r.failed and r.record_id is not None]

    def _update_buttons(self) -> None:
        can = bool(self._actionable()) and not self._calls
        self.btn_retry.setEnabled(can)
        self.btn_cancel.setEnabled(can)

    def retry_selected(self) -> bool:
        return self._stuck("retry_chapter_downloads", "Retrying...", "Retried {n} chapter{s}.")

    def cancel_selected(self) -> bool:
        rows = self._actionable()
        if not rows:
            return False
        n = len(rows)
        if not self._confirm(self, self._w(CANCEL_ASK.format(n=n, s="" if n == 1 else "s", program="Suwayomi"))):
            return False
        return self._stuck("cancel_chapter_downloads", "Cancelling...", "Cancelled {n} chapter{s}.")

    def _stuck(self, method: str, busy: str, done: str) -> bool:
        rows = self._actionable()
        call = getattr(self._backend, method, None)
        if not rows or not callable(call) or self._calls:
            return False
        ids = [r.record_id for r in rows]
        set_tone(self.status_label, "")
        self.status_label.setText(busy)
        self._spawn(lambda: call(ids), lambda n: self._stuck_done(done, n), self._failed_action)
        self._update_buttons()
        return True

    def _stuck_done(self, done: str, n: int) -> None:
        set_tone(self.status_label, "ok")
        self.status_label.setText(self._w(done.format(n=n, s="" if n == 1 else "s")))
        self.changed.emit()
        self.load(keep_status=True)

    def _failed_action(self, message: str) -> None:
        set_tone(self.status_label, "bad")
        self.status_label.setText(self._w(message))
        self._update_buttons()

    def done(self, result: int) -> None:  # noqa: N802 - Qt
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()
        super().done(result)


class ChapterQueueBar(QWidget):
    """The strip under the In progress list: "Chapter queue…" opens the dialog. Hidden when the backend cannot read a
    queue."""

    changed = Signal()

    def __init__(self, backend, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("chapterQueueBar")
        self._backend = backend
        self.dialog: Optional[ChapterQueueDialog] = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.btn_open = button("Chapter queue…", link=True)
        self.btn_open.clicked.connect(self.open_queue)
        row.addStretch(1)
        row.addWidget(self.btn_open)
        self.refresh()

    def refresh(self) -> None:
        """Visible while the backend can read the queue; the tooltip names the program in force."""
        self.setVisible(queue_available(self._backend))
        self.btn_open.setToolTip(reword(self._backend, BAR_TIP) or "")

    def open_queue(self) -> Optional[ChapterQueueDialog]:
        if not queue_available(self._backend):
            return None
        dialog = ChapterQueueDialog(self._backend, self)
        dialog.changed.connect(self.changed)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda _o=None: setattr(self, "dialog", None))
        self.dialog = dialog
        dialog.load()
        dialog.show()
        return dialog
