"""MangaList's icon: MangaPixer's icon rotated 90 degrees counter-clockwise, with every colour inverted (owner, 2026-10-08).

The shapes are MangaPixer's own (its ``assets/Square.svg``, the visible "skew" layer), copied unchanged; the colours are
inverted exactly: ``#333333`` -> ``#cccccc``, ``#f2f2f2`` -> ``#0d0d0d``, ``#f6f9fb`` -> ``#090604``, the ``#000000``
outlines -> ``#ffffff``. :data:`ICON_SVG` is the master: the window icon, the installers' icons
(``packaging/make_icon.py``) and the container / Unraid PNG (``packaging/icons/``) are all rendered from it.

Qt's SVG renderer implements SVG Tiny and ignores ``clipPath``, so :func:`render_app_icon` clips to the rounded square
itself (a browser applies the file's own clip).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: The SVG viewBox is 800 x 800 with a rounded square of corner radius 16.59 (MangaPixer's geometry).
_VIEW = 800.0
_RADIUS = 16.59

ICON_SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" version="1.1" viewBox="0 0 800 800">
  <defs>
    <clipPath id="square">
      <rect x="-.4" y="-2.17" width="803" height="803" rx="16.59" ry="16.59"/>
    </clipPath>
  </defs>
  <g clip-path="url(#square)" stroke="#ffffff" stroke-miterlimit="10">
    <g transform="rotate(-90 400 400)">
      <rect fill="#cccccc" stroke-width="4.96" x="-1.5" y="-.5" width="803" height="803" rx="16.59" ry="16.59"/>
      <path fill="#0d0d0d" stroke-width="5.09" d="M98.46,809.5l242.08,82.98c11.85,4.06,21.46-4.93,21.46-20.1V181.5c0-15.16-9.61-30.75-21.46-34.81L98.46,63.72c-11.85-4.06-21.46,4.93-21.46,20.1v690.88c0,15.16,9.61,30.75,21.46,34.81Z"/>
      <path fill="#090604" stroke-width="5.09" d="M458.46,932.9l242.08,82.98c11.85,4.06,21.46-4.93,21.46-20.1V304.9c0-15.16-9.61-30.75-21.46-34.81l-242.08-82.98c-11.85-4.06-21.46,4.93-21.46,20.1v690.88c0,15.16,9.61,30.75,21.46,34.81Z"/>
      <path fill="#cccccc" stroke-width="5.09" d="M482.91,549.84l194.17,66.56c7.68,2.63,13.91-3.2,13.91-13.03v-275.25c0-9.83-6.23-19.93-13.91-22.57l-194.17-66.56c-7.68-2.63-13.91,3.2-13.91,13.03v275.25c0,9.83,6.23,19.93,13.91,22.57Z"/>
    </g>
  </g>
</svg>
"""


def render_app_icon(size: int) -> QPixmap:
    """The app icon at *size* px, clipped to its rounded square, on a transparent background."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    scale = size / _VIEW
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), _RADIUS * scale, _RADIUS * scale)
    p.setClipPath(clip)
    QSvgRenderer(QByteArray(ICON_SVG.encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def build_app_icon() -> QIcon:
    """The multi-resolution window / taskbar icon."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(render_app_icon(size))
    return icon
