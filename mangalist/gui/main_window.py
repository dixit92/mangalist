"""Main application window (UI cycle, owner-approved mockup 2026-10-08): the top bar (List / Download tabs, the
library status, Rescan, Settings), the List tab (filter bar with state chips, the table, the collapsible details
panel, the footer) and the Download tab (lane B's; a placeholder until it merges).

The window owns the model and every action - scans, MangaUpdates lookups, MangaPixer data, the row menu, kind
answers, missing series, signatures; :mod:`.list_tab` and :mod:`.top_bar` only build the widgets."""

from __future__ import annotations

import datetime
import logging
import os
from contextlib import contextmanager
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

from .. import config, mu_cache, process_pool, store, wanted
from .._version import __version__
from ..models import MangaEntry
from ..scanner import (
    LibraryScan,
    RootScan,
    apply_kind_hint,
    folder_scopes,
    journal_folders_since,
    journal_mark,
    merge_scopes,
    scan_and_record_library,
    widen_scopes,
)
from ..store import Journal, Root, RootError
from ..unbundled import UnbundledFolders
from . import lanes
from .app_icon import build_app_icon
from .downloads_backend import create_backend as create_downloads_backend
from .list_tab import DUPLICATES, PAGE_DUPLICATES, PAGE_EMPTY, PAGE_TABLE, ListTab, all_filter_keys, chip_label
from .list_text import unbundled_first_status, unbundled_status, wanted_label
from .mu_picker import MuPickerDialog
from .mu_worker import MuWorker, _apply_cache, _clear_examined_if_newly_licensed
from .shell import WantedSeries
from .table_model import (
    COL_BEHIND, COL_DUPE, COL_ENGLISH, COL_EXAMINED, COL_FILES, COL_GAPS, COL_LIBRARY, COL_LICENSED, COL_MTIME,
    COL_MU_TITLE, COL_OFFICIAL, COL_RENAME, COL_STATE, COL_TITLE, COL_VERDICT, COLUMNS, MangaTableModel, column_label,
    merge_folder_scan, state_matches, under_any,
)
from .top_bar import TAB_DOWNLOAD, TAB_LIST, TopBar
from .volumes_target import search_titles
from .links import open_folder, open_link
from .background import describe_error, release_on_ui_thread
from .series_actions import SeriesMenu, SeriesRef


# ---------------------------------------------------------------------------
# Background scan worker
# ---------------------------------------------------------------------------


_log = logging.getLogger(__name__)

#: What the status line says about a library whose folder is there but holds nothing (owner, 2026-10-15).
LIBRARY_EMPTY = "Library Empty"


def root_of(path: str, roots: Sequence[Root]) -> Optional[Root]:
    """The library folder *path* is in (the deepest one, when folders nest), else None."""
    norm = os.path.normcase(os.path.abspath(path))
    best = None
    for r in roots:
        base = os.path.normcase(os.path.abspath(r.path)).rstrip("/\\")
        if (norm == base or norm.startswith(base + os.sep)) and (best is None or len(r.path) > len(best.path)):
            best = r
    return best


class _ScanStopped(Exception):
    """The window closed during a scan."""


