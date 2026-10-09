"""The releases panel: the nyaa releases for the missing volumes of ONE series, best first, the folder they will be filed
into, and Send to qBittorrent. It is the find-volumes dialog's content as a widget: the Download tab shows it on the
right (fed by its search queue), :class:`~mangalist.gui.nyaa_dialog.NyaaDialog` shows it alone and runs the search itself.

Everything slow (the placement lookup, the nyaa search, the send) runs off the UI thread through :mod:`.background`.
The backend's rank order is kept (sorting is off); a release's ranking reasons are the small line under its name.
When the series' folder layout is ambiguous the owner picks one of the offered folders first: Send stays disabled until a
folder is chosen. Nothing is sent without a confirmation that names the series, the volumes and the target folder.
"""

from __future__ import annotations

import html
import webbrowser
from dataclasses import dataclass
from pathlib import PurePath
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHeaderView,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..classifier import _human
from ..downloads.contracts import DownloadRecord, NyaaCandidate, Placement
from .background import BackgroundCall, start_call
from .download_rules import release_why
from .download_style import FONT_MONO, set_tone
from .download_widgets import ROLE_SUB, RadioDelegate, TwoLineDelegate, button, flat_table, hbox, label
from .downloads_backend import DownloadsBackend
from .volumes_target import VolumeTarget, numbers_text, volume_label

ConfirmFn = Callable[[QWidget, str], bool]

COLUMNS = ("", "Release", "Fills", "You have", "Source", "Size", "Seeders")
COL_PICK, COL_RELEASE, COL_FILLS, COL_HELD, COL_SOURCE, COL_SIZE, COL_SEEDERS = range(len(COLUMNS))
PAGE_MESSAGE, PAGE_RESULTS = 0, 1
ROW_HEIGHT = 48

NO_FOLDER_CHOSEN = "Choose a folder..."
FOOTER_NOTE = "MangaList links only the missing volumes into the series folder; nothing there is replaced."


