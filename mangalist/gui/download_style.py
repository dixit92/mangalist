"""The look of the Download tab and the Settings dialog, as a Qt stylesheet keyed on ``objectName`` / dynamic
properties, from the approved mockup (``Download.dc.html`` / ``Settings.dc.html``: #f3f3f1 ground, white panels, #161616
ink, #1f4fb8 accent) - in the palette in force (:mod:`.palette`: Light is the mockup, Dark follows MangaPixer).

The widgets carry the hooks, the stylesheet decides the look - so the application theme can restyle them by targeting
the same selectors, and :func:`apply_style` is the only place this module's sheet is attached (drop the call to let the
theme alone style them):

- ``QFrame[nav="true"]`` a Settings section entry (``[current="true"]`` the open one);
- ``QPushButton[primary="true"]`` the blue button; ``QPushButton[link="true"]`` a text-link button
- ``QLabel[badge="ok|run|done|bad|warn|muted"]`` a pill; ``QLabel[tone="ok|bad|warn"]`` a coloured message
- ``QLabel[role="h1|h2|h3|muted|mono|lead"]`` text roles; ``QFrame[card="true"]`` a bordered card
  (``[card="dashed"]`` the "later" placeholders, ``[card="quiet"]`` a greyed-out one)
- ``QFrame#toGetPanel`` / ``#inProgressPanel`` / ``QDialog#settingsDialog`` the big surfaces

:func:`stylesheet` builds the sheet from the current palette, and :func:`apply_style` binds it to its host
(:func:`.theme.bind_sheet`), so a switch of Appearance re-makes it. The old constant names (``INK``, ``BADGES``,
``STYLESHEET``, ...) still read the current palette (module ``__getattr__``).
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from PySide6.QtWidgets import QWidget

from . import theme
from .palette import Palette, current

#: Badge kinds of this sheet and the palette's badge each takes.
BADGE_KINDS = {"run": "info", "ok": "ok", "bad": "bad", "done": "neutral", "muted": "neutral", "warn": "warn"}
TONE_TOKENS = {"ok": "ok_text", "bad": "bad_text", "warn": "warn_text"}

FONT_SANS = "'IBM Plex Sans', 'Segoe UI', sans-serif"
FONT_MONO = "'IBM Plex Mono', 'DejaVu Sans Mono', monospace"


def badges(pal: Optional[Palette] = None) -> Dict[str, Tuple[str, str]]:
    """Badge colours: kind -> (background, text)."""
    p = pal or current()
    return {kind: p.badge(token) for kind, token in BADGE_KINDS.items()}


def tones(pal: Optional[Palette] = None) -> Dict[str, str]:
    p = pal or current()
    return {tone: getattr(p, token) for tone, token in TONE_TOKENS.items()}


_LEGACY = {"INK": "text", "MUTED": "muted", "ACCENT": "accent", "ACCENT_SOFT": "accent_tint", "SELECTED": "selected",
           "GROUND": "background", "LINE": "border", "FIELD_LINE": "control"}


def __getattr__(name: str):
    if name in _LEGACY:
        return getattr(current(), _LEGACY[name])
    if name == "BADGES":
        return badges()
    if name == "TONES":
        return tones()
    if name == "STYLESHEET":
        return stylesheet()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def stylesheet(pal: Optional[Palette] = None) -> str:
    """The sheet in *pal*'s colours (the current palette's)."""
    p = pal or current()
    badge_rules = "\n".join(f'QLabel[badge="{kind}"] {{ background: {bg}; color: {fg}; }}'
                            for kind, (bg, fg) in badges(p).items())
    tone_rules = "\n".join(f'QLabel[tone="{tone}"] {{ color: {color}; }}' for tone, color in tones(p).items())
    return f"""
QWidget#downloadTab, QDialog#settingsDialog {{ background: {p.background}; color: {p.text}; font-family: {FONT_SANS};
                                              font-size: 14px; }}
QDialog#settingsDialog {{ background: {p.surface}; }}
QFrame#toGetPanel, QFrame#inProgressPanel {{ background: {p.surface}; }}
QFrame#toGetPanel {{ border: none; border-right: 1px solid {p.border}; }}
QFrame#inProgressPanel {{ border: none; border-top: 1px solid {p.border}; }}
QFrame#toGetFooter {{ background: {p.surface_subtle}; border: none; border-top: 1px solid {p.border_soft}; }}
QFrame#toGetHeader {{ background: {p.surface}; border: none; border-bottom: 1px solid {p.border_soft}; }}
QFrame#settingsHeader {{ background: {p.surface}; border: none; border-bottom: 1px solid {p.border_soft}; }}
QScrollArea#settingsScroll {{ background: {p.surface}; border: none; }}
QWidget#settingsBody {{ background: {p.surface}; }}
QFrame#settingsNav {{ background: {p.surface_subtle}; border: none; border-right: 1px solid {p.border_soft}; }}

