"""The palette's tokens as ``QColor``\\ s, made once per palette: a painted cell asks for its colours on every paint (UI
performance research, stage 1 #7 - a ``QColor`` per ``data()`` call and per chip is cheap alone, not 20,000 times a
scroll). The palette module stays Qt-free; this is its Qt side."""

from __future__ import annotations

from typing import Dict, Tuple

from PySide6.QtGui import QColor

from .palette import current

_cache: Dict[Tuple[str, str], QColor] = {}


def qcolor(token: str) -> QColor:
    """The current palette's colour of *token* (``text``, ``muted``, ``accent_fill`` ...). Shared: do not change it."""
    pal = current()
    key = (pal.name, token)
    color = _cache.get(key)
    if color is None:
        color = _cache[key] = QColor(getattr(pal, token))
    return color


def hex_color(value: str) -> QColor:
    """A ``#rrggbb`` colour (a chip's), made once. Shared: do not change it."""
    key = ("#", value)
    color = _cache.get(key)
    if color is None:
        color = _cache[key] = QColor(value)
    return color
