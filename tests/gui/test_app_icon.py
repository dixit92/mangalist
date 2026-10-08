"""MangaList's icon: MangaPixer's icon rotated 90 degrees counter-clockwise with inverted colours (owner, 2026-10-08)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402

from mangalist.gui.app_icon import ICON_SVG, build_app_icon, render_app_icon  # noqa: E402

from .conftest import qapp  # noqa: E402,F401

ICONS = Path(__file__).resolve().parents[2] / "packaging" / "icons"


def _rgb(img, x, y):
    c = QColor(img.pixel(x, y))
    return c.red(), c.green(), c.blue(), QColor.fromRgba(img.pixel(x, y)).alpha()


def test_rendered_icon_is_the_inverted_rotated_mangapixer_icon(qapp):
    img = render_app_icon(256).toImage()
    assert (img.width(), img.height()) == (256, 256)
    assert _rgb(img, 0, 0)[3] == 0                                   # outside the rounded square: transparent
    assert _rgb(img, 128, 4)[:3] == (204, 204, 204)                  # the background: #333333 inverted
    assert _rgb(img, 230, 180)[:3] == (13, 13, 13)                   # the plain panel (bottom, from the right): #f2f2f2 inverted
    assert _rgb(img, 230, 50)[:3] == (9, 6, 4)                       # the device panel (top): #f6f9fb inverted
    assert "rotate(-90 400 400)" in ICON_SVG                         # counter-clockwise (the owner's pick)


def test_every_window_icon_size_is_present(qapp):
    sizes = {s.width() for s in build_app_icon().availableSizes()}
    assert {16, 32, 48, 256} <= sizes


def test_the_committed_svg_is_the_master():
    # packaging/make_icon.py --repo rewrites it from ICON_SVG; a change to one must reach the other.
    assert (ICONS / "mangalist-icon.svg").read_text(encoding="utf-8") == ICON_SVG
    png = (ICONS / "mangalist-512.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png[16:20], "big") == 512
