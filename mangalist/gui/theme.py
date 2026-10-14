"""MangaList's look (UI cycle, owner-approved mockup 2026-10-08): Fusion, one Qt stylesheet with the mockup's colours,
radii and controls, and the bundled IBM Plex Sans / Mono (OFL, ``mangalist/assets/fonts``) - in a Light and a Dark
palette (:mod:`.palette`; owner, 2026-10-13: "we need a dark mode option").

:func:`apply_theme` is called once at start (``__main__``) with the stored Appearance, before the window is built, and
again when Settings > Appearance changes it: every open window switches at once. Every window and dialog takes the look
from the application; a widget asks for a variant with a dynamic property, set before it is shown::

    button.setProperty("variant", "primary")    # the blue call to action ("Get the missing volumes")
    button.setProperty("variant", "ghost")      # borderless (the details panel's close button)
    label.setProperty("role", "muted")          # secondary text; "section" = small upper-case headings
    label.setProperty("mono", True)             # IBM Plex Mono (numbers, file names)

Colours come from the palette in force (:func:`~.palette.current`), read when they are used: painted cells read it at
paint time, models at ``data()`` time. The old constant names (``theme.INK``, ``theme.ACCENT``, ...) still work and read
the current palette too (module ``__getattr__``), so ``theme.MUTED_2`` at paint time follows a switch. What a widget
builds once - its own stylesheet, a rich-text colour, an item's brush - is re-made by the switch:

- :func:`bind_sheet` sets a widget's own stylesheet from a function and runs it again on every switch;
- :func:`on_retheme` runs a function on every switch (an icon drawn in the ink colour);
- :func:`color_attr` colours a rich-text ``<span>`` so the switch can re-tint it;
- the brushes of list / tree / table widget items in a palette colour are swapped for the new palette's.

The fonts load from the package folder, so the same code works from source, in the PyInstaller build (the spec's
``datas`` carry the folder) and in the container (which copies ``mangalist/``). A missing or broken font file falls back
to the system font; nothing fails.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from . import palette as _palette
from .palette import APPEARANCE_SYSTEM, Palette, current, resolve, set_current

_log = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
SANS = "IBM Plex Sans"
MONO = "IBM Plex Mono"
FONT_FILES = (
    "IBMPlexSans-Regular.ttf", "IBMPlexSans-Medium.ttf", "IBMPlexSans-SemiBold.ttf", "IBMPlexSans-Bold.ttf",
    "IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf",
)
BASE_PX = 14                    # the mockup's body text

# --- the colours ----------------------------------------------------------------------------------------------

#: The mockup-era constant names and the palette token each reads (``theme.INK`` is ``current().text``).
LEGACY_NAMES: Dict[str, str] = {
    "GROUND": "background", "PANEL": "surface", "PANEL_SUBTLE": "surface_subtle", "INK": "text", "TEXT_2": "text_2",
    "MUTED": "muted", "MUTED_2": "muted_2", "DISABLED": "disabled", "BORDER": "border", "BORDER_SOFT": "border_soft",
    "ROW_RULE": "row_rule", "CONTROL": "control", "HOVER": "hover", "PRESSED": "pressed", "ACCENT": "accent",
    "ACCENT_DARK": "accent_hover", "ACCENT_TINT": "accent_tint", "SELECTED": "selected",
}


def __getattr__(name: str) -> str:
    token = LEGACY_NAMES.get(name)
    if token is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(current(), token)


# State badges: the badge kind of each state. The mockup's six; the Wanted states (empty folders, not in the mockup)
# take a violet of the same lightness.
_STATE_BADGES: Dict[str, str] = {
    "Missing volumes": "warn",
    "Missing chapters": "bad",
    "Upgrade available": "info",
    "Complete": "ok",
    "Can't tell": "neutral",
    "Not a series": "neutral",
    "Up to date": "quiet",
}


def badge_kind(state: Optional[str]) -> str:
    if state is None:
        return "quiet"
    if state.startswith("Wanted"):
        return "wanted"
    return _STATE_BADGES.get(state, "quiet")


def badge_colors(state: Optional[str], pal: Optional[Palette] = None) -> Tuple[str, str]:
    """(background, text) of the badge for a state's value (``State.value``), in *pal* (the current palette)."""
    return (pal or current()).badge(badge_kind(state))


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