QLabel[role="h1"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {p.muted}; font-size: 13px; }}
QLabel[role="lead"] {{ color: {p.muted}; }}
QLabel[role="mono"] {{ font-family: {FONT_MONO}; color: {p.muted}; font-size: 13px; }}
QLabel[role="name"] {{ font-weight: 700; font-size: 15px; }}
QLabel[role="navtitle"] {{ font-weight: 600; font-size: 14px; background: transparent; }}
QLabel[role="navhint"] {{ font-size: 12px; color: {p.muted_2}; background: transparent; }}
QLabel[badge] {{ border-radius: 8px; padding: 3px 9px; font-size: 12px; font-weight: 600; }}
{badge_rules}
{tone_rules}

QPushButton {{ min-height: 34px; padding: 0 12px; border: 1px solid {p.control}; border-radius: 6px;
               background: {p.surface}; color: {p.text}; }}
QPushButton:hover {{ background: {p.hover}; }}
QPushButton:disabled {{ color: {p.disabled}; background: {p.background}; border-color: {p.border}; }}
QPushButton[primary="true"] {{ border: 1px solid {p.accent_fill}; background: {p.accent_fill}; color: {p.accent_text};
                               font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {p.accent_fill_hover_2}; }}
QPushButton[primary="true"]:disabled {{ background: {p.accent_fill_disabled}; border-color: {p.accent_fill_disabled};
                                        color: {p.accent_text}; }}
QPushButton[link="true"] {{ border: none; background: transparent; color: {p.accent}; min-height: 24px; padding: 0 4px; }}
QPushButton[link="true"]:hover {{ color: {p.accent_hover}; text-decoration: underline; }}
QPushButton[link="true"]:disabled {{ color: {p.disabled}; }}
QFrame[nav="true"] {{ border: none; border-radius: 6px; background: transparent; }}
QFrame[nav="true"][current="true"] {{ background: {p.accent_tint}; }}

QLineEdit, QComboBox, QSpinBox {{ min-height: 32px; border: 1px solid {p.control}; border-radius: 6px; padding: 0 10px;
                                  background: {p.surface}; selection-background-color: {p.accent_fill}; }}
QLineEdit:disabled {{ background: {p.background}; color: {p.faint}; }}

QFrame[card="true"] {{ border: 1px solid {p.border}; border-radius: 8px; background: {p.surface}; }}
QFrame[card="dashed"] {{ border: 1px dashed {p.control}; border-radius: 8px; background: transparent; }}
QFrame[card="quiet"] {{ border: 1px solid {p.border}; border-radius: 8px; background: {p.surface_subtle}; }}
QFrame[card="dashed"] QLabel {{ color: {p.muted}; }}

QTreeWidget#toGetTree {{ border: none; background: {p.surface}; outline: 0; selection-background-color: {p.selected};
                         selection-color: {p.text}; }}
QTreeWidget#toGetTree::item {{ border: none; }}
QTableWidget#releasesTable, QTableWidget#progressTable {{ border: none; background: {p.surface};
                                                          gridline-color: {p.row_rule}; outline: 0;
                                                          selection-background-color: {p.selected};
                                                          selection-color: {p.text};
                                                          alternate-background-color: {p.surface}; }}
QFrame#releasesCard {{ border: 1px solid {p.border}; border-radius: 8px; background: {p.surface}; }}
QHeaderView::section {{ background: {p.surface_subtle}; color: {p.muted}; border: none; padding: 8px 12px; font-size: 11px;
                        font-weight: 600; }}
QTableWidget#progressTable QHeaderView::section {{ background: {p.surface}; padding: 6px 8px 6px 0; }}
QProgressBar {{ border: none; background: {p.background}; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}
"""


def apply_style(widget: QWidget, extra: Optional[Callable[[], str]] = None) -> None:
    """Attach the stylesheet (plus ``extra()``, a host's own rules) to a top-level host (the Download tab, the
    Settings dialog, a wrapper dialog); a switch of Appearance re-makes it."""
    from .spin_arrows import spin_rules

    def build() -> str:
        p = current()
        return stylesheet(p) + spin_rules(line=p.control, color=p.muted) + (extra() if extra is not None else "")

    theme.bind_sheet(widget, build)


def repolish(widget: QWidget) -> None:
    """Re-evaluate the stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget: QWidget, name: str, value) -> None:
    """Set a dynamic property the stylesheet selects on, and restyle."""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


def set_tone(label: QWidget, tone: str = "") -> None:
    """Colour a message: ``ok`` / ``bad`` / ``warn``, or '' for the plain text colour."""
    set_prop(label, "tone", tone)
