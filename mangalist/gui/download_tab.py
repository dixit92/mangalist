"""The Download tab: what to get (left), the selected series' releases (right) and what is in progress (bottom right).

**To get** lists the shell's :class:`~mangalist.gui.shell.WantedSeries` in three groups - Missing volumes (nyaa), Missing
chapters (needs Suwayomi), Upgrades (nyaa: volumes for chapters held) - with a filter, a checkbox per series, each
series' gaps and what is going on with it (a search, a download). A series in two nyaa groups (missing volumes AND
upgrades) is one search for every volume it wants. **Releases** is the find-volumes panel for the selected series
(:class:`~mangalist.gui.releases_panel.ReleasesPanel`): selecting a series searches nyaa for it (a short pause first, so
arrowing through the list asks nothing) and keeps the result for the session; a series that cannot be searched says why.
**Find releases for selected** searches every checked series one after another - the backend keeps nyaa's politeness
delay - and marks each "Releases ready"; there is no "send all". **In progress** is the downloads list with Check now.
For an upgrade, a line above the releases says what happens to the chapters the volumes replace (Settings >
Automation); **Replaced chapters** (:mod:`.replaced_chapters`) - a line above In progress - asks about the chapter files
the filed volumes replaced, or says they are in the holding folder, and opens the review (restore, move, keep,
delete after the confirmation).

Every backend call runs off the UI thread (:mod:`.background`); the search queue runs one series at a time.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, List, Optional, Sequence

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QSplitter,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..downloads.contracts import DownloadRecord
from .. import config
from .background import describe_error, start_call
from .chips import ChipButton
from .download_rules import (
    GROUP_NOTES,
    GROUP_TITLES,
    SEARCH_FAILED,
    SEARCH_NONE,
    SEARCH_QUEUED,
    SEARCH_READY,
    SEARCH_RUNNING,
    count_text,
    grouped,
    merge_entries,
    not_findable_reason,
    row_status,
    upgrade_note,
    upgrade_volumes_of,
)
from .download_style import FONT_MONO, apply_style, set_tone
from .download_widgets import ROLE_ASIDE, ROLE_SUB, TwoLineDelegate, button, hbox, label
from .downloads_backend import DownloadsBackend
from .downloads_list import DownloadsList
from .releases_panel import ConfirmFn, ReleasesPanel, SearchOutcome
from .replaced_chapters import ConfirmDeleteFn, ReplacedBar, ReplacedDialog
from .shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, SECTION_SERVICES, WantedSeries
from .volumes_target import VolumeTarget, latest_by_series

ROLE_FOLDER = Qt.ItemDataRole.UserRole + 10
ROLE_HEADER = Qt.ItemDataRole.UserRole + 11       # a group header's text
ROLE_NOTE_TONE = Qt.ItemDataRole.UserRole + 12

_log = logging.getLogger(__name__)

REFRESH_MS = 60_000
SEARCH_DELAY_MS = 350          # a pause after selecting a series, before it is searched
UPGRADE_NOTE_STYLE = "\nQLabel#upgradeNote { background: #e8eefb; border-radius: 6px; padding: 8px 12px; }\n"
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


class DownloadTab(QWidget):
    count_changed = Signal(int)             # how many series are to get (the tab's badge)
    show_in_list = Signal(str)              # a series folder: show it in the List tab
    settings_requested = Signal(str)        # a section of the Settings dialog (SECTION_*)
    library_changed = Signal(list)          # series folders whose files moved (restore / hold / delete): rescan them

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmFn] = None, open_url: Optional[Callable[[str], object]] = None,
                 autostart: bool = True, refresh_ms: int = REFRESH_MS, search_delay_ms: int = SEARCH_DELAY_MS,
                 replaced=None, confirm_delete: Optional[ConfirmDeleteFn] = None):
        super().__init__(parent)
        self.setObjectName("downloadTab")
        self._backend = backend
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
        self._group = cfg.get(CFG_GROUP) if cfg.get(CFG_GROUP) in TAB_GROUPS else GROUP_VOLUMES
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
        self._latest: Dict[int, DownloadRecord] = {}
        self._items: Dict[str, QTreeWidgetItem] = {}
        self._rows: Dict[str, List[QTreeWidgetItem]] = {}
        self._header_items: Dict[str, QTreeWidgetItem] = {}
        self._current: Optional[str] = None
        self._pending_focus: Optional[str] = None
        self._rebuilding = False
        self._stopped = False
        self._build_ui(confirm, open_url)
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
        self.setStyleSheet(self.styleSheet() + UPGRADE_NOTE_STYLE)
        if autostart:
            self.downloads.refresh()
            self.refresh_replaced()

    # --- UI ------------------------------------------------------------------------------------------

    def _build_ui(self, confirm, open_url) -> None:
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
        hv.addWidget(self.filter_edit)
        self.group_chips: Dict[str, ChipButton] = {}
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for group in TAB_GROUPS:
            chip = ChipButton(GROUP_TITLES[group], group)
            chip.setCheckable(True)
            chip.clicked.connect(lambda _=False, g=group: self.show_group(g))
            self.group_chips[group] = chip
            chips.addWidget(chip)
        chips.addStretch(1)
        hv.addLayout(chips)
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
        self.tree.setItemDelegate(_ToGetDelegate(self.tree, mono_sub=True, row_height=46, left_pad=12))
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        lv.addWidget(self.tree, 1)

        foot = QFrame()
        foot.setObjectName("toGetFooter")
        fv = QVBoxLayout(foot)
        fv.setContentsMargins(16, 12, 16, 12)
        fv.setSpacing(6)
        self.selected_label = label("0 selected", "lead")
        self.btn_find = button("Find releases for selected", primary=True)
        self.btn_find.setMinimumHeight(36)
        self.btn_find.clicked.connect(self.find_selected)
        fv.addLayout(hbox(self.selected_label, None, self.btn_find))
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
                                      source_label=self._source_label())
        self.releases.search_again.connect(self._search_again)
        self.releases.sent.connect(self._on_sent)
        self.releases.settings_requested.connect(self.settings_requested)
        tv.addWidget(self.releases)
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
        self._checked &= set(self._by_folder)
        self._queue = deque(f for f in self._queue if f in self._by_folder)
        self._rebuild()
        self.downloads.set_known_titles({sid: self._by_folder[f].title for f, sid in self._series_ids.items()
                                         if sid is not None})
        if self._current in self._by_folder and self._current not in self._results:
            self._show_series(self._current, now=False)      # its wanted volumes changed: look again
        self.count_changed.emit(len(self._by_folder))

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
        groups = grouped(self._wanted, self.filter_edit.text())
        counts = {g: len(items) for g, items in groups}
        if not counts.get(self._group) and not self._group_chosen:     # nothing in the remembered group: the first with any
            self._group = next((g for g in TAB_GROUPS if counts.get(g)), self._group)
        for g, chip in self.group_chips.items():
            chip.set_count(counts.get(g, 0))
            chip.setChecked(g == self._group)
        for group, items in groups:
            if group != self._group:
                continue
            header = QTreeWidgetItem(self.tree)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            note, tone = GROUP_NOTES[group]
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
        if current in self._items:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(self._items[current])
            self.tree.blockSignals(False)
        elif current is not None:
            # the series is filtered out or gone: the panel keeps what it shows only while the series exists
            if current not in self._by_folder:
                self._current = None
                self._show_upgrade_note(None)
                self.releases.show_message("", "Select a series to see its releases.")

    def _refresh_statuses(self) -> None:
        for folder, rows in self._rows.items():
            sid = self._series_ids.get(folder)
            record = self._latest.get(sid) if sid is not None else None
            for row in rows:
                row.setData(0, ROLE_ASIDE, row_status(record, self._states.get(folder)))

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

    # --- selecting a series -------------------------------------------------------------------------------

    def show_group(self, group: str) -> None:
        """Show one group of the "To get" list (Volumes / Upgrades / Chapters); remembered."""
        if group not in TAB_GROUPS:
            return
        self._group = group
        self._group_chosen = True
        cfg = config.load()
        cfg[CFG_GROUP] = group
        config.save(cfg)
        self._rebuild()

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

    def _show_series(self, folder: str, *, now: bool) -> None:
        series = self._by_folder.get(folder)
        if series is None:
            return
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
        needs_suwayomi = series.group == GROUP_CHAPTERS
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

    # --- records -------------------------------------------------------------------------------------------

    def _on_records(self, records: Sequence[DownloadRecord]) -> None:
        self._latest = latest_by_series(records)
        self._refresh_statuses()
        self.refresh_replaced()

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

    def _on_sent(self, _record: DownloadRecord) -> None:
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
        self.downloads.stop()
        self._update_selected()


# The group chips, in the order the owner works through them: what nyaa can get first, chapters (Suwayomi, later) last.
TAB_GROUPS = (GROUP_VOLUMES, GROUP_UPGRADES, GROUP_CHAPTERS)
CFG_GROUP = "download_tab_group"
CFG_SPLIT = "download_tab_split"
CFG_VSPLIT = "download_tab_progress_split"
