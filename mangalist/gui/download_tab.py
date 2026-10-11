"""The Download tab: what to get (left), the selected series' releases (right) and what is in progress (bottom right).

**To get** lists the shell's :class:`~mangalist.gui.shell.WantedSeries` in three groups - Missing volumes (nyaa), Missing
chapters (needs Suwayomi), Upgrades (nyaa: volumes for chapters held) - with a filter, a checkbox per series, each
series' gaps and what is going on with it (a search, a download). A series in two nyaa groups (missing volumes AND
upgrades) is one search for every volume it wants. **Releases** is the find-volumes panel for the selected series
(:class:`~mangalist.gui.releases_panel.ReleasesPanel`): selecting a series searches nyaa for it (a short pause first, so
arrowing through the list asks nothing) and keeps the result for the session; a series that cannot be searched says why.
**Search selected on nyaa** searches every checked series one after another - the backend keeps nyaa's politeness
delay - and marks each "Releases ready"; there is no "send all". **In progress** is the downloads list with Check now.
For an upgrade, a line above the releases says what happens to the chapters the volumes replace (Settings >
Automation); **Replaced chapters** (:mod:`.replaced_chapters`) - a line above In progress - asks about the chapter files
the filed volumes replaced, or says they are in the holding folder, and opens the review (restore, move, keep,
delete after the confirmation).

**Missing chapters** (the Suwayomi MVP, 2026-10-10): once Suwayomi is connected (``backend.suwayomi_ready``) the group is
searchable - selecting a series opens the chapters panel (:class:`~mangalist.gui.chapters_panel.ChaptersPanel`) in the
releases panel's place: the series found in Suwayomi (MangaDex by id; title matches confirmed by the owner), its missing
chapters with the groups that have them, the default group's copies ticked, Send to Suwayomi. A row of the group shows
the series' chapter downloads as chips ("Downloading ch 101-104", "Filed ch 101-104") and the lookup's state; a row of
the volume groups shows its torrents. The In progress list shows both.

**Automatic downloads** (owner decisions 2026-10-10; :mod:`mangalist.downloads.finding`), when the backend has them
(:func:`~.automation_rules.has_automation`): a fourth chip, **Found (N)**, lists the series whose findings wait for the
owner and shows the :class:`~.found_view.FoundView` in the panels' place (Send / Skip per finding, Send all); **Find now**
next to "Check downloads now" runs the find pass; **Find releases for N series** under the list runs the same pass for
the checked series (or, none checked, the whole group shown) - a bulk find, which sends nothing by itself - and opens
Found. The tab's badge counts the series to get plus the findings waiting. **Search selected on nyaa** (the search queue
above) stays: it searches nyaa for each checked series and leaves the pick to the owner.

Every backend call runs off the UI thread (:mod:`.background`) - the findings, the modes and the last pass are quick
reads (the contract) and run on it; the search queue runs one series at a time.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, List, Optional, Sequence

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..downloads.contracts import DownloadRecord, DownloadStatus
from .. import config
from .background import describe_error, start_call
from .chips import ChipButton, FlowLayout
from .chapters_panel import ChaptersPanel
from .automation_rules import bulk_find_label, by_series, has_automation, report_text, series_line
from .download_rules import (
    CHAPTER_CHIPS,
    GROUP_TITLES,
    NOT_NYAA_REASONS,
    SEARCH_FAILED,
    SEARCH_NONE,
    SEARCH_QUEUED,
    SEARCH_READY,
    SEARCH_RUNNING,
    SORT_TITLE,
    SORTS,
    count_text,
    grouped,
    merge_entries,
    chapters_reason,
    chips_text,
    group_note,
    not_findable_reason,
    row_chips,
    upgrade_note,
    upgrade_volumes_of,
)
from .download_style import FONT_MONO, apply_style, set_tone
from .download_widgets import ROLE_ASIDE, ROLE_CHIPS, ROLE_SUB, TwoLineDelegate, button, hbox, label
from .downloads_backend import DownloadsBackend
from .downloads_list import DownloadsList
from .found_view import FoundView
from .releases_panel import ConfirmFn, ReleasesPanel, SearchOutcome
from .replaced_chapters import ConfirmDeleteFn, ReplacedBar, ReplacedDialog
from .shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, SECTION_SERVICES, WantedSeries
from .volumes_target import VolumeTarget

ROLE_FOLDER = Qt.ItemDataRole.UserRole + 10
ROLE_HEADER = Qt.ItemDataRole.UserRole + 11       # a group header's text
ROLE_NOTE_TONE = Qt.ItemDataRole.UserRole + 12
ROLE_GROUP = Qt.ItemDataRole.UserRole + 13         # a series row's group (its chips: torrents or chapter downloads)
ROLE_FOUND = Qt.ItemDataRole.UserRole + 14         # a Found row's series id
PANEL_RELEASES, PANEL_CHAPTERS, PANEL_FOUND = 0, 1, 2
GROUP_FOUND = "found"           # the Found chip (automatic downloads): not a "To get" group

_log = logging.getLogger(__name__)

REFRESH_MS = 60_000
SEARCH_DELAY_MS = 350          # a pause after selecting a series, before it is searched
UPGRADE_NOTE_STYLE = "\nQLabel#upgradeNote { background: #e8eefb; border-radius: 6px; padding: 8px 12px; }\n"
# The group chips as the List tab's (theme.py): the tab's own sheet would make them 36 px, taller than the flow layout's
# lines (they wrap since the Found chip).
CHIP_STYLE = 'QPushButton[chip="true"] { min-height: 30px; max-height: 30px; padding: 0; border-radius: 16px; }\n'

_NOTE_COLORS = {"muted": "#5a5a57", "warn": "#8a3f00"}


def replaced_service(backend):
    """The replaced-chapters service for this backend: its own (``replaced_chapters()``), else one over the backend's
    library database (the adapter's ``db``); None without either (the line stays hidden)."""
    own = getattr(backend, "replaced_chapters", None)
    if callable(own):
        return own()
    db = getattr(backend, "db", None)
    if db is None:
        return None
    from ..upgrades import ReplacedChapters

    return ReplacedChapters(db)


def run_lookup(backend: DownloadsBackend, target: VolumeTarget) -> SearchOutcome:
    """The target folder and the nyaa search for one series (runs off the UI thread; a failure of either is part of the
    outcome, shown in the panel)."""
    outcome = SearchOutcome(target)
    try:
        outcome.placement = backend.placement(target.series_id)
    except Exception as exc:  # noqa: BLE001 - shown to the owner
        outcome.placement_error = describe_error(exc)
    try:
        outcome.candidates = list(backend.search(target.titles, target.missing, target.held))
    except Exception as exc:  # noqa: BLE001
        outcome.error = describe_error(exc)
    return outcome


class _ToGetDelegate(TwoLineDelegate):
    """Series rows as the two-line cell; a group header as small caps-like text with its note at the right."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        if index.data(ROLE_HEADER):
            return QSize(option.rect.width(), 36)
        return super().sizeHint(option, index)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        header = index.data(ROLE_HEADER)
        if not header:
            return super().paint(painter, option, index)
        painter.save()
        rect = option.rect.adjusted(16, 8, -16, 0)
        font = QFont(option.font)
        font.setPointSizeF(max(option.font.pointSizeF() - 2, 7.0))
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        painter.setFont(font)
        painter.setPen(QColor("#3a3a38"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(header))
        note = index.data(ROLE_ASIDE) or ""
        if note:
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor(_NOTE_COLORS.get(index.data(ROLE_NOTE_TONE) or "muted", "#5a5a57")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(note))
        painter.restore()


_FIND_STARTING = object()   # the find call between start_call and its return (see _start_find)


class DownloadTab(QWidget):
    count_changed = Signal(int)             # how many series are to get (the tab's badge)
    show_in_list = Signal(str)              # a series folder: show it in the List tab
    settings_requested = Signal(str)        # a section of the Settings dialog (SECTION_*)
    library_changed = Signal(list)          # series folders whose files moved (restore / hold / delete): rescan them

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmFn] = None, open_url: Optional[Callable[[str], object]] = None,
                 autostart: bool = True, refresh_ms: int = REFRESH_MS, search_delay_ms: int = SEARCH_DELAY_MS,
                 replaced=None, confirm_delete: Optional[ConfirmDeleteFn] = None,
                 confirm_send_all: Optional[Callable[[str, str], bool]] = None):
        super().__init__(parent)
        self.setObjectName("downloadTab")
        self._backend = backend
        self._automation = has_automation(backend)
        self._findings: list = []                   # the waiting findings (automatic downloads)
        self._last_run = None
        self._find_call = None                      # the find pass running for this tab (Find now / a bulk find)
        self._replaced = replaced if replaced is not None else replaced_service(backend)
        self._confirm_delete = confirm_delete
        self._replaced_call = None
        self._wanted: List[WantedSeries] = []
        self._entries: Dict[str, List[WantedSeries]] = {}
        self._by_folder: Dict[str, WantedSeries] = {}
        self._series_ids: Dict[str, Optional[int]] = {}
        self._checked: set = set()
        # One group at a time, picked with the chips above the list (owner, 2026-10-09: "I shouldn't need to scroll down
        # to get to 'upgrades'"); remembered, like the panel's width.
        cfg = config.load()
        self._group = cfg.get(CFG_GROUP) if cfg.get(CFG_GROUP) in self.chip_groups() else GROUP_VOLUMES
        self._group_chosen = False          # the owner picked a group this session: keep it even when it is empty
        self._results: Dict[str, SearchOutcome] = {}
        self._signatures: Dict[str, tuple] = {}
        self._states: Dict[str, str] = {}
        self._queue: Deque[str] = deque()
        self._running: Optional[str] = None
        self._calls: list = []
        self._bulk_total = 0
        self._bulk_done = 0
        self._bulk_notes = ""
        self._records: Dict[int, List[DownloadRecord]] = {}     # every download of a series (the row chips)
        self._seen_status: Optional[Dict[int, str]] = None     # record id -> status at the last reload (None: none yet)
        self._chapter_lookups: Dict[str, object] = {}           # folder -> the chapters panel's last lookup
        self._chapter_states: Dict[str, str] = {}               # folder -> SEARCH_* of its chapter lookup
        self._items: Dict[str, QTreeWidgetItem] = {}
        self._rows: Dict[str, List[QTreeWidgetItem]] = {}
        self._header_items: Dict[str, QTreeWidgetItem] = {}
        self._current: Optional[str] = None
        self._pending_focus: Optional[str] = None
        self._rebuilding = False
        self._stopped = False
        self._build_ui(confirm, open_url, confirm_send_all)
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(search_delay_ms)
        self._delay.timeout.connect(self._search_selected)
        self._refresh_timer: Optional[QTimer] = None
        if refresh_ms > 0:
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setInterval(refresh_ms)
            self._refresh_timer.timeout.connect(self.downloads.refresh)
            self._refresh_timer.start()
        apply_style(self)
        self.setStyleSheet(self.styleSheet() + UPGRADE_NOTE_STYLE + CHIP_STYLE)
        if self._group == GROUP_FOUND:                  # remembered from the last session
            self.panels.setCurrentIndex(PANEL_FOUND)
            self.footer.setVisible(False)
        if autostart:
            self.downloads.refresh()
            self.refresh_replaced()
            self.reload_found()

    # --- UI ------------------------------------------------------------------------------------------

    def _build_ui(self, confirm, open_url, confirm_send_all=None) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)       # the "To get" panel is resizable (owner, 2026-10-09)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        outer.addWidget(self.splitter)

        left = QFrame()
        left.setObjectName("toGetPanel")
        left.setMinimumWidth(260)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        head = QFrame()
        head.setObjectName("toGetHeader")
        hv = QVBoxLayout(head)
        hv.setContentsMargins(16, 16, 16, 10)
        hv.setSpacing(10)
        self.count_label = label("0 series", "muted")
        hv.addLayout(hbox(label("To get", "h1"), None, self.count_label))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setAccessibleName("Filter series to get")
        self.filter_edit.textChanged.connect(self._rebuild)
        # owner, 2026-10-11: "Can I sort this? For example by highest number of missing volumes"
        self.sort_combo = QComboBox()
        self.sort_combo.setAccessibleName("Sort series to get")
        for key, text in SORTS:
            self.sort_combo.addItem(text, key)
        saved_sort = config.load().get(CFG_SORT)
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(saved_sort)))
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.sort_combo)
        hv.addLayout(filter_row)
        self.group_chips: Dict[str, ChipButton] = {}
        chip_box = QWidget()
        chips = FlowLayout(chip_box, spacing=6)         # wraps when the panel is narrow (four chips with Found)
        for group in self.chip_groups():
            chip = ChipButton(GROUP_TITLES.get(group, FOUND_TITLE), group)
            chip.setCheckable(True)
            chip.clicked.connect(lambda _=False, g=group: self.show_group(g))
            self.group_chips[group] = chip
            chips.addWidget(chip)
        hv.addWidget(chip_box)
        lv.addWidget(head)

        self.tree = QTreeWidget()
        self.tree.setObjectName("toGetTree")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setItemsExpandable(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)
        self.tree.setItemDelegate(_ToGetDelegate(self.tree, mono_sub=True, row_height=46, left_pad=12))
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        lv.addWidget(self.tree, 1)

        foot = QFrame()
        foot.setObjectName("toGetFooter")
        self.footer = foot
        fv = QVBoxLayout(foot)
        fv.setContentsMargins(16, 12, 16, 12)
        fv.setSpacing(6)
        self.selected_label = label("0 selected", "lead")
        self.btn_find = button("Search selected on nyaa", primary=not self._automation,
                               tip="Search nyaa for each checked series, one after another; you pick a release in the "
                                   "panel. Nothing is sent.")
        self.btn_find.setMinimumHeight(36)
        self.btn_find.clicked.connect(self.find_selected)
        fv.addLayout(hbox(self.selected_label, None, self.btn_find))
        # The bulk find (automatic downloads): the find pass for these series; what it finds waits under Found.
        self.btn_bulk = button(bulk_find_label(0), primary=True,
                               tip="Run the find pass for the checked series (none checked: every series of the group "
                                   "shown). What it finds is listed under Found for you to send or skip; nothing is "
                                   "sent by itself.")
        self.btn_bulk.setMinimumHeight(36)
        self.btn_bulk.clicked.connect(self.bulk_find)
        self.btn_bulk.setVisible(self._automation)
        fv.addLayout(hbox(None, self.btn_bulk))
        self.bulk_label = label("", "muted", wrap=True)
        self.bulk_label.setVisible(False)
        fv.addWidget(self.bulk_label)
        lv.addWidget(foot)
        self.splitter.addWidget(left)

        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(20, 18, 20, 18)
        self.upgrade_label = label("", wrap=True)
        self.upgrade_label.setObjectName("upgradeNote")        # a tinted banner (its rule is in the tab's sheet)
        self.upgrade_label.setVisible(False)
        tv.addWidget(self.upgrade_label)
        self.releases = ReleasesPanel(self._backend, top, confirm=confirm, open_url=open_url, managed=True,
                                      source_label=self._source_label(),
                                      downloads_for=lambda sid: [r for r in self._records.get(sid, ())
                                                                 if not r.is_chapters])
        self.releases.search_again.connect(self._search_again)
        self.releases.sent.connect(self._on_sent)
        self.releases.settings_requested.connect(self.settings_requested)
        self.chapters = ChaptersPanel(self._backend, top, confirm=confirm,
                                      downloads_for=lambda sid: [r for r in self._records.get(sid, ()) if r.is_chapters])
        self.chapters.sent.connect(self._on_sent)
        self.chapters.looked_up.connect(self._on_chapter_lookup)
        self.chapters.settings_requested.connect(self.settings_requested)
        self.found = FoundView(self._backend, top, confirm=confirm_send_all)
        self.found.changed.connect(self._on_found_changed)
        self.panels = QStackedWidget()
        self.panels.addWidget(self.releases)
        self.panels.addWidget(self.chapters)
        self.panels.addWidget(self.found)                   # PANEL_FOUND
        tv.addWidget(self.panels)
        upper = QWidget()                       # the releases and the replaced-chapters line, above the divider
        uv = QVBoxLayout(upper)
        uv.setContentsMargins(0, 0, 0, 0)
        uv.setSpacing(0)
        uv.addWidget(top, 1)

        self.replaced_bar = ReplacedBar()
        self.replaced_bar.review_requested.connect(self.open_replaced)
        uv.addWidget(self.replaced_bar)

        # The In progress panel's height is the owner's too (owner, 2026-10-09: "should also be resizable").
        self.vsplitter = QSplitter(Qt.Orientation.Vertical)
        self.vsplitter.setChildrenCollapsible(False)
        self.vsplitter.setHandleWidth(5)
        self.vsplitter.addWidget(upper)
        right.addWidget(self.vsplitter)

        bottom = QFrame()
        bottom.setObjectName("inProgressPanel")
        bottom.setMinimumHeight(150)
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(20, 14, 20, 14)
        self.downloads = DownloadsList(self._backend, bottom, autostart=False)
        self.downloads.records_loaded.connect(self._on_records)
        self.downloads.set_find_available(self._automation)
        self.downloads.find_now_requested.connect(self.find_now)
        bv.addWidget(self.downloads)
        self.vsplitter.addWidget(bottom)
        self.vsplitter.setStretchFactor(0, 1)
        self.vsplitter.setStretchFactor(1, 0)
        vsaved = config.load().get(CFG_VSPLIT)
        self.vsplitter.setSizes([int(vsaved[0]), int(vsaved[1])] if isinstance(vsaved, list) and len(vsaved) == 2
                                else [600, 270])
        self.vsplitter.splitterMoved.connect(self._remember_vsplit)
        self.splitter.addWidget(right_widget)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        saved = config.load().get(CFG_SPLIT)
        self.splitter.setSizes([int(saved[0]), int(saved[1])] if isinstance(saved, list) and len(saved) == 2
                               else [400, 1000])
        self.splitter.splitterMoved.connect(self._remember_split)

    def _source_label(self) -> str:
        options_fn = getattr(self._backend, "nyaa_options", None)
        try:
            options = options_fn() if options_fn else None
        except Exception:  # noqa: BLE001 - a label only
            options = None
        if options is None or (options.english and not options.raw) or not (options.english or options.raw):
            return "nyaa · English"
        return "nyaa · Raw" if not options.english else "nyaa · English + Raw"

    # --- the shell's data -------------------------------------------------------------------------------

    def set_wanted(self, series: Sequence[WantedSeries]) -> None:
        """The "To get" list (the shell sends it after every scan / sync). Results of an earlier search are kept for a
        series whose wanted volumes did not change."""
        self._wanted = list(series)
        entries: Dict[str, List[WantedSeries]] = defaultdict(list)
        for item in self._wanted:
            entries[item.folder].append(item)
        self._entries = dict(entries)
        self._by_folder = {folder: merge_entries(items) for folder, items in self._entries.items()}
        self._series_ids = {s.folder: s.series_id if s.series_id is not None else self._backend.series_id_for(s.folder)
                            for s in self._wanted}
        for folder in list(self._results):
            if folder not in self._by_folder or self._signatures.get(folder) != self._signature(self._by_folder[folder]):
                self._results.pop(folder, None)
                self._states.pop(folder, None)
        for folder in list(self._chapter_lookups):          # a lookup stands while the missing chapters do not change
            entry = self._chapters_entry(folder)
            if entry is None or tuple(self._chapter_lookups[folder].missing) != tuple(entry.missing):
                self._chapter_lookups.pop(folder, None)
                self._chapter_states.pop(folder, None)
        self._checked &= set(self._by_folder)
        self._queue = deque(f for f in self._queue if f in self._by_folder)
        if self._automation:                            # the findings' series names come from this list
            self.found.set_findings(self._findings, self.found_names(), self._last_run)
        self._rebuild()
        self.downloads.set_known_titles({sid: self._by_folder[f].title for f, sid in self._series_ids.items()
                                         if sid is not None})
        if self._current in self._by_folder and self._current not in self._results and self._group != GROUP_FOUND:
            self._show_series(self._current, now=False)      # its wanted volumes changed: look again
        self.count_changed.emit(self.badge_count())

    def badge_count(self) -> int:
        """The tab's badge: the series to get plus the findings waiting for the owner."""
        return len(self._by_folder) + len(self._findings)

    @staticmethod
    def _signature(item: WantedSeries) -> tuple:
        return (item.missing, item.held, item.titles, item.findable)

    # --- the list ---------------------------------------------------------------------------------------

    def _rebuild(self, *_args) -> None:
        current = self._current
        self._rebuilding = True
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items.clear()
        self._rows.clear()
        self._header_items.clear()
        groups = grouped(self._wanted, self.filter_edit.text(), self.sort_key())
        counts = {g: len(items) for g, items in groups}
        found = self._found_by_series()
        counts[GROUP_FOUND] = len(self._findings)
        if not counts.get(self._group) and not self._group_chosen:     # nothing in the remembered group: the first with any
            self._group = next((g for g in TAB_GROUPS if counts.get(g)), self._group)   # (Found only when asked for)
        for g, chip in self.group_chips.items():
            chip.set_count(counts.get(g, 0))
            chip.setChecked(g == self._group)
        self.footer.setVisible(self._group != GROUP_FOUND)
        if self._group == GROUP_FOUND:
            self._add_found_rows(found)
        for group, items in groups:
            if group != self._group:
                continue
            header = QTreeWidgetItem(self.tree)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            note, tone = group_note(group, self.suwayomi_ready())
            header.setData(0, ROLE_HEADER, f"{GROUP_TITLES[group].upper()} · {len(items)}")
            header.setData(0, ROLE_ASIDE, note)
            header.setData(0, ROLE_NOTE_TONE, tone)
            header.setSizeHint(0, QSize(100, 36))
            self._header_items[group] = header
            for series in items:
                row = QTreeWidgetItem(self.tree)
                row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                row.setText(0, series.title)
                row.setData(0, ROLE_SUB, series.gaps)
                row.setData(0, ROLE_FOLDER, series.folder)
                row.setData(0, ROLE_GROUP, group)
                row.setCheckState(0, Qt.CheckState.Checked if series.folder in self._checked else Qt.CheckState.Unchecked)
                row.setToolTip(0, f"{series.title}\n{series.folder}")
                self._items.setdefault(series.folder, row)
                self._rows.setdefault(series.folder, []).append(row)
        self.tree.blockSignals(False)
        self._rebuilding = False
        self._refresh_statuses()
        matching = {item.folder for _g, items in groups for item in items}       # every group, the filter applied
        self.count_label.setText(count_text(len(matching), len(self._by_folder)))
        self._update_selected()
        if self._group == GROUP_FOUND:
            self._select_found_row()
        elif current in self._items:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(self._items[current])
            self.tree.blockSignals(False)
        elif current is not None:
            # the series is filtered out or gone: the panel keeps what it shows only while the series exists
            if current not in self._by_folder:
                self._current = None
                self._show_upgrade_note(None)
                self.releases.show_message("", "Select a series to see its releases.")

    def _found_by_series(self) -> Dict[int, list]:
        """The waiting findings by series, the filter applied to the series' names."""
        needle = self.filter_edit.text().strip().casefold()
        names = self.found_names()
        return {sid: items for sid, items in by_series(self._findings).items()
                if not needle or needle in names.get(sid, "").casefold()}

    def _add_found_rows(self, found: Dict[int, list]) -> None:
        """The Found chip's list: one row per series with findings waiting (what they would get, their size)."""
        names = self.found_names()
        header = QTreeWidgetItem(self.tree)
        header.setFlags(Qt.ItemFlag.ItemIsEnabled)
        n = sum(len(items) for items in found.values())
        header.setData(0, ROLE_HEADER, f"{FOUND_TITLE.upper()} · {len(found)} SERIES")
        header.setData(0, ROLE_ASIDE, f"{n} waiting")
        header.setData(0, ROLE_NOTE_TONE, "muted")
        header.setSizeHint(0, QSize(100, 36))
        for sid in sorted(found, key=lambda i: (names.get(i, f"Series #{i}").casefold(), i)):
            items = found[sid]
            row = QTreeWidgetItem(self.tree)
            row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            row.setText(0, names.get(sid, f"Series #{sid}"))
            row.setData(0, ROLE_SUB, series_line(items))
            row.setData(0, ROLE_FOUND, sid)
            row.setData(0, ROLE_CHIPS, [(f"{len(items)} waiting", "ready")])
            row.setToolTip(0, row.text(0))

    def _select_found_row(self) -> None:
        """Keep the series the Found view shows selected in the list (none: every series)."""
        sid = self.found.series_filter()
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(None)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if sid is not None and item.data(0, ROLE_FOUND) == sid:
                self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)

    def _refresh_statuses(self) -> None:
        for folder, rows in self._rows.items():
            sid = self._series_ids.get(folder)
            records = self._records.get(sid, ()) if sid is not None else ()
            for row in rows:
                if row.data(0, ROLE_GROUP) == GROUP_CHAPTERS:
                    chips = row_chips([r for r in records if r.is_chapters], self._chapter_states.get(folder),
                                      CHAPTER_CHIPS)
                else:
                    chips = row_chips([r for r in records if not r.is_chapters], self._states.get(folder))
                row.setData(0, ROLE_CHIPS, chips)
                row.setData(0, ROLE_ASIDE, chips_text(chips))

    def visible_folders(self) -> List[str]:
        """The folders in the list, top to bottom (after the filter)."""
        out = []
        for i in range(self.tree.topLevelItemCount()):
            folder = self.tree.topLevelItem(i).data(0, ROLE_FOLDER)
            if folder and folder not in out:
                out.append(folder)
        return out

    def status_of(self, folder: str) -> str:
        item = self._items.get(folder)
        return (item.data(0, ROLE_ASIDE) or "") if item is not None else ""

    def checked_folders(self) -> List[str]:
        return [f for f in self.visible_folders() if f in self._checked] + \
               [f for f in self._checked if f not in self.visible_folders()]

    def set_checked(self, folder: str, checked: bool = True) -> None:
        """Tick (or untick) a series - also one in a group not shown now (the selection spans the groups)."""
        item = self._items.get(folder)
        if item is not None:
            item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        elif folder in self._by_folder:
            (self._checked.add if checked else self._checked.discard)(folder)
            self._update_selected()

    def _on_item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        if self._rebuilding:
            return
        folder = item.data(0, ROLE_FOLDER)
        if not folder:
            return
        checked = item.checkState(0) == Qt.CheckState.Checked
        if checked:
            self._checked.add(folder)
        else:
            self._checked.discard(folder)
        self._rebuilding = True                     # the same series' row in another group follows
        for row in self._rows.get(folder, ()):
            if row is not item:
                row.setCheckState(0, item.checkState(0))
        self._rebuilding = False
        self._update_selected()

    def _update_selected(self) -> None:
        n = len(self._checked)
        self.selected_label.setText(f"{n} selected")
        self.btn_find.setEnabled(n > 0 and not self._stopped)
        ids = self.bulk_series_ids()
        self.btn_bulk.setText(bulk_find_label(len(ids)))
        self.btn_bulk.setEnabled(bool(ids) and self._find_call is None and not self._stopped)

    # --- selecting a series -------------------------------------------------------------------------------

    def chip_groups(self) -> tuple:
        """The chips above the list: the "To get" groups, and Found when the backend can find."""
        return TAB_GROUPS + ((GROUP_FOUND,) if self._automation else ())

    def sort_key(self) -> str:
        return self.sort_combo.currentData() or SORT_TITLE

    def set_sort(self, key: str) -> None:
        index = self.sort_combo.findData(key)
        if index >= 0:
            self.sort_combo.setCurrentIndex(index)

    def _on_sort_changed(self, _index: int) -> None:
        cfg = config.load()
        cfg[CFG_SORT] = self.sort_key()
        config.save(cfg)
        self._rebuild()

    def show_group(self, group: str) -> None:
        """Show one group of the "To get" list (Volumes / Upgrades / Chapters), or Found; remembered."""
        if group not in self.chip_groups():
            return
        leaving_found = self._group == GROUP_FOUND and group != GROUP_FOUND
        self._group = group
        self._group_chosen = True
        cfg = config.load()
        cfg[CFG_GROUP] = group
        config.save(cfg)
        self._rebuild()
        if group == GROUP_FOUND:
            self._delay.stop()
            self._show_upgrade_note(None)
            self.panels.setCurrentIndex(PANEL_FOUND)
        elif leaving_found:
            if self._current in self._by_folder:
                self._show_series(self._current, now=False)
            else:
                self.panels.setCurrentIndex(PANEL_RELEASES)
                self.releases.show_message("", "Select a series to see its releases.")

    def current_group(self) -> str:
        return self._group

    def _remember_split(self, *_args) -> None:
        cfg = config.load()
        cfg[CFG_SPLIT] = [int(x) for x in self.splitter.sizes()]
        config.save(cfg)

    def _remember_vsplit(self, *_args) -> None:
        cfg = config.load()
        cfg[CFG_VSPLIT] = [int(x) for x in self.vsplitter.sizes()]
        config.save(cfg)

    def focus(self, folder: str) -> None:
        """Select that series and search it now ("Get the missing volumes" in the List tab)."""
        if folder not in self._by_folder:
            return
        groups = [e.group for e in self._entries.get(folder, ())] or [self._by_folder[folder].group]
        if self._group not in groups:                    # show the group that holds it
            self.show_group(next((g for g in TAB_GROUPS if g in groups), groups[0]))
        if folder not in self._items and self.filter_edit.text():
            self.filter_edit.clear()                     # the filter hid it
        item = self._items.get(folder)
        if item is None:
            return
        self._pending_focus = folder
        if self.tree.currentItem() is item:
            self._show_series(folder, now=True)
        else:
            self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)

    def current_folder(self) -> Optional[str]:
        return self._current

    def _on_current_changed(self, item: Optional[QTreeWidgetItem], _prev) -> None:
        if item is not None and item.data(0, ROLE_FOUND) is not None:
            self.found.set_series_filter(item.data(0, ROLE_FOUND))
            self.panels.setCurrentIndex(PANEL_FOUND)
            return
        folder = item.data(0, ROLE_FOLDER) if item is not None else None
        if not folder:
            return
        now = self._pending_focus == folder
        self._pending_focus = None
        self._show_series(folder, now=now)

    def _target_for(self, series: WantedSeries) -> Optional[VolumeTarget]:
        sid = self._series_ids.get(series.folder)
        if sid is None:
            return None
        upgrade = tuple(v for v in upgrade_volumes_of(self._entries.get(series.folder, ())) if v in series.missing)
        return VolumeTarget(series_id=sid, folder=series.folder, title=series.title,
                            titles=series.titles or (series.title,), missing=series.missing, held=series.held,
                            upgrade=upgrade)

    def suwayomi_ready(self) -> bool:
        """Suwayomi is connected: the Missing chapters group can be looked up (quick: one database read)."""
        ready = getattr(self._backend, "suwayomi_ready", None)
        try:
            return bool(ready()) if callable(ready) else False
        except Exception:  # noqa: BLE001 - a missing connection is "not ready", never a crash
            return False

    def _chapters_entry(self, folder: str) -> Optional[WantedSeries]:
        return next((e for e in self._entries.get(folder, ()) if e.group == GROUP_CHAPTERS), None)

    def _show_series(self, folder: str, *, now: bool) -> None:
        series = self._by_folder.get(folder)
        if series is None:
            return
        chapters = self._chapters_entry(folder)
        if chapters is not None and (self._group == GROUP_CHAPTERS or series.group == GROUP_CHAPTERS):
            self._current = folder
            self._delay.stop()
            self._show_chapters(chapters)
            return
        self.panels.setCurrentIndex(PANEL_RELEASES)
        self._current = folder
        self._delay.stop()
        reason = not_findable_reason(series)
        self._show_upgrade_note(folder if not reason else None)
        if reason:
            self._show_unavailable(series, reason)
            return
        target = self._target_for(series)
        if target is None:
            self.releases.show_message(series.title, "MangaList has not scanned this folder yet: rescan first.")
            return
        result = self._results.get(folder)
        if result is not None:
            self.releases.show_outcome(result)
            return
        self.releases.show_searching(target)
        if self._states.get(folder) in (SEARCH_QUEUED, SEARCH_RUNNING):
            if self._states.get(folder) == SEARCH_QUEUED:
                self._enqueue(folder, front=True)
            return
        if now:
            self._search_selected()
        else:
            self._delay.start()

    def _show_chapters(self, series: WantedSeries) -> None:
        """A Missing chapters series: the chapters panel (or why it cannot be looked up)."""
        self._show_upgrade_note(None)
        sid = self._series_ids.get(series.folder)
        reason = chapters_reason(series, self.suwayomi_ready(), sid)
        if reason:
            self.panels.setCurrentIndex(PANEL_RELEASES)
            self._show_unavailable(series, reason)
            return
        self.panels.setCurrentIndex(PANEL_CHAPTERS)
        cached = self._chapter_lookups.get(series.folder)
        if cached is None:
            self._chapter_states[series.folder] = SEARCH_RUNNING
            self._refresh_statuses()
        self.chapters.open_series(sid, series.title, series.missing, series.titles or (series.title,), cached=cached)

    def _exec_menu(self, menu: QMenu, pos):
        return menu.exec(pos)

    def _on_tree_menu(self, pos) -> None:
        """A To get row's menu: Check for new chapters (owner, 2026-10-11: "Can I check for new chapters on a
        volumes-based series within the download view?")."""
        item = self.tree.itemAt(pos)
        folder = item.data(0, ROLE_FOLDER) if item is not None else None
        series = self._by_folder.get(folder) if folder else None
        if series is None:
            return
        menu = QMenu(self.tree)
        act_new = menu.addAction("Check for new chapters…")
        act_new.setToolTip("Ask the series' Suwayomi source for chapters newer than the newest one you have (for "
                           "volumes: the last chapter of your newest volume)")
        if self._exec_menu(menu, self.tree.viewport().mapToGlobal(pos)) is act_new:
            sid = self._series_ids.get(folder) or series.series_id
            self.check_new_chapters(sid, folder, series.title, series.titles or (series.title,))

    def check_new_chapters(self, series_id: Optional[int], folder: str, title: str, titles: Sequence[str]) -> bool:
        """"Check for new chapters" (the List's row menu; owner, 2026-10-11): the chapters panel with what the series'
        source has after the newest chapter held - also for a series with no gaps, since MangaUpdates' latest chapter
        (what the gaps count up to) can be behind the sites."""
        self._show_upgrade_note(None)
        self._delay.stop()
        if not self.suwayomi_ready() or series_id is None:
            self.panels.setCurrentIndex(PANEL_RELEASES)
            ready = self.suwayomi_ready()
            self.releases.show_message(title, NOT_NYAA_REASONS[GROUP_CHAPTERS] if not ready else
                                       "MangaList has not scanned this folder yet: rescan first.",
                                       None if ready else "Open Settings", None if ready else SECTION_SERVICES)
            self.releases.subtitle_label.setText("")
            return False
        self._current = folder
        self.panels.setCurrentIndex(PANEL_CHAPTERS)
        self.chapters.open_series(series_id, title, (), tuple(titles) or (title,), newer=True)
        return True

    def _on_chapter_lookup(self, series_id: int, lookup) -> None:
        folder = next((f for f, sid in self._series_ids.items() if sid == series_id), None)
        if folder is None:
            return
        if lookup is None:
            self._chapter_lookups.pop(folder, None)
            self._chapter_states[folder] = SEARCH_FAILED
        else:
            self._chapter_lookups[folder] = lookup
            ready = lookup.match is not None and bool(lookup.available) or bool(lookup.candidates)
            self._chapter_states[folder] = SEARCH_READY if ready else SEARCH_NONE
        self._refresh_statuses()

    def chapter_lookup_of(self, folder: str):
        """The chapters panel's last lookup of *folder* (None when not looked up this session)."""
        return self._chapter_lookups.get(folder)

    def _show_upgrade_note(self, folder: Optional[str]) -> None:
        """The line above the releases of a series with upgrade volumes: what happens to the chapters they replace."""
        volumes = upgrade_volumes_of(self._entries.get(folder, ())) if folder else ()
        text = ""
        if volumes:
            settings = None
            try:
                settings = self._replaced.settings() if self._replaced is not None else None
            except Exception:  # noqa: BLE001 - a note only
                _log.warning("Reading the replaced-chapters settings failed", exc_info=True)
            from ..upgrades import ReplacedSettings

            settings = settings or ReplacedSettings()
            text = upgrade_note(volumes, settings.mode, settings.holding_days)
        self.upgrade_label.setText(text)
        self.upgrade_label.setVisible(bool(text))

    def upgrade_note_text(self) -> str:
        """The upgrade line shown above the releases ('' when none)."""
        return self.upgrade_label.text()

    def _show_unavailable(self, series: WantedSeries, reason: str) -> None:
        self.panels.setCurrentIndex(PANEL_RELEASES)
        needs_suwayomi = series.group == GROUP_CHAPTERS and not self.suwayomi_ready()
        self.releases.show_message(series.title, reason, "Open Settings" if needs_suwayomi else None,
                                   SECTION_SERVICES if needs_suwayomi else None)
        self.releases.subtitle_label.setText(f"Wanted: {series.gaps}")

    def _search_selected(self) -> None:
        folder = self._current
        if folder is None or folder in self._results or self._states.get(folder) in (SEARCH_QUEUED, SEARCH_RUNNING):
            return
        series = self._by_folder.get(folder)
        if series is None or not_findable_reason(series) or self._target_for(series) is None:
            return
        self._enqueue(folder, front=True)

    def _search_again(self) -> None:
        folder = self._current
        if folder is None or self._states.get(folder) in (SEARCH_QUEUED, SEARCH_RUNNING):
            return
        self._results.pop(folder, None)
        self._states.pop(folder, None)
        series = self._by_folder.get(folder)
        target = self._target_for(series) if series else None
        if target is not None:
            self.releases.show_searching(target)
            self._enqueue(folder, front=True)
        self._refresh_statuses()

    # --- the search queue ---------------------------------------------------------------------------------

    def _enqueue(self, folder: str, *, front: bool = False) -> None:
        if folder == self._running:
            return
        if folder in self._queue:
            if front:
                self._queue.remove(folder)
                self._queue.appendleft(folder)
            return
        (self._queue.appendleft if front else self._queue.append)(folder)
        self._states[folder] = SEARCH_QUEUED
        self._refresh_statuses()
        self._pump()

    def _pump(self) -> None:
        if self._running is not None or self._stopped:
            return
        while self._queue:
            folder = self._queue.popleft()
            series = self._by_folder.get(folder)
            target = self._target_for(series) if series else None
            if series is None or target is None:
                self._states.pop(folder, None)
                continue
            self._start_lookup(folder, series, target)
            return
        self._end_bulk()

    def _start_lookup(self, folder: str, series: WantedSeries, target: VolumeTarget) -> None:
        self._running = folder
        self._states[folder] = SEARCH_RUNNING
        self._signatures[folder] = self._signature(series)
        self._refresh_statuses()
        self._show_bulk_progress()
        if folder == self._current:
            self.releases.show_searching(target)
        backend = self._backend
        made: list = []
        call = start_call(lambda: run_lookup(backend, target), lambda outcome, f=folder: self._on_outcome(f, outcome),
                          lambda message, f=folder, t=target: self._on_outcome(
                              f, SearchOutcome(t, candidates=None, error=message)),
                          lambda: self._lookup_finished(made[0] if made else None))
        made.append(call)
        self._calls.append(call)

    def _on_outcome(self, folder: str, outcome: SearchOutcome) -> None:
        series = self._by_folder.get(folder)
        if series is None or self._signatures.get(folder) != self._signature(series):
            return                                           # the series changed meanwhile: the result is stale
        self._results[folder] = outcome
        if outcome.candidates is None:
            self._states[folder] = SEARCH_FAILED
        else:
            self._states[folder] = SEARCH_READY if outcome.candidates else SEARCH_NONE
        self._bulk_done += 1 if self._bulk_total else 0
        self._refresh_statuses()
        if folder == self._current:
            self.releases.show_outcome(outcome)

    def _lookup_finished(self, call) -> None:
        if call in self._calls:
            self._calls.remove(call)
        self._running = None
        self._pump()

    # --- bulk ---------------------------------------------------------------------------------------------

    def find_selected(self) -> int:
        """Search every checked series, one after another. Returns how many were queued."""
        queued = skipped = ready = 0
        reasons: List[str] = []
        for folder in self.checked_folders():
            series = self._by_folder.get(folder)
            if series is None:
                continue
            reason = not_findable_reason(series)
            if reason or self._target_for(series) is None:
                skipped += 1
                reasons.append(reason or "not scanned yet")
                continue
            if folder in self._results:
                ready += 1
                continue
            if self._states.get(folder) in (SEARCH_QUEUED, SEARCH_RUNNING):
                continue
            self._enqueue(folder)
            queued += 1
        if queued:
            self._bulk_total += queued
        notes = []
        if skipped:
            notes.append(f"{skipped} skipped ({reasons[0].rstrip('.')})" if len(set(reasons)) == 1 else f"{skipped} skipped")
        if ready:
            notes.append(f"{ready} already searched")
        if not queued:
            self._set_bulk_text("; ".join(notes) or "Nothing to search.")
        elif notes:
            self._bulk_notes = "; ".join(notes)
        return queued

    def _show_bulk_progress(self) -> None:
        if not self._bulk_total:
            return
        done = min(self._bulk_done + 1, self._bulk_total)
        text = f"Searching {done} of {self._bulk_total}..."
        self._set_bulk_text(text + (f" ({self._bulk_notes})" if self._bulk_notes else ""))

    def _end_bulk(self) -> None:
        if self._bulk_total:
            text = f"Searched {self._bulk_total}: pick a series to see its releases."
            if self._bulk_notes:
                text += f" ({self._bulk_notes})"
            self._set_bulk_text(text)
        self._bulk_total = self._bulk_done = 0
        self._bulk_notes = ""

    def _set_bulk_text(self, text: str) -> None:
        self.bulk_label.setText(text)
        self.bulk_label.setVisible(bool(text))

    # --- automatic downloads: Found, Find now, the bulk find ----------------------------------------------------

    def found_names(self) -> Dict[int, str]:
        """series id -> its name, for the findings: the "To get" titles, else what the backend calls it."""
        names = {sid: self._by_folder[f].title for f, sid in self._series_ids.items()
                 if sid is not None and f in self._by_folder}
        missing = {f.series_id for f in self._findings} - set(names)
        titles_fn = getattr(self._backend, "series_titles", None)
        if missing and callable(titles_fn):
            try:
                names.update({int(k): v for k, v in dict(titles_fn(missing)).items() if v})
            except Exception:  # noqa: BLE001 - names only: "Series #id" stands in
                _log.warning("Reading series names for the findings failed", exc_info=True)
        return names

    def reload_found(self) -> None:
        """Re-read the waiting findings and the last find pass (quick reads) - after a pass, a send or skip, and with
        every downloads reload (the container's scheduled pass adds findings too)."""
        if not self._automation or self._stopped:
            return
        try:
            findings = list(self._backend.findings("waiting"))
            last_run = self._backend.last_find_run()
        except Exception as exc:  # noqa: BLE001 - shown in the view; the old list stays
            self.found.say(f"Could not read what was found: {describe_error(exc)}", "bad")
            return
        changed = [f.id for f in findings] != [f.id for f in self._findings]
        self._findings, self._last_run = findings, last_run
        self.found.set_findings(findings, self.found_names(), last_run)
        if changed or self._group == GROUP_FOUND:
            self._rebuild()
        self.count_changed.emit(self.badge_count())

    def findings(self) -> list:
        return list(self._findings)

    def _on_found_changed(self) -> None:
        self.reload_found()
        self.downloads.refresh()

    def bulk_series_ids(self) -> List[int]:
        """The series a bulk find covers: the checked ones, else every series of the group shown (the filter applied)."""
        if self._group == GROUP_FOUND:
            return []
        folders = self.checked_folders() or self.visible_folders()
        return sorted({sid for sid in (self._series_ids.get(f) for f in folders) if sid is not None})

    def bulk_find(self) -> bool:
        """"Find releases for N series": the find pass for :meth:`bulk_series_ids`, then the Found view."""
        return self.find_for(self.bulk_series_ids())

    def find_for(self, series_ids: Sequence[int]) -> bool:
        """A bulk find of *series_ids* (the List's row menu, the button under the list): shows Found while it runs.
        Nothing is sent by itself; what it finds waits under Found."""
        ids = sorted({int(i) for i in series_ids if i is not None})
        if not ids or not self._automation:
            return False
        self.show_group(GROUP_FOUND)
        return self._start_find(ids)

    def find_now(self) -> bool:
        """Find now: the find pass for every series with gaps (off the UI thread)."""
        return self._start_find(None)

    def find_running(self) -> bool:
        return self._find_call is not None

    def _start_find(self, ids: Optional[List[int]]) -> bool:
        if not self._automation or self._stopped:
            return False
        if self._find_call is not None:
            text = "A find pass is already running: its results show under Found when it ends."
            self.downloads.set_note(text, "warn")
            self.found.say(text, "warn")
            return False
        bulk = ids is not None
        self.found.say("")
        self.found.set_busy(f"Finding releases for {len(ids)} series..." if bulk
                            else "Finding releases for every series with gaps...")
        self.downloads.set_find_busy(True)
        _log.info("Download tab: %s", f"bulk find for {len(ids)} series" if bulk else "Find now")
        backend = self._backend
        made: list = []
        self._find_call = _FIND_STARTING
        call = start_call(lambda: backend.find_now(ids) if bulk else backend.find_now(),
                          lambda report: self._on_found_report(report, bulk),
                          lambda message: self._on_find_error(message),
                          lambda: self._find_finished(made[0] if made else None))
        made.append(call)
        if self._find_call is _FIND_STARTING:       # still running (a runner that finished at once has cleared it)
            self._find_call = call
            self._calls.append(call)
        self._update_selected()
        return True

    def _on_found_report(self, report, bulk: bool) -> None:
        text = report_text(report, bulk)
        tone = "bad" if getattr(report, "errors", 0) else ""
        self.downloads.set_note(text, tone)
        self.found.say(text, tone)

    def _on_find_error(self, message: str) -> None:
        text = f"Could not run the find pass: {message}"
        self.downloads.set_note(text, "bad")
        self.found.say(text, "bad")

    def _find_finished(self, call) -> None:
        if call in self._calls:
            self._calls.remove(call)
        self._find_call = None
        if self._stopped:
            return
        self.found.set_busy("")
        self.downloads.set_find_busy(False)
        self._update_selected()
        self.reload_found()
        self.downloads.refresh()                # a series on Auto may have had something sent

    # --- records -------------------------------------------------------------------------------------------

    def _on_records(self, records: Sequence[DownloadRecord]) -> None:
        self._rescan_newly_filed(records)
        by_series: Dict[int, List[DownloadRecord]] = {}
        for record in records:
            by_series.setdefault(record.series_id, []).append(record)
        self._records = by_series
        self._refresh_statuses()
        self.releases.refresh_downloads()
        self.refresh_replaced()
        self.reload_found()

    def _rescan_newly_filed(self, records: Sequence[DownloadRecord]) -> None:
        """Files landed in the library since the last reload (a check filed volumes or chapters, here or in the
        container's hourly job): ask for a rescan of those series, so the To get list and the panel follow (owner,
        2026-10-10: "It finished but the to get and the main panel didn't refresh"). The first reload only remembers."""
        done = (DownloadStatus.FILED, DownloadStatus.REMOVED)
        seen = self._seen_status
        self._seen_status = {r.id: r.status for r in records}
        if seen is None:
            return
        landed = {r.series_id for r in records
                  if r.status in done and r.filed_files and seen.get(r.id) not in done}
        if not landed:
            return
        folders = [f for f, sid in self._series_ids.items() if sid in landed]
        for folder in folders:                              # the panel looks the series up again after the rescan
            self._chapter_lookups.pop(folder, None)
            self._results.pop(folder, None)
        _log.info("Download tab: files filed for %d series - asking for a rescan", len(landed))
        self.library_changed.emit(folders)

    # --- replaced chapters ------------------------------------------------------------------------------

    def refresh_replaced(self) -> None:
        """Reload the replaced-chapters line (off the UI thread; after every downloads reload)."""
        service = self._replaced
        if service is None or self._stopped or self._replaced_call is not None:
            return
        made: list = []
        self._replaced_call = start_call(service.open_batches, self.replaced_bar.set_batches,
                                         lambda message: _log.warning("Replaced chapters: %s", message),
                                         lambda: self._replaced_loaded(made[0] if made else None))
        made.append(self._replaced_call)
        self._calls.append(self._replaced_call)

    def _replaced_loaded(self, call) -> None:
        if call in self._calls:
            self._calls.remove(call)
        if call is self._replaced_call:
            self._replaced_call = None

    def replaced_dialog(self) -> Optional[ReplacedDialog]:
        """The review of the replaced chapters (not shown; :meth:`open_replaced` runs it)."""
        if self._replaced is None:
            return None
        dialog = ReplacedDialog(self._replaced, self.replaced_bar.batches, self, confirm=self._confirm_delete)
        dialog.changed.connect(self.library_changed)
        dialog.finished.connect(lambda _code: self.refresh_replaced())
        return dialog

    def open_replaced(self) -> None:
        dialog = self.replaced_dialog()
        if dialog is None:
            return
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _on_sent(self, _record) -> None:
        self.downloads.refresh()

    def refresh(self) -> None:
        """Reload the downloads (the hourly timer does this too)."""
        self.downloads.refresh()

    # --- closing --------------------------------------------------------------------------------------------

    def stop(self) -> None:
        """On close: abandon the background calls (their results are dropped)."""
        self._stopped = True
        self._delay.stop()
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self._queue.clear()
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()
        self.releases.stop()
        self.chapters.stop()
        self.found.stop()
        self.downloads.stop()
        self._update_selected()


# The group chips, in the order the owner works through them: what nyaa can get first, chapters (Suwayomi, later) last.
TAB_GROUPS = (GROUP_VOLUMES, GROUP_UPGRADES, GROUP_CHAPTERS)
FOUND_TITLE = "Found"
CFG_GROUP = "download_tab_group"
CFG_SORT = "download_tab_sort"
CFG_SPLIT = "download_tab_split"
CFG_VSPLIT = "download_tab_progress_split"
