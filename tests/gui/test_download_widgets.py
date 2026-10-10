"""The shared cell painter's text fitting and chips (offscreen Qt)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter  # noqa: E402

from mangalist.gui import download_widgets as dw  # noqa: E402

from .conftest import qapp  # noqa: E402,F401

TEXTS = ("Releases ready", "Sent", "Searching...", "Filed v19-v20", "No releases", "Downloading v36")


@pytest.mark.parametrize("size", [7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0])
def test_a_text_is_never_cut_at_its_own_width(qapp, size):
    """The owner's screen showed "Releases rea..." and "Se...": the rounded-down width cut the text it measured."""
    font = QFont("IBM Plex Sans")
    font.setPointSizeF(size)
    fm = QFontMetricsF(font)
    for text in TEXTS:
        assert dw.fit_text(fm, text, dw.text_width(fm, text)) == (text, dw.text_width(fm, text))
        shown, width = dw.fit_text(fm, text, 12)
        assert shown != text and width == 12                            # too narrow: elided, and no wider


def _paint(font, width, chips):
    image = QImage(400, 30, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    used = dw.paint_chips(painter, font, QRect(400 - width, 0, width, 30), chips)
    painter.end()
    return image, used


def test_chips_are_drawn_right_aligned_and_the_last_that_does_not_fit_is_elided(qapp):
    font = QFont()
    font.setPointSizeF(9)
    chips = [("Seeding v09-v18", "ok"), ("Releases ready", "ready")]
    fm = QFontMetricsF(font)
    whole = sum(dw.text_width(fm, t) + 2 * dw.CHIP_PAD_X for t, _k in chips) + dw.CHIP_GAP
    image, used = _paint(font, 400, chips)
    assert used == whole
    assert image.pixelColor(400 - used + 3, 15) != QColor("#ffffff")   # painted from the right edge leftwards
    assert image.pixelColor(400 - used - 3, 15) == QColor("#ffffff")
    assert _paint(font, 90, chips)[1] == 90                             # the first chip elided into the room
    assert _paint(font, 20, chips)[1] == 0                              # no room: nothing drawn
    assert set(dw.CHIP_COLORS) >= {"run", "ok", "bad", "done", "muted", "ready"}