def stylesheet(sans: str = SANS, mono: str = MONO, pal: Optional[Palette] = None) -> str:
    """The application stylesheet. Fonts come from the application font (:func:`apply_theme`); only the selectors
    that need another size, weight or the mono family set one here (a stylesheet font beats ``setFont``). Colours: *pal*,
    else the current palette."""
    p = pal or current()
    return f"""
QMainWindow, QDialog {{ background: {p.background}; }}
QToolTip {{ background: {p.tooltip_bg}; color: {p.tooltip_text}; border: none; padding: 6px 8px; }}

QLabel[role="muted"] {{ color: {p.muted_2}; }}
QLabel[role="status"] {{ color: {p.muted}; font-size: 13px; }}
QLabel[role="section"] {{ color: {p.muted}; font-size: 12px; font-weight: 600; }}
QLabel[role="title"] {{ font-size: 18px; font-weight: 700; }}
QLabel[role="brand"] {{ font-size: 17px; font-weight: 700; }}
QLabel[mono="true"] {{ font-family: "{mono}"; font-size: 12px; color: {p.text_2}; }}
QLabel a {{ color: {p.accent}; }}

/* buttons */
QPushButton, QToolButton {{
    min-height: 34px; padding: 0 16px; border: 1px solid {p.control}; border-radius: 6px;
    background: {p.surface}; color: {p.text}; font-weight: 500;
}}
QToolButton {{ padding: 0 10px; }}
QPushButton:hover, QToolButton:hover {{ background: {p.hover}; }}
QPushButton:pressed, QToolButton:pressed, QPushButton:checked {{ background: {p.pressed}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {p.disabled}; border-color: {p.border}; background: {p.surface_subtle}; }}
QPushButton:focus {{ border-color: {p.accent}; }}
/* a text box's own clear button: not a 34 px button (it sat 9 px low in the box - owner, 2026-10-11) */
QLineEdit QToolButton, QLineEdit QToolButton:hover, QLineEdit QToolButton:pressed {{
    min-height: 0px; min-width: 0px; padding: 0px; border: none; background: transparent;
}}
QPushButton[variant="primary"] {{
    min-height: 38px; background: {p.accent_fill}; border-color: {p.accent_fill}; color: {p.accent_text}; font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {p.accent_fill_hover}; border-color: {p.accent_fill_hover}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.control}; border-color: {p.control}; color: {p.accent_text}; }}
QPushButton[variant="ghost"] {{ border-color: transparent; background: transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {p.pressed}; }}
QPushButton[variant="icon"] {{ min-width: 38px; max-width: 38px; padding: 0; }}
QPushButton[variant="close"] {{ min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0;
    color: {p.text_2}; }}
QPushButton[variant="link"] {{ border: none; background: transparent; color: {p.accent}; padding: 0; min-height: 0;
    text-align: left; font-weight: 400; }}
QPushButton[variant="link"]:hover {{ color: {p.accent_hover}; text-decoration: underline; }}
QPushButton[variant="missing"] {{ border-color: {p.warn_line}; background: {p.warn_bg}; color: {p.warn_text}; }}
QPushButton[variant="missing"]:hover {{ background: {p.warn_hover}; }}
QPushButton[chip="true"] {{ min-height: 30px; max-height: 30px; padding: 0; border-radius: 16px; }}
QPushButton[chip="true"]:checked {{ border-color: {p.accent}; background: {p.accent_tint}; }}
QPushButton[tab="true"] {{ min-height: 0; padding: 0; border: none; border-bottom: 3px solid transparent;
    border-radius: 0; background: transparent; }}
QPushButton[tab="true"]:hover {{ background: transparent; border-bottom-color: {p.border}; }}
QPushButton[tab="true"]:checked {{ background: transparent; border-bottom-color: {p.accent}; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    min-height: 34px; padding: 0 10px; border: 1px solid {p.control}; border-radius: 6px;
    background: {p.surface}; color: {p.text}; selection-background-color: {p.accent_tint}; selection-color: {p.text};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p.accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {p.disabled}; background: {p.surface_subtle}; }}
QLineEdit:read-only {{ background: {p.surface_subtle}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ border: 1px solid {p.border}; background: {p.raised}; selection-background-color: {p.selected};
    selection-color: {p.text}; outline: 0; }}
QPlainTextEdit, QTextEdit {{ border: 1px solid {p.control}; border-radius: 6px; background: {p.surface}; }}
QCheckBox, QRadioButton {{ color: {p.text_2}; spacing: 6px; }}

/* tables, lists, trees */
QTableView, QTreeView, QListView, QTableWidget, QTreeWidget, QListWidget {{
    background: {p.surface}; alternate-background-color: {p.surface_subtle}; border: 1px solid {p.border_soft};
    gridline-color: {p.row_rule}; selection-background-color: {p.selected}; selection-color: {p.text}; outline: 0;
}}
QTableView::item, QTreeView::item, QListView::item {{ padding: 0 8px; min-height: 28px; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{ background: {p.selected}; color: {p.text}; }}
QTableView#seriesTable {{ border: none; font-size: 13px; }}
QTableView#seriesTable::item {{ border-bottom: 1px solid {p.row_rule}; padding: 0 12px; }}
QHeaderView {{ background: {p.surface}; border: none; }}
QHeaderView::section {{
    background: {p.surface}; color: {p.muted}; font-size: 12px; font-weight: 600; padding: 8px 12px; border: none;
    border-bottom: 1px solid {p.border_soft};
}}
QHeaderView::section:hover {{ background: {p.hover}; }}
QHeaderView::section:first {{ padding-left: 20px; }}
QTableCornerButton::section {{ background: {p.surface}; border: none; }}

/* scroll bars: thin, rounded */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.control}; border-radius: 4px; min-height: 32px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ background: {p.control}; border-radius: 4px; min-width: 32px; margin: 3px 2px; }}
QScrollBar::handle:hover {{ background: {p.control_strong}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* menus */
QMenu {{ background: {p.raised}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 26px; border-radius: 4px; color: {p.text}; }}
QMenu::item:selected {{ background: {p.selected}; }}
QMenu::item:disabled {{ color: {p.disabled}; }}
QMenu::separator {{ height: 1px; background: {p.border_soft}; margin: 4px 8px; }}
QMenu::indicator {{ left: 6px; }}

/* groups, tabs, progress, splitter */
QGroupBox {{ border: 1px solid {p.border_soft}; border-radius: 8px; margin-top: 16px; padding: 12px 10px 10px 10px;
    background: {p.surface}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p.muted}; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 6px; background: {p.surface}; top: -1px; }}
QTabBar::tab {{ padding: 8px 16px; border: none; border-bottom: 3px solid transparent; color: {p.muted}; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom-color: {p.accent}; font-weight: 600; }}
QProgressBar {{ border: none; border-radius: 3px; background: {p.pressed}; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}
QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

/* the shell (top bar, filter bar, details, footer) */
QWidget#topBar {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
QWidget#filterBar {{ background: {p.surface_subtle}; border-bottom: 1px solid {p.border_soft}; }}
QWidget#footer {{ background: {p.surface}; border-top: 1px solid {p.border}; }}
QWidget#footer QLabel {{ color: {p.muted}; font-size: 12px; }}
QScrollArea#detailsScroll, QWidget#details {{ background: {p.surface_subtle}; border: none; }}
QWidget#emptyState {{ background: {p.surface}; }}
QLabel#emptyTitle {{ font-size: 18px; font-weight: 600; }}
QFrame#placeholder {{ background: {p.surface}; border: 1px dashed {p.control}; border-radius: 8px; }}
"""


