"""Add a series by link, as a dialog (owner, 2026-10-11). Paste a MangaUpdates or MangaDex link (or type a title),
pick the library and the folder name; MangaList makes the empty folder and records the match and the source
(:mod:`mangalist.add_series` does the work; this file only asks and shows). Everything that touches a network or the
disk runs off the UI thread (:func:`~mangalist.gui.background.start_call`).

For the main window::

    dlg = AddSeriesDialog(LiveBackend(db), roots, parent=self, current_root_id=picked_library)
    if dlg.exec() == AddSeriesDialog.Accepted:
        rescan(dlg.root)        # dlg.outcome: the AddResult (folder, series id, notes)
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .. import add_series as core
from .background import BackgroundCall, start_call
from .download_style import FONT_SANS, apply_style, set_tone
from .download_widgets import button, card, hbox, label, vbox
from .downloads_backend import BackendError
from .palette import current

_log = logging.getLogger(__name__)

NAME_CHECK_DELAY_MS = 300


class LiveBackend:
    """What the dialog asks, answered by the real services (the tests pass a fake with the same methods)."""

    def __init__(self, db):
        self.db = db

    def lookup(self, text: str) -> core.Lookup:
        return _readable(core.lookup, text)

    def with_mu_record(self, look: core.Lookup, record: Dict[str, Any]) -> core.SeriesInfo:
        return _readable(core.with_mu_record, look, record)

    def find_source(self, mangadex_id: str) -> core.SourceLookup:
        return core.find_mangadex_source(self.db, mangadex_id)

    def existing(self, mu_id: Optional[int]) -> List[str]:
        return core.existing_folders_for(mu_id)

    def problem(self, root, name: str) -> Optional[str]:
        return core.folder_problem(root, name)

    def add(self, root, name: str, info: core.SeriesInfo, source: Optional[core.SourceChoice]) -> core.AddResult:
        from ..renamer import request_mangapixer_scans
        from .mu_worker import _detail_progress

        progress = _detail_progress(info.detail) if info.matched else None
        return _readable(core.add_series, self.db, root, name, info, source=source, progress=progress,
                         request_scans=request_mangapixer_scans)


def _readable(fn: Callable, *args, **kwargs):
    """Run *fn*; an :class:`~mangalist.add_series.AddSeriesError` becomes the :class:`BackendError` the background call
    shows as it is."""
    try:
        return fn(*args, **kwargs)
    except core.AddSeriesError as exc:
        raise BackendError(str(exc)) from None


def _candidate_text(record: Dict[str, Any]) -> str:
    bits = [str(record.get("type") or ""), str(record.get("year") or "")]
    extra = "  [" + "  ".join(b for b in bits if b) + "]" if any(bits) else ""
    return f"{record.get('title') or '?'}{extra}"


class AddSeriesDialog(QDialog):
    """After ``exec()`` accepted: ``outcome`` (the :class:`~mangalist.add_series.AddResult`) and ``root`` (the library
    folder it was made in) are set."""

    def __init__(self, backend, roots: Sequence[Any], parent: Optional[QWidget] = None,
                 current_root_id: Optional[int] = None):
        super().__init__(parent)
        self.setObjectName("addSeriesDialog")
        self.setWindowTitle("Add a series")
        self.resize(660, 780)
        self.setMinimumSize(560, 520)
        self.backend = backend
        self.roots = list(roots)
        self.outcome: Optional[core.AddResult] = None
        self.root = None
        self.look: Optional[core.Lookup] = None
        self.info: Optional[core.SeriesInfo] = None
        self.source: Optional[core.SourceChoice] = None
        self._source_note = ""
        self._busy = 0
        self._calls: List[BackgroundCall] = []
        self._problem: Optional[str] = None
        self._adding = False
        self._closed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)
        outer.addWidget(label("Add a series", "h2"))
        outer.addWidget(label(
            "Paste a MangaUpdates or MangaDex link, or type a title. MangaList makes an empty folder in your library, "
            "remembers the MangaUpdates match and where the chapters are, and rescans, so the series shows up as "
            "missing and can be found and downloaded.", "lead", wrap=True))

        self.input = QLineEdit()
        self.input.setPlaceholderText("A MangaUpdates link, a MangaDex link, or a title")
        self.input.setAccessibleName("Link or title")
        self.input.returnPressed.connect(self.look_up)
        self.btn_lookup = button("Look up", primary=True)
        self.btn_lookup.clicked.connect(self.look_up)
        outer.addLayout(hbox(self.input, self.btn_lookup, spacing=8))

        self.status = label("", "muted", wrap=True)
        outer.addWidget(self.status)

        # the MangaUpdates series to pick (a title search, or a MangaDex series MangaUpdates is not linked from)
        self.pick_box = card()
        self.pick_title = label("Pick the MangaUpdates series", "h3")
        self.candidates = QListWidget()
        self.candidates.setObjectName("addSeriesCandidates")
        self.candidates.setMaximumHeight(170)
        self.candidates.itemActivated.connect(self._picked)
        self.candidates.itemClicked.connect(self._picked)
        self.pick_hint = label("", "muted", wrap=True)
        lay = vbox(self.pick_title, self.candidates, self.pick_hint, spacing=6, margins=(14, 12, 14, 12))
        self.pick_box.setLayout(lay)
        self.pick_box.setVisible(False)
        outer.addWidget(self.pick_box)

        # the series
        self.series_box = card()
        self.series_name = label("", "name", wrap=True, selectable=True)
        self.series_facts = label("", "muted", wrap=True)
        self.series_source = label("", "muted", wrap=True)
        self.series_warning = label("", wrap=True)
        set_tone(self.series_warning, "warn")
        self.series_box.setLayout(vbox(self.series_name, self.series_facts, self.series_source, self.series_warning,
                                       spacing=4, margins=(14, 12, 14, 12)))
        self.series_box.setVisible(False)
        outer.addWidget(self.series_box)

        # where it goes
        self.form_box = card()
        self.library = QComboBox()
        self.library.setAccessibleName("Library")
        for root in self.roots:
            self.library.addItem(root.name, root.id)
            self.library.setItemData(self.library.count() - 1, root.path, Qt.ItemDataRole.ToolTipRole)
        if current_root_id is not None:
            i = self.library.findData(current_root_id)
            if i >= 0:
                self.library.setCurrentIndex(i)
        self.library.currentIndexChanged.connect(self._schedule_check)
        self.folder = QComboBox()
        self.folder.setEditable(True)
        self.folder.setAccessibleName("Folder name")
        self.folder.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.folder.editTextChanged.connect(self._schedule_check)
        self.folder_problem = label("", wrap=True)
        set_tone(self.folder_problem, "bad")
        self.form_box.setLayout(vbox(
            label("Library", "h3"), self.library,
            label("Folder name", "h3"), self.folder,
            label("The English title first; edit it if the library names this series differently.", "muted", wrap=True),
            self.folder_problem, spacing=8, margins=(14, 12, 14, 14)))
        self.form_box.setVisible(False)
        outer.addWidget(self.form_box)
        outer.addStretch(1)

        self.buttons = QDialogButtonBox()
        self.btn_add = button("Add series", primary=True)
        self.btn_cancel = button("Cancel")
        self.buttons.addButton(self.btn_add, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(self.btn_cancel, QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_add.clicked.connect(self.add)
        self.btn_cancel.clicked.connect(self.reject)
        outer.addWidget(self.buttons)

        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(NAME_CHECK_DELAY_MS)
        self._check_timer.timeout.connect(self._check_name)

        apply_style(self, extra=lambda: f"\nQDialog#addSeriesDialog {{ background: {current().surface}; "
                                        f"color: {current().text}; font-family: {FONT_SANS}; font-size: 14px; }}")
        if not self.roots:
            self._say("Add a library folder first (Settings).", "bad")
            self.input.setEnabled(False)
        self._refresh_buttons()

    # --- looking up -----------------------------------------------------------------------------------

    def look_up(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy or not self.roots:
            return
        try:
            core.parse_link(text)
        except core.LinkError as exc:                      # said at once, no request
            self._say(str(exc), "bad")
            return
        self._clear_series()
        self._say("Looking it up...", "")
        self._run(lambda: self.backend.lookup(text), self._looked_up)

    def _looked_up(self, look: core.Lookup) -> None:
        self.look = look
        notes = " ".join(look.notes)
        if look.info is not None:
            self._show_series(look.info)
            if look.candidates and not look.info.matched:
                self._show_candidates(look.candidates, "Pick the MangaUpdates series for it, or add it without one.")
            self._say(notes, "warn" if notes else "")
            return
        if look.candidates:
            self._show_candidates(look.candidates, "")
            self._say("Pick the MangaUpdates series." + (f" {notes}" if notes else ""), "")
        else:
            self._say(notes or "Nothing found.", "warn")

    def _show_candidates(self, records: Sequence[Dict[str, Any]], hint: str) -> None:
        self.candidates.clear()
        for rec in records:
            item = QListWidgetItem(_candidate_text(rec))
            item.setData(Qt.ItemDataRole.UserRole, rec)
            self.candidates.addItem(item)
        self.pick_hint.setText(hint)
        self.pick_hint.setVisible(bool(hint))
        self.pick_box.setVisible(True)

    def _picked(self, item: QListWidgetItem) -> None:
        record = item.data(Qt.ItemDataRole.UserRole)
        if self._busy or not isinstance(record, dict) or self.look is None:
            return
        look = self.look
        self._say("Reading the MangaUpdates series...", "")
        self._run(lambda: self.backend.with_mu_record(look, record), self._show_series_after_pick)

    def _show_series_after_pick(self, info: core.SeriesInfo) -> None:
        self._say("", "")
        self._show_series(info)

    # --- the series -----------------------------------------------------------------------------------

    def _clear_series(self) -> None:
        self.look = self.info = self.source = None
        self._source_note = ""
        self.pick_box.setVisible(False)
        self.series_box.setVisible(False)
        self.form_box.setVisible(False)
        self._refresh_buttons()

    def _show_series(self, info: core.SeriesInfo) -> None:
        self.info = info
        self.source, self._source_note = None, ""
        self.series_name.setText(info.title)
        self.series_facts.setText("  ·  ".join(info.summary()))
        existing = self.backend.existing(info.mu_id)
        self.series_warning.setText("Already in the library as " + ", ".join(f"“{_leaf(p)}”" for p in existing[:3])
                                    + ". Adding it again makes a second folder." if existing else "")
        self.series_warning.setVisible(bool(existing))
        self.series_source.setText("")
        self.series_box.setVisible(True)
        self.folder.blockSignals(True)
        self.folder.clear()
        self.folder.addItems(core.propose_names(info))
        self.folder.setCurrentIndex(0 if self.folder.count() else -1)
        self.folder.blockSignals(False)
        self.form_box.setVisible(True)
        self._check_name()
        if info.mangadex_id:
            self.series_source.setText("Asking Suwayomi for the chapters' source...")
            mdx = info.mangadex_id
            self._run(lambda: self.backend.find_source(mdx), lambda found: self._source_found(info, found))
        else:
            self.series_source.setText("The chapters' source is found when you first open them (Suwayomi).")

    def _source_found(self, info: core.SeriesInfo, found: core.SourceLookup) -> None:
        if info is not self.info:
            return                                          # another series was picked meanwhile
        self.source, self._source_note = found.choice, found.note
        if found.choice is not None:
            self.series_source.setText(f"Chapters from {found.choice.source_name}: “{found.choice.manga_title}” "
                                       "(saved with the series).")
        else:
            self.series_source.setText(found.note)

    # --- the folder name ------------------------------------------------------------------------------

    def current_root(self):
        i = self.library.currentIndex()
        return self.roots[i] if 0 <= i < len(self.roots) else None

    def folder_name(self) -> str:
        return self.folder.currentText()

    def _schedule_check(self, *_args) -> None:
        self._check_timer.start()
        self._refresh_buttons()

    def _check_name(self) -> None:
        """Say at once why the name cannot be used (a name that exists, a Windows rule); the Add button follows."""
        self._check_timer.stop()
        root, name = self.current_root(), self.folder_name()
        self._problem = self.backend.problem(root, name) if root is not None and self.info is not None else None
        self.folder_problem.setText(self._problem or "")
        self.folder_problem.setVisible(bool(self._problem))
        self._refresh_buttons()

    # --- adding ---------------------------------------------------------------------------------------

    def _refresh_buttons(self) -> None:
        ready = (self.info is not None and not self._busy and self.current_root() is not None
                 and bool(self.folder_name().strip()) and not self._problem)
        self.btn_add.setEnabled(ready)
        self.btn_lookup.setEnabled(not self._busy and bool(self.roots))

    def add(self) -> None:
        self._check_name()                                 # a name typed a moment ago has not been checked yet
        root, name, info = self.current_root(), self.folder_name(), self.info
        if root is None or info is None or self._busy or self._problem:
            return
        source = self.source
        self._say(f"Making “{name}”...", "")
        self._run(lambda: self.backend.add(root, name, info, source), lambda res: self._added(root, res))

    def _added(self, root, res: core.AddResult) -> None:
        self.outcome, self.root = res, root
        _log.info("Added the series folder %s (%d note(s))", res.folder.name, len(res.notes))
        if res.notes:
            QMessageBox.information(self, "Series added", f"“{res.folder.name}” was made, but:\n\n"
                                    + "\n".join(res.notes))
        self.accept()

    # --- plumbing -------------------------------------------------------------------------------------

    def _say(self, text: str, tone: str) -> None:
        self.status.setText(text)
        set_tone(self.status, tone)
        self.status.setVisible(bool(text))

    def _run(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        self._busy += 1
        self._refresh_buttons()

        def finished() -> None:
            if self._closed:
                return
            self._busy = max(0, self._busy - 1)
            self._refresh_buttons()

        def failed(msg: str) -> None:
            self._adding = False
            self._say(msg, "bad")

        call = start_call(fn, on_done=on_done, on_error=failed, on_finished=finished)
        self._calls.append(call)

    def reject(self) -> None:
        if not self._adding:                                # the folder is being made: let that finish
            super().reject()

    def done(self, result: int) -> None:
        self._closed = True
        for call in self._calls:
            call.abandon()
        self._calls.clear()
        self._check_timer.stop()
        super().done(result)


def _leaf(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


__all__ = ["AddSeriesDialog", "LiveBackend"]
