"""The shell's look (offscreen Qt): the bundled IBM Plex fonts load from the package, the theme applies Fusion, the palette,
the font and a stylesheet Qt parses without a warning; the painted chips, tabs and the chips' flow layout."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QFontDatabase, QFontInfo  # noqa: E402
from PySide6.QtWidgets import QPushButton, QWidget  # noqa: E402

from mangalist.gui import theme  # noqa: E402
from mangalist.gui.chips import ChipButton, FlowLayout, TabButton  # noqa: E402

from .conftest import qapp  # noqa: E402,F401


@pytest.fixture
def themed(qapp):
    """The theme on the shared application, undone afterwards (the other tests keep Qt's defaults)."""
    saved = (qapp.style().name(), qapp.palette(), qapp.font(), qapp.styleSheet())
    warnings = []

    def handler(kind, _context, message):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
            warnings.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        loaded = theme.apply_theme(qapp)
        yield qapp, loaded, warnings
    finally:
        qInstallMessageHandler(previous)
        qapp.setStyleSheet(saved[3])
        if saved[0]:
            qapp.setStyle(saved[0])
        qapp.setPalette(saved[1])
        qapp.setFont(saved[2])


def test_the_fonts_ship_with_the_package_and_their_licence():
    names = {p.name for p in theme.FONTS_DIR.iterdir()}
    assert set(theme.FONT_FILES) <= names and "OFL.txt" in names
    assert "SIL Open Font License" in (theme.FONTS_DIR / "OFL.txt").read_text(encoding="utf-8")


def test_apply_theme_loads_plex_and_parses_the_stylesheet(themed):
    app, loaded, warnings = themed
    assert loaded
    assert {"IBM Plex Sans", "IBM Plex Mono"} <= set(QFontDatabase.families())
    assert app.styleSheet() == theme.stylesheet(theme.SANS, theme.MONO)
    assert QFontInfo(app.font()).family() == "IBM Plex Sans" and app.font().pixelSize() == theme.BASE_PX
    assert app.palette().window().color().name() == theme.GROUND
    widgets = []
    for variant in ("primary", "ghost", "icon", "close", "link", "missing"):
        b = QPushButton(variant)
        b.setProperty("variant", variant)
        widgets.append(b)
    widgets += [ChipButton("Complete", "Complete"), TabButton("List")]
    for w in widgets:
        w.ensurePolished()
        w.grab()
    assert not [m for m in warnings if "style" in m.lower() or "parse" in m.lower()], warnings
    for w in widgets:
        w.deleteLater()
    mono = theme.font(12, 500, mono=True)
    assert QFontInfo(mono).family() == "IBM Plex Mono" and QFontInfo(mono).weight() == 500


def test_badge_colours_follow_the_mockup():
    assert theme.badge_colors("Missing volumes") == ("#fdf0e3", "#8a3f00")
    assert theme.badge_colors("Missing chapters") == ("#fbeaea", "#8b1d1d")
    assert theme.badge_colors("Upgrade available") == ("#e8eefb", "#1f4fb8")
    assert theme.badge_colors("Complete") == ("#e7f3ec", "#1d5e36")
    assert theme.badge_colors("Can't tell") == ("#ededea", "#4a4a47")
    assert theme.badge_colors("Up to date") == ("#eef0f2", "#3a3f45")
    assert theme.badge_colors("Wanted - official available") == theme.badge_colors("Wanted")
    assert theme.badge_colors(None) == theme.badge_colors("Something new")


def test_chip_and_tab_counts(qapp):
    chip = ChipButton("Missing volumes", "Missing volumes")
    width = chip.sizeHint().width()
    chip.set_count(14)
    assert chip.count == 14 and chip.sizeHint().width() > width and chip.isCheckable()
    chip.setChecked(True)
    assert not chip.grab().isNull()
    tab = TabButton("Download")
    assert tab.badge is None
    width = tab.sizeHint().width()
    tab.set_badge(31)
    assert tab.badge == 31 and tab.sizeHint().width() > width
    assert not tab.grab().isNull()
    for w in (chip, tab):
        w.deleteLater()


def test_flow_layout_wraps_onto_more_lines_when_narrow(qapp):
    box = QWidget()
    flow = FlowLayout(box, spacing=6)
    buttons = []
    for i in range(5):
        b = QPushButton(f"Chip {i}")
        b.setFixedSize(100, 30)
        flow.addWidget(b)
        buttons.append(b)
    assert flow.hasHeightForWidth()
    assert flow.heightForWidth(1000) == 30                 # one line
    assert flow.heightForWidth(250) == 30 * 3 + 6 * 2       # two per line
    buttons[4].hide()
    assert flow.heightForWidth(250) == 30 * 2 + 6           # a hidden chip takes no place
    box.resize(250, 200)
    flow.setGeometry(box.rect())
    assert buttons[2].geometry().topLeft().y() == 36 and buttons[1].geometry().left() == 106
    box.deleteLater()
