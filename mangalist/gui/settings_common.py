"""What the Settings dialog's sections share: the page frame (a heading and a lead line over the body) and the small
parts they are made of - a switch row, a service card, a "later" placeholder.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .download_style import set_prop
from .download_widgets import button, card, checkbox, hbox, label, pill


class SectionPage(QWidget):
    """One section: a heading, a lead line, then ``body`` (a vertical layout the section fills)."""

    roots_changed = Signal()
    mangapixer_changed = Signal()
    downloads_changed = Signal()
    section_requested = Signal(str)             # jump to another section (SECTION_*)

    def __init__(self, title: str, lead: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        head = QVBoxLayout()
        head.setSpacing(4)
        self.title_label = label(title, "h2")
        self.lead_label = label(lead, "lead", wrap=True)
        head.addWidget(self.title_label)
        head.addWidget(self.lead_label)
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body)
        outer.addStretch(1)

    def on_show(self) -> None:
        """The section was brought to the front (the live checks of Connected services start here)."""

    def stop(self) -> None:
        """The dialog closes: abandon background work."""


def clear_layout(layout) -> None:
    """Remove every item of *layout* (nested layouts too) and delete the widgets in them."""
    while layout.count():
        item = layout.takeAt(0)
        widget, child = item.widget(), item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif child is not None:
            clear_layout(child)


def placeholder(text: str) -> QFrame:
    """The dashed box of something that comes later."""
    box = card("dashed")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(16, 12, 16, 12)
    note = label(text, wrap=True)
    lay.addWidget(note)
    box.note = note                                          # type: ignore[attr-defined]
    return box


class ServiceCard(QFrame):
    """A connected service: its name and status, what it does, where it is, what uses it, and its buttons."""

    def __init__(self, name: str, what: str, used_by: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("card", "true")
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.name_label = label(name, "name")
        self.badge = pill("Not set up", "muted")
        col.addLayout(hbox(self.name_label, self.badge, None))
        self.what_label = label(what, wrap=True)
        self.detail_label = label("-", "mono", wrap=True, selectable=True)
        self.used_label = label(f"Used by: {used_by}", "muted", wrap=True)
        self.note_label = label("", wrap=True)
        self.note_label.setVisible(False)
        for w in (self.what_label, self.detail_label, self.used_label, self.note_label):
            col.addWidget(w)
        row.addLayout(col, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        self.btn_secondary = button("Test")
        self.btn_primary = button("Edit", primary=True)
        self.buttons.addWidget(self.btn_secondary)
        self.buttons.addWidget(self.btn_primary)
        wrap = QVBoxLayout()
        wrap.addLayout(self.buttons)
        wrap.addStretch(1)
        row.addLayout(wrap)

    def set_status(self, text: str, kind: str) -> None:
        self.badge.setText(text)
        set_prop(self.badge, "badge", kind)

    def set_note(self, text: str, tone: str = "warn") -> None:
        self.note_label.setText(text)
        self.note_label.setVisible(bool(text))
        set_prop(self.note_label, "tone", tone)


def switch(text: str, checked: bool, *, enabled: bool = True, tip: str = "") -> QCheckBox:
    return checkbox(text, checked, enabled=enabled, tip=tip)


def back_link(text: str) -> QPushButton:
    out = button(f"‹ {text}", link=True)
    out.setCursor(Qt.CursorShape.PointingHandCursor)
    return out