def palette(pal: Optional[Palette] = None):
    """Fusion's palette in *pal*'s colours (the current palette's), for what the stylesheet does not cover: check
    marks, focus frames, dialogs of other lanes."""
    from PySide6.QtGui import QColor, QPalette

    c = pal or current()
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: c.background, QPalette.ColorRole.WindowText: c.text,
        QPalette.ColorRole.Base: c.surface, QPalette.ColorRole.AlternateBase: c.surface_subtle,
        QPalette.ColorRole.Text: c.text, QPalette.ColorRole.Button: c.surface, QPalette.ColorRole.ButtonText: c.text,
        QPalette.ColorRole.BrightText: c.accent_text,
        QPalette.ColorRole.Highlight: c.accent_fill, QPalette.ColorRole.HighlightedText: c.accent_text,
        QPalette.ColorRole.ToolTipBase: c.tooltip_bg, QPalette.ColorRole.ToolTipText: c.tooltip_text,
        QPalette.ColorRole.PlaceholderText: c.muted_2, QPalette.ColorRole.Link: c.accent,
        QPalette.ColorRole.LinkVisited: c.accent_hover, QPalette.ColorRole.Mid: c.control,
        QPalette.ColorRole.Midlight: c.border_soft, QPalette.ColorRole.Dark: c.control_strong,
        QPalette.ColorRole.Light: c.surface, QPalette.ColorRole.Shadow: c.shadow,
    }
    accent_role = getattr(QPalette.ColorRole, "Accent", None)          # Qt 6.6+
    if accent_role is not None:
        roles[accent_role] = c.accent_fill
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.disabled))
    return p


