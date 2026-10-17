"""Unbundled Chapters: the "first chapter to get" question (owner, 2026-10-15, question 14 - "Unmapped newest volume.
... Ask once for 'the first chapter to get' instead?" - "Yes").

Where MangaPixer's volume list does not say which chapter the newest held volume of a series ends with, the setting
cannot tell where the chapters after the volumes start. MangaList asks once - when the setting is switched on for that
one series, or later from the row menu's "First chapter to get…" - and remembers the answer for the series
(:meth:`mangalist.unbundled.UnbundledFolders.set_first_chapter`, with the newest held volume it was given for). The
question shows what the list does say (the newest mapped volume and where it ends) and an estimate to start from
(:func:`mangalist.states.unbundled_suggestion`). "Ask later" stores nothing: the series is compared as without the
setting and shows Needs attention until it has an answer.

For the main window::

    text = prompt_first_chapter(self, self._unbundled, entry, state)   # the status line
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout

from ..states import UnbundledAsk
from .list_text import UNBUNDLED_FIRST_MENU, unbundled_first_status
from .spin_arrows import pad_spin

_log = logging.getLogger(__name__)

TITLE = "Unbundled chapters: first chapter to get"
MAX_CHAPTER = 9999


def question_text(title: str, ask: UnbundledAsk) -> str:
    """The question's text (rich text) for the series *title*."""
    known = (f" It maps volumes up to {html.escape(ask.mapped_volume)}, which ends with chapter "
             f"{html.escape(ask.mapped_end)}." if ask.mapped_volume and ask.mapped_end else
             " It maps none of the volumes." if ask.not_held else " It maps none of the volumes you hold.")
    newest = ("the newest volume out in English, which the folder does not hold yet" if ask.not_held
              else "the newest volume you hold")
    after = (f"the first one after volume {html.escape(ask.volume)}" if ask.not_held
             else "the first one after your volumes")
    again = ""
    if ask.stale:
        again = (f"<br><br>You set the first chapter for volume {html.escape(ask.given_for or '')}; volume "
                 f"{html.escape(ask.volume)} {'came out in English' if ask.not_held else 'arrived'} since, so it may hold "
                 "chapters after that one.")
    return (f"<b>{html.escape(title)}</b>: MangaPixer's volume list does not say which chapter volume "
            f"{html.escape(ask.volume)} - {newest} - ends with.{known}{again}<br><br>"
            f"Which is the <b>first chapter to get</b>, {after}? MangaList asks once and "
            f"remembers it for this series; change it later from the row menu ({html.escape(UNBUNDLED_FIRST_MENU)}).")


class FirstChapterDialog(QDialog):
    """Asks for one series' first chapter to get. After ``exec()``, :attr:`answer` is the chapter (an int) or None
    (Ask later)."""

    def __init__(self, title: str, ask: UnbundledAsk, parent=None):
        super().__init__(parent)
        self.answer: Optional[int] = None
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        self.question = QLabel(question_text(title, ask))
        self.question.setObjectName("unbundledFirstQuestion")
        self.question.setWordWrap(True)
        self.question.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.question)

        row = QHBoxLayout()
        row.addWidget(QLabel("First chapter to get:"))
        self.spin = pad_spin(QSpinBox())
        self.spin.setObjectName("unbundledFirstChapter")
        self.spin.setRange(1, MAX_CHAPTER)
        start = ask.current if ask.current is not None else str(ask.suggestion)
        try:
            self.spin.setValue(max(1, min(MAX_CHAPTER, int(float(start)))))
        except ValueError:
            self.spin.setValue(max(1, min(MAX_CHAPTER, int(ask.suggestion))))
        row.addWidget(self.spin)
        row.addStretch(1)
        layout.addLayout(row)
        estimate = (f"Suggested: {ask.suggestion} (an estimate from the chapters per volume of volumes 1-"
                    f"{ask.mapped_volume}; check it against your newest volume)" if ask.mapped_volume else
                    f"Suggested: {ask.suggestion} (a rough estimate from the latest chapter and the original edition's "
                    "volume count - the list maps none of your volumes; check it against your newest volume)"
                    if ask.suggestion > 1 else "No estimate: the list maps none of your volumes.")
        if ask.current is not None:
            estimate = f"Now: chapter {ask.current}. " + estimate
        self.hint = QLabel(estimate)
        self.hint.setObjectName("unbundledFirstHint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox()
        self.use_button = buttons.addButton("Use this chapter", QDialogButtonBox.ButtonRole.AcceptRole)
        self.later_button = buttons.addButton("Ask later", QDialogButtonBox.ButtonRole.RejectRole)
        self.use_button.setDefault(True)
        buttons.accepted.connect(self._use)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _use(self) -> None:
        self.answer = int(self.spin.value())
        self.accept()


def ask_first_chapter(parent, title: str, ask: UnbundledAsk) -> Optional[int]:
    """Show the question; the chapter given, or None (Ask later). Tests replace this function."""
    dialog = FirstChapterDialog(title, ask, parent)
    dialog.exec()
    return dialog.answer


def prompt_first_chapter(parent, folders, entry, state) -> str:
    """Ask *entry*'s first chapter to get when its *state* depends on it (:attr:`~mangalist.states.SeriesState.
    unbundled_ask`) and store the answer through *folders* (an :class:`~mangalist.unbundled.UnbundledFolders`); the
    status line. Nothing is asked when the volume list says where the volumes end."""
    ask = getattr(state, "unbundled_ask", None)
    if ask is None:
        return ""
    title = getattr(entry, "title", None) or Path(str(entry.folder)).name
    first = ask_first_chapter(parent, title, ask)
    if first is None:
        return unbundled_first_status(None, ask.volume)
    try:
        stored = folders.set_first_chapter(entry.folder, first, ask.volume)
    except Exception as exc:  # noqa: BLE001 - shown, nothing assumed stored
        _log.warning("Unbundled chapters: the first chapter to get could not be stored", exc_info=True)
        return f"Could not store the first chapter to get: {type(exc).__name__}: {exc}"
    if not stored:
        return "Unbundled chapters: the setting is off for this series (or it is not scanned yet) - nothing stored"
    return unbundled_first_status(first, ask.volume)


__all__ = ["FirstChapterDialog", "TITLE", "ask_first_chapter", "prompt_first_chapter", "question_text"]
