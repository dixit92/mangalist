"""The look of the Download tab and the Settings dialog, as a Qt stylesheet keyed on ``objectName`` / dynamic
properties, from the approved mockup (``Download.dc.html`` / ``Settings.dc.html``: #f3f3f1 ground, white panels, #161616
ink, #1f4fb8 accent).

The widgets carry the hooks, the stylesheet decides the look - so the application theme can restyle them by targeting
the same selectors, and :func:`apply_style` is the only place this module's sheet is attached (drop the call to let the
theme alone style them):

- ``QFrame[nav="true"]`` a Settings section entry (``[current="true"]`` the open one);
- ``QPushButton[primary="true"]`` the blue button; ``QPushButton[link="true"]`` a text-link button
- ``QLabel[badge="ok|run|done|bad|warn|muted"]`` a pill; ``QLabel[tone="ok|bad|warn"]`` a coloured message
- ``QLabel[role="h1|h2|h3|muted|mono|lead"]`` text roles; ``QFrame[card="true"]`` a bordered card
  (``[card="dashed"]`` the "later" placeholders, ``[card="quiet"]`` a greyed-out one)
- ``QFrame#toGetPanel`` / ``#inProgressPanel`` / ``QDialog#settingsDialog`` the big surfaces
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

INK = "#161616"
MUTED = "#5a5a57"
ACCENT = "#1f4fb8"
ACCENT_SOFT = "#e8eefb"
SELECTED = "#eef3fd"
GROUND = "#f3f3f1"
LINE = "#dcdcd8"
FIELD_LINE = "#c4c4bf"

# badge colours: (background, text)
BADGES = {
    "run": ("#e8eefb", "#1f4fb8"),
    "ok": ("#e7f3ec", "#1d5e36"),
    "bad": ("#fbeaea", "#8b1d1d"),
    "done": ("#ededea", "#4a4a47"),
    "muted": ("#ededea", "#4a4a47"),
    "warn": ("#fdf0e3", "#8a3f00"),
}
TONES = {"ok": "#1d5e36", "bad": "#8b1d1d", "warn": "#8a3f00"}

FONT_SANS = "'IBM Plex Sans', 'Segoe UI', sans-serif"
FONT_MONO = "'IBM Plex Mono', 'DejaVu Sans Mono', monospace"


def _badge_rules() -> str:
    return "\n".join(f'QLabel[badge="{kind}"] {{ background: {bg}; color: {fg}; }}' for kind, (bg, fg) in BADGES.items())


def _tone_rules() -> str:
    return "\n".join(f'QLabel[tone="{tone}"] {{ color: {color}; }}' for tone, color in TONES.items())


STYLESHEET = f"""
QWidget#downloadTab, QDialog#settingsDialog {{ background: {GROUND}; color: {INK}; font-family: {FONT_SANS}; font-size: 14px; }}
QDialog#settingsDialog {{ background: #ffffff; }}
QFrame#toGetPanel, QFrame#inProgressPanel {{ background: #ffffff; }}
QFrame#toGetPanel {{ border: none; border-right: 1px solid {LINE}; }}
QFrame#inProgressPanel {{ border: none; border-top: 1px solid {LINE}; }}
QFrame#toGetFooter {{ background: #fafaf8; border: none; border-top: 1px solid #e3e3df; }}
QFrame#toGetHeader {{ background: #ffffff; border: none; border-bottom: 1px solid #e3e3df; }}
QFrame#settingsHeader {{ background: #ffffff; border: none; border-bottom: 1px solid #e3e3df; }}
QScrollArea#settingsScroll {{ background: #ffffff; border: none; }}
QWidget#settingsBody {{ background: #ffffff; }}
QFrame#settingsNav {{ background: #fafaf8; border: none; border-right: 1px solid #e3e3df; }}

QLabel[role="h1"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {MUTED}; font-size: 13px; }}
QLabel[role="lead"] {{ color: {MUTED}; }}
QLabel[role="mono"] {{ font-family: {FONT_MONO}; color: {MUTED}; font-size: 13px; }}
QLabel[role="name"] {{ font-weight: 700; font-size: 15px; }}
QLabel[role="navtitle"] {{ font-weight: 600; font-size: 14px; background: transparent; }}
QLabel[role="navhint"] {{ font-size: 12px; color: #6b6b67; background: transparent; }}
QLabel[badge] {{ border-radius: 8px; padding: 3px 9px; font-size: 12px; font-weight: 600; }}
{_badge_rules()}
{_tone_rules()}

QPushButton {{ min-height: 34px; padding: 0 12px; border: 1px solid {FIELD_LINE}; border-radius: 6px;
               background: #ffffff; color: {INK}; }}
QPushButton:hover {{ background: #f6f6f4; }}
QPushButton:disabled {{ color: #9a9a96; background: #f3f3f1; border-color: #dcdcd8; }}
QPushButton[primary="true"] {{ border: 1px solid {ACCENT}; background: {ACCENT}; color: #ffffff; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: #1a43a0; }}
QPushButton[primary="true"]:disabled {{ background: #a9b9dc; border-color: #a9b9dc; color: #ffffff; }}
QPushButton[link="true"] {{ border: none; background: transparent; color: {ACCENT}; min-height: 24px; padding: 0 4px; }}
QPushButton[link="true"]:hover {{ color: #163a87; text-decoration: underline; }}
QPushButton[link="true"]:disabled {{ color: #9a9a96; }}
QFrame[nav="true"] {{ border: none; border-radius: 6px; background: transparent; }}
QFrame[nav="true"][current="true"] {{ background: {ACCENT_SOFT}; }}

QLineEdit, QComboBox, QSpinBox {{ min-height: 32px; border: 1px solid {FIELD_LINE}; border-radius: 6px; padding: 0 10px;
                                  background: #ffffff; selection-background-color: {ACCENT}; }}
QLineEdit:disabled {{ background: #f3f3f1; color: #8a8a86; }}

QFrame[card="true"] {{ border: 1px solid {LINE}; border-radius: 8px; background: #ffffff; }}
QFrame[card="dashed"] {{ border: 1px dashed {FIELD_LINE}; border-radius: 8px; background: transparent; }}
QFrame[card="quiet"] {{ border: 1px solid {LINE}; border-radius: 8px; background: #fafaf8; }}
QFrame[card="dashed"] QLabel {{ color: {MUTED}; }}

QTreeWidget#toGetTree {{ border: none; background: #ffffff; outline: 0; selection-background-color: {SELECTED};
                         selection-color: {INK}; }}
QTreeWidget#toGetTree::item {{ border: none; }}
QTableWidget#releasesTable, QTableWidget#progressTable {{ border: none; background: #ffffff; gridline-color: #efefec;
                                                          outline: 0; selection-background-color: {SELECTED};
                                                          selection-color: {INK}; alternate-background-color: #ffffff; }}
QFrame#releasesCard {{ border: 1px solid {LINE}; border-radius: 8px; background: #ffffff; }}
QHeaderView::section {{ background: #fafaf8; color: {MUTED}; border: none; padding: 8px 12px; font-size: 11px;
                        font-weight: 600; }}
QTableWidget#progressTable QHeaderView::section {{ background: #ffffff; padding: 6px 8px 6px 0; }}
QProgressBar {{ border: none; background: {GROUND}; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
"""


def apply_style(widget: QWidget) -> None:
    """Attach the stylesheet to a top-level host (the Download tab, the Settings dialog, a wrapper dialog)."""
    from .spin_arrows import spin_rules

    widget.setStyleSheet(STYLESHEET + spin_rules(line=FIELD_LINE, color=MUTED))


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
