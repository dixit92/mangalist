"""MangaList's look (UI cycle, owner-approved mockup 2026-10-08): Fusion, one Qt stylesheet with the mockup's colours,
radii and controls, and the bundled IBM Plex Sans / Mono (OFL, ``mangalist/assets/fonts``).

:func:`apply_theme` is called once at start (``__main__``), before the window is built. Every window and dialog then
takes the look from the application: no widget needs its own stylesheet. A widget asks for a variant with a dynamic
property, set before it is shown::

    button.setProperty("variant", "primary")    # the blue call to action ("Get the missing volumes")
    button.setProperty("variant", "ghost")      # borderless (the details panel's close button)
    label.setProperty("role", "muted")          # secondary text (#6b6b67); "section" = small upper-case headings
    label.setProperty("mono", True)             # IBM Plex Mono (numbers, file names)

The fonts load from the package folder, so the same code works from source, in the PyInstaller build (the spec's
``datas`` carry the folder) and in the container (which copies ``mangalist/``). A missing or broken font file falls back
to the system font; nothing fails.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

_log = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
SANS = "IBM Plex Sans"
MONO = "IBM Plex Mono"
FONT_FILES = (
    "IBMPlexSans-Regular.ttf", "IBMPlexSans-Medium.ttf", "IBMPlexSans-SemiBold.ttf", "IBMPlexSans-Bold.ttf",
    "IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf",
)
BASE_PX = 14                    # the mockup's body text

# --- the mockup's colours -----------------------------------------------------------------------------------

GROUND = "#f3f3f1"              # the window behind the panels
PANEL = "#ffffff"
PANEL_SUBTLE = "#fafaf8"        # the filter bar, the details panel
INK = "#161616"
TEXT_2 = "#3a3a38"
MUTED = "#5a5a57"               # headings, the top bar's status
MUTED_2 = "#6b6b67"             # secondary lines
DISABLED = "#9a9a96"
BORDER = "#dcdcd8"              # panel edges
BORDER_SOFT = "#e3e3df"         # inner rules
ROW_RULE = "#efefec"            # between table rows
CONTROL = "#c4c4bf"             # buttons and inputs
HOVER = "#f6f6f4"
PRESSED = "#ededea"
ACCENT = "#1f4fb8"
ACCENT_DARK = "#163a87"
ACCENT_TINT = "#e8eefb"
SELECTED = "#eef3fd"            # the picked row

# State badges: (background, text). The mockup's six; the Wanted states (empty folders, not in the mockup) take a
# violet of the same lightness.
_BADGES: Dict[str, Tuple[str, str]] = {
    "Missing volumes": ("#fdf0e3", "#8a3f00"),
    "Missing chapters": ("#fbeaea", "#8b1d1d"),
    "Upgrade available": (ACCENT_TINT, ACCENT),
    "Complete": ("#e7f3ec", "#1d5e36"),
    "Can't tell": ("#ededea", "#4a4a47"),
    "Not a series": ("#ededea", "#4a4a47"),
    "Up to date": ("#eef0f2", "#3a3f45"),
}
_WANTED_BADGE = ("#f1ecfa", "#5a2d91")
_DEFAULT_BADGE = ("#eef0f2", "#3a3f45")


def badge_colors(state: Optional[str]) -> Tuple[str, str]:
    """(background, text) of the badge for a state's value (``State.value``)."""
    if state is None:
        return _DEFAULT_BADGE
    if state.startswith("Wanted"):
        return _WANTED_BADGE
    return _BADGES.get(state, _DEFAULT_BADGE)


# --- fonts ----------------------------------------------------------------------------------------------------

_loaded: Optional[bool] = None


def load_fonts() -> bool:
    """Register the bundled fonts with Qt (once; needs a QGuiApplication). True when both families are usable."""
    global _loaded
    if _loaded is not None:
        return _loaded
    from PySide6.QtGui import QFontDatabase

    for name in FONT_FILES:
        path = FONTS_DIR / name
        if QFontDatabase.addApplicationFont(str(path)) < 0:
            _log.warning("Could not load the font %s; the system font stands in", path)
    families = set(QFontDatabase.families())
    _loaded = SANS in families and MONO in families
    return _loaded