# --- following a switch ---------------------------------------------------------------------------------------

_SHEET_ATTR = "_mangalist_sheet"        # the function that builds a widget's own stylesheet (bind_sheet)
_HOOKS_ATTR = "_mangalist_retheme"      # functions run on a switch (on_retheme)


def bind_sheet(widget, build: Optional[Callable[[], str]]) -> None:
    """Give *widget* the stylesheet ``build()`` now and again after every switch (None: no own stylesheet)."""
    setattr(widget, _SHEET_ATTR, build)
    widget.setStyleSheet(build() if build is not None else "")


def on_retheme(widget, fn: Callable[[], object]) -> None:
    """Run *fn* after every switch while *widget* lives (it re-makes what was drawn in the old colours)."""
    hooks = getattr(widget, _HOOKS_ATTR, None)
    if hooks is None:
        hooks = []
        setattr(widget, _HOOKS_ATTR, hooks)
    hooks.append(fn)


_SPAN_TOKEN = re.compile(r'data-tok="(\w+)" style="color:#[0-9a-fA-F]{6}"')


def color_attr(token: str) -> str:
    """The attributes of a rich-text element in a palette colour: ``<span {color_attr("muted_2")}>...</span>``. Qt
    ignores ``data-tok``; a switch reads it to re-tint the text."""
    return f'data-tok="{token}" style="color:{getattr(current(), token)}"'


def retint_html(text: str, pal: Optional[Palette] = None) -> str:
    """*text* with every :func:`color_attr` colour in *pal*'s (the current palette's) value."""
    p = pal or current()
    return _SPAN_TOKEN.sub(lambda m: f'data-tok="{m.group(1)}" style="color:{getattr(p, m.group(1), "#000000")}"', text)


# An item's brush in one of these colours is swapped for the new palette's colour of the same token (the first token
# that has the old value: text colours before backgrounds for a foreground, and the other way round).
_TEXT_TOKENS = ("text", "text_2", "muted", "muted_2", "accent", "ok_text", "warn_text", "error_text", "bad_text",
                "info_text", "neutral_text", "quiet_text", "wanted_text", "caution_text", "disabled")
_BG_TOKENS = ("surface", "surface_subtle", "background", "selected", "ok_bg", "warn_bg", "bad_bg", "info_bg",
              "neutral_bg", "quiet_bg", "wanted_bg", "busy_row", "accent_tint")


def _swap_table(old: Palette, new: Palette, tokens) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for token in tokens:
        out.setdefault(getattr(old, token).lower(), getattr(new, token))
    return out


def _swap_item_brushes(view, old: Palette, new: Palette) -> None:
    from PySide6.QtGui import QBrush, QColor
    from PySide6.QtWidgets import QListWidget, QTableWidget, QTreeWidget, QTreeWidgetItemIterator

    fg, bg = _swap_table(old, new, _TEXT_TOKENS), _swap_table(old, new, _BG_TOKENS)

    def swap(get, put, table) -> None:
        brush = get()
        if brush.style() == 0:                                  # Qt.NoBrush: the item has no colour of its own
            return
        name = brush.color().name().lower()
        if name in table:
            put(QBrush(QColor(table[name])))

    if isinstance(view, QListWidget):
        for i in range(view.count()):
            it = view.item(i)
            swap(it.foreground, it.setForeground, fg)
            swap(it.background, it.setBackground, bg)
    elif isinstance(view, QTableWidget):
        for r in range(view.rowCount()):
            for c in range(view.columnCount()):
                it = view.item(r, c)
                if it is not None:
                    swap(it.foreground, it.setForeground, fg)
                    swap(it.background, it.setBackground, bg)
    elif isinstance(view, QTreeWidget):
        walker = QTreeWidgetItemIterator(view)
        while walker.value() is not None:
            it = walker.value()
            for c in range(view.columnCount()):
                swap(lambda: it.foreground(c), lambda b: it.setForeground(c, b), fg)
                swap(lambda: it.background(c), lambda b: it.setBackground(c, b), bg)
            walker += 1


