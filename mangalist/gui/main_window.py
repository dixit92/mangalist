"""Main application window."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

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
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import config, mu_cache, store
from .._version import __version__
from ..models import MangaEntry
from ..scanner import LibraryScan, record_library_scan, scan_library
from ..store import Journal, Root, RootError
from .detail_panel import DetailPanel
from .mu_picker import MuPickerDialog
from .mu_worker import MuWorker, _apply_cache, _clear_examined_if_newly_licensed
from .roots_dialog import RootsDialog
from .table_model import (
    COLUMNS, COL_BEHIND, COL_DUPE, COL_EXAMINED, COL_LICENSED,
    COL_MU_TITLE, COL_TITLE, MangaTableModel,
)


# ---------------------------------------------------------------------------
# Background scan worker
# ---------------------------------------------------------------------------


_log = logging.getLogger(__name__)


class ScanWorker(QObject):
    """Scans every root, then records the series rows (a renamed series folder keeps its link)."""

    progress = Signal(int, int, str)
    finished = Signal(object)  # LibraryScan, with .renamed = [(old folder, new folder)]
    failed = Signal(str)

    def __init__(self, roots: Sequence[Root], db=None):
        super().__init__()
        self._roots = list(roots)
        self._db = db

    def run(self) -> None:
        try:
            result = scan_library(
                self._roots,
                progress=lambda d, t, name: self.progress.emit(d, t, name),
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
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


# ---------------------------------------------------------------------------
# Toolbar helpers
# ---------------------------------------------------------------------------


def _toolbar_spacer(width: int) -> QWidget:
    spacer = QWidget()
    spacer.setFixedWidth(width)
    return spacer


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

    def set_dupes_only(self, enabled: bool) -> None:
        """Filter to show only duplicate MU matches."""
        self._dupes_only = enabled
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        """Check if row should be shown based on text filter AND dupe filter."""
        # First apply the standard text filter
        if not super().filterAcceptsRow(source_row, source_parent):
            return False

        # Then apply duplicates-only filter if enabled
        if self._dupes_only:
            source_model = self.sourceModel()
            if source_model is not None:
                return source_model.is_duplicate(source_row)

        return True


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


def render_app_icon(size: int) -> QPixmap:
    """Paint the app icon at *size* px (also used by packaging/make_icon.py for the installers)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    # Rounded gradient background (purple -> blue, evoking Volumes/Chapters/Both)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#6a1b9a"))
    grad.setColorAt(1.0, QColor("#1565c0"))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    radius = max(2, size // 6)
    p.drawRoundedRect(0, 0, size, size, radius, radius)

    # Stylized white "M" glyph
    p.setPen(QPen(QColor("white")))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.7))
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignCenter, "M")
    p.end()
    return pm


