"""Up / down arrows for the styled spin boxes.

A stylesheet that gives ``QSpinBox`` a border and padding (both of MangaList's sheets do) also takes over its buttons, and
Qt then draws no arrows at all (owner, 2026-10-10, on Settings > Download sources: "Arrow buttons don't show"). The two
arrows are painted once at runtime into the user's cache folder - no image files to package on every platform - and
:func:`spin_rules` gives the rules that use them. If they cannot be written, no rules are added (the spin box then looks
as before: no arrows, still typeable and wheel-able).
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

_log = logging.getLogger(__name__)

ARROW_PX = 16                       # drawn at 16 px, shown at 8 px: sharp on high-DPI screens
_made: Dict[str, Optional[Dict[str, str]]] = {}


def _cache_dir() -> Optional[str]:
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    if not base:
        import tempfile

        base = os.path.join(tempfile.gettempdir(), f"mangalist-{os.getuid() if hasattr(os, 'getuid') else 'user'}")
    return os.path.join(base, "ui")


def _paint(path: str, up: bool, color: str) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

    img = QImage(ARROW_PX, ARROW_PX, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    top, bottom, left, right, mid = 4.0, 12.0, 2.0, 14.0, ARROW_PX / 2
    pts = ([QPointF(left, bottom), QPointF(right, bottom), QPointF(mid, top)] if up
           else [QPointF(left, top), QPointF(right, top), QPointF(mid, bottom)])
    p.drawPolygon(QPolygonF(pts))
    p.end()
    if not img.save(path, "PNG"):
        raise OSError(f"could not write {path}")


def arrow_files(color: str = "#5a5a57", disabled: str = "#b4b4b0") -> Optional[Dict[str, str]]:
    """{"up", "down", "up_off", "down_off"} -> file paths ('/' separators, as stylesheets want); None when they cannot
    be written. Painted once per process and colour pair."""
    key = f"{color}|{disabled}"
    if key in _made:
        return _made[key]
    out: Optional[Dict[str, str]] = None
    try:
        folder = _cache_dir()
        os.makedirs(folder, exist_ok=True)
        tag = (color + disabled).replace("#", "")
        out = {}
        for name, up, col in (("up", True, color), ("down", False, color), ("up_off", True, disabled),
                              ("down_off", False, disabled)):
            path = os.path.join(folder, f"spin-{name}-{tag}.png")
            _paint(path, up, col)
            out[name] = path.replace("\\", "/")
    except Exception as exc:  # noqa: BLE001 - a missing arrow must never stop the window from opening
        _log.warning("Spin box arrows not drawn (%s); the boxes keep working without them", exc)
        out = None
    _made[key] = out
    return out


def spin_rules(line: str = "#c4c4bf", hover: str = "#f3f3f1", color: str = "#5a5a57") -> str:
    """Stylesheet rules giving ``QSpinBox`` / ``QDoubleSpinBox`` visible up / down buttons ("" when the arrows could not
    be drawn)."""
    files = arrow_files(color)
    if not files:
        return ""
    boxes = ("QSpinBox", "QDoubleSpinBox")

    def sel(part: str) -> str:
        return ", ".join(f"{b}{part}" for b in boxes)

    return f"""
{sel("")} {{ padding-left: 10px; padding-right: 26px; }}
{sel("::up-button")} {{ subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: none;
    border-left: 1px solid {line}; border-top-right-radius: 6px; background: transparent; }}
{sel("::down-button")} {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border: none;
    border-left: 1px solid {line}; border-bottom-right-radius: 6px; background: transparent; }}
{sel("::up-button:hover")}, {sel("::down-button:hover")} {{ background: {hover}; }}
{sel("::up-arrow")} {{ image: url("{files['up']}"); width: 9px; height: 9px; }}
{sel("::down-arrow")} {{ image: url("{files['down']}"); width: 9px; height: 9px; }}
{sel("::up-arrow:disabled")}, {sel("::up-arrow:off")} {{ image: url("{files['up_off']}"); }}
{sel("::down-arrow:disabled")}, {sel("::down-arrow:off")} {{ image: url("{files['down_off']}"); }}
"""