def sans_family() -> str:
    from PySide6.QtGui import QFontDatabase

    return SANS if load_fonts() else QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def mono_family() -> str:
    from PySide6.QtGui import QFontDatabase

    return MONO if load_fonts() else QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def font(px: int = BASE_PX, weight: int = 400, mono: bool = False):
    """A QFont of the theme's family at *px* pixels and CSS *weight* (400 / 500 / 600 / 700)."""
    from PySide6.QtGui import QFont

    f = QFont(mono_family() if mono else sans_family())
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    return f


# --- the stylesheet -------------------------------------------------------------------------------------------


def stylesheet(sans: str = SANS, mono: str = MONO) -> str:
    """The application stylesheet. Fonts come from the application font (:func:`apply_theme`); only the selectors
    that need another size, weight or the mono family set one here (a stylesheet font beats ``setFont``)."""
    return f"""
QMainWindow, QDialog {{ background: {GROUND}; }}
QToolTip {{ background: {INK}; color: #ffffff; border: none; padding: 6px 8px; }}

QLabel[role="muted"] {{ color: {MUTED_2}; }}
QLabel[role="status"] {{ color: {MUTED}; font-size: 13px; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: 12px; font-weight: 600; }}
QLabel[role="title"] {{ font-size: 18px; font-weight: 700; }}
QLabel[role="brand"] {{ font-size: 17px; font-weight: 700; }}
QLabel[mono="true"] {{ font-family: "{mono}"; font-size: 12px; color: {TEXT_2}; }}
QLabel a {{ color: {ACCENT}; }}

/* buttons */
QPushButton, QToolButton {{
    min-height: 34px; padding: 0 16px; border: 1px solid {CONTROL}; border-radius: 6px;
    background: {PANEL}; color: {INK}; font-weight: 500;
}}
QToolButton {{ padding: 0 10px; }}
QPushButton:hover, QToolButton:hover {{ background: {HOVER}; }}
QPushButton:pressed, QToolButton:pressed, QPushButton:checked {{ background: {PRESSED}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {DISABLED}; border-color: {BORDER}; background: {PANEL_SUBTLE}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton[variant="primary"] {{
    min-height: 38px; background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
QPushButton[variant="primary"]:disabled {{ background: {CONTROL}; border-color: {CONTROL}; color: #ffffff; }}
QPushButton[variant="ghost"] {{ border-color: transparent; background: transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {PRESSED}; }}
QPushButton[variant="icon"] {{ min-width: 38px; max-width: 38px; padding: 0; }}
QPushButton[variant="close"] {{ min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0;
    color: {TEXT_2}; }}
QPushButton[variant="link"] {{ border: none; background: transparent; color: {ACCENT}; padding: 0; min-height: 0;
    text-align: left; font-weight: 400; }}
QPushButton[variant="link"]:hover {{ color: {ACCENT_DARK}; text-decoration: underline; }}
QPushButton[variant="missing"] {{ border-color: #e8c9a4; background: #fdf0e3; color: #8a3f00; }}
QPushButton[variant="missing"]:hover {{ background: #fae4cc; }}
QPushButton[chip="true"] {{ min-height: 30px; max-height: 30px; padding: 0; border-radius: 16px; }}
QPushButton[chip="true"]:checked {{ border-color: {ACCENT}; background: {ACCENT_TINT}; }}
QPushButton[tab="true"] {{ min-height: 0; padding: 0; border: none; border-bottom: 3px solid transparent;
    border-radius: 0; background: transparent; }}
QPushButton[tab="true"]:hover {{ background: transparent; border-bottom-color: {BORDER}; }}
QPushButton[tab="true"]:checked {{ background: transparent; border-bottom-color: {ACCENT}; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    min-height: 34px; padding: 0 10px; border: 1px solid {CONTROL}; border-radius: 6px;
    background: {PANEL}; color: {INK}; selection-background-color: {ACCENT_TINT}; selection-color: {INK};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {DISABLED}; background: {PANEL_SUBTLE}; }}
QLineEdit:read-only {{ background: {PANEL_SUBTLE}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ border: 1px solid {BORDER}; background: {PANEL}; selection-background-color: {SELECTED};
    selection-color: {INK}; outline: 0; }}
QPlainTextEdit, QTextEdit {{ border: 1px solid {CONTROL}; border-radius: 6px; background: {PANEL}; }}
QCheckBox, QRadioButton {{ color: {TEXT_2}; spacing: 6px; }}

/* tables, lists, trees */
QTableView, QTreeView, QListView, QTableWidget, QTreeWidget, QListWidget {{
    background: {PANEL}; alternate-background-color: {PANEL_SUBTLE}; border: 1px solid {BORDER_SOFT};
    gridline-color: {ROW_RULE}; selection-background-color: {SELECTED}; selection-color: {INK}; outline: 0;
}}
QTableView::item, QTreeView::item, QListView::item {{ padding: 0 8px; min-height: 28px; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{ background: {SELECTED}; color: {INK}; }}
QTableView#seriesTable {{ border: none; font-size: 13px; }}
QTableView#seriesTable::item {{ border-bottom: 1px solid {ROW_RULE}; padding: 0 12px; }}
QHeaderView {{ background: {PANEL}; border: none; }}
QHeaderView::section {{
    background: {PANEL}; color: {MUTED}; font-size: 12px; font-weight: 600; padding: 8px 12px; border: none;
    border-bottom: 1px solid {BORDER_SOFT};
}}
QHeaderView::section:hover {{ background: {HOVER}; }}
QHeaderView::section:first {{ padding-left: 20px; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

/* scroll bars: thin, rounded */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {CONTROL}; border-radius: 4px; min-height: 32px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ background: {CONTROL}; border-radius: 4px; min-width: 32px; margin: 3px 2px; }}
QScrollBar::handle:hover {{ background: #a9a9a4; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* menus */
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 26px; border-radius: 4px; color: {INK}; }}
QMenu::item:selected {{ background: {SELECTED}; }}
QMenu::item:disabled {{ color: {DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER_SOFT}; margin: 4px 8px; }}
QMenu::indicator {{ left: 6px; }}

/* groups, tabs, progress, splitter */
QGroupBox {{ border: 1px solid {BORDER_SOFT}; border-radius: 8px; margin-top: 16px; padding: 12px 10px 10px 10px;
    background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ padding: 8px 16px; border: none; border-bottom: 3px solid transparent; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom-color: {ACCENT}; font-weight: 600; }}
QProgressBar {{ border: none; border-radius: 3px; background: {PRESSED}; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

/* the shell (top bar, filter bar, details, footer) */
QWidget#topBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QWidget#filterBar {{ background: {PANEL_SUBTLE}; border-bottom: 1px solid {BORDER_SOFT}; }}
QWidget#footer {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
QWidget#footer QLabel {{ color: {MUTED}; font-size: 12px; }}
QScrollArea#detailsScroll, QWidget#details {{ background: {PANEL_SUBTLE}; border: none; }}
QWidget#emptyState {{ background: {PANEL}; }}
QLabel#emptyTitle {{ font-size: 18px; font-weight: 600; }}
QFrame#placeholder {{ background: {PANEL}; border: 1px dashed {CONTROL}; border-radius: 8px; }}
"""


