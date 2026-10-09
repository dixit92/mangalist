"""The duplicates view: what the List tab shows behind its "Duplicates" chip.

Two sections, styled after the approved mockup (UI cycle, 2026-10-08):

- **Series in more than one folder** - the folders of one series side by side (root, file count, size, last change) with
  "Show in list" and "Open folder". Listing only: nothing here deletes a folder.
- **Duplicate files within a series** - the same chapter / volume number in more than one file of one folder
  (:func:`mangalist.duplicates.find_duplicate_files`, MangaPixer's rule). Every file has Keep / Discard; the default keeps the
  newest (the largest of equally new ones) of each number and never discards them all. "Apply" lists every file to be
  deleted and asks; only then are they deleted (no holding folder), unless a scan or a filing holds the root's lock.

The scan of the duplicates and the deletion run off the UI thread. Deleting goes through
:func:`mangalist.duplicates.discard_duplicates` only.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..duplicates import (
    DiscardOutcome,
    busy_reason,
    default_keep,
    discard_duplicates,
    find_duplicate_files,
    format_time,
    human_size,
    iso_from_ns,
    largest,
    newest,
)
from .background import BackgroundCall, start_call
from .links import open_link
from .shell import DuplicateFile, DuplicateGroup, DuplicateSeries

_log = logging.getLogger(__name__)

# The mockup's palette (Main.dc.html): ground, panels, ink, accent and the badge colours.
STYLE = """
#DuplicatesView { background: #f3f3f1; color: #161616; }
#DuplicatesView QScrollArea, #DuplicatesView #Body { background: transparent; border: none; }
#DuplicatesView QLabel { background: transparent; color: #161616; }
#DuplicatesView QLabel[role="heading"] { font-size: 12px; font-weight: 600; color: #5a5a57; }
#DuplicatesView QLabel[role="muted"] { color: #6b6b67; font-size: 12px; }
#DuplicatesView QLabel[role="title"] { font-size: 15px; font-weight: 700; }
#DuplicatesView QLabel[role="mono"] { font-family: "IBM Plex Mono", "DejaVu Sans Mono", monospace; font-size: 12px; }
#DuplicatesView QLabel[role="mono"][tone="muted"] { color: #6b6b67; }
#DuplicatesView QLabel[role="pill"] { padding: 1px 8px; border-radius: 9px; font-size: 11px; font-weight: 600;
    background: #e8eefb; color: #1f4fb8; }
#DuplicatesView QLabel[role="error"] { color: #8b1d1d; }
#DuplicatesView QFrame[role="card"] { background: #ffffff; border: 1px solid #dcdcd8; border-radius: 8px; }
#DuplicatesView QFrame[role="folder"] { background: #fafaf8; border: 1px solid #e3e3df; border-radius: 6px; }
#DuplicatesView QFrame[role="row"] { background: transparent; border: none; border-top: 1px solid #efefec; }
#DuplicatesView QFrame[role="bar"] { background: #ffffff; border: none; border-top: 1px solid #dcdcd8; }
#DuplicatesView QPushButton { height: 32px; padding: 0 14px; border: 1px solid #c4c4bf; border-radius: 6px;
    background: #ffffff; color: #161616; }
#DuplicatesView QPushButton:hover { background: #f3f3f1; }
#DuplicatesView QPushButton:disabled { color: #9a9a96; background: #f3f3f1; }
#DuplicatesView QPushButton[role="primary"] { height: 36px; border: 1px solid #1f4fb8; background: #1f4fb8;
    color: #ffffff; font-weight: 600; }
#DuplicatesView QPushButton[role="primary"]:hover { background: #163a87; }
#DuplicatesView QPushButton[role="primary"]:disabled { border-color: #c4c4bf; background: #e3e3df; color: #9a9a96; }
#DuplicatesView QPushButton[role="danger"] { height: 36px; border: 1px solid #8b1d1d; background: #8b1d1d; color: #ffffff;
    font-weight: 600; }
#DuplicatesView QPushButton[role="danger"]:hover { background: #6e1616; }
#DuplicatesView QListWidget { background: #ffffff; border: 1px solid #dcdcd8; border-radius: 6px;
    font-family: "IBM Plex Mono", "DejaVu Sans Mono", monospace; font-size: 12px; }
