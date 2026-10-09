"""Main application window (UI cycle, owner-approved mockup 2026-10-08): the top bar (List / Download tabs, the
library status, Rescan, Settings), the List tab (filter bar with state chips, the table, the collapsible details
panel, the footer) and the Download tab (lane B's; a placeholder until it merges).

The window owns the model and every action - scans, MangaUpdates lookups, MangaPixer data, the row menu, kind
answers, missing series, signatures; :mod:`.list_tab` and :mod:`.top_bar` only build the widgets."""

from __future__ import annotations

import datetime
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from PySide6.QtCore import (
    QByteArray,
    QModelIndex,
    QObject,
    QPoint,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config, mu_cache, store
from .._version import __version__
from ..models import MangaEntry
from ..scanner import LibraryScan, apply_kind_hint, record_library_scan, scan_library
from ..store import Journal, Root, RootError
from . import lanes
from .app_icon import build_app_icon
from .downloads_backend import create_backend as create_downloads_backend
from .list_tab import DUPLICATES, PAGE_DUPLICATES, PAGE_EMPTY, PAGE_TABLE, ListTab, all_filter_keys, chip_label
from .list_text import wanted_label, wanted_series
from .mu_picker import MuPickerDialog
from .mu_worker import MuWorker, _apply_cache, _clear_examined_if_newly_licensed
from .shell import WantedSeries
from .table_model import (
    COL_BEHIND, COL_DUPE, COL_ENGLISH, COL_EXAMINED, COL_FILES, COL_GAPS, COL_LICENSED, COL_MU_TITLE, COL_OFFICIAL,
    COL_STATE, COL_TITLE, COL_VERDICT, COLUMNS, MangaTableModel, column_label, state_matches,
)
from .top_bar import TAB_DOWNLOAD, TAB_LIST, TopBar
from .links import open_link


# ---------------------------------------------------------------------------
# Background scan worker
# ---------------------------------------------------------------------------


_log = logging.getLogger(__name__)


class _ScanStopped(Exception):
    """The window closed during a scan."""


class ScanWorker(QObject):
    """Scans every root, then records the series rows (a renamed series folder keeps its link)."""

    progress = Signal(int, int, str)
    finished = Signal(object)  # LibraryScan, with .renamed = [(old folder, new folder)]
    failed = Signal(str)

    def __init__(self, roots: Sequence[Root], db=None):
        super().__init__()
        self._roots = list(roots)
        self._db = db
        self._stop = False

    def stop(self) -> None:
        """Abandon the scan at the next folder (nothing is recorded)."""
        self._stop = True

    def _progress(self, done: int, total: int, name: str) -> None:
        if self._stop:
            raise _ScanStopped()
        self.progress.emit(done, total, name)

    def run(self) -> None:
        try:
            result = scan_library(self._roots, progress=self._progress)
        except _ScanStopped:
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if self._stop:
            return
        if not result.entries and result.errors and len(result.errors) == len(self._roots):
            self.failed.emit("\n".join(result.errors))
            return
        result.renamed = []
        if self._db is not None:
            try:
                result.renamed = record_library_scan(self._db, result)
            except Exception:  # noqa: BLE001 - the scan itself is still shown
                _log.warning("Recording the scan in the library database failed", exc_info=True)
        self.finished.emit(result)


class SignatureWorker(QObject):
    """Signs the archives that have no content signature yet, in the background after a scan (a signature
    must exist BEFORE a file moves for the move to be recognised; :mod:`mangalist.identity.backfill`)."""

    progress = Signal(int, int)
    finished = Signal(object)  # BackfillResult or None

    def __init__(self, db):
        super().__init__()
        self._db = db
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..identity.backfill import backfill_signatures

        try:
            res = backfill_signatures(self._db, should_stop=lambda: self._stop,
                                      progress=lambda d, t: self.progress.emit(d, t))
        except Exception:  # noqa: BLE001 - signatures are an optimisation for the next rename
            _log.warning("The content signature backfill failed", exc_info=True)
            res = None
        self.finished.emit(res)


# ---------------------------------------------------------------------------
# Sort proxy that uses Qt.UserRole for sortable values
# ---------------------------------------------------------------------------


class _SortProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(Qt.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)  # filter across all columns
        self._dupes_only = False
        self._state_filter: Optional[str] = None

    def set_dupes_only(self, enabled: bool) -> None:
        """Filter to show only duplicate MU matches."""
        self._dupes_only = enabled
        self.invalidateFilter()

    def set_state_filter(self, key: Optional[str]) -> None:
        """Show only rows whose rescan state passes *key* (table_model.STATE_FILTERS; None = all)."""
        self._state_filter = key
        self.invalidateFilter()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        # The mockup's headers: upper case, numbers right-aligned over their column.
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return column_label(section).upper()
            if role == Qt.TextAlignmentRole and section == COL_FILES:
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return super().headerData(section, orientation, role)

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        """Check if row should be shown based on text filter AND dupe filter."""
        # First apply the standard text filter
        if not super().filterAcceptsRow(source_row, source_parent):
            return False

        source_model = self.sourceModel()
        if self._state_filter is not None and source_model is not None:
            if not state_matches(source_model.state_at(source_row), self._state_filter):
                return False

        # Then apply duplicates-only filter if enabled
        if self._dupes_only:
            if source_model is not None:
                return source_model.is_duplicate(source_row)

        return True


def _when(moment: Optional[datetime.datetime]) -> str:
    """``03:30`` today, ``Oct 7 19:21`` before."""
    if moment is None:
        return ""
    if moment.date() == datetime.date.today():
        return moment.strftime("%H:%M")
    return f"{moment.strftime('%b')} {moment.day} {moment.strftime('%H:%M')}"


def _parse_time(text: Optional[str]) -> Optional[datetime.datetime]:
    """An ISO 8601 time from the database, in local time (None when missing or unreadable)."""
    if not text:
        return None
    try:
        moment = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone() if moment.tzinfo is not None else moment


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    # The theme styles every button; the dialogs that accept a button style get none (gui/theme.py).
    _BUTTON_STYLE = ""
    # The List tab's columns: what shows by default and in which order (the owner's choices are remembered under
    # their own keys, so the old toolbar-era settings do not carry the old 18-column layout over).
    _DEFAULT_COL_ORDER = [
        "Title", "State", "Gaps", "English", "Verdict", "Files",
        "✓", "Dupe", "MU Title", "Behind", "Licensed", "Completed", "Official source", "Alternative Title",
        "Last Modified", "Subfolders", "Vol %", "Ch %", "Both %",
    ]
    _DEFAULT_SHOWN = frozenset({"Title", "State", "Gaps", "English", "Verdict", "Files"})
    _DEFAULT_WIDTHS = {COL_TITLE: 300, COL_STATE: 170, COL_GAPS: 160, COL_ENGLISH: 150, COL_VERDICT: 100,
                       COL_FILES: 80, COL_EXAMINED: 32, COL_DUPE: 130, COL_MU_TITLE: 260, COL_OFFICIAL: 180}
    _CFG_COLUMNS = "list_column_state"
    _CFG_HIDDEN = "list_hidden_columns"
    _CFG_SPLITTER = "list_splitter_sizes"
    _CFG_DETAILS = "details_panel"

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MangaList {__version__}")
        self._app_icon = build_app_icon()
        self.setWindowIcon(self._app_icon)
        QGuiApplication.setWindowIcon(self._app_icon)

        self._cfg = config.load()
        self._db = store.get_store()
        self._recover_journal()
        win = self._cfg.get("window") or {}
        self.resize(int(win.get("w", 1440)), int(win.get("h", 900)))

        self._model = MangaTableModel()
        self._proxy = _SortProxy(self)
        self._proxy.setSourceModel(self._model)
        # MangaPixer source: a folder MangaPixer knows takes its knowledge from the export; the others
        # fall back to the own matcher (knowledge_for returns None).
        self._mp_resolver = None
        self._mp_items: dict = {}   # folder -> the resolved MangaPixer item (or None), per scan
        self._model.set_state_providers(knowledge_for=self._knowledge_for, inventory_for=self._inventory_for,
                                        needs_kind_for=lambda e: bool(getattr(e, "needs_kind", False)))

        # Downloads: only with downloads switched on and a backend (the Download tab and the volumes controller are
        # built in _build_ui).
        self._volumes = None
        self._volumes_backend = self._make_volumes_backend()
        self._download_tab = None
        self._duplicates_view = None
        self._dupe_file_groups: Optional[int] = None
        self._dupe_focus: Optional[str] = None      # "Review duplicate files": the series the view opens on
        self._dupe_call = None
        self._wanted_series: List[WantedSeries] = []
        self._last_scan: Optional[datetime.datetime] = None
        self._scan_label = ""

        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._mu_thread: QThread | None = None
        self._mu_worker: MuWorker | None = None
        self._mu_entries: List[MangaEntry] = []
        self._sig_thread: QThread | None = None
        self._sig_worker: SignatureWorker | None = None
        self._build_ui()

        # Debounce timer so rapid column-resize events don't thrash config I/O.
        self._col_resize_timer = QTimer(self)
        self._col_resize_timer.setSingleShot(True)
        self._col_resize_timer.setInterval(400)
        self._col_resize_timer.timeout.connect(self._save_column_state)

        self._show_roots()
        self._update_missing_count()
        self._refresh_derived()

    # --- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._top = TopBar()
        self._top.tab_changed.connect(self._on_tab_changed)
        self._top.rescan_clicked.connect(self._on_rescan)
        self._top.settings_clicked.connect(self._on_settings)
        self._top.missing_clicked.connect(self._on_missing)
        col.addWidget(self._top)
        self._pages = QStackedWidget()
        col.addWidget(self._pages, 1)
        self.setCentralWidget(central)
        self._btn_rescan = self._top.btn_rescan
        self._btn_missing = self._top.btn_missing
        # "Missing (N)" shows only when N > 0; the action carries that state for the window being hidden too.
        self._missing_action = QAction("Missing series", self)
        self._missing_action.setVisible(False)
        self._missing_action.triggered.connect(self._on_missing)

        # --- the List tab
        lst = ListTab()
        self._list = lst
        self._pages.addWidget(lst)
        self._table = lst.table
        self._table.setModel(self._proxy)
        self._detail = lst.details
        self._filter_edit = lst.search
        self._filter_edit.textChanged.connect(self._proxy.setFilterFixedString)
        self._status_label = lst.status_label
        self._progress = lst.progress
        self._sig_label = lst.sig_label
        self._btn_mu_start = lst.btn_mu_start
        self._btn_mu_stop = lst.btn_mu_stop
        self._btn_mu_start.clicked.connect(self._on_mu_start)
        self._btn_mu_stop.clicked.connect(self._on_mu_stop)
        lst.filter_changed.connect(self._on_filter_changed)
        lst.details_toggled.connect(self._on_details_toggled)
        lst.add_root_clicked.connect(lambda: self._on_choose_root())
        self._detail.get_requested.connect(self._on_get_requested)

        header = self._table.horizontalHeader()
        header.customContextMenuRequested.connect(self._on_header_context_menu)
        self._table.sortByColumn(COL_TITLE, Qt.AscendingOrder)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.clicked.connect(self._on_table_clicked)

        lst.set_details_visible(bool(self._cfg.get(self._CFG_DETAILS, True)))
        saved = self._cfg.get(self._CFG_SPLITTER)
        width = max(self.width(), 900)
        lst.splitter.setSizes([int(s) for s in saved] if saved and len(saved) == 2 else [width - 380, 380])
        lst.splitter.splitterMoved.connect(self._on_splitter_moved)

        # --- the Download tab (lane B's) and the downloads' status
        if self._volumes_backend is not None:
            from .volumes_controller import VolumesController

            self._volumes = VolumesController(self, self._volumes_backend, self._model, self._detail,
                                              self._status_label.setText)
            tab = lanes.download_tab_class()(self._volumes_backend, self)
            tab.count_changed.connect(self._top.set_download_count)
            tab.show_in_list.connect(self.show_folder_in_list)
            self._download_tab = tab
            self._pages.addWidget(tab)
            self._top.set_download_available(True)

        # --- the duplicates view (lane C's)
        view = lanes.duplicates_view_class()(self._db, self)
        view.show_in_list.connect(self.show_folder_in_list)
        view.files_deleted.connect(self._on_files_deleted)
        if hasattr(view, "set_series_link"):
            view.set_series_link(self.mangapixer_series_url)
        lst.set_duplicates_widget(view)
        self._duplicates_view = view

        # Chip counts, the "To get" list and the details' call to action follow the model (debounced).
        self._derived_timer = QTimer(self)
        self._derived_timer.setSingleShot(True)
        self._derived_timer.setInterval(300)
        self._derived_timer.timeout.connect(self._refresh_derived)
        for sig in (self._model.modelReset, self._model.dataChanged, self._model.layoutChanged):
            sig.connect(lambda *_a: self._derived_timer.start())

        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_search)
        QShortcut(QKeySequence(Qt.Key_F5), self, activated=self._on_rescan)

        # Connect selection (rebind in case it returned None earlier)
        sel = self._table.selectionModel()
        if sel is not None:
            sel.selectionChanged.connect(self._on_row_changed)
        self._model.modelReset.connect(self._on_row_changed)     # a reset drops the selection without a signal

        # Restore or apply default column order, then apply visibility.
        self._restore_column_state()
        self._apply_hidden_columns()
        header.sectionMoved.connect(self._on_column_moved)
        header.sectionResized.connect(self._on_section_resized)

    # --- Tabs, Settings, the List tab's state ------------------------------

    def _exec_menu(self, menu: QMenu, global_pos: QPoint):
        """Open *menu* at *global_pos* and return the chosen action (one place, so tests can answer for the owner)."""
        return menu.exec(global_pos)

    def _on_tab_changed(self, tab: int) -> None:
        self._pages.setCurrentIndex(1 if tab == TAB_DOWNLOAD and self._download_tab is not None else 0)

    def show_tab(self, tab: int) -> None:
        self._top.set_current(tab)
        self._on_tab_changed(tab)

    def _focus_search(self) -> None:
        self.show_tab(TAB_LIST)
        self._filter_edit.setFocus()
        self._filter_edit.selectAll()

    def _on_settings(self) -> None:
        from ..identity.carry import last_carry_id

        try:
            before = last_carry_id(self._db)
        except Exception:  # noqa: BLE001
            before = 0
        result = lanes.open_settings_function()(self, self._db, self._volumes_backend)
        self._apply_settings_result(result, before)

    def _apply_settings_result(self, result, carry_before: int = 0) -> None:
        """After the Settings dialog: re-read the settings it may have written, then rescan / re-read MangaPixer /
        reload the downloads as it says."""
        self._cfg = config.load()           # every change this window makes is saved at once: nothing is lost
        if result is None:
            return
        if getattr(result, "mangapixer_changed", False):
            self._after_mangapixer_changed(carry_before)
        if getattr(result, "roots_changed", False):
            self._after_roots_changed(rescan=True)
        if getattr(result, "downloads_changed", False) and self._volumes is not None:
            self._volumes.refresh_records()
            self._rebuild_wanted(force=True)

    def _on_details_toggled(self, visible: bool) -> None:
        self._cfg[self._CFG_DETAILS] = visible
        config.save(self._cfg)

    def _on_filter_changed(self, key) -> None:
        """A chip was picked: All, a state, a "More" filter, or Duplicates."""
        dupes = key == DUPLICATES
        self._proxy.set_state_filter(None if dupes else key)
        self._proxy.set_dupes_only(dupes and self._duplicates_view is None)
        if dupes and self._duplicates_view is not None:
            self._duplicates_view.set_series_duplicates(self._model.duplicate_series())
            if hasattr(self._duplicates_view, "focus_series"):
                self._duplicates_view.focus_series(self._dupe_focus)     # the chip itself: every series
            self._dupe_focus = None
            self._duplicates_view.refresh()
        self._update_page()
        if key is None:
            self._status_label.setText("Showing all entries")
        elif dupes:
            series, numbers = len(self._model.duplicate_series()), self._dupe_file_groups or 0
            self._status_label.setText(f"Duplicates: {series} series in more than one folder, "
                                       f"{numbers} number{'s' if numbers != 1 else ''} held by more than one file")
        else:
            self._status_label.setText(f"Showing {self._proxy.rowCount()} series: {chip_label(key)}")

    def _update_page(self) -> None:
        """The table, the duplicates view, or the empty state (no library / scanning / nothing found)."""
        lst = self._list
        if self._list.current_filter() == DUPLICATES and self._duplicates_view is not None:
            lst.show_page(PAGE_DUPLICATES)
            return
        if self._model.rowCount():
            lst.show_page(PAGE_TABLE)
            return
        roots = self._roots()
        if self._thread is not None:
            lst.set_empty("Scanning your library…", self._scan_label, can_add=False, busy=True)
        elif not roots:
            lst.set_empty("No library folder yet", "Add the folder that holds your series folders. MangaList scans it "
                          "and shows every series here.", can_add=True)
        else:
            names = ", ".join(r.name for r in roots)
            lst.set_empty("No series to show", f"Rescan to read {names}, or add another library folder.",
                          can_add=True)
        lst.show_page(PAGE_EMPTY)

    def _refresh_derived(self) -> None:
        """What follows the table: the chips' counts, the footer, the Download tab's list, the details' button."""
        self._update_counts()
        self._rebuild_wanted()
        self._update_detail_wanted()
        if self._list.current_filter() == DUPLICATES and self._duplicates_view is not None:
            self._duplicates_view.set_series_duplicates(self._model.duplicate_series())

    def _update_counts(self) -> None:
        model = self._model
        n = model.rowCount()
        keys = [k for k in all_filter_keys() if k is not None and k != DUPLICATES]
        counts: Dict[Optional[str], Optional[int]] = {None: n}
        for k in keys:
            counts[k] = 0
        for row in range(n):
            st = model.state_at(row)
            if st is None:
                continue
            for k in keys:
                if state_matches(st, k):
                    counts[k] += 1
        series_dupes = len(model.duplicate_series())
        counts[DUPLICATES] = series_dupes + (self._dupe_file_groups or 0)
        self._list.set_counts(counts)
        entries = [model.entry_at(r) for r in range(n)]
        by_kind = {"Volumes": 0, "Chapters": 0, "Both": 0}
        for e in entries:
            if e is not None and e.verdict.value in by_kind:
                by_kind[e.verdict.value] += 1
        unknown = n - sum(by_kind.values())
        self._list.counts_label.setText(f"{n} series" if n else "")
        self._list.kinds_label.setText(
            f"{by_kind['Volumes']} volume folders · {by_kind['Chapters']} chapter folders · {by_kind['Both']} both · "
            f"{unknown} unknown" if n else "")

    # --- The Download tab's "To get" list ----------------------------------

    def _rebuild_wanted(self, force: bool = False) -> None:
        """Hand the Download tab every series with gaps (after every scan / MangaPixer sync / state change; an
        unchanged list is not sent again, so the tab keeps its selection while MangaUpdates rows come in)."""
        if self._download_tab is None:
            return
        out: List[WantedSeries] = []
        model = self._model
        for row in range(model.rowCount()):
            entry = model.entry_at(row)
            st = model.state_at(row)
            if entry is None or st is None or not st.gaps:
                continue
            volumes = self._volumes.availability(row) if self._volumes is not None and st.missing_volumes else None
            series_id = self._volumes.series_id_for(entry) if self._volumes is not None else None
            out += wanted_series(folder=str(entry.folder), title=entry.title, english_title=entry.english_title,
                                 state=st, knowledge=model.knowledge_at(row), held=model.held_volumes_at(row),
                                 series_id=series_id, volumes=volumes)
        if out == self._wanted_series and not force:
            return
        self._wanted_series = out
        self._download_tab.set_wanted(out)

    def _wanted_groups(self, folder: str) -> List[str]:
        return [w.group for w in self._wanted_series if w.folder == folder]

    def _update_detail_wanted(self) -> None:
        entry = self._current_entry()
        if entry is None or self._download_tab is None:
            self._detail.set_wanted("")
            return
        self._detail.set_wanted(wanted_label(self._wanted_groups(str(entry.folder))))

    def _current_entry(self) -> Optional[MangaEntry]:
        sel = self._table.selectionModel()
        idx = sel.currentIndex() if sel is not None else QModelIndex()
        if not idx.isValid():
            return None
        return self._model.entry_at(self._proxy.mapToSource(idx).row())

    def _on_get_requested(self) -> None:
        entry = self._current_entry()
        if entry is not None:
            self.get_missing(str(entry.folder))

    def get_missing(self, folder: str) -> bool:
        """"Get the missing volumes": switch to the Download tab with *folder*'s series selected."""
        if self._download_tab is None:
            return False
        self._rebuild_wanted()
        self.show_tab(TAB_DOWNLOAD)
        self._download_tab.focus(folder)
        return True

    def _row_for_folder(self, folder: str) -> Optional[int]:
        for r in range(self._model.rowCount()):
            e = self._model.entry_at(r)
            if e is not None and str(e.folder) == str(folder):
                return r
        return None

    def show_folder_in_list(self, folder: str) -> None:
        """Select *folder*'s series in the List tab (from the Download tab or the duplicates view)."""
        self.show_tab(TAB_LIST)
        row = self._row_for_folder(folder)
        if row is not None:
            self._select_source_row(row)

    def _on_files_deleted(self, paths) -> None:
        """Lane C's view deleted duplicate files: rescan so the table and the database follow."""
        self._status_label.setText(f"{len(paths)} duplicate file(s) deleted - rescanning")
        self._start_scan()

    def _count_duplicate_files(self) -> None:
        """Lane C's duplicate-numbers finder, off the UI thread, for the Duplicates chip's count."""
        finder = lanes.find_duplicate_files_function()
        if self._dupe_call is not None:
            return
        from .background import start_call

        db = self._db
        self._dupe_call = start_call(lambda: _count_by_folder(finder(db)), self._on_duplicate_files_counted,
                                     lambda msg: _log.warning("Counting duplicate files failed: %s", msg),
                                     self._on_duplicate_count_finished)

    def _on_duplicate_files_counted(self, counted) -> None:
        total, per_folder = counted
        self._dupe_file_groups = int(total)
        self._model.set_duplicate_file_counts(per_folder)
        self._update_counts()

    def review_duplicates(self, folder: str) -> None:
        """The Duplicates view on *folder*'s series only (its own Apply deletes nothing elsewhere)."""
        self._dupe_focus = folder
        self.show_tab(TAB_LIST)
        self._list.set_filter(DUPLICATES, emit=True)

    def mangapixer_series_url(self, folder: str) -> Optional[str]:
        """The series' page in MangaPixer's web UI (``/series/{nodeId}``, the export item's node), or None when
        MangaPixer does not know the folder or is not connected."""
        row = self._row_for_folder(folder)
        entry = self._model.entry_at(row) if row is not None else None
        item = self._mp_item_for(entry) if entry is not None else None
        node = (item or {}).get("nodeId") if isinstance(item, dict) else None
        if not node:
            return None
        from ..services.mangapixer import open_cache
        from ..services.mangapixer.client import normalize_base_url

        try:
            base = normalize_base_url(open_cache(self._db).connection().base_url)
        except ValueError:
            return None
        return f"{base}/series/{quote(str(node), safe='')}"

    def _on_duplicate_count_finished(self) -> None:
        self._dupe_call = None

    # --- Slots -----------------------------------------------------------

    # --- Roots -----------------------------------------------------------

    def _make_volumes_backend(self):
        """The downloads backend, or None (downloads off, or no adapter): then no Download tab exists."""
        return create_downloads_backend(self._db)

    def _recover_journal(self) -> None:
        """Settle any rename plan a crash interrupted (write-ahead records)."""
        try:
            for plan in Journal(self._db).recover():
                _log.warning("Rename plan %d (%s) was interrupted; it can be resumed or undone",
                             plan.id, plan.reason)
        except Exception:  # noqa: BLE001 - never block start-up
            _log.warning("Journal recovery failed", exc_info=True)

    def _roots(self) -> List[Root]:
        try:
            return self._db.list_roots()
        except Exception:  # noqa: BLE001
            _log.warning("Could not read the roots", exc_info=True)
            return []

    def _mangapixer_synced(self) -> Optional[datetime.datetime]:
        try:
            from ..services.mangapixer import open_cache

            return _parse_time(open_cache(self._db).last_sync_at())
        except Exception:  # noqa: BLE001 - the status line only
            return None

    def _show_roots(self) -> None:
        """The top bar's status: the library folder(s), the last scan, MangaPixer's last sync."""
        roots = self._roots()
        if not roots:
            parts = ["No library folder"]
            tip = "No library folder yet: add one in Settings"
        else:
            parts = [", ".join(r.name for r in roots) if len(roots) <= 2 else f"{len(roots)} library folders"]
            tip = "\n".join(f"{r.name}: {r.path}" for r in roots)
        if self._thread is not None:
            parts.append("scanning…")
        elif self._last_scan is not None:
            parts.append(f"scanned {_when(self._last_scan)}")
        synced = self._mangapixer_synced()
        if synced is not None:
            parts.append(f"MangaPixer synced {_when(synced)}")
        self._top.set_status(" · ".join(parts), tip)
        self._update_page()

    def start_initial_scan(self) -> bool:
        """At start: scan the library folders so the table fills without a click (never blocks the UI)."""
        roots = self._roots()
        if not roots or not any(Path(r.path).is_dir() for r in roots):
            self._update_page()
            return False
        self._start_scan(roots)
        return self._thread is not None

    def _add_root_path(self, d: str) -> bool:
        """Make *d* a root (if it is not one already). False when it cannot be one."""
        try:
            norm = store.roots.normalize_root_path(d)
        except RootError:
            return False
        if any(r.path == norm for r in self._roots()):
            return True
        try:
            self._db.add_root(d)
        except RootError as exc:
            QMessageBox.warning(self, "Cannot add root", str(exc))
            return False
        return True

    def _on_choose_root(self) -> None:
        roots = self._roots()
        start = (roots[-1].path if roots else "") or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Choose Manga Root", start)
        if not d:
            return
        if not self._add_root_path(d):
            return
        self._cfg["last_root"] = d
        config.save(self._cfg)
        self._show_roots()
        self._start_scan()

    def _after_mangapixer_changed(self, carry_before: int) -> None:
        """The connection, the mappings or the synced items may have changed: re-read MangaPixer's data."""
        from ..identity.carry import carries_since

        self._mp_resolver = None
        self._mp_items = {}
        self._model.refresh_states()
        # A sync may have carried missing series along MangaPixer's carriedFrom.
        try:
            self._apply_identity_changes(carries_since(self._db, carry_before))
        except Exception:  # noqa: BLE001
            _log.warning("Reading MangaPixer's carried series failed", exc_info=True)
        self._update_missing_count()
        self._show_roots()

    def _mp_item_for(self, entry: MangaEntry):
        """The MangaPixer export item that applies to *entry*'s folder (its own or an ancestor's), or
        None; resolved once per scan / sync."""
        key = str(entry.folder)
        if key in self._mp_items:
            return self._mp_items[key]
        item = None
        if entry.root_id is not None:
            try:
                if self._mp_resolver is None:
                    from ..services.mangapixer import open_cache
                    from ..services.mangapixer.resolve import Resolver

                    self._mp_resolver = Resolver(open_cache(self._db))
                root = self._db.get_root(entry.root_id)
                if root is not None:
                    rel = Path(entry.folder).resolve().relative_to(Path(root.path).resolve()).as_posix()
                    res = self._mp_resolver.resolve(entry.root_id, rel)
                    item = res.item if res is not None else None
            except Exception:  # noqa: BLE001 - a broken MangaPixer cache must not break the table
                _log.warning("MangaPixer data for %s unavailable", entry.folder, exc_info=True)
        self._mp_items[key] = item
        return item

    def _knowledge_for(self, entry: MangaEntry):
        """MangaPixer's knowledge for *entry* when its folder (or an ancestor) has an exported link,
        else None (the table then uses the own matcher's)."""
        item = self._mp_item_for(entry)
        if item is None:
            return None
        from ..knowledge import from_mangapixer_item

        return from_mangapixer_item(item)

    def _inventory_for(self, entry: MangaEntry):
        """The series' held units for the state columns, merged with MangaPixer's volume list (which
        chapters each volume collects) when MangaPixer knows the series."""
        from ..inventory import as_state_inventory

        item = self._mp_item_for(entry) or {}
        volumes = item.get("volumes") if isinstance(item.get("volumes"), dict) else None
        volume_list = volumes.get("items") if volumes else None
        return as_state_inventory(entry.inventory(volume_list or None))

    def _after_roots_changed(self, rescan: bool = False) -> None:
        self._show_roots()
        if rescan and self._roots():
            self._start_scan()
        elif self._roots() and self._model.rowCount():
            self._status_label.setText("Library folders changed - Rescan to apply")

    def _on_rescan(self) -> None:
        if not self._roots():
            QMessageBox.information(self, "No library folder", "Add a library folder first (Settings).")
            return
        self._start_scan()

    def _start_scan(self, roots: Optional[Sequence[Root]] = None) -> None:
        if self._thread is not None:
            return  # scan already running
        roots = list(roots) if roots is not None else self._roots()
        if not roots:
            return
        if not any(Path(r.path).is_dir() for r in roots):
            QMessageBox.warning(self, "Invalid folder",
                                "Not a directory:\n" + "\n".join(r.path for r in roots))
            return

        self._btn_rescan.setEnabled(False)
        label = roots[0].path if len(roots) == 1 else f"{len(roots)} roots"
        self._scan_label = label
        self._status_label.setText(f"Scanning {label}…")
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # busy until first progress update

        thread = QThread(self)
        worker = ScanWorker(roots, self._db)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()
        self._show_roots()
        if self._duplicates_view is not None and hasattr(self._duplicates_view, "set_blocked"):
            self._duplicates_view.set_blocked("the library is being rescanned")

    def _on_progress(self, done: int, total: int, name: str) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        if name:
            self._status_label.setText(f"Scanning ({done}/{total}): {name}")
            self._list.empty_text.setText(f"{done} / {total}: {name}")

    def _on_scan_finished(self, result) -> None:
        if isinstance(result, LibraryScan):
            entries: List[MangaEntry] = result.entries
            loose = result.loose
            errors = result.errors
            renamed = getattr(result, "renamed", [])
        else:  # a plain list of entries
            entries, loose, errors, renamed = list(result), [], [], []
        self._reload_examined()                     # the scan may have carried marks with series
        if self._volumes is not None:
            self._volumes.forget_series_ids()
        if renamed:
            # A renamed series folder keeps its "examined" mark, like its MangaUpdates link.
            moved = {str(old): str(new) for old, new in renamed}
            self._cfg["examined"] = [moved.get(str(p), str(p)) for p in self._cfg.get("examined", [])]
            config.save(self._cfg)
        # Re-apply examined flags from config before showing.
        examined_set = {str(p) for p in self._cfg.get("examined", [])}
        for e in entries:
            e.examined = str(e.folder) in examined_set

        # Pre-fill any cached MU data so columns aren't blank while worker runs.
        cached_all = mu_cache.load_all()
        for e in entries:
            cached = cached_all.get(str(e.folder))
            if cached:
                _apply_cache(e, cached)

        self._mp_resolver = None  # folders may have been renamed or added
        self._mp_items = {}
        self._model.set_entries(entries)
        self._last_scan = datetime.datetime.now()

        # The counts are in the footer (_update_counts); the message says what else the scan found.
        text = f"Scanned {len(entries)} folder(s)"
        tips = []
        if loose:
            text += f"  —  {len(loose)} archive(s) not in a series folder"
            tips.append("Not in a series folder (never matched):")
            tips += [f"  {la.root_name}: {la.path.name}" for la in loose[:50]]
            if len(loose) > 50:
                tips.append(f"  … and {len(loose) - 50} more")
            for la in loose:
                _log.info("Not in a series folder: %s", la.path)
        if renamed:
            text += f"  —  {len(renamed)} renamed / moved series kept their data"
        if errors:
            text += f"  —  {len(errors)} root(s) not reachable"
            tips.append("Not reachable:")
            tips += [f"  {e}" for e in errors]
        self._status_label.setText(text)
        self._status_label.setToolTip("\n".join(tips))
        self._progress.setVisible(False)
        self._mu_entries = list(entries)
        self._update_missing_count()
        self._refresh_derived()
        self._show_roots()
        self._count_duplicate_files()
        if self._duplicates_view is not None and self._list.current_filter() == DUPLICATES:
            self._duplicates_view.refresh()
        self._start_signatures()

        if self._cfg.get("mu_autostart"):
            self._start_mu_lookup(self._mu_entries)

    def _on_scan_failed(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._status_label.setText("Scan failed")
        QMessageBox.critical(self, "Scan failed", msg)

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._btn_rescan.setEnabled(True)
        self._progress.setVisible(False)
        self._show_roots()
        if self._duplicates_view is not None and hasattr(self._duplicates_view, "set_blocked"):
            self._duplicates_view.set_blocked(None)

    def _stop_scan(self, wait_ms: int = 5000) -> None:
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(wait_ms)


    # --- Series identity: missing series, background signatures ----------

    def _update_missing_count(self) -> int:
        """Show "Missing (N)" in the top bar when N > 0."""
        try:
            from ..identity.carry import missing_count

            n = missing_count(self._db)
        except Exception:  # noqa: BLE001
            _log.warning("Could not count the missing series", exc_info=True)
            n = 0
        self._top.set_missing(n)
        self._missing_action.setVisible(n > 0)
        return n

    def _make_missing_dialog(self):
        from .missing_series_dialog import MissingSeriesDialog

        return MissingSeriesDialog(self._db, self, button_style=self._BUTTON_STYLE)

    def _on_missing(self) -> None:
        dlg = self._make_missing_dialog()
        dlg.exec()
        self._after_missing_dialog(dlg)

    def _after_missing_dialog(self, dlg) -> None:
        if dlg.changed:
            self._apply_identity_changes(dlg.renamed, dropped=dlg.forgotten)
        self._update_missing_count()

    def _reload_examined(self) -> None:
        """Take the examined marks from the database (carry-over / re-attach / forget move them there)."""
        try:
            stored = self._db.get_setting("examined", None)
        except Exception:  # noqa: BLE001
            return
        if isinstance(stored, list):
            self._cfg["examined"] = [str(p) for p in stored]

    def _apply_identity_changes(self, renamed, dropped=()) -> None:
        """Series data moved to *renamed* folders (``(old, new)``) outside a scan (re-attach, background
        carry-over, MangaPixer): reload their MangaUpdates data, examined mark and kind answer in the table."""
        self._reload_examined()
        moved = {str(old): str(new) for old, new in renamed or ()}
        if any(p in moved for p in self._cfg.get("examined", [])):
            # A settings save from this window may have raced the database's move: apply it here too.
            self._cfg["examined"] = sorted({moved.get(p, p) for p in self._cfg.get("examined", [])})
            config.save(self._cfg)
        targets = set(moved.values())
        examined = set(self._cfg.get("examined", []))
        entries = [self._model.entry_at(r) for r in range(self._model.rowCount())]
        if not entries:
            return
        cached_all = mu_cache.load_all()
        changed = False
        for e in entries:
            key = str(e.folder)
            if e.examined != (key in examined):
                e.examined = key in examined
                changed = True
            if key not in targets:
                continue
            cached = cached_all.get(key)
            if cached:
                _apply_cache(e, cached)
            try:
                located = self._db._locate(e.folder)
                row = self._db.get_series(*located) if located else None
                if row is not None and (row.kind_hint or None) != (e.kind_hint or None):
                    root = self._db.get_root(located[0])
                    scheme = getattr(root, "naming_scheme", None)
                    apply_kind_hint(e, row.kind_hint, (scheme,) if isinstance(scheme, str) and scheme.strip() else ())
            except Exception:  # noqa: BLE001
                _log.warning("Refreshing %s after a re-attach failed", e.folder, exc_info=True)
            changed = True
        if changed:
            self._mp_resolver = None
            self._mp_items = {}
            self._model.set_entries(entries)

    def _start_signatures(self) -> None:
        """Sign new archives in the background (never blocks the UI; progress in the status bar)."""
        if self._sig_thread is not None:
            return
        try:
            if self._db.unsigned_count() == 0:
                return
        except Exception:  # noqa: BLE001
            return
        thread = QThread(self)
        worker = SignatureWorker(self._db)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_signature_progress)
        worker.finished.connect(self._on_signatures_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_signature_thread_finished)
        self._sig_thread = thread
        self._sig_worker = worker
        self._sig_label.setText("Signing archives…")
        self._sig_label.setVisible(True)
        thread.start()

    def _on_signature_progress(self, done: int, total: int) -> None:
        self._sig_label.setText(f"Signing archives {done}/{total}")

    def _on_signatures_finished(self, res) -> None:
        self._sig_label.setVisible(False)
        if res is None:
            return
        carried = res.renamed
        if carried:
            self._apply_identity_changes(carried)
            self._status_label.setText(self._status_label.text() +
                                       f"  —  {len(carried)} renamed / moved series recognised after signing")
        self._update_missing_count()

    def _on_signature_thread_finished(self) -> None:
        self._sig_thread = None
        self._sig_worker = None

    def _stop_signatures(self, wait_ms: int = 3000) -> None:
        if self._sig_worker is not None:
            self._sig_worker.stop()
        if self._sig_thread is not None:
            self._sig_thread.quit()
            self._sig_thread.wait(wait_ms)

    # --- MangaUpdates background lookup ----------------------------------

    def _start_mu_lookup(self, entries: List[MangaEntry]) -> None:
        """Start a background worker to enrich *entries* with MU data."""
        if self._mu_thread is not None:
            self._mu_worker.abort()
            self._mu_thread = None
            self._mu_worker = None

        if not entries:
            return
        # Owner decision (MangaPixer Data Source, 2026-10-02): for folders MangaPixer knows, MangaList
        # fetches nothing itself - their knowledge comes from the MangaPixer export.
        known = [e for e in entries if self._mp_item_for(e) is not None]
        if known:
            entries = [e for e in entries if self._mp_item_for(e) is None]
            self._status_label.setText(
                f"{len(known)} folder(s) skipped: MangaPixer knows them (sync MangaPixer to refresh)")
            if not entries:
                return

        # Build (source_row, entry) pairs — find the row of each entry in the model.
        folder_to_row = {
            str(self._model.entry_at(r).folder): r
            for r in range(self._model.rowCount())
            if self._model.entry_at(r) is not None
        }
        pairs = [(folder_to_row[str(e.folder)], e)
                 for e in entries if str(e.folder) in folder_to_row]
        if not pairs:
            return

        worker = MuWorker(pairs)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.entry_started.connect(self._on_mu_entry_started)
        worker.entry_updated.connect(self._on_mu_entry_updated)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_mu_thread_finished)
        self._mu_thread = thread
        self._mu_worker = worker
        self._list.set_mu_running(True)
        thread.start()

    def _on_mu_entry_started(self, row: int) -> None:
        """Highlight the row currently being fetched from MangaUpdates."""
        self._model.set_mu_processing_row(row)

    def _clear_mu_processing_row(self, row: int) -> None:
        self._model.set_mu_processing_row(None)

    def _on_mu_entry_updated(self, entry: MangaEntry, row: int) -> None:
        """Called from the MU worker thread via signal; refreshes one row."""
        self._clear_mu_processing_row(row)
        left = self._model.index(row, 0)
        right = self._model.index(row, self._model.columnCount() - 1)
        self._model.dataChanged.emit(left, right, [Qt.DisplayRole, Qt.BackgroundRole,
                                                    Qt.ForegroundRole, Qt.ToolTipRole,
                                                    Qt.UserRole])

    def _resize_mu_columns(self) -> None:
        """Resize MU-populated columns to fit content, leaving Title/Examined alone."""
        for col in (COL_MU_TITLE, COL_LICENSED, COL_BEHIND, COL_STATE, COL_GAPS, COL_OFFICIAL):
            self._table.resizeColumnToContents(col)

    def _on_mu_thread_finished(self) -> None:
        self._model.set_mu_processing_row(None)
        self._mu_thread = None
        self._mu_worker = None
        self._list.set_mu_running(False)

    def _on_mu_start(self) -> None:
        if not self._mu_entries:
            QMessageBox.information(self, "No data", "Scan a folder first.")
            return
        self._start_mu_lookup(self._mu_entries)

    def _on_mu_stop(self) -> None:
        if self._mu_worker is not None:
            self._mu_worker.abort()
        self._list.set_mu_running(False)

    def _stop_mu(self, wait_ms: int = 5000) -> None:
        if self._mu_worker is not None:
            self._mu_worker.abort()
        if self._mu_thread is not None:
            self._mu_thread.quit()
            self._mu_thread.wait(wait_ms)

    # --- Selection from elsewhere -------------------------------------------

    def _select_source_row(self, src_row: int) -> None:
        """Select a series in the table (clearing the filters that hide it, leaving the duplicates view)."""
        src = self._model.index(src_row, COL_TITLE)
        idx = self._proxy.mapFromSource(src)
        if not idx.isValid() or self._list.current_filter() == DUPLICATES:
            self._list.set_filter(None)
            self._on_filter_changed(None)
            self._filter_edit.clear()
            idx = self._proxy.mapFromSource(src)
        if idx.isValid():
            self._table.selectRow(idx.row())
            self._table.scrollTo(idx)

    def _open_url(self, url: str) -> None:
        if not open_link(url):
            self._status_label.setText(f"No browser here - link copied: {url}")

    # --- Column order & state --------------------------------------------

    def _apply_default_column_order(self) -> None:
        """Move header sections to match _DEFAULT_COL_ORDER."""
        header = self._table.horizontalHeader()
        for visual_idx, col_name in enumerate(self._DEFAULT_COL_ORDER):
            if col_name not in COLUMNS:
                continue
            logical_idx = COLUMNS.index(col_name)
            current_visual = header.visualIndex(logical_idx)
            if current_visual != visual_idx:
                header.moveSection(current_visual, visual_idx)

    def _restore_column_state(self) -> None:
        """Restore saved header state, or apply the default order and widths."""
        state_hex = self._cfg.get(self._CFG_COLUMNS) or ""
        header = self._table.horizontalHeader()
        if state_hex:
            try:
                ok = header.restoreState(QByteArray.fromHex(state_hex.encode()))
                if ok and header.count() == len(COLUMNS):
                    # Re-apply stretch/movable settings after restore (Qt may reset them)
                    header.setStretchLastSection(True)
                    header.setSectionsMovable(True)
                    return
                # Section count mismatch (e.g. new column added) — discard stale state.
                self._cfg[self._CFG_COLUMNS] = ""
                config.save(self._cfg)
            except Exception:  # noqa: BLE001
                pass
        self._apply_default_column_order()
        for col, width in self._DEFAULT_WIDTHS.items():
            header.resizeSection(col, width)
        # Ensure settings are applied after default order too
        header.setStretchLastSection(True)
        header.setSectionsMovable(True)

    def _save_column_state(self) -> None:
        state = self._table.horizontalHeader().saveState()
        self._cfg[self._CFG_COLUMNS] = bytes(state.toHex()).decode()
        config.save(self._cfg)

    def _on_column_moved(self, _logical: int, _old: int, _new: int) -> None:
        self._save_column_state()

    def _on_section_resized(self, _logical: int, _old: int, _new: int) -> None:
        self._col_resize_timer.start()

    def _on_splitter_moved(self, _pos: int, _idx: int) -> None:
        self._cfg[self._CFG_SPLITTER] = self._list.splitter.sizes()
        config.save(self._cfg)

    # --- Column visibility -----------------------------------------------

    def _hidden_columns(self) -> set:
        """The columns the owner hid (by name); the mockup's trimmed set until the owner changes it."""
        hidden = self._cfg.get(self._CFG_HIDDEN)
        if not isinstance(hidden, list):
            return {name for name in COLUMNS if name not in self._DEFAULT_SHOWN}
        return set(hidden)

    def _apply_hidden_columns(self) -> None:
        """Hide/show columns according to config."""
        hidden = self._hidden_columns()
        header = self._table.horizontalHeader()
        for col, name in enumerate(COLUMNS):
            header.setSectionHidden(col, name in hidden)

    def _on_header_context_menu(self, pos: QPoint) -> None:
        """Show / hide columns (remembered); the title column always stays."""
        menu = QMenu(self._table.horizontalHeader())
        hidden = self._hidden_columns()
        order = sorted(range(len(COLUMNS)), key=self._table.horizontalHeader().visualIndex)
        for col in order:
            name = COLUMNS[col]
            act = menu.addAction(column_label(col, menu=True))
            act.setEnabled(col != COL_TITLE)
            act.setCheckable(True)
            act.setChecked(name not in hidden)
            act.setData(col)
        chosen = self._exec_menu(menu, self._table.horizontalHeader().mapToGlobal(pos))
        if chosen is None:
            return
        self._toggle_column(chosen.data())

    def _toggle_column(self, col: int) -> None:
        hidden = self._hidden_columns()
        name = COLUMNS[col]
        if name in hidden:
            hidden.discard(name)
        else:
            hidden.add(name)
        self._cfg[self._CFG_HIDDEN] = sorted(hidden)
        config.save(self._cfg)
        self._apply_hidden_columns()

    def _on_row_changed(self, *_args) -> None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            self._detail.show_entry(None)
            if self._volumes is not None:
                self._volumes.show_in_detail(None)
            return
        src_index: QModelIndex = self._proxy.mapToSource(idx)
        row = src_index.row()
        entry = self._model.entry_at(row)
        m = self._model
        self._detail.show_entry(entry, m.state_at(row), m.links_at(row), knowledge=m.knowledge_at(row),
                                held_volumes=m.held_volumes_at(row), held_chapters=m.held_chapters_at(row))
        self._update_detail_wanted()
        if self._volumes is not None:
            self._volumes.show_in_detail(row)

    # --- Context menu ----------------------------------------------------

    def _entry_at_view_row(self, view_row: int) -> MangaEntry | None:
        proxy_index = self._proxy.index(view_row, 0)
        if not proxy_index.isValid():
            return None
        src_index = self._proxy.mapToSource(proxy_index)
        return self._model.entry_at(src_index.row())

    def _selected_source_rows(self) -> List[int]:
        """Return source-model row indices for all selected rows."""
        sel = self._table.selectionModel()
        if sel is None:
            return []
        rows = set()
        for idx in sel.selectedRows():
            rows.add(self._proxy.mapToSource(idx).row())
        return sorted(rows)

    def _on_context_menu(self, pos: QPoint) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return

        # Make sure the row under the cursor is part of the selection so
        # right-click on an unselected row operates on that single row.
        sel = self._table.selectionModel()
        if sel is not None and not sel.isSelected(index):
            self._table.selectRow(index.row())

        rows = self._selected_source_rows()
        if not rows:
            return
        entries = [self._model.entry_at(r) for r in rows]
        entries = [e for e in entries if e is not None]
        if not entries:
            return

        n = len(entries)
        n_examined = sum(1 for e in entries if e.examined)
        n_with_mu = sum(1 for e in entries if e.mu_id is not None)
        n_confirmed = sum(1 for e in entries if e.mu_confirmed)
        n_unconfirmed = sum(1 for e in entries if e.mu_id is not None and not e.mu_confirmed)
        n_overridable = sum(1 for e in entries if e.mu_id is not None and e.behind_override != "done")
        n_overridden = sum(1 for e in entries if e.behind_override == "done")

        menu = QMenu(self._table)
        act_kind = None
        act_get = None
        act_dupes = act_mangapixer = None

        if n == 1:
            if getattr(entries[0], "needs_kind", False):
                act_kind = menu.addAction("Volumes or chapters?…")
                act_kind.setToolTip("Its files are bare numbers: say once whether they are volumes or chapters")
                menu.addSeparator()
            act_open = menu.addAction("Open folder in Explorer")
            menu.addSeparator()
            act_copy_path = menu.addAction("Copy folder path")
            act_copy_title = menu.addAction("Copy title")
            menu.addSeparator()
            act_fix_mu = menu.addAction("Fix MangaUpdates match…")
            entry0 = entries[0]
            act_open_mu = menu.addAction("Open MangaUpdates page")
            act_open_mu.setEnabled(bool(entry0.mu_url))
            act_check_mu = menu.addAction("Check MU for this entry")
            if self._download_tab is not None:
                groups = self._wanted_groups(str(entry0.folder))
                act_get = menu.addAction((wanted_label(groups) or "Get the missing volumes") + "…")
                act_get.setEnabled(bool(groups))
                act_get.setToolTip("Open this series in the Download tab" if groups else
                                   "Nothing missing: no gaps to download")
                menu.setToolTipsVisible(True)
            vols, chs = self._model.duplicate_files_at(rows[0])
            if vols or chs:
                act_dupes = menu.addAction(f"Review duplicate files ({vols + chs})…")
                act_dupes.setToolTip("The Duplicates view on this series only: keep or discard its extra copies")
            mp_url = self.mangapixer_series_url(str(entry0.folder))
            if mp_url:
                act_mangapixer = menu.addAction("Open in MangaPixer")
            links_menu = menu.addMenu("Official sources")
            link_actions = {}
            for link in self._model.links_at(rows[0]):
                act = links_menu.addAction(link.label if link.url else f"{link.label} (no page known)")
                act.setEnabled(bool(link.url))
                link_actions[act] = link.url
            links_menu.setEnabled(bool(link_actions))
            menu.addSeparator()
        else:
            act_open = act_copy_path = act_copy_title = act_fix_mu = act_open_mu = None
            link_actions = {}
            menu.addAction(f"{n} folders selected").setEnabled(False)
            menu.addSeparator()
            act_check_mu = menu.addAction(f"Check MU for {n} selected entries")
            menu.addSeparator()

        act_confirm_mu = menu.addAction(
            "Confirm MU match" if n == 1 else f"Confirm MU match ({n_unconfirmed})"
        )
        act_confirm_mu.setEnabled(n_unconfirmed > 0)
        act_unconfirm_mu = menu.addAction(
            "Un-confirm MU match" if n == 1 else f"Un-confirm MU match ({n_confirmed})"
        )
        act_unconfirm_mu.setEnabled(n_confirmed > 0)
        act_clear_mu = menu.addAction(
            "Clear MU match" if n == 1 else f"Clear MU match ({n_with_mu})"
        )
        act_clear_mu.setEnabled(n_with_mu > 0)

        menu.addSeparator()
        act_mark_behind_done = menu.addAction(
            "Mark Behind as up to date" if n == 1 else f"Mark Behind as up to date ({n_overridable})"
        )
        act_mark_behind_done.setEnabled(n_overridable > 0)
        act_clear_behind_override = menu.addAction(
            "Clear 'up to date' override" if n == 1 else f"Clear 'up to date' override ({n_overridden})"
        )
        act_clear_behind_override.setEnabled(n_overridden > 0)

        menu.addSeparator()
        act_mark = menu.addAction(
            "Mark as examined" if n == 1 else f"Mark all {n} as examined"
        )
        act_unmark = menu.addAction(
            "Mark as not examined" if n == 1 else f"Reset examined on {n} folders"
        )
        act_toggle = menu.addAction("Toggle examined")
        # Sensible enable/disable hints
        act_mark.setEnabled(n_examined < n)
        act_unmark.setEnabled(n_examined > 0)

        chosen = self._exec_menu(menu, self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        if act_kind is not None and chosen is act_kind:
            from .kind_dialog import ask_series_kind

            if ask_series_kind(self, entries[0], db=self._db):
                self._model.refresh_states()
        elif act_dupes is not None and chosen is act_dupes:
            self.review_duplicates(str(entries[0].folder))
        elif act_mangapixer is not None and chosen is act_mangapixer:
            self._open_url(self.mangapixer_series_url(str(entries[0].folder)) or "")
        elif chosen in link_actions and link_actions[chosen]:
            self._open_url(link_actions[chosen])
        elif chosen is act_open and entries:
            self._open_in_explorer(entries[0].folder)
        elif chosen is act_get and act_get is not None:
            self.get_missing(str(entries[0].folder))
        elif chosen is act_check_mu:
            self._start_mu_lookup(entries)
        elif chosen is act_confirm_mu:
            for r, e in zip(rows, entries):
                if e.mu_id is not None and not e.mu_confirmed:
                    if self._model.set_mu_confirmed(r, True):
                        mu_cache.set_mu_confirmed(e.folder, True)
        elif chosen is act_unconfirm_mu:
            for r, e in zip(rows, entries):
                if e.mu_confirmed:
                    if self._model.set_mu_confirmed(r, False):
                        mu_cache.set_mu_confirmed(e.folder, False)
        elif chosen is act_clear_mu:
            for r, e in zip(rows, entries):
                if e.mu_id is not None:
                    if self._model.clear_mu_match(r):
                        mu_cache.delete_entry(e.folder)
        elif chosen is act_mark_behind_done:
            for r, e in zip(rows, entries):
                if e.mu_id is not None and e.behind_override != "done":
                    e.behind_override = "done"
                    mu_cache.set_behind_override(e.folder, "done")
                    self._model.dataChanged.emit(
                        self._model.index(r, COL_BEHIND),
                        self._model.index(r, COL_BEHIND),
                        [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole],
                    )
        elif chosen is act_clear_behind_override:
            for r, e in zip(rows, entries):
                if e.behind_override == "done":
                    e.behind_override = None
                    mu_cache.set_behind_override(e.folder, None)
                    self._model.dataChanged.emit(
                        self._model.index(r, COL_BEHIND),
                        self._model.index(r, COL_BEHIND),
                        [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole],
                    )
        elif chosen is act_fix_mu and entries:
            self._on_fix_mu_match(rows[0], entries[0])
        elif chosen is act_open_mu and entries and entries[0].mu_url:
            self._open_url(entries[0].mu_url)
        elif chosen is act_copy_path and entries:
            QGuiApplication.clipboard().setText(str(entries[0].folder))
            self._status_label.setText(f"Copied path: {entries[0].folder}")
        elif chosen is act_copy_title and entries:
            QGuiApplication.clipboard().setText(entries[0].title)
            self._status_label.setText(f"Copied title: {entries[0].title}")
        elif chosen is act_mark:
            self._set_examined_for_rows(rows, True)
        elif chosen is act_unmark:
            self._set_examined_for_rows(rows, False)
        elif chosen is act_toggle:
            # Per-row toggle.
            for r in rows:
                e = self._model.entry_at(r)
                if e is not None:
                    self._model.set_examined(r, not e.examined)
            self._persist_examined()

    def _on_fix_mu_match(self, src_row: int, entry: MangaEntry) -> None:
        """Open the MU picker so the user can manually select the correct series."""
        from ..mu_client import search_series
        from .mu_worker import _apply_progress, _detail_progress
        query = entry.english_title or entry.title
        try:
            candidates = search_series(query, page_size=15)
        except Exception:  # noqa: BLE001
            candidates = []

        dlg = MuPickerDialog(query, candidates, parent=self)
        if dlg.exec() != MuPickerDialog.Accepted or dlg.selected is None:
            return

        rec = dlg.selected
        mu_id = rec.get("series_id")
        mu_title = rec.get("title") or entry.title
        mu_url = rec.get("url") or ""

        # Fetch full detail: licensed flag + publisher/scan progress.
        from .. import mu_client
        licensed = None
        detail = None
        try:
            detail = mu_client.get_series(mu_id)
            licensed = detail.get("licensed")
        except Exception:  # noqa: BLE001
            pass

        progress = _detail_progress(detail)

        # Clear examined if becoming licensed
        _clear_examined_if_newly_licensed(entry, licensed)
        entry.mu_id = mu_id
        entry.mu_title = mu_title
        entry.mu_url = mu_url
        entry.licensed = licensed
        entry.mu_confirmed = True
        # A manual pick is not scored by the matcher: no score, band or reasons.
        entry.mu_score = 0.0
        entry.mu_score_version = mu_cache.MU_SCORE_VERSION
        entry.mu_band = None
        entry.mu_reasons = []
        _apply_progress(entry, progress)

        mu_cache.save_entry(
            entry.folder, mu_id, mu_title, mu_url, licensed,
            mu_confirmed=True, **progress,
        )

        left = self._model.index(src_row, 0)
        right = self._model.index(src_row, self._model.columnCount() - 1)
        self._model.dataChanged.emit(left, right, [Qt.DisplayRole, Qt.BackgroundRole,
                                                    Qt.ForegroundRole, Qt.ToolTipRole,
                                                    Qt.UserRole])

    def _set_examined_for_rows(self, rows: List[int], examined: bool) -> None:
        changed = False
        for r in rows:
            if self._model.set_examined(r, examined):
                changed = True
        if changed:
            self._persist_examined()

    def _persist_examined(self) -> None:
        paths = []
        for r in range(self._model.rowCount()):
            e = self._model.entry_at(r)
            if e is not None and e.examined:
                paths.append(str(e.folder))
        # Preserve any examined entries from other roots not currently loaded.
        existing = {str(p) for p in self._cfg.get("examined", [])}
        loaded_paths = {str(self._model.entry_at(r).folder)
                        for r in range(self._model.rowCount())
                        if self._model.entry_at(r) is not None}
        # Drop loaded paths from existing, then re-add only the currently examined ones.
        merged = (existing - loaded_paths) | set(paths)
        self._cfg["examined"] = sorted(merged)
        config.save(self._cfg)

    def _on_table_clicked(self, proxy_index: QModelIndex) -> None:
        """Single-click on the ✓ column toggles examined."""
        if not proxy_index.isValid():
            return
        col = proxy_index.column()
        src_row = self._proxy.mapToSource(proxy_index).row()
        entry = self._model.entry_at(src_row)
        if entry is None:
            return

        if col == COL_EXAMINED:
            target = not entry.examined
            sel_rows = self._selected_source_rows()
            rows = sel_rows if src_row in sel_rows and len(sel_rows) > 1 else [src_row]
            self._set_examined_for_rows(rows, target)

    def _on_double_click(self, proxy_index: QModelIndex) -> None:
        """Double-click on MU Title toggles mu_confirmed.
        Double-click anywhere else opens the folder in Explorer.
        """
        if not proxy_index.isValid():
            return
        col = proxy_index.column()
        src_row = self._proxy.mapToSource(proxy_index).row()
        entry = self._model.entry_at(src_row)
        if entry is None:
            return

        if col == COL_MU_TITLE and entry.mu_title is not None:
            new_confirmed = not entry.mu_confirmed
            if self._model.set_mu_confirmed(src_row, new_confirmed):
                mu_cache.set_mu_confirmed(entry.folder, new_confirmed)
            return

        if entry is not None:
            self._open_in_explorer(entry.folder)

    def _open_in_explorer(self, path: Path) -> None:
        path = Path(path)
        try:
            if path.is_dir():
                import os
                os.startfile(str(path))  # noqa: S606  (Windows-only, intentional)
            else:
                subprocess.Popen(["explorer", str(path)])
        except OSError as exc:
            QMessageBox.warning(self, "Open in Explorer failed", str(exc))

    # --- Lifecycle --------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._volumes is not None:
            self._volumes.stop()
        if self._download_tab is not None:
            self._download_tab.stop()
        if self._dupe_call is not None:
            self._dupe_call.abandon()
        self._derived_timer.stop()
        self._stop_scan()
        self._stop_mu()
        self._stop_signatures()
        self._cfg["window"] = {"w": self.width(), "h": self.height()}
        self._save_column_state()
        super().closeEvent(event)


def _count_by_folder(groups) -> Tuple[int, Dict[str, Tuple[int, int]]]:
    """Lane C's duplicate groups -> (how many, per series folder (volume numbers, chapter numbers))."""
    groups = list(groups)
    per_folder: Dict[str, List[int]] = {}
    for g in groups:
        folder = getattr(g, "folder", None)
        if folder is None:
            continue
        counts = per_folder.setdefault(str(folder), [0, 0])
        counts[0 if getattr(g, "kind", "") == "volume" else 1] += 1
    return len(groups), {k: (v[0], v[1]) for k, v in per_folder.items()}
