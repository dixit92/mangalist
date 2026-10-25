"""The releases panel: the nyaa releases for the missing volumes of ONE series, best first, the folder they will be filed
into, and Send to qBittorrent. It is the find-volumes dialog's content as a widget: the Download tab shows it on the
right (fed by its search queue), :class:`~mangalist.gui.nyaa_dialog.NyaaDialog` shows it alone and runs the search itself.

Everything slow (the placement lookup, the nyaa search, the send) runs off the UI thread through :mod:`.background`.
The backend's rank order is kept (sorting is off); a release's ranking reasons are the small line under its name.
When the series' folder layout is ambiguous the owner picks one of the offered folders first: Send stays disabled until a
folder is chosen. Nothing is sent without a confirmation that names the series, the volumes and the target folder.

**Only the missing volumes.** Under the table a ticked box (the default comes from Settings > Download sources) says how
much of the selected pack MangaList will download: "Only the missing volumes (3 of 23 files, 410 MB of 1.7 GB)". The file
list is read from the release's ``.torrent`` off the UI thread (a short pause after the selection settles, so arrowing
through the table does not hit nyaa for every row); Send waits for it only while the box is ticked. When it cannot be
read, or no file names a missing volume, the box says so and the whole pack is what is sent. Unticking sends the whole
pack. The box is hidden for a release that has nothing to leave out, and for a backend that cannot read file lists.

**What a numberless pack holds.** A release whose title names no volumes ("Series (2019-2021) (Digital)") shows its
Fills / You have from its file list once that is read, and the line under its name says "file list: v01-v10". The
selected release is read first; the other numberless ones (at most :data:`MAX_BACKGROUND_PACKS`) are read one after
another in the background, so the table fills in without selecting each.

**Already in qBittorrent.** A line above the table names the series' torrents still in qBittorrent (downloading, or
filed and seeding) or queued under the download budget, and a release that IS one of them cannot be sent again (owner,
2026-10-09).

**The download budget** (owner, 2026-10-09; :mod:`mangalist.downloads.budget`). With a backend that keeps one
(``budget_status``), Send first works out what the release adds - the selected files' total of a partial send, else the
release's size - against the cap. Fits: the usual confirmation, with a line on the budget. Would go over the cap (or
others wait in the queue): one question that names the usage, the cap and the size and offers **Queue it** (the default),
**Send now (past the cap)** or Cancel. Bigger than the whole cap on its own: an alert that names its size and the cap and
offers **Send anyway (past the cap)** or Cancel (the default) - never silently refused, never silently sent. The send
itself tells the backend the choice (``over_cap``) and the size; the backend checks the budget again and returns a SENT
or a QUEUED record, and the status line says which.

**Sends are queued** (owner, 2026-10-15: "I shouldn't have to wait while the previous item was being sent to
qbittorrent. It should be queued."). One send runs at a time (with "Only the missing volumes" the backend adds the
torrent and waits for its file list, which can take a while); Send while one runs - for this series or another - queues
the release with exactly what was chosen at the click (the volumes, the folder, the budget answer, the box) and the
queue is worked through in order, like the Found list's. The status line is per series: switching series shows that
series' own state (sending, "Queued to send (1st)", sent, or why it failed), never another series' "Sending...". A
failed send does not stop the queue; closing the panel (:meth:`ReleasesPanel.stop`) drops what is still queued.

**Other releases** (owner, 2026-10-24: "optionally show downloads even if you don't have missing chapters in a
collapsible search result list hidden by default. It should work similarly to upgrade, where you can replace a
collection downloaded over time with an updated collection"). The search (``search_all``) also returns the releases that
fill no missing volume (``other=True``: they hold only volumes the owner has, or numbers beyond the series'). They are
listed under the results in a collapsible **Other releases (N)** section, collapsed by default, with what each
replaces and adds; with no usable release the panel says so (not "No releases found") and the section is the offer.
Selecting one shows "Choose volumes..." (every volume of its range, ticked; the owner unticks what to keep) and the
"Only the chosen volumes" box. Sending one confirms which of the owner's files it replaces - they go to the holding
folder, restorable - what it adds, its size and the budget line; the send carries ``replace_volumes`` and the filing
replaces them (:mod:`mangalist.downloads.replacing`). A series with no gaps reaches it by "Search releases..." (the List's
row menu, the To get rows): :attr:`VolumeTarget.browse`.

**A queued send can be cancelled** (owner, 2026-10-15: "Yes" to a "Cancel" link in the status line): under the status
line each of the shown series' queued sends has a **Cancel** link (:meth:`ReleasesPanel.cancel_queued`); it takes the
send off the queue - nothing was sent - and the release can be sent again. The send already running cannot be called
back (qBittorrent may have it); it is not offered.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..classifier import _human
from ..downloads.budget import (
    FITS,
    OVER_CAP_QUEUE,
    OVER_CAP_SEND,
    TOO_BIG,
    BudgetState,
    gb_text,
    ordinal,
    over_cap_text,
    place_text,
    too_big_text,
)
from ..downloads.contracts import DownloadRecord, DownloadStatus, NyaaCandidate, Placement
from ..knowledge import fmt_num, to_decimal
from ..downloads.partial import PackSelection, describe, describe_whole, file_lines
from ..services.nyaa.ranking import range_volumes
from .background import BackgroundCall, start_call
from .download_rules import download_chip, in_hand, release_why
from .download_style import FONT_MONO, set_prop, set_tone
from .download_widgets import ROLE_SUB, RadioDelegate, TwoLineDelegate, button, flat_table, hbox, label, text_width
from .downloads_backend import DownloadsBackend
from .tables import resizable_columns
from .volumes_target import VolumeTarget, numbers_text, volume_label
from .links import open_link
from .series_actions import below, menu_button

_log = logging.getLogger(__name__)

ConfirmFn = Callable[[QWidget, str], bool]
#: "Choose volumes..." of an other release: (parent, release title, [(volume, its line)], the chosen ones) -> the new
#: choice, or None (cancelled).
ChooseVolumesFn = Callable[[QWidget, str, Sequence[Tuple[str, str]], Sequence[str]], Optional[Sequence[str]]]
#: The over-the-cap question: (parent, kind, text) -> "queue" | "send" | "cancel"; kind is "over" (queue it, send it now
#: past the cap, or cancel) or "too big" (send it anyway, or cancel).
BudgetChoiceFn = Callable[[QWidget, str, str], str]
BUDGET_OVER, BUDGET_TOO_BIG = "over", "too big"
CHOICE_QUEUE, CHOICE_SEND, CHOICE_CANCEL = OVER_CAP_QUEUE, OVER_CAP_SEND, "cancel"

COLUMNS = ("", "Release", "Fills", "You have", "Published", "Source", "Size", "Seeders")
COL_PICK, COL_RELEASE, COL_FILLS, COL_HELD, COL_DATE, COL_SOURCE, COL_SIZE, COL_SEEDERS = range(len(COLUMNS))
#: The other releases' table: what each would replace of the owner's and what it adds, in the Fills / You have places.
OTHER_COLUMNS = ("", "Release", "Replaces", "Adds", "Published", "Source", "Size", "Seeders")
OTHER_TOGGLE = "Other releases ({n})"
OTHER_TIP = ("Releases that hold none of the missing volumes - only volumes you have (a newer collection, another "
             "group's): sending one replaces your copies of the volumes you choose; the old copies go to the holding "
             "folder, restorable from the Download tab")
CHOOSE_TEXT = "Choose volumes..."
MAX_LISTED_FILES = 8                # the replacement's confirmation names this many of the owner's files, then "N more"
DATE_PAD = 16                       # each side of the Published column's date
MAX_BACKGROUND_PACKS = 5            # numberless packs whose file lists are read without being selected
PAGE_MESSAGE, PAGE_RESULTS = 0, 1
ROW_HEIGHT = 48
PACK_DELAY_MS = 400                 # the selection must settle this long before a release's file list is fetched

NO_FOLDER_CHOSEN = "Choose a folder..."
FOOTER_NOTE = "MangaList links only the missing volumes into the series folder; nothing there is replaced."
UPGRADE_FOOTER_NOTE = ("MangaList links the volumes into the series folder; the chapter files they replace are then handled "
                       "as Settings > Automation says (holding folder or deleted after you confirm).")


def wanted_text(target) -> str:
    """"Missing v07", "Upgrade v23-v24" or "Missing v07 · upgrade v01-v03" (rich text, numbers in mono)."""
    upgrade = [v for v in getattr(target, "upgrade", ()) if v in target.missing]
    plain = [v for v in target.missing if v not in upgrade]
    parts = []
    if plain:
        parts.append(f"Missing {_mono(numbers_text(plain, pad=True))}")
    if upgrade:
        parts.append(f"{'upgrade' if plain else 'Upgrade'} {_mono(numbers_text(upgrade, pad=True))}")
    return " &middot; ".join(parts)
PARTIAL_TEXT = "Only the missing volumes"
PARTIAL_TIP = ("Only the files that hold a missing volume are downloaded; qBittorrent skips the rest of the pack. "
               "The torrent then seeds only what it downloaded, not the whole pack. Untick to download everything.")
PARTIAL_OTHER_TEXT = "Only the chosen volumes"


def _default_confirm(parent: QWidget, text: str) -> bool:
    return QMessageBox.question(parent, "Send to qBittorrent", text,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def _default_budget_choice(parent: QWidget, kind: str, text: str) -> str:
    """The over-the-cap question (see :data:`BudgetChoiceFn`). Queueing is the default when it is offered; for a release
    bigger than the whole cap, Cancel is."""
    if kind == BUDGET_TOO_BIG:
        box = QMessageBox(QMessageBox.Icon.Warning, "Bigger than the download budget", text, parent=parent)
        send = box.addButton("Send anyway (past the cap)", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return CHOICE_SEND if box.clickedButton() is send else CHOICE_CANCEL
    box = QMessageBox(QMessageBox.Icon.Question, "Over the download budget", text, parent=parent)
    queue = box.addButton("Queue it", QMessageBox.ButtonRole.AcceptRole)
    send = box.addButton("Send now (past the cap)", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(queue)
    box.exec()
    clicked = box.clickedButton()
    return CHOICE_QUEUE if clicked is queue else CHOICE_SEND if clicked is send else CHOICE_CANCEL


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


def release_volumes(candidate: NyaaCandidate) -> Tuple[str, ...]:
    """Every volume the release's title states (its range: whole numbers and both ends), ascending."""
    return range_volumes(candidate.vol_from, candidate.vol_to)


