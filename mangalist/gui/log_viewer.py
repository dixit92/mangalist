"""The log viewer: MangaList's two log files in a window (Settings > Logging > View logs).

Owner, 2026-10-10: "does mangalist have an in-application log viewer?" - in the container the web GUI cannot open the
log folder, so the logs are read here.

- **App** (``mangalist.log``) or **Scheduled jobs** (``headless.log``, the container's background runner).
- Newest lines at the bottom; the file's size is polled every :data:`POLL_MS` while the window is open and new lines
  are added as they are written (the view stays at the bottom when it was there). **Pause** holds new lines back
  (counted in the footer) so a stretch can be read; **Follow** shows them and goes back to the bottom.
- Filters: the level (Everything / Warnings and errors / Errors), the area (from the logger names, only the areas the
  loaded lines have), a search box (any part of a record, case ignored). A record's traceback lines go with it.
- Only the last few MB are read at first (:data:`log_tail.CHUNK_BYTES`); **Load older lines** reads the chunk before,
  then the rotated copies. Every read runs off the UI thread (``background.start_call``), one at a time.
- Read-only: the files are opened, read and closed at once (see :mod:`log_tail`); nothing here writes, truncates or
  holds a log. Lines are shown as written - the logging masks secrets before a line reaches the file.
- **Copy selected lines** (or Ctrl+C) and **Open the log folder** (desktop; the container's web GUI cannot open it).

:func:`open_log_viewer` keeps ONE viewer: asking again brings it to the front. The Settings dialog is modal, so the
viewer opened from it is its child window; :meth:`LogViewer.move_to` hands it to the main window when Settings closes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from . import log_tail, theme
from .background import start_call
from .download_style import set_prop, stylesheet
from .download_widgets import button, hbox, label
from .downloads_backend import BackendError
from .links import open_folder as open_folder_default
from .palette import Palette, current

_log = logging.getLogger(__name__)

POLL_MS = 1500
ALL_AREAS = ""

#: The text shown when a log has no file yet, per log key.
EMPTY_TEXT = {
    "app": "Nothing has been written to mangalist.log yet.",
    "jobs": ("Nothing has been written to headless.log yet. It is written by the container's background runner "
             "(the scheduled jobs); the desktop app on its own runs none."),
}
NO_MATCH_TEXT = "No lines match. Change the filters or the search."

#: The line colours by tone: (foreground token, background token or None) - the badges' warn / bad, debug grey.
_TONE_TOKENS = {"bad": ("bad_text", "bad_bg"), "warn": ("warn_text", "warn_bg"), "debug": ("muted_2", None),
                "marker": ("muted", None)}


def viewer_style(pal: Optional[Palette] = None) -> str:
    p = pal or current()
    return f"""
QWidget#logViewer {{ background: {p.background}; color: {p.text}; font-size: 14px; }}
QFrame#logViewerBar {{ background: {p.surface}; border: none; border-bottom: 1px solid {p.border}; }}
QFrame#logViewerFooter {{ background: {p.surface_subtle}; border: none; border-top: 1px solid {p.border}; }}
QListView#logLines {{ border: none; background: {p.surface}; outline: 0; selection-background-color: {p.selected};
                      selection-color: {p.text}; }}
