"""The Renamer window: bring a series, a library or every library to the naming scheme (renamer cycle, 2026-10-10).

Opened from the List: right-click a series > "Rename to the scheme…" (the pilot: one series first, then check its read
marks in MangaPixer), or "Rename library…" (the library picked, or every library). It shows:

- the dry run's summary: how many files would be renamed, are already named by the scheme, are left alone, collide;
  how many names of each pattern were found; the plain warning about MangaPixer's read state;
- per series, old -> new for every file, coloured: renamed (blue), unchanged (grey), left alone (amber), collision (red);
  the series folder itself on the first row when it is not named ``<romaji> [<English>]`` (owner, 2026-10-15; it is
  renamed only by this window's Rename, never automatically); a volume renamed only for the new volume format
  (``Vol. 001 [group]``, no series title; owner, 2026-10-16) is marked as waiting for this window's Rename too;
- "To check": the files left alone, the name collisions (with "Review duplicates…"), the titles the length rule would
  shorten and the titles that hold a volume or chapter word;
- Apply (a confirmation naming the counts first), a busy state while it works, the result, and "Undo last batch".

Every action runs off the UI thread through :class:`mangalist.renamer.Renamer` (the core decides everything; this is a
view). After an apply or an undo the window emits ``library_changed(root ids)`` - the main window rescans them - and
looks again.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..renamer import (
    COLLISION,
    FOLDER_NO_TITLE,
    LEFT_ALONE,
    RENAME,
    UNCHANGED,
    UNIT_CHAPTER,
    UNIT_VOLUME,
    BatchRecord,
    BatchResult,
    DryRun,
    FileUnits,
    SeriesPreview,
    file_units,
)
from . import palette, theme
from .download_widgets import ROLE_CHIPS, ROLE_SUB, TwoLineDelegate
from .links import OPEN_FOLDER_LABEL
from .links import open_folder as _open_folder

_log = logging.getLogger(__name__)

SCOPE_SERIES, SCOPE_ROOT, SCOPE_ALL = "series", "root", "all"


class _SeriesRowDelegate(TwoLineDelegate):
    """A series row as wide as the list (a long title elides instead of widening the row past the chips)."""

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setWidth(1)
        return hint

#: The colour of each status: a palette token (accent blue, grey, amber, red), read when the preview is filled.
STATUS_TOKENS = {RENAME: "accent", UNCHANGED: "muted_2", LEFT_ALONE: "caution_text", COLLISION: "bad_text"}


def status_colors() -> dict:
    return {status: getattr(palette.current(), token) for status, token in STATUS_TOKENS.items()}


def __getattr__(name: str):
    if name == "STATUS_COLORS":                 # the old constant: the colours in the current palette
        return status_colors()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# A preview: the table always shows what renaming WOULD do (applying re-runs the preview) - owner, 2026-10-10.
STATUS_TEXT = {RENAME: "will be renamed", UNCHANGED: "already named by the scheme", LEFT_ALONE: "left alone",
               COLLISION: "keeps its name: name collision"}

PATTERN_LABELS = {"scheme": "already in a scheme", "fmd2": "FMD2 names", "release": "release names",
                  "generic": "other chapter / volume names", "bare": "bare numbers", "none": "unreadable"}

LEAD = ("Only names change - never a file's contents - so MangaPixer can keep its read marks. Renames are made in "
        "batches; each batch can be undone. A series folder not named \"<romaji> [<English>]\" is renamed too, as one "
        "step (the series keeps its data). Volumes are named \"Vol. 001 [group]\" (the folder carries the title). "
        "Folder names and volumes renamed only for that new format wait for your Rename here - never automatically; "
        "everything else a library set to Rename automatically renames by itself.")
PILOT_SERIES = ("Start here: rename this one series, then open it in MangaPixer and check that its read marks are still "
                "there. If they are, rename its whole library.")
PILOT_LIBRARY = ("Tip: rename one series first (right-click it in the List > Rename to the scheme…) and check its read "
                 "marks in MangaPixer before renaming a whole library.")
NOT_AVAILABLE = "The naming scheme is not in this build yet, so nothing can be renamed."


@dataclass(frozen=True)
class Scope:
    """What the window looks at: some series (the pilot), one library (root), or every library."""

    kind: str
    ids: Tuple[int, ...] = ()
    label: str = ""

    @classmethod
    def series(cls, series_ids: Sequence[int], label: str) -> "Scope":
        return cls(SCOPE_SERIES, tuple(int(i) for i in series_ids), label)

    @classmethod
    def root(cls, root_id: int, label: str) -> "Scope":
        return cls(SCOPE_ROOT, (int(root_id),), label)

    @classmethod
    def all(cls) -> "Scope":
        return cls(SCOPE_ALL, (), "every library")

    @property
    def heading(self) -> str:
        if self.kind == SCOPE_SERIES:
            return f"Rename to the scheme: {self.label}"
        if self.kind == SCOPE_ROOT:
            return f"Rename library: {self.label}"
        return "Rename every library"


Runner = Callable[[Callable[[], object], Callable[[object], None], Callable[[str], None]], object]


ROLE_PATH = Qt.ItemDataRole.UserRole + 1     # the file's path on a preview row


def _number_text(start, end) -> str:
    def one(d):
        return format(d.normalize(), "f") if d != d.to_integral_value() else str(int(d))
    return one(start) if end == start else f"{one(start)}-{one(end)}"


class UnitsDialog(QDialog):
    """ "Set chapter / volume...": what one file holds (owner, 2026-10-11: "I still can't set volume or chapter in the
    rename window"). The answer is ``(kind, numbers, volume)`` as typed; :func:`mangalist.renamer.file_units` checks it."""

    def __init__(self, name: str, current: Optional[FileUnits] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Set chapter / volume")
        lay = QVBoxLayout(self)
        intro = QLabel(f"What does this file hold?\n{name}")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.rb_chapter = QRadioButton("Chapter")
        self.rb_volume = QRadioButton("Volume")
        group = QButtonGroup(self)
        group.addButton(self.rb_chapter)
        group.addButton(self.rb_volume)
        kinds = QHBoxLayout()
        kinds.addWidget(self.rb_chapter)
        kinds.addWidget(self.rb_volume)
        kinds.addStretch(1)
        lay.addLayout(kinds)
        form = QFormLayout()
        self.numbers = QLineEdit()
        self.numbers.setPlaceholderText("12, 12.5 or 106-110")
        self.volume = QLineEdit()
        self.volume.setPlaceholderText("blank: the name's, else the volume list's")
        form.addRow("Number", self.numbers)
        form.addRow("Its volume", self.volume)
        lay.addLayout(form)
        self.error = QLabel("")
        self.error.setProperty("role", "muted")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.rb_chapter.toggled.connect(lambda on: self.volume.setEnabled(on))
        is_volume = current is not None and current.kind == UNIT_VOLUME
        (self.rb_volume if is_volume else self.rb_chapter).setChecked(True)
        self.volume.setEnabled(not is_volume)
        if current is not None:
            self.numbers.setText(_number_text(current.start, current.end))
            if current.volume is not None:
                self.volume.setText(_number_text(current.volume, current.volume))

    def answer(self) -> Tuple[str, str, str]:
        kind = UNIT_VOLUME if self.rb_volume.isChecked() else UNIT_CHAPTER
        return kind, self.numbers.text(), self.volume.text() if kind == UNIT_CHAPTER else ""

    def _accept(self) -> None:
        try:
            file_units(*self.answer())
        except ValueError as exc:
            self.error.setText(str(exc).capitalize() + ".")
            return
        self.accept()


AUTOMATIC_TITLE = "Keep this library in the scheme?"


def automatic_text(name: str) -> str:
    return (f"{name} is being renamed to the naming scheme. Rename its new files automatically from now on?\n\n"
            "Downloaded volumes are then filed under the scheme's names, and the scheduled rescan renames anything else "
            "named differently (each batch can be undone). Folder names and the new volume name format wait for your "
            "Rename: a folder not named \"<romaji> [<English>]\", or a volume still named \"<Series title> - Vol. "
            "001\" (now \"Vol. 001 [group]\"), shows as Rename pending until you rename it here - never automatically; "
            "everything else is automatic. You can change this later in Settings > Library.")


def _thread_runner(fn, on_done, on_error):
    from .background import start_call

    return start_call(fn, on_done, on_error)


FOLDER_TEXT = {RENAME: "the series folder will be renamed - the series keeps its data (one step, undoable)",
               UNCHANGED: "the series folder is named by the scheme",
               LEFT_ALONE: "the series folder keeps its name",
               COLLISION: "the series folder keeps its name: name collision"}


def _in_scheme(s: SeriesPreview) -> bool:
    """Every file of the series - and its folder - is already named by the scheme: nothing to rename, check or
    decide (a folder left alone for want of a title is not a reason to show the series)."""
    c = s.counts()
    return not (c[RENAME] or c[COLLISION] or c[LEFT_ALONE] or s.error or s.folder_status in (RENAME, COLLISION))


def _folder_untitled(s: SeriesPreview) -> bool:
    """The folder was left alone only for want of a MangaUpdates title (not worth a row of its own)."""
    return s.folder_status == LEFT_ALONE and s.folder_reason == FOLDER_NO_TITLE


def _folder_row_shown(s: SeriesPreview, show_all: bool) -> bool:
    if s.folder_status is None or _folder_untitled(s):
        return False
    return s.folder_status != UNCHANGED or show_all


def _changes_text(files: int, folders: int) -> str:
    parts = ([_plural(files, "file")] if files or not folders else []) + (
        [_plural(folders, "folder")] if folders else [])
    return " and ".join(parts)


def _plural(n: int, word: str, many: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


class RenamerWindow(QDialog):
    library_changed = Signal(list)          # root ids whose files were renamed (or put back): rescan them
    series_changed = Signal(list, list)     # (root ids, series ids): no rename journaled (a kind answer, a deleted
                                            # stray file) - rescan those series' folders
    duplicates_requested = Signal(str)      # a series folder: the duplicates review on it

    def __init__(self, renamer, scope: Scope, parent: Optional[QWidget] = None, *,
                 confirm: Optional[Callable[[str, str], bool]] = None, run: Optional[Runner] = None):
        super().__init__(parent)
        self.setObjectName("RenamerWindow")
        self.setWindowTitle("Rename to the scheme")
        self.resize(1080, 720)
        self._renamer = renamer
        self._scope = scope
        self._confirm = confirm or self._ask
        if confirm is not None:             # the host answers every question (tests): this one too
            self._ask_automatic = lambda name: confirm(AUTOMATIC_TITLE, automatic_text(name))
        self._run = run or _thread_runner
        self._call = None
        self._dry: Optional[DryRun] = None
        self._last: Optional[BatchRecord] = None
        self.rescan_follows = False         # the host rescans the libraries after library_changed (main window: True)
        self._awaiting: Set[int] = set()    # libraries renamed, their rescan not reported yet (library_rescanned)
        self._busy = False
        self._changing = False
        self._closed = False
        self._english_call = None           # the background lookup of official English titles (answer 76)
        self._english_scope: Optional[Scope] = None   # the scope it was started for (once per scope)
        self._build()
        self.refresh()

    # --- building ---------------------------------------------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)
        self.heading = QLabel()
        self.heading.setProperty("role", "h2")
        self.lead = QLabel(LEAD)
        self.lead.setWordWrap(True)
        self.pilot_note = QLabel()
        self.pilot_note.setWordWrap(True)
        self.pilot_note.setProperty("tone", "info")
        for w in (self.heading, self.lead, self.pilot_note):
            outer.addWidget(w)

        busy = QHBoxLayout()
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setFixedWidth(200)
        self.busy_label = QLabel()
        busy.addWidget(self.busy_bar)
        busy.addWidget(self.busy_label, 1)
        outer.addLayout(busy)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.patterns_label = QLabel()
        self.patterns_label.setWordWrap(True)
        self.patterns_label.setProperty("role", "muted")
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setProperty("tone", "warn")
        # owner, 2026-10-16 (answer 76): the folder names' English part is the official English title, looked up in the
        # background when the window opens (cached afterwards); this line says so while it runs
        self.english_label = QLabel()
        self.english_label.setWordWrap(True)
        self.english_label.setProperty("role", "muted")
        self.english_label.setVisible(False)
        for w in (self.summary_label, self.patterns_label, self.warning_label, self.english_label):
            outer.addWidget(w)

        self.tabs = QTabWidget()
        series_page = QWidget()
        sp = QVBoxLayout(series_page)
        sp.setContentsMargins(0, 6, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.series_list = QListWidget()
        # owner, 2026-10-10: "not a clear distinction between the title and the '224 to rename'" - the title on its own
        # line in medium weight, the counts smaller and grey under it, collisions as a red chip at the right
        self.series_list.setItemDelegate(_SeriesRowDelegate(self.series_list, row_height=48, left_pad=6))
        self.series_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # long titles elide
        self.series_list.setSpacing(2)
        self.series_list.currentRowChanged.connect(self._show_series)
        self.preview = QTreeWidget()
        theme.on_retheme(self.preview, self._recolor_preview)
        self.preview.setColumnCount(3)
        self.preview.setHeaderLabels(["Now", "After", "What happens"])
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.setColumnWidth(0, 360)
        self.preview.setColumnWidth(1, 360)
        self.preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.preview.customContextMenuRequested.connect(self._on_preview_menu)
        self.preview.itemDoubleClicked.connect(self._on_preview_double_click)
        split.addWidget(self.series_list)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([260, 780])
        # owner, 2026-10-11: "You should be able to set unknown as chapters or volumes from within the renamer" - the
        # selected series' "volumes or chapters?" answer, when some of its files are bare numbers left alone for it
        self.kind_bar = QWidget()
        kb = QHBoxLayout(self.kind_bar)
        kb.setContentsMargins(0, 0, 0, 6)
        kb.setSpacing(8)
        self.kind_label = QLabel("")
        self.kind_label.setWordWrap(True)
        self.btn_kind_chapters = QPushButton("Chapters")
        self.btn_kind_chapters.setToolTip("This series' bare numbers are chapters (saved for the series, as in the List)")
        self.btn_kind_chapters.clicked.connect(lambda: self.answer_kind("chapters"))
        self.btn_kind_volumes = QPushButton("Volumes")
        self.btn_kind_volumes.setToolTip("This series' bare numbers are volumes (saved for the series, as in the List)")
        self.btn_kind_volumes.clicked.connect(lambda: self.answer_kind("volumes"))
        kb.addWidget(self.kind_label, 1)
        kb.addWidget(self.btn_kind_chapters)
        kb.addWidget(self.btn_kind_volumes)
        self.kind_bar.setVisible(False)
        sp.addWidget(self.kind_bar)
        sp.addWidget(split, 1)
        # owner, 2026-10-10: after a library's rename every series said a small "in the scheme" - they are hidden now
        self.show_done = QCheckBox("Show series already in the scheme")
        self.show_done.toggled.connect(lambda _on: self._fill_series())
        self.show_unchanged = QCheckBox("Show files already named by the scheme")
        self.show_unchanged.toggled.connect(lambda _on: self._show_series(self.series_list.currentRow()))
        toggles = QHBoxLayout()
        toggles.setSpacing(16)
        toggles.addWidget(self.show_done)
        toggles.addWidget(self.show_unchanged)
        toggles.addStretch(1)
        sp.addLayout(toggles)
        self.tabs.addTab(series_page, "Series")
        self.notes = QTreeWidget()
        self.notes.setColumnCount(2)
        self.notes.setHeaderLabels(["File", "Why"])
        self.notes.setColumnWidth(0, 460)
        self.tabs.addTab(self.notes, "To check")
        outer.addWidget(self.tabs, 1)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.result_label)

        bar = QHBoxLayout()
        self.btn_duplicates = QPushButton("Review duplicates…")
        self.btn_duplicates.setToolTip("Two files of this series would get one name: the duplicates review shows them")
        self.btn_duplicates.clicked.connect(self._review_duplicates)
        self.btn_library = QPushButton("Rename its whole library…")
        self.btn_library.setToolTip("Done the pilot and the read marks are still there in MangaPixer? Look at the whole "
                                    "library next")
        self.btn_library.clicked.connect(self.widen_to_library)
        self.btn_undo = QPushButton("Undo last batch")
        self.btn_undo.setToolTip("Put the files of the last batch back under their old names")
        self.btn_undo.clicked.connect(self.undo_last)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        self.btn_apply = QPushButton("Rename")
        self.btn_apply.setProperty("primary", True)
        self.btn_apply.setProperty("variant", "primary")
        self.btn_apply.setDefault(False)
        self.btn_apply.setAutoDefault(False)
        self.btn_apply.clicked.connect(self.apply)
        for b in (self.btn_duplicates, self.btn_library, self.btn_undo):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_close)
        bar.addWidget(self.btn_apply)
        outer.addLayout(bar)
        self._apply_scope_texts()

    def _apply_scope_texts(self) -> None:
        self.heading.setText(self._scope.heading)
        self.setWindowTitle(self._scope.heading)
        pilot = self._scope.kind == SCOPE_SERIES
        self.pilot_note.setText(PILOT_SERIES if pilot else PILOT_LIBRARY)
        self.btn_library.setVisible(pilot and len(self._scope.ids) == 1)

    # --- state --------------------------------------------------------------------------------------------------

    @property
    def scope(self) -> Scope:
        return self._scope

    @property
    def dry_run(self) -> Optional[DryRun]:
        return self._dry

    @property
    def busy(self) -> bool:
        return self._busy

    def set_scope(self, scope: Scope) -> None:
        self._scope = scope
        self._apply_scope_texts()
        self.refresh()

    def _set_busy(self, text: Optional[str]) -> None:
        self._busy = text is not None
        self.busy_bar.setVisible(self._busy)
        self.busy_label.setText(text or "")
        self.busy_label.setVisible(self._busy)
        self._update_buttons()

    def _update_buttons(self) -> None:
        n = self._dry.count(RENAME) if self._dry is not None else 0
        folders = len(self._dry.folders_to_rename) if self._dry is not None else 0
        self.btn_apply.setText(f"Rename {_changes_text(n, folders)}…" if n or folders else "Rename")
        self.btn_apply.setEnabled(not self._busy and (n > 0 or folders > 0))
        # while the rescan runs, the last batch is not known yet: no undo of an older one
        self.btn_undo.setEnabled(not self._busy and not self._awaiting and self._last is not None)
        if self._last is not None:
            self.btn_undo.setToolTip(f"Put the {_plural(self._last.done, 'file')} of the last batch back under their "
                                     f"old names ({self._last.note})")
        current = self._current_series()
        self.btn_duplicates.setVisible(current is not None and bool(current.collisions))
        self.btn_duplicates.setEnabled(not self._busy)
        self.btn_library.setEnabled(not self._busy and self._dry is not None and bool(self._dry.roots))

    def _start(self, text: str, fn, on_done, *, changes: bool = False) -> None:
        if self._busy:
            return
        self._changing = changes
        self._set_busy(text)
        self._call = self._run(fn, lambda result: self._finish(on_done, result), self._failed)

    def _finish(self, on_done, result) -> None:
        self._call = None
        self._changing = False
        self._set_busy(None)
        on_done(result)

    def _failed(self, message: str) -> None:
        self._call = None
        self._changing = False
        self._set_busy(None)
        self.result_label.setProperty("tone", "bad")
        self.result_label.setText(f"Something went wrong: {message}. Nothing more was changed.")
        _log.warning("Renamer window: %s", message)

    # --- looking ------------------------------------------------------------------------------------------------

    def _look(self):
        r = self._renamer
        if not r.available():
            return None, None
        if self._scope.kind == SCOPE_SERIES:
            dry = r.dry_run(series_ids=list(self._scope.ids))
        elif self._scope.kind == SCOPE_ROOT:
            dry = r.dry_run(root_ids=list(self._scope.ids))
        else:
            dry = r.dry_run()
        return dry, r.last_undoable()

    def refresh(self) -> None:
        """Look again (the dry run of the scope; nothing is changed)."""
        self._start("Looking at the files…", self._look, self._looked)

    def _looked(self, result) -> None:
        dry, last = result
        self._last = last
        if dry is None:
            self._dry = None
            self.summary_label.setText(NOT_AVAILABLE)
            self.patterns_label.setText("")
            self.warning_label.setText("")
            self.series_list.clear()
            self.preview.clear()
            self.notes.clear()
            self._update_buttons()
            return
        self._dry = dry
        self._fill(dry)
        self._look_up_english()

    # --- official English titles (answer 76) ----------------------------------------------------------------------

    def _look_up_english(self) -> None:
        """The official English titles of the scope's series that are not cached yet (or are old) are looked up in the
        background, once per scope (:meth:`mangalist.renamer.Renamer.fill_english`: AniList through MangaDex's links,
        batched and rate-limited); the preview is looked at again when one changed. Never blocks the window, never
        renames anything: a new English title only changes what the preview proposes."""
        refs_of = getattr(self._renamer, "english_refs", None)
        fill = getattr(self._renamer, "fill_english", None)
        if refs_of is None or fill is None or self._english_call is not None or self._english_scope == self._scope:
            return
        scope = self._english_scope = self._scope
        kwargs = ({"series_ids": list(scope.ids)} if scope.kind == SCOPE_SERIES
                  else {"root_ids": list(scope.ids)} if scope.kind == SCOPE_ROOT else {})
        # two steps, both off the UI thread: which series are due (the database), then the lookup (the network)
        self._english_call = self._run(lambda: refs_of(**kwargs), lambda refs: self._english_due(refs, fill),
                                       self._english_failed)

    def _english_due(self, refs, fill) -> None:
        self._english_call = None
        if self._closed or not refs:
            return
        self.english_label.setText(f"Looking up the official English titles of {_plural(len(refs), 'series', 'series')} "
                                   "(AniList) in the background; the preview updates when they arrive.")
        self.english_label.setVisible(True)
        self._english_call = self._run(lambda: fill(refs, should_stop=lambda: self._closed),
                                       self._english_found, self._english_failed)

    def _english_found(self, report) -> None:
        self._english_call = None
        if self._closed:
            return
        changed = bool(getattr(report, "changed", None))
        summary = report.summary() if hasattr(report, "summary") else ""
        self.english_label.setText(summary + (" The preview shows them." if changed else ""))
        self.english_label.setVisible(bool(summary))
        if changed:
            self._refresh_when_idle()

    def _english_failed(self, message: str) -> None:
        self._english_call = None
        _log.warning("Renamer window: looking up official English titles failed: %s", message)
        if not self._closed:
            self.english_label.setText("The official English titles could not be looked up now; the names use the "
                                       "titles known so far.")

    def _refresh_when_idle(self) -> None:
        if self._closed:
            return
        if self._busy or self._awaiting:
            QTimer.singleShot(500, self._refresh_when_idle)
            return
        self.refresh()

    def _fill(self, dry: DryRun) -> None:
        series = dry.series
        moving = sum(1 for s in series if s.renames)
        folders = len(dry.folders_to_rename)
        self.summary_label.setText(
            f"{_plural(dry.count(RENAME), 'file')} to rename in {_plural(moving, 'series', 'series')}; "
            f"{dry.count(UNCHANGED)} already named by the scheme; {dry.count(LEFT_ALONE)} left alone; "
            f"{_plural(dry.count(COLLISION), 'name collision')} (not renamed)."
            + (f" {_plural(folders, 'series folder')} not named \"<romaji> [<English>]\" to rename." if folders else "")
            if series else "Nothing to look at: no series here has been scanned yet.")
        patterns = dry.patterns
        self.patterns_label.setText(
            "Names found: " + ", ".join(f"{PATTERN_LABELS.get(k, k)} {n}" for k, n in patterns.most_common())
            if patterns else "")
        self.warning_label.setText("\n".join(dry.warnings))
        self.warning_label.setVisible(bool(dry.warnings))
        # series: the ones with something to do first
        self._all_series: List[SeriesPreview] = sorted(series, key=lambda s: (not s.renames, not s.collisions,
                                                                              s.title.casefold()))
        self._fill_series()
        self._fill_notes(dry)
        self._update_buttons()

    def _fill_series(self) -> None:
        """The series list: series already wholly in the scheme only when "Show series already in the scheme" is on."""
        everything = getattr(self, "_all_series", [])
        done = [s for s in everything if _in_scheme(s)]
        self.show_done.setText(f"Show series already in the scheme ({len(done)})" if done else
                               "Show series already in the scheme")
        self.show_done.setEnabled(bool(done))
        current = self._current_series()
        self.series_list.blockSignals(True)
        self.series_list.clear()
        self._series: List[SeriesPreview] = [s for s in everything if self.show_done.isChecked() or not _in_scheme(s)]
        for s in self._series:
            c = s.counts()
            bits = [f"{c[RENAME]} to rename" if c[RENAME] else s.error if s.error and not s.folder_rename
                    else "" if s.folder_rename
                    else "nothing to rename" if (c[COLLISION] or c[LEFT_ALONE]) else "in the scheme"]
            if s.folder_rename:
                bits.append("folder renamed")
            elif s.folder_status == COLLISION:
                bits.append("folder: name collision")
            bits = [b for b in bits if b]
            if c[COLLISION]:
                bits.append(_plural(c[COLLISION], "collision"))
            if c[LEFT_ALONE]:
                bits.append(f"{c[LEFT_ALONE]} left alone")
            item = QListWidgetItem(s.title)
            item.setData(ROLE_SUB, " · ".join(b for b in bits if not b.endswith(("collision", "collisions"))))
            if c[COLLISION]:
                item.setData(ROLE_CHIPS, [(_plural(c[COLLISION], "collision"), "bad")])
            item.setToolTip(f"{s.title}\n{' · '.join(bits)}\n{s.folder}")
            self.series_list.addItem(item)
        self.series_list.blockSignals(False)
        if self._series:
            again = next((i for i, s in enumerate(self._series) if current is not None and s.folder == current.folder), 0)
            self.series_list.setCurrentRow(again)
        else:
            self.preview.clear()

    def _current_series(self) -> Optional[SeriesPreview]:
        row = self.series_list.currentRow()
        series = getattr(self, "_series", [])
        return series[row] if 0 <= row < len(series) else None

    def _show_kind_bar(self, s: Optional[SeriesPreview]) -> None:
        bare = s.needs_kind if s is not None else []
        self.kind_bar.setVisible(bool(bare))
        if bare:
            example = bare[0].name
            self.kind_label.setText(f"{_plural(len(bare), 'file')} of this series {'is a' if len(bare) == 1 else 'are'} "
                                    f"bare number{'s' if len(bare) != 1 else ''} (e.g. {example}) - are they:")
        enabled = not self._busy and bool(bare)
        self.btn_kind_chapters.setEnabled(enabled)
        self.btn_kind_volumes.setEnabled(enabled)

    def answer_kind(self, kind: str) -> bool:
        """Save the selected series' "volumes or chapters?" answer, then look again (after the library's rescan when the
        host rescans, like after a rename)."""
        s = self._current_series()
        if self._busy or s is None or s.series_id is None or not s.needs_kind:
            return False
        if not self._renamer.answer_kind(s.series_id, kind):
            self.result_label.setProperty("tone", "bad")
            self.result_label.setText(f"Could not save the answer for {s.title}.")
            return False
        self.result_label.setProperty("tone", "ok")
        self.result_label.setText(f"{s.title}: its bare numbers are {kind} (saved for the series).")
        self._after_change([s.root_id] if s.root_id is not None else [], series_ids=[s.series_id])
        return True

    def _recolor_preview(self) -> None:
        """The preview's rows in the palette in force (a switch of Appearance)."""
        colors = status_colors()
        for i in range(self.preview.topLevelItemCount()):
            item = self.preview.topLevelItem(i)
            color = colors.get(item.data(0, Qt.ItemDataRole.UserRole))
            if color is not None:
                for col in range(3):
                    item.setForeground(col, QBrush(QColor(color)))

    def _show_series(self, _row: int) -> None:
        self.preview.clear()
        s = self._current_series()
        self._show_kind_bar(s)
        if s is None:
            self._update_buttons()
            return
        show_all = self.show_unchanged.isChecked()
        if _folder_row_shown(s, show_all):
            self._add_folder_row(s)
        order = {RENAME: 0, COLLISION: 1, LEFT_ALONE: 2, UNCHANGED: 3}
        for f in sorted(s.files, key=lambda f: (order.get(f.status, 9), f.name.casefold())):
            if f.status == UNCHANGED and not show_all:
                continue
            rel_dir = os.path.relpath(f.folder, s.folder) if f.folder != s.folder else ""
            prefix = f"{rel_dir}/" if rel_dir and rel_dir != "." else ""
            after = (prefix + f.target) if f.status in (RENAME, COLLISION) and f.target else (
                "(as it is)" if f.status != UNCHANGED else prefix + f.name)
            what = STATUS_TEXT[f.status] + (f": {f.reason}" if f.reason and f.status != COLLISION else
                                            f" - {f.reason}" if f.reason else "")
            if f.status == RENAME and f.format_held:
                what += " - new volume name format (waits for your Rename, never automatic)"
            elif f.status == RENAME and f.title_held:
                what += " - new series title (waits for your Rename, never automatic)"
            if f.status == LEFT_ALONE and not f.needs_kind:
                what += " - right-click to set its chapter or volume"
            flags = [t for t, on in (("title shortened", f.title_cut), ("group shortened", f.group_cut),
                                     ("title holds a volume / chapter word", f.unit_words)) if on]
            if flags and f.status == RENAME:
                what += " (" + ", ".join(flags) + ")"
            item = QTreeWidgetItem([prefix + f.name, after, what])
            color = QBrush(QColor(status_colors()[f.status]))
            for col in range(3):
                item.setForeground(col, color)
            item.setToolTip(0, f.path)
            item.setToolTip(2, what)
            item.setData(0, Qt.ItemDataRole.UserRole, f.status)
            item.setData(0, ROLE_PATH, f.path)
            self.preview.addTopLevelItem(item)
        self._update_buttons()

    def _add_folder_row(self, s: SeriesPreview) -> None:
        """The series folder's own row (first): old name -> the scheme's ``<romaji> [<English>]``."""
        status = s.folder_status
        now = os.path.basename(s.folder) + "/"
        after = (s.folder_target + "/") if status in (RENAME, COLLISION) and s.folder_target else (
            now if status == UNCHANGED else "(as it is)")
        what = FOLDER_TEXT.get(status, "") + (f": {s.folder_reason}" if s.folder_reason else "")
        item = QTreeWidgetItem([now, after, what])
        font = item.font(0)
        font.setBold(True)
        color = QBrush(QColor(status_colors()[status]))
        for col in range(3):
            item.setForeground(col, color)
            item.setFont(col, font)
        item.setToolTip(0, s.folder)
        item.setToolTip(2, what)
        item.setData(0, Qt.ItemDataRole.UserRole, status)
        self.preview.addTopLevelItem(item)

    def _fill_notes(self, dry: DryRun) -> None:
        self.notes.clear()
        groups = (
            ("New volume name format \"Vol. 001 [group]\" (renamed only by your Rename)",
             lambda f: f.status == RENAME and f.held),
            ("Left alone", lambda f: f.status == LEFT_ALONE),
            ("Name collisions (both files keep their names)", lambda f: f.status == COLLISION),
            ("Chapter titles the length rule would shorten", lambda f: f.status == RENAME and f.title_cut),
            ("Group names the length rule would shorten", lambda f: f.status == RENAME and f.group_cut),
            ("Chapter titles with a volume or chapter word (a reader may read a number from them)",
             lambda f: f.status in (RENAME, UNCHANGED) and f.unit_words),
        )
        total = 0
        for title, pick in groups:
            found = dry.files_where(pick)
            if not found:
                continue
            total += len(found)
            top = QTreeWidgetItem([f"{title} ({len(found)})", ""])
            for s, f in found:
                why = f.reason or (f.target or "")
                child = QTreeWidgetItem([f"{s.title} › {f.name}", why])
                child.setToolTip(0, f.path)
                child.setToolTip(1, why)
                top.addChild(child)
            self.notes.addTopLevelItem(top)
            top.setExpanded(len(found) <= 20)
        for title, statuses in (("Series folders with a name collision (not renamed)", (COLLISION,)),
                                ("Series folders left alone", (LEFT_ALONE,))):
            folders = [s for s in dry.folders_where(*statuses) if not _folder_untitled(s)]
            if not folders:
                continue
            total += len(folders)
            top = QTreeWidgetItem([f"{title} ({len(folders)})", ""])
            for s in folders:
                child = QTreeWidgetItem([s.title + "/", s.folder_reason])
                child.setToolTip(0, s.folder)
                child.setToolTip(1, s.folder_reason)
                top.addChild(child)
            self.notes.addTopLevelItem(top)
            top.setExpanded(len(folders) <= 20)
        self.tabs.setTabText(1, f"To check ({total})" if total else "To check")

    # --- one file's numbers ---------------------------------------------------------------------------------------

    def _exec_menu(self, menu: QMenu, pos):
        return menu.exec(pos)

    def _ask_units(self, name: str, current: Optional[FileUnits]) -> Optional[Tuple[str, str, str]]:
        dialog = UnitsDialog(name, current, self)
        return dialog.answer() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _on_preview_menu(self, pos) -> None:
        item = self.preview.itemAt(pos)
        path = item.data(0, ROLE_PATH) if item is not None else None
        if not path:
            return
        menu = QMenu(self.preview)
        act_open = menu.addAction(OPEN_FOLDER_LABEL)
        act_open.setToolTip("Show the folder this file is in")
        act_set = act_forget = act_delete = act_dupes = None
        if not self._busy:
            if callable(getattr(self._renamer, "set_file_units", None)):
                menu.addSeparator()
                act_set = menu.addAction("Set chapter / volume…")
                act_set.setToolTip("Say which chapter or volume this file holds; it is then named by the scheme")
                act_forget = (menu.addAction("Forget the number I set") if self._renamer.file_units(path) is not None
                              else None)
            if getattr(self._renamer, "db", None) is not None:
                menu.addSeparator()
                act_delete = menu.addAction("Delete this file…")
                act_delete.setToolTip("A stray file - a hiatus notice, an announcement page: delete it for good")
            if self._current_series() is not None:
                act_dupes = menu.addAction("Review duplicates of this series…")
        chosen = self._exec_menu(menu, self.preview.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_open:
            self.open_folder_of(path)
        elif chosen is act_set:
            self.set_units_for(path)
        elif act_forget is not None and chosen is act_forget:
            self._renamer.set_file_units(path, None)
            self.refresh()
        elif chosen is act_delete:
            self.delete_file(path)
        elif chosen is act_dupes:
            self.duplicates_requested.emit(self._current_series().folder)

    def _on_preview_double_click(self, item, _column: int) -> None:
        path = item.data(0, ROLE_PATH) if item is not None else None
        if path:
            self.open_folder_of(path)

    def open_folder_of(self, path: str) -> bool:
        """The file manager on the folder *path* is in (owner, 2026-10-17: "open the file explorer by clicking on
        files in the rename window")."""
        return _open_folder(os.path.dirname(path) or path)

    def _confirm_delete(self, name: str, size: int) -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, "Delete this file?",
                          f"Delete {name} ({size / 1048576:.1f} MB) for good?\n\nIt is not moved anywhere - it cannot "
                          "be put back. Only this one file is deleted.")
        yes = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def delete_file(self, path: str) -> bool:
        """Delete one stray file after the owner's yes (owner, 2026-10-17: "Hiatus notices can just be deleted"):
        the guarded delete (:func:`mangalist.duplicates.discard_file`), then its series' folder is read again."""
        from ..duplicates import discard_file, file_as_listed

        db = getattr(self._renamer, "db", None)
        listed = file_as_listed(path)
        if self._busy or db is None:
            return False
        name = os.path.basename(path)
        if listed is None:
            self._say("bad", f"{name} cannot be read - is it already gone?")
            return False
        if not self._confirm_delete(name, listed[0]):
            return False
        outcome = discard_file(db, path, *listed)
        if not outcome.deleted:
            self._say("bad", f"{name} was not deleted: {outcome.reason}.")
            return False
        self._say("ok", f"{name} deleted.")
        s = self._current_series()
        if s is not None and s.root_id is not None and s.series_id is not None:
            self._after_change([s.root_id], series_ids=[s.series_id])
        else:
            self.refresh()
        return True

    def _say(self, tone: str, text: str) -> None:
        self.result_label.setProperty("tone", tone)
        self.result_label.setText(text)

    def set_units_for(self, path: str) -> bool:
        """Ask what *path* holds, then look again: the file shows the scheme's name for it, to rename with the rest."""
        if self._busy:
            return False
        answer = self._ask_units(os.path.basename(path), self._renamer.file_units(path))
        if answer is None:
            return False
        try:
            units = file_units(*answer)
        except ValueError:
            return False
        self._renamer.set_file_units(path, units)
        kind, numbers, volume = answer
        self.result_label.setProperty("tone", "ok")
        self.result_label.setText(f"{os.path.basename(path)}: {kind} {numbers.strip()}"
                                  + (f" of volume {volume.strip()}" if volume.strip() else "")
                                  + " - check its new name, then Rename.")
        self.refresh()
        return True

    # --- acting -------------------------------------------------------------------------------------------------

    @staticmethod
    def _ask(title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text)
        yes = box.addButton("Rename" if title.startswith("Rename") else "Undo", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def confirmation_text(self) -> str:
        dry = self._dry
        if dry is None:
            return ""
        batches = self._renamer.batches(dry.series, folders=True)
        moving = sum(1 for s in dry.series if s.renames or s.folder_rename)
        folders = len(dry.folders_to_rename)
        lines = [f"Rename {_changes_text(dry.count(RENAME), folders)} in {_plural(moving, 'series', 'series')} "
                 f"({_plural(len(batches), 'batch', 'batches')})?", "",
                 "Only the names change - every file's contents stay exactly as they are."]
        if folders:
            examples = [f"  {os.path.basename(s.folder)}  ->  {s.folder_target}" for s in dry.folders_to_rename[:3]]
            lines += [f"{_plural(folders, 'series folder')} {'is' if folders == 1 else 'are'} renamed to "
                      "\"<romaji> [<English>]\" (one step each; the series keeps its data):", *examples]
            if folders > 3:
                lines.append(f"  … and {folders - 3} more")
        held = sum(len(s.held_renames) for s in dry.series)
        if held:
            lines.append(f"{_plural(held, 'volume')} {'is' if held == 1 else 'are'} renamed to the new volume name format "
                         "\"Vol. 001 [group]\" (no series title: the folder has it).")
        lines += [f"{dry.count(LEFT_ALONE)} file(s) are left alone and {_plural(dry.count(COLLISION), 'name collision')} "
                  "are not renamed.",
                  "Each batch can be undone with \"Undo last batch\"."]
        if dry.warnings:
            lines += ["", *dry.warnings]
        return "\n".join(lines)

    def apply(self) -> None:
        dry = self._dry
        folders = len(dry.folders_to_rename) if dry is not None else 0
        if self._busy or dry is None or not (dry.count(RENAME) or folders):
            return
        if not self._confirm(f"Rename {_changes_text(dry.count(RENAME), folders)}?", self.confirmation_text()):
            return
        # the owner's explicit Rename: the series folders too (the automatic pass never renames a folder)
        batches = self._renamer.batches(dry.series, folders=True)
        root_ids = sorted({b.root_id for b in batches if b.root_id is not None})
        guided = self._scope.kind in (SCOPE_ROOT, SCOPE_ALL)
        renamer = self._renamer

        def work():
            if guided:
                renamer.mark_guided(root_ids)
            return renamer.apply_all(batches)

        self._start(f"Renaming {_changes_text(dry.count(RENAME), folders)}…", work, self._applied, changes=True)

    def _applied(self, results: Sequence[BatchResult]) -> None:
        renamed = sum(r.renamed for r in results)
        folders = sum(getattr(r, "folders", 0) for r in results)
        lines = [f"{_plural(renamed, 'file')} renamed in {_plural(len(results), 'batch', 'batches')}."
                 + (f" {_plural(folders, 'series folder')} renamed to the scheme." if folders else "")]
        bad = [r for r in results if r.status not in ("applied", "nothing")]
        for r in bad:
            lines.append(r.message)
        skipped = [x for r in results for x in r.skipped]
        if skipped:
            lines.append(f"{_plural(len(skipped), 'file')} changed since the preview and were not renamed.")
        after = "; ".join(r.after for r in results if r.after)
        if after:
            lines.append(after)
        if self._scope.kind == SCOPE_SERIES and (renamed or folders) and not bad:
            lines.append("Now open the series in MangaPixer and check its read marks. All there? Rename its whole "
                         "library next.")
        self.result_label.setProperty("tone", "bad" if bad else "ok")
        self.result_label.setText(" ".join(lines))
        roots = sorted({rid for r in results if r.renamed or getattr(r, "folders", 0) for rid in r.root_ids})
        if self._scope.kind in (SCOPE_ROOT, SCOPE_ALL) and renamed and not bad:
            self._offer_automatic(roots)
        self._after_change(roots)

    @staticmethod
    def _ask_automatic(name: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, AUTOMATIC_TITLE, automatic_text(name))
        yes = box.addButton("Rename automatically", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        return box.clickedButton() is yes

    def _offer_automatic(self, root_ids: Sequence[int]) -> None:
        """After a library's first rename from here: ask, once per library, to keep it in the scheme automatically
        (owner, 2026-10-12)."""
        offers = getattr(self._renamer, "automatic_offers", None)
        if not callable(offers):
            return
        try:
            roots = offers(root_ids)
        except Exception:  # noqa: BLE001 - a question only
            _log.warning("Renamer: could not read the libraries' rename setting", exc_info=True)
            return
        for root in roots:
            on = self._ask_automatic(root.name)
            try:
                self._renamer.answer_automatic(root.id, on)
            except Exception:  # noqa: BLE001
                _log.warning("Renamer: could not save the answer for %s", root.name, exc_info=True)

    def undo_last(self) -> None:
        last = self._last
        if self._busy or last is None:
            return
        if not self._confirm("Undo the last batch?", f"Put {_plural(last.done, 'file')} back under their old names?\n\n"
                                                      f"({last.note}, renamed {last.created_at})"):
            return
        renamer = self._renamer
        self._start("Putting the files back…", lambda: renamer.undo(last.plan_id), self._undone, changes=True)

    def _undone(self, result: BatchResult) -> None:
        self.result_label.setProperty("tone", "ok" if result.status == "undone" else "bad")
        self.result_label.setText(result.message + (f" {result.after}" if result.after else ""))
        self._after_change(sorted(result.root_ids) if result.root_ids and (result.renamed or getattr(result, "folders", 0))
                           else [])

    def _after_change(self, root_ids: Sequence[int], series_ids: Optional[Sequence[int]] = None) -> None:
        """Files were renamed or put back. The preview reads the files as the last scan recorded them, so with a host
        that rescans (``rescan_follows``) it waits for that rescan - looking at once listed every renamed file under its
        old name as "gone since the last scan" (owner, 2026-10-10). *series_ids*: no rename was journaled (a "volumes or
        chapters?" answer, a deleted stray file) - the host rescans these series' folders, not the whole library (owner,
        2026-10-17)."""
        if root_ids and series_ids:
            self.series_changed.emit(list(root_ids), list(series_ids))
        elif root_ids:
            self.library_changed.emit(list(root_ids))
        if root_ids and self.rescan_follows:
            self._awaiting = set(root_ids)
            self._dry = None
            self.series_list.clear()
            self.preview.clear()
            self.kind_bar.setVisible(False)
            self.summary_label.setText("")
            self.patterns_label.setText("")
            # the window's busy bar while the library is read again (owner, 2026-10-11: "the page was blank with no
            # indication it was refreshing")
            self._set_busy("Rescanning the library to show the new names…")
            return
        self.refresh()

    def library_rescanned(self, root_ids: Sequence[int]) -> None:
        """The host finished a scan of *root_ids*: look again once every library this window changed is rescanned."""
        if not self._awaiting:
            return
        self._awaiting -= set(root_ids)
        if not self._awaiting and self._call is None:
            self._set_busy(None)
            self.refresh()

    def widen_to_library(self) -> None:
        """After the pilot: the same window on the series' whole library."""
        dry = self._dry
        if self._busy or dry is None or not dry.roots:
            return
        root = dry.roots[0]
        if root.root_id is not None:
            self.set_scope(Scope.root(root.root_id, root.name))

    def _review_duplicates(self) -> None:
        s = self._current_series()
        if s is not None:
            self.duplicates_requested.emit(s.folder)

    def closeEvent(self, event) -> None:
        if self._changing:                  # renaming or undoing: stay until it is done (its result must reach the list)
            self.result_label.setText("Renaming is in progress - the window closes once it is done.")
            event.ignore()
            return
        call = self._call
        if call is not None and hasattr(call, "abandon"):
            call.abandon()
        self._call = None
        self._closed = True                 # the English lookup stops at its next request (what it found is cached)
        english = self._english_call
        if english is not None and hasattr(english, "abandon"):
            english.abandon()
        self._english_call = None
        super().closeEvent(event)