class ScanWorker(QObject):
    """Scans the roots one after another and records each as soon as it was read (a renamed series folder keeps its
    link): the window shows a root's series without waiting for the others. *scopes* (``{root id: folder names}``)
    reads only those folders of a root (selective scanning)."""

    progress = Signal(int, int, str)        # folders done, folders in the root being read, the folder's name
    root_started = Signal(int, int, str)    # which root (1-based), how many, its name
    root_scanned = Signal(object, object)   # RootScan (already recorded), [(old folder, new folder)] it carried
    finished = Signal(object)  # LibraryScan, with .renamed = [(old folder, new folder)]
    failed = Signal(str)
    waiting = Signal()                      # the headless runner is scanning: this scan waits for it

    def __init__(self, roots: Sequence[Root], db=None, scopes: Optional[Dict[int, Optional[set]]] = None):
        super().__init__()
        self._roots = list(roots)
        self._db = db
        self._scopes = scopes
        self._stop = False

    def stop(self) -> None:
        """Abandon the scan at the next folder (the root being read is not recorded; roots done stay recorded)."""
        self._stop = True

    def _progress(self, done: int, total: int, name: str) -> None:
        if self._stop:
            raise _ScanStopped()
        self.progress.emit(done, total, name)

    def _started(self, number: int, count: int, name: str) -> None:
        if self._stop:
            raise _ScanStopped()
        self.root_started.emit(number, count, name)

    def run(self) -> None:
        try:
            result = scan_and_record_library(self._roots, self._db, progress=self._progress, on_start=self._started,
                                             on_root=self.root_scanned.emit, scopes=self._scopes,
                                             on_wait=self.waiting.emit)
        except _ScanStopped:
            return
        except Exception as exc:  # noqa: BLE001
            _log.exception("Scan failed")
            self.failed.emit(str(exc))
            return
        if self._stop:
            return
        if not result.entries and result.errors and len(result.errors) == len(self._roots):
            _log.error("Scan failed: no root could be read (%s)", "; ".join(result.errors))
            self.failed.emit("\n".join(result.errors))
            return
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
        self._dupes_only = False
        self._state_filter: Optional[str] = None
        # The filter box's text, case-folded: matched against the Title, the alternative title and the Kind only (what
        # the box's placeholder promises; the model's search_text) - never the 21 columns, some of which compute the
        # series' state or a MangaPixer view per call (UI Performance Research, stage 1 #1).
        self._needle = ""

    @contextmanager
    def _filter_change(self):
        """Qt 6.10+ announces a filter change around it (``invalidateFilter`` is deprecated there and warned on every
        call); older PySide6 (requirements allow 6.6+) re-filters after it."""
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            try:
                yield
            finally:
                self.endFilterChange()
        else:
            yield
            self.invalidateFilter()

    def refresh_scope(self) -> None:
        """The model's library scope changed: let the rows of the picked library through."""
        with self._filter_change():
            pass

    def set_text_filter(self, text: str) -> None:
        """Show only the rows whose Title, alternative title or Kind holds *text* (any case; '' shows every row)."""
        needle = (text or "").strip().casefold()
        if needle == self._needle:
            return
        with self._filter_change():
            self._needle = needle

    def text_filter(self) -> str:
        return self._needle

    def set_dupes_only(self, enabled: bool) -> None:
        """Filter to show only duplicate MU matches."""
        with self._filter_change():
            self._dupes_only = enabled

    def set_state_filter(self, key: Optional[str]) -> None:
        """Show only rows whose rescan state passes *key* (table_model.STATE_FILTERS; None = all)."""
        with self._filter_change():
            self._state_filter = key

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        # The mockup's headers: upper case, numbers right-aligned over their column.
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return column_label(section).upper()
            if role == Qt.TextAlignmentRole and section == COL_FILES:
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return super().headerData(section, orientation, role)

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        """The row is in the library picked, holds the filter box's text, and passes the state and duplicates
        filters (the cheap checks first: the state is computed only for rows the text lets through)."""
        source_model = self.sourceModel()
        if source_model is None:
            return True
        if not source_model.in_scope(source_row):     # the Library picker
            return False
        if self._needle and self._needle not in source_model.search_text(source_row):
            return False
        if self._state_filter is not None:
            # the state the row shows (never computed here: a state still being computed shows the one before)
            if not state_matches(source_model.shown_state_at(source_row), self._state_filter):
                return False
        if self._dupes_only:
            return source_model.is_duplicate(source_row)
        return True

    def lessThan(self, left, right) -> bool:
        """Compare the rows' sort keys, kept per row by the model (``MangaTableModel.sort_key``: the value
        ``data(UserRole)`` gives, compared as Qt compares it) - one Python call per comparison instead of two
        ``data()`` calls, each through the whole role dispatch (a sort of 700 rows: ~300 ms -> tens of ms)."""
        model = self.sourceModel()
        key = getattr(model, "sort_key", None)
        if key is None:
            return super().lessThan(left, right)
        col = left.column()
        return key(left.row(), col) < key(right.row(), col)


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
        "Title", "Library", "State", "Gaps", "English", "Verdict", "Files",
        "✓", "Dupe", "MU Title", "Behind", "Licensed", "Completed", "Official source", "Alternative Title",
        "Last Modified", "Subfolders", "Vol %", "Ch %", "Both %", "Rename",
    ]
    _DEFAULT_SHOWN = frozenset({"Title", "State", "Gaps", "English", "Verdict", "Files"})
    _DEFAULT_WIDTHS = {COL_TITLE: 300, COL_STATE: 170, COL_GAPS: 160, COL_ENGLISH: 150, COL_VERDICT: 100,
                       COL_FILES: 80, COL_EXAMINED: 32, COL_DUPE: 130, COL_MU_TITLE: 260, COL_OFFICIAL: 180,
                       COL_LIBRARY: 130, COL_RENAME: 90, COL_MTIME: 150}
    _CFG_COLUMNS = "list_column_state"
    _CFG_HIDDEN = "list_hidden_columns"
    # 2: the Library column exists, 3: the Rename column exists (each hidden unless the owner showed it)
    _CFG_COLUMNS_VERSION = "list_columns_version"
    _COLUMNS_VERSION = 3
    _CFG_LIBRARY = "list_library"                      # the picked library folder's path ("" or absent: all libraries)
    _CFG_SPLITTER = "list_splitter_sizes"
    _CFG_DETAILS = "details_panel"
    _FILTER_DELAY_MS = 150                  # the filter box: how long after the last key the rows are filtered
    _SAVE_DELAY_MS = 400                    # a splitter drag / column move: how long after the last change it is saved

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MangaList {__version__}")
        self._app_icon = build_app_icon()
        self.setWindowIcon(self._app_icon)
        QGuiApplication.setWindowIcon(self._app_icon)

        self._cfg = config.load()
        self._db = store.get_store()
        self._db.keep_ui_connection = True          # one connection for this thread's reads and writes (store/db.py)
        self._recover_journal()
        try:        # the Renamer window's rescans read what it renamed from the journal (both sides) since here
            self._journal_seen = journal_mark(self._db)
        except Exception:  # noqa: BLE001 - without it a rename rescans its whole library
            self._journal_seen = None
        win = self._cfg.get("window") or {}
        self.resize(int(win.get("w", 1440)), int(win.get("h", 900)))

        self._model = MangaTableModel()
        self._proxy = _SortProxy(self)
        self._proxy.setSourceModel(self._model)
        # MangaPixer source: a folder MangaPixer knows takes its knowledge from the export; the others
        # fall back to the own matcher (knowledge_for returns None).
        self._mp_resolver = None
        self._mp_items: dict = {}   # folder -> the resolved MangaPixer item (or None), per scan
        self._mp_roots: dict = {}   # root id -> (stored path, resolved path), read once per resolver
        self._mp_roots_of = None    # ... the resolver they were read for
        self._unbundled = UnbundledFolders(self._db)     # the per-series Unbundled Chapters setting
        self._model.set_state_providers(knowledge_for=self._knowledge_for, inventory_for=self._inventory_for,
                                        needs_kind_for=lambda e: bool(getattr(e, "needs_kind", False)),
                                        unbundled_for=lambda e: self._unbundled.choice(e.folder) or False)

        # Downloads: only with downloads switched on and a backend (the Download tab and the volumes controller are
        # built in _build_ui).
        self._volumes = None
        self._volumes_backend = self._make_volumes_backend()
        self._download_tab = None
        self._auto_modes = None                     # automatic downloads: the per-series modes (auto_modes.py)
        self._duplicates_view = None
        self._dupe_file_groups: Optional[int] = None
        self._dupe_focus: Optional[str] = None      # "Review duplicate files": the series the view opens on
        self._dupe_call = None
        self._wanted_series: List[WantedSeries] = []
        self._wanted_cache: Dict[int, tuple] = {}    # id(entry) -> (entry, its state, series id, its To get entries)
        self._dupe_per_folder: Dict[str, Tuple[int, int]] = {}
        self._last_scan: Optional[datetime.datetime] = None
        self._scan_note: Optional[str] = None       # said with the next scan's result (a move to library started it)
        self._scan_label = ""
        self._library: Optional[int] = None          # the picked library folder (root id); None = all libraries
        self._libraries_key: Optional[tuple] = None
        self._scan_applied: set = set()              # root ids whose rows the running scan has put in the table
        self._scan_pos: Optional[Tuple[int, int, str]] = None   # (which root, how many, its name) being read

        self._thread: QThread | None = None
        self._scanning_ids: List[int] = []                 # the roots the running scan reads
        self._scan_queued: Optional[List[Root]] = None     # roots asked for while a scan ran: scanned next
        self._scan_queued_scopes: Dict[int, Optional[set]] = {}   # ... their folders (None: the whole root)
        self._scan_queue_all = False                       # ... or every root
        self._scan_scopes: Optional[Dict[int, Optional[set]]] = None   # the running scan's folders (None: roots)
        self._worker: ScanWorker | None = None
        self._mu_thread: QThread | None = None
        self._mu_worker: MuWorker | None = None
        self._mu_entries: List[MangaEntry] = []
        self._mu_lookup_gen = 0                            # bumped by every start / stop of the MangaUpdates lookup
        self._sig_thread: QThread | None = None
        self._sig_worker: SignatureWorker | None = None
        # The renamer: its window, and "Rename pending" counted after each scan (off the UI thread). Tests swap
        # _background for a synchronous call and _make_renamer for one with a fake namer.
        self._renamer_window = None
        self._rename_call = None
        self._wanted_waiting = False                # the Download tab's counts show spinners (_set_wanted_waiting)
        self._rename_again = False
        self._rename_again_folders: Optional[set] = None   # the queued count's folders (None: every series)
        self._renamer_runner = None                 # the Renamer window's runner (None: its own threads)
        from .background import start_call as _start_call

        self._background = _start_call
        # The series' states off the UI thread (UI performance stage 2): with a background runner the table never
        # computes a state while painting, filtering, sorting or counting - a worker computes the rows without one and
        # pushes them in one step (_request_states). A synchronous runner (tests) keeps computing them on first use.
        self._states_runner = _start_call
        self._states_call = None
        self._model.deferred = bool(getattr(_start_call, "threaded", False))
        self._model.states_wanted.connect(self._request_states, Qt.ConnectionType.QueuedConnection)
        # Long counts in a subprocess (Rename pending, duplicates; mangalist.process_pool) - with the real runners only
        # (a test's synchronous runner keeps them in this process).
        from .background import start_process_call

        self._process_runner = start_process_call
        self._build_ui()

        # Debounce timers so rapid column-resize / column-move / splitter events don't thrash config I/O.
        self._col_resize_timer = QTimer(self)
        self._col_resize_timer.setSingleShot(True)
        self._col_resize_timer.setInterval(self._SAVE_DELAY_MS)
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
        self._top.rescan_all_clicked.connect(self._on_rescan_all)
        self._top.rescan_root_clicked.connect(self._on_rescan_root)
        self._top.settings_clicked.connect(self._on_settings)
        self._top.missing_clicked.connect(self._on_missing)
        self._top.add_series_clicked.connect(self._on_add_series)
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
        # Typing filters 150 ms after the last key (a burst of keys filters once); clearing the box applies at once.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(self._FILTER_DELAY_MS)
        self._filter_timer.timeout.connect(self._apply_text_filter)
        self._filter_edit.textChanged.connect(self._on_filter_text)
        self._status_label = lst.status_label
        self._progress = lst.progress
        self._sig_label = lst.sig_label
        self._btn_mu_start = lst.btn_mu_start
        self._btn_mu_stop = lst.btn_mu_stop
        self._btn_mu_start.clicked.connect(self._on_mu_start)
        self._btn_mu_stop.clicked.connect(self._on_mu_stop)
        lst.filter_changed.connect(self._on_filter_changed)
        self._top.library_changed.connect(self._on_library_changed)
        lst.details_toggled.connect(self._on_details_toggled)
        lst.add_root_clicked.connect(lambda: self._on_choose_root())
        lst.rename_library_clicked.connect(self.rename_library)
        self._detail.get_requested.connect(self._on_get_requested)
        self._detail.unbundled_toggled.connect(self._on_detail_unbundled)

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
        self._splitter_timer = QTimer(self)
        self._splitter_timer.setSingleShot(True)
        self._splitter_timer.setInterval(self._SAVE_DELAY_MS)
        self._splitter_timer.timeout.connect(self._save_splitter)
        lst.splitter.splitterMoved.connect(self._on_splitter_moved)

        # --- the Download tab (lane B's) and the downloads' status
        if self._volumes_backend is not None:
            from .volumes_controller import VolumesController

            self._volumes = VolumesController(self, self._volumes_backend, self._model, self._detail,
                                              self._status_label.setText)
            tab = lanes.download_tab_class()(self._volumes_backend, self)
            tab.count_changed.connect(self._top.set_download_count)
            tab.show_in_list.connect(self.show_folder_in_list)
            if hasattr(tab, "library_changed"):         # Restore / Move / Delete of replaced chapters moved files
                tab.library_changed.connect(self._on_replaced_chapters_moved)
            if hasattr(tab, "modes_changed"):           # a series' mode set in Found: the List's Auto chips follow
                tab.modes_changed.connect(lambda: self._refresh_auto_modes(force=True))
            if hasattr(tab, "set_series_host"):         # the series menus' List side (series_actions.py)
                tab.set_series_host(self)
            self._download_tab = tab
            self._pages.addWidget(tab)
            self._top.set_download_available(True)
        self._make_auto_modes()

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
        for sig in (self._model.modelReset, self._model.dataChanged, self._model.layoutChanged,
                    self._model.rowsInserted, self._model.rowsRemoved):
            sig.connect(lambda *_a: self._derived_timer.start())

        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_search)
        QShortcut(QKeySequence(Qt.Key_F5), self, activated=self._on_rescan)

        # Connect selection (rebind in case it returned None earlier)
        sel = self._table.selectionModel()
        if sel is not None:
            sel.selectionChanged.connect(self._on_row_changed)
        self._model.modelReset.connect(self._on_row_changed)     # a reset drops the selection without a signal
        # a library's rows replaced (set_entries inserts and removes only what changed): the details follow too
        self._model.rowsInserted.connect(self._on_row_changed)
        self._model.rowsRemoved.connect(self._on_row_changed)

        # Restore or apply default column order, then apply visibility.
        self._restore_column_state()
        self._apply_hidden_columns()
        self._keep_last_column_readable()
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

    def _on_filter_text(self, text: str) -> None:
        if not text.strip():
            self._filter_timer.stop()
            self._apply_text_filter()
        else:
            self._filter_timer.start()

    def _apply_text_filter(self) -> None:
        self._filter_timer.stop()
        self._proxy.set_text_filter(self._filter_edit.text())

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
        changed = [name for name, flag in (("roots", "roots_changed"), ("MangaPixer", "mangapixer_changed"),
                                           ("downloads", "downloads_changed")) if getattr(result, flag, False)]
        _log.info("Settings closed%s", f"; changed: {', '.join(changed)}" if changed else "")
        if getattr(result, "mangapixer_changed", False):
            self._after_mangapixer_changed(carry_before)
        if getattr(result, "roots_changed", False):
            self._after_roots_changed(rescan=True)
            self._refresh_auto_modes(force=True)            # a library's default mode may have changed
        if getattr(result, "downloads_changed", False) and self._volumes is not None:
            self._volumes.refresh_records()
            self._rebuild_wanted(force=True)
            self._refresh_auto_modes(force=True)            # the default mode may have changed
            if callable(getattr(self._download_tab, "reload_found", None)):
                self._download_tab.reload_found()

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
        where = self._scope_note()
        if key is None:
            self._status_label.setText("Showing all entries" + where)
        elif dupes:
            series, numbers = len(self._model.duplicate_series()), self._scoped_file_groups()
            self._status_label.setText(f"Duplicates{where}: {series} series in more than one folder, "
                                       f"{numbers} number{'s' if numbers != 1 else ''} held by more than one file")
        else:
            self._status_label.setText(f"Showing {self._proxy.rowCount()} series: {chip_label(key)}{where}")

    # --- The Library picker ------------------------------------------------

    def _scope_note(self) -> str:
        """`` (Manhwa)`` while one library is picked, else nothing - for the footer's messages."""
        return f" ({self._top.library_name()})" if self._library is not None else ""

    def _refresh_libraries(self) -> None:
        """Rebuild the picker and the Rescan menu from the library folders (when they changed) and re-pick the remembered
        one; with fewer than two folders there is no picker and every series shows."""
        roots = [r for r in self._roots() if getattr(r, "id", None) is not None]
        key = tuple((r.id, r.name, r.path) for r in roots)
        if key == self._libraries_key:
            return
        self._libraries_key = key
        targets = [(r.id, r.name, r.path) for r in roots]
        remembered = self._cfg.get(self._CFG_LIBRARY)
        picked = next((r.id for r in roots if r.path == remembered), None) if len(roots) > 1 else None
        self._model.set_root_names({r.id: r.name for r in roots})
        self._top.set_rescan_targets(targets)
        self._top.set_libraries(targets, picked)
        self._apply_library(picked)

    def _apply_library(self, root_id: Optional[int]) -> None:
        """Make *root_id* (None: all) the library the table, the counts, the duplicates and the Download list show."""
        self._library = root_id
        root = next((r for r in self._roots() if r.id == root_id), None) if root_id is not None else None
        self._top.set_rescan_scope(root.name if root is not None else None)
        self._model.set_scope(root_id)
        self._proxy.refresh_scope()
        if hasattr(self._duplicates_view, "set_library_scope"):
            self._duplicates_view.set_library_scope(None if root_id is None else [root_id])

    def _on_library_changed(self, root_id) -> None:
        """The owner picked a library (or All libraries): remember it, then everything that follows the table follows."""
        _log.info("Library picked: %s", self._top.library_name())
        root = next((r for r in self._roots() if r.id == root_id), None) if root_id is not None else None
        if root_id is not None and root is None:
            root_id = None
        # "" = all libraries (the settings store only writes keys, so a removed key would keep its old value)
        self._cfg[self._CFG_LIBRARY] = root.path if root is not None else ""
        config.save_values({self._CFG_LIBRARY: self._cfg[self._CFG_LIBRARY]})
        self._apply_library(root_id)
        self._refresh_derived()
        self._on_filter_changed(self._list.current_filter())

    def _scoped_file_groups(self) -> int:
        """How many numbers are held by more than one file, in the picked library (all of them when none is picked)."""
        if self._library is None:
            return self._dupe_file_groups or 0
        folders = {str(self._model.entry_at(r).folder) for r in self._model.scope_rows()}
        return sum(v + c for folder, (v, c) in self._dupe_per_folder.items() if folder in folders)

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

    # --- The series' states, computed off the UI thread (UI performance stage 2) ----------------------------------

    def _request_states(self) -> None:
        """Compute the states of the rows without one in the background (one computation at a time; the rows that
        lose their state meanwhile follow when it is in). The result is pushed in one step (_on_states_computed)."""
        if not self._model.deferred or self._states_call is not None:
            return
        request = self._model.take_states_request()
        if not request:
            return
        token = object()
        self._states_call = token
        call = self._states_runner(lambda: self._compute_states(request), self._on_states_computed,
                                   lambda message: self._on_states_failed(request, message),
                                   self._on_states_finished)
        if self._states_call is token:              # still running (a synchronous runner has finished already)
            self._states_call = call

    def _compute_states(self, request):
        """Off the UI thread: the states of *request* (the table model's providers - the same functions the headless
        runner's :func:`mangalist.wanted.library_states` uses), and what each series with gaps adds to the Download
        tab's "To get" (its series id read in one query for all of them)."""
        computed = self._model.compute_states(request)
        return computed, self._wanted_of(computed)

    def _wanted_of(self, computed) -> list:
        """``(entry, state, series id, its To get entries)`` for the series of *computed* with gaps (any thread)."""
        if self._download_tab is None:
            return []
        gaps = [(e, result) for e, _generation, result in computed if result is not None and result[0].gaps]
        if not gaps:
            return []
        downloads = self._volumes is not None
        ids = self._volumes.series_ids_for([e for e, _result in gaps]) if downloads else {}
        out = []
        for e, (st, _links, knowledge) in gaps:
            series_id = ids.get(str(e.folder)) if downloads else None
            out.append((e, st, series_id, wanted.series_wanted(e, st, knowledge, self._model.held_volumes_of(e),
                                                               series_id, downloads=downloads)))
        return out

    def _on_states_computed(self, result) -> None:
        computed, wanted_items = result
        self._model.push_states(computed)
        model = self._model
        for e, st, series_id, items in wanted_items:
            if model.ready_state_of(e) is st:     # the state pushed (not one replaced meanwhile)
                self._wanted_cache[id(e)] = (e, st, series_id, items)
        self._derived_timer.stop()              # the push's own dataChanged: the counts and To get follow now
        self._refresh_derived()
        self._run_after_add()                   # an Add series choice waiting for the new series' state

    def _stop_states(self, wait_ms: int = 5000) -> None:
        """The window closes: drop a states computation in flight and let its thread end (a running thread destroyed at
        exit aborts the process)."""
        self._model.deferred = False
        call, self._states_call = self._states_call, None
        if call is not None and hasattr(call, "abandon"):
            call.abandon()
            if hasattr(call, "wait"):
                call.wait(wait_ms)

    def _on_states_failed(self, request, message: str) -> None:
        _log.warning("Computing the series' states in the background failed (%s); they are computed when shown",
                     message)
        self._model.abandon_states(request)

    def _on_states_finished(self) -> None:
        self._states_call = None
        if self._model.states_pending():
            self._request_states()

    def _refresh_derived(self) -> None:
        """What follows the table: the chips' counts, the footer, the Download tab's list, the details' button."""
        self._update_counts()
        self._rebuild_wanted()
        self._update_detail_wanted()
        self._refresh_auto_modes()
        if self._list.current_filter() == DUPLICATES and self._duplicates_view is not None:
            self._duplicates_view.set_series_duplicates(self._model.duplicate_series())

    def _update_counts(self) -> None:
        model = self._model
        rows = model.scope_rows()       # the library picked
        n = len(rows)
        keys = tuple(k for k in all_filter_keys() if k is not None and k != DUPLICATES)
        counts: Dict[Optional[str], Optional[int]] = {None: n}
        for k in keys:
            counts[k] = 0
        for row in rows:
            # the state each row shows (a state still being computed counts as before; the push counts again)
            st = model.shown_state_at(row)
            if st is None:
                continue
            for k in model.filter_matches(st, keys):
                counts[k] += 1
        series_dupes = len(model.duplicate_series())
        counts[DUPLICATES] = series_dupes + self._scoped_file_groups()
        self._list.set_counts(counts)
        entries = [model.entry_at(r) for r in rows]
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
        downloads = self._volumes is not None
        cache = self._wanted_cache
        live = {id(e) for e in model.entries()}
        for key in [k for k in cache if k not in live]:     # series no longer in the table
            del cache[key]
        rows = model.scope_rows()               # the library picked
        if model.states_pending(rows):
            self._set_wanted_waiting(True)      # (spinners where the counts of the list before stand)
            return                              # some states are being computed: the list follows when they are in
        self._set_wanted_waiting(False)
        for row in rows:
            entry = model.entry_at(row)
            st = model.state_at(row)
            if entry is None or st is None or not st.gaps:
                continue
            series_id = self._volumes.series_id_for(entry) if downloads else None
            # A series' entries are kept while its entry and its (cached) state are the same objects: a library
            # switch or an unrelated change does not work them out again.
            got = cache.get(id(entry))
            if got is not None and got[0] is entry and got[1] is st and got[2] == series_id:
                items = got[3]
            else:
                items = wanted.series_wanted(entry, st, model.knowledge_at(row), model.held_volumes_at(row), series_id,
                                             downloads=downloads)
                cache[id(entry)] = (entry, st, series_id, items)
            out += items
        if out == self._wanted_series and not force:
            return
        self._wanted_series = out
        self._download_tab.set_wanted(out)

    def _set_wanted_waiting(self, waiting: bool) -> None:
        """The Download tab's counts (its chips and the tab's badge) show a spinner while its list waits for the picked
        library's states (up to about a second after a scan, a library's first visit): owner, 2026-10-16, answer 80."""
        if waiting == self._wanted_waiting:
            return
        self._wanted_waiting = waiting
        tab = self._download_tab
        if tab is not None and callable(getattr(tab, "set_waiting", None)):
            tab.set_waiting(waiting)
        self._top.set_download_busy(waiting)

    def _update_busy_counts(self) -> None:
        """Spinners on the chips whose count a background job is still working out: Rename pending (a subprocess count,
        35-50 s on 700 series) and Duplicates (the duplicate-numbers finder)."""
        keys = set()
        if self._rename_call is not None or self._rename_again:
            keys.add("rename")
        if self._dupe_call is not None:
            keys.add(DUPLICATES)
        self._list.set_busy_counts(keys)

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

    def check_new_chapters(self, folder: str) -> bool:
        """"Check for new chapters": switch to the Download tab with the chapters panel asking *folder*'s source for
        chapters newer than the newest one held (owner, 2026-10-11: a chapter out on the sites before MangaUpdates
        lists it)."""
        if self._download_tab is None:
            return False
        row = self._row_for_folder(folder)
        entry = self._model.entry_at(row) if row is not None else None
        if entry is None:
            return False
        knowledge = self._model.knowledge_at(row)
        own = (entry.english_title, entry.title)
        titles = (search_titles(knowledge, *own) if knowledge is not None
                  else tuple(dict.fromkeys(t.strip() for t in own if t and t.strip())))
        series_id = self._volumes.series_id_for(entry) if self._volumes is not None else None
        self.show_tab(TAB_DOWNLOAD)
        return self._download_tab.check_new_chapters(series_id, folder, entry.title, titles)

    def set_aside_volume_chapters(self, folder: str) -> bool:
        """"Set aside chapters its volumes hold…" (owner, 2026-10-11: "Until then, 3 is correct"; MangaPixer 1.42.0
        estimates the volumes its list does not map): list the chapter files inside volumes the series holds, ask, then
        move the confirmed ones to the holding folder - off the UI thread, then a rescan of that library."""
        from .. import upgrades

        sid = self._make_renamer().series_id_for_folder(folder)
        if sid is None:
            self._status_label.setText("Rescan first: this folder is not in the library database yet")
            return False
        db, title = self._db, Path(folder).name
        self._status_label.setText(f"{title}: looking for chapter files its volumes hold…")

        def found(batch) -> None:
            if batch.status != "pending":
                self._status_label.setText(f"{title}: nothing to set aside ({batch.error})")
                return
            if not self.confirm_set_aside(title, batch):
                self._status_label.setText(f"{title}: nothing set aside")
                return
            rels = [f.rel for f in batch.files]
            self._background(lambda: upgrades.set_aside_estimated(db, sid, rels), done,
                             lambda msg: self._status_label.setText(f"{title}: setting chapters aside failed: {msg}"))

        def done(held) -> None:
            moved = len(held.files) if held.status == "held" else 0
            self._status_label.setText(f"{title}: {moved} chapter file(s) set aside - restorable from the Download tab"
                                       + (f" ({held.error})" if held.error else ""))
            self._scan_paths([folder])
            if self._download_tab is not None and hasattr(self._download_tab, "refresh_replaced"):
                self._download_tab.refresh_replaced()

        self._background(lambda: upgrades.estimated_duplicates(db, sid), found,
                         lambda msg: self._status_label.setText(f"{title}: looking failed: {msg}"))
        return True

    def confirm_set_aside(self, title: str, batch) -> bool:
        """Ask before setting chapter files aside (tests answer for the owner by replacing this on the window)."""
        from .. import upgrades
        from ..knowledge import fmt_num, to_decimal

        by_volume: Dict[str, List] = {}
        for f in batch.files:
            by_volume.setdefault(f.volume, []).append(f)
        lines = []
        for volume in sorted(by_volume, key=lambda v: to_decimal(v) or 0):
            files = by_volume[volume]
            numbers = sorted({to_decimal(end) for f in files for end in str(f.chapters).split("-")} - {None})
            span = f"ch {fmt_num(numbers[0])}-{fmt_num(numbers[-1])}" if numbers else ""
            lines.append(f"  Volume {volume}: {span} ({len(files)} file{'s' if len(files) != 1 else ''})")
        days = upgrades.load_settings(self._db).holding_days
        size = batch.total_size if hasattr(batch, "total_size") else sum(f.size for f in batch.files)
        box = QMessageBox(QMessageBox.Icon.Question, "Set aside chapters the volumes hold?",
                          f"{title}: {len(batch.files)} chapter files lie inside volumes you hold, by MangaPixer's "
                          f"estimated volume ranges:\n\n" + "\n".join(lines) +
                          f"\n\nThey move to the holding folder ({size / 1e6:.0f} MB): restorable from the Download "
                          f"tab, emptied after {days} days. The volumes are not touched. Check one volume against its "
                          "chapters first if unsure - the ranges are estimates.", parent=self)
        yes = box.addButton(f"Set aside {len(batch.files)} files", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

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

    def _exclusion_for(self, entry) -> Optional[Tuple[Root, str]]:
        """(the entry's root, the anchored pattern for its folder): ``<folder>/**`` hides that folder (and itself) in
        that root only - a pattern without a ``/`` would hide every folder of that name."""
        root = next((r for r in self._roots() if r.id == getattr(entry, "root_id", None)), None)
        if root is None:
            return None
        try:
            rel = Path(entry.folder).resolve().relative_to(Path(root.path).resolve()).as_posix()
        except ValueError:
            return None
        return (root, f"{rel}/**") if rel and rel != "." else None

    def confirm_exclude(self, names: Sequence[str]) -> bool:
        """Ask before excluding (tests answer for the owner by replacing this on the window)."""
        return self._confirm_exclude(names)

    def _confirm_exclude(self, names: Sequence[str]) -> bool:
        listed = "\n".join(f"  {n}" for n in names[:12]) + ("\n  ..." if len(names) > 12 else "")
        box = QMessageBox(QMessageBox.Icon.Question, "Exclude from the library?",
                          f"Stop scanning {'this folder' if len(names) == 1 else f'these {len(names)} folders'}?\n\n"
                          f"{listed}\n\nNothing in it is moved or deleted; it just leaves MangaList's list. Settings > "
                          "Library > Edit undoes it (its MangaUpdates link is kept).", parent=self)
        yes = box.addButton("Exclude", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def exclude_from_library(self, entries) -> int:
        """Add the entries' folders to their roots' exclusions (after a yes), then rescan those roots. Returns how many
        patterns were added. Owner, 2026-10-09: "add an entry to the ignorelist for a root in list view's right click
        menu"."""
        found = [(e, x) for e in entries for x in [self._exclusion_for(e)] if x is not None]
        if not found or not self.confirm_exclude([str(e.title) for e, _x in found]):
            return 0
        by_root: Dict[int, Tuple[Root, List[str]]] = {}
        for _e, (root, pattern) in found:
            by_root.setdefault(root.id, (root, list(root.exclusions)))[1].append(pattern)
        added = 0
        for root_id, (root, patterns) in by_root.items():
            new = list(dict.fromkeys(patterns))                 # no duplicates; the existing ones keep their order
            added += len(new) - len(root.exclusions)
            try:
                self._db.set_exclusions(root_id, new)
            except Exception as exc:  # noqa: BLE001 - say it; nothing else changed
                QMessageBox.warning(self, "Could not exclude", f"{root.name}: {exc}")
                return 0
            _log.info("Excluded from %s: %s", root.name, ", ".join(p for p in new if p not in root.exclusions))
        self._status_label.setText(f"Excluded {len(found)} folder(s) - rescanning")
        self._scan_paths([e.folder for e, _x in found])     # only those folders: their rows are let go
        return added

    def _on_replaced_chapters_moved(self, folders) -> None:
        """The Download tab filed downloads, or restored, moved or deleted replaced chapter files: rescan those series'
        folders so the table, the To get list and the database follow."""
        folders = list(folders)
        n = len(folders)
        self._status_label.setText(f"Files changed in {n} series - rescanning" if n else "Files changed")
        self._scan_paths(folders)

    def _on_files_deleted(self, paths) -> None:
        """Lane C's view deleted duplicate files: rescan their series' folders so the table and the database follow."""
        self._status_label.setText(f"{len(paths)} duplicate file(s) deleted - rescanning")
        self._scan_paths(paths)

    def _scan_paths(self, paths) -> bool:
        """Rescan only the series folders holding *paths* (files or folders; selective scanning, owner 2026-10-13: "it
        rescans the whole library or ALL libraries sometimes and it's not always necessary"). Paths in no library
        folder scan nothing. False when nothing was asked."""
        roots, paths = self._roots(), list(paths)
        scopes = folder_scopes(roots, paths)
        if not scopes:
            if paths:
                _log.info("Rescan asked for %d path(s) in no library folder: nothing to scan", len(paths))
            return False
        self._start_scan([r for r in roots if r.id in scopes], scopes)
        return True

    def _count_duplicate_files(self) -> None:
        """Lane C's duplicate-numbers finder, off the UI thread, for the Duplicates chip's count."""
        finder = lanes.find_duplicate_files_function()
        if self._dupe_call is not None:
            return
        from .background import start_call

        db = self._db
        failed = lambda msg: _log.warning("Counting duplicate files failed: %s", msg)    # noqa: E731
        if self._in_subprocess() and finder is _real_duplicate_finder():
            # a subprocess: the count does not slow the window down (UI performance stage 2)
            self._dupe_call = self._process_runner(process_pool.duplicate_counts, (str(db.path),),
                                                   self._on_duplicate_files_counted, failed,
                                                   self._on_duplicate_count_finished)
            self._update_busy_counts()
            return
        self._dupe_call = start_call(lambda: _count_by_folder(finder(db)), self._on_duplicate_files_counted,
                                     failed, self._on_duplicate_count_finished)
        self._update_busy_counts()

    def _on_duplicate_files_counted(self, counted) -> None:
        total, per_folder = counted
        self._dupe_file_groups = int(total)
        self._dupe_per_folder = dict(per_folder)
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
        self._update_busy_counts()

    # --- The renamer -------------------------------------------------------

    def _make_renamer(self):
        """The renamer over the library database; this window rescans after a batch itself (the table follows)."""
        from ..renamer import Renamer

        return Renamer(self._db, rescan=None)

    def _count_rename_pending(self, folders=None) -> None:
        """"Rename pending" per series (the files the naming scheme would rename), off the UI thread, after a scan.
        *folders* (the top-level folders a folder scan read): count only the series in them and keep the others'
        counts - a count over the whole table costs about 35 s for 700 series (selective scanning, 2026-10-13)."""
        want = None if folders is None else {str(f) for f in folders}
        if self._rename_call is not None:
            # a scan finished meanwhile: count again once this count is in (the folders of both, or everything)
            if not self._rename_again:
                self._rename_again, self._rename_again_folders = True, want
            elif self._rename_again_folders is not None:
                self._rename_again_folders = None if want is None else self._rename_again_folders | want
            self._update_busy_counts()
            return
        self._rename_again, self._rename_again_folders = False, None
        counted = lambda counts: self._on_rename_counted(counts, want)                     # noqa: E731
        failed = lambda msg: _log.warning("Counting Rename pending failed: %s", msg)       # noqa: E731
        if (self._in_subprocess() and self._background is self._states_runner
                and getattr(self._make_renamer, "__func__", None) is MainWindow._make_renamer):
            # A subprocess (UI performance stage 2): the 35-50 s count used to slow every UI action down by 40-50 %.
            # It reads the series from the last scan's records (what this window's rows were scanned from).
            token = object()
            self._rename_call = token
            call = self._process_runner(process_pool.rename_pending_counts,
                                        (str(self._db.path), sorted(want) if want is not None else None),
                                        counted, failed, self._on_rename_count_finished)
            if self._rename_call is token:
                self._rename_call = call
            self._update_busy_counts()
            return
        entries = self._model.entries()
        if want is not None:
            entries = [e for e in entries if under_any(str(e.folder), want)]
        renamer = self._make_renamer()
        self._rename_call = self._background(lambda: renamer.pending_counts(entries), counted, failed,
                                             self._on_rename_count_finished)
        self._update_busy_counts()

    def _in_subprocess(self) -> bool:
        """Do the long counts run in a subprocess (the real background runner, subprocesses not switched off)?"""
        return bool(getattr(self._states_runner, "threaded", False)) and process_pool.enabled()

    def _on_rename_counted(self, counts, folders=None) -> None:
        counts = dict(counts or {})
        if folders is not None:         # a folder scan's count: the other series keep theirs
            kept = {k: v for k, v in self._model.rename_counts().items() if not under_any(k, folders)}
            kept.update(counts)
            counts = kept
        self._model.set_rename_counts(counts)
        self._update_counts()

    def _on_rename_count_finished(self) -> None:
        self._rename_call = None
        if self._rename_again:
            self._count_rename_pending(self._rename_again_folders)
        self._update_busy_counts()

    def open_renamer(self, scope):
        """The Renamer window on *scope* (:class:`.renamer_view.Scope`); one window, re-aimed when it is open."""
        from .renamer_view import RenamerWindow

        win = self._renamer_window
        if win is not None and win.isVisible():
            if not win.busy:
                win.set_scope(scope)
            win.raise_()
            win.activateWindow()
            return win
        win = RenamerWindow(self._make_renamer(), scope, self, run=self._renamer_runner)
        win.rescan_follows = True           # _on_library_renamed rescans; the window looks again after it
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        win.library_changed.connect(self._on_library_renamed)
        win.duplicates_requested.connect(self.review_duplicates)
        win.destroyed.connect(lambda *_a: setattr(self, "_renamer_window", None))
        self._renamer_window = win
        win.show()
        return win

    def rename_series(self, entries) -> None:
        """Row menu "Rename to the scheme…": the window on these series (one series is the pilot)."""
        from .renamer_view import Scope

        renamer = self._make_renamer()
        ids = [i for i in (renamer.series_id_for_folder(e.folder) for e in entries) if i is not None]
        if not ids:
            self._status_label.setText("Not scanned into the library yet - rescan first")
            return
        label = str(entries[0].title) if len(ids) == 1 else f"{len(ids)} series"
        self.open_renamer(Scope.series(ids, label))

    def rename_library(self, root_id: Optional[int] = None) -> None:
        """"Rename library…": the library picked (or *root_id*), else every library."""
        from .renamer_view import Scope

        root_id = root_id if root_id is not None else self._library
        root = next((r for r in self._roots() if r.id == root_id), None) if root_id is not None else None
        self.open_renamer(Scope.root(root.id, root.name) if root is not None else Scope.all())

    def _on_library_renamed(self, root_ids) -> None:
        """The Renamer window renamed (or put back) files: rescan the folders it renamed in (both sides of each step,
        from the journal) so the table and Rename pending follow; a library the journal names nothing in is read
        whole."""
        roots = [r for r in self._roots() if r.id in set(root_ids)]
        self._status_label.setText(f"Files renamed in {len(roots)} librar{'y' if len(roots) == 1 else 'ies'} - "
                                   "rescanning")
        if not roots:
            return
        scopes: Dict[int, Optional[set]] = {}
        try:
            paths, self._journal_seen = journal_folders_since(self._db, self._journal_seen)
            scopes = {rid: names for rid, names in folder_scopes(roots, paths).items()}
        except Exception:  # noqa: BLE001 - read the libraries whole
            _log.warning("Reading the renamed folders from the journal failed", exc_info=True)
        for r in roots:
            scopes.setdefault(r.id, None)
        if all(v is None for v in scopes.values()):
            self._start_scan(roots)
        else:
            self._start_scan(roots, scopes)

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
        self._refresh_libraries()
        roots = self._roots()
        if not roots:
            parts = ["No library folder"]
            tip = "No library folder yet: add one in Settings"
        else:
            # with the Library picker next to it, the status does not repeat the names
            parts = [] if self._top.picker_shown() else [", ".join(r.name for r in roots)]
            tip = "\n".join(f"{r.name}: {r.path}" for r in roots)
        if self._thread is not None:
            parts.append(self._scanning_text(len(roots)))
        elif self._last_scan is not None:
            parts.append(f"scanned {_when(self._last_scan)}")
        synced = self._mangapixer_synced()
        if synced is not None:
            parts.append(f"MangaPixer synced {_when(synced)}")
        text = " · ".join(parts)
        self._top.set_status(text[:1].upper() + text[1:], tip)                    # "Scanned 14:52 · MangaPixer ..."
        self._update_page()

    def _scanning_text(self, n_roots: int) -> str:
        """What the top bar says while a scan runs: which library folder is being read (not its name when there is only
        one), and which of how many when several are scanned."""
        if self._scan_pos is None or n_roots < 2:
            return "scanning…"
        number, count, name = self._scan_pos
        return f"scanning {name} ({number} of {count})…" if count > 1 else f"scanning {name}…"

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
        # MangaPixer's titles and volume lists name the files: a volume learned, a new title -> Rename pending again.
        self._count_rename_pending()

    def _mp_item_for(self, entry: MangaEntry):
        """The MangaPixer export item that applies to *entry*'s folder (its own or an ancestor's), or
        None; resolved once per scan / sync (:func:`mangalist.wanted.mangapixer_item`)."""
        key = str(entry.folder)
        # Also called from the states' worker thread: the cache and the resolver are taken once, in this order (a
        # reset - a scan, a MangaPixer sync - replaces the resolver first, then the cache), so an item read with an old
        # resolver only ever lands in the old cache.
        items = self._mp_items
        if key in items:
            return items[key]
        item = None
        if entry.root_id is not None:
            try:
                resolver = self._mp_resolver
                if resolver is None:
                    resolver = self._mp_resolver = wanted.open_resolver(self._db)
                roots = self._mp_roots
                if self._mp_roots_of is not resolver:      # the roots, read and resolved once per resolver
                    roots = self._mp_roots = wanted.resolved_roots(self._db)
                    self._mp_roots_of = resolver
                root = roots.get(entry.root_id)
                if root is not None:
                    item = wanted.mangapixer_item(resolver, entry.root_id, root[0], entry.folder,
                                                  resolved_root=root[1])
            except Exception:  # noqa: BLE001 - a broken MangaPixer cache must not break the table
                _log.warning("MangaPixer data for %s unavailable", entry.folder, exc_info=True)
        items[key] = item
        return item

    def _knowledge_for(self, entry: MangaEntry):
        """MangaPixer's knowledge for *entry* when its folder (or an ancestor) has an exported link,
        else None (the table then uses the own matcher's)."""
        return wanted.knowledge_of_item(self._mp_item_for(entry))

    def _inventory_for(self, entry: MangaEntry):
        """The series' held units for the state columns, merged with MangaPixer's volume list (which
        chapters each volume collects) when MangaPixer knows the series."""
        return wanted.inventory_of(entry, self._mp_item_for(entry))

    def _after_roots_changed(self, rescan: bool = False) -> None:
        self._show_roots()
        if rescan and self._roots():
            self._start_scan()
        elif self._roots() and self._model.rowCount():
            self._status_label.setText("Library folders changed - Rescan to apply")

    def _on_rescan(self) -> None:
        """The Rescan button: the library the picker shows (owner, 2026-10-10), else every library folder."""
        if self._library is not None and any(r.id == self._library for r in self._roots()):
            self._on_rescan_root(self._library)
            return
        self._on_rescan_all()

    def _on_rescan_all(self) -> None:
        if not self._roots():
            QMessageBox.information(self, "No library folder", "Add a library folder first (Settings).")
            return
        self._start_scan()

    def _on_rescan_root(self, root_id: int) -> None:
        """The Rescan arrow: read only this library folder (the others' rows and database records stay as they were)."""
        root = next((r for r in self._roots() if r.id == root_id), None)
        if root is None:
            return
        _log.info("Rescan of one library folder: %s", root.name)
        self._start_scan([root])

    def _start_scan(self, roots: Optional[Sequence[Root]] = None,
                    scopes: Optional[Dict[int, Optional[set]]] = None) -> None:
        """Scan *roots* (None: every library folder) in the background. *scopes* (``{root id: folder names}``, see
        :func:`mangalist.scanner.folder_scopes`) reads only those folders of a root; a root not in it is read whole.
        Asked while a scan runs, it follows that scan (queued scopes are joined; a long folder list reads the root)."""
        if self._thread is not None:
            # a scan is running: this one follows it (a rename's rescan used to be dropped here)
            if roots is None:
                self._scan_queue_all = True
            elif not self._scan_queue_all:
                queued = {r.id: r for r in (self._scan_queued or [])}
                queued.update({r.id: r for r in roots})
                self._scan_queued = list(queued.values())
                self._scan_queued_scopes = merge_scopes(
                    self._scan_queued_scopes, {r.id: (scopes or {}).get(r.id) for r in roots})
            return
        roots = list(roots) if roots is not None else self._roots()
        if not roots:
            return
        if not any(Path(r.path).is_dir() for r in roots):
            QMessageBox.warning(self, "Invalid folder",
                                "Not a directory:\n" + "\n".join(r.path for r in roots))
            return
        if scopes:
            scopes = widen_scopes(self._db, {r.id: scopes.get(r.id) for r in roots})
            if all(v is None for v in scopes.values()):
                scopes = None
        self._scan_scopes = scopes or None
        self._scanning_ids = [r.id for r in roots]

        self._top.set_scanning(True)
        label = roots[0].path if len(roots) == 1 else f"{len(roots)} roots"
        if self._scan_scopes:
            n = sum(len(v) for v in self._scan_scopes.values() if v is not None)
            label = f"{n} folder{'' if n == 1 else 's'} in " + ", ".join(r.name for r in roots)
        _log.info("Scan started: %s", label)
        self._scan_label = label
        self._scan_applied = set()
        self._scan_pos = (1, len(roots), roots[0].name)
        self._forget_vanished_roots()
        self._status_label.setText(f"Scanning {label}…")
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # busy until first progress update

        thread = QThread(self)
        worker = ScanWorker(roots, self._db, self._scan_scopes)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.root_started.connect(self._on_root_started)
        worker.root_scanned.connect(self._on_root_scanned)
        worker.waiting.connect(self._on_scan_waiting)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        release_on_ui_thread(thread, worker)          # never deleteLater in its own thread (a deadlock)
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

    def _on_root_started(self, number: int, count: int, name: str) -> None:
        """The next library folder is being read: the progress bar starts over, the top bar names it."""
        self._scan_pos = (number, count, name)
        self._progress.setRange(0, 0)
        self._status_label.setText(f"Scanning {name}…")
        self._list.empty_text.setText(name)
        self._show_roots()

    def _on_scan_waiting(self) -> None:
        self._status_label.setText("Waiting for the container's scan to finish…")

    def _on_root_scanned(self, rs: RootScan, renamed) -> None:
        """A library folder was read and recorded: its series replace its old rows now, before the next folder is read."""
        self._apply_root_scan(rs, renamed)
        if rs.error:
            self._status_label.setText(f"{rs.root_name}: not reachable")
        elif rs.empty:
            self._status_label.setText(f"{rs.root_name}: {LIBRARY_EMPTY}")
        elif rs.scope is not None:
            self._status_label.setText(f"{rs.root_name}: {len(rs.scope)} folder{'' if len(rs.scope) == 1 else 's'}")
        else:
            self._status_label.setText(f"{rs.root_name}: {len(rs.entries)} series")
        self._show_roots()

    # --- The table's rows, root by root ------------------------------------

    def _forget_vanished_roots(self) -> None:
        """Rows of a library folder that is no longer one (removed in Settings) leave the table when a scan starts."""
        known = {r.id for r in self._roots()}
        rows = self._model.entries()
        kept = [e for e in rows if e.root_id in known]
        if len(kept) != len(rows):
            self._set_rows(kept)

    def _set_rows(self, entries: List[MangaEntry]) -> None:
        """Put *entries* in the table, keeping the picked series and the scroll position (a reset drops both)."""
        current = self._current_entry()
        folder = str(current.folder) if current is not None else None
        scroll = self._table.verticalScrollBar().value()
        self._model.set_entries(entries)
        row = self._row_for_folder(folder) if folder is not None else None
        if row is not None:
            idx = self._proxy.mapFromSource(self._model.index(row, COL_TITLE))
            if idx.isValid():
                self._table.selectRow(idx.row())
        self._table.verticalScrollBar().setValue(scroll)

    def _prepare_entries(self, entries: Sequence[MangaEntry], renamed) -> None:
        """What a scan's entries need before they are shown: the examined marks (moved with renamed series), the
        MangaUpdates data already cached."""
        self._reload_examined()                     # the scan may have carried marks with series
        if self._volumes is not None:          # the series this scan read (and moved) are looked up again
            self._volumes.forget_series_ids([e.folder for e in entries] + [p for pair in renamed or () for p in pair])
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
        self._unbundled.reset()

    def _apply_root_scan(self, rs: RootScan, renamed=()) -> None:
        """Show one library folder's scan: its series replace the folder's rows (the other folders' rows stay); a
        folder-level scan replaces only the rows below the folders it read, keeping the order, the picked series and
        the subseries. A folder that could not be read changes nothing here - the end of the scan drops its rows
        unless the whole scan failed."""
        if rs.error:
            return
        self._scan_applied.add(rs.root_id)
        self._prepare_entries(rs.entries, renamed)
        order = {r.id: i for i, r in enumerate(self._roots())}
        if rs.scope is not None:            # a folder scan: only those folders' rows change, in the scan's order
            rows = merge_folder_scan(self._model.entries(), rs.root_id, rs.folder, rs.scope, rs.entries)
        else:
            rows = [e for e in self._model.entries() if e.root_id != rs.root_id] + list(rs.entries)
        self._set_rows(sorted(rows, key=lambda e: order.get(e.root_id, len(order))))

    def _on_scan_finished(self, result) -> None:
        if isinstance(result, LibraryScan):
            shown, self._scan_applied = self._scan_applied, set()      # this scan's roots; the next one starts empty
            for rs in result.roots:         # normally all shown already, root by root
                if rs.error:
                    self._drop_root_rows(rs.root_id)
                elif rs.root_id not in shown:
                    self._apply_root_scan(rs, result.renamed if len(result.roots) == 1 else ())
            self._scan_applied = set()
            entries: List[MangaEntry] = result.entries
            loose = result.loose
            errors = result.errors
            renamed = result.renamed
            partial = len(result.roots) < len(self._roots())
        else:  # a plain list of entries
            entries, loose, errors, renamed, partial = list(result), [], [], [], False
            self._prepare_entries(entries, renamed)
            self._set_rows(entries)
        self._last_scan = datetime.datetime.now()

        # The counts are in the footer (_update_counts); the message says what else the scan found.
        folders_only = isinstance(result, LibraryScan) and result.folders_only
        empty_roots = [rs.root_name for rs in result.roots if rs.empty] if isinstance(result, LibraryScan) else []
        all_empty = bool(empty_roots) and len(empty_roots) == len(result.roots)
        if all_empty:
            text = f"{', '.join(empty_roots)}: {LIBRARY_EMPTY}"      # owner, 2026-10-15: not "Scan failed"
        elif folders_only:
            n = sum(len(rs.scope) for rs in result.roots)
            text = f"Scanned {n} folder{'' if n == 1 else 's'} in " + ", ".join(rs.root_name for rs in result.roots)
        else:
            text = f"Scanned {len(entries)} folder(s)"
        if empty_roots and not all_empty:
            text += f"  —  {', '.join(empty_roots)}: {LIBRARY_EMPTY}"
        if partial and isinstance(result, LibraryScan) and not folders_only and not all_empty:
            text += " in " + ", ".join(rs.root_name for rs in result.roots)
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
        note, self._scan_note = self._scan_note, None
        if note:                            # what started this scan (a move to library) stays readable
            text = f"{note}  —  {text}"
        self._status_label.setText(text)
        self._status_label.setToolTip("\n".join(tips))
        self._progress.setVisible(False)
        self._mu_entries = self._model.entries()
        self._update_missing_count()
        self._refresh_derived()
        self._show_roots()
        self._count_duplicate_files()
        if folders_only:                    # recount only the folders read (the others' counts stand)
            self._count_rename_pending([str(Path(rs.folder) / n) for rs in result.roots for n in rs.scope])
        else:
            self._count_rename_pending()
        if self._duplicates_view is not None and self._list.current_filter() == DUPLICATES:
            self._duplicates_view.refresh()
        self._start_signatures()
        self._run_after_add()

        if self._cfg.get("mu_autostart"):
            self._start_mu_lookup(list(entries))

    def _drop_root_rows(self, root_id: Optional[int]) -> None:
        rows = self._model.entries()
        kept = [e for e in rows if e.root_id != root_id]
        if len(kept) != len(rows):
            self._set_rows(kept)

    def _on_scan_failed(self, msg: str) -> None:
        self._scan_applied = set()
        self._progress.setVisible(False)
        self._status_label.setText("Scan failed")
        QMessageBox.critical(self, "Scan failed", msg)

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._scan_pos = None
        self._top.set_scanning(False)
        self._progress.setVisible(False)
        self._show_roots()
        if self._duplicates_view is not None and hasattr(self._duplicates_view, "set_blocked"):
            self._duplicates_view.set_blocked(None)
        scanned, self._scanning_ids = self._scanning_ids, []
        self._scan_scopes = None
        queued_all, queued, scopes = self._scan_queue_all, self._scan_queued, self._scan_queued_scopes
        self._scan_queue_all, self._scan_queued, self._scan_queued_scopes = False, None, {}
        if queued_all or queued:
            if not queued_all and queued and any(v is not None for v in scopes.values()):
                QTimer.singleShot(0, lambda: self._start_scan(queued, scopes))
            else:
                QTimer.singleShot(0, lambda: self._start_scan(None if queued_all else queued))
        win = self._renamer_window
        if win is not None and hasattr(win, "library_rescanned"):
            win.library_rescanned(scanned)

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

    def _on_add_series(self) -> None:
        """The top bar's Add series...: paste a link, make the empty folder, rescan its library (add_series_dialog.py)."""
        from .add_series_dialog import NEXT_FIND, NEXT_OPEN, AddSeriesDialog, LiveBackend

        steps = (NEXT_FIND, NEXT_OPEN) if self._download_tab is not None else ()
        dlg = AddSeriesDialog(LiveBackend(self._db), self._roots(), self, current_root_id=self._library,
                              next_steps=steps)
        dlg.exec()
        if dlg.outcome is not None and dlg.root is not None:
            # the finish step's "Find releases now" / "Open in Download" runs once the rescan shows the series
            self._after_add = (getattr(dlg, "next_step", None), str(dlg.outcome.folder), dlg.outcome.series_id)
            scopes = folder_scopes([dlg.root], [dlg.outcome.folder]) if getattr(dlg.outcome, "folder", None) else {}
            self._start_scan([dlg.root], scopes or None)          # the new folder only

    def _run_after_add(self) -> None:
        """After the rescan that follows Add series: the finish step's choice (lane A, owner 2026-10-15, answer 5). The
        series' state is computed in the background after the scan (UI performance stage 2): until it is in, the new
        series is in no wanted list, so a find then searched nothing ("0 series searched", owner 2026-10-15) - the
        choice waits for it and :meth:`_on_states_computed` runs this again."""
        step = getattr(self, "_after_add", None)
        if not step or not step[0] or self._download_tab is None:
            self._after_add = None
            return
        what, folder, series_id = step
        key = os.path.normcase(os.path.normpath(folder))
        rows = [r for r in range(self._model.rowCount())
                if os.path.normcase(os.path.normpath(str(getattr(self._model.entry_at(r), "folder", "")))) == key]
        if rows and self._model.states_pending(rows):
            return                              # its state is on its way
        self._after_add = None
        if what == "find" and series_id is not None and callable(getattr(self._download_tab, "find_for", None)):
            self._rebuild_wanted()
            self.show_tab(TAB_DOWNLOAD)
            if self._download_tab.find_for([series_id]):
                return
        self.get_missing(folder)

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
        moved_entries = []
        for e in entries:
            key = str(e.folder)
            if e.examined != (key in examined):
                e.examined = key in examined
                changed = True
            if key not in targets:
                continue
            moved_entries.append(e)
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
            if self._volumes is not None and moved:    # their series rows moved: looked up again
                self._volumes.forget_series_ids(list(moved) + list(moved.values()))
            self._model.forget_states(moved_entries)     # changed in place: the others keep their states
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
        release_on_ui_thread(thread, worker)          # never deleteLater in its own thread (a deadlock)
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
        """Start a background worker to enrich *entries* with MU data. Which of them MangaPixer knows is read off the
        UI thread for the folders not resolved yet (a MangaPixer lookup resolves each folder on the library share: 3 s
        for a whole library at the end of a scan)."""
        self._mu_lookup_gen += 1                    # a lookup still deciding what to fetch is dropped
        if self._mu_thread is not None:
            self._mu_worker.abort()
            self._mu_thread = None
            self._mu_worker = None

        if not entries:
            return
        entries = list(entries)
        unresolved = [(e.root_id, str(e.folder)) for e in entries if str(e.folder) not in self._mp_items]
        if not unresolved:
            self._run_mu_lookup(entries, set())
            return
        gen, db = self._mu_lookup_gen, self._db

        def known_now(known) -> None:
            if gen == self._mu_lookup_gen:
                self._run_mu_lookup(entries, set(known))

        def failed(message: str) -> None:
            _log.warning("Reading which folders MangaPixer knows failed: %s", message)
            known_now(set())

        self._background(lambda: wanted.mangapixer_known(db, unresolved), known_now, failed, None)

    def _run_mu_lookup(self, entries: List[MangaEntry], known_folders: set) -> None:
        """The MangaUpdates worker for *entries*, but the folders MangaPixer knows (*known_folders*, plus those the
        table resolved already) and the entries no longer shown."""
        # Owner decision (MangaPixer Data Source, 2026-10-02): for folders MangaPixer knows, MangaList
        # fetches nothing itself - their knowledge comes from the MangaPixer export.
        def known(e: MangaEntry) -> bool:
            key = str(e.folder)
            return self._mp_items[key] is not None if key in self._mp_items else key in known_folders

        skipped = [e for e in entries if known(e)]
        if skipped:
            entries = [e for e in entries if not known(e)]
            self._status_label.setText(
                f"{len(skipped)} folder(s) skipped: MangaPixer knows them (sync MangaPixer to refresh)")
            if not entries:
                return

        # Build (source_row, entry) pairs - the row of each entry still in the model (a scan may have replaced it
        # while MangaPixer was asked).
        folder_to_row = {
            str(self._model.entry_at(r).folder): r
            for r in range(self._model.rowCount())
            if self._model.entry_at(r) is not None
        }
        pairs = [(folder_to_row[str(e.folder)], e) for e in entries
                 if str(e.folder) in folder_to_row and self._model.entry_at(folder_to_row[str(e.folder)]) is e]
        if not pairs:
            return

        worker = MuWorker(pairs)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.entry_started.connect(self._on_mu_entry_started)
        worker.entry_updated.connect(self._on_mu_entry_updated)
        worker.finished.connect(thread.quit)
        release_on_ui_thread(thread, worker)          # never deleteLater in its own thread (a deadlock)
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
        self._mu_lookup_gen += 1                    # a lookup still asking MangaPixer does not start either
        if self._mu_worker is not None:
            _log.info("MangaUpdates lookup stopped by the owner")
            self._mu_worker.abort()
        self._list.set_mu_running(False)

    def _stop_mu(self, wait_ms: int = 5000) -> None:
        self._mu_lookup_gen += 1
        if self._mu_worker is not None:
            self._mu_worker.abort()
        if self._mu_thread is not None:
            self._mu_thread.quit()
            self._mu_thread.wait(wait_ms)

    # --- Selection from elsewhere -------------------------------------------

    def _select_source_row(self, src_row: int) -> None:
        """Select a series in the table (clearing the filters that hide it, leaving the duplicates view)."""
        src = self._model.index(src_row, COL_TITLE)
        if not self._model.in_scope(src_row):       # another library is picked: go to the series' own
            entry = self._model.entry_at(src_row)
            own = entry.root_id if entry is not None and any(r.id == entry.root_id for r in self._roots()) else None
            self._top.set_library(own)
            self._on_library_changed(own)
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
        self._keep_last_column_readable()
        self._col_resize_timer.start()              # saved once the columns stop moving (every key is rewritten)

    def _on_section_resized(self, _logical: int, _old: int, _new: int) -> None:
        self._col_resize_timer.start()

    def _on_splitter_moved(self, _pos: int, _idx: int) -> None:
        """A drag of the details panel's edge: saved 400 ms after the last move, not on every pixel."""
        self._splitter_timer.start()

    def _save_splitter(self) -> None:
        self._splitter_timer.stop()
        self._cfg[self._CFG_SPLITTER] = self._list.splitter.sizes()
        config.save_values({self._CFG_SPLITTER: self._cfg[self._CFG_SPLITTER]})

    # --- Column visibility -----------------------------------------------

    def _hidden_columns(self) -> set:
        """The columns the owner hid (by name); the mockup's trimmed set until the owner changes it."""
        hidden = self._cfg.get(self._CFG_HIDDEN)
        if not isinstance(hidden, list):
            return {name for name in COLUMNS if name not in self._DEFAULT_SHOWN}
        hidden = set(hidden)
        version = self._cfg.get(self._CFG_COLUMNS_VERSION, 1)
        if version < 2:
            hidden.add("Library")           # a list saved before the column existed: it stays hidden until chosen
        if version < 3:
            hidden.add("Rename")
        return hidden

    def _keep_last_column_readable(self) -> None:
        """The last column stretches into what is left of the view, which can be a sliver with its header cut off;
        its right edge then sits at the view's edge where it cannot be dragged (owner, 2026-10-11: "the last column
        gets cut off"). Give it at least its header's width: the table then scrolls sideways instead."""
        header = self._table.horizontalHeader()
        shown = [header.logicalIndex(v) for v in range(header.count())
                 if not header.isSectionHidden(header.logicalIndex(v))]
        if not shown:
            return
        last = shown[-1]
        need = max(header.sectionSizeHint(last), self._DEFAULT_WIDTHS.get(last, 0))
        if header.sectionSize(last) < need:
            header.resizeSection(last, need)

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
            header = self._table.horizontalHeader()      # a column shown for the first time may be narrower than its default
            header.resizeSection(col, max(header.sectionSize(col), self._DEFAULT_WIDTHS.get(col, 0)))
        else:
            hidden.add(name)
        self._cfg[self._CFG_HIDDEN] = sorted(hidden)
        self._cfg[self._CFG_COLUMNS_VERSION] = self._COLUMNS_VERSION
        config.save(self._cfg)
        self._apply_hidden_columns()
        self._keep_last_column_readable()

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
        self._update_detail_auto()
        self._update_detail_unbundled()
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

        menu = QMenu(self._table)
        # The entries every screen shares (series_actions.py: the Download tab's To get rows, Found cards, the panels)
        modes = self._auto_modes
        series = SeriesMenu(menu, [SeriesRef(str(e.folder), modes.series_id(str(e.folder)) if modes else None, e.title)
                                   for e in entries],
                            backend=self._volumes_backend if self._auto_modes is not None else None, host=self)
        act_kind = None
        act_get = act_new = act_aside = None
        act_dupes = None
        act_exclude = None
        act_rename = act_rename_library = None

        if n == 1:
            if getattr(entries[0], "needs_kind", False):
                act_kind = menu.addAction("Volumes or chapters?…")
                act_kind.setToolTip("Its files are bare numbers: say once whether they are volumes or chapters")
                menu.addSeparator()
            series.add_open_folder()
            menu.addSeparator()
            act_copy_path = menu.addAction("Copy folder path")
            series.add_copy_title()
            act_exclude = menu.addAction("Exclude from its library…")
            act_exclude.setToolTip("Add this folder to its library's exclusions: MangaList stops scanning it (its files "
                                   "are not touched; Settings > Library undoes it)")
            series.add_move_to_library()
            pending = self._model.rename_count_at(rows[0])
            act_rename = menu.addAction(f"Rename to the scheme ({pending} file{'s' if pending != 1 else ''})…"
                                        if pending else "Rename to the scheme…")
            act_rename.setToolTip("Preview the naming scheme's names for this series' files, then rename them "
                                  "(undoable) - the first step before a whole library")
            act_rename_library = menu.addAction("Rename its whole library…")
            menu.addSeparator()
            series.add_fix_mu()
            entry0 = entries[0]
            series.add_mu_page(entry0.mu_url or "")
            act_check_mu = menu.addAction("Check MU for this entry")
            if self._download_tab is not None:
                groups = self._wanted_groups(str(entry0.folder))
                act_get = menu.addAction((wanted_label(groups) or "Get the missing volumes") + "…")
                act_get.setEnabled(bool(groups))
                act_get.setToolTip("Open this series in the Download tab" if groups else
                                   "Nothing missing: no gaps to download")
                act_new = menu.addAction("Check for new chapters…")
                act_new.setToolTip("Ask the series' Suwayomi source for chapters newer than the newest one you have - "
                                   "also before MangaUpdates lists them")
                menu.setToolTipsVisible(True)
            act_aside = menu.addAction("Set aside chapters its volumes hold…")
            act_aside.setToolTip("Chapter files inside volumes this series holds, by MangaPixer's estimated volume ranges: "
                                 "listed for you to confirm, then moved to the holding folder (restorable)")
            menu.setToolTipsVisible(True)
            vols, chs = self._model.duplicate_files_at(rows[0])
            if vols or chs:
                act_dupes = menu.addAction(f"Review duplicate files ({vols + chs})…")
                act_dupes.setToolTip("The Duplicates view on this series only: keep or discard its extra copies")
            series.add_mangapixer()
            links_menu = menu.addMenu("Official sources")
            link_actions = {}
            for link in self._model.links_at(rows[0]):
                act = links_menu.addAction(link.label if link.url else f"{link.label} (no page known)")
                act.setEnabled(bool(link.url))
                link_actions[act] = link.url
            links_menu.setEnabled(bool(link_actions))
            menu.addSeparator()
        else:
            act_copy_path = None
            link_actions = {}
            menu.addAction(f"{n} folders selected").setEnabled(False)
            menu.addSeparator()
            act_exclude = menu.addAction(f"Exclude {n} folders from their libraries…")
            series.add_move_to_library()
            act_rename = menu.addAction(f"Rename {n} series to the scheme…")
            act_check_mu = menu.addAction(f"Check MU for {n} selected entries")
            menu.addSeparator()

        series.add_unbundled()
        act_bulk_find = self._add_automation_menu(series, entries)

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
        series.add_behind()

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

        said = series.run(chosen)
        if said is not None:                # one of the shared entries (series_actions.py): done, its line shown
            if said:
                self._status_label.setText(said)
        elif act_bulk_find is not None and chosen is act_bulk_find:
            self.bulk_find(entries)
        elif act_kind is not None and chosen is act_kind:
            from .kind_dialog import ask_series_kind

            if ask_series_kind(self, entries[0], db=self._db):
                self._model.refresh_states()
        elif act_dupes is not None and chosen is act_dupes:
            self.review_duplicates(str(entries[0].folder))
        elif chosen in link_actions and link_actions[chosen]:
            self._open_url(link_actions[chosen])
        elif act_exclude is not None and chosen is act_exclude:
            self.exclude_from_library(entries)
        elif act_rename is not None and chosen is act_rename:
            self.rename_series(entries)
        elif act_rename_library is not None and chosen is act_rename_library:
            self.rename_library(entries[0].root_id)
        elif chosen is act_get and act_get is not None:
            self.get_missing(str(entries[0].folder))
        elif chosen is act_new and act_new is not None:
            self.check_new_chapters(str(entries[0].folder))
        elif chosen is act_aside and act_aside is not None:
            self.set_aside_volume_chapters(str(entries[0].folder))
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
        elif chosen is act_copy_path and entries:
            QGuiApplication.clipboard().setText(str(entries[0].folder))
            self._status_label.setText(f"Copied path: {entries[0].folder}")
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

    def set_unbundled(self, entries, on: bool) -> int:
        """The row menu's Unbundled Chapters tick for *entries*' series (mangalist.unbundled); the states follow."""
        try:
            n = self._unbundled.set([e.folder for e in entries], on)
        except Exception as exc:  # noqa: BLE001 - shown, nothing assumed changed
            self._status_label.setText(f"Could not change Unbundled chapters: {type(exc).__name__}: {exc}")
            return 0
        self._status_label.setText(unbundled_status(on, n))
        if n:
            self._model.refresh_states()
            self._on_row_changed()
        if n and on:        # where the volume list does not say where the volumes end: the first chapter to get
            waiting = [(r, e) for r, e in self._entries_for([e.folder for e in entries])
                       if getattr(getattr(self._model.state_at(r), "unbundled_ask", None), "needed", False)]
            if len(entries) == 1 and waiting:
                self.ask_unbundled_first_for(str(waiting[0][1].folder))
            elif waiting:
                self._status_label.setText(unbundled_status(on, n) + unbundled_first_status(None, "", len(waiting)))
        return n

    def unbundled_first_ask(self, folders):
        """The "first chapter to get" question of *folders*' one series with Unbundled chapters on (None: several,
        off, or its volume list says where the volumes end) - the series menus show "First chapter to get…" for it."""
        found = self._entries_for(folders)
        if len(found) != 1 or not self._unbundled.is_on(found[0][1].folder):
            return None
        return getattr(self._model.state_at(found[0][0]), "unbundled_ask", None)

    def ask_unbundled_first_for(self, folder) -> str:
        """Ask (again) for *folder*'s first chapter to get (gui/unbundled_dialog.py); the states follow."""
        from .unbundled_dialog import prompt_first_chapter

        found = self._entries_for([folder])
        if not found:
            return ""
        row, entry = found[0]
        text = prompt_first_chapter(self, self._unbundled, entry, self._model.state_at(row))
        if text:
            self._status_label.setText(text)
            self._model.refresh_states()
            self._on_row_changed()
        return text

    # --- The series host (series_actions.py): what the series menus of every screen need from the List ----------

    def _entries_for(self, folders) -> List[Tuple[int, MangaEntry]]:
        wanted_folders = {str(f) for f in folders}
        out = []
        for r in range(self._model.rowCount()):
            e = self._model.entry_at(r)
            if e is not None and str(e.folder) in wanted_folders:
                out.append((r, e))
        return out

    def open_series_folder(self, folder: str) -> None:
        self._open_in_explorer(Path(folder))

    def copy_series_title(self, title: str) -> None:
        QGuiApplication.clipboard().setText(title)
        self._status_label.setText(f"Copied title: {title}")

    def series_mu_url(self, folder: str) -> Optional[str]:
        found = self._entries_for([folder])
        return (found[0][1].mu_url or None) if found else None

    def fix_mu_match_for(self, folder: str) -> bool:
        """"Fix MangaUpdates match…" for *folder*'s series (the List's dialog, from any screen)."""
        found = self._entries_for([folder])
        if not found:
            return False
        self._on_fix_mu_match(*found[0])
        return True

    def unbundled_on(self, folders) -> bool:
        return self._unbundled.all_on(folders)

    def set_unbundled_for(self, folders, on: bool) -> str:
        """Unbundled chapters for *folders*' series from any screen; the states and the To get list follow."""
        entries = [e for _r, e in self._entries_for(folders)]
        if not entries:
            return unbundled_status(on, 0)
        self.set_unbundled(entries, on)
        return self._status_label.text()

    def behind_counts(self, folders) -> Tuple[int, int]:
        """(how many of *folders*' series can be marked up to date - a MangaUpdates match, not marked yet -, how many
        are marked)."""
        entries = [e for _r, e in self._entries_for(folders)]
        return (sum(1 for e in entries if e.mu_id is not None and e.behind_override != "done"),
                sum(1 for e in entries if e.behind_override == "done"))

    def set_behind_done(self, folders, done: bool) -> str:
        """Mark Behind as up to date (or clear it) for *folders*' series, from any screen: the states follow (Up to
        date), and the find pass skips a series marked so."""
        n = 0
        for r, e in self._entries_for(folders):
            if done and (e.mu_id is None or e.behind_override == "done"):
                continue
            if not done and e.behind_override != "done":
                continue
            e.behind_override = "done" if done else None
            mu_cache.set_behind_override(e.folder, e.behind_override)
            self._model.dataChanged.emit(self._model.index(r, COL_BEHIND), self._model.index(r, COL_BEHIND),
                                         [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole])
            n += 1
        what = "marked up to date" if done else "counted as behind again"
        return f"Behind: {n} series {what}." if n else ""

    # --- Move to library (mangalist.library_move; owner, 2026-10-15) ---------------------------------------

    def library_targets(self, folders) -> List[Tuple[int, str, str]]:
        """The library folders *folders* can move to: every other one (all of them when the series lie in several)."""
        roots = self._roots()
        held = set()
        for f in folders:
            loc = self._db._locate(f)
            if loc is not None:
                held.add(loc[0])
        return [(r.id, r.name, r.path) for r in roots if not (len(held) == 1 and r.id in held)]

    def confirm_move(self, check):
        """Ask before a move to library, with the folder inside the target library the series go into (its top by
        default; :mod:`.move_dialog`). The check to move, None for Cancel (tests answer with ``answer_move``)."""
        from .move_dialog import MoveDialog

        dialog = MoveDialog(self._db, check, show_modes=self._auto_modes is not None, parent=self)
        return dialog.ask(getattr(self, "answer_move", None))

    def confirm_undo_move(self, record) -> bool:
        return self._ask("Undo the last move?", f"Move {record.count} series back from {record.target_name} to "
                                                f"{' / '.join(record.source_names) or 'where they were'}?\n\n"
                                                f"(moved {record.created_at})", "Move back")

    def tell_move(self, title: str, text: str) -> None:
        """Say why nothing moved (tests replace this on the window)."""
        QMessageBox.information(self, title, text)

    def _ask(self, title: str, text: str, label: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self)
        yes = box.addButton(label, QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def move_to_library(self, folders, root_id: int) -> str:
        """Move to library ▸ <library>: check, confirm (Cancel is the default), move through one cross-root journal plan,
        then scan both folders so the List shows the series in its new library at once. Returns the status line."""
        from .. import library_move

        if self._thread is not None:
            return f"Move to library: {library_move.SCAN_RUNNING}"
        titles = {str(e.folder): str(e.title) for _r, e in self._entries_for(folders)}
        try:
            check = library_move.check_move(self._db, [(str(f), titles.get(str(f), Path(f).name)) for f in folders],
                                            root_id)
        except Exception as exc:  # noqa: BLE001 - said, nothing moved
            _log.warning("Move to library: the check failed", exc_info=True)
            return f"Move to library: {exc if isinstance(exc, ValueError) else describe_error(exc)}"
        if not check.items:
            lines = library_move.refused_lines(check) or [f"{t} - already in {check.target.name}" for t in check.already]
            self.tell_move(f"Not moved to {check.target.name}", "Nothing can move:\n\n" + "\n".join(lines))
            return f"Move to library: nothing moved ({len(lines)} refused)."
        check = self.confirm_move(check)
        if check is None:
            return ""
        result = library_move.move_series(self._db, check)
        return self._after_library_move(result)

    def last_library_move(self) -> Optional[str]:
        """The "Undo last move (…)" label of the menu, None when no move can be undone."""
        from ..library_move import last_move

        try:
            record = last_move(self._db)
        except Exception:  # noqa: BLE001 - the entry stays out
            _log.warning("Move to library: reading the last move failed", exc_info=True)
            return None
        return record.label if record is not None else None

    def undo_library_move(self) -> str:
        from .. import library_move

        if self._thread is not None:
            return f"Undo move: {library_move.SCAN_RUNNING}"
        record = library_move.last_move(self._db)
        if record is None:
            return "Move to library: nothing to undo."
        if not self.confirm_undo_move(record):
            return ""
        return self._after_library_move(library_move.undo_move(self._db, record.plan_id))

    def _after_library_move(self, result) -> str:
        """A move (or its undo) ran: what failed is shown in a box; what moved updates the table's data (examined marks,
        modes) and both sides are scanned; the status line says what happened once that scan is in."""
        if not result.moved:
            self.tell_move("Not moved", result.message)
            return result.message
        if not result.ok:
            self.tell_move("Moved only in part", result.message)
        self._apply_identity_changes(result.moved)      # the examined marks moved in the database; the scan does the rest
        self._scan_note = result.message
        if not self._scan_paths(result.paths):
            self._scan_note = None
        return result.message

    def folder_for_series(self, series_id: int) -> Optional[str]:
        """The folder of a store series id among the List's rows (the To get list's ids first, then the modes' cache,
        then a look at every row)."""
        for item in self._wanted_series:
            if item.series_id == series_id:
                return item.folder
        if self._auto_modes is not None:
            folder = self._auto_modes.folder_of(series_id)
            if folder:
                return folder
        if self._volumes is None:
            return None
        for r in range(self._model.rowCount()):
            e = self._model.entry_at(r)
            if e is not None and self._volumes.series_id_for(e) == series_id:
                return str(e.folder)
        return None

    # --- Automatic downloads: the per-series mode, the mode chip, the bulk find ---------------------------

    def _make_auto_modes(self) -> None:
        """The per-series modes, when the downloads backend offers automatic downloads (auto_modes.py)."""
        from .auto_modes import AutoModes

        if self._volumes_backend is None or not AutoModes.available(self._volumes_backend):
            return
        self._auto_modes = AutoModes(self._volumes_backend, self, background=lambda *a: self._background(*a))
        self._auto_modes.changed.connect(self._on_auto_modes_changed)
        self._detail.set_auto_enabled(True)
        self._detail.auto_mode_chosen.connect(self._on_detail_mode_chosen)

    def _refresh_auto_modes(self, force: bool = False) -> None:
        if self._auto_modes is None:
            return
        folders = [str(e.folder) for e in (self._model.entry_at(r) for r in range(self._model.rowCount())) if e]
        self._auto_modes.refresh(folders, force=force)

    def _on_auto_modes_changed(self) -> None:
        self._model.set_mode_chips(self._auto_modes.chips())
        self._update_detail_auto()
        if callable(getattr(self._download_tab, "refresh_modes", None)):   # Found's picker, the panels' menus
            self._download_tab.refresh_modes()

    def _update_detail_auto(self) -> None:
        if self._auto_modes is None:
            return
        entry = self._current_entry()
        if entry is None:
            self._detail.set_auto_mode(None)
            return
        state = self._auto_modes.detail_state(str(entry.folder))       # read once for the line and the picker
        self._detail.set_auto_mode(*self._auto_modes.detail_text(state))
        self._detail.set_auto_choice(*state if isinstance(state, tuple) else (None, None, False),
                                     available=isinstance(state, tuple))

    def _update_detail_unbundled(self) -> None:
        """The details panel's Unbundled chapters switch (owner, 2026-10-13: toggled there too)."""
        entry = self._current_entry()
        if entry is None:
            return
        try:
            scanned = self._unbundled.series_id(entry.folder) is not None
            on = self._unbundled.is_on(entry.folder)
        except Exception:  # noqa: BLE001 - the switch stays off; the row menu says why
            scanned = on = False
        self._detail.set_unbundled_choice(on, available=scanned)

    def _on_detail_mode_chosen(self, mode) -> None:
        """The details panel's picker (owner, 2026-10-13: the Automatic line becomes a picker, like Found's)."""
        entry = self._current_entry()
        if entry is None or self._auto_modes is None:
            return
        self._status_label.setText(self._auto_modes.apply([str(entry.folder)], mode))

    def _on_detail_unbundled(self, on: bool) -> None:
        entry = self._current_entry()
        if entry is not None:
            self.set_unbundled([entry], on)

    def _add_automation_menu(self, series: SeriesMenu, entries):
        """The row menu's "Automatic downloads" submenu and "Find releases for N series" (None without)."""
        if self._auto_modes is None:
            return None
        from .automation_rules import bulk_find_label

        menu = series.menu
        folders = [str(e.folder) for e in entries]
        series.add_modes(apply=lambda mode: self._auto_modes.apply(folders, mode))
        act_bulk = None
        if callable(getattr(self._download_tab, "find_for", None)):
            ids = self._auto_modes.series_ids(folders)
            act_bulk = menu.addAction(bulk_find_label(len(ids)))
            act_bulk.setEnabled(bool(ids))
            act_bulk.setToolTip("Run the find pass for these series; what it finds waits under Found in the Download "
                                "tab - nothing is sent by itself" if ids else "Not scanned yet: rescan first")
            menu.setToolTipsVisible(True)
        menu.addSeparator()
        return act_bulk

    def bulk_find(self, entries) -> bool:
        """"Find releases for N series": the Download tab's bulk find for these series, shown under Found."""
        if self._auto_modes is None or not callable(getattr(self._download_tab, "find_for", None)):
            return False
        ids = self._auto_modes.series_ids(str(e.folder) for e in entries)
        if not ids:
            return False
        self.show_tab(TAB_DOWNLOAD)
        return bool(self._download_tab.find_for(ids))

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
        """The series folder in a file manager; where none opens, its path is copied instead and the status bar says so."""
        if open_folder(path):
            return
        QGuiApplication.clipboard().setText(str(path))
        self._status_label.setText(f"No file manager here - folder path copied: {path}")

    # --- Lifecycle --------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._volumes is not None:
            self._volumes.stop()
        if self._download_tab is not None:
            self._download_tab.stop()
        if self._auto_modes is not None:
            self._auto_modes.stop()
        if self._dupe_call is not None:
            self._dupe_call.abandon()
        if self._rename_call is not None and hasattr(self._rename_call, "abandon"):
            self._rename_call.abandon()
        if self._renamer_window is not None:
            self._renamer_window.close()
        self._derived_timer.stop()
        self._stop_states()
        self._stop_scan()
        self._stop_mu()
        self._stop_signatures()
        process_pool.shutdown()                     # a count still running in a subprocess is stopped
        self._db.keep_ui_connection = False
        self._db.release_ui_connection()
        self._cfg["window"] = {"w": self.width(), "h": self.height()}
        if self._splitter_timer.isActive():         # a drag just before closing is kept
            self._cfg[self._CFG_SPLITTER] = self._list.splitter.sizes()
        self._splitter_timer.stop()
        self._col_resize_timer.stop()
        self._save_column_state()
        super().closeEvent(event)


_count_by_folder = process_pool.count_by_folder     # (moved: the duplicate count runs in a subprocess)


def _real_duplicate_finder():
    from ..duplicates import find_duplicate_files

    return find_duplicate_files
