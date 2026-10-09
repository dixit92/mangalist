"""The List tab's widgets (mockup ``Main.dc.html``): the filter bar (search, the Library picker, state chips with live
counts, Details, MU lookup / Stop), the table - or the duplicates view, or an empty state - beside the collapsible details panel, and
the footer (counts, messages, scan and signing progress).

A view only: the main window owns the model and every action (it connects to the widgets exposed here)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..states import State
from .chips import ChipButton, FlowLayout
from .detail_panel import DetailPanel
from .list_delegates import ROW_HEIGHT, MonoDelegate, SecondaryDelegate, StateBadgeDelegate, TitleDelegate
from .table_model import (
    COL_ENGLISH, COL_FILES, COL_GAPS, COL_LIBRARY, COL_STATE, COL_TITLE, COL_VERDICT, STATE_FILTERS,
)

DUPLICATES = "duplicates"               # the Duplicates chip's key (not a state filter)
MORE = "more"
CHIPS: Tuple[Tuple[Optional[str], str], ...] = (
    (None, "All"),
    (State.MISSING_VOLUMES.value, "Missing volumes"),
    (State.MISSING_CHAPTERS.value, "Missing chapters"),
    (State.UPGRADE.value, "Upgrade available"),
    (State.UP_TO_DATE.value, "Up to date"),
    (State.COMPLETE.value, "Complete"),
    (State.CANT_TELL.value, "Can't tell"),
    (DUPLICATES, "Duplicates"),
)
_CHIP_KEYS = {key for key, _ in CHIPS}
#: The other state filters (Wanted, Upcoming, Needs attention, ...) sit behind "More".
MORE_FILTERS: List[Tuple[str, str]] = [(k, label) for k, label in STATE_FILTERS if k not in _CHIP_KEYS]

ALL_LIBRARIES = "All libraries"
PAGE_TABLE, PAGE_DUPLICATES, PAGE_EMPTY = 0, 1, 2
DETAILS_WIDTH = 380


class ListTab(QWidget):
    filter_changed = Signal(object)     # a chip's key: None (All), a STATE_FILTERS key, or DUPLICATES
    library_changed = Signal(object)    # the picker: a root id, or None (All libraries)
    details_toggled = Signal(bool)
    add_root_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter: Optional[str] = None
        self._counts: Dict[Optional[str], Optional[int]] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_filter_bar())

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.stack = QStackedWidget()
        self.table = self._build_table()
        self.stack.addWidget(self.table)                       # PAGE_TABLE
        self._duplicates_slot = QWidget()                      # PAGE_DUPLICATES (lane C's view goes here)
        QVBoxLayout(self._duplicates_slot).setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self._duplicates_slot)
        self.stack.addWidget(self._build_empty_state())        # PAGE_EMPTY
        self.details = DetailPanel()
        self.details.close_requested.connect(lambda: self.set_details_visible(False, emit=True))
        self.splitter.addWidget(self.stack)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        outer.addWidget(self.splitter, 1)
        outer.addWidget(self._build_footer())

    # --- building -------------------------------------------------------------------------------------------

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("filterBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 12, 20, 12)
        row.setSpacing(12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter title, English title, verdict")
        self.search.setAccessibleName("Filter series")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(200)
        self.search.setMaximumWidth(320)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self.search, 1, Qt.AlignmentFlag.AlignTop)

        # "All libraries · <root> · <root> ..." - a dropdown, so many roots never crowd the bar (hidden with one).
        self.library_picker = QComboBox()
        self.library_picker.setObjectName("libraryPicker")
        self.library_picker.setAccessibleName("Library")
        self.library_picker.setToolTip("Show one library folder, or all of them")
        self.library_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.library_picker.setMinimumWidth(150)
        self.library_picker.addItem(ALL_LIBRARIES, None)
        self.library_picker.setVisible(False)
        self.library_picker.activated.connect(self._on_library_activated)
        row.addWidget(self.library_picker, 0, Qt.AlignmentFlag.AlignTop)

        chip_box = QWidget()
        chips = FlowLayout(chip_box, spacing=6)
        self.chips: Dict[Optional[str], ChipButton] = {}
        for key, label in CHIPS:
            chip = ChipButton(label, key)
            chip.clicked.connect(lambda _c=False, k=key: self.set_filter(k, emit=True))
            chips.addWidget(chip)
            self.chips[key] = chip
        self.chip_more = ChipButton("More ▾", MORE)
        self.chip_more.setToolTip("Other state filters: Wanted, Upcoming, Needs attention, ...")
        self.more_menu = QMenu(self.chip_more)
        self._more_actions: Dict[str, QAction] = {}
        for key, label in MORE_FILTERS:
            act = self.more_menu.addAction(label)
            act.triggered.connect(lambda _c=False, k=key: self.set_filter(k, emit=True))
            self._more_actions[key] = act
        self.chip_more.clicked.connect(self._open_more)
        chips.addWidget(self.chip_more)
        self.chips[None].setChecked(True)
        # The search takes spare width first (up to the mockup's 320 px); the chips wrap only when it is short.
        chip_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        row.addWidget(chip_box)

        # Shown only while the details panel is collapsed (its own × hides it).
        self.btn_details = QPushButton("Details")
        self.btn_details.setToolTip("Show the details panel")
        self.btn_details.clicked.connect(lambda: self.set_details_visible(True, emit=True))
        self.btn_details.setVisible(False)
        self.btn_mu_start = QPushButton("MU lookup")
        self.btn_mu_start.setToolTip("Look up every series on MangaUpdates (series MangaPixer knows are skipped)")
        self.btn_mu_stop = QPushButton("Stop")
        self.btn_mu_stop.setToolTip("Stop the MangaUpdates lookup")
        self.btn_mu_stop.setVisible(False)
        self.btn_mu_stop.setEnabled(False)
        for b in (self.btn_details, self.btn_mu_start, self.btn_mu_stop):
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        return bar

    def _build_table(self) -> QTableView:
        table = QTableView()
        table.setObjectName("seriesTable")
        table.setSortingEnabled(True)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setSectionsMovable(True)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._delegates = {COL_TITLE: TitleDelegate(table), COL_STATE: StateBadgeDelegate(table),
                           COL_GAPS: MonoDelegate(table), COL_FILES: MonoDelegate(table),
                           COL_ENGLISH: SecondaryDelegate(table), COL_VERDICT: SecondaryDelegate(table),
                           COL_LIBRARY: SecondaryDelegate(table)}
        for col, delegate in self._delegates.items():
            table.setItemDelegateForColumn(col, delegate)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        return table

    def _build_empty_state(self) -> QWidget:
        page = QWidget()
        page.setObjectName("emptyState")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        col = QVBoxLayout(page)
        col.setContentsMargins(40, 40, 40, 40)
        col.setSpacing(10)
        col.addStretch(1)
        self.empty_title = QLabel("No library folder yet")
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text = QLabel("Add the folder that holds your series folders. MangaList scans it and shows "
                                 "every series here.")
        self.empty_text.setProperty("role", "muted")
        self.empty_text.setWordWrap(True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_progress = QProgressBar()
        self.empty_progress.setTextVisible(False)
        self.empty_progress.setFixedWidth(280)
        self.empty_progress.setVisible(False)
        self.btn_add_root = QPushButton("Add a library folder…")
        self.btn_add_root.setProperty("variant", "primary")
        self.btn_add_root.clicked.connect(self.add_root_clicked)
        for w in (self.empty_title, self.empty_text):
            col.addWidget(w)
        col.addWidget(self.empty_progress, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.btn_add_root, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(2)
        return page

    def _build_footer(self) -> QWidget:
        foot = QWidget()
        foot.setObjectName("footer")
        foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        foot.setFixedHeight(32)
        row = QHBoxLayout(foot)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(20)
        self.counts_label = QLabel("")
        self.kinds_label = QLabel("")
        self.status_label = QLabel("Ready")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(160)
        self.progress.setVisible(False)
        self.sig_label = QLabel("")
        self.sig_label.setToolTip("Content signatures (128 KiB read per archive) let MangaList recognise renamed or "
                                  "moved series; signed in the background after a scan")
        self.sig_label.setVisible(False)
        row.addWidget(self.counts_label)
        row.addWidget(self.kinds_label)
        row.addWidget(self.status_label, 1)
        row.addWidget(self.progress)
        row.addWidget(self.sig_label)
        return foot

    # --- state ----------------------------------------------------------------------------------------------

    def _open_more(self) -> None:
        self.chip_more.setChecked(self._filter in self._more_actions)        # only the menu decides
        self.more_menu.exec(self.chip_more.mapToGlobal(self.chip_more.rect().bottomLeft()))

    def _on_library_activated(self, index: int) -> None:
        self.library_changed.emit(self.library_picker.itemData(index))

    def set_libraries(self, roots: Sequence[Tuple[int, str, str]], current: Optional[int] = None) -> None:
        """The picker's items from ``(root id, name, path)`` of every library folder; *current* (a root id, or None for
        all) is picked again when it is still there. Nothing is emitted. One library folder: no picker."""
        picker = self.library_picker
        picker.blockSignals(True)
        picker.clear()
        picker.addItem(ALL_LIBRARIES, None)
        for root_id, name, path in roots:
            picker.addItem(name, root_id)
            picker.setItemData(picker.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
        found = picker.findData(current) if current is not None else 0
        picker.setCurrentIndex(found if found >= 0 else 0)
        picker.blockSignals(False)
        picker.setVisible(len(roots) > 1)

    def current_library(self) -> Optional[int]:
        return self.library_picker.currentData()

    def library_name(self) -> str:
        """What the picker shows now ("All libraries" or the root's name)."""
        return self.library_picker.currentText()

    def set_library(self, root_id: Optional[int], emit: bool = False) -> None:
        index = self.library_picker.findData(root_id) if root_id is not None else 0
        self.library_picker.blockSignals(True)
        self.library_picker.setCurrentIndex(index if index >= 0 else 0)
        self.library_picker.blockSignals(False)
        if emit:
            self.library_changed.emit(self.current_library())

    def current_filter(self) -> Optional[str]:
        return self._filter

    def set_filter(self, key: Optional[str], emit: bool = False) -> None:
        """Make *key*'s chip the current one (a "More" filter checks the More chip and names itself on it)."""
        self._filter = key
        for k, chip in self.chips.items():
            chip.setChecked(k == key)
        in_more = key in self._more_actions
        self.chip_more.setChecked(in_more)
        self.chip_more.setText(f"{dict(MORE_FILTERS)[key]} ▾" if in_more else "More ▾")
        self.chip_more.set_count(self._counts.get(key) if in_more else None)
        if emit:
            self.filter_changed.emit(key)

    def set_counts(self, counts: Dict[Optional[str], Optional[int]]) -> None:
        """The chips' counts by key (a key missing: no count shown)."""
        self._counts = dict(counts)
        for key, chip in self.chips.items():
            chip.set_count(counts.get(key))
        if self._filter in self._more_actions:
            self.chip_more.set_count(counts.get(self._filter))
        for key, act in self._more_actions.items():
            n = counts.get(key)
            label = dict(MORE_FILTERS)[key]
            act.setText(f"{label}   {n}" if n is not None else label)

    def show_page(self, page: int) -> None:
        self.stack.setCurrentIndex(page)

    def set_duplicates_widget(self, widget: QWidget) -> None:
        self._duplicates_slot.layout().addWidget(widget)

    def set_empty(self, title: str, text: str, *, can_add: bool, busy: bool = False) -> None:
        self.empty_title.setText(title)
        self.empty_text.setText(text)
        self.btn_add_root.setVisible(can_add)
        self.empty_progress.setVisible(busy)
        if busy:
            self.empty_progress.setRange(0, 0)

    def details_visible(self) -> bool:
        return not self.details.isHidden()

    def set_details_visible(self, visible: bool, emit: bool = False) -> None:
        self.details.setVisible(visible)
        self.btn_details.setVisible(not visible)
        if visible:
            sizes = self.splitter.sizes()
            if len(sizes) == 2 and sizes[1] < 200:
                total = sum(sizes) or (DETAILS_WIDTH * 3)
                self.splitter.setSizes([max(total - DETAILS_WIDTH, 200), DETAILS_WIDTH])
        if emit:
            self.details_toggled.emit(visible)

    def set_mu_running(self, running: bool) -> None:
        self.btn_mu_start.setVisible(not running)
        self.btn_mu_start.setEnabled(not running)
        self.btn_mu_stop.setVisible(running)
        self.btn_mu_stop.setEnabled(running)


def chip_label(key: Optional[str]) -> str:
    for k, label in CHIPS:
        if k == key:
            return label
    return dict(MORE_FILTERS).get(key, str(key))


def all_filter_keys() -> Sequence[Optional[str]]:
    return [k for k, _ in CHIPS] + [k for k, _ in MORE_FILTERS]