def palette():
    """Fusion's palette in the mockup's colours (what the stylesheet does not cover: check marks, focus frames,
    dialogs of other lanes)."""
    from PySide6.QtGui import QColor, QPalette

    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: GROUND, QPalette.ColorRole.WindowText: INK,
        QPalette.ColorRole.Base: PANEL, QPalette.ColorRole.AlternateBase: PANEL_SUBTLE,
        QPalette.ColorRole.Text: INK, QPalette.ColorRole.Button: PANEL, QPalette.ColorRole.ButtonText: INK,
        QPalette.ColorRole.Highlight: ACCENT, QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: INK, QPalette.ColorRole.ToolTipText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: MUTED_2, QPalette.ColorRole.Link: ACCENT,
        QPalette.ColorRole.LinkVisited: ACCENT_DARK, QPalette.ColorRole.Mid: CONTROL,
        QPalette.ColorRole.Midlight: BORDER_SOFT, QPalette.ColorRole.Dark: "#a9a9a4",
        QPalette.ColorRole.Light: PANEL, QPalette.ColorRole.Shadow: "#8e8e8a",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    return p


def apply_theme(app) -> bool:
    """Fusion, the palette, the bundled font and the stylesheet on *app*. True when the bundled fonts loaded."""
    from PySide6.QtWidgets import QStyleFactory

    loaded = load_fonts()
    style = QStyleFactory.create("Fusion")
    if style is not None:
        app.setStyle(style)
    app.setPalette(palette())
    app.setFont(font(BASE_PX))
    app.setStyleSheet(stylesheet(sans_family(), mono_family()))
    return loaded
