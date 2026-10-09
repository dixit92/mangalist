"""The List tab's details panel (mockup: the right-hand aside): the selected series' title and match, its state as a
badge, gaps, what the folder holds, the English edition, MangaPixer's cross-check, the download status, "Get the missing
volumes" (to the Download tab), the official sources and the folder's files.

Collapsible (owner, 2026-10-08): the close button asks the window to hide it (:attr:`DetailPanel.close_requested`)."""

from __future__ import annotations

import html
from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..classifier import _human  # internal helper, fine to reuse
from ..knowledge import OfficialLink, SeriesKnowledge
from ..models import MangaEntry
from ..states import ATTENTION_LABELS, SeriesState
from . import theme
from .list_text import english_text, holds_text, match_text
from .links import open_link

MAX_SAMPLE_FILES = 30
LABEL_WIDTH = 96
EMPTY_TEXT = "Select a series to see its details"


def _label(text: str = "", *, role: Optional[str] = None, mono: bool = False, wrap: bool = True,
           selectable: bool = True) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    if mono:
        lbl.setProperty("mono", True)
    lbl.setWordWrap(wrap)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def badge_style(state_value: Optional[str]) -> str:
    bg, fg = theme.badge_colors(state_value)
    return (f"QLabel {{ background: {bg}; color: {fg}; border-radius: 10px; padding: 2px 8px; font-size: 12px; "
            f"font-weight: 600; }}")


