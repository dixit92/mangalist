"""The "volumes or chapters?" question for one series (Design Decisions C12).

A series whose archives are bare numbers (``01.cbz``, ``Title 01.cbz``) cannot be read as volumes or
chapters from the names. MangaList asks once per series, shows a few of its file names, and remembers
the answer (``series.kind_hint``); later scans parse those names with it.

For the main window::

    answer = ask_series_kind(self, entry)            # "volumes" | "chapters" | None (ask later)
    if answer:
        scanner.answer_series_kind(db, entry, answer)  # store it, re-parse the entry, store its units

or ``ask_series_kind(self, entry, db=db)``, which does both.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

# How many file names the question shows.
MAX_SAMPLES = 8


def sample_names(entry, limit: int = MAX_SAMPLES) -> Tuple[List[str], int]:
    """Up to *limit* file names the question is about: the bare-number files of unknown kind, or (for
    a series already answered) the files the answer decided, else any of its files; plus how many
    there are in all."""
    hits = list(entry.unknown_kind_files)
    if not hits:
        hits = [f for f in entry.inventory_files if f.parsed is not None
                and any("series hint" in n for n in f.parsed.notes)]
    if not hits:
        hits = list(entry.inventory_files)
    folder = Path(entry.folder)
    names = []
    for f in sorted(hits, key=lambda h: str(h.path).lower()):
        try:
            names.append(Path(f.path).relative_to(folder).as_posix())
        except ValueError:
            names.append(Path(f.path).name)
    return names[:limit], len(names)


class KindDialog(QDialog):
    """Asks whether one series' numbered files are volumes or chapters. After ``exec()``, ``answer`` is
    ``"volumes"``, ``"chapters"`` or None (ask later)."""

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.answer: Optional[str] = None
        title = entry.title or Path(entry.folder).name
        self.setWindowTitle("Volumes or chapters?")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        n_unknown = len(entry.unknown_kind_files)
        if n_unknown:
            text = (f"<b>{_esc(title)}</b>: {n_unknown} file(s) are numbered without saying whether they "
                    "are volumes or chapters.<br>MangaList asks once and remembers your answer for this "
                    "series.")
        else:
            current = f" (now: <b>{_esc(entry.kind_hint)}</b>)" if entry.kind_hint else ""
            text = (f"<b>{_esc(title)}</b>: are this series' numbered files volumes or chapters?{current}")
        self.question = QLabel(text)
        self.question.setWordWrap(True)
        self.question.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.question)

        names, total = sample_names(entry)
        self.sample_list = QListWidget()
        self.sample_list.addItems(names)
        if total > len(names):
            self.sample_list.addItem(f"... and {total - len(names)} more")
        self.sample_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.sample_list.setMaximumHeight(22 * (min(len(names), MAX_SAMPLES) + 2))
        layout.addWidget(self.sample_list)

        buttons = QDialogButtonBox()
        self.volumes_button = QPushButton("Volumes")
        self.chapters_button = QPushButton("Chapters")
        self.later_button = QPushButton("Ask later")
        buttons.addButton(self.volumes_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.chapters_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.later_button, QDialogButtonBox.ButtonRole.RejectRole)
        self.volumes_button.clicked.connect(lambda: self.choose("volumes"))
        self.chapters_button.clicked.connect(lambda: self.choose("chapters"))
        self.later_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        if entry.kind_hint == "chapters":
            self.chapters_button.setDefault(True)
        else:
            self.volumes_button.setDefault(True)

    def choose(self, kind: Optional[str]) -> None:
        """Answer (``"volumes"`` / ``"chapters"``) and close; None = ask later."""
        self.answer = kind
        if kind is None:
            self.reject()
        else:
            self.accept()


def _esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def ask_series_kind(parent, entry, *, db=None) -> Optional[str]:
    """Ask the "volumes or chapters?" question for *entry* (a scanner ``MangaEntry``); returns
    ``"volumes"``, ``"chapters"`` or None (the owner chose to answer later). With *db*, an answer is
    also recorded (:func:`mangalist.scanner.answer_series_kind`: stored on the series, the entry
    re-parsed in place, its units stored)."""
    dlg = KindDialog(entry, parent)
    dlg.exec()
    answer = dlg.answer
    if answer and db is not None:
        from ..scanner import answer_series_kind

        answer_series_kind(db, entry, answer)
    return answer


__all__ = ["KindDialog", "MAX_SAMPLES", "ask_series_kind", "sample_names"]
