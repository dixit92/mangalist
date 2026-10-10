"""The chapters panel: the missing chapters of ONE series as Suwayomi has them, the scanlation group they come from, the
folder they will be filed into, and Send to Suwayomi. The releases panel's counterpart for the Missing chapters group of
the Download tab (owner, 2026-10-09: "we don't want a completely different interface"): the same header, folder choice,
table, footer and status line; the same chips and In progress rows once sent.

- **The lookup** (:meth:`ChaptersPanel.open_series`) runs off the UI thread (``backend.chapter_lookup``): the series is
  found in Suwayomi by the MangaDex id MangaPixer links, else by title on the owner's other sources - those matches are
  listed for the owner to CONFIRM ("Use this series") before any chapter is listed; the confirmed one is remembered for
  the series. "Not this series?" forgets a match and looks again.
- **The table**: one row per missing chapter - ticked by default when the source has it; the group it comes from (a
  choice when more than one group has it); the chapter's title as the source names it. A chapter already sent shows
  how it stands and cannot be sent again; a chapter the source does not have says so.
- **The group** above the table: the series' group (the owner's choice for the series, else the folder's usual group,
  else the group with the most chapters) and why; changing it re-picks every row and is remembered for the series.
- A line says when chapters of the series are already in Suwayomi's download queue.
- **Try another source…** (when the matched source lacks some missing chapters - owner, 2026-10-10: MangaDex had 3 of
  90): the owner's other sources by title, to confirm one for the series; "Back to the chapters" keeps the match.
- **Send to Suwayomi** asks first (the chapters, the source, the group, the folder) and then records and enqueues them.
"""

from __future__ import annotations

import html
from dataclasses import replace
from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..downloads.chapters import ChapterLookup, ChapterRow, MangaMatch, group_key
from ..downloads.contracts import DownloadRecord, SuwayomiChapter
from .background import BackgroundCall, start_call
from .download_rules import download_chip
from .download_style import set_tone
from .download_widgets import button, flat_table, hbox, label
from .downloads_backend import DownloadsBackend
from .releases_panel import NO_FOLDER_CHOSEN, folder_text
from .shell import SECTION_SOURCES
from .tables import cell, resizable_columns
from .volumes_target import numbers_text

ConfirmFn = Callable[[QWidget, str], bool]

COLUMNS = ("", "Chapter", "Group", "Title", "Status")
COL_PICK, COL_CHAPTER, COL_GROUP, COL_TITLE, COL_STATUS = range(len(COLUMNS))
CAND_COLUMNS = ("Source", "Series in Suwayomi")
PAGE_MESSAGE, PAGE_CANDIDATES, PAGE_CHAPTERS = 0, 1, 2
FOOTER_NOTE = ("MangaList files each chapter into the series folder under its naming scheme once Suwayomi has it, then "
               "Suwayomi deletes its own copy. Chapter downloads are outside the download budget.")
NO_GROUP = "(no group named)"
ROLE_CHAPTER = Qt.ItemDataRole.UserRole + 1