class DetailPanel(QWidget):
    close_requested = Signal()
    get_requested = Signal()            # "Get the missing volumes": the window switches to the Download tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("details")
        self.setMinimumWidth(300)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("detailsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("details")
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(18)

        # Header: title, match line, close
        head = QHBoxLayout()
        head.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title_label = _label(EMPTY_TEXT, role="title")
        self._eng_label = _label("", role="muted")      # the matched series' title and where the match comes from
        titles.addWidget(self._title_label)
        titles.addWidget(self._eng_label)
        head.addLayout(titles, 1)
        self.btn_close = QPushButton("×")
        self.btn_close.setProperty("variant", "close")
        self.btn_close.setToolTip("Hide the details (Details in the filter bar shows them again)")
        self.btn_close.clicked.connect(self.close_requested)
        head.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # The facts grid
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnMinimumWidth(0, LABEL_WIDTH)
        grid.setColumnStretch(1, 1)
        self._grid = grid
        self._rows: dict = {}
        self._lbl_state = _label("-", wrap=False)
        self._lbl_state.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        state_cell = QWidget()
        sl = QHBoxLayout(state_cell)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self._lbl_state)
        sl.addStretch(1)
        self._lbl_flags = _label("-")
        self._lbl_gaps = _label("-", mono=True)
        self._lbl_holds = _label("-")
        self._lbl_english = _label("-")
        self._lbl_mangapixer = _label("-")
        self._lbl_download = _label("-")
        self._lbl_kind = _label("-")
        self._path_label = _label("", mono=True)
        for key, caption, widget in (("state", "State", state_cell), ("flags", "Flags", self._lbl_flags),
                                     ("gaps", "Gaps", self._lbl_gaps), ("holds", "Holds", self._lbl_holds),
                                     ("english", "English", self._lbl_english),
                                     ("mangapixer", "MangaPixer", self._lbl_mangapixer),
                                     ("download", "Download", self._lbl_download), ("kind", "Kind", self._lbl_kind),
                                     ("folder", "Folder", self._path_label)):
            self._add_row(key, caption, widget)
        root.addLayout(grid)

        self.btn_get = QPushButton("Get the missing volumes")
        self.btn_get.setProperty("variant", "primary")
        self.btn_get.setToolTip("Open this series in the Download tab")
        self.btn_get.clicked.connect(self.get_requested)
        self.btn_get.setVisible(False)
        root.addWidget(self.btn_get)

        # Official sources (they stay in List: owner, 2026-10-08)
        self._links_box = QWidget()
        lb = QVBoxLayout(self._links_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(8)
        lb.addWidget(_label("OFFICIAL SOURCES", role="section", selectable=False))
        self._links_label = QLabel("-")
        self._links_label.setWordWrap(True)
        self._links_label.setTextFormat(Qt.TextFormat.RichText)
        self._links_label.setOpenExternalLinks(False)
        self._links_label.linkActivated.connect(open_link)       # copied where there is no browser (the container)
        self._links_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        lb.addWidget(self._links_label)
        root.addWidget(self._links_box)

        # Files
        self._files_box = QWidget()
        fb = QVBoxLayout(self._files_box)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(8)
        fb.addWidget(_label("FILES", role="section", selectable=False))
        self._files_label = _label("", mono=True)
        self._files_label.setTextFormat(Qt.TextFormat.RichText)
        fb.addWidget(self._files_label)
        root.addWidget(self._files_box)
        root.addStretch(1)

        self._downloads = False
        self.show_entry(None)

    # ------------------------------------------------------------------

    def _add_row(self, key: str, caption: str, widget: QWidget) -> None:
        row = self._grid.rowCount()
        cap = _label(caption, role="muted", wrap=False, selectable=False)
        cap.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._grid.addWidget(cap, row, 0, Qt.AlignmentFlag.AlignTop)
        self._grid.addWidget(widget, row, 1)
        self._rows[key] = (cap, widget)

    def _show_row(self, key: str, shown: bool) -> None:
        for w in self._rows[key]:
            w.setVisible(shown)

    def set_downloads_enabled(self, enabled: bool) -> None:
        self._downloads = enabled
        self._show_row("download", enabled)

    def set_download(self, text: Optional[str], tooltip: str = "") -> None:
        """The selected series' download status ("Sent", "Filed v03-v05", ...), or None for none."""
        self._lbl_download.setText(text or "-")
        self._lbl_download.setToolTip(tooltip if text else "")

    def set_wanted(self, label: str, tooltip: str = "") -> None:
        """Show the call to action (``Get the missing volumes``) with *label*; empty hides it."""
        self.btn_get.setText(label or "Get the missing volumes")
        self.btn_get.setToolTip(tooltip or "Open this series in the Download tab")
        self.btn_get.setVisible(bool(label))

    def show_entry(self, entry: Optional[MangaEntry], state: Optional[SeriesState] = None,
                   links: Optional[Sequence[OfficialLink]] = None, knowledge: Optional[SeriesKnowledge] = None,
                   held_volumes: Sequence[str] = (), held_chapters: Sequence = ()) -> None:
        has = entry is not None
        for key in self._rows:
            self._show_row(key, has and (key != "download" or self._downloads))
        self._show_state(state if has else None, links if has else None)
        self._links_box.setVisible(has)
        self._files_box.setVisible(has)
        self.btn_close.setVisible(True)
        if entry is None:
            self._title_label.setText(EMPTY_TEXT)
            self._title_label.setProperty("role", "muted")
            self._repolish(self._title_label)
            self._eng_label.setText("")
            self._eng_label.setVisible(False)
            self._path_label.setText("")
            self._files_label.setText("")
            self.set_wanted("")
            return

        self._title_label.setProperty("role", "title")
        self._repolish(self._title_label)
        title = entry.title if entry.parent_folder is None else f"{entry.parent_folder.name} / {entry.title}"
        self._title_label.setText(title)
        match = match_text(knowledge)
        matched = (knowledge.title if knowledge is not None and knowledge.matched else None) or ""
        sub = " · ".join(t for t in (matched, match) if t)
        if entry.english_title and entry.english_title != matched:
            sub = " · ".join(t for t in (entry.english_title, sub) if t)
        self._eng_label.setText(sub)
        self._eng_label.setVisible(bool(sub))
        self._path_label.setText(str(entry.folder))
        self._lbl_holds.setText(holds_text(held_volumes, held_chapters))
        self._lbl_english.setText(english_text(knowledge) or "Not known")
        self._lbl_kind.setText(f"{entry.verdict.value}  (V {entry.vol_pct:.0f}% · C {entry.ch_pct:.0f}% · "
                               f"B {entry.both_pct:.0f}%)")
        median = _human(entry.median_size) if entry.n_files else "-"
        tip = [f"{entry.n_files} files, {entry.n_subfolders} subfolders, median size {median}",
               f"Volume files {entry.n_volume_files}, chapter files {entry.n_chapter_files}, "
               f"ambiguous {entry.n_ambiguous}"]
        if entry.reasons:
            tip += ["", "Why:"] + [f"• {r}" for r in entry.reasons]
        self._lbl_kind.setToolTip("\n".join(tip))

        lines = []
        for hit in entry.files[:MAX_SAMPLE_FILES]:
            name = html.escape(("  " * hit.depth) + hit.path.name)
            lines.append(f'{name} <span style="color:{theme.MUTED_2}">{html.escape(_human(hit.size))}</span>')
        if len(entry.files) > MAX_SAMPLE_FILES:
            lines.append(f'<span style="color:{theme.MUTED_2}">and {len(entry.files) - MAX_SAMPLE_FILES} more</span>')
        self._files_label.setText("<br>".join(lines) or f'<span style="color:{theme.MUTED_2}">No files</span>')

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ------------------------------------------------------------------

    def _show_state(self, state: Optional[SeriesState], links: Optional[Sequence[OfficialLink]]) -> None:
        if state is None:
            for lbl in (self._lbl_state, self._lbl_flags, self._lbl_gaps, self._lbl_mangapixer):
                lbl.setText("-")
                lbl.setToolTip("")
            self._lbl_state.setStyleSheet("")
        else:
            self._lbl_state.setText(state.state.value)
            self._lbl_state.setStyleSheet(badge_style(state.state.value))
            self._lbl_state.setToolTip("\n".join(state.reasons))
            flags: List[str] = []
            if state.complete_with_upgrade:
                flags.append("Upgrade available")
            if state.upcoming:
                vol = f"vol. {state.upcoming_volume} " if state.upcoming_volume else ""
                flags.append(f"Upcoming (English {vol}{state.upcoming_date or 'date not known'})")
            if state.requested:
                flags.append("Requested")
            flags += [f"Needs attention: {ATTENTION_LABELS.get(a, a)}" for a in state.needs_attention]
            if state.rename_pending:
                flags.append("Rename pending")
            self._lbl_flags.setText("\n".join(flags) or "-")
            self._lbl_gaps.setText(state.gaps_text.replace("  ·  ", "\n") or "No gaps")
            self._lbl_gaps.setToolTip(state.gaps_tooltip())
            mp = state.mangapixer_text()
            if mp and state.mangapixer_disagrees:
                mp += " - differs; MangaList's own count is shown"
            self._lbl_mangapixer.setText(mp[len("MangaPixer: "):] if mp else "-")
        self._links_label.setText(links_html(links or []) or "-")
        if state is not None:
            self._show_row("flags", self._lbl_flags.text() != "-")
            self._show_row("mangapixer", self._lbl_mangapixer.text() != "-")


def links_html(links: Sequence[OfficialLink]) -> str:
    """Official sources as rich text: one line each, a link when the page is known."""
    lines = []
    for link in links:
        label = html.escape(link.label)
        if link.url:
            lines.append(f'<a href="{html.escape(link.url, quote=True)}">{label}</a>')
        else:
            lines.append(f'<span style="color:{theme.MUTED_2}">{label} (no page known)</span>')
    return "<br>".join(lines)