def _retheme_widgets(app, old: Palette, new: Palette) -> None:
    """Re-make what the widgets built in the old colours, and repaint everything."""
    from PySide6.QtWidgets import QAbstractItemView, QLabel, QListWidget, QTableWidget, QTreeWidget

    for w in app.allWidgets():
        try:
            build = getattr(w, _SHEET_ATTR, None)
            if build is not None:
                w.setStyleSheet(build())
            if isinstance(w, QLabel) and "data-tok=" in w.text():
                w.setText(retint_html(w.text(), new))
            if old is not new and isinstance(w, (QListWidget, QTableWidget, QTreeWidget)):
                _swap_item_brushes(w, old, new)                 # before the hooks: they colour in the new palette
            for fn in list(getattr(w, _HOOKS_ATTR, None) or ()):
                fn()
            if isinstance(w, QAbstractItemView):
                w.viewport().update()
            w.update()
        except RuntimeError:                                    # deleted while we went through the list
            continue


# --- applying ---------------------------------------------------------------------------------------------------

_appearance: Optional[str] = None       # the choice in force (None until apply_theme is given one)
_watching: bool = False


def system_scheme(app=None) -> Optional[str]:
    """What the platform says: ``"dark"`` / ``"light"``, or None when it says nothing (Qt's ``Unknown`` - the
    container's web GUI, a bare X server, the offscreen platform)."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        hints = (app or QGuiApplication.instance()).styleHints()
        scheme = hints.colorScheme()
    except Exception:  # noqa: BLE001 - Qt before 6.5, or no application: nothing said
        return None
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    if scheme == Qt.ColorScheme.Light:
        return "light"
    return None


def appearance() -> Optional[str]:
    """The Appearance choice in force (``light`` / ``dark`` / ``system``), None before :func:`apply_theme` had one."""
    return _appearance


def _watch_system(app) -> None:
    """Follow the system live: a change of the platform's scheme re-applies while the choice is Follow the system."""
    global _watching
    if _watching:
        return
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: _system_changed(app))
        _watching = True
    except (AttributeError, RuntimeError):                     # Qt before 6.5
        pass


def _system_changed(app) -> None:
    if _appearance == APPEARANCE_SYSTEM and resolve(APPEARANCE_SYSTEM, system_scheme(app)) is not current():
        apply_theme(app, APPEARANCE_SYSTEM)


def apply_theme(app, appearance: Optional[str] = None) -> bool:
    """Fusion, the palette, the bundled font and the stylesheet on *app*. True when the bundled fonts loaded.

    *appearance* (``light`` / ``dark`` / ``system``) picks the palette; None keeps the one in force (Light at start).
    Called again with another choice, it switches every open window at once: the application's palette and
    stylesheet, every :func:`bind_sheet` stylesheet, :func:`on_retheme` hook, :func:`color_attr` span and item brush,
    then a repaint (painted cells read the palette as they paint)."""
    global _appearance
    from PySide6.QtWidgets import QStyleFactory

    old = current()
    if appearance is not None:
        _appearance = appearance
        set_current(resolve(appearance, system_scheme(app)))
        _watch_system(app)
    pal = current()
    loaded = load_fonts()
    if (app.style().name() or "").lower() != "fusion":         # a style change restyles every widget: only once
        style = QStyleFactory.create("Fusion")
        if style is not None:
            app.setStyle(style)
    app.setPalette(palette(pal))
    base = font(BASE_PX)
    if app.font() != base:
        app.setFont(base)
    from .spin_arrows import spin_rules

    app.setStyleSheet(stylesheet(sans_family(), mono_family(), pal)
                      + spin_rules(line=pal.control, color=pal.muted, hover=pal.background, disabled=pal.arrow_off))
    _retheme_widgets(app, old, pal)
    if old is not pal:
        _log.info("Appearance: %s (%s palette)", _appearance, pal.name)
    return loaded


def set_appearance(app, choice: str) -> Palette:
    """Switch to *choice* now (Settings > Appearance); returns the palette in force."""
    apply_theme(app, choice)
    return current()


def stored_appearance() -> str:
    """The Appearance stored in the library database, without creating the data folder (the default when there is
    nothing yet, or it cannot be read)."""
    try:
        from .. import paths, store

        if not paths.db_file().exists():
            return _palette.DEFAULT_APPEARANCE
        return _palette.load_appearance(store.get_store())
    except Exception:  # noqa: BLE001 - the look must not stop the app
        _log.warning("Could not read the Appearance setting; using Light", exc_info=True)
        return _palette.DEFAULT_APPEARANCE
