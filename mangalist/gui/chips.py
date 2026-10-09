"""The shell's two painted buttons: a state chip (label + its count, the count dimmer) and a top-bar tab (label + an
optional count pill). A Qt stylesheet cannot style two runs of text in one button, so the stylesheet draws the
button's frame (``chip`` / ``tab`` properties, :mod:`.theme`) and these paint the text themselves."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QPushButton, QSizePolicy, QStyle, QStyleOptionButton

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