def split_other(candidate: NyaaCandidate, held: Sequence[str], missing: Sequence[str] = ()
                ) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(replaces, adds) of an other release: the volumes of its range the owner has, and those neither held nor
    missing (new numbers)."""
    vols = release_volumes(candidate)
    have = _among(vols, held)
    known = set(have) | set(_among(vols, missing))
    return have, tuple(v for v in vols if v not in known)


def other_line(candidate: NyaaCandidate, held: Sequence[str], missing: Sequence[str] = ()) -> str:
    """"replaces v01-v14 you have, adds nothing" - an other release against the owner's volumes."""
    replaces, adds = split_other(candidate, held, missing)
    first = f"replaces {numbers_text(replaces, pad=True)} you have" if replaces else "replaces nothing you have"
    return f"{first}, adds {numbers_text(adds, pad=True) if adds else 'nothing'}"


def search_releases(backend, target) -> List[NyaaCandidate]:
    """The releases card's search: the usable releases and the other ones (``search_all``) where the backend offers
    it, else the usable ones only."""
    every = getattr(backend, "search_all", None)
    if callable(every):
        return list(every(target.titles, target.missing, target.held))
    return list(backend.search(target.titles, target.missing, target.held))


def _default_choose_volumes(parent: QWidget, title: str, items: Sequence[Tuple[str, str]],
                            chosen: Sequence[str]) -> Optional[Sequence[str]]:
    """The volumes of an other release to download, as ticks (Cancel keeps the choice as it was)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Choose volumes")
    lay = QVBoxLayout(dlg)
    lay.addWidget(label(f"Which volumes of {title} should MangaList download? Your copies of the ticked ones go to "
                        "the holding folder when it is filed; the unticked ones stay as you have them.", wrap=True))
    box_list = QListWidget()
    box_list.setObjectName("chooseVolumesList")
    picked = set(chosen)
    for volume, text in items:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if volume in picked else Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, volume)
        box_list.addItem(item)
    lay.addWidget(box_list, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return [box_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(box_list.count())
            if box_list.item(i).checkState() == Qt.CheckState.Checked]


def pack_volumes(selection: Optional[PackSelection]) -> Tuple[str, ...]:
    """Every volume a read file list names, ascending (empty when it was not read)."""
    if selection is None or not selection.readable:
        return ()
    numbers = {d for f in selection.files for d in (to_decimal(v) for v in f.volumes) if d is not None}
    return tuple(fmt_num(d) for d in sorted(numbers))


def _among(volumes: Sequence[str], pool: Sequence[str]) -> Tuple[str, ...]:
    wanted = {to_decimal(v) for v in pool} - {None}
    return tuple(v for v in volumes if to_decimal(v) in wanted)


def contents_text(selection: Optional[PackSelection]) -> str:
    """"file list: v01-v10, 10 files" - what a numberless pack turned out to hold ("" before it is read)."""
    if selection is None or not selection.readable or not selection.files:
        return ""
    vols = numbers_text(pack_volumes(selection), pad=True)
    n = selection.total_files
    files = f"{n} file{'s' if n != 1 else ''}"
    return f"file list: {vols}, {files}" if vols else f"file list: no volume numbers in the {files}"


def date_text(published: str) -> str:
    """The day a release was published (``2021-11-09``), from nyaa's ISO time."""
    return (published or "")[:10]


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


_JOB_IDS = iter(range(1, 1 << 62))


@dataclass
class SendJob:
    """One click of Send, with everything chosen at the click: it is sent exactly so when its turn comes."""

    series_id: int
    candidate: NyaaCandidate
    wanted: Tuple[str, ...]
    target_dir: str
    extra: dict = field(default_factory=dict)       # the budget answer (size_bytes, over_cap) and only_missing
    fits: bool = True                               # False: the budget said it would be queued there ("Queueing...")
    ident: int = field(default_factory=lambda: next(_JOB_IDS))     # names it in its Cancel link

    def same(self, series_id: int, info_hash: str) -> bool:
        return self.series_id == series_id and self.candidate.info_hash == info_hash


def _mono(text: str) -> str:
    return f"<span style=\"font-family: {FONT_MONO.replace(chr(34), '')}\">{html.escape(text)}</span>"


