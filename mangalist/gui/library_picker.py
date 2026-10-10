"""The Library picker (top bar): a combo box that also draws its dropdown chevron. The theme styles every combo's
drop-down area with no border and no arrow image (:mod:`.theme`), which leaves the picker looking like a second
search box; the chevron is painted here, like the chips paint their own counts (:mod:`.chips`)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox

from . import theme

_CHEVRON_RIGHT = 17       # px from the right edge to the chevron's middle
_CHEVRON_HALF_WIDTH = 4
_CHEVRON_HEIGHT = 4


class LibraryPicker(QComboBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(theme.MUTED if self.isEnabled() else theme.DISABLED), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x, y = self.width() - _CHEVRON_RIGHT, self.height() / 2 - _CHEVRON_HEIGHT / 2
        painter.drawPolyline([QPointF(x - _CHEVRON_HALF_WIDTH, y), QPointF(x, y + _CHEVRON_HEIGHT),
                              QPointF(x + _CHEVRON_HALF_WIDTH, y)])
        painter.end()
