"""A confirmation whose long list of files never pushes its buttons off the screen.

Owner, 2026-10-24: the "Merge into this folder?" question listed every moved, renamed and deleted file (27 of them for
one series) and grew taller than the screen, so Merge and Cancel could not be reached (the container's desktop is about
1080 px tall; the owner also uses an iPad). The short *summary* - counts, the deletion, what happens to the folders -
stays in view; the *details* (every file) sit in a scrollable box of bounded height; the buttons are always visible.
The whole dialog is also capped at a share of the screen it is on.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

SCREEN_SHARE = 0.8                      # the dialog is never taller than this share of the screen's usable height
DETAILS_MIN = 90                        # px: the details box keeps at least this much, however small the screen
DETAILS_MAX = 360                       # px: and never more, however tall the screen (long lists scroll)


def available_height(widget: Optional[QWidget] = None) -> int:
    """The usable height of the screen *widget* is on (the primary screen without one); 0 when unknown."""
    screen = None
    if widget is not None and widget.window() is not None:
        handle = widget.window().windowHandle()
        screen = handle.screen() if handle is not None else None
    screen = screen or QGuiApplication.primaryScreen()
    return screen.availableGeometry().height() if screen is not None else 0


class DetailsConfirm(QDialog):
    """Summary on top, the files in a scroll box under it, the accept and Cancel buttons at the bottom."""

    def __init__(self, title: str, summary: str, details: str, accept_label: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("detailsConfirm")
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        self.summary_label = QLabel(summary)
        self.summary_label.setObjectName("detailsSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.summary_label)
        self.details_box = QPlainTextEdit()
        self.details_box.setObjectName("detailsList")
        self.details_box.setReadOnly(True)
        self.details_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.details_box.setPlainText(details)
        self.details_box.setAccessibleName("Details: every file")
        self.details_box.setMinimumHeight(DETAILS_MIN)
        self.details_box.setMaximumHeight(DETAILS_MAX)
        self.details_box.setVisible(bool(details))
        outer.addWidget(self.details_box, 1)
        self.buttons = QDialogButtonBox()
        self.btn_accept = self.buttons.addButton(accept_label, QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_cancel = self.buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_cancel.setDefault(True)
        self.btn_accept.setAutoDefault(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.apply_height_cap()

    def apply_height_cap(self) -> None:
        """Cap the dialog at SCREEN_SHARE of the screen; the details box takes the squeeze (it scrolls)."""
        height = available_height(self)
        if height > 0:
            self.setMaximumHeight(max(int(height * SCREEN_SHARE), 240))

    def showEvent(self, event) -> None:
        self.apply_height_cap()
        super().showEvent(event)
        # the summary is wrapped to the final width only now: grow/shrink to the content within the cap
        self.adjustSize()


def ask_with_details(parent: Optional[QWidget], title: str, summary: str, details: str, accept_label: str) -> bool:
    dialog = DetailsConfirm(title, summary, details, accept_label, parent)
    try:
        return dialog.exec() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


__all__ = ["DetailsConfirm", "ask_with_details", "available_height", "SCREEN_SHARE"]