class ReleasesPanel(QWidget):
    sent = Signal(object)               # a DownloadRecord, once qBittorrent has the release
    search_again = Signal()             # managed: "Search again" was pressed, the host runs the search
    settings_requested = Signal(str)
    series_menu_requested = Signal(QPoint)  # the "⋯" in the header: the host opens the series' menu there    # a section of the Settings dialog
    search_by_hand = Signal()           # "Search nyaa by hand" under a message (a declared edition's volumes)

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmFn] = None, open_url: Optional[Callable[[str], object]] = None,
                 managed: bool = False, source_label: str = "nyaa",
                 downloads_for: Optional[Callable[[int], Sequence[DownloadRecord]]] = None,
                 budget_choice: Optional[BudgetChoiceFn] = None, choose_volumes: Optional[ChooseVolumesFn] = None):
        super().__init__(parent)
        self.setObjectName("releasesPanel")
        self._backend = backend
        self._confirm = confirm or _default_confirm
        self.ask_budget: BudgetChoiceFn = budget_choice or _default_budget_choice     # replaceable (tests answer it)
        self.choose_volumes: ChooseVolumesFn = choose_volumes or _default_choose_volumes   # replaceable too
        self._open_url = open_url or open_link
        self.managed = managed
        self.source_label = source_label
        self.target: Optional[VolumeTarget] = None
        self._candidates: List[NyaaCandidate] = []          # every release shown: the usable ones, then the others
        self._usable: List[NyaaCandidate] = []              # ... the main table's
        self._others: List[NyaaCandidate] = []              # ... and the Other releases section's (other=True)
        self._chosen: Dict[str, Tuple[str, ...]] = {}       # an other release's volumes the owner chose, by info hash
        self._others_open = False                           # the section is collapsed by default
        self._open_others_next: Optional[int] = None        # "Search releases...": open it with this series' results
        self._switching = False                             # one selection across the two tables
        self._other_fitted = False
        self._placement: Optional[Placement] = None
        self._placement_error: Optional[str] = None
        self._search_state = "idle"          # idle | searching | done | error
        self._calls: List[BackgroundCall] = []
        self._sending: Optional[SendJob] = None              # the send running now
        self._send_queue: List[SendJob] = []                # ... and the ones clicked meanwhile, in order
        self._send_outcomes: Dict[int, Tuple[str, str]] = {}  # series id -> its last send's (text, tone)
        self._sent_hashes = set()
        self._downloads_for = downloads_for                 # series id -> its download records (the host's)
        self._background: List[str] = []                   # numberless packs still to read, by info hash
        self.sent_records: List[DownloadRecord] = []
        self._settings_section: Optional[str] = None
        self._fitted = False
        self._packs: Dict[str, PackSelection] = {}       # file lists read for this target, by info hash
        self._pack_failed: Dict[str, str] = {}           # ... and why one could not be read
        self._pack_reading: set = set()
        self._partial_on = True                          # the box: ticked unless the owner unticked it for this release
        self._pack_timer = QTimer(self)
        self._pack_timer.setSingleShot(True)
        self._pack_timer.timeout.connect(self._read_pack)
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
        self.downloads_label = label("", "muted", wrap=True)
        self.downloads_label.setTextFormat(Qt.TextFormat.PlainText)
        self.downloads_label.setVisible(False)
        titles.addWidget(self.downloads_label)
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
        self.btn_series = menu_button()             # the series' menu (series_actions.py); the host shows it
        self.btn_series.clicked.connect(lambda: self.series_menu_requested.emit(below(self.btn_series)))
        self.btn_series.setVisible(False)
        head.addWidget(self.btn_series)
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
        self.message_search = button("Search nyaa by hand", primary=True,
                                     tip="Search nyaa for these volumes now; the releases are shown as usual and you "
                                         "decide - never searched automatically")
        self.message_search.setObjectName("searchByHand")
        self.message_search.clicked.connect(self.search_by_hand)
        self.message_search.setVisible(False)
        mv.addWidget(self.message_title)
        mv.addWidget(self.message_text)
        mv.addLayout(hbox(self.message_action, self.message_search, None))
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
        resizable_columns(self.table, {COL_PICK: 36, COL_RELEASE: 420, COL_FILLS: 110, COL_HELD: 110, COL_DATE: 100,
                                       COL_SOURCE: 84, COL_SIZE: 90, COL_SEEDERS: 80})        # every column can be dragged
        for col in (COL_SIZE, COL_SEEDERS):
            self.table.horizontalHeaderItem(col).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.itemSelectionChanged.connect(self._on_main_selection)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected_page())
        cv.addWidget(self.table)
        # With no usable release but other ones: what was found, instead of "No releases found" (owner, 2026-10-24)
        self.results_note = label("", "lead", wrap=True, selectable=True)
        self.results_note.setObjectName("otherReleasesNote")
        self.results_note.setVisible(False)
        rp.addWidget(self.results_note)
        rp.addWidget(self.results_card)
        # Other releases (N): collapsed by default
        self.other_toggle = button("", link=True, tip=OTHER_TIP)
        self.other_toggle.setObjectName("otherReleasesToggle")
        self.other_toggle.setCheckable(True)
        self.other_toggle.toggled.connect(self.set_others_open)
        self.other_toggle.setVisible(False)
        rp.addSpacing(4)
        rp.addLayout(hbox(self.other_toggle, None))
        self.other_card = QFrame()
        self.other_card.setObjectName("releasesCard")
        ov = QVBoxLayout(self.other_card)
        ov.setContentsMargins(0, 0, 0, 0)
        self.other_table = flat_table("otherReleasesTable", OTHER_COLUMNS)
        self.other_table.setSortingEnabled(False)
        self.other_table.setItemDelegateForColumn(COL_PICK, RadioDelegate(self.other_table))
        self.other_table.setItemDelegateForColumn(COL_RELEASE, TwoLineDelegate(self.other_table, row_height=ROW_HEIGHT))
        self.other_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        resizable_columns(self.other_table, {COL_PICK: 36, COL_RELEASE: 420, COL_FILLS: 110, COL_HELD: 110,
                                             COL_DATE: 100, COL_SOURCE: 84, COL_SIZE: 90, COL_SEEDERS: 80})
        for col in (COL_SIZE, COL_SEEDERS):
            self.other_table.horizontalHeaderItem(col).setTextAlignment(Qt.AlignmentFlag.AlignRight
                                                                        | Qt.AlignmentFlag.AlignVCenter)
        self.other_table.itemSelectionChanged.connect(self._on_other_selection)
        self.other_table.itemDoubleClicked.connect(lambda *_: self.open_selected_page())
        ov.addWidget(self.other_table)
        self.other_card.setVisible(False)
        rp.addWidget(self.other_card)
        self.other_choice = QWidget()
        self.other_line = label("", "muted", wrap=True)
        self.other_line.setObjectName("otherReleaseLine")
        self.btn_choose = button(CHOOSE_TEXT, tip="Pick which of the release's volumes to download (and so which of "
                                                  "your copies it replaces)")
        self.btn_choose.setObjectName("chooseVolumes")
        self.btn_choose.clicked.connect(self._on_choose)
        cl = hbox(self.other_line, self.btn_choose, spacing=12)
        cl.setStretch(0, 1)
        self.other_choice.setLayout(cl)
        self.other_choice.setVisible(False)
        rp.addWidget(self.other_choice)
        self.partial_row = QWidget()
        pr = QVBoxLayout(self.partial_row)
        pr.setContentsMargins(0, 8, 0, 0)
        pr.setSpacing(2)
        self.partial_check = QCheckBox(PARTIAL_TEXT)
        self.partial_check.setChecked(True)
        self.partial_check.setToolTip(PARTIAL_TIP)
        self.partial_check.toggled.connect(self._on_partial_toggled)
        self.partial_note = label("", "muted", wrap=True)
        pr.addWidget(self.partial_check)
        pr.addWidget(self.partial_note)
        self.partial_row.setVisible(False)
        rp.addWidget(self.partial_row)
        self.status_label = label("", wrap=True, selectable=True)
        # The shown series' queued sends, each with a Cancel link (cancel_queued).
        self.cancel_label = label("", "muted", wrap=True)
        self.cancel_label.setObjectName("queuedCancel")
        self.cancel_label.setTextFormat(Qt.TextFormat.RichText)
        self.cancel_label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                                  | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.cancel_label.setToolTip("Take a queued send off the queue - nothing was sent; you can send it again")
        self.cancel_label.linkActivated.connect(self._on_cancel_link)
        self.cancel_label.setVisible(False)
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
        rp.addWidget(self.cancel_label)
        rp.addStretch(1)
        self.stack.addWidget(self.results_page)
        outer.addWidget(self.stack, 1)
        self.show_message("", "Select a series to see its releases.")

    def _fit_release_column(self) -> None:
        """Give the Release column the width the others leave (once: after that the owner's drag stands)."""
        if self._fitted or not self.table.isVisible():
            return
        date_w = text_width(QFontMetricsF(self.table.font()), "0000-00-00") + 2 * DATE_PAD
        if self.table.columnWidth(COL_DATE) < date_w:           # the cell font as styled: a full date, never "2021-11-..."
            self.table.setColumnWidth(COL_DATE, date_w)
        others = sum(self.table.columnWidth(c) for c in range(self.table.columnCount()) if c != COL_RELEASE)
        room = self.table.viewport().width() - others
        if room > 200:
            self.table.setColumnWidth(COL_RELEASE, room)
            self._fitted = True

    def _fit_other_release_column(self) -> None:
        """The other releases' table: the same fit, once, when it first shows."""
        table = self.other_table
        if self._other_fitted or not table.isVisible():
            return
        table.setColumnWidth(COL_DATE, max(table.columnWidth(COL_DATE), self.table.columnWidth(COL_DATE)))
        others = sum(table.columnWidth(c) for c in range(table.columnCount()) if c != COL_RELEASE)
        room = table.viewport().width() - others
        if room > 200:
            table.setColumnWidth(COL_RELEASE, room)
            self._other_fitted = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_release_column)

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
        self.message_search.setVisible(False)
        self.search_label.setText("")
        self.stack.setCurrentIndex(PAGE_MESSAGE)
        self._set_chrome(False)
        self._update_send()

    def offer_search_by_hand(self, on: bool = True) -> None:
        """Under the message: "Search nyaa by hand" (:attr:`search_by_hand`) - for volumes never searched automatically
        (a declared edition's), which the owner may still look up and judge."""
        self.message_search.setVisible(on)

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
        self._pack_timer.stop()
        self._packs, self._pack_failed, self._pack_reading = {}, {}, set()
        self._background = []
        self._candidates = []
        self._usable, self._others, self._chosen = [], [], {}
        self._placement = None
        self._placement_error = None
        self._search_state = "idle"
        self.table.setRowCount(0)
        self.other_table.setRowCount(0)
        self._show_others(0)
        self._partial_on = self._partial_default()
        self._show_pack()
        self.progress.setVisible(False)
        self.btn_retry.setEnabled(target is not None)
        self.folder_row.setVisible(False)
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.blockSignals(False)
        self._show_send_status()                         # this series' own sends (none: an empty line)

    def _begin_target_view(self) -> None:
        target = self.target
        assert target is not None
        self.title_label.setText(target.title)
        self.message_title.setVisible(False)
        self.message_action.setVisible(False)
        self.message_search.setVisible(False)
        self._set_chrome(True)
        self.btn_retry.setEnabled(True)
        self._update_subtitle()
        self.refresh_downloads()

    def _update_subtitle(self) -> None:
        target = self.target
        if target is None:
            self.subtitle_label.setText("")
            return
        if target.missing:
            missing = wanted_text(target)
        elif getattr(target, "browse", False):
            missing = "Nothing missing - every release on nyaa is compared with the volumes you have"
        else:
            missing = "Missing volumes not known - releases are compared with the volumes you have"
        self.note_label.setText(UPGRADE_FOOTER_NOTE if getattr(target, "upgrade", ()) else FOOTER_NOTE)
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
        if self._others_open:                       # Search again keeps the Other releases section as it was
            self._open_others_next = target.series_id
        backend = self._backend
        self._spawn(lambda: search_releases(backend, target), self._on_results, self._on_search_error)

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
            missing = wanted_text(target) if target and target.missing else ""
            self.subtitle_label.setText(f"{missing} &middot; files go to {_mono(folder_text(chosen, placement.series_dir))}")

    def _on_results(self, candidates: Sequence[NyaaCandidate]) -> None:
        self._search_state = "done"
        self.progress.setVisible(False)
        self.btn_retry.setEnabled(True)
        self._usable = [c for c in candidates if not getattr(c, "other", False)]
        self._others = [c for c in candidates if getattr(c, "other", False)]
        self._candidates = self._usable + self._others
        self._chosen = {c.info_hash: release_volumes(c) for c in self._others}
        self.table.setRowCount(len(self._usable))
        for row, candidate in enumerate(self._usable):
            self._fill_row(row, candidate)
        self._fit_rows(self.table, len(self._usable))
        self.other_table.setRowCount(len(self._others))
        for row, candidate in enumerate(self._others):
            self._fill_row(row, candidate, self.other_table)
        self._fit_rows(self.other_table, len(self._others))
        n, k = len(self._usable), len(self._others)
        self.search_label.setText(f"{self.source_label} · {n} release{'s' if n != 1 else ''}"
                                  + (f" · {k} other" if k else ""))
        opened = self.target is not None and self._open_others_next == self.target.series_id
        self._open_others_next = None
        if not self._candidates:
            self.message_title.setVisible(False)
            self.message_text.setText("No releases found on nyaa for this series (with seeders, English).")
            self.stack.setCurrentIndex(PAGE_MESSAGE)
        else:
            self.stack.setCurrentIndex(PAGE_RESULTS)
            self.results_card.setVisible(bool(self._usable))
            self.results_note.setText(self._none_usable_text() if not self._usable else "")
            self.results_note.setVisible(not self._usable)
            self._show_others(k, opened)
            self._background = [c.info_hash for c in self._usable if c.vol_from is None][:MAX_BACKGROUND_PACKS]
            if self._usable:
                self.table.selectRow(0)
            QTimer.singleShot(0, self._fit_release_column)
        self._update_send()

    def _none_usable_text(self) -> str:
        """The note above the other releases when no release holds a missing volume (never "No releases found")."""
        k = len(self._others)
        target = self.target
        what = ("a missing volume" if target is not None and target.missing else "a volume you do not have")
        them = "it" if k == 1 else "them"
        return (f"No release on nyaa holds {what} (with seeders, English). {k} other release{'s' if k != 1 else ''} "
                f"hold{'s' if k == 1 else ''} only volumes you have - {'it' if k == 1 else 'each'} can replace your "
                f"copies (a newer collection, another group's). Open Other releases below to see {them}.")

    @staticmethod
    def _fit_rows(table, rows: int) -> None:
        head = table.horizontalHeader().height()
        table.setMinimumHeight(head + ROW_HEIGHT * min(rows, 3) + 2)
        table.setMaximumHeight(head + ROW_HEIGHT * rows + 2)      # the card fits its rows

    def _fill_row(self, row: int, candidate: NyaaCandidate, table=None) -> None:
        table = table if table is not None else self.table
        if table is self.other_table:
            fills, held, why = self._other_cells(candidate)
        else:
            fills, held, why = self._covers(candidate)
        values = ("", candidate.title, fills, held, date_text(candidate.published), "nyaa",
                  _human(candidate.size_bytes), str(candidate.seeders))
        tip = self._tooltip(candidate)
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setToolTip(tip)
            if col == COL_RELEASE:
                item.setData(ROLE_SUB, why)
            if col in (COL_SEEDERS, COL_SIZE):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, col, item)

    def _other_cells(self, candidate: NyaaCandidate) -> Tuple[str, str, str]:
        """(Replaces, Adds, the line under the name) of an other release: its range against the volumes held - the
        chosen volumes only, once the owner chose."""
        target = self.target
        held, missing = (target.held, target.missing) if target is not None else ((), ())
        replaces, adds = split_other(candidate, held, missing)
        chosen = set(self._chosen.get(candidate.info_hash, release_volumes(candidate)))
        replaces = tuple(v for v in replaces if v in chosen)
        adds = tuple(v for v in adds if v in chosen)
        parts = [other_line(candidate, held, missing)]
        if len(chosen) < len(release_volumes(candidate)):
            parts.append(f"chosen: {numbers_text(sorted(chosen, key=to_decimal), pad=True) or 'none'}")
        newer = self._newer_note(candidate)
        if newer:
            parts.append(newer)
        parts += [r for r in candidate.reasons if "you already have" not in r]
        return numbers_text(replaces, pad=True) or "-", numbers_text(adds, pad=True) or "-", ", ".join(parts)

    def _newer_note(self, candidate: NyaaCandidate) -> str:
        """"published after your last download of this series (2026-05-01)" ('' when not, or no download is known)."""
        dates = [r.created_at[:10] for r in self._series_downloads()
                 if r.created_at and r.status in (DownloadStatus.FILED, DownloadStatus.REMOVED)]
        published = date_text(candidate.published)
        if not dates or not published or published <= max(dates):
            return ""
        return f"published after your last download of this series ({max(dates)})"

    def _covers(self, candidate: NyaaCandidate) -> Tuple[str, str, str]:
        """(Fills, You have, the line under the name): from the title's numbers, else from the file list once read
        ("?" before)."""
        why = release_why(candidate)
        if candidate.vol_from is not None:
            return (numbers_text(candidate.covers_missing, pad=True) or "-",
                    numbers_text(candidate.covers_held, pad=True) or "-", why)
        selection = self._packs.get(candidate.info_hash)
        contents = contents_text(selection)
        if not contents or self.target is None:
            return "?", "?", why
        vols = pack_volumes(selection)
        why = why.replace("pack without volume numbers: contents unknown", contents) if "contents unknown" in why \
            else f"{contents}, {why}"
        return (numbers_text(_among(vols, self.target.missing), pad=True) or "-",
                numbers_text(_among(vols, self.target.held), pad=True) or "-", why)

    def _refill(self, key: str) -> None:
        for table, shown in ((self.table, self._usable), (self.other_table, self._others)):
            for row, candidate in enumerate(shown):
                if candidate.info_hash == key and table.item(row, COL_RELEASE) is not None:
                    self._fill_row(row, candidate, table)

    # --- the other releases --------------------------------------------------------------------------------

    def _show_others(self, n: int, opened: bool = False) -> None:
        """The Other releases (N) toggle (hidden without other releases) and its section, open or not."""
        self.other_toggle.setVisible(n > 0)
        self._others_open = bool(opened and n > 0)
        self.other_toggle.blockSignals(True)
        self.other_toggle.setChecked(self._others_open)
        self.other_toggle.blockSignals(False)
        self._sync_other_toggle(n)
        self.other_card.setVisible(self._others_open)
        self.other_choice.setVisible(False)
        if self._others_open:
            QTimer.singleShot(0, self._fit_other_release_column)

    def _sync_other_toggle(self, n: Optional[int] = None) -> None:
        n = len(self._others) if n is None else n
        arrow = "\u25be" if self._others_open else "\u25b8"
        self.other_toggle.setText(f"{arrow} {OTHER_TOGGLE.format(n=n)}")
        self.other_toggle.setAccessibleName(OTHER_TOGGLE.format(n=n))

    def others_open(self) -> bool:
        """Whether the Other releases section is open (it is collapsed by default)."""
        return self._others_open

    def set_others_open(self, on: bool) -> None:
        """Open or collapse the Other releases section; collapsing it drops a selection there (the first usable
        release is selected again)."""
        on = bool(on) and bool(self._others)
        self._others_open = on
        self.other_toggle.blockSignals(True)
        self.other_toggle.setChecked(on)
        self.other_toggle.blockSignals(False)
        self._sync_other_toggle()
        self.other_card.setVisible(on)
        if on:
            QTimer.singleShot(0, self._fit_other_release_column)
        elif self.other_table.selectionModel() is not None and self.other_table.selectionModel().hasSelection():
            self.other_table.clearSelection()
            if self._usable:
                self.table.selectRow(0)
        self._update_other_line()
        self._update_send()

    def open_others_next(self, series_id: int) -> None:
        """Open the Other releases section when the next results of *series_id* show ("Search releases...")."""
        self._open_others_next = int(series_id)

    def is_other(self, candidate: Optional[NyaaCandidate]) -> bool:
        return candidate is not None and any(c is candidate or c.info_hash == candidate.info_hash for c in self._others)

    def chosen_volumes(self, candidate: NyaaCandidate) -> Tuple[str, ...]:
        """The volumes of an other release the owner chose to download (all of its range unless changed)."""
        return tuple(self._chosen.get(candidate.info_hash, release_volumes(candidate)))

    def set_chosen(self, candidate: NyaaCandidate, volumes: Sequence[str]) -> None:
        """Change an other release's chosen volumes: its file list is looked at again for them."""
        allowed = release_volumes(candidate)
        picked = {to_decimal(v) for v in volumes} - {None}
        self._chosen[candidate.info_hash] = tuple(v for v in allowed if to_decimal(v) in picked)
        key = candidate.info_hash
        self._packs.pop(key, None)
        self._pack_failed.pop(key, None)
        self._refill(key)
        if (self._chosen[key] and self.selected_candidate() is not None and self.selected_candidate().info_hash == key
                and self._can_inspect()):
            self._pack_timer.start(PACK_DELAY_MS)
        self._show_pack()
        self._update_other_line()
        self._update_send()

    def replace_volumes_for(self, candidate: NyaaCandidate) -> Tuple[str, ...]:
        """Of an other release's chosen volumes, those the owner has: their files are replaced when it is filed."""
        held = self.target.held if self.target is not None else ()
        return _among(self.chosen_volumes(candidate), held)

    def _choose_items(self, candidate: NyaaCandidate) -> List[Tuple[str, str]]:
        held = self.target.held if self.target is not None else ()
        have = set(_among(release_volumes(candidate), held))
        return [(v, f"{volume_label(v)} - replaces yours" if v in have else f"{volume_label(v)} - new")
                for v in release_volumes(candidate)]

    def _on_choose(self) -> None:
        candidate = self.selected_candidate()
        if candidate is None or not self.is_other(candidate):
            return
        picked = self.choose_volumes(self, candidate.title, self._choose_items(candidate),
                                     self.chosen_volumes(candidate))
        if picked is not None:
            self.set_chosen(candidate, picked)

    def _update_other_line(self) -> None:
        """Under the other releases: what the selected one replaces and adds, and "Choose volumes..."."""
        candidate = self.selected_candidate()
        if candidate is None or not self.is_other(candidate) or not self._others_open:
            self.other_choice.setVisible(False)
            return
        replace = self.replace_volumes_for(candidate)
        chosen = self.chosen_volumes(candidate)
        adds = tuple(v for v in chosen if v not in replace)
        text = (f"Replaces {numbers_text(replace, pad=True)} - your copies go to the holding folder (restorable)"
                if replace else "Replaces none of your copies")
        text += f" · adds {numbers_text(adds, pad=True) if adds else 'nothing'}"
        if not chosen:
            text = "No volume chosen: choose at least one to send this release."
        self.other_line.setText(text)
        self.other_choice.setVisible(True)

    # --- the series' torrents already in qBittorrent ------------------------------------------------------

    def _series_downloads(self) -> List[DownloadRecord]:
        if self.target is None:
            return []
        sid = self.target.series_id
        records = [r for r in (self._downloads_for(sid) if self._downloads_for else ()) if r.series_id == sid]
        known = {r.id for r in records}
        records += [r for r in self.sent_records if r.series_id == sid and r.id not in known]
        return records

    def refresh_downloads(self) -> None:
        """Say which of the series' torrents are in qBittorrent or queued (the host calls this when its records
        reload)."""
        live = in_hand(self._series_downloads())
        if not live:
            self.downloads_label.setVisible(False)
            self.downloads_label.setText("")
        else:
            lines = [f"{download_chip(r)[0]}: {r.title}" for r in live[:3]]
            if len(live) > 3:
                lines.append(f"... and {len(live) - 3} more")
            queued = any(r.status == DownloadStatus.QUEUED for r in live)
            head = "Already queued or in qBittorrent - " if queued else "Already in qBittorrent - "
            self.downloads_label.setText(head + "\n".join(lines))
            self.downloads_label.setVisible(True)
        self._update_send()

    def _in_qbittorrent(self, candidate: NyaaCandidate) -> Optional[DownloadRecord]:
        """The series' record that has this release in hand: in qBittorrent, or queued."""
        key = (candidate.info_hash or "").lower()
        return next((r for r in in_hand(self._series_downloads()) if key and r.info_hash.lower() == key), None)

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
        for table, shown in ((self.table, self._usable), (self.other_table, self._others)):
            rows = table.selectionModel().selectedRows() if table.selectionModel() else []
            if rows:
                row = rows[0].row()
                return shown[row] if 0 <= row < len(shown) else None
        return None

    def _on_main_selection(self) -> None:
        self._one_selection(self.table, self.other_table)

    def _on_other_selection(self) -> None:
        self._one_selection(self.other_table, self.table)

    def _one_selection(self, chosen, other) -> None:
        """One release is selected across the two tables: a pick in one clears the other."""
        if self._switching:
            return
        model = chosen.selectionModel()
        if model is not None and model.hasSelection():
            self._switching = True
            try:
                other.clearSelection()
            finally:
                self._switching = False
        self._on_selection_changed()
        self._update_other_line()

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
        if self._placement is None:
            return ("The target folder could not be worked out." if self._placement_error
                    else "Still looking up the target folder.")
        if self._placement.ambiguous and self.chosen_target_dir() is None:
            return "Choose the target folder first."
        candidate = self.selected_candidate()
        if candidate is None:
            return "Select a release."
        sid = self.target.series_id
        if self._sending is not None and self._sending.same(sid, candidate.info_hash):
            return "This release is being sent to qBittorrent now."
        if any(job.same(sid, candidate.info_hash) for job in self._send_queue):
            return "This release is already queued to send; it is not queued twice."
        existing = self._in_qbittorrent(candidate)
        if candidate.info_hash in self._sent_hashes and (existing is None or existing.status != DownloadStatus.QUEUED):
            return "This release was already sent."
        if existing is not None:
            where = "queued" if existing.status == DownloadStatus.QUEUED else "in qBittorrent"
            return f"This release is already {where} ({download_chip(existing)[0]}); it is not added twice."
        if self.is_other(candidate) and not self.chosen_volumes(candidate):
            return "Choose at least one volume of this release to download."
        refused = self._packs.get(candidate.info_hash)
        if refused is not None and getattr(refused, "refused", False):
            return f"Not a volume release: {refused.problem}. It is not sent."
        if self._partial_on and self.pack_state() in ("waiting", "reading"):
            return "Reading the release's file list... (or untick the box to send the whole pack)"
        if self.is_other(candidate):
            return None
        if not wanted_volumes_for(candidate, self.target.missing):
            if candidate.vol_from is None:
                return "The title does not say which volumes this release holds, and the missing volumes are not known."
            return ("This release holds none of the missing volumes." if self.target.missing
                    else "This release holds only volumes you already have.")
        return None

    def _update_send(self, *_args) -> None:
        blocker = self.send_blocker()
        self.btn_send.setEnabled(blocker is None)
        busy = ("Queue the selected release: MangaList sends it to qBittorrent after the send before it"
                if self._sending is not None else "Add the selected release to qBittorrent")
        self.btn_send.setToolTip(blocker or busy)
        self.btn_page.setEnabled(self.selected_candidate() is not None)

    # --- only the missing volumes ------------------------------------------------------------------------

    def _can_inspect(self) -> bool:
        return callable(getattr(self._backend, "inspect_pack", None))

    def _partial_default(self) -> bool:
        getter = getattr(self._backend, "partial_default", None)
        try:
            return bool(getter()) if callable(getter) else True
        except Exception:  # noqa: BLE001 - the box starts ticked, the documented default
            return True

    def wanted_for(self, candidate: NyaaCandidate) -> Tuple[str, ...]:
        """The volumes a send of *candidate* is for: an other release's chosen ones, else the missing ones it holds."""
        if self.is_other(candidate):
            return self.chosen_volumes(candidate)
        return tuple(wanted_volumes_for(candidate, self.target.missing)) if self.target is not None else ()

    def _pack_wanted(self) -> Sequence[str]:
        candidate = self.selected_candidate()
        return self.wanted_for(candidate) if candidate and self.target else ()

    def pack_selection(self) -> Optional[PackSelection]:
        """The file list read for the selected release (None until it has been read; a selection with ``problem`` set
        when it could not be)."""
        candidate = self.selected_candidate()
        return self._packs.get(candidate.info_hash) if candidate is not None else None

    def pack_state(self) -> str:
        """``none`` (nothing to show), ``waiting`` / ``reading`` (a file list is on its way), ``failed`` (not readable),
        ``whole`` (readable, but nothing to leave out), ``partial`` (some files can be left out), ``refused`` (its files
        are chapters or e-books / audio: never sent for volumes)."""
        candidate = self.selected_candidate()
        if candidate is None or self.target is None or not self._can_inspect():
            return "none"
        if self.is_other(candidate) and not self.chosen_volumes(candidate):
            return "none"                                   # nothing chosen: no file list to read
        sel = self._packs.get(candidate.info_hash)
        if sel is None:
            if candidate.info_hash in self._pack_failed:
                return "failed"
            return "reading" if candidate.info_hash in self._pack_reading else "waiting"
        if getattr(sel, "refused", False):
            return "refused"
        if not sel.readable:
            return "failed"
        return "partial" if sel.narrows else "whole"

    def partial_requested(self) -> bool:
        """True when this send downloads only the missing volumes: the box is ticked and there is something to leave out."""
        return self._partial_on and self.pack_state() == "partial"

    def _on_selection_changed(self) -> None:
        self._partial_on = self._partial_default()          # "untick per send": every release starts from the default
        self._pack_timer.stop()
        candidate = self.selected_candidate()
        if candidate is not None and candidate.info_hash in self._pack_failed:      # selecting it again tries again
            self._pack_failed.pop(candidate.info_hash, None)
            self._packs.pop(candidate.info_hash, None)
        if (candidate is not None and self._can_inspect() and candidate.info_hash not in self._packs
                and candidate.info_hash not in self._pack_reading):
            self._pack_timer.start(PACK_DELAY_MS)
        self._show_pack()
        self._update_send()

    def _read_pack(self) -> None:
        self._read_pack_of(self.selected_candidate())

    def _read_next_background(self) -> None:
        """Read the next numberless pack's file list - one at a time, and never while another read is on its way."""
        if self._pack_reading or not self._can_inspect():
            return
        while self._background:
            key = self._background.pop(0)
            if key in self._packs:
                continue
            candidate = next((c for c in self._candidates if c.info_hash == key), None)
            if candidate is not None:
                self._read_pack_of(candidate)
                return

    def _read_pack_of(self, candidate: Optional[NyaaCandidate]) -> None:
        target = self.target
        if candidate is None or target is None or not self._can_inspect():
            return
        key, wanted, series_id = candidate.info_hash, self.wanted_for(candidate), target.series_id
        if key in self._packs or key in self._pack_reading:
            return
        self._pack_reading.add(key)
        self._show_pack()
        self._update_send()
        self._spawn(lambda: self._backend.inspect_pack(candidate, wanted),
                    lambda sel, k=key, sid=series_id: self._on_pack(k, sid, sel),
                    lambda message, k=key, sid=series_id: self._on_pack_error(k, sid, message))

    def _same_target(self, series_id: int) -> bool:
        return self.target is not None and self.target.series_id == series_id

    def _on_pack(self, key: str, series_id: int, selection: PackSelection) -> None:
        if not self._same_target(series_id):
            return                                          # the owner moved to another series meanwhile
        self._pack_reading.discard(key)
        self._packs[key] = selection
        if not selection.readable:                          # selecting the release again tries again
            self._pack_failed[key] = selection.problem or "the file list is not available"
        self._refill(key)
        self._show_pack()
        self._update_send()
        self._after_read()

    def _on_pack_error(self, key: str, series_id: int, message: str) -> None:
        if not self._same_target(series_id):
            return
        self._pack_reading.discard(key)
        self._packs[key] = PackSelection(problem=message)
        self._pack_failed[key] = message
        self._show_pack()
        self._update_send()
        self._after_read()

    def _after_read(self) -> None:
        """The selected release first (its timer may be waiting), then the next numberless pack."""
        if not self._pack_timer.isActive():
            QTimer.singleShot(0, self._read_next_background)

    def _on_partial_toggled(self, on: bool) -> None:
        self._partial_on = bool(on)
        self._show_pack()
        self._update_send()

    def _show_pack(self) -> None:
        """Set the box and its line for the selected release."""
        state = self.pack_state()
        sel = self.pack_selection()
        self.partial_row.setVisible(state in ("waiting", "reading", "failed", "partial", "refused")
                                    or (state == "whole" and bool(sel and (sel.whole_reason or sel.unknown_kept))))
        self.partial_check.blockSignals(True)
        tone, note, tip = "", "", ""
        base = PARTIAL_OTHER_TEXT if self.is_other(self.selected_candidate()) else PARTIAL_TEXT
        text, checked, enabled = base, self._partial_on, False
        if state in ("waiting", "reading"):
            enabled = True                                  # unticking now sends the whole pack without waiting
            note = "Reading the release's file list..."
        elif state == "refused" and sel is not None:
            checked = False
            note, tone = f"Not a volume release: {sel.problem}. It is not sent.", "bad"
            tip = file_lines(sel)
        elif state == "failed":
            checked = False
            reason = (sel.problem if sel is not None and sel.problem else None) or self._failed_reason()
            note, tone = f"The file list could not be read ({reason}). The whole pack will be downloaded.", "warn"
        elif state == "whole" and sel is not None:
            checked = False
            if sel.whole_reason:
                note, tone = f"{sel.whole_reason[:1].upper()}{sel.whole_reason[1:]}.", "warn"
            else:
                note = f"{sel.unknown_kept} file name{'s do' if sel.unknown_kept != 1 else ' does'} not say a volume, so all {sel.total_files} files are kept."
                tone = "warn"
            tip = file_lines(sel)
        elif state == "partial" and sel is not None:
            enabled = True
            text = f"{base} ({describe(sel)})"
            tip = file_lines(sel)
            bits = []
            if self._partial_on:
                bits.append("The torrent seeds only what it downloaded.")
                if sel.unknown_kept:
                    bits.append(f"{sel.unknown_kept} file{'s' if sel.unknown_kept != 1 else ''} kept because "
                                f"{'their names do' if sel.unknown_kept != 1 else 'its name does'} not say a volume.")
                if sel.not_found and not sel.unknown_kept:
                    bits.append(f"Not in this pack: {numbers_text(sel.not_found, pad=True)}.")
            else:
                bits.append(f"The whole pack will be downloaded ({describe_whole(sel)}).")
            note = " ".join(bits)
        self.partial_check.setText(text)
        self.partial_check.setChecked(bool(checked))
        self.partial_check.setEnabled(enabled)
        self.partial_check.blockSignals(False)
        self.partial_note.setText(note)
        self.partial_note.setToolTip(tip)
        set_prop(self.partial_note, "role", "" if tone else "muted")         # the muted colour would hide the tone
        set_tone(self.partial_note, tone)

    def _failed_reason(self) -> str:
        candidate = self.selected_candidate()
        return self._pack_failed.get(candidate.info_hash, "") if candidate is not None else ""

    def _pack_line(self, candidate: NyaaCandidate) -> str:
        """The confirmation's line about what is downloaded ('' when the backend does not read file lists)."""
        if not self._can_inspect():
            return ""
        sel = self._packs.get(candidate.info_hash)
        if self.partial_requested() and sel is not None:
            what = "the chosen volumes" if self.is_other(candidate) else "the missing volumes"
            return f"Download: only {what} ({describe(sel)})\n"
        if sel is not None and sel.readable:
            return f"Download: the whole pack ({describe_whole(sel)})\n"
        if sel is None:
            return "Download: the whole pack\n"                  # unticked before the list was read
        return "Download: the whole pack (its file list could not be read)\n"

    # --- actions ---------------------------------------------------------------------------------------

    def open_selected_page(self) -> bool:
        candidate = self.selected_candidate()
        if candidate is None or not candidate.view_url.lower().startswith(("http://", "https://")):
            return False
        self._open_url(candidate.view_url)
        return True

    def confirmation_text(self, candidate: NyaaCandidate, wanted: Sequence[str], target_dir: str,
                          budget_line: str = "") -> str:
        if self.is_other(candidate):
            return self.replacement_text(candidate, wanted, target_dir, budget_line)
        title = self.target.title if self.target else ""
        if candidate.vol_from is None:
            vols = (f"{numbers_text(wanted, pad=True)} (the release's title does not say which volumes it holds; "
                    "MangaList files whichever of these it contains)")
        else:
            vols = numbers_text(wanted, pad=True)
        return (f"Send this release to qBittorrent?\n\n{candidate.title}\n\n"
                f"Series: {title}\nVolumes: {vols}\n{self._pack_line(candidate)}Target folder: {target_dir}\n"
                f"{budget_line}\n"
                "qBittorrent downloads it; MangaList files these volumes into the target folder when it has finished.")

    def replacement_text(self, candidate: NyaaCandidate, wanted: Sequence[str], target_dir: str,
                         budget_line: str = "") -> str:
        """The confirmation of an other release: which of the owner's files it replaces (to the holding folder,
        restorable), what it adds, the size, the folder and the budget line."""
        title = self.target.title if self.target else ""
        replace = self.replace_volumes_for(candidate)
        adds = tuple(v for v in wanted if v not in replace)
        lines = [f"Replace your copies with this release?\n\n{candidate.title}\n", f"Series: {title}"]
        if replace:
            lines.append(f"Replaces: {numbers_text(replace, pad=True)} - your copies go to the holding folder "
                         "(restorable from the Download tab):")
            files, kept = self._replaced_files(replace)
            lines += [f"  {rel}" for rel in files[:MAX_LISTED_FILES]]
            if len(files) > MAX_LISTED_FILES:
                lines.append(f"  ... and {len(files) - MAX_LISTED_FILES} more")
            if kept:
                lines.append("Kept (they hold more than the chosen volumes, so the release's file for them is not "
                             f"filed): {', '.join(kept[:MAX_LISTED_FILES])}"
                             + (f" and {len(kept) - MAX_LISTED_FILES} more" if len(kept) > MAX_LISTED_FILES else ""))
        else:
            lines.append("Replaces: none of your copies")
        lines.append(f"Adds: {numbers_text(adds, pad=True) if adds else 'nothing'}")
        lines.append(f"{self._pack_line(candidate)}Size: {_human(self.send_size(candidate))}")
        lines.append(f"Target folder: {target_dir}")
        if budget_line:
            lines.append(budget_line.rstrip("\n"))
        lines.append("\nqBittorrent downloads it; when it has finished, MangaList moves your copies of these volumes to "
                     "the holding folder and files the release's in their place. Your other files stay as they are.")
        return "\n".join(lines)

    def _replaced_files(self, volumes: Sequence[str]) -> Tuple[List[str], List[str]]:
        """(the owner's files a replacement of *volumes* moves to the holding folder, files that hold one of them but
        stay) from the backend's ``replacement_preview`` (([], []) without one, or when it cannot say)."""
        preview = getattr(self._backend, "replacement_preview", None)
        if not callable(preview) or self.target is None:
            return [], []
        try:
            files, kept = preview(self.target.series_id, tuple(volumes))
        except Exception:  # noqa: BLE001 - the confirmation then names the volumes only
            _log.warning("Releases: the files a replacement would move could not be listed", exc_info=True)
            return [], []
        return list(files), list(kept)

    # --- the download budget -----------------------------------------------------------------------------------

    def send_size(self, candidate: NyaaCandidate) -> int:
        """What this send adds to the download budget: the selected files of a partial send, else the release's size
        (qBittorrent's own figure replaces it once the torrent runs)."""
        sel = self._packs.get(candidate.info_hash)
        if self.partial_requested() and sel is not None:
            return sel.kept_bytes
        return max(0, candidate.size_bytes)

    def budget_status(self) -> Optional[BudgetState]:
        """The backend's budget right now (None: the backend keeps none, or it could not be read - the backend then
        still checks it when sending)."""
        getter = getattr(self._backend, "budget_status", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # noqa: BLE001 - the send decides: the backend queues what does not fit
            return None

    def _budget_choice(self, candidate: NyaaCandidate, wanted: Sequence[str], target_dir: str) -> Optional[dict]:
        """Ask the owner as the budget needs; returns the send's extra arguments ({} without a budget), or None when
        the owner cancelled."""
        state = self.budget_status()
        if state is None:
            return {} if self._confirm(self, self.confirmation_text(candidate, wanted, target_dir)) else None
        size = self.send_size(candidate)
        verdict = state.verdict(0 if candidate.info_hash.lower() in state.hashes else size)
        extra = {"size_bytes": size, "over_cap": OVER_CAP_QUEUE}
        if verdict == FITS:
            line = (f"Download budget: {state.usage_text()}; this adds {gb_text(size)}.\n" if state.limited else "")
            return extra if self._confirm(self, self.confirmation_text(candidate, wanted, target_dir, line)) else None
        if verdict == TOO_BIG:
            text = (f"{too_big_text(size, state)}\n\nSend it anyway, past the cap? It then counts against the budget "
                    "like any download, and later sends wait in the queue until there is room again.\n\n"
                    + self.confirmation_text(candidate, wanted, target_dir))
            choice = self.ask_budget(self, BUDGET_TOO_BIG, text)
        else:
            text = (f"{over_cap_text(state, size)}\n\nQueue it: MangaList sends it to qBittorrent by itself once "
                    "Remove Completed has made room (oldest first). Or send it now, past the cap.\n\n"
                    + self.confirmation_text(candidate, wanted, target_dir))
            choice = self.ask_budget(self, BUDGET_OVER, text)
        if choice == CHOICE_SEND:
            return {**extra, "over_cap": OVER_CAP_SEND}
        if choice == CHOICE_QUEUE and verdict != TOO_BIG:
            return extra
        return None

    def send_selected(self) -> bool:
        """Send the selected release (after the confirmation / the budget question, asked now). While another send
        runs it is queued, with everything chosen now, and sent after the ones before it."""
        if self.send_blocker() is not None:
            return False
        candidate = self.selected_candidate()
        target_dir = self.chosen_target_dir()
        if candidate is None or target_dir is None or self.target is None:
            return False
        wanted = self.wanted_for(candidate)
        extra = self._budget_choice(candidate, wanted, target_dir)
        if extra is None:
            return False
        if self.partial_requested():
            extra["only_missing"] = True
        replace = self.replace_volumes_for(candidate) if self.is_other(candidate) else ()
        if replace:
            extra["replace_volumes"] = replace
        job = SendJob(self.target.series_id, candidate, wanted, target_dir, extra,
                      fits=extra.get("over_cap") != OVER_CAP_QUEUE or self._fits_now(candidate))
        if self._sending is not None:
            self._send_queue.append(job)
            _log.info("Releases: %s queued to send (%d waiting)", candidate.title, len(self._send_queue))
            self._show_send_status()
            self._update_send()
            return True
        self._start_send(job)
        return True

    def _start_send(self, job: SendJob) -> None:
        self._sending = job
        _log.info("Releases: sending %s (series %s)", job.candidate.title, job.series_id)
        self._show_send_status()
        self._update_send()
        backend = self._backend
        self._spawn(lambda: backend.send(job.series_id, job.candidate, job.wanted, job.target_dir, **job.extra),
                    lambda record, j=job: self._on_sent(j, record),
                    lambda message, j=job: self._on_send_error(j, message))

    def _fits_now(self, candidate: NyaaCandidate) -> bool:
        state = self.budget_status()
        return state is None or state.verdict(self.send_size(candidate)) == FITS

    def queued_sends(self) -> List[SendJob]:
        """The sends waiting for their turn, in order (not the one running)."""
        return list(self._send_queue)

    def _show_send_status(self) -> None:
        """The status line: the shown series' sends - the one running, its queued ones with their place, then how its
        last send went (empty when it has none)."""
        lines, tone = [], ""
        sid = self.target.series_id if self.target is not None else None
        job = self._sending
        if job is not None and job.series_id == sid:
            more = len(self._send_queue)
            lines.append(("Sending to qBittorrent: " if job.fits else "Queueing under the download budget: ")
                         + f"{job.candidate.title}..." + (f" ({more} more queued)" if more else ""))
        for place, waiting in enumerate(self._send_queue, 1):
            if waiting.series_id == sid:
                lines.append(f"Queued to send ({ordinal(place)}): {waiting.candidate.title} - MangaList sends it "
                             "after the one before it.")
        outcome = self._send_outcomes.get(sid) if sid is not None else None
        if outcome is not None:
            text, outcome_tone = outcome
            lines.append(text)
            tone = outcome_tone if len(lines) == 1 or outcome_tone == "bad" else ""
        set_tone(self.status_label, tone)
        self.status_label.setText("\n".join(lines))
        links = [f'<a href="cancel:{waiting.ident}">Cancel</a> the queued send ({ordinal(place)}): '
                 f"{html.escape(waiting.candidate.title)}"
                 for place, waiting in enumerate(self._send_queue, 1) if waiting.series_id == sid]
        self.cancel_label.setText("<br>".join(links))
        self.cancel_label.setVisible(bool(links))

    def cancel_links(self) -> List[str]:
        """The Cancel links shown now (``cancel:<ident>``), in order."""
        return re.findall(r'href="(cancel:\d+)"', self.cancel_label.text())

    def _on_cancel_link(self, href: str) -> None:
        if href.startswith("cancel:"):
            try:
                self.cancel_queued(int(href.split(":", 1)[1]))
            except ValueError:
                pass

    def cancel_queued(self, ident: int) -> bool:
        """Take a queued send off the queue (its Cancel link): nothing was sent; the status line says so. False when
        it is not queued (any more) - the one running cannot be called back."""
        job = next((j for j in self._send_queue if j.ident == ident), None)
        if job is None:
            return False
        self._send_queue.remove(job)
        _log.info("Releases: queued send of %s cancelled by the owner (%d still queued)", job.candidate.title,
                  len(self._send_queue))
        self._send_outcomes[job.series_id] = (f"Cancelled the queued send of {job.candidate.title}: nothing was sent.",
                                              "")
        self._show_send_status()
        self._update_send()
        return True

    def _on_sent(self, job: SendJob, record: DownloadRecord) -> None:
        candidate = job.candidate
        self._sent_hashes.add(candidate.info_hash)
        self.sent_records.append(record)
        if record.status == DownloadStatus.QUEUED:
            self._send_outcomes[job.series_id] = (
                f"Queued: {candidate.title} ({place_text(record.queue_position)}). MangaList sends it to qBittorrent "
                "when there is room under the download budget (Settings > Download sources); right-click it in "
                "the In progress list to send it now.", "")
        else:
            files = ("MangaList replaces your copies of "
                     f"{numbers_text(job.extra['replace_volumes'], pad=True)} (the old ones go to the holding folder) "
                     "and files the volumes when the download has finished." if job.extra.get("replace_volumes")
                     else "MangaList files the volumes when the download has finished.")
            self._send_outcomes[job.series_id] = (
                f"Sent to qBittorrent: {candidate.title}. {self._sent_what(candidate)}{files}", "ok")
        self._send_finished()
        self.refresh_downloads()                # the "Already ..." line names it now, before the host reloads
        self.sent.emit(record)

    def _sent_what(self, candidate: NyaaCandidate) -> str:
        """What the send did with the pack, in a sentence ('' when the backend does not say)."""
        taker = getattr(self._backend, "take_pack_outcome", None)
        outcome = taker(candidate.info_hash) if callable(taker) else None
        if outcome is None:
            return ""
        if outcome.partial:
            what = "chosen" if self.is_other(candidate) else "missing"
            return f"Only the {what} volumes are downloaded ({describe(outcome.selection)}). "
        return f"The whole pack is downloaded{f' ({outcome.note})' if outcome.note else ''}. "

    def _on_send_error(self, job: SendJob, message: str) -> None:
        _log.warning("Releases: could not send %s: %s", job.candidate.title, message)
        self._send_outcomes[job.series_id] = (f"Could not send {job.candidate.title}: {message}", "bad")
        self._send_finished()

    def _send_finished(self) -> None:
        """One send is done (sent or failed): the next queued one goes, exactly as it was clicked."""
        self._sending = None
        if self._send_queue:
            self._start_send(self._send_queue.pop(0))
        else:
            self._show_send_status()
            self._update_send()

    # --- closing ---------------------------------------------------------------------------------------

    def stop(self) -> None:
        """Abandon the calls in flight (their results are dropped) and drop the sends still queued (said in the log;
        a send already running may still reach qBittorrent - its answer is dropped)."""
        if self._send_queue:
            _log.info("Releases: the panel closed; %d queued send(s) dropped: %s", len(self._send_queue),
                      "; ".join(job.candidate.title for job in self._send_queue))
        self._send_queue.clear()
        self._sending = None
        self._pack_timer.stop()
        self._background = []
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()
