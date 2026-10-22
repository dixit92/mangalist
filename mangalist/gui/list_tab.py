"""The List tab's widgets (mockup ``Main.dc.html``): the filter bar (search, the Library picker, state chips with live
counts, Details, MU lookup / Stop), the table - or the duplicates view, or an empty state - beside the collapsible details panel, and
the footer (counts, messages, scan and signing progress).

The table's Title column takes the width the other columns leave (they keep their own, remembered widths), so the
numbers sit beside their labels and Kind and Files stay in view at 1200 px (UI taste review 2026-10-16). The empty state
(no series yet: no library folder, a first scan, nothing found) is an icon, a heading, one sentence and the button; the
filter bar with its chips and the details panel have nothing to say then and stay hidden until there is a library.

A view only: the main window owns the model and every action (it connects to the widgets exposed here)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
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

from ..alias_search import SEARCH_PLACEHOLDER, SEARCH_TIP
from ..states import State
from . import theme
from .chips import ChipButton, FlowLayout
from .detail_panel import DetailPanel
from .list_delegates import ROW_HEIGHT, MonoDelegate, SecondaryDelegate, StateBadgeDelegate, TitleDelegate
from .table_model import (
    COL_ENGLISH, COL_FILES, COL_GAPS, COL_LIBRARY, COL_RENAME, COL_STATE, COL_TITLE, COL_VERDICT, STATE_FILTERS,
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

BUSY_COUNT = "…"                    # a More-menu entry whose count is being worked out
PAGE_TABLE, PAGE_DUPLICATES, PAGE_EMPTY = 0, 1, 2
DETAILS_WIDTH = 380
#: The Title column stretches into what the others leave, but never below this: the table scrolls sideways instead.
TITLE_MIN_WIDTH = 180
EMPTY_ICON_PX = 72

# The empty state's folder (Feather's "folder"), stroked in the accent (``%(ink)s``) on a soft accent circle - drawn in
# code like the top bar's gear and re-drawn on a theme switch.
_FOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
    'stroke="%(ink)s" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
)


def empty_icon(size: int = EMPTY_ICON_PX, ratio: float = 2.0) -> QPixmap:
    """The empty state's icon in the palette in force: a folder in the accent on a circle of the accent's tint."""
    pal = theme.current()
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(pal.accent_tint))
    p.drawEllipse(QRectF(0, 0, pm.width(), pm.height()))
    inner = pm.width() * 0.44
    off = (pm.width() - inner) / 2
    QSvgRenderer(QByteArray((_FOLDER_SVG % {"ink": pal.accent}).encode("utf-8"))).render(
        p, QRectF(off, off, inner, inner))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


class ListTab(QWidget):
    filter_changed = Signal(object)     # a chip's key: None (All), a STATE_FILTERS key, or DUPLICATES
    details_toggled = Signal(bool)
    add_root_clicked = Signal()
    rename_library_clicked = Signal()   # "Rename library…": the library picked (or every library) to the naming scheme

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter: Optional[str] = None
        self._counts: Dict[Optional[str], Optional[int]] = {}
        self._busy_keys: frozenset = frozenset()        # the filters whose count is being worked out (a spinner)
        self._details_wanted = True                     # the owner's choice; the panel also waits for a library
        self._has_library = True                        # False while the empty state shows (no series at all)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.filter_bar = self._build_filter_bar()
        outer.addWidget(self.filter_bar)

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
        self.search.setPlaceholderText(SEARCH_PLACEHOLDER)
        self.search.setToolTip(SEARCH_TIP)
        self.search.setAccessibleName("Filter series")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(200)
        self.search.setMaximumWidth(320)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self.search, 1, Qt.AlignmentFlag.AlignTop)

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
        self.btn_rename = QPushButton("Rename library…")
        self.btn_rename.setToolTip("Bring the file names of the library picked (or of every library) to the naming "
                                   "scheme: a preview first, then renames in batches you can undo")
        self.btn_rename.clicked.connect(self.rename_library_clicked)
        self.btn_mu_start = QPushButton("MU lookup")
        self.btn_mu_start.setToolTip("Look up every series on MangaUpdates (series MangaPixer knows are skipped)")
        self.btn_mu_stop = QPushButton("Stop")
        self.btn_mu_stop.setToolTip("Stop the MangaUpdates lookup")
        self.btn_mu_stop.setVisible(False)
        self.btn_mu_stop.setEnabled(False)
        for b in (self.btn_details, self.btn_rename, self.btn_mu_start, self.btn_mu_stop):
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
        header.setStretchLastSection(False)                 # the Title column fills the rest instead (fit_title_column)
        header.sectionResized.connect(self._on_section_resized)
        table.viewport().installEventFilter(self)
        # by pixel: a column at a time never brought the last column fully into view (owner, 2026-10-11)
        table.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        header.setSectionsMovable(True)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._delegates = {COL_TITLE: TitleDelegate(table), COL_STATE: StateBadgeDelegate(table),
                           COL_GAPS: MonoDelegate(table), COL_FILES: MonoDelegate(table),
                           COL_ENGLISH: SecondaryDelegate(table), COL_VERDICT: SecondaryDelegate(table),
                           COL_LIBRARY: SecondaryDelegate(table), COL_RENAME: MonoDelegate(table)}
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
        self.empty_icon = QLabel()
        self.empty_icon.setObjectName("emptyIcon")
        self.empty_icon.setPixmap(empty_icon())
        self.empty_icon.setFixedSize(EMPTY_ICON_PX, EMPTY_ICON_PX)
        theme.on_retheme(self.empty_icon, lambda: self.empty_icon.setPixmap(empty_icon()))
        col.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(6)
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
        col.addWidget(self.empty_title)
        col.addWidget(self.empty_text)
        col.addSpacing(6)
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
        self.chip_more.set_busy(in_more and key in self._busy_keys)
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
            if key in self._busy_keys:
                act.setText(f"{label}   {BUSY_COUNT}")
            else:
                act.setText(f"{label}   {n}" if n is not None else label)

    def set_busy_counts(self, keys) -> None:
        """The filters (*keys*) whose count is still being worked out in the background (Rename pending, Duplicates:
        the long counts of a subprocess, tens of seconds on a big library): their chips show a spinner in place of the
        number, the "More" menu a "…" - never a count that is not final yet (owner, 2026-10-16, answer 80)."""
        self._busy_keys = frozenset(keys)
        for key, chip in self.chips.items():
            chip.set_busy(key in self._busy_keys)
        self.chip_more.set_busy(self._filter in self._more_actions and self._filter in self._busy_keys)
        self.set_counts(self._counts)

    def show_page(self, page: int) -> None:
        """Show the table, the duplicates view or the empty state. The empty state shows only while there is no series
        at all: the filter bar (the chips all at 0, a search with nothing to search, Rename library / MU lookup with
        nothing to act on) and the details panel ("select a series") hide with it, and come back with the first series -
        the owner's choice for the panel (collapsed or not) is kept meanwhile."""
        self.stack.setCurrentIndex(page)
        has_library = page != PAGE_EMPTY
        if has_library != self._has_library:
            self._has_library = has_library
            self.filter_bar.setVisible(has_library)
            self._apply_details()

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
        """The owner's choice: the panel shown (it still waits for a library: :meth:`show_page`)."""
        return self._details_wanted

    def set_details_visible(self, visible: bool, emit: bool = False) -> None:
        self._details_wanted = bool(visible)
        self._apply_details()
        if emit:
            self.details_toggled.emit(visible)

    def _apply_details(self) -> None:
        shown = self._details_wanted and self._has_library
        self.details.setVisible(shown)
        self.btn_details.setVisible(self._has_library and not self._details_wanted)
        if shown:
            sizes = self.splitter.sizes()
            if len(sizes) == 2 and sizes[1] < 200:
                total = sum(sizes) or (DETAILS_WIDTH * 3)
                self.splitter.setSizes([max(total - DETAILS_WIDTH, 200), DETAILS_WIDTH])

    # --- the columns' geometry ------------------------------------------------------------------------------

    def apply_column_geometry(self, defaults: Dict[int, int], restored: bool) -> None:
        """After the window set the columns (its remembered layout when *restored*, else *defaults*' widths): the Title
        column fills the rest (:meth:`fit_title_column`), the others keep their widths. A layout remembered from before (when the LAST column
        stretched) gets that column's default width back - its width was what the window left over, not a choice;
        every other remembered width wins."""
        header = self.table.horizontalHeader()
        if restored and header.stretchLastSection():
            shown = [header.logicalIndex(v) for v in range(header.count())
                     if not header.isSectionHidden(header.logicalIndex(v))]
            if shown and shown[-1] != COL_TITLE and shown[-1] in defaults:
                header.setStretchLastSection(False)
                header.resizeSection(shown[-1], defaults[shown[-1]])
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        self.fit_title_column()

    def fit_title_column(self) -> None:
        """The Title column takes what the other columns leave of the table's width; when that is under
        ``TITLE_MIN_WIDTH`` (many columns shown, a narrow window) it stays that wide and the table scrolls sideways.

        Done by hand, not with Qt's stretch mode (``gui/tables.py``: a stretched column cannot be dragged and squeezes
        the others until their headers are cut off): every column stays interactive, the others are never squeezed, and
        a drag of the Title column holds until the window or another column changes size."""
        if getattr(self, "table", None) is None:
            return
        header = self.table.horizontalHeader()
        if header.isSectionHidden(COL_TITLE):
            return
        others = sum(header.sectionSize(c) for c in range(header.count())
                     if c != COL_TITLE and not header.isSectionHidden(c))
        width = max(self.table.viewport().width() - others, TITLE_MIN_WIDTH)
        if header.sectionSize(COL_TITLE) != width:
            header.resizeSection(COL_TITLE, width)

    def _on_section_resized(self, logical: int, _old: int, _new: int) -> None:
        if logical != COL_TITLE:                            # (the Title column's own: this fit, or the owner's drag)
            self.fit_title_column()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        table = getattr(self, "table", None)                # (not yet while the table is being built)
        if event.type() == QEvent.Type.Resize and table is not None and obj is table.viewport():
            self.fit_title_column()
        return super().eventFilter(obj, event)

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
