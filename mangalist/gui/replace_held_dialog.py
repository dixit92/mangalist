"""Replace held chapters… (owner, 2026-11-22: chapters already held, in parts from one site, replaced by another
group's whole chapters from another site): pick a source, confirm the series on it, tick the held chapters to replace
and Replace. The series' saved source is not changed. :mod:`mangalist.downloads.replace_held` pairs the source's
chapters with the files held; the replaced files go to the holding folder when the new copies are filed (restorable
from the Download tab, emptied after the holding period).

Every lookup runs off the UI thread (``backend.replace_candidates``, ``replace_lookup``, ``send_replacements``); the
dialog asks before anything is sent (*confirm*, injectable for the tests).
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QMessageBox, QStackedWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ..downloads.chapters import MangaMatch
from .background import BackgroundCall, start_call
from .download_style import set_tone
from .download_widgets import button, flat_table, hbox, label
from .tables import cell, resizable_columns

ConfirmFn = Callable[[QWidget, str], bool]

TITLE = "Replace held chapters"
MENU_TEXT = "Replace held chapters…"
MENU_TIP = ("Replace chapter files you already have with another source's or group's copy (for example its whole "
            "chapters for the parts you hold): the old files go to the holding folder, restorable")
COLUMNS = ("", "Chapter", "Replaces", "New copy", "Note")
COL_PICK, COL_CHAPTER, COL_HELD, COL_COPY, COL_NOTE = range(len(COLUMNS))
CAND_COLUMNS = ("Title", "Source")


def _ask(parent: QWidget, text: str) -> bool:
    return QMessageBox.question(parent, TITLE, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


def available(backend) -> bool:
    """The backend can replace held chapters (the built-in extensions or Suwayomi set up)."""
    return all(callable(getattr(backend, n, None)) for n in ("replace_candidates", "replace_lookup",
                                                             "send_replacements", "suwayomi_sources"))


class ReplaceHeldDialog(QDialog):
    def __init__(self, backend, series_id: int, title: str, titles: Sequence[str], parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmFn] = None):
        super().__init__(parent)
        self.backend = backend
        self.series_id, self.series_title = series_id, title
        self.titles = tuple(t for t in titles if t) or (title,)
        self._confirm = confirm or _ask
        self._calls: List[BackgroundCall] = []
        self._candidates: List[MangaMatch] = []
        self.lookup = None
        self.sent: list = []
        self.setWindowTitle(TITLE)
        self.resize(900, 560)
        self._build()
        self._load_sources()

    # --- the widgets ------------------------------------------------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.addWidget(label(self.series_title, "h2"))
        outer.addWidget(label("Chapters you already have, replaced by another source's or group's copy. The files "
                              "replaced go to the holding folder when the new copies are filed - restorable from the "
                              "Download tab, emptied after the holding period. The series' saved source stays as it "
                              "is.", "muted", wrap=True))
        self.source_combo = QComboBox()
        self.btn_find = button("Find the series", tip="Search the source for this series by its titles")
        outer.addLayout(hbox(label("Source"), self.source_combo, self.btn_find, None, spacing=8))
        self.stack = QStackedWidget()
        cand = QWidget()
        cv = QVBoxLayout(cand)
        cv.setContentsMargins(0, 0, 0, 0)
        self.cand_note = label("Choose a source and Find the series.", "lead", wrap=True)
        self.cand_table = flat_table("replaceCandidates", CAND_COLUMNS)
        self.btn_use = button("Use this series", primary=True, tip="List the chapters this source has that you hold")
        cv.addWidget(self.cand_note)
        cv.addWidget(self.cand_table, 1)
        cv.addLayout(hbox(None, self.btn_use))
        self.stack.addWidget(cand)
        rows = QWidget()
        rv = QVBoxLayout(rows)
        rv.setContentsMargins(0, 0, 0, 0)
        self.rows_note = label("", "lead", wrap=True)
        self.table = flat_table("replaceRows", COLUMNS, select_rows=False)
        resizable_columns(self.table, {COL_PICK: 28, COL_CHAPTER: 70, COL_HELD: 260, COL_COPY: 200})
        self.btn_back = button("Back to the search", link=True)
        self.btn_replace = button("Replace", primary=True)
        rv.addWidget(self.rows_note)
        rv.addWidget(self.table, 1)
        rv.addLayout(hbox(self.btn_back, None, self.btn_replace))
        self.stack.addWidget(rows)
        outer.addWidget(self.stack, 1)
        self.status_label = label("", wrap=True, selectable=True)
        self.btn_close = button("Close")
        outer.addLayout(hbox(self.status_label, None, self.btn_close))
        self.btn_find.clicked.connect(self.find)
        self.btn_use.clicked.connect(self.use_selected)
        self.btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        self.btn_replace.clicked.connect(self.replace_selected)
        self.btn_close.clicked.connect(self.reject)
        self.cand_table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemChanged.connect(lambda *_a: self._update_buttons())
        self._update_buttons()

    def say(self, text: str, tone: str = "") -> None:
        self.status_label.setText(text)
        set_tone(self.status_label, tone)

    def _busy(self) -> bool:
        return bool(self._calls)

    def _update_buttons(self) -> None:
        busy = self._busy()
        self.btn_find.setEnabled(not busy and self.source_combo.currentData() is not None)
        self.btn_use.setEnabled(not busy and self.selected_candidate() is not None)
        n = len(self.picks())
        self.btn_replace.setEnabled(not busy and n > 0)
        self.btn_replace.setText(f"Replace {n} chapter{'' if n == 1 else 's'}" if n else "Replace")

    # --- background calls -------------------------------------------------------------------------------------------

    def _spawn(self, fn, on_done, on_error) -> None:
        made: list = []

        def finished() -> None:
            if made and made[0] in self._calls:
                self._calls.remove(made[0])
            if made and not getattr(made[0], "abandoned", False):
                self._update_buttons()

        call = start_call(fn, on_done, on_error, finished)
        made.append(call)
        self._calls.append(call)
        self._update_buttons()

    def done(self, result: int) -> None:  # noqa: N802 - Qt
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()
        super().done(result)

    # --- 1. the source and the series on it -------------------------------------------------------------------------

    def _load_sources(self) -> None:
        self.say("Reading the installed sources…")
        self._spawn(self.backend.suwayomi_sources, self._on_sources, lambda m: self.say(f"No sources: {m}", "bad"))

    def _on_sources(self, sources) -> None:
        self.source_combo.clear()
        for src, allowed in sources:
            self.source_combo.addItem(src.display_name + ("" if allowed else "  (not allowed for downloads)"), src.id)
        self.say("" if sources else "No source is installed (Settings > Built-in extensions > Extensions).",
                 "" if sources else "bad")
        self._update_buttons()

    def find(self) -> bool:
        source_id = self.source_combo.currentData()
        if source_id is None or self._busy():
            return False
        name = self.source_combo.currentText().split("  (")[0]
        self.say(f"Searching {name}…")
        self._spawn(lambda: self.backend.replace_candidates(source_id, self.titles), self._on_candidates,
                    lambda m: self.say(f"Could not search {name}: {m}", "bad"))
        return True

    def _on_candidates(self, found: Sequence[MangaMatch]) -> None:
        self._candidates = list(found)
        self.cand_table.setRowCount(len(self._candidates))
        for r, m in enumerate(self._candidates):
            self.cand_table.setItem(r, 0, cell(m.manga.title))
            self.cand_table.setItem(r, 1, cell(m.source.display_name))
        if self._candidates:
            self.cand_table.selectRow(0)
        self.cand_note.setText("Is this the series? Choose it and Use this series." if self._candidates else
                               "The source has no series by these titles.")
        self.say("")
        self.stack.setCurrentIndex(0)
        self._update_buttons()

    def selected_candidate(self) -> Optional[MangaMatch]:
        rows = {i.row() for i in self.cand_table.selectedIndexes()}
        return self._candidates[min(rows)] if rows and min(rows) < len(self._candidates) else None

    def use_selected(self) -> bool:
        match = self.selected_candidate()
        if match is None or self._busy():
            return False
        self.say(f"Reading {match.source.display_name}'s chapters…")
        self._spawn(lambda: self.backend.replace_lookup(self.series_id, match), self._on_lookup,
                    lambda m: self.say(f"Could not read the chapters: {m}", "bad"))
        return True

    # --- 2. the chapters it would replace ---------------------------------------------------------------------------

    def _on_lookup(self, lookup) -> None:
        self.lookup = lookup
        self.table.blockSignals(True)
        self.table.setRowCount(len(lookup.rows))
        for r, row in enumerate(lookup.rows):
            pick = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled | (Qt.ItemFlag.ItemIsUserCheckable if row.offered else Qt.ItemFlag(0))
            pick.setFlags(flags)
            pick.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(r, COL_PICK, pick)
            self.table.setItem(r, COL_CHAPTER, cell(row.number))
            self.table.setItem(r, COL_HELD, cell(" + ".join(f.rel.split("/")[-1] for f in row.held) or "-"))
            self.table.removeCellWidget(r, COL_COPY)
            if row.offered and len(row.options) > 1:
                combo = QComboBox()
                for ch in row.options:
                    combo.addItem(ch.scanlator or "(no group)", ch.id)
                self.table.setCellWidget(r, COL_COPY, combo)
                self.table.setItem(r, COL_COPY, cell(""))
            else:
                self.table.setItem(r, COL_COPY, cell((row.default.scanlator or "(no group)") if row.default else "-"))
            self.table.setItem(r, COL_NOTE, cell(row.note))
        self.table.blockSignals(False)
        offered = len(lookup.offered)
        self.rows_note.setText(
            f"{lookup.source_name} has {offered} chapter{'' if offered == 1 else 's'} you hold that can be replaced"
            + (f" ({len(lookup.rows) - offered} listed but not replaceable: see the note)"
               if len(lookup.rows) > offered else "") + ". Tick the ones to replace."
            if lookup.rows else f"{lookup.source_name} has none of the chapters you hold.")
        self.say("")
        self.stack.setCurrentIndex(1)
        self._update_buttons()

    def set_checked(self, numbers: Sequence[str], checked: bool = True) -> None:
        """Tick (untick) the rows of *numbers* that can be replaced (the tests; the owner clicks)."""
        if self.lookup is None:
            return
        for r, row in enumerate(self.lookup.rows):
            if row.number in numbers and row.offered:
                self.table.item(r, COL_PICK).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def picks(self) -> List[Tuple[object, List[str], object]]:
        """``[(chapter, [paths it replaces], row)]`` of the ticked rows."""
        out = []
        if self.lookup is None:
            return out
        for r, row in enumerate(self.lookup.rows):
            item = self.table.item(r, COL_PICK)
            if not row.offered or item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            chapter = row.default
            combo = self.table.cellWidget(r, COL_COPY)
            if isinstance(combo, QComboBox):
                chapter = next((ch for ch in row.options if ch.id == combo.currentData()), row.default)
            if chapter is not None:
                out.append((chapter, [f.path for f in row.held], row))
        return out

    def confirmation_text(self, picks) -> str:
        source = self.lookup.source_name if self.lookup is not None else ""
        lines = [f"ch {row.number}: {' + '.join(f.rel.split('/')[-1] for f in row.held)}  ->  "
                 f"{chapter.scanlator or 'no group'} ({source})" for chapter, _paths, row in picks[:12]]
        more = f"\n… and {len(picks) - 12} more" if len(picks) > 12 else ""
        return (f"Replace {len(picks)} held chapter{'' if len(picks) == 1 else 's'} of {self.series_title} with "
                f"{source}'s copies?\n\n" + "\n".join(lines) + more +
                "\n\nThe files replaced go to the holding folder when each new copy is filed (restorable from the "
                "Download tab; emptied after the holding period). If a new copy cannot be filed, they are put back.")

    def replace_selected(self) -> bool:
        picks = self.picks()
        if not picks or self._busy() or self.lookup is None:
            return False
        if not self._confirm(self, self.confirmation_text(picks)):
            return False
        lookup = self.lookup
        payload = [(chapter, paths) for chapter, paths, _row in picks]
        self.say(f"Sending {len(picks)} chapter{'' if len(picks) == 1 else 's'}…")
        self._spawn(lambda: self.backend.send_replacements(self.series_id, lookup.match, payload,
                                                           lookup.manga_title, lookup.source_name),
                    self._on_sent, lambda m: self.say(f"Not sent: {m}", "bad"))
        return True

    def _on_sent(self, outcome) -> None:
        self.sent.extend(outcome.records)
        self.say(outcome.text() + " - the files they replace move to the holding folder when each is "
                 "filed.", "bad" if outcome.error else "ok")
        self.set_checked([row.number for _c, _p, row in self.picks()], False)


def open_replace_dialog(parent: QWidget, backend, series_id: Optional[int], title: str, titles: Sequence[str]) -> bool:
    """The dialog for one series (False without a series id or a backend that can replace)."""
    if series_id is None or not available(backend):
        return False
    ReplaceHeldDialog(backend, series_id, title, titles, parent).exec()
    return True


__all__ = ["MENU_TEXT", "MENU_TIP", "ReplaceHeldDialog", "available", "open_replace_dialog"]
