"""The Download tab's extension update line (owner, 2026-10-22: "Update notice"): "2 extension updates" with an **Open
Extensions** button, shown while "Built-in extensions" is chosen and the last update check
(:mod:`mangalist.downloads.extension_updates`) has something to tell - updates waiting, extensions Auto-update installed
(for a week), or updates that failed. Nothing to tell: hidden.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from . import theme
from .download_style import set_tone
from .download_widgets import button, label
from .palette import current

#: The pseudo-section of the Settings dialog that opens Connected services with the Extensions page open.
SECTION_EXTENSIONS = "extensions"


class ExtensionUpdatesBar(QFrame):
    open_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("extensionUpdatesBar")
        theme.bind_sheet(self, lambda: f"QFrame#extensionUpdatesBar {{ background: {current().surface}; border: none; "
                                       f"border-top: 1px solid {current().border}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(10)
        self.text_label = label("", wrap=True)
        self.open_button = button("Open Extensions", tip="Settings > Connected services > Built-in extensions > "
                                                         "Extensions: update them there (Update / Update all)")
        self.open_button.setAccessibleName("Open Extensions")
        self.open_button.clicked.connect(self.open_requested)
        lay.addWidget(self.text_label, 1)
        lay.addWidget(self.open_button)
        self.check = None
        self.setVisible(False)

    def set_check(self, check) -> None:
        """Show the last check's result (:class:`~mangalist.downloads.extension_updates.UpdateCheck`, or None)."""
        self.check = check
        text = check.short_text() if check is not None else ""
        if text and check is not None and check.pending:
            text += ": " + ", ".join(f"{p.name} {p.new_version}" for p in check.pending[:4]) + (
                ", ..." if len(check.pending) > 4 else "")
        elif text and check is not None and check.updated:
            text += ": " + ", ".join(f"{u.name} {u.new_version}" for u in check.updated[:4])
        self.text_label.setText(text)
        warn = check is not None and bool(check.pending or check.errors)
        set_tone(self.text_label, "warn" if warn else "")
        self.open_button.setProperty("primary", warn)
        self.open_button.style().unpolish(self.open_button)
        self.open_button.style().polish(self.open_button)
        self.setVisible(bool(text))