QListView#logLines::item {{ padding: 0 16px; min-height: 20px; }}
QLabel#logEmpty {{ background: {p.surface}; color: {p.muted}; padding: 32px; }}
QPushButton[segment] {{ min-height: 32px; padding: 0 14px; border: 1px solid {p.control}; background: {p.surface}; }}
QPushButton[segment="first"] {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
QPushButton[segment="last"] {{ border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; }}
QPushButton[segment]:checked {{ background: {p.accent_tint}; color: {p.accent}; border-color: {p.accent};
                               font-weight: 600; }}
QPushButton[segment="last"]:checked {{ border-left: 1px solid {p.accent}; }}
"""


def __getattr__(name: str):
    if name == "VIEWER_STYLE":                  # the old constant: the viewer's rules in the current palette
        return viewer_style()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

Row = Tuple[str, int, bool]         # the text of one line, its record's level, a marker


def rows_of(records: Sequence[log_tail.Record], min_level: int = 0, area: str = "", needle: str = "") -> List[Row]:
    """The lines of the records that pass the filters."""
    out: List[Row] = []
    for record in records:
        if log_tail.visible(record, min_level, area, needle):
            out.extend((line, record.level, record.marker) for line in record.lines)
    return out


class LogModel(QAbstractListModel):
    """The shown lines, coloured by level like the app's badges (warnings amber, errors red, debug grey)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.rows: List[Row] = []
        self._brushes: Dict[str, Tuple[Dict[str, QBrush], Dict[str, QBrush]]] = {}     # per palette: (fg, bg)
        self.marker_font = None

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    @staticmethod
    def tone(level: int, marker: bool) -> str:
        if marker:
            return "marker"
        if level >= logging.ERROR:
            return "bad"
        if level >= logging.WARNING:
            return "warn"
        if 0 < level < logging.INFO:
            return "debug"
        return ""

    def brushes(self) -> Tuple[Dict[str, QBrush], Dict[str, QBrush]]:
        """(foreground, background) brushes by tone in the palette in force (made once per palette, read at
        ``data()`` time so a switch of Appearance shows at the next repaint)."""
        pal = current()
        made = self._brushes.get(pal.name)
        if made is None:
            fg = {tone: QBrush(QColor(getattr(pal, f))) for tone, (f, _b) in _TONE_TOKENS.items()}
            bg = {tone: QBrush(QColor(getattr(pal, b))) for tone, (_f, b) in _TONE_TOKENS.items() if b}
            made = self._brushes[pal.name] = (fg, bg)
        return made

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        text, level, marker = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return text
        if role == Qt.ItemDataRole.ForegroundRole:
            return self.brushes()[0].get(self.tone(level, marker))
        if role == Qt.ItemDataRole.BackgroundRole:
            return self.brushes()[1].get(self.tone(level, marker))
        if role == Qt.ItemDataRole.FontRole and marker:
            return self.marker_font
        return None

    def reset(self, rows: List[Row]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def append(self, rows: List[Row]) -> None:
        if not rows:
            return
        first = len(self.rows)
        self.beginInsertRows(QModelIndex(), first, first + len(rows) - 1)
        self.rows.extend(rows)
        self.endInsertRows()


class LogList(QListView):
    """The lines: several can be selected; Ctrl+C copies all of them (the default copies only the current one)."""

    def __init__(self, on_copy: Callable[[], object], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._on_copy = on_copy

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self._on_copy()
            event.accept()
            return
        super().keyPressEvent(event)


def _sync_run(fn, on_done=None, on_error=None, on_finished=None):
    """Run *fn* at once on this thread, with :func:`start_call`'s callbacks (tests)."""
    from .background import describe_error

    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - same as a background call
        if on_error is not None:
            on_error(describe_error(exc))
    else:
        if on_done is not None:
            on_done(result)
    if on_finished is not None:
        on_finished()
    return None


class LogViewer(QWidget):
    """The log viewer window. Keyword hooks are for tests: *log_dir* (default ``paths.log_dir()``), *run* (how a read
    is run; default off the UI thread), *poll_ms* (0: no timer), *chunk_bytes*, *open_folder*, *copy_text*."""

    def __init__(self, parent: Optional[QWidget] = None, *, log: str = "app", log_dir: Optional[Path] = None,
                 run: Optional[Callable[..., Any]] = None, poll_ms: int = POLL_MS,
                 chunk_bytes: int = log_tail.CHUNK_BYTES,
                 open_folder: Optional[Callable[[str], object]] = None,
                 copy_text: Optional[Callable[[str], object]] = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("logViewer")
        self.setWindowTitle("MangaList - logs")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1180, 720)
        self._dir = Path(log_dir) if log_dir is not None else paths.log_dir()
        self._run = run or start_call
        self._chunk = chunk_bytes
        self._open_folder = open_folder or open_folder_default
        self._copy_text = copy_text or (lambda text: QApplication.clipboard().setText(text))
        self._log_key = log if log in log_tail.LOG_FILE else "app"
        self._call: Any = None
        self._busy = False
        self._gen = 0
        self._closed = False
        self._reset_state()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("logViewerBar")
        bv = QVBoxLayout(bar)
        bv.setContentsMargins(16, 12, 16, 12)
        bv.setSpacing(10)
        self.log_buttons = {}
        self._log_group = QButtonGroup(self)
        self._log_group.setExclusive(True)
        for i, (key, text, _name) in enumerate(log_tail.LOGS):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setChecked(key == self._log_key)
            btn.setProperty("segment", "first" if i == 0 else "last")
            btn.setAccessibleName(f"Show the {text} log")
            btn.clicked.connect(lambda _=False, k=key: self.show_log(k))
            self._log_group.addButton(btn)
            self.log_buttons[key] = btn
        segments = hbox(*self.log_buttons.values(), spacing=0)
        self.level_combo = QComboBox()
        self.level_combo.setAccessibleName("Level")
        for key, text, _level in log_tail.LEVEL_FILTERS:
            self.level_combo.addItem(text, key)
        self.area_combo = QComboBox()
        self.area_combo.setAccessibleName("Area")
        self.area_combo.setMinimumWidth(170)
        self.area_combo.addItem("All areas", ALL_AREAS)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the lines")
        self.search.setAccessibleName("Search")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        row1 = hbox(spacing=10)
        row1.addLayout(segments)
        row1.addSpacing(14)
        row1.addWidget(label("Show"))
        row1.addWidget(self.level_combo)
        row1.addWidget(label("Area"))
        row1.addWidget(self.area_combo)
        row1.addWidget(self.search, 1)
        bv.addLayout(row1)

        self.info_label = label("", "mono")
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_older = button("Load older lines", link=True)
        self.btn_follow = button("Pause", tip="Pause: hold new lines back while you read. Follow: show them and "
                                              "keep to the newest line.")
        self.btn_copy = button("Copy selected lines")
        self.btn_folder = button("Open the log folder", tip="On the desktop: opens the folder in the file manager.")
        self.info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)   # never widens
        row2 = hbox(self.info_label, spacing=10)
        row2.setStretch(0, 1)
        for widget in (self.btn_older, self.btn_follow, self.btn_copy, self.btn_folder):
            row2.addWidget(widget)
        bv.addLayout(row2)
        outer.addWidget(bar)

        self.model = LogModel(self)
        self.view = LogList(self.copy_selected)
        self.view.setObjectName("logLines")
        self.view.setModel(self.model)
        self.view.setUniformItemSizes(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setWordWrap(False)
        self.view.setTextElideMode(Qt.TextElideMode.ElideNone)
        mono = theme.font(12, mono=True)
        self.view.setFont(mono)
        italic = theme.font(12, mono=True)
        italic.setItalic(True)
        self.model.marker_font = italic
        self.empty_label = QLabel("")
        self.empty_label.setObjectName("logEmpty")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.empty_label)
        outer.addWidget(self.stack, 1)

        foot = QFrame()
        foot.setObjectName("logViewerFooter")
        self.status_label = label("")
        fl = hbox(self.status_label, None, margins=(16, 8, 16, 8))
        foot.setLayout(fl)
        outer.addWidget(foot)

        theme.bind_sheet(self, lambda: stylesheet() + viewer_style())

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.rebuild)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.level_combo.currentIndexChanged.connect(lambda _i: self.rebuild())
        self.area_combo.currentIndexChanged.connect(lambda _i: self.rebuild())
        self.btn_older.clicked.connect(self.load_older)
        self.btn_follow.clicked.connect(self.toggle_follow)
        self.btn_copy.clicked.connect(self.copy_selected)
        self.btn_folder.clicked.connect(self.open_log_folder)
        self.view.selectionModel().selectionChanged.connect(lambda *_a: self._update_buttons())

        self._timer = QTimer(self)
        self._timer.setInterval(max(1, poll_ms))
        self._timer.timeout.connect(self.poll)
        if poll_ms > 0:
            self._timer.start()
        _log.info("Log viewer opened (%s)", log_tail.LOG_FILE[self._log_key])
        self.load()

    # --- state ---------------------------------------------------------------------------------------------------

    def _reset_state(self) -> None:
        self.records: List[log_tail.Record] = []
        self._pending: List[log_tail.Record] = []
        self._ident: Optional[log_tail.Ident] = None
        self._end = 0
        self._older: Optional[Tuple[log_tail.Ident, int]] = None
        self._more = False
        self._loaded = False
        self._missing = False
        self._total = 0
        self._files = 0
        self._areas: set = set()
        self.following = True

    @property
    def log_key(self) -> str:
        return self._log_key

    @property
    def path(self) -> Path:
        return self._dir / log_tail.LOG_FILE[self._log_key]

    def filters(self) -> Tuple[int, str, str]:
        level = log_tail.LEVEL_FILTERS[max(0, self.level_combo.currentIndex())][2]
        return level, self.area_combo.currentData() or ALL_AREAS, self.search.text().strip().lower()

    # --- reading -------------------------------------------------------------------------------------------------

    def _start(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        """Run one read off the UI thread; its result counts only if the log shown is still the one it was for."""
        gen = self._gen
        name = self.path.name

        def guarded():
            try:
                return fn()
            except OSError as exc:              # a readable reason; the path is in the window already
                raise BackendError(f"could not read {name}: {exc.strerror or type(exc).__name__}") from exc

        def done(result):
            self._busy = False
            if gen == self._gen and not self._closed:
                on_done(result)

        def failed(message):
            self._busy = False
            if gen == self._gen and not self._closed:
                _log.warning("Log viewer: %s", message)
                self._say(f"Could not read the log: {message}.", "bad")
                self._update_buttons()

        self._busy = True
        self._update_buttons()
        self._call = self._run(guarded, done, failed)

    def load(self) -> None:
        """(Re)read the shown log from its end."""
        self._abandon()
        self._gen += 1
        self._reset_state()
        self.model.reset([])
        self._say("Reading the log...", "")
        path, chunk = self.path, self._chunk

        def read():
            tail = log_tail.read_tail(path, chunk)
            files = log_tail.rotated_files(path)
            more = tail is not None and log_tail.has_older(path, tail.ident, tail.start)
            return tail, more, sum(st.st_size for _p, st in files), len(files)

        self._start(read, self._loaded_tail)

    def _loaded_tail(self, result) -> None:
        tail, more, total, files = result
        self._loaded = True
        self._total, self._files = total, files
        if tail is None:
            self._missing = True
            self._show_empty()
            self._say("This window shows the log as soon as it is written.", "")
            self._update_buttons()
            return
        self._ident, self._end = tail.ident, tail.end
        self._older, self._more = (tail.ident, tail.start), more
        self.records = tail.records
        self._note_areas(self.records)
        self.rebuild()
        self.view.scrollToBottom()
        self._say_following()

    def poll(self) -> None:
        """Read what was appended since the last read (the timer calls this; a read in flight is not doubled)."""
        if self._busy or not self._loaded or self._closed:
            return
        path, ident, end = self.path, self._ident, self._end

        def read():
            update = log_tail.read_new(path, ident, end)
            files = log_tail.rotated_files(path)
            return update, sum(st.st_size for _p, st in files), len(files)

        self._start(read, self._polled)

    def _polled(self, result) -> None:
        update, total, files = result
        self._total, self._files = total, files
        if update.missing:
            self._refresh_info()
            return
        self._ident, self._end = update.ident, update.end
        if self._missing:
            self._missing = False
            self._older, self._more = (update.ident, 0), False
        if not update.records:
            self._refresh_info()
            return
        self._note_areas(update.records)
        if self.following:
            self._add(update.records)
        else:
            log_tail.append_records(self._pending, update.records)
            self._say_paused()
        self._refresh_info()

    def _add(self, new: List[log_tail.Record]) -> None:
        """Put *new* after the shown records; the view stays at the bottom when it was there."""
        bar = self.view.verticalScrollBar()
        at_bottom = self.model.rowCount() == 0 or bar.value() >= bar.maximum() - 2
        level, area, needle = self.filters()
        last = self.records[-1] if self.records else None
        last_shown = last is not None and log_tail.visible(last, level, area, needle)
        before = len(self.records)
        merged = log_tail.append_records(self.records, new)
        rows: List[Row] = []
        if merged and last_shown and last is not None:
            rows.extend((line, last.level, last.marker) for line in last.lines[-merged:])
        rows.extend(rows_of(self.records[before:], level, area, needle))
        self.model.append(rows)
        self._show_list_or_empty()
        if at_bottom:
            self.view.scrollToBottom()

    def load_older(self) -> None:
        """Read the chunk before the oldest line shown (then the rotated copies)."""
        if self._busy or not self._more or self._older is None:
            return
        path, (ident, start), chunk = self.path, self._older, self._chunk
        self._start(lambda: log_tail.read_older(path, ident, start, chunk), self._loaded_older)

    def _loaded_older(self, older: "log_tail.Older") -> None:
        if older.gone:
            self._more = False
            self._say("The older lines are not there any more: the log was rotated and the oldest file deleted.",
                      "warn")
            self._update_buttons()
            return
        if older.chunk is None:
            self._more = False
            self._update_buttons()
            return
        rows_before = self.model.rowCount()
        self.records = log_tail.prepend_records(older.chunk.records, self.records)
        self._older, self._more = (older.chunk.ident, older.chunk.start), older.more
        self._note_areas(older.chunk.records)
        self.rebuild(keep_bottom=False)
        added = self.model.rowCount() - rows_before
        if added > 0:
            self.view.scrollTo(self.model.index(added, 0), QAbstractItemView.ScrollHint.PositionAtTop)
        _log.info("Log viewer loaded older lines of %s (from byte %d)", older.chunk.name, older.chunk.start)
        if self.following:
            self._say_following()
        else:
            self._say_paused()

    def show_log(self, key: str) -> None:
        """Switch between the App and the Scheduled jobs log."""
        if key not in log_tail.LOG_FILE:
            return
        self.log_buttons[key].setChecked(True)
        if key == self._log_key and self._loaded:
            return
        self._log_key = key
        self.area_combo.blockSignals(True)
        self.area_combo.setCurrentIndex(0)
        self.area_combo.blockSignals(False)
        _log.info("Log viewer shows %s", log_tail.LOG_FILE[key])
        self.load()

    # --- showing -------------------------------------------------------------------------------------------------

    def rebuild(self, keep_bottom: bool = True) -> None:
        """Apply the filters to every loaded record."""
        self._search_timer.stop()
        self.model.reset(rows_of(self.records, *self.filters()))
        self._show_list_or_empty()
        if keep_bottom:
            self.view.scrollToBottom()
        self._refresh_info()

    def _note_areas(self, records: Sequence[log_tail.Record]) -> None:
        found = {r.area for r in records if r.headed} - self._areas
        if not found:
            return
        self._areas |= found
        current = self.area_combo.currentData() or ALL_AREAS
        self.area_combo.blockSignals(True)
        self.area_combo.clear()
        self.area_combo.addItem("All areas", ALL_AREAS)
        for area in sorted(self._areas, key=log_tail.area_sort_key):
            self.area_combo.addItem(area, area)
        self.area_combo.setCurrentIndex(max(0, self.area_combo.findData(current)))
        self.area_combo.blockSignals(False)

    def _show_empty(self) -> None:
        self.empty_label.setText(f"{EMPTY_TEXT[self._log_key]}\n\n{self.path}")
        self.stack.setCurrentWidget(self.empty_label)
        self._refresh_info()

    def _show_list_or_empty(self) -> None:
        if self.model.rowCount() == 0 and self.records:
            self.empty_label.setText(NO_MATCH_TEXT)
            self.stack.setCurrentWidget(self.empty_label)
        else:
            self.stack.setCurrentWidget(self.view)

    def _refresh_info(self) -> None:
        name = log_tail.LOG_FILE[self._log_key]
        if self._missing:
            self.info_label.setText(f"{name} - not written yet")
        elif self._loaded:
            older = self._files - 1
            files = f" and {older} older file{'s' if older != 1 else ''}" if older > 0 else ""
            lines = log_tail.line_count(self.records)
            shown = self.model.rowCount()
            match = f", {shown:,} shown" if shown != lines else ""
            text = f"{name}{files} - {log_tail.size_text(self._total)} - {lines:,} lines loaded{match}"
            self.info_label.setText(text)
            more = "\nOlder lines are not loaded yet: Load older lines." if self._more else ""
            self.info_label.setToolTip(f"{text}\n{self.path}{more}")
        else:
            self.info_label.setText(name)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.btn_older.setEnabled(not self._busy)
        self.btn_older.setVisible(self._loaded and not self._missing and self._more)
        self.btn_copy.setEnabled(bool(self.view.selectionModel().selectedRows()))
        self.btn_follow.setText("Pause" if self.following else "Follow")
        set_prop(self.btn_follow, "primary", not self.following)

    def _say(self, text: str, tone: str) -> None:
        self.status_label.setText(text)
        set_prop(self.status_label, "tone", tone)

    def _say_following(self) -> None:
        self._say("Following: new lines appear at the bottom as they are written.", "")

    def _say_paused(self) -> None:
        waiting = log_tail.line_count(self._pending)
        self._say(f"Paused - {waiting:,} new line{'s' if waiting != 1 else ''} waiting. Follow shows them."
                  if waiting else "Paused - new lines are held back. Follow shows them.", "warn")

    # --- actions -------------------------------------------------------------------------------------------------

    def toggle_follow(self) -> None:
        self.set_following(not self.following)

    def set_following(self, on: bool) -> None:
        self.following = on
        if on:
            pending, self._pending = self._pending, []
            if pending:
                self._add(pending)
            self.view.scrollToBottom()
            self._say_following()
            self._refresh_info()
        else:
            self._say_paused()
        self._update_buttons()

    def selected_text(self) -> str:
        rows = sorted(index.row() for index in self.view.selectionModel().selectedRows())
        return "\n".join(self.model.rows[r][0] for r in rows if r < len(self.model.rows))

    def copy_selected(self) -> int:
        text = self.selected_text()
        if not text:
            return 0
        self._copy_text(text)
        count = text.count("\n") + 1
        self._say(f"Copied {count:,} line{'s' if count != 1 else ''}.", "ok")
        return count

    def open_log_folder(self) -> bool:
        folder = self._dir
        try:
            folder.mkdir(parents=True, exist_ok=True)           # nothing is written before the first message
            opened = self._open_folder(str(folder))
        except OSError:
            opened = False
        if opened is False:
            self._say(f"Could not open the folder here (the container's web view cannot). It is {folder}.", "bad")
            return False
        return True

    def move_to(self, parent: Optional[QWidget]) -> None:
        """Give the window a new owner (Settings closes: the main window), keeping where it is and whether shown."""
        if parent is self.parentWidget():
            return
        geometry, shown = self.geometry(), self.isVisible()
        self.setParent(parent, Qt.WindowType.Window)
        self.setGeometry(geometry)
        if shown:
            self.show()

    def _abandon(self) -> None:
        call, self._call = self._call, None
        if call is not None and hasattr(call, "abandon"):
            call.abandon()
        self._busy = False

    def stop(self) -> None:
        """Stop polling and drop any read in flight."""
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._search_timer.stop()
        self._abandon()
        _log.info("Log viewer closed")

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)


_viewer: Optional[LogViewer] = None


def current_viewer() -> Optional[LogViewer]:
    """The open viewer, if any."""
    global _viewer
    if _viewer is not None:
        try:
            from shiboken6 import isValid
        except ImportError:  # pragma: no cover - shiboken ships with PySide6
            return _viewer
        if not isValid(_viewer) or _viewer._closed:
            _viewer = None
    return _viewer


def open_log_viewer(parent: Optional[QWidget] = None, **kw: Any) -> LogViewer:
    """Show the log viewer (one at a time: an open one is moved to *parent* and brought to the front)."""
    global _viewer
    viewer = current_viewer()
    if viewer is None:
        viewer = _viewer = LogViewer(parent, **kw)
    else:
        viewer.move_to(parent)
    viewer.show()
    viewer.raise_()
    viewer.activateWindow()
    return viewer


__all__ = ["EMPTY_TEXT", "LogModel", "LogViewer", "NO_MATCH_TEXT", "POLL_MS", "current_viewer", "open_log_viewer",
           "rows_of"]
