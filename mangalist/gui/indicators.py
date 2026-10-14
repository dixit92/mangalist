"""Check boxes and radio buttons that show on a dark ground.

Fusion draws an indicator's edge from the window colour made darker (``outline = window.darker(140)``), so on the Dark
palette's near-black window the boxes and circles all but vanish (seen in the Dark renders, 2026-10-13). In Dark the
theme adds :func:`indicator_rules`: a box / circle edged in the palette's strong control colour, filled with the accent
when checked, with a tick and a dot painted once at runtime into the user's cache folder (like the spin box arrows,
:mod:`.spin_arrows` - no image files to package). Light keeps Fusion's own indicators: its look must not change.
:func:`paint_radio` draws the Download tab's "pick one release" radio (a delegate, not a QRadioButton) the same way.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

from .palette import Palette, current
from .spin_arrows import _cache_dir

_log = logging.getLogger(__name__)

PX = 28                             # drawn at 28 px, shown at 14 px: sharp on high-DPI screens
_made: Dict[str, Optional[Dict[str, str]]] = {}


def _paint(path: str, kind: str, color: str) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    img = QImage(PX, PX, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if kind == "tick":
        p.setPen(QPen(QColor(color), 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        stroke = QPainterPath(QPointF(7, 14.5))
        stroke.lineTo(12, 19.5)
        stroke.lineTo(21, 9)
        p.drawPath(stroke)
    else:                           # the radio's dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(PX / 2, PX / 2), 6.5, 6.5)
    p.end()
    if not img.save(path, "PNG"):
        raise OSError(f"could not write {path}")


def indicator_files(tick: str, dot: str) -> Optional[Dict[str, str]]:
    """{"tick", "dot"} -> file paths ('/' separators); None when they cannot be written. Once per colour pair."""
    key = f"{tick}|{dot}"
    if key in _made:
        return _made[key]
    out: Optional[Dict[str, str]] = None
    try:
        folder = _cache_dir()
        os.makedirs(folder, exist_ok=True)
        out = {}
        for name, color in (("tick", tick), ("dot", dot)):
            path = os.path.join(folder, f"indicator-{name}-{color.replace('#', '')}.png")
            _paint(path, name, color)
            out[name] = path.replace("\\", "/")
    except Exception as exc:  # noqa: BLE001 - a missing tick must never stop the window from opening
        _log.warning("Check box marks not drawn (%s); Fusion's own are used", exc)
        out = None
    _made[key] = out
    return out


def indicator_rules(pal: Optional[Palette] = None) -> str:
    """Stylesheet rules for check box / radio indicators in *pal* ("" for a light palette, or when the marks could
    not be drawn)."""
    p = pal or current()
    if not p.dark:
        return ""
    files = indicator_files(p.accent_text, p.accent)
    if not files:
        return ""
    box = "QCheckBox::indicator, QTreeView::indicator, QListView::indicator, QTableView::indicator"

    def sel(state: str, selector: str = box) -> str:
        return ", ".join(f"{s.strip()}{state}" for s in selector.split(","))

    return f"""
{box} {{ width: 14px; height: 14px; border: 1px solid {p.control_strong}; border-radius: 3px;
    background: {p.surface}; }}
{sel(":hover")} {{ border-color: {p.accent}; }}
{sel(":checked")} {{ background: {p.accent_fill}; border-color: {p.accent_fill}; image: url("{files['tick']}"); }}
{sel(":indeterminate")} {{ background: {p.accent_tint}; border-color: {p.accent}; }}
{sel(":disabled")} {{ border-color: {p.control}; background: {p.surface_subtle}; }}
{sel(":checked:disabled")} {{ background: {p.control}; border-color: {p.control}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {p.control_strong}; border-radius: 8px;
    background: {p.surface}; }}
QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QRadioButton::indicator:checked {{ border-color: {p.accent}; image: url("{files['dot']}"); }}
QRadioButton::indicator:disabled {{ border-color: {p.control}; background: {p.surface_subtle}; }}
"""


def paint_radio(painter, rect, on: bool, pal: Optional[Palette] = None) -> None:
    """A radio indicator in *rect* (a QRect), edged and dotted in *pal*'s colours."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen

    p = pal or current()
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor(p.accent if on else p.control_strong), 1))
    painter.setBrush(QColor(p.surface))
    painter.drawEllipse(r)
    if on:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.accent))
        d = r.width() * 0.25
        painter.drawEllipse(r.adjusted(d, d, -d, -d))
    painter.restore()
