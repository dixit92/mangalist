"""The List tab's table cells as the mockup draws them: the title with the matched series' title under it, the state
as a coloured badge, numbers and gaps in IBM Plex Mono, the English / Kind columns in the secondary ink.

Each delegate lets the style draw the cell first (background, the examined / review tints, the selection, the row rule)
and then paints its text on top, so the model's roles keep working unchanged."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from . import theme
from .table_model import REVIEW_ROLE, STATE_ROLE, SUBTITLE_ROLE, state_text

ROW_HEIGHT = 50                 # two lines of the Title cell with the mockup's 9 px padding
TITLE_PAD = 20                  # the first column's left padding
CELL_PAD = 12


def _draw_cell(option: QStyleOptionViewItem, painter: QPainter, index, delegate: QStyledItemDelegate) -> QStyleOptionViewItem:
    """The cell without its text; returns the option (its rect, state and palette) for the caller's own painting."""
    opt = QStyleOptionViewItem(option)
    delegate.initStyleOption(opt, index)
    opt.text = ""
    style = opt.widget.style() if opt.widget is not None else None
    if style is not None:
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
    return opt


class TitleDelegate(QStyledItemDelegate):
    """The folder's title (medium weight) over the matched series' title (smaller, muted; orange when the match
    needs review)."""

    def paint(self, painter: QPainter, option, index) -> None:
        opt = _draw_cell(option, painter, index, self)
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        review = bool(index.data(REVIEW_ROLE))
        rect = QRectF(opt.rect).adjusted(TITLE_PAD, 0, -CELL_PAD, 0)
        main = theme.font(13, 500)
        small = theme.font(12, 400)
        painter.save()
        main_h = QFontMetrics(main).height()
        small_h = QFontMetrics(small).height() if subtitle else 0
        top = rect.top() + (rect.height() - main_h - small_h) / 2
        painter.setFont(main)
        painter.setPen(QColor(theme.INK))
        painter.drawText(QRectF(rect.left(), top, rect.width(), main_h), Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(main).elidedText(title, Qt.TextElideMode.ElideRight, int(rect.width())))
        if subtitle:
            painter.setFont(small)
            painter.setPen(QColor("#8a3f00" if review else theme.MUTED_2))
            painter.drawText(QRectF(rect.left(), top + main_h, rect.width(), small_h), Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(small).elidedText(subtitle, Qt.TextElideMode.ElideRight, int(rect.width())))
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        return QSize(QFontMetrics(theme.font(13, 500)).horizontalAdvance(title) + TITLE_PAD + CELL_PAD, ROW_HEIGHT)


class StateBadgeDelegate(QStyledItemDelegate):
    """The state as a rounded badge in its colours, its flags (``Upcoming``, ``Needs attention``) after it."""

    def paint(self, painter: QPainter, option, index) -> None:
        opt = _draw_cell(option, painter, index, self)
        st = index.data(STATE_ROLE)
        if st is None:
            return
        label = st.reasons[0] if st.state.value == "Not a series" and st.reasons else st.state.value
        flags = "  ·  ".join(st.flags)
        bg, fg = theme.badge_colors(st.state.value)
        badge_font = theme.font(12, 600)
        fm = QFontMetrics(badge_font)
        rect = QRectF(opt.rect).adjusted(CELL_PAD, 0, -CELL_PAD, 0)
        text_w = fm.horizontalAdvance(label) + 1
        width = min(text_w + 16, rect.width())
        height = fm.height() + 4
        box = QRectF(rect.left(), rect.top() + (rect.height() - height) / 2, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(box, height / 2, height / 2)
        painter.setFont(badge_font)
        painter.setPen(QColor(fg))
        shown = label if text_w + 16 <= box.width() else fm.elidedText(label, Qt.TextElideMode.ElideRight,
                                                                         int(box.width() - 16))
        painter.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, shown)
        if flags:
            small = theme.font(12, 400)
            painter.setFont(small)
            painter.setPen(QColor(theme.MUTED_2))
            rest = QRectF(box.right() + 8, rect.top(), rect.right() - box.right() - 8, rect.height())
            if rest.width() > 20:
                painter.drawText(rest, Qt.AlignmentFlag.AlignVCenter,
                                 QFontMetrics(small).elidedText(flags, Qt.TextElideMode.ElideRight, int(rest.width())))
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        st = index.data(STATE_ROLE)
        text = state_text(st) if st is not None else ""
        return QSize(QFontMetrics(theme.font(12, 600)).horizontalAdvance(text) + 2 * CELL_PAD + 24, ROW_HEIGHT)


class MonoDelegate(QStyledItemDelegate):
    """Numbers and gaps in IBM Plex Mono (the mockup's Gaps and Files)."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.font = theme.font(12, 400, mono=True)


class SecondaryDelegate(QStyledItemDelegate):
    """Secondary columns (English, Kind) in the second ink."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.palette.setColor(option.palette.ColorRole.Text, QColor(theme.TEXT_2))