def _build_app_icon() -> QIcon:
    """Generate a simple multi-resolution app icon at runtime (no asset file)."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(render_app_icon(size))
    return icon


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MangaList {__version__}")
        self._app_icon = _build_app_icon()
        self.setWindowIcon(self._app_icon)
        QGuiApplication.setWindowIcon(self._app_icon)

        self._cfg = config.load()
        self._db = store.get_store()
        self._recover_journal()
        win = self._cfg.get("window") or {}
        self.resize(int(win.get("w", 1200)), int(win.get("h", 720)))

        self._model = MangaTableModel()
        self._proxy = _SortProxy(self)
        self._proxy.setSourceModel(self._model)

        self._build_ui()

        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._mu_thread: QThread | None = None
        self._mu_worker: MuWorker | None = None
        self._mu_entries: List[MangaEntry] = []

        # Debounce timer so rapid column-resize events don't thrash config I/O.
        self._col_resize_timer = QTimer(self)
        self._col_resize_timer.setSingleShot(True)
        self._col_resize_timer.setInterval(400)
        self._col_resize_timer.timeout.connect(self._save_column_state)

        self._show_roots()

    # --- UI construction -------------------------------------------------

    _BUTTON_STYLE = (
        "QPushButton {"
        "  padding: 4px 12px;"
        "  border: 1px solid #888;"
        "  border-radius: 4px;"
        "  background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #f5f5f5,stop:1 #dcdcdc);"
        "  color: #111;"
        "  font-weight: 600;"
        "}"
        "QPushButton:hover {"
        "  background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #e8f0fe,stop:1 #c5d8fc);"
        "  border-color: #5585d6;"
        "}"
        "QPushButton:pressed {"
        "  background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #b8ccf5,stop:1 #d0e2ff);"
        "}"
        "QPushButton:disabled {"
        "  color: #999;"
        "  background: #e8e8e8;"
        "  border-color: #bbb;"
        "}"
    )

    def _make_button(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setStyleSheet(self._BUTTON_STYLE)
        return btn

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setContentsMargins(4, 2, 4, 2)
        self.addToolBar(toolbar)

        btn_choose = self._make_button("Choose Manga Root…")
        btn_choose.setToolTip("Add a folder of series folders as a root and scan")
        btn_choose.clicked.connect(self._on_choose_root)
        toolbar.addWidget(btn_choose)

        toolbar.addWidget(_toolbar_spacer(6))

        btn_roots = self._make_button("Roots…")
        btn_roots.setToolTip("Manage roots and their exclusions")
        btn_roots.clicked.connect(self._on_roots)
        toolbar.addWidget(btn_roots)
        self._btn_roots = btn_roots

        toolbar.addWidget(_toolbar_spacer(6))

        btn_rescan = self._make_button("Rescan")
        btn_rescan.clicked.connect(self._on_rescan)
        toolbar.addWidget(btn_rescan)
        self._btn_rescan = btn_rescan

        toolbar.addSeparator()

        toolbar.addWidget(QLabel("Root: "))
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setMinimumWidth(420)
        toolbar.addWidget(self._path_edit)

        toolbar.addSeparator()

        # Duplicates filter checkbox
        self._dupes_checkbox = QCheckBox("Show Dupes Only")
        self._dupes_checkbox.setToolTip("Show only entries with duplicate MU matches")
        self._dupes_checkbox.toggled.connect(self._on_dupes_filter_changed)
        toolbar.addWidget(self._dupes_checkbox)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Filter: "))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Type to filter title / english / verdict…")
        self._filter_edit.setMaximumWidth(280)
        self._filter_edit.textChanged.connect(self._proxy.setFilterFixedString)
        toolbar.addWidget(self._filter_edit)

        toolbar.addSeparator()

        btn_mu_start = self._make_button("▶ MU Lookup")
        btn_mu_start.setToolTip("Start MangaUpdates lookup for all entries")
        btn_mu_start.clicked.connect(self._on_mu_start)
        toolbar.addWidget(btn_mu_start)
        self._btn_mu_start = btn_mu_start

        toolbar.addWidget(_toolbar_spacer(4))

        btn_mu_stop = self._make_button("■ Stop")
        btn_mu_stop.setToolTip("Stop MangaUpdates lookup")
        btn_mu_stop.clicked.connect(self._on_mu_stop)
        btn_mu_stop.setEnabled(False)
        toolbar.addWidget(btn_mu_stop)
        self._btn_mu_stop = btn_mu_stop

        toolbar.addWidget(_toolbar_spacer(8))

        chk_autostart = QCheckBox("Auto-start MU")
        chk_autostart.setToolTip("Automatically start MU lookup after each scan")
        chk_autostart.setChecked(bool(self._cfg.get("mu_autostart", False)))
        chk_autostart.toggled.connect(self._on_mu_autostart_toggled)
        toolbar.addWidget(chk_autostart)
        self._chk_autostart = chk_autostart

        # Central splitter
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        # All columns user-resizable; last column does not auto-stretch.
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_context_menu)
        self._table.sortByColumn(COL_TITLE, Qt.AscendingOrder)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.clicked.connect(self._on_table_clicked)
        left_layout.addWidget(self._table)

        self._detail = DetailPanel()

        splitter.addWidget(left)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        self._splitter = splitter

        saved_sizes = self._cfg.get("splitter_sizes")
        if saved_sizes and len(saved_sizes) == 2:
            splitter.setSizes([int(s) for s in saved_sizes])
        else:
            splitter.setSizes([800, 360])

        splitter.splitterMoved.connect(self._on_splitter_moved)
        self.setCentralWidget(splitter)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_label = QLabel("Ready")
        sb.addWidget(self._status_label, 1)
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(200)
        self._progress.setVisible(False)
        sb.addPermanentWidget(self._progress)

        # Connect selection (rebind in case it returned None earlier)
        sel = self._table.selectionModel()
        if sel is not None:
            sel.selectionChanged.connect(self._on_row_changed)

        # Restore or apply default column order, then apply visibility.
        self._restore_column_state()
        self._apply_hidden_columns()
        header.sectionMoved.connect(self._on_column_moved)
        header.sectionResized.connect(self._on_section_resized)

    # --- Slots -----------------------------------------------------------

    # --- Roots -----------------------------------------------------------

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

    def _show_roots(self) -> None:
        roots = self._roots()
        if not roots:
            self._path_edit.setText("")
            self._path_edit.setToolTip("No root yet: choose a Manga Root")
        elif len(roots) == 1:
            self._path_edit.setText(roots[0].path)
            self._path_edit.setToolTip(f"{roots[0].name}: {roots[0].path}")
        else:
            self._path_edit.setText(f"{len(roots)} roots: " + ", ".join(r.name for r in roots))
            self._path_edit.setToolTip("\n".join(f"{r.name}: {r.path}" for r in roots))

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

    def _make_roots_dialog(self) -> RootsDialog:
        return RootsDialog(self._db, self, button_style=self._BUTTON_STYLE)

    def _on_roots(self) -> None:
        dlg = self._make_roots_dialog()
        if dlg.exec() == RootsDialog.Accepted:
            self._after_roots_changed()

    def _after_roots_changed(self) -> None:
        self._show_roots()
        if self._roots() and self._model.rowCount():
            self._status_label.setText("Roots changed - Rescan to apply")

    def _on_rescan(self) -> None:
        if not self._roots():
            QMessageBox.information(self, "No folder", "Choose a Manga Root first.")
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

    def _on_progress(self, done: int, total: int, name: str) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        if name:
            self._status_label.setText(f"Scanning ({done}/{total}): {name}")

    def _on_scan_finished(self, result) -> None:
        if isinstance(result, LibraryScan):
            entries: List[MangaEntry] = result.entries
            loose = result.loose
            errors = result.errors
            renamed = getattr(result, "renamed", [])
        else:  # a plain list of entries
            entries, loose, errors, renamed = list(result), [], [], []
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

        self._model.set_entries(entries)
        # Only auto-size columns when the user has no saved column state.
        if not self._cfg.get("column_state"):
            self._table.resizeColumnsToContents()
            # Give Title a generous default width but keep it user-resizable.
            header = self._table.horizontalHeader()
            title_w = max(self._table.columnWidth(COL_TITLE), 360)
            header.resizeSection(COL_TITLE, title_w)
            # Examined column: narrow and centered.
            header.resizeSection(COL_EXAMINED, 32)

        n = len(entries)
        n_vol = sum(1 for e in entries if e.verdict.value == "Volumes")
        n_ch = sum(1 for e in entries if e.verdict.value == "Chapters")
        n_both = sum(1 for e in entries if e.verdict.value == "Both")
        n_unk = n - n_vol - n_ch - n_both
        text = f"{n} folder(s)  —  Volumes: {n_vol}, Chapters: {n_ch}, Both: {n_both}, Unknown: {n_unk}"
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
            text += f"  —  {len(renamed)} renamed folder(s) kept their link"
        if errors:
            text += f"  —  {len(errors)} root(s) not reachable"
            tips.append("Not reachable:")
            tips += [f"  {e}" for e in errors]
        self._status_label.setText(text)
        self._status_label.setToolTip("\n".join(tips))
        self._progress.setVisible(False)
        self._mu_entries = list(entries)

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

    # --- MangaUpdates background lookup ----------------------------------

    def _start_mu_lookup(self, entries: List[MangaEntry]) -> None:
        """Start a background worker to enrich *entries* with MU data."""
        if self._mu_thread is not None:
            self._mu_worker.abort()
            self._mu_thread = None
            self._mu_worker = None

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
        self._btn_mu_start.setEnabled(False)
        self._btn_mu_stop.setEnabled(True)
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
        for col in (COL_MU_TITLE, COL_LICENSED, COL_BEHIND):
            self._table.resizeColumnToContents(col)

    def _on_mu_thread_finished(self) -> None:
        self._model.set_mu_processing_row(None)
        self._mu_thread = None
        self._mu_worker = None
        self._btn_mu_start.setEnabled(True)
        self._btn_mu_stop.setEnabled(False)

    def _on_mu_start(self) -> None:
        if not self._mu_entries:
            QMessageBox.information(self, "No data", "Scan a folder first.")
            return
        self._start_mu_lookup(self._mu_entries)

    def _on_mu_stop(self) -> None:
        if self._mu_worker is not None:
            self._mu_worker.abort()
        self._btn_mu_start.setEnabled(True)
        self._btn_mu_stop.setEnabled(False)

    def _on_mu_autostart_toggled(self, checked: bool) -> None:
        self._cfg["mu_autostart"] = checked
        config.save(self._cfg)

    def _on_dupes_filter_changed(self, checked: bool) -> None:
        """Toggle showing only duplicate MU matches."""
        self._proxy.set_dupes_only(checked)
        # Update status label to show how many duplicates found
        if checked:
            dupes_count = sum(1 for i in range(self._model.rowCount())
                              if self._model.is_duplicate(i))
            self._status_label.setText(f"Showing {dupes_count} duplicate entries")
        else:
            self._status_label.setText("Showing all entries")

    # --- Column order & state --------------------------------------------

    # Desired logical order: ✓ Title MU-Title Behind Licensed Verdict
    #                        Last-Modified Alt-Title Files Subfolders Vol% Ch% Both%
    _DEFAULT_COL_ORDER = [
        "✓", "Dupe", "Title", "MU Title", "Behind", "Licensed", "Completed", "Verdict",
        "Last Modified", "Alternative Title", "Files", "Subfolders",
        "Vol %", "Ch %", "Both %",
    ]

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
        """Restore saved header state, or apply the default order."""
        state_hex = self._cfg.get("column_state") or ""
        header = self._table.horizontalHeader()
        if state_hex:
            try:
                ok = header.restoreState(QByteArray.fromHex(state_hex.encode()))
                if ok and header.count() == len(COLUMNS):
                    # Re-apply stretch/movable settings after restore (Qt may reset them)
                    header.setStretchLastSection(False)
                    header.setSectionsMovable(True)
                    return
                # Section count mismatch (e.g. new column added) — discard stale state.
                self._cfg["column_state"] = ""
                config.save(self._cfg)
            except Exception:  # noqa: BLE001
                pass
        self._apply_default_column_order()
        # Ensure settings are applied after default order too
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)

    def _save_column_state(self) -> None:
        state = self._table.horizontalHeader().saveState()
        self._cfg["column_state"] = bytes(state.toHex()).decode()
        config.save(self._cfg)

    def _on_column_moved(self, _logical: int, _old: int, _new: int) -> None:
        self._save_column_state()

    def _on_section_resized(self, _logical: int, _old: int, _new: int) -> None:
        self._col_resize_timer.start()

    def _on_splitter_moved(self, _pos: int, _idx: int) -> None:
        self._cfg["splitter_sizes"] = self._splitter.sizes()
        config.save(self._cfg)

    # --- Column visibility -----------------------------------------------

    def _apply_hidden_columns(self) -> None:
        """Hide/show columns according to config."""
        hidden = set(self._cfg.get("hidden_columns", []))
        header = self._table.horizontalHeader()
        for col, name in enumerate(COLUMNS):
            header.setSectionHidden(col, name in hidden)

    def _on_header_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self._table.horizontalHeader())
        hidden = set(self._cfg.get("hidden_columns", []))
        for col, name in enumerate(COLUMNS):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name not in hidden)
            act.setData(col)
        chosen = menu.exec(self._table.horizontalHeader().mapToGlobal(pos))
        if chosen is None:
            return
        col = chosen.data()
        name = COLUMNS[col]
        if name in hidden:
            hidden.discard(name)
        else:
            hidden.add(name)
        self._cfg["hidden_columns"] = sorted(hidden)
        config.save(self._cfg)
        self._apply_hidden_columns()

    def _on_row_changed(self, *_args) -> None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            self._detail.show_entry(None)
            return
        src_index: QModelIndex = self._proxy.mapToSource(idx)
        entry = self._model.entry_at(src_index.row())
        self._detail.show_entry(entry)

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

        if n == 1:
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
            menu.addSeparator()
        else:
            act_open = act_copy_path = act_copy_title = act_fix_mu = act_open_mu = None
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

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        if chosen is act_open and entries:
            self._open_in_explorer(entries[0].folder)
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
            import webbrowser
            webbrowser.open(entries[0].mu_url)
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
        self._cfg["window"] = {"w": self.width(), "h": self.height()}
        self._save_column_state()
        super().closeEvent(event)
