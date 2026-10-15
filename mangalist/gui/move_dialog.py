"""The Move to library confirmation (:mod:`mangalist.library_move`), with where the folders land.

Owner, 2026-10-15 (question 61, "Where a folder lands"): "OK default, user settable location". The series go to the top
of the target library under their own names unless the owner picks one of the folders at its top in **Into** (an
existing franchise parent, say - the series then become its subseries). Each choice is checked again at once
(:func:`~mangalist.library_move.check_move` with ``into``): the text below names what moves where and what is refused
there (a name already taken in that folder, a folder with subseries of its own, ...), and **Move** says how many series
go - disabled when none can. Cancel is the default button, as in every confirmation that changes the library.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from .. import library_move
from .download_widgets import hbox, label

_log = logging.getLogger(__name__)

INTO_TIP = ("Where the series folders go: the top of the library (the default), or one of the folders at its top - "
            "e.g. a franchise parent, where each becomes one of its subseries")


def top_text(target) -> str:
    return f"{target.name} (the top of the library)"


class MoveDialog(QDialog):
    """Asks before a move to library. After :meth:`ask`, :attr:`check` is the check for the folder chosen in Into."""

    def __init__(self, db, check: library_move.MoveCheck, *, show_modes: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("moveDialog")
        self._db = db
        self._show_modes = show_modes
        self.check = check
        self.setMinimumWidth(560)
        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        self.into_combo = QComboBox()
        self.into_combo.setObjectName("moveInto")
        self.into_combo.setToolTip(INTO_TIP)
        self.into_combo.setAccessibleName("Move into")
        self.into_combo.addItem(top_text(check.target), "")
        for name in library_move.place_choices(db, check.target.id):
            self.into_combo.addItem(name, name)
        if check.into:
            index = self.into_combo.findData(check.into)
            self.into_combo.setCurrentIndex(index if index >= 0 else 0)
        self.into_combo.currentIndexChanged.connect(self._on_into)
        row = hbox(label("Into", "muted"), self.into_combo, None, spacing=8)
        row.setStretchFactor(self.into_combo, 1)
        outer.addLayout(row)
        self.text_label = QLabel()
        self.text_label.setObjectName("moveText")
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.text_label, 1)
        self.buttons = QDialogButtonBox()
        self.btn_move: QPushButton = self.buttons.addButton("Move", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_cancel: QPushButton = self.buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_cancel.setDefault(True)
        self.btn_move.setAutoDefault(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._show()

    # --- what it shows ---------------------------------------------------------------------------------------

    def text(self) -> str:
        return self.text_label.text()

    def move_label(self) -> str:
        return self.btn_move.text()

    def into(self) -> str:
        return self.into_combo.currentData() or ""

    def choose(self, into: str) -> bool:
        """Pick *into* ('' = the top of the library) as the owner would in Into; False when it is not offered."""
        index = self.into_combo.findData(into or "")
        if index < 0:
            return False
        self.into_combo.setCurrentIndex(index)
        return True

    def _show(self) -> None:
        check = self.check
        if check.items:
            title, text, move = library_move.confirm_text(check, show_modes=self._show_modes)
        else:
            title = f"Move to {check.target.name}?"
            lines = library_move.refused_lines(check) or [f"{t} - already in {check.target.name}" for t in check.already]
            text = f"Nothing can move into {check.destination}:\n\n" + "\n".join(f"    {line}" for line in lines[:12])
            move = "Move"
        self.setWindowTitle(title)
        self.text_label.setText(text)
        self.btn_move.setText(move)
        self.btn_move.setEnabled(bool(check.items))

    def _on_into(self, _index: int) -> None:
        into = self.into()
        try:
            self.check = library_move.check_move(self._db, self.check.asked, self.check.target.id, into=into)
        except Exception as exc:  # noqa: BLE001 - said in the dialog; nothing can move then
            _log.warning("Move to library: the check for %r failed", into, exc_info=True)
            self.check = library_move.MoveCheck(target=self.check.target, into=into, asked=self.check.asked,
                                                refused=[(t, str(exc)) for _f, t in self.check.asked])
        self._show()

    # --- asking ------------------------------------------------------------------------------------------------

    def ask(self, answer: Optional[Callable[["MoveDialog"], bool]] = None) -> Optional[library_move.MoveCheck]:
        """Show it (or let *answer* - the tests - answer for the owner); the check to move, None for Cancel or when
        nothing can move into the folder chosen."""
        ok = answer(self) if answer is not None else self.exec() == QDialog.DialogCode.Accepted
        return self.check if ok and self.check.items else None


__all__ = ["MoveDialog", "INTO_TIP", "top_text"]
