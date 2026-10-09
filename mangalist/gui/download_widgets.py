"""Small building blocks the Download tab and the Settings dialog share: labelled controls carrying the properties the
stylesheet selects on (:mod:`.download_style`), and the two-line table cell (a name over a smaller grey line).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .download_style import FONT_MONO, set_prop

ROLE_SUB = Qt.ItemDataRole.UserRole + 1          # the second line of a two-line cell
ROLE_ASIDE = Qt.ItemDataRole.UserRole + 2        # text at the right edge of a cell


def label(text: str = "", role: Optional[str] = None, *, wrap: bool = False, selectable: bool = False) -> QLabel:
    out = QLabel(text)
    if role:
        out.setProperty("role", role)
    if wrap:
        out.setWordWrap(True)
    if selectable:
        out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return out


def pill(text: str, kind: str = "muted") -> QLabel:
    """A small rounded status label (``badge`` = ok / run / done / bad / warn / muted)."""
    out = QLabel(text)
    out.setProperty("badge", kind)
    out.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return out


def set_pill(out: QLabel, text: str, kind: str) -> None:
    out.setText(text)
    set_prop(out, "badge", kind)


def button(text: str, *, primary: bool = False, link: bool = False, tip: str = "") -> QPushButton:
    out = QPushButton(text)
    if primary:
        out.setProperty("primary", True)
    if link:
        out.setProperty("link", True)
        out.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        out.setToolTip(tip)
    return out


def checkbox(text: str, checked: bool = False, *, enabled: bool = True, tip: str = "") -> QCheckBox:
    out = QCheckBox(text)
    out.setChecked(checked)
    out.setEnabled(enabled)
    if tip:
        out.setToolTip(tip)
    return out


def card(kind: str = "true") -> QFrame:
    """A bordered box (``true``), a dashed placeholder (``dashed``) or a greyed-out one (``quiet``)."""
    out = QFrame()
    out.setProperty("card", kind)
    return out


def hbox(*widgets: QWidget, spacing: int = 10, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        else:
            lay.addWidget(w)
    return lay


def vbox(*widgets: QWidget, spacing: int = 4, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    return lay


def flat_table(name: str, columns, *, select_rows: bool = True) -> QTableWidget:
    """A read-only table without a grid or row numbers, the look of the mockup's lists."""
    table = QTableWidget(0, len(columns))
    table.setObjectName(name)
    table.setHorizontalHeaderLabels([c.upper() for c in columns])      # QSS cannot upper-case
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection if select_rows
                           else QAbstractItemView.SelectionMode.NoSelection)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setFrameShape(QFrame.Shape.NoFrame)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    return table


class TwoLineDelegate(QStyledItemDelegate):
    """A cell with the text on top (medium weight) and ``ROLE_SUB`` under it (smaller, grey; monospace when
    ``mono_sub``), as the mockup's "To get" rows and release names."""

    def __init__(self, parent=None, *, mono_sub: bool = False, row_height: int = 46, left_pad: int = 0):
        super().__init__(parent)
        self._mono_sub = mono_sub
        self._height = row_height
        self._left_pad = left_pad

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        base = super().sizeHint(option, index)
        return QSize(base.width(), max(base.height(), self._height))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title, sub = opt.text, index.data(ROLE_SUB) or ""
        opt.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else None
        if style is None:
            return super().paint(painter, option, index)
        if opt.state & QStyle.StateFlag.State_Selected:      # the highlight spans the whole row, the content is padded
            painter.fillRect(option.rect, option.palette.highlight())
            opt.state &= ~QStyle.StateFlag.State_Selected
        opt.rect = opt.rect.adjusted(self._left_pad, 0, 0, 0)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget).adjusted(
            4, 0, -4 - self._left_pad, 0)
        painter.save()
        top = QFont(opt.font)
        top.setWeight(QFont.Weight.Medium)
        sub_font = QFont(opt.font)
        sub_font.setPointSizeF(max(opt.font.pointSizeF() - 1.5, 7.0))
        if self._mono_sub:
            sub_font.setFamilies([f.strip("' ") for f in FONT_MONO.split(",")])
        aside = index.data(ROLE_ASIDE) or ""
        if aside:
            aside_font = QFont(opt.font)
            aside_font.setPointSizeF(max(opt.font.pointSizeF() - 1.5, 7.0))
            aside_fm = QFontMetrics(aside_font)
            aside_w = min(aside_fm.horizontalAdvance(aside), max(rect.width() // 2, 60))
            painter.setFont(aside_font)
            painter.setPen(QColor("#5a5a57"))
            painter.drawText(QRect(rect.right() - aside_w, rect.top(), aside_w, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             aside_fm.elidedText(aside, Qt.TextElideMode.ElideRight, aside_w))
            rect = rect.adjusted(0, 0, -(aside_w + 8), 0)
        top_h = QFontMetrics(top).height()
        sub_h = QFontMetrics(sub_font).height()
        y = rect.top() + (rect.height() - top_h - (sub_h if sub else 0)) // 2
        painter.setFont(top)
        painter.setPen(QColor("#161616"))
        painter.drawText(QRect(rect.left(), y, rect.width(), top_h), Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(top).elidedText(title, Qt.TextElideMode.ElideRight, rect.width()))
        if sub:
            painter.setFont(sub_font)
            painter.setPen(QColor("#6b6b67"))
            painter.drawText(QRect(rect.left(), y + top_h, rect.width(), sub_h), Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(sub_font).elidedText(sub, Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()


class RadioDelegate(QStyledItemDelegate):
    """Draws a radio button, on for the selected row - the mockup's "pick one release" column."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else None
        if style is None:
            return super().paint(painter, option, index)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        radio = QStyleOptionButton()
        radio.state = QStyle.StateFlag.State_Enabled | (
            QStyle.StateFlag.State_On if option.state & QStyle.StateFlag.State_Selected else QStyle.StateFlag.State_Off)
        size = style.pixelMetric(QStyle.PixelMetric.PM_ExclusiveIndicatorWidth, radio, widget)
        radio.rect = QRect(option.rect.left() + 10, option.rect.center().y() - size // 2, size, size)
        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorRadioButton, radio, painter, widget)