def _default_confirm(parent: QWidget, text: str) -> bool:
    return QMessageBox.question(parent, "Send to Suwayomi", text,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def _group_name(group: Optional[str]) -> str:
    return group or NO_GROUP


def how_text(match: MangaMatch) -> str:
    if match.how == "mangadex-id":
        return "found by the MangaDex id MangaPixer links"
    if match.how == "confirmed":
        return "confirmed by you"
    return "found by its title - not confirmed"


class ChaptersPanel(QWidget):
    sent = Signal(object)               # a list of DownloadRecord, once Suwayomi has them
    looked_up = Signal(int, object)     # (series id, ChapterLookup or None on failure) - the host's chips
    settings_requested = Signal(str)

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmFn] = None,
                 downloads_for: Optional[Callable[[int], Sequence[DownloadRecord]]] = None):
        super().__init__(parent)
        self.setObjectName("chaptersPanel")
        self._backend = backend
        self._confirm = confirm or _default_confirm
        self._downloads_for = downloads_for
        self._calls: List[BackgroundCall] = []
        self._series_id: Optional[int] = None
        self._title = ""
        self._missing: tuple = ()
        self._titles: tuple = ()
        self.lookup: Optional[ChapterLookup] = None
        self._busy = False
        self._combos: Dict[int, QComboBox] = {}
        self._loading = False
        self._build_ui()

    # --- UI ---------------------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        head = hbox(spacing=16)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = label("", "h2")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.subtitle_label = label("", "muted", wrap=True)
        self.subtitle_label.setTextFormat(Qt.TextFormat.RichText)
        self.match_label = label("", "muted", wrap=True)
        self.match_label.setTextFormat(Qt.TextFormat.PlainText)
        self.queued_label = label("", "muted", wrap=True)
        self.queued_label.setVisible(False)
        for w in (self.title_label, self.subtitle_label, self.match_label, self.queued_label):
            titles.addWidget(w)
        head.addLayout(titles, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(90)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.btn_forget = button("Not this series?", link=True,
                                 tip="Forget this match and look the series up again (by title on your sources)")
        self.btn_forget.clicked.connect(self.forget_match)
        self.btn_again = button("Look up again")
        self.btn_again.clicked.connect(self.look_up_again)
        self.btn_other = button("Try another source…", link=True,
                                tip="Some missing chapters are not on this source: look the series up by title on your "
                                    "other Suwayomi sources and get its chapters from there")
        self.btn_other.clicked.connect(self.other_sources)
        self.btn_other.setVisible(False)
        self._before_other: Optional[ChapterLookup] = None
        head.addWidget(self.progress)
        head.addWidget(self.btn_other)
        head.addWidget(self.btn_forget)
        head.addWidget(self.btn_again)
        outer.addLayout(head)

        self.folder_row = QWidget()
        fr = QVBoxLayout(self.folder_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.setSpacing(4)
        self.folder_label = label("", wrap=True, selectable=True)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._update_send)
        fr.addWidget(self.folder_label)
        fr.addWidget(self.folder_combo)
        self.folder_row.setVisible(False)
        outer.addWidget(self.folder_row)

        self.stack = QStackedWidget()
        msg = QWidget()
        mv = QVBoxLayout(msg)
        mv.setContentsMargins(0, 24, 0, 0)
        self.message_text = label("", "lead", wrap=True, selectable=True)
        self.message_action = button("", primary=True)
        self.message_action.clicked.connect(lambda: self.settings_requested.emit(self._section or ""))
        self.message_action.setVisible(False)
        self._section: Optional[str] = None
        mv.addWidget(self.message_text)
        mv.addLayout(hbox(self.message_action, None))
        mv.addStretch(1)
        self.stack.addWidget(msg)

        cand = QWidget()
        cv = QVBoxLayout(cand)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(8)
        self.cand_note = label("", "lead", wrap=True)
        self.cand_table = flat_table("candidatesTable", CAND_COLUMNS)
        resizable_columns(self.cand_table, {0: 180, 1: 420})
        self.cand_table.itemSelectionChanged.connect(self._update_confirm)
        self.btn_confirm = button("Use this series", primary=True,
                                  tip="This is the series: MangaList remembers it and lists its chapters")
        self.btn_confirm.clicked.connect(self.confirm_selected)
        self.btn_cand_back = button("Back to the chapters", link=True,
                                    tip="Keep the source this series has now")
        self.btn_cand_back.clicked.connect(self.back_to_chapters)
        self.btn_cand_back.setVisible(False)
        cv.addWidget(self.cand_note)
        cv.addWidget(self.cand_table, 1)
        cv.addLayout(hbox(self.btn_cand_back, None, self.btn_confirm))
        self.stack.addWidget(cand)

        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(8)
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(220)
        self.group_combo.currentIndexChanged.connect(self._on_group_changed)
        self.group_reason = label("", "muted")
        pv.addLayout(hbox(label("Group"), self.group_combo, self.group_reason, None, spacing=8))
        self.table = flat_table("chaptersTable", COLUMNS, select_rows=False)
        self.table.setSortingEnabled(False)
        resizable_columns(self.table, {COL_PICK: 36, COL_CHAPTER: 90, COL_GROUP: 220, COL_TITLE: 360, COL_STATUS: 180})
        self.table.itemChanged.connect(self._update_send)
        pv.addWidget(self.table, 1)
        self.note_label = label(FOOTER_NOTE, "muted", wrap=True)
        self.btn_send = button("Send to Suwayomi", primary=True)
        self.btn_send.setMinimumHeight(38)
        self.btn_send.clicked.connect(self.send_selected)
        pv.addLayout(hbox(self.note_label, self.btn_send, spacing=12))
        self.stack.addWidget(page)
        outer.addWidget(self.stack, 1)
        self.status_label = label("", wrap=True, selectable=True)
        outer.addWidget(self.status_label)
        self.show_message("", "Select a series to see its chapters.")

    # --- what the panel shows ----------------------------------------------------------------------------

    def show_message(self, title: str, text: str, action: Optional[str] = None, section: Optional[str] = None) -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText("")
        self.match_label.setText("")
        self.queued_label.setVisible(False)
        self.message_text.setText(text)
        self._section = section
        self.message_action.setText(action or "")
        self.message_action.setVisible(bool(action))
        self.folder_row.setVisible(False)
        self.btn_forget.setVisible(False)
        self.btn_other.setVisible(False)
        self.btn_again.setVisible(self._series_id is not None and bool(title))
        self.stack.setCurrentIndex(PAGE_MESSAGE)

    def open_series(self, series_id: int, title: str, missing: Sequence[str], titles: Sequence[str],
                    cached: Optional[ChapterLookup] = None) -> None:
        """Show the series' chapters: *cached* when the host kept a lookup, else look it up now (off the UI thread)."""
        self._series_id, self._title = series_id, title
        self._missing, self._titles = tuple(missing), tuple(titles) or (title,)
        self.status_label.setText("")
        if cached is not None and cached.series_id == series_id and cached.missing == self._missing:
            self.show_lookup(cached)
            return
        self._look_up()

    def look_up_again(self) -> None:
        if self._series_id is not None and not self._busy:
            self._look_up()

    def _look_up(self) -> None:
        sid, missing, titles = self._series_id, self._missing, self._titles
        self.lookup = None
        self.show_message(self._title, "Looking the series up in Suwayomi...")
        self.subtitle_label.setText(f"Missing {html.escape('ch ' + numbers_text(missing))}")
        self._set_busy(True)
        backend = self._backend
        self._spawn(lambda: backend.chapter_lookup(sid, missing, titles),
                    lambda lookup, s=sid: self._on_lookup(s, lookup),
                    lambda message, s=sid: self._on_lookup_error(s, message))

    def _spawn(self, fn, on_done, on_error) -> None:
        made: list = []
        call = start_call(fn, on_done, on_error, lambda: self._finished(made[0] if made else None))
        made.append(call)
        self._calls.append(call)

    def _finished(self, call) -> None:
        if call in self._calls:
            self._calls.remove(call)
        if not self._calls:
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.btn_again.setEnabled(not busy)
        self._update_send()

    def _on_lookup(self, series_id: int, lookup: ChapterLookup) -> None:
        self.looked_up.emit(series_id, lookup)
        if series_id == self._series_id:
            self.show_lookup(lookup)

    def _on_lookup_error(self, series_id: int, message: str) -> None:
        self.looked_up.emit(series_id, None)
        if series_id == self._series_id:
            self.show_message(self._title, f"Suwayomi could not be asked: {message}")
            self.btn_again.setVisible(True)

    def show_lookup(self, lookup: ChapterLookup) -> None:
        self.lookup = lookup
        self.title_label.setText(self._title)
        self.btn_again.setVisible(True)
        self._show_folder(lookup)
        missing = f"Missing {html.escape('ch ' + numbers_text(lookup.missing))}"
        if lookup.error:
            self.show_message(self._title, f"Cannot look this series up: {lookup.error}", "Open Settings",
                              SECTION_SOURCES)
            self.subtitle_label.setText(missing)
            return
        if lookup.match is None:
            self._show_candidates(lookup, missing)
            return
        match = lookup.match
        self.match_label.setText(f"{match.source.display_name}: {lookup.manga_title or match.manga.title} - "
                                 f"{how_text(match)}")
        self.btn_forget.setVisible(True)
        queued = lookup.queued_in_suwayomi
        self.queued_label.setText(f"Already in Suwayomi's download queue: ch {numbers_text(queued)}" if queued else "")
        self.queued_label.setVisible(bool(queued))
        if lookup.candidates:                               # MangaDex has none of them: the owner's other sources
            self._show_candidates(lookup, missing, keep_match=True)
            return
        self._before_other = None
        self._fill_groups(lookup)
        self._fill_rows(lookup.rows)
        self.btn_other.setVisible(bool(lookup.not_available) and callable(getattr(self._backend, "other_candidates",
                                                                                  None)))
        self.stack.setCurrentIndex(PAGE_CHAPTERS)
        self._update_subtitle()
        self._update_send()

    def _show_candidates(self, lookup: ChapterLookup, missing: str, keep_match: bool = False,
                         lead: Optional[str] = None) -> None:
        self.subtitle_label.setText(missing)
        self.btn_other.setVisible(False)
        self.btn_cand_back.setVisible(lead is not None)
        if not keep_match:
            self.match_label.setText("; ".join(lookup.notes))
            self.btn_forget.setVisible(False)
        if not lookup.candidates:
            note = "; ".join(lookup.notes)
            self.show_message(self._title, "None of your Suwayomi sources has this series" +
                              (f" ({note})." if note else ".") + " Allow more sources under Settings > Download "
                              "sources > Suwayomi sources, or install their extensions in Suwayomi.", "Open Settings",
                              SECTION_SOURCES)
            self.subtitle_label.setText(missing)
            self.btn_again.setVisible(True)
            return
        if lead is None:
            lead = "MangaDex has none of the missing chapters. " if keep_match else ""
        self.cand_note.setText(lead + "MangaList found these by title on your other sources. Pick the one that is "
                               "this series - it is remembered for the series - or look again later.")
        self.cand_table.setRowCount(len(lookup.candidates))
        for row, cand in enumerate(lookup.candidates):
            self.cand_table.setItem(row, 0, cell(cand.source.display_name))
            item = cell(cand.manga.title)
            item.setToolTip(cand.manga.url)
            self.cand_table.setItem(row, 1, item)
        self.cand_table.clearSelection()
        self.stack.setCurrentIndex(PAGE_CANDIDATES)
        self._update_confirm()

    def _show_folder(self, lookup: ChapterLookup) -> None:
        placement = lookup.placement
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        if lookup.placement_error or placement is None:
            self.folder_label.setText(f"Could not work out the target folder: {lookup.placement_error or 'unknown'}")
            self.folder_combo.setVisible(False)
            self.folder_row.setVisible(True)
        elif placement.ambiguous:
            self.folder_label.setText(f"Which folder gets the chapters? {placement.reason}")
            self.folder_combo.addItem(NO_FOLDER_CHOSEN, None)
            for option in placement.options:
                self.folder_combo.addItem(option, option)
            self.folder_combo.setVisible(True)
            self.folder_row.setVisible(True)
        else:
            self.folder_row.setVisible(False)
        self.folder_combo.blockSignals(False)

    def _update_subtitle(self) -> None:
        lookup = self.lookup
        if lookup is None:
            return
        target, placement = self.chosen_target_dir(), lookup.placement
        missing = f"Missing {html.escape('ch ' + numbers_text(lookup.missing))}"
        if placement is not None and target:
            where = f"files go to {html.escape(folder_text(target, placement.series_dir))}"
            if not placement.ambiguous and placement.reason:
                where += f" ({html.escape(placement.reason)})"
        else:
            where = "choose the folder the chapters go to"
        not_there = lookup.not_available
        extra = f" &middot; not on {html.escape(lookup.source_name)}: ch {numbers_text(not_there)}" if not_there else ""
        self.subtitle_label.setText(f"{missing} &middot; {where}{extra}")

    # --- groups and rows ------------------------------------------------------------------------------------

    def _fill_groups(self, lookup: ChapterLookup) -> None:
        self._loading = True
        self.group_combo.clear()
        for group, count in sorted(lookup.groups.items(), key=lambda kv: (-kv[1], group_key(kv[0]))):
            self.group_combo.addItem(f"{_group_name(group)} ({count})", group)
        current = next((i for i in range(self.group_combo.count())
                        if group_key(self.group_combo.itemData(i)) == group_key(lookup.group.group)), 0)
        self.group_combo.setCurrentIndex(current)
        self.group_combo.setEnabled(self.group_combo.count() > 1)
        self.group_reason.setText(lookup.group.reason)
        self._loading = False

    def _status_of(self, row: ChapterRow) -> str:
        if row.in_hand is not None:
            return download_chip(row.in_hand)[0]
        if not row.available:
            return f"not on {self.lookup.source_name}" if self.lookup else "not available"
        return ""

    def _fill_rows(self, rows: Sequence[ChapterRow]) -> None:
        self._loading = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        self._combos = {}
        for r, row in enumerate(rows):
            pick = QTableWidgetItem()
            sendable = row.available and row.in_hand is None
            pick.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable if sendable
                          else Qt.ItemFlag.ItemIsEnabled)
            pick.setCheckState(Qt.CheckState.Checked if sendable else Qt.CheckState.Unchecked)
            self.table.setItem(r, COL_PICK, pick)
            self.table.setItem(r, COL_CHAPTER, cell(row.number))
            self.table.setItem(r, COL_STATUS, cell(self._status_of(row)))
            if len(row.options) > 1 and sendable:
                combo = QComboBox()
                for ch in row.options:
                    combo.addItem(_group_name(ch.scanlator), ch.id)
                combo.currentIndexChanged.connect(lambda _i, rr=r: self._row_group_changed(rr))
                self.table.setCellWidget(r, COL_GROUP, combo)
                self._combos[r] = combo
                self.table.setItem(r, COL_GROUP, cell(""))
            else:
                self.table.setItem(r, COL_GROUP, cell(_group_name(row.default.scanlator) if row.default else "-"))
            self.table.setItem(r, COL_TITLE, cell(row.default.name if row.default else ""))
        self.table.blockSignals(False)
        self._loading = False

    def _row_group_changed(self, r: int) -> None:
        ch = self.chosen_chapter(r)
        item = self.table.item(r, COL_TITLE)
        if item is not None and ch is not None:
            item.setText(ch.name)

    def _on_group_changed(self, *_args) -> None:
        if self._loading or self.lookup is None or self._series_id is None:
            return
        group = self.group_combo.currentData()
        for r, combo in self._combos.items():
            row = self.lookup.rows[r]
            idx = next((i for i, ch in enumerate(row.options) if group_key(ch.scanlator) == group_key(group)), None)
            if idx is not None:
                combo.setCurrentIndex(idx)
        self.group_reason.setText("your choice for this series")
        sid, backend = self._series_id, self._backend
        setter = getattr(backend, "set_series_group", None)
        if callable(setter):
            self._spawn(lambda: setter(sid, group), lambda _r: None, self._show_error)

    def chosen_chapter(self, r: int) -> Optional[SuwayomiChapter]:
        if self.lookup is None or r >= len(self.lookup.rows):
            return None
        row = self.lookup.rows[r]
        combo = self._combos.get(r)
        if combo is not None:
            cid = combo.currentData()
            return next((ch for ch in row.options if ch.id == cid), row.default)
        return row.default

    def picks(self) -> List[SuwayomiChapter]:
        """The ticked chapters with the group chosen for each."""
        out = []
        if self.lookup is None:
            return out
        for r in range(self.table.rowCount()):
            item = self.table.item(r, COL_PICK)
            ch = self.chosen_chapter(r)
            if item is not None and item.checkState() == Qt.CheckState.Checked and ch is not None:
                out.append(ch)
        return out

    def chosen_target_dir(self) -> Optional[str]:
        lookup = self.lookup
        if lookup is None or lookup.placement is None or lookup.placement_error:
            return None
        if lookup.placement.ambiguous:
            return self.folder_combo.currentData()
        return lookup.placement.target_dir

    def send_blocker(self) -> Optional[str]:
        if self.lookup is None or self.lookup.match is None:
            return "no series found in Suwayomi yet"
        if self._busy:
            return "busy"
        if not self.chosen_target_dir():
            return "choose the folder first"
        if not self.picks():
            return "tick at least one chapter"
        return None

    def _update_send(self, *_args) -> None:
        if self._loading:
            return
        blocker = self.send_blocker()
        self.btn_send.setEnabled(blocker is None)
        self.btn_send.setToolTip(blocker or "")
        n = len(self.picks()) if self.lookup is not None else 0
        self.btn_send.setText(f"Send {n} to Suwayomi" if n else "Send to Suwayomi")
        if self.lookup is not None and self.lookup.match is not None:
            self._update_subtitle()

    def _update_confirm(self) -> None:
        self.btn_confirm.setEnabled(bool(self.cand_table.selectionModel().selectedRows()) and not self._busy)

    # --- confirming a title match ------------------------------------------------------------------------

    def selected_candidate(self) -> Optional[MangaMatch]:
        rows = self.cand_table.selectionModel().selectedRows()
        if not rows or self.lookup is None:
            return None
        return self.lookup.candidates[rows[0].row()]

    def confirm_selected(self) -> bool:
        cand = self.selected_candidate()
        sid = self._series_id
        if cand is None or sid is None or self._busy:
            return False
        backend = self._backend
        self._set_busy(True)
        self._spawn(lambda: backend.confirm_match(sid, cand), lambda _r: self._look_up(), self._show_error)
        return True

    def other_sources(self) -> bool:
        """The matched source lacks some missing chapters: the owner's other sources, by title (off the UI thread)."""
        lookup, sid = self.lookup, self._series_id
        find = getattr(self._backend, "other_candidates", None)
        if lookup is None or lookup.match is None or sid is None or self._busy or not callable(find):
            return False
        exclude, titles = lookup.match.source.id, self._titles
        set_tone(self.status_label, "")
        self.status_label.setText("Searching your other sources by title...")
        self._set_busy(True)
        self._spawn(lambda: find(sid, titles, exclude), lambda found, s=sid, lk=lookup: self._on_others(s, lk, found),
                    self._show_error)
        return True

    def _on_others(self, series_id: int, before: ChapterLookup, found) -> None:
        if series_id != self._series_id or self.lookup is not before:
            return
        if not found:
            self.status_label.setText("None of your other Suwayomi sources has this series by title. Allow more under "
                                      "Settings > Download sources > Suwayomi sources.")
            return
        self.status_label.setText("")
        self._before_other = before
        self.lookup = replace(before, candidates=list(found))
        source = before.source_name or before.match.source.display_name
        lead = (f"{source} does not have ch {numbers_text(before.not_available)}. Chapters "
                "already sent stay as they are. ")
        self._show_candidates(self.lookup, f"Missing {html.escape('ch ' + numbers_text(before.missing))}",
                              keep_match=True, lead=lead)

    def back_to_chapters(self) -> None:
        if self._before_other is not None:
            self.show_lookup(self._before_other)

    def forget_match(self) -> bool:
        sid = self._series_id
        forget = getattr(self._backend, "forget_match", None)
        if sid is None or self._busy or not callable(forget):
            return False
        self._set_busy(True)
        self._spawn(lambda: forget(sid), lambda _r: self._look_up(), self._show_error)
        return True

    # --- sending ---------------------------------------------------------------------------------------

    def confirmation_text(self, picks: Sequence[SuwayomiChapter], target_dir: str) -> str:
        lookup = self.lookup
        assert lookup is not None and lookup.match is not None
        groups = sorted({_group_name(ch.scanlator) for ch in picks})
        return (f"Send {len(picks)} chapter{'s' if len(picks) != 1 else ''} of \"{self._title}\" to Suwayomi?\n\n"
                f"Chapters: {numbers_text([ch.number for ch in picks])}\n"
                f"From: {lookup.source_name} ({', '.join(groups)})\n"
                f"Filed into: {target_dir}\n\n"
                "Suwayomi downloads them; MangaList files each one under its naming scheme at the next check.")

    def send_selected(self) -> bool:
        if self.send_blocker() is not None:
            return False
        lookup, picks, target = self.lookup, self.picks(), self.chosen_target_dir()
        assert lookup is not None and lookup.match is not None and target
        if not self._confirm(self, self.confirmation_text(picks, target)):
            return False
        sid, backend, match = self._series_id, self._backend, lookup.match
        self._set_busy(True)
        set_tone(self.status_label, "")
        self.status_label.setText("Sending to Suwayomi...")
        self._spawn(lambda: backend.send_chapters(sid, match, picks, target, lookup.manga_title, lookup.source_name),
                    self._on_sent, self._show_error)
        return True

    def _on_sent(self, outcome) -> None:
        bad = bool(getattr(outcome, "error", None))
        set_tone(self.status_label, "bad" if bad else "ok")
        self.status_label.setText(outcome.text() + ("" if bad else " - they show under In progress."))
        self.sent.emit(list(outcome.records))
        if self._series_id is not None:
            self._look_up()

    def _show_error(self, message: str) -> None:
        set_tone(self.status_label, "bad")
        self.status_label.setText(message)

    def stop(self) -> None:
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()