def _default_confirm(parent: QWidget, text: str) -> bool:
    return QMessageBox.question(parent, "Send to qBittorrent", text,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


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


def folder_text(target_dir: Optional[str], series_dir: str) -> str:
    """``Series/Volumes/`` - the target folder written from the series folder's parent; the full path when it is not
    below it."""
    if not target_dir:
        return ""
    try:
        rel = PurePath(target_dir).relative_to(PurePath(series_dir).parent)
    except ValueError:
        return target_dir
    return rel.as_posix() + "/"


@dataclass
class SearchOutcome:
    """What the placement lookup and the nyaa search found for one series (a host that runs the searches itself, like
    the Download tab's queue, hands it to :meth:`ReleasesPanel.show_outcome`)."""

    target: VolumeTarget
    placement: Optional[Placement] = None
    placement_error: Optional[str] = None
    candidates: Optional[List[NyaaCandidate]] = None     # None: the search failed (see ``error``)
    error: Optional[str] = None


def _mono(text: str) -> str:
    return f"<span style=\"font-family: {FONT_MONO.replace(chr(34), '')}\">{html.escape(text)}</span>"


class ReleasesPanel(QWidget):
    sent = Signal(object)               # a DownloadRecord, once qBittorrent has the release
    search_again = Signal()             # managed: "Search again" was pressed, the host runs the search
    settings_requested = Signal(str)    # a section of the Settings dialog

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmFn] = None, open_url: Optional[Callable[[str], object]] = None,
                 managed: bool = False, source_label: str = "nyaa"):
        super().__init__(parent)
        self.setObjectName("releasesPanel")
        self._backend = backend
        self._confirm = confirm or _default_confirm
        self._open_url = open_url or webbrowser.open
        self.managed = managed
        self.source_label = source_label
        self.target: Optional[VolumeTarget] = None
        self._candidates: List[NyaaCandidate] = []
        self._placement: Optional[Placement] = None
        self._placement_error: Optional[str] = None
        self._search_state = "idle"          # idle | searching | done | error
        self._calls: List[BackgroundCall] = []
        self._sending = False
        self._sent_hashes = set()
        self.sent_records: List[DownloadRecord] = []
        self._settings_section: Optional[str] = None
        self._build_ui()

    # --- UI ------------------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        head = hbox(spacing=16)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = label("", "h2")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.subtitle_label = label("", "muted", wrap=True)
        self.subtitle_label.setTextFormat(Qt.TextFormat.RichText)
        self.subtitle_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        head.addLayout(titles, 1)
        self.search_label = label("", "muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(90)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.btn_retry = button("Search again")
        self.btn_retry.clicked.connect(self._on_search_again)
        head.addWidget(self.search_label)
        head.addWidget(self.progress)
        head.addWidget(self.btn_retry)
        outer.addLayout(head)

        # The folder choice (only when the layout is ambiguous)
        self.folder_row = QWidget()
        fr = QVBoxLayout(self.folder_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.setSpacing(4)
        self.folder_label = label("", wrap=True, selectable=True)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_chosen)
        self.folder_reason = label("", "muted", wrap=True)
        fr.addWidget(self.folder_label)
        fr.addWidget(self.folder_combo)
        fr.addWidget(self.folder_reason)
        self.folder_row.setVisible(False)
        outer.addWidget(self.folder_row)

        self.stack = QStackedWidget()
        self.message = QWidget()
        mv = QVBoxLayout(self.message)
        mv.setContentsMargins(0, 24, 0, 0)
        mv.setSpacing(10)
        self.message_title = label("", "h3", wrap=True)
        self.message_text = label("", "lead", wrap=True, selectable=True)
        self.message_action = button("", primary=True)
        self.message_action.clicked.connect(lambda: self.settings_requested.emit(self._settings_section or ""))
        self.message_action.setVisible(False)
        mv.addWidget(self.message_title)
        mv.addWidget(self.message_text)
        mv.addLayout(hbox(self.message_action, None))
        mv.addStretch(1)
        self.stack.addWidget(self.message)

        self.results_page = QWidget()
        rp = QVBoxLayout(self.results_page)
        rp.setContentsMargins(0, 0, 0, 0)
        self.results_card = QFrame()
        self.results_card.setObjectName("releasesCard")
        cv = QVBoxLayout(self.results_card)
        cv.setContentsMargins(0, 0, 0, 0)
        self.table = flat_table("releasesTable", COLUMNS)
        self.table.setSortingEnabled(False)               # the backend's rank order is the point
        self.table.setItemDelegateForColumn(COL_PICK, RadioDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_RELEASE, TwoLineDelegate(self.table, row_height=ROW_HEIGHT))
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_RELEASE, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        for col, width in ((COL_PICK, 36), (COL_FILLS, 110), (COL_HELD, 110), (COL_SOURCE, 84), (COL_SIZE, 90),
                           (COL_SEEDERS, 80)):
            self.table.setColumnWidth(col, width)
        for col in (COL_SIZE, COL_SEEDERS):
            self.table.horizontalHeaderItem(col).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.itemSelectionChanged.connect(self._update_send)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected_page())
        cv.addWidget(self.table)
        rp.addWidget(self.results_card)
        self.status_label = label("", wrap=True, selectable=True)
        self.footer = QWidget()
        foot = hbox(spacing=12)
        self.footer.setLayout(foot)
        foot.setContentsMargins(0, 0, 0, 0)
        self.note_label = label(FOOTER_NOTE, "muted", wrap=True)
        self.btn_page = button("Open the nyaa page", link=True, tip="Open the selected release's page on nyaa in the browser")
        self.btn_page.clicked.connect(self.open_selected_page)
        self.btn_send = button("Send to qBittorrent", primary=True)
        self.btn_send.setMinimumHeight(38)
        self.btn_send.clicked.connect(self.send_selected)
        foot.addWidget(self.note_label, 1)
        foot.addWidget(self.btn_page)
        foot.addWidget(self.btn_send)
        rp.addSpacing(4)
        rp.addWidget(self.footer)
        rp.addWidget(self.status_label)
        rp.addStretch(1)
        self.stack.addWidget(self.results_page)
        outer.addWidget(self.stack, 1)
        self.show_message("", "Select a series to see its releases.")

    # --- what the panel shows -------------------------------------------------------------------------

    def show_message(self, title: str, text: str, action: Optional[str] = None, section: Optional[str] = None) -> None:
        """No results to show: a heading and a sentence, optionally with a button to a Settings section."""
        self._reset(None)
        self.title_label.setText(title)
        self.subtitle_label.setText("")
        self.message_title.setText("")
        self.message_title.setVisible(False)
        self.message_text.setText(text)
        self._settings_section = section
        self.message_action.setText(action or "")
        self.message_action.setVisible(bool(action))
        self.search_label.setText("")
        self.stack.setCurrentIndex(PAGE_MESSAGE)
        self._set_chrome(False)
        self._update_send()

    def _set_chrome(self, on: bool) -> None:
        """Search again belongs to a series; a message about no series stands alone."""
        self.btn_retry.setVisible(on)

    def show_searching(self, target: VolumeTarget) -> None:
        self._reset(target)
        self._search_state = "searching"
        self._begin_target_view()
        self.btn_retry.setEnabled(False)
        self.progress.setVisible(True)
        self.search_label.setText("Searching nyaa...")
        self.message_text.setText("Searching nyaa...")
        self.stack.setCurrentIndex(PAGE_MESSAGE)
        self._update_send()

    def show_outcome(self, outcome: SearchOutcome) -> None:
        """Show a finished lookup + search (the host ran them)."""
        self._reset(outcome.target)
        self._begin_target_view()
        if outcome.placement_error:
            self._on_placement_error(outcome.placement_error)
        elif outcome.placement is not None:
            self._on_placement(outcome.placement)
        if outcome.candidates is None:
            self._on_search_error(outcome.error or "the search failed")
        else:
            self._on_results(outcome.candidates)

    def _reset(self, target: Optional[VolumeTarget]) -> None:
        self.target = target
        self._candidates = []
        self._placement = None
        self._placement_error = None
        self._search_state = "idle"
        self.table.setRowCount(0)
        self.progress.setVisible(False)
        self.btn_retry.setEnabled(target is not None)
        self.folder_row.setVisible(False)
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.blockSignals(False)
        if not self._sending:
            self.status_label.setText("")
            set_tone(self.status_label, "")

    def _begin_target_view(self) -> None:
        target = self.target
        assert target is not None
        self.title_label.setText(target.title)
        self.message_title.setVisible(False)
        self.message_action.setVisible(False)
        self._set_chrome(True)
        self.btn_retry.setEnabled(True)
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        target = self.target
        if target is None:
            self.subtitle_label.setText("")
            return
        missing = (f"Missing {_mono(numbers_text(target.missing, pad=True))}" if target.missing
                   else "Missing volumes not known - releases are compared with the volumes you have")
        if self._placement is None:
            where = "files go to ..." if not self._placement_error else "the target folder could not be worked out"
        elif self._placement.ambiguous:
            where = "choose the folder the files go to"
        else:
            where = (f"files go to {_mono(folder_text(self._placement.target_dir, self._placement.series_dir))}"
                     + (f" ({html.escape(self._placement.reason)})" if self._placement.reason else ""))
        self.subtitle_label.setText(f"{missing} &middot; {where}")

    # --- standalone search (the dialog) -----------------------------------------------------------------

    def open_target(self, target: VolumeTarget) -> None:
        """Look up the target folder and search for *target* (the panel runs both itself, off the UI thread)."""
        self.show_searching(target)
        self._spawn(lambda: self._backend.placement(target.series_id), self._on_placement, self._on_placement_error)
        self._search()

    def _search(self) -> None:
        target = self.target
        if target is None:
            return
        self._search_state = "searching"
        self.btn_retry.setEnabled(False)
        self.progress.setVisible(True)
        self.search_label.setText("Searching nyaa...")
        self._spawn(lambda: list(self._backend.search(target.titles, target.missing, target.held)),
                    self._on_results, self._on_search_error)

    def _on_search_again(self) -> None:
        if self.managed:
            self.search_again.emit()
        elif self.target is not None and self._search_state != "searching":
            self._search()

    def _spawn(self, fn, on_done, on_error) -> None:
        made = []                       # the call, for its own finish callback (delivered by the event loop, after this)
        call = start_call(fn, on_done, on_error,
                          lambda: self._calls.remove(made[0]) if made and made[0] in self._calls else None)
        made.append(call)
        self._calls.append(call)

    # --- results ---------------------------------------------------------------------------------------

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
            self.folder_row.setVisible(True)
        else:
            self.folder_row.setVisible(False)
            self.folder_label.setText(placement.target_dir or "")
        self.subtitle_label.setToolTip(placement.target_dir or "")
        self._update_subtitle()
        self._update_send()

    def _on_placement_error(self, message: str) -> None:
        self._placement = None
        self._placement_error = message
        self.folder_label.setText(f"Could not work out the target folder: {message}")
        self.folder_reason.setText("")
        self.folder_row.setVisible(True)
        self.folder_combo.setVisible(False)
        self._update_subtitle()
        self._update_send()

    def _on_folder_chosen(self, *_args) -> None:
        self._update_subtitle_for_choice()
        self._update_send()

    def _update_subtitle_for_choice(self) -> None:
        placement, chosen = self._placement, self.chosen_target_dir()
        if placement is not None and placement.ambiguous and chosen:
            target = self.target
            missing = f"Missing {_mono(numbers_text(target.missing, pad=True))}" if target and target.missing else ""
            self.subtitle_label.setText(f"{missing} &middot; files go to {_mono(folder_text(chosen, placement.series_dir))}")

    def _on_results(self, candidates: Sequence[NyaaCandidate]) -> None:
        self._search_state = "done"
        self.progress.setVisible(False)
        self.btn_retry.setEnabled(True)
        self._candidates = list(candidates)
        self.table.setRowCount(len(self._candidates))
        for row, candidate in enumerate(self._candidates):
            self._fill_row(row, candidate)
        head = self.table.horizontalHeader().height()
        self.table.setMinimumHeight(head + ROW_HEIGHT * min(len(self._candidates), 3) + 2)
        self.table.setMaximumHeight(head + ROW_HEIGHT * len(self._candidates) + 2)      # the card fits its rows
        n = len(self._candidates)
        self.search_label.setText(f"{self.source_label} · {n} release{'s' if n != 1 else ''}")
        if not self._candidates:
            self.message_title.setVisible(False)
            self.message_text.setText("No releases found on nyaa for this series (with seeders, English).")
            self.stack.setCurrentIndex(PAGE_MESSAGE)
        else:
            self.stack.setCurrentIndex(PAGE_RESULTS)
            self.table.selectRow(0)
        self._update_send()

    def _fill_row(self, row: int, candidate: NyaaCandidate) -> None:
        unknown = candidate.vol_from is None
        values = ("", candidate.title,
                  "?" if unknown else numbers_text(candidate.covers_missing, pad=True) or "-",
                  "?" if unknown else numbers_text(candidate.covers_held, pad=True) or "-",
                  "nyaa", _human(candidate.size_bytes), str(candidate.seeders))
        tip = self._tooltip(candidate)
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setToolTip(tip)
            if col == COL_RELEASE:
                item.setData(ROLE_SUB, release_why(candidate))
            if col in (COL_SEEDERS, COL_SIZE):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)

    def _on_search_error(self, message: str) -> None:
        self._search_state = "error"
        self.progress.setVisible(False)
        self.btn_retry.setEnabled(True)
        self._candidates = []
        self.table.setRowCount(0)
        self.search_label.setText("")
        self.message_title.setVisible(False)
        self.message_text.setText(f"The nyaa search failed: {message}")
        self.stack.setCurrentIndex(PAGE_MESSAGE)
        self._update_send()

    @staticmethod
    def _tooltip(candidate: NyaaCandidate) -> str:
        lines = list(candidate.reasons) or ["No ranking reasons given"]
        if candidate.not_comic:
            lines.append("Light novel / not a comic release")
        if candidate.remake:
            lines.append("Marked as a remake on nyaa")
        if candidate.group:
            lines.append(f"Group: {candidate.group}")
        if candidate.published:
            lines.append(f"Published {candidate.published[:10]}")
        return "\n".join(lines)

    # --- selection ------------------------------------------------------------------------------------

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
        if self.target is None:
            return "Select a series."
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
            if candidate.vol_from is None:
                return "The title does not say which volumes this release holds, and the missing volumes are not known."
            return ("This release holds none of the missing volumes." if self.target.missing
                    else "This release holds only volumes you already have.")
        return None

    def _update_send(self, *_args) -> None:
        blocker = self.send_blocker()
        self.btn_send.setEnabled(blocker is None)
        self.btn_send.setToolTip(blocker or "Add the selected release to qBittorrent")
        self.btn_page.setEnabled(self.selected_candidate() is not None)

    # --- actions ---------------------------------------------------------------------------------------

    def open_selected_page(self) -> bool:
        candidate = self.selected_candidate()
        if candidate is None or not candidate.view_url.lower().startswith(("http://", "https://")):
            return False
        self._open_url(candidate.view_url)
        return True

    def confirmation_text(self, candidate: NyaaCandidate, wanted: Sequence[str], target_dir: str) -> str:
        title = self.target.title if self.target else ""
        if candidate.vol_from is None:
            vols = (f"{numbers_text(wanted, pad=True)} (the release's title does not say which volumes it holds; "
                    "MangaList files whichever of these it contains)")
        else:
            vols = numbers_text(wanted, pad=True)
        return (f"Send this release to qBittorrent?\n\n{candidate.title}\n\n"
                f"Series: {title}\nVolumes: {vols}\nTarget folder: {target_dir}\n\n"
                "qBittorrent downloads it; MangaList files these volumes into the target folder when it has finished.")

    def send_selected(self) -> bool:
        if self.send_blocker() is not None:
            return False
        candidate = self.selected_candidate()
        target_dir = self.chosen_target_dir()
        if candidate is None or target_dir is None or self.target is None:
            return False
        wanted = tuple(wanted_volumes_for(candidate, self.target.missing))
        if not self._confirm(self, self.confirmation_text(candidate, wanted, target_dir)):
            return False
        self._sending = True
        self._update_send()
        set_tone(self.status_label, "")
        self.status_label.setText("Sending to qBittorrent...")
        series_id = self.target.series_id
        self._spawn(lambda: self._backend.send(series_id, candidate, wanted, target_dir),
                    lambda record, c=candidate: self._on_sent(c, record), self._on_send_error)
        return True

    def _on_sent(self, candidate: NyaaCandidate, record: DownloadRecord) -> None:
        self._sending = False
        self._sent_hashes.add(candidate.info_hash)
        self.sent_records.append(record)
        if self.target is not None and self.target.series_id == record.series_id:     # not after a switch of series
            set_tone(self.status_label, "ok")
            self.status_label.setText(f"Sent to qBittorrent: {candidate.title}. MangaList files the volumes when "
                                      "the download has finished.")
        self._update_send()
        self.sent.emit(record)

    def _on_send_error(self, message: str) -> None:
        self._sending = False
        set_tone(self.status_label, "bad")
        self.status_label.setText(f"Could not send it: {message}")
        self._update_send()

    # --- closing ---------------------------------------------------------------------------------------

    def stop(self) -> None:
        """Abandon the calls in flight (their results are dropped)."""
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()
