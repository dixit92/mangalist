"""Small building blocks the Download tab and the Settings dialog share: labelled controls carrying the properties the
stylesheet selects on (:mod:`.download_style`), and the two-line table cell (a name over a smaller grey line).
"""

from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QFontMetricsF, QPainter, QPen
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

from .download_style import ACCENT, BADGES, FONT_MONO, set_prop

ROLE_SUB = Qt.ItemDataRole.UserRole + 1          # the second line of a two-line cell
ROLE_ASIDE = Qt.ItemDataRole.UserRole + 2        # text at the right edge of a cell
ROLE_CHIPS = Qt.ItemDataRole.UserRole + 3        # [(text, kind)]: pills at the right edge, drawn instead of the aside

#: Chip colours (background, text, border): the badges of the In progress list, and "ready" - an outlined accent pill
#: for releases found, so it is not mistaken for a download on its way (blue, filled).
CHIP_COLORS = {kind: (bg, fg, bg) for kind, (bg, fg) in BADGES.items()}
CHIP_COLORS["ready"] = ("#ffffff", ACCENT, ACCENT)
CHIP_PAD_X, CHIP_PAD_Y, CHIP_GAP, CHIP_MIN = 8, 3, 6, 40
TITLE_MIN = 120                 # a row with chips keeps at least this much for its title


def text_width(fm: QFontMetricsF, text: str) -> int:
    """The whole pixels *text* needs. ``QFontMetrics.horizontalAdvance`` rounds the fractional width DOWN as often as
    up, and ``elidedText`` then cuts the text at its own width ("Releases rea..." - seen on the owner's Unraid display);
    rounding up, plus a pixel, never does."""
    return math.ceil(fm.horizontalAdvance(text)) + 1


def fit_text(fm: QFontMetricsF, text: str, room: int) -> Tuple[str, int]:
    """(*text*, its width) when it fits in *room*, else the elided text and *room*."""
    need = text_width(fm, text)
    if need <= room:
        return text, need
    return fm.elidedText(text, Qt.TextElideMode.ElideRight, room), room


def paint_chips(painter: QPainter, font: QFont, rect: QRect, chips: Sequence[Tuple[str, str]]) -> int:
    """Draw *chips* right-aligned inside *rect* (the first chip leftmost), no wider than *rect*; a chip that does not
    fit is elided, and the ones after it are left out. Returns the width used."""
    fm = QFontMetricsF(font)
    height = math.ceil(fm.height()) + 2 * CHIP_PAD_Y
    widths, texts, room = [], [], rect.width()
    for text, _kind in chips:
        avail = room - 2 * CHIP_PAD_X - (CHIP_GAP if texts else 0)
        if avail < CHIP_MIN - 2 * CHIP_PAD_X:
            break
        shown, width = fit_text(fm, text, avail)
        texts.append(shown)
        widths.append(width + 2 * CHIP_PAD_X)
        room -= widths[-1] + (CHIP_GAP if len(texts) > 1 else 0)
        if shown != text:
            break
    used = sum(widths) + CHIP_GAP * max(len(widths) - 1, 0)
    x = rect.right() + 1 - used
    y = rect.top() + (rect.height() - height) / 2
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setFont(font)
    for (text, (_t, kind)), width in zip(zip(texts, chips), widths):
        bg, fg, border = CHIP_COLORS.get(kind, CHIP_COLORS["muted"])
        box = QRectF(x + 0.5, y + 0.5, width - 1, height - 1)
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(box, height / 2, height / 2)
        painter.setPen(QColor(fg))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        x += width + CHIP_GAP
    painter.restore()
    return used


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
        chips = index.data(ROLE_CHIPS) or ()
        aside = index.data(ROLE_ASIDE) or ""
        aside_font = QFont(opt.font)
        aside_font.setPointSizeF(max(opt.font.pointSizeF() - 1.5, 7.0))
        room = max(rect.width() // 2, 60)
        if chips:                                       # the chips come first; the title keeps TITLE_MIN and elides
            room = max(rect.width() - TITLE_MIN, room)
            aside_font.setWeight(QFont.Weight.DemiBold)
            used = paint_chips(painter, aside_font, QRect(rect.right() - room + 1, rect.top(), room, rect.height()),
                               chips)
            rect = rect.adjusted(0, 0, -(used + 8), 0)
        elif aside:
            shown, aside_w = fit_text(QFontMetricsF(aside_font), aside, room)
            painter.setFont(aside_font)
            painter.setPen(QColor("#5a5a57"))
            painter.drawText(QRect(rect.right() - aside_w + 1, rect.top(), aside_w, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, shown)
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
