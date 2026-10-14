"""Dark mode (offscreen Qt): Settings > Appearance stores the choice and switches at once; a switch re-colours a painted
State badge, a stylesheet-styled widget, a rich-text span, an item's brush and the model's row tints; Follow the system
with nothing said by the platform is Light. Made-up data only."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QStandardItem, QStandardItemModel  # noqa: E402
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QListWidgetItem, QStyleOptionViewItem, QVBoxLayout, QWidget  # noqa: E402,E501

from mangalist import store  # noqa: E402
from mangalist.gui import download_style, palette, theme  # noqa: E402
from mangalist.gui.list_delegates import StateBadgeDelegate  # noqa: E402
from mangalist.gui.palette import DARK, LIGHT  # noqa: E402
from mangalist.gui.settings_appearance import AppearancePage  # noqa: E402
from mangalist.gui.table_model import STATE_ROLE  # noqa: E402

from .conftest import qapp  # noqa: E402,F401


@pytest.fixture
def themed(qapp, monkeypatch):
    """The theme on the shared application, undone afterwards (Light, Qt's defaults for the other tests). Widgets
    earlier tests let go of are deleted first: an application-wide style change restyles every live widget."""
    monkeypatch.delenv("DARK_MODE", raising=False)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    saved = (qapp.style().name(), qapp.palette(), qapp.font(), qapp.styleSheet())
    try:
        theme.apply_theme(qapp, "light")
        yield qapp
    finally:
        theme._ask_platform(qapp, "light")                   # give the platform its own say back
        palette.set_current(LIGHT)
        theme._appearance = None
        qapp.setStyleSheet(saved[3])
        if saved[0]:
            qapp.setStyle(saved[0])
        qapp.setPalette(saved[1])
        qapp.setFont(saved[2])


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


def _pixel(widget: QWidget, x: int, y: int) -> str:
    return widget.grab().toImage().pixelColor(x, y).name()


def _badge_pixel(state_value: str) -> str:
    """Paint the List's State cell for *state_value* and read the badge's fill (left of its text)."""
    delegate = StateBadgeDelegate()
    model = QStandardItemModel(1, 1)
    item = QStandardItem("x")
    item.setData(SimpleNamespace(state=SimpleNamespace(value=state_value), reasons=[], flags=[]), STATE_ROLE)
    model.setItem(0, 0, item)
    img = QImage(220, 50, QImage.Format.Format_ARGB32)
    img.fill(QColor("#808080"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 220, 50)
    painter = QPainter(img)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    return img.pixelColor(12 + 3, 25).name()           # CELL_PAD + 3 px into the badge, mid-height


def test_a_switch_recolours_a_painted_badge(themed):
    assert _badge_pixel("Complete") == LIGHT.ok_bg
    theme.apply_theme(themed, "dark")
    assert palette.current() is DARK
    assert _badge_pixel("Complete") == DARK.ok_bg
    assert _badge_pixel("Missing volumes") == DARK.warn_bg
    assert theme.badge_colors("Wanted - official available") == (DARK.wanted_bg, DARK.wanted_text)


def test_a_switch_recolours_stylesheet_styled_widgets_at_once(themed):
    host = QWidget()
    host.setObjectName("downloadTab")
    download_style.apply_style(host)                     # the Download tab's own sheet (bound to the palette)
    card = QFrame()
    card.setProperty("card", "true")
    card.setFixedSize(120, 40)
    QVBoxLayout(host).addWidget(card)
    host.resize(200, 120)
    host.show()
    themed.processEvents()
    assert _pixel(host, 2, 2) == LIGHT.background and _pixel(card, 60, 20) == LIGHT.surface
    theme.set_appearance(themed, "dark")
    themed.processEvents()
    assert _pixel(host, 2, 2) == DARK.background and _pixel(card, 60, 20) == DARK.surface
    assert themed.palette().window().color().name() == DARK.background
    assert DARK.background in themed.styleSheet() and LIGHT.background not in themed.styleSheet()
    theme.set_appearance(themed, "light")
    themed.processEvents()
    assert _pixel(card, 60, 20) == LIGHT.surface
    host.close()
    host.deleteLater()


def test_a_switch_retints_spans_item_brushes_and_runs_hooks(themed):
    label = QLabel(f'Folder <span {theme.color_attr("muted_2")}>12 MB</span>')
    listing = QListWidget()
    item = QListWidgetItem("A missing folder")
    item.setForeground(QColor(LIGHT.error_text))
    listing.addItem(item)
    seen = []
    theme.on_retheme(label, lambda: seen.append(palette.current().name))
    theme.apply_theme(themed, "dark")
    assert DARK.muted_2 in label.text() and LIGHT.muted_2 not in label.text()
    assert listing.item(0).foreground().color().name() == DARK.error_text
    assert seen == ["dark"]
    theme.apply_theme(themed, "light")
    assert LIGHT.muted_2 in label.text() and listing.item(0).foreground().color().name() == LIGHT.error_text
    for w in (label, listing):
        w.deleteLater()


def test_model_row_tints_and_legacy_names_read_the_palette_at_use(themed):
    from mangalist.gui import table_model

    assert table_model.EXAMINED_ROW_BG.name() == LIGHT.ok_bg and theme.INK == LIGHT.text
    theme.apply_theme(themed, "dark")
    assert table_model.EXAMINED_ROW_BG.name() == DARK.ok_bg and theme.INK == DARK.text
    assert download_style.BADGES["warn"] == (DARK.warn_bg, DARK.warn_text)
    with pytest.raises(AttributeError):
        theme.NOT_A_COLOUR  # noqa: B018


def test_follow_the_system_with_nothing_said_is_light(themed, monkeypatch):
    monkeypatch.setattr(theme, "system_scheme", lambda app=None: None)
    theme.apply_theme(themed, "system")
    assert palette.current() is LIGHT and theme.appearance() == "system"
    monkeypatch.setattr(theme, "system_scheme", lambda app=None: "dark")
    theme._system_changed(themed)                        # the platform switched to dark
    assert palette.current() is DARK
    theme.apply_theme(themed, "light")
    theme._system_changed(themed)                        # not followed while the choice is Light
    assert palette.current() is LIGHT


def test_the_offscreen_platform_says_nothing(themed):
    assert theme.system_scheme(themed) in (None, "light")
    theme.apply_theme(themed, "system")
    assert palette.current() is LIGHT


def test_settings_appearance_stores_and_applies(themed, db):
    applied = []
    page = AppearancePage(db, apply=applied.append)
    assert page.choice() == "light" and not applied           # Light until the owner chooses; showing applies nothing
    page.radios["dark"].setChecked(True)
    assert palette.load_appearance(db) == "dark" and applied == ["dark"]
    page.radios["system"].setChecked(True)
    assert db.get_setting(palette.KEY_APPEARANCE) == "system" and applied == ["dark", "system"]
    again = AppearancePage(db, apply=applied.append)
    assert again.choice() == "system"
    for w in (page, again):
        w.deleteLater()


def test_settings_appearance_switches_the_application(themed, db):
    page = AppearancePage(db)
    page.radios["dark"].setChecked(True)
    assert palette.current() is DARK and themed.palette().base().color().name() == DARK.surface
    assert theme.stored_appearance() == "dark"
    page.radios["light"].setChecked(True)
    assert palette.current() is LIGHT
    page.deleteLater()



def test_dark_asks_the_platform_for_dark_frames_and_light_gives_it_back(themed):
    # the offscreen platform ignores the request, so a fake style-hints object records it
    from PySide6.QtCore import Qt

    asked = []
    app = SimpleNamespace(styleHints=lambda: SimpleNamespace(setColorScheme=asked.append))
    theme._ask_platform(app, "light")
    assert asked == []                                   # Light alone never touches the platform (as before)
    theme._ask_platform(app, "dark")
    theme._ask_platform(app, "system")
    theme._ask_platform(app, "light")
    assert asked == [Qt.ColorScheme.Dark, Qt.ColorScheme.Unknown]
