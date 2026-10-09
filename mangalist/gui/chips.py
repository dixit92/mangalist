"""The shell's two painted buttons - a state chip (label + its count, the count dimmer) and a top-bar tab (label + an
optional count pill) - and the flow layout that wraps the chips. A Qt stylesheet cannot style two runs of text in one button, so the stylesheet draws the
button's frame (``chip`` / ``tab`` properties, :mod:`.theme`) and these paint the text themselves."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QLayout, QLayoutItem, QPushButton, QSizePolicy, QStyle, QStyleOptionButton

from . import theme

_CHIP_PAD = 12
_CHIP_GAP = 5
_TAB_PAD = 18
_PILL_GAP = 8


def _frame(button: QPushButton, painter: QPainter) -> None:
    """The stylesheet's frame of *button*, without its text."""
    opt = QStyleOptionButton()
    button.initStyleOption(opt)
    opt.text = ""
    button.style().drawControl(QStyle.ControlElement.CE_PushButton, opt, painter, button)


class ChipButton(QPushButton):
    """A checkable state chip: ``Missing volumes 14``."""

    def __init__(self, label: str, key: Optional[str] = None, parent=None):
        super().__init__(label, parent)
        self.key = key
        self._count: Optional[int] = None
        self.setCheckable(True)
        self.setProperty("chip", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @property
    def count(self) -> Optional[int]:
        return self._count

    def set_count(self, count: Optional[int]) -> None:
        if count != self._count:
            self._count = count
            self.updateGeometry()
            self.update()

    def label(self) -> str:
        return self.text()

    def _fonts(self):
        return theme.font(13, 600 if self.isChecked() else 400), theme.font(13, 400)

    def sizeHint(self) -> QSize:
        main, small = self._fonts()
        bold = theme.font(13, 600)               # measured bold so a chip does not jump when checked
        width = QFontMetrics(bold).horizontalAdvance(self.text()) + 2 * _CHIP_PAD
        if self._count is not None:
            width += _CHIP_GAP + QFontMetrics(small).horizontalAdvance(str(self._count))
        return QSize(width + 2, 32)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        _frame(self, p)
        main, small = self._fonts()
        color = QColor(theme.ACCENT_DARK if self.isChecked() else theme.TEXT_2)
        if not self.isEnabled():
            color = QColor(theme.DISABLED)
        rect = self.rect()
        width = QFontMetrics(main).horizontalAdvance(self.text())
        if self._count is not None:
            width += _CHIP_GAP + QFontMetrics(small).horizontalAdvance(str(self._count))
        x = (rect.width() - width) / 2
        p.setFont(main)
        p.setPen(color)
        label_w = QFontMetrics(main).horizontalAdvance(self.text())
        p.drawText(QRectF(x, 0, label_w + 1, rect.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self._count is not None:
            dim = QColor(color)
            dim.setAlphaF(0.7)
            p.setFont(small)
            p.setPen(dim)
            p.drawText(QRectF(x + label_w + _CHIP_GAP, 0, rect.width(), rect.height()), Qt.AlignmentFlag.AlignVCenter,
                       str(self._count))
        p.end()


class TabButton(QPushButton):
    """A top-bar tab: its label, the accent underline when current, and an optional count pill (``Download 31``)."""

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self._badge: Optional[int] = None
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setProperty("tab", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    @property
    def badge(self) -> Optional[int]:
        return self._badge

    def set_badge(self, count: Optional[int]) -> None:
        """The pill's number; None hides the pill."""
        if count != self._badge:
            self._badge = count
            self.updateGeometry()
            self.update()

    def _pill_width(self) -> int:
        if self._badge is None:
            return 0
        return QFontMetrics(theme.font(12, 600)).horizontalAdvance(str(self._badge)) + 16

    def sizeHint(self) -> QSize:
        width = QFontMetrics(theme.font(theme.BASE_PX, 600)).horizontalAdvance(self.text()) + 2 * _TAB_PAD
        if self._badge is not None:
            width += _PILL_GAP + self._pill_width()
        return QSize(width, 56)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _frame(self, p)
        checked = self.isChecked()
        main = theme.font(theme.BASE_PX, 600 if checked else 500)
        rect = self.rect()
        p.setFont(main)
        p.setPen(QColor(theme.INK if checked else theme.MUTED))
        label_w = QFontMetrics(main).horizontalAdvance(self.text())
        x = _TAB_PAD
        p.drawText(QRectF(x, 0, label_w + 1, rect.height() - 3), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self._badge is not None:
            pill = theme.font(12, 600)
            w = self._pill_width()
            h = 20
            top = (rect.height() - 3 - h) / 2
            box = QRectF(x + label_w + _PILL_GAP, top, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT_TINT))
            p.drawRoundedRect(box, 10, 10)
            p.setFont(pill)
            p.setPen(QColor(theme.ACCENT))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(self._badge))
        p.end()


class FlowLayout(QLayout):
    """Lays its items out left to right and wraps them onto the next line when the width runs out (the mockup's
    chips: ``flex-wrap: wrap``)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        width = sum(i.sizeHint().width() for i in self._visible()) + self._spacing * max(len(self._visible()) - 1, 0)
        height = max((i.sizeHint().height() for i in self._visible()), default=0)
        return QSize(width, height)

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        return size

    def _visible(self) -> List[QLayoutItem]:
        return [i for i in self._items if i.widget() is None or not i.widget().isHidden()]

    def _arrange(self, rect: QRect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._visible():
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x = rect.x()
                y += line + self._spacing
                line = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y()
