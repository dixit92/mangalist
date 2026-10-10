"""The Renamer window: bring a series, a library or every library to the naming scheme (renamer cycle, 2026-10-10).

Opened from the List: right-click a series > "Rename to the scheme…" (the pilot: one series first, then check its read
marks in MangaPixer), or "Rename library…" (the library picked, or every library). It shows:

- the dry run's summary: how many files would be renamed, are already named by the scheme, are left alone, collide;
  how many names of each pattern were found; the plain warning about MangaPixer's read state;
- per series, old -> new for every file, coloured: renamed (blue), unchanged (grey), left alone (amber), collision (red);
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
from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..renamer import COLLISION, LEFT_ALONE, RENAME, UNCHANGED, BatchRecord, BatchResult, DryRun, SeriesPreview
from .download_widgets import ROLE_CHIPS, ROLE_SUB, TwoLineDelegate

_log = logging.getLogger(__name__)

SCOPE_SERIES, SCOPE_ROOT, SCOPE_ALL = "series", "root", "all"


class _SeriesRowDelegate(TwoLineDelegate):
    """A series row as wide as the list (a long title elides instead of widening the row past the chips)."""

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setWidth(1)
        return hint

STATUS_COLORS = {RENAME: "#1f4fb8", UNCHANGED: "#6b6b67", LEFT_ALONE: "#9a5b00", COLLISION: "#8b1d1d"}
# A preview: the table always shows what renaming WOULD do (applying re-runs the preview) - owner, 2026-10-10.
STATUS_TEXT = {RENAME: "will be renamed", UNCHANGED: "already named by the scheme", LEFT_ALONE: "left alone",
               COLLISION: "keeps its name: name collision"}

PATTERN_LABELS = {"scheme": "already in a scheme", "fmd2": "FMD2 names", "release": "release names",
                  "generic": "other chapter / volume names", "bare": "bare numbers", "none": "unreadable"}

LEAD = ("Only file names change - never a file's contents - so MangaPixer can keep its read marks. Renames are made in "
        "batches; each batch can be undone. Folders are not renamed.")
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


def _thread_runner(fn, on_done, on_error):
    from .background import start_call

    return start_call(fn, on_done, on_error)


def _plural(n: int, word: str, many: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


class RenamerWindow(QDialog):
    library_changed = Signal(list)          # root ids whose files were renamed (or put back): rescan them
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
        self._run = run or _thread_runner
        self._call = None
        self._dry: Optional[DryRun] = None
        self._last: Optional[BatchRecord] = None
        self._busy = False
        self._changing = False
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
        for w in (self.summary_label, self.patterns_label, self.warning_label):
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
        self.preview.setColumnCount(3)
        self.preview.setHeaderLabels(["Now", "After", "What happens"])
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.setColumnWidth(0, 360)
        self.preview.setColumnWidth(1, 360)
        split.addWidget(self.series_list)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([260, 780])
        sp.addWidget(split, 1)
        self.show_unchanged = QCheckBox("Show files already named by the scheme")
        self.show_unchanged.toggled.connect(lambda _on: self._show_series(self.series_list.currentRow()))
        sp.addWidget(self.show_unchanged)
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
        self.btn_apply.setText(f"Rename {_plural(n, 'file')}…" if n else "Rename")
        self.btn_apply.setEnabled(not self._busy and n > 0)
        self.btn_undo.setEnabled(not self._busy and self._last is not None)
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

    def _fill(self, dry: DryRun) -> None:
        series = dry.series
        moving = sum(1 for s in series if s.renames)
        self.summary_label.setText(
            f"{_plural(dry.count(RENAME), 'file')} to rename in {_plural(moving, 'series', 'series')}; "
            f"{dry.count(UNCHANGED)} already named by the scheme; {dry.count(LEFT_ALONE)} left alone; "
            f"{_plural(dry.count(COLLISION), 'name collision')} (not renamed)."
            if series else "Nothing to look at: no series here has been scanned yet.")
        patterns = dry.patterns
        self.patterns_label.setText(
            "Names found: " + ", ".join(f"{PATTERN_LABELS.get(k, k)} {n}" for k, n in patterns.most_common())
            if patterns else "")
        self.warning_label.setText("\n".join(dry.warnings))
        self.warning_label.setVisible(bool(dry.warnings))
        # series: the ones with something to do first
        self.series_list.blockSignals(True)
        self.series_list.clear()
        self._series: List[SeriesPreview] = sorted(series, key=lambda s: (not s.renames, not s.collisions,
                                                                          s.title.casefold()))
        for s in self._series:
            c = s.counts()
            bits = [f"{c[RENAME]} to rename" if c[RENAME] else s.error if s.error
                    else "nothing to rename" if (c[COLLISION] or c[LEFT_ALONE]) else "in the scheme"]
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
            self.series_list.setCurrentRow(0)
        else:
            self.preview.clear()
        self._fill_notes(dry)
        self._update_buttons()

    def _current_series(self) -> Optional[SeriesPreview]:
        row = self.series_list.currentRow()
        series = getattr(self, "_series", [])
        return series[row] if 0 <= row < len(series) else None

    def _show_series(self, _row: int) -> None:
        self.preview.clear()
        s = self._current_series()
        if s is None:
            self._update_buttons()
            return
        show_all = self.show_unchanged.isChecked()
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
            flags = [t for t, on in (("title shortened", f.title_cut), ("group shortened", f.group_cut),
                                     ("title holds a volume / chapter word", f.unit_words)) if on]
            if flags and f.status == RENAME:
                what += " (" + ", ".join(flags) + ")"
            item = QTreeWidgetItem([prefix + f.name, after, what])
            color = QBrush(QColor(STATUS_COLORS[f.status]))
            for col in range(3):
                item.setForeground(col, color)
            item.setToolTip(0, f.path)
            item.setToolTip(2, what)
            item.setData(0, Qt.ItemDataRole.UserRole, f.status)
            self.preview.addTopLevelItem(item)
        self._update_buttons()

    def _fill_notes(self, dry: DryRun) -> None:
        self.notes.clear()
        groups = (
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
        self.tabs.setTabText(1, f"To check ({total})" if total else "To check")

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
        batches = self._renamer.batches(dry.series)
        moving = sum(1 for s in dry.series if s.renames)
        lines = [f"Rename {_plural(dry.count(RENAME), 'file')} in {_plural(moving, 'series', 'series')} "
                 f"({_plural(len(batches), 'batch', 'batches')})?", "",
                 "Only the names change - every file's contents stay exactly as they are.",
                 f"{dry.count(LEFT_ALONE)} file(s) are left alone and {_plural(dry.count(COLLISION), 'name collision')} "
                 "are not renamed.",
                 "Each batch can be undone with \"Undo last batch\"."]
        if dry.warnings:
            lines += ["", *dry.warnings]
        return "\n".join(lines)

    def apply(self) -> None:
        dry = self._dry
        if self._busy or dry is None or not dry.count(RENAME):
            return
        if not self._confirm(f"Rename {_plural(dry.count(RENAME), 'file')}?", self.confirmation_text()):
            return
        batches = self._renamer.batches(dry.series)
        root_ids = sorted({b.root_id for b in batches if b.root_id is not None})
        guided = self._scope.kind in (SCOPE_ROOT, SCOPE_ALL)
        renamer = self._renamer

        def work():
            if guided:
                renamer.mark_guided(root_ids)
            return renamer.apply_all(batches)

        self._start(f"Renaming {_plural(dry.count(RENAME), 'file')}…", work, self._applied, changes=True)

    def _applied(self, results: Sequence[BatchResult]) -> None:
        renamed = sum(r.renamed for r in results)
        lines = [f"{_plural(renamed, 'file')} renamed in {_plural(len(results), 'batch', 'batches')}."]
        bad = [r for r in results if r.status not in ("applied", "nothing")]
        for r in bad:
            lines.append(r.message)
        skipped = [x for r in results for x in r.skipped]
        if skipped:
            lines.append(f"{_plural(len(skipped), 'file')} changed since the preview and were not renamed.")
        after = "; ".join(r.after for r in results if r.after)
        if after:
            lines.append(after)
        if self._scope.kind == SCOPE_SERIES and renamed and not bad:
            lines.append("Now open the series in MangaPixer and check its read marks. All there? Rename its whole "
                         "library next.")
        self.result_label.setProperty("tone", "bad" if bad else "ok")
        self.result_label.setText(" ".join(lines))
        roots = sorted({rid for r in results if r.renamed for rid in r.root_ids})
        if roots:
            self.library_changed.emit(roots)
        self.refresh()

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
        if result.root_ids and result.renamed:
            self.library_changed.emit(list(result.root_ids))
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
        super().closeEvent(event)