#DuplicatesView QPushButton[role="link"] { height: 26px; padding: 0 10px; font-size: 12px; }
#DuplicatesView QPushButton[role="keep"], #DuplicatesView QPushButton[role="discard"] { height: 26px; min-width: 52px;
    padding: 0 10px; font-size: 12px; background: #ffffff; color: #3a3a38; }
#DuplicatesView QPushButton[role="keep"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
#DuplicatesView QPushButton[role="discard"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; }
#DuplicatesView QPushButton[role="keep"]:checked { background: #e7f3ec; color: #1d5e36; border-color: #1d5e36; font-weight: 600; }
#DuplicatesView QPushButton[role="discard"]:checked { background: #fbeaea; color: #8b1d1d; border-color: #8b1d1d; font-weight: 600; }
#DuplicatesView QProgressBar { border: none; background: #e3e3df; border-radius: 2px; max-height: 4px; min-height: 4px; }
#DuplicatesView QProgressBar::chunk { background: #1f4fb8; border-radius: 2px; }
#DuplicatesView QLabel[role="busy"] { color: #1f4fb8; font-size: 12px; font-weight: 600; }
"""


def _label(text: str = "", role: Optional[str] = None, tone: Optional[str] = None, tip: Optional[str] = None) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    if role == "pill":
        label.setAlignment(Qt.AlignCenter)
        label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        label.setFixedHeight(20)
    if tone:
        label.setProperty("tone", tone)
    if tip:
        label.setToolTip(tip)
    return label


def _button(text: str, role: Optional[str] = None, checkable: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setAutoDefault(False)
    button.setCursor(Qt.PointingHandCursor)
    if role:
        button.setProperty("role", role)
    button.setCheckable(checkable)
    return button


def _frame(role: str) -> QFrame:
    frame = QFrame()
    frame.setProperty("role", role)
    return frame


class _Elided(QLabel):
    """A one-line label that shortens the middle of a long path instead of widening the layout; the tooltip has it all."""

    def __init__(self, text: str, role: str = "mono", tone: Optional[str] = None):
        super().__init__("")
        self._full = text
        self.setProperty("role", role)
        if tone:
            self.setProperty("tone", tone)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:
        return QSize(40, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self._full) + 4, super().sizeHint().height())

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        text = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, self.width())
        painter.drawText(self.rect(), int(Qt.AlignVCenter | Qt.AlignLeft), text)


class ConfirmDiscardDialog(QDialog):
    """Lists every file about to be deleted; Delete is explicit, Cancel is the default."""

    def __init__(self, files: Sequence[DuplicateFile], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete these files?")
        self.setObjectName("DuplicatesView")          # the view's look (its stylesheet is keyed on this name)
        self.setStyleSheet(STYLE)
        self.setModal(True)
        self.resize(720, 420)
        total = sum(f.size for f in files)
        layout = QVBoxLayout(self)
        layout.addWidget(_label(
            f"Delete {len(files)} file{'s' if len(files) != 1 else ''} ({human_size(total)}) for good?", "title"))
        layout.addWidget(_label("There is no holding folder: deleted files are gone, not moved. "
                                "At least one copy of every number stays.", "muted"))
        self.list = QListWidget()
        for f in files:
            self.list.addItem(f"{f.path}    {human_size(f.size)}")
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox()
        self.delete_button = buttons.addButton(f"Delete {len(files)} file{'s' if len(files) != 1 else ''}",
                                               QDialogButtonBox.AcceptRole)
        self.cancel_button = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.delete_button.setAutoDefault(False)
        self.delete_button.setProperty("role", "danger")
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _ask_confirmation(parent: QWidget, files: Sequence[DuplicateFile]) -> bool:
    dialog = ConfirmDiscardDialog(files, parent)
    try:
        return dialog.exec() == QDialog.Accepted
    finally:
        dialog.deleteLater()


class _FileRow(QFrame):
    """One file of a duplicate number: Keep / Discard, its name, size, time, group and what makes it the default."""

    chosen = Signal(str, bool)           # path, discard?

    def __init__(self, file: DuplicateFile, shown_name: str, tags: Sequence[str], discard: bool):
        super().__init__()
        self.file = file
        self.setProperty("role", "row")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(12)
        self.keep_button = _button("Keep", "keep", checkable=True)
        self.discard_button = _button("Discard", "discard", checkable=True)
        self._choices = QButtonGroup(self)
        self._choices.setExclusive(True)
        self._choices.addButton(self.keep_button)
        self._choices.addButton(self.discard_button)
        self.keep_button.clicked.connect(lambda: self.chosen.emit(file.path, False))
        self.discard_button.clicked.connect(lambda: self.chosen.emit(file.path, True))
        pair = QHBoxLayout()
        pair.setSpacing(0)
        pair.addWidget(self.keep_button)
        pair.addWidget(self.discard_button)
        row.addLayout(pair)
        self.name = _Elided(shown_name)
        self.name.setToolTip(file.path)
        row.addWidget(self.name, 1)
        for tag in tags:
            row.addWidget(_label(tag, "pill"), 0, Qt.AlignVCenter)
        self.group = _label(file.group or "", "muted")
        self.group.setMinimumWidth(90)
        row.addWidget(self.group)
        self.size = _label(human_size(file.size), "mono")
        self.size.setMinimumWidth(70)
        self.size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.size)
        self.time = _label(format_time(file.modified), "mono")
        self.time.setMinimumWidth(120)
        row.addWidget(self.time)
        self.set_discard(discard)

    def set_discard(self, discard: bool) -> None:
        (self.discard_button if discard else self.keep_button).setChecked(True)

    @property
    def discard(self) -> bool:
        return self.discard_button.isChecked()


class DuplicatesView(QWidget):
    """Series held in more than one folder, and duplicate files to keep or discard.

    ``show_in_list(folder)``: the owner wants that series in the List. ``files_deleted(paths)``: these files were deleted
    (the shell rescans). ``groups_found(n)``: the number of duplicate numbers the last scan found (for a chip's count).

    Each series card has its own Apply (that series only) and, when the shell gives :meth:`set_series_link`, "Open in
    MangaPixer" (the series' page there, to judge the copies in its reader). :meth:`focus_series` shows one series only.
    """

    show_in_list = Signal(str)
    files_deleted = Signal(list)
    groups_found = Signal(int)

    def __init__(self, db, parent=None, *, confirm: Optional[Callable[[Sequence[DuplicateFile]], bool]] = None,
                 open_folder: Optional[Callable[[str], object]] = None):
        super().__init__(parent)
        self.setObjectName("DuplicatesView")
        self.setStyleSheet(STYLE)
        self._db = db
        self._confirm = confirm or (lambda files: _ask_confirmation(self, files))
        self._open_folder = open_folder or (lambda folder: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        self._series: List[DuplicateSeries] = []
        self._groups: List[DuplicateGroup] = []
        self._rows: Dict[str, _FileRow] = {}               # path -> its row
        self._by_group: List[Tuple[DuplicateGroup, List[_FileRow]]] = []
        self._choice: Dict[str, bool] = {}                 # path -> discard?, kept across refreshes
        self._calls: List[BackgroundCall] = []
        self._generation = 0
        self._scanning = False
        self._applying = False
        self._blocked: Optional[str] = None
        self._scan_error = ""
        self._deleting = 0                                  # files in the running Apply
        self._after_apply = False                           # busy until the rescan + re-read that follow an Apply
        self._series_link: Optional[Callable[[str], Optional[str]]] = None
        self._focus: Optional[str] = None                   # a series folder: show only its duplicate files
        self._card_apply: Dict[Tuple[Optional[int], str], QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._body = QWidget()
        self._body.setObjectName("Body")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(20, 18, 20, 18)
        self._body_layout.setSpacing(10)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

        self.series_heading = _label("", "heading")
        self.series_box = QVBoxLayout()
        self.series_box.setSpacing(10)
        self.files_heading = _label("", "heading")
        self.show_all_button = _button("Show all series", "link")
        self.show_all_button.clicked.connect(lambda: self.focus_series(None))
        self.show_all_button.hide()
        self.files_box = QVBoxLayout()
        self.files_box.setSpacing(10)
        self._body_layout.addWidget(self.series_heading)
        self._body_layout.addLayout(self.series_box)
        self._body_layout.addSpacing(14)
        files_head = QHBoxLayout()
        files_head.addWidget(self.files_heading, 1)
        files_head.addWidget(self.show_all_button)
        self._body_layout.addLayout(files_head)
        self._body_layout.addLayout(self.files_box)
        self._body_layout.addStretch(1)

        bar = _frame("bar")
        bar_layout = QVBoxLayout(bar)
        bar_layout.setContentsMargins(20, 10, 20, 10)
        bar_layout.setSpacing(6)
        # Deleting, the library's rescan and the re-read can take a while on a big library: a moving bar and the cards
        # greyed out say "working", not "stuck".
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.hide()
        bar_layout.addWidget(self.busy_bar)
        self.report = _label("", "muted")
        self.report.setWordWrap(True)
        self.report.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.report.hide()
        bar_layout.addWidget(self.report)
        line = QHBoxLayout()
        self.status = _label("", "muted")
        line.addWidget(self.status, 1)
        self.refresh_button = _button("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.apply_button = _button("Apply", "primary")
        self.apply_button.clicked.connect(self.apply)
        line.addWidget(self.refresh_button)
        line.addWidget(self.apply_button)
        bar_layout.addLayout(line)
        outer.addWidget(bar)

        self._render_series()
        self._render_files()
        self._update_summary()

    # --- the series held in more than one folder ----------------------------------------------------------

    def set_series_duplicates(self, groups: Sequence[DuplicateSeries]) -> None:
        self._series = list(groups)
        self._render_series()

    def _render_series(self) -> None:
        _clear(self.series_box)
        n = len(self._series)
        self.series_heading.setText("SERIES IN MORE THAN ONE FOLDER" + (f"  ·  {n}" if n else ""))
        if not n:
            self.series_box.addWidget(_label("No series is held in more than one folder.", "muted"))
            return
        roots = self._db.list_roots()
        for series in self._series:
            card = _frame("card")
            box = QVBoxLayout(card)
            box.setContentsMargins(16, 12, 16, 14)
            box.setSpacing(10)
            head = QHBoxLayout()
            head.addWidget(_label(series.title, "title"))
            head.addWidget(_label(f"{len(series.folders)} folders", "pill"), 0, Qt.AlignVCenter)
            head.addStretch(1)
            box.addLayout(head)
            side = QHBoxLayout()
            side.setSpacing(12)
            for folder in series.folders:
                side.addWidget(self._folder_panel(folder, roots), 1)
            box.addLayout(side)
            self.series_box.addWidget(card)

    def _folder_panel(self, folder: str, roots) -> QFrame:
        root_name, files, size, mtime_ns = self._folder_stats(folder, roots)
        panel = _frame("folder")
        box = QVBoxLayout(panel)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(6)
        box.addWidget(_Elided(folder))
        grid = QVBoxLayout()
        grid.setSpacing(2)
        for name, value in (("Root", root_name), ("Files", files), ("Size", size), ("Last change", mtime_ns)):
            line = QHBoxLayout()
            key = _label(name, "muted")
            key.setMinimumWidth(84)
            line.addWidget(key)
            line.addWidget(_label(value), 1)
            grid.addLayout(line)
        box.addLayout(grid)
        buttons = QHBoxLayout()
        show = _button("Show in list", "link")
        show.clicked.connect(lambda _=False, f=folder: self.show_in_list.emit(f))
        open_ = _button("Open folder", "link")
        open_.clicked.connect(lambda _=False, f=folder: self._open_folder(f))
        buttons.addWidget(show)
        buttons.addWidget(open_)
        buttons.addStretch(1)
        box.addLayout(buttons)
        return panel

    def _folder_stats(self, folder: str, roots) -> Tuple[str, str, str, str]:
        """(root name, file count, size, last change) of a series folder as the last scan recorded it."""
        norm = os.path.normcase(os.path.normpath(folder))
        root_name = next((r.name for r in roots if norm.startswith(os.path.normcase(os.path.normpath(r.path)).rstrip(os.sep) + os.sep)),
                         "-")
        series = self._db.series_for_folder(folder)
        if series is None:
            return root_name, "-", "-", "-"
        archives = self._db.list_archives(series_id=series.id, status="present")
        if not archives:
            return root_name, "0", "-", "-"
        last = max(a.mtime_ns for a in archives)
        return (root_name, str(len(archives)), human_size(sum(a.size for a in archives)), format_time(iso_from_ns(last)))

    # --- the duplicate files ------------------------------------------------------------------------------

    @property
    def file_groups(self) -> List[DuplicateGroup]:
        """The duplicate numbers the last scan found."""
        return list(self._groups)

    def refresh(self) -> None:
        """Re-read the duplicate files (off the UI thread)."""
        if self._applying:
            return
        self._generation += 1
        generation = self._generation
        self._scanning = True
        self._scan_error = ""
        self._update_summary()
        db = self._db
        self._start(lambda: find_duplicate_files(db),
                    lambda groups: self._scanned(generation, groups),
                    lambda message: self._scan_failed(generation, message))

    def _scanned(self, generation: int, groups: List[DuplicateGroup]) -> None:
        if generation != self._generation:
            return                                          # a newer scan is on its way
        self._scanning = False
        if not self._blocked:
            self._after_apply = False
        self._groups = list(groups)
        self._render_files()
        self._update_summary()
        self.groups_found.emit(len(self._groups))

    def _scan_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._scanning = False
        if not self._blocked:
            self._after_apply = False
        self._scan_error = message
        self._update_summary()

    def _shown_groups(self) -> List[DuplicateGroup]:
        if self._focus is None:
            return self._groups
        return [g for g in self._groups if os.path.normpath(g.folder) == os.path.normpath(self._focus)]

    def _render_files(self) -> None:
        _clear(self.files_box)
        self._rows.clear()
        self._by_group.clear()
        self._card_apply.clear()
        groups = self._shown_groups()
        n_groups = len(groups)
        n_series = len({(g.series_id, g.folder) for g in groups})
        self.show_all_button.setVisible(self._focus is not None)
        heading = "DUPLICATE FILES WITHIN A SERIES" if self._focus is None else "DUPLICATE FILES IN THIS SERIES"
        self.files_heading.setText(heading + (f"  ·  {n_groups} number{'s' if n_groups != 1 else ''} in "
                                              f"{n_series} series" if n_groups else ""))
        if not n_groups:
            empty = ("Looking for duplicate files..." if self._scanning
                     else "No number is held by more than one file of a folder." if self._focus is None
                     else "This series has no duplicate files.")
            self.files_box.addWidget(_label(empty, "muted"))
            return
        card: Optional[QFrame] = None
        card_box: Optional[QVBoxLayout] = None
        current = None
        for group in groups:
            key = (group.series_id, group.folder)
            if key != current:
                current = key
                card = _frame("card")
                card_box = QVBoxLayout(card)
                card_box.setContentsMargins(16, 12, 16, 8)
                card_box.setSpacing(4)
                head = QHBoxLayout()
                head.addWidget(_label(group.title, "title"))
                head.addSpacing(8)
                head.addWidget(_Elided(group.folder, tone="muted"), 1)
                url = self._link_for(group.folder)
                if url:
                    mp = _button("Open in MangaPixer", "link")
                    mp.setToolTip(f"The series in MangaPixer, to compare the copies in its reader\n{url}")
                    mp.clicked.connect(lambda _=False, u=url: self.open_link(u))
                    head.addWidget(mp)
                show = _button("Show in list", "link")
                show.clicked.connect(lambda _=False, f=group.folder: self.show_in_list.emit(f))
                head.addWidget(show)
                only = _button("Apply for this series", "link")
                only.setToolTip("Delete the files marked Discard in this series only (after you confirm)")
                only.clicked.connect(lambda _=False, k=key: self.apply(series=k))
                head.addWidget(only)
                self._card_apply[key] = only
                card_box.addLayout(head)
                self.files_box.addWidget(card)
            card_box.addLayout(self._group_block(group))

    def _group_block(self, group: DuplicateGroup) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(0)
        word = "Volume" if group.kind == "volume" else "Chapter"
        container = os.path.relpath(os.path.dirname(group.files[0].path), group.folder)
        where = "" if container == "." else f"  ·  in {container}"
        title = _label(f"{word} {group.number}  ·  {len(group.files)} files{where}", "heading")
        title.setContentsMargins(0, 10, 0, 4)
        block.addWidget(title)
        keep = default_keep(group)
        known = all(f.path in self._choice for f in group.files)
        remembered = known and not all(self._choice[f.path] for f in group.files)
        rows: List[_FileRow] = []
        for f in sorted(group.files, key=lambda f: (f.modified, f.size, f.path), reverse=True):
            tags = [t for t, best in (("newest", newest(group)), ("largest", largest(group))) if best is f]
            if group.usual_group and (f.group or "").strip().casefold() == group.usual_group.strip().casefold():
                tags.insert(0, "series' group")
            discard = self._choice[f.path] if remembered else f.path != keep.path
            row = _FileRow(f, os.path.relpath(f.path, group.folder), tags, discard)
            row.chosen.connect(lambda path, discard, g=group: self._chose(g, path, discard))
            block.addWidget(row)
            rows.append(row)
            self._rows[f.path] = row
            self._choice[f.path] = discard
        self._by_group.append((group, rows))
        return block

    def _chose(self, group: DuplicateGroup, path: str, discard: bool) -> None:
        rows = next(r for g, r in self._by_group if g is group)
        if discard and all(r.discard for r in rows if r.file.path != path):
            self._rows[path].set_discard(False)             # the last copy of a number always stays
            self.status.setText("Keep at least one copy of each number.")
            return
        self._choice[path] = discard
        self._update_summary()

    # --- choices ------------------------------------------------------------------------------------------

    def discarded_paths(self) -> List[str]:
        return [path for _g, rows in self._by_group for r in rows if r.discard for path in (r.file.path,)]

    def set_discard(self, path: str, discard: bool) -> bool:
        """Choose Discard (or Keep) for a listed file as a click would; False when the choice was refused or the file is unknown."""
        row = self._rows.get(path)
        if row is None:
            return False
        group = next(g for g, rows in self._by_group if row in rows)
        row.set_discard(discard)
        self._chose(group, path, discard)
        return row.discard == discard

    def set_blocked(self, reason: Optional[str]) -> None:
        """The shell says why files must not be deleted right now (a scan is running), or None. After an Apply it also
        keeps the busy state up until the library's rescan is done."""
        self._blocked = reason or None
        if self._blocked is None and not self._scanning:
            self._after_apply = False                       # the rescan after an Apply is done, and so is the re-read
        self._update_summary()

    def busy_text(self) -> str:
        """What the view is busy with, or "" (shown next to the moving bar)."""
        if self._applying:
            n = self._deleting
            return f"Deleting {n} file{'s' if n != 1 else ''}..."
        if self._after_apply and self._blocked:
            return "Rescanning the library..."
        if self._scanning:
            return "Looking for duplicate files..."
        return ""

    def _update_summary(self) -> None:
        discards = [r.file for _g, rows in self._by_group for r in rows if r.discard]
        total = sum(f.size for f in discards)
        busy = self._applying or self._scanning
        working = self.busy_text()
        self.busy_bar.setVisible(bool(working))
        self._body.setEnabled(not working)                  # no Keep / Discard while the list is about to change
        self.apply_button.setText(f"Apply ({len(discards)})" if discards else "Apply")
        self.apply_button.setEnabled(bool(discards) and not busy and not self._blocked)
        for key, button in self._card_apply.items():
            n = sum(1 for g, rows in self._by_group if (g.series_id, g.folder) == key for r in rows if r.discard)
            button.setText(f"Apply for this series ({n})" if n else "Apply for this series")
            button.setEnabled(bool(n) and not busy and not self._blocked)
        self.refresh_button.setEnabled(not self._applying)
        self.status.setProperty("role", "busy" if working else "muted")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        if self._scan_error:
            self.status.setText(f"Could not look for duplicates: {self._scan_error}")
        elif working:
            self.status.setText(working)
        elif self._blocked and discards:
            self.status.setText(f"Deleting is paused: {self._blocked}")
        elif discards:
            self.status.setText(f"{len(discards)} file{'s' if len(discards) != 1 else ''} to discard ({human_size(total)}). "
                                "Nothing is deleted until you apply and confirm.")
        else:
            self.status.setText("")

    # --- one series, MangaPixer links -----------------------------------------------------------------------

    def focus_series(self, folder: Optional[str]) -> None:
        """Show only *folder*'s duplicate files (the List's "Review duplicate files"), or every series (None). The
        Keep / Discard choices of the other series are kept."""
        self._focus = folder
        self._render_files()
        self._update_summary()
        self._scroll.verticalScrollBar().setValue(0)

    def set_series_link(self, link: Optional[Callable[[str], Optional[str]]]) -> None:
        """*link(series folder)* -> the series' page in MangaPixer, or None (not known there / not connected)."""
        self._series_link = link
        self._render_files()
        self._update_summary()

    def _link_for(self, folder: str) -> Optional[str]:
        if self._series_link is None:
            return None
        try:
            return self._series_link(folder) or None
        except Exception:  # noqa: BLE001 - a link is a convenience; it must not break the list
            _log.warning("No MangaPixer link for %s", folder, exc_info=True)
            return None

    def open_link(self, url: str) -> None:
        """Open *url* in the browser. Where there is none (the container's desktop), copy it and say so."""
        if not open_link(url):
            self._show_report([f"No browser here, so the link is copied - paste it into your browser: {url}"], error=False)

    # --- apply --------------------------------------------------------------------------------------------

    def apply(self, series: Optional[Tuple[Optional[int], str]] = None) -> None:
        """Delete the files marked Discard - after the owner has seen every one of them and said yes. *series*
        ``(series id, folder)``: that series' files only (its card's own Apply)."""
        if self._applying or self._scanning:
            return
        selections = [(group, [r.file.path for r in rows if r.discard]) for group, rows in self._by_group
                      if series is None or (group.series_id, group.folder) == series]
        selections = [(g, paths) for g, paths in selections if paths]
        files = [f for g, paths in selections for f in g.files if f.path in set(paths)]
        if not files:
            return
        if self._blocked:
            self._show_report([f"Not deleted: {self._blocked}."], error=True)
            return
        reason = busy_reason(self._db, [f.path for f in files])
        if reason:
            self._show_report([f"Not deleted: {reason}. Try again when it has finished."], error=True)
            return
        if not self._confirm(files):
            self.report.hide()
            self.status.setText("Nothing was deleted.")
            return
        self._applying = True
        self._deleting = len(files)
        self._after_apply = True
        self.report.hide()
        self._update_summary()
        db = self._db
        self._start(lambda: discard_duplicates(db, selections), self._applied,
                    lambda message: self._apply_failed(message))

    def _applied(self, outcomes: List[DiscardOutcome]) -> None:
        self._applying = False
        deleted = [o.path for o in outcomes if o.deleted]
        skipped = [o for o in outcomes if not o.deleted]
        lines = [f"Deleted {len(deleted)} file{'s' if len(deleted) != 1 else ''}."]
        lines += [f"Not deleted: {o.path} - {o.reason}" for o in skipped]
        self._show_report(lines, error=bool(skipped))
        for path in deleted:
            self._choice.pop(path, None)
        if deleted:
            self.files_deleted.emit(deleted)
        self.refresh()

    def _apply_failed(self, message: str) -> None:
        self._applying = False
        self._after_apply = False
        self._show_report([f"Nothing more was deleted: {message}"], error=True)
        self.refresh()

    def _show_report(self, lines: Sequence[str], error: bool) -> None:
        self.report.setProperty("role", "error" if error else "muted")
        self.report.style().unpolish(self.report)
        self.report.style().polish(self.report)
        self.report.setText("\n".join(lines))
        self.report.show()
        self._update_summary()

    # --- threads ------------------------------------------------------------------------------------------

    def _start(self, fn, on_done, on_error) -> None:
        call = start_call(fn, on_done, on_error)
        self._calls.append(call)
        call.finished.connect(lambda c=call: self._calls.remove(c) if c in self._calls else None)

    def stop(self) -> None:
        """On close: let go of calls in flight (their results are dropped)."""
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)


def _clear(layout) -> None:
    """Remove and delete everything in *layout* (widgets, nested layouts and spacers)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
            item.layout().deleteLater()
