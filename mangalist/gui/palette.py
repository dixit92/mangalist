"""MangaList's colours as named tokens, in a Light and a Dark palette, and the Appearance setting (Light / Dark / Follow
the system). Qt-free: the stylesheets (:mod:`.theme`, :mod:`.download_style`) and every painted cell read the tokens of
:func:`current`, and the setting is read before Qt starts.

Owner, 2026-10-13: "we need a dark mode option. Switching from animefetch to MangaList to MangaPixer is disorienting."

- **Light** is the approved mockup (2026-10-08) exactly: every value below is a colour the app already drew, one token
  per role - where two roles shared a hex in the mockup they get two tokens with the same light value, so Dark can
  tell them apart (the blue of a link is not the blue a white label sits on).
- **Dark** comes from MangaPixer's dark theme (``web/src/styles.scss`` ``:root``, 1.40.0), so the three apps read as one
  family: its page, surfaces, text ramp and status colours as they are; borders, hover fills and badge backgrounds are
  MangaPixer's ink / status colours over the surface at the alphas its components use (``rgb(var(--mp-ink-rgb) / a)``),
  flattened to solid colours here. The accent is MangaPixer's default **violet** (``--mp-accent`` / ``-strong`` /
  ``-soft`` of its dark ``:root``), so a MangaPixer left on violet and MangaList's Dark look alike (owner, 2026-10-15:
  "Yes" to taking violet over the blue Dark first shipped with). Light keeps MangaList's blue. Every text token reads
  at WCAG AA (4.5:1) on the surfaces it is drawn on (``tests/test_palette.py``).
- **Not tracked** (Track completion off in MangaPixer) has its own badge colour in both palettes (owner, 2026-10-15:
  "a new color ... Theme sensitive"): MangaPixer's teal, which no other state uses, so it no longer reads as the quiet
  grey of Up to date.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Mapping, Optional, Tuple

# --- the tokens -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Palette:
    name: str                   # "light" / "dark"
    dark: bool
    # grounds and surfaces
    background: str             # the window behind the panels
    surface: str                # panels, tables, inputs, cards
    surface_subtle: str         # the filter bar, the details panel, footers, alternate rows
    raised: str                 # menus and drop-down lists (above the panels)
    hover: str                  # a button or header under the mouse
    pressed: str                # a pressed button; the progress bar's groove
    selected: str               # the picked row
    # text
    text: str                   # body text (the mockup's "ink")
    text_2: str                 # the second ink (English / Kind columns, check boxes)
    muted: str                  # headings, the top bar's status, notes
    muted_2: str                # secondary lines (a matched title under the folder's)
    disabled: str               # disabled text
    faint: str                  # a disabled text box's text (Download tab / Settings)
    # lines
    border: str                 # panel edges
    border_soft: str            # inner rules
    row_rule: str               # between table rows
    control: str                # the edge of buttons and inputs; scroll bar handles
    control_strong: str         # a scroll bar handle under the mouse
    shadow: str
    arrow_off: str              # a spin box arrow that cannot go further
    # the accent
    accent: str                 # links, focus edges, the current tab, the "Upgrade available" text
    accent_fill: str            # a filled (primary) button
    accent_text: str            # the label on a filled button
    accent_hover: str           # a link under the mouse; the checked chip's text
    accent_fill_hover: str      # a primary button under the mouse (the List's sheet)
    accent_fill_hover_2: str    # a primary button under the mouse (the Download tab's / Settings' sheet)
    accent_fill_disabled: str   # a disabled primary button (Download tab / Settings)
    accent_text_disabled: str   # the label on a disabled primary button
    accent_tint: str            # a checked chip, the current Settings section, the upgrade note
    busy_row: str               # the List row being looked up on MangaUpdates
    tooltip_bg: str
    tooltip_text: str
    # badges: (background, text) each
    ok_bg: str
    ok_text: str
    warn_bg: str
    warn_text: str
    bad_bg: str
    bad_text: str
    info_bg: str
    info_text: str
    neutral_bg: str             # Can't tell / Not a series / done / muted
    neutral_text: str
    quiet_bg: str               # Up to date, and a state without its own colour
    quiet_text: str
    wanted_bg: str              # the Wanted states (empty folders)
    wanted_text: str
    untracked_bg: str           # Not tracked (Track completion off in MangaPixer)
    untracked_text: str
    # the semantic colours outside badges
    warn_line: str              # the edge of the "missing" button
    warn_hover: str             # the "missing" button under the mouse
    bad_fill: str               # a danger button (Delete)
    bad_fill_hover: str
    bad_line: str               # the edge of a quiet danger button
    caution_text: str           # the Renamer's "left alone"
    error_text: str             # a library folder that is not there (Settings > Library)

    def badge(self, kind: str) -> Tuple[str, str]:
        """(background, text) of a badge kind: ``ok`` / ``warn`` / ``bad`` / ``info`` / ``neutral`` / ``quiet`` /
        ``wanted`` / ``untracked``."""
        return getattr(self, f"{kind}_bg"), getattr(self, f"{kind}_text")

    def tokens(self) -> Dict[str, str]:
        """Every colour token by name (not ``name`` / ``dark``)."""
        return {k: v for k, v in asdict(self).items() if k not in ("name", "dark")}


TOKEN_NAMES = tuple(f.name for f in fields(Palette) if f.name not in ("name", "dark"))

#: The approved mockup's colours (2026-10-08), as the app drew them before Dark existed.
LIGHT = Palette(
    name="light", dark=False,
    background="#f3f3f1", surface="#ffffff", surface_subtle="#fafaf8", raised="#ffffff", hover="#f6f6f4",
    pressed="#ededea", selected="#eef3fd",
    text="#161616", text_2="#3a3a38", muted="#5a5a57", muted_2="#6b6b67", disabled="#9a9a96", faint="#8a8a86",
    border="#dcdcd8", border_soft="#e3e3df", row_rule="#efefec", control="#c4c4bf", control_strong="#a9a9a4",
    shadow="#8e8e8a", arrow_off="#b4b4b0",
    accent="#1f4fb8", accent_fill="#1f4fb8", accent_text="#ffffff", accent_hover="#163a87",
    accent_fill_hover="#163a87", accent_fill_hover_2="#1a43a0", accent_fill_disabled="#a9b9dc",
    accent_text_disabled="#ffffff", accent_tint="#e8eefb",
    busy_row="#dce6fa", tooltip_bg="#161616", tooltip_text="#ffffff",
    ok_bg="#e7f3ec", ok_text="#1d5e36", warn_bg="#fdf0e3", warn_text="#8a3f00", bad_bg="#fbeaea", bad_text="#8b1d1d",
    info_bg="#e8eefb", info_text="#1f4fb8", neutral_bg="#ededea", neutral_text="#4a4a47", quiet_bg="#eef0f2",
    quiet_text="#3a3f45", wanted_bg="#f1ecfa", wanted_text="#5a2d91",
    untracked_bg="#e3f2ef", untracked_text="#00655a",       # added 2026-10-15: MangaPixer's light-base teal
    warn_line="#e8c9a4", warn_hover="#fae4cc", bad_fill="#8b1d1d", bad_fill_hover="#6e1616", bad_line="#d9a3a3",
    caution_text="#9a5b00", error_text="#b71c1c",
)


def _rgb(color: str) -> Tuple[int, int, int]:
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(top: str, under: str, alpha: float) -> str:
    """*top* at *alpha* over *under*, as one solid colour (``rgb(top / alpha)`` painted on *under*)."""
    a, b = _rgb(top), _rgb(under)
    return "#%02x%02x%02x" % tuple(round(b[i] + (a[i] - b[i]) * alpha) for i in range(3))


def luminance(color: str) -> float:
    """WCAG 2 relative luminance."""
    def channel(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio of two colours (1 to 21; 4.5 is AA for body text)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# MangaPixer's dark theme (web/src/styles.scss, :root - the dark values; themes/_accents.scss for blue). The names
# are MangaPixer's tokens, so the derivation of each MangaList token below can be read against its source.
MP_PAGE = "#121212"                 # --mp-page
MP_SURFACE = "#14141c"              # --mp-surface
MP_SURFACE_RAISED = "#1c1c26"       # --mp-surface-raised
MP_SURFACE_HIGH = "#2a2a36"         # --mp-surface-high
MP_INK = "#ffffff"                  # --mp-ink-rgb 255 255 255 (hover fills, borders and dividers are tinted from it)
MP_TEXT = "#e6e6ee"                 # --mp-text
MP_TEXT_SECONDARY = "#c8c8d4"       # --mp-text-secondary
MP_TEXT_MUTED = "#9a9aa8"           # --mp-text-muted
MP_TEXT_DIM = "#8a8a99"             # --mp-text-dim
# --mp-text-dim lifted a step (as MangaPixer's black base lifts its faintest text): the secondary lines sit on the picked
# row and on hover fills too, where #8a8a99 reads at 4.3:1.
MP_TEXT_DIM_LIFTED = "#90909f"
MP_TEXT_FAINT = "#777777"           # --mp-text-faint
MP_TEXT_FAINT_BLACK = "#808080"     # --mp-text-faint on the black base (lifted a step for contrast)
# The blue accent (the accent of Dark until 2026-10-15) stays for the info badge (Upgrade available), as in Light
# where the info badge is MangaList's blue.
MP_BLUE = "#9ecaff"                 # --mp-accent, blue accent (tone 80)
MP_BLUE_STRONG = "#0075c4"          # --mp-accent-strong, blue (tone 48: white on it 4.8:1)
MP_VIOLET = "#b39dff"               # --mp-accent (the default violet)
MP_VIOLET_STRONG = "#7c4dff"        # --mp-accent-strong (violet: white on it 4.8:1)
MP_VIOLET_SOFT = "#d8ccff"          # --mp-accent-soft (violet)
# --mp-accent of the light base (violet): the deeper violet a primary button darkens to under the mouse, as blue's
# tone 40 did (white on it 7.6:1).
MP_VIOLET_DEEP = "#5b33c9"
MP_TEAL = "#80cbc4"                 # --mp-teal
MP_TEAL_LIGHT = "#00655a"           # --mp-teal on the light base
MP_WARN = "#ffcc80"                 # --mp-warn
MP_WARN_STRONG = "#ffb300"          # --mp-warn-strong
MP_CAUTION = "#ffb74d"              # --mp-caution
MP_ERROR = "#ff8a80"                # --mp-error
MP_ERROR_STRONG = "#f44336"         # --mp-error-strong
MP_ERROR_LIGHT = "#c62828"          # --mp-error on the light base (white on it 5.6:1: a danger button's fill)
MP_ERROR_STRONG_LIGHT = "#d32f2f"   # --mp-error-strong on the light base
MP_SUCCESS = "#81c784"              # --mp-success
MP_SUCCESS_STRONG = "#4caf50"       # --mp-success-strong
MP_INFO = "#90caf9"                 # --mp-info

_ON = MP_SURFACE_RAISED             # the panels: what the tints and lines are flattened onto

DARK = Palette(
    name="dark", dark=True,
    background=MP_PAGE, surface=MP_SURFACE_RAISED, surface_subtle=MP_SURFACE, raised=MP_SURFACE_HIGH,
    hover=mix(MP_INK, _ON, 0.05), pressed=MP_SURFACE_HIGH, selected=mix(MP_VIOLET_STRONG, _ON, 0.16),
    text=MP_TEXT, text_2=MP_TEXT_SECONDARY, muted=MP_TEXT_MUTED, muted_2=MP_TEXT_DIM_LIFTED, disabled=MP_TEXT_FAINT,
    faint=MP_TEXT_FAINT_BLACK,
    border=mix(MP_INK, _ON, 0.13), border_soft=mix(MP_INK, _ON, 0.09), row_rule=mix(MP_INK, _ON, 0.06),
    control=mix(MP_INK, _ON, 0.22), control_strong=mix(MP_INK, _ON, 0.36), shadow="#000000",
    arrow_off=mix(MP_INK, _ON, 0.25),
    accent=MP_VIOLET, accent_fill=MP_VIOLET_STRONG, accent_text="#ffffff", accent_hover=MP_VIOLET_SOFT,
    accent_fill_hover=MP_VIOLET_DEEP, accent_fill_hover_2=MP_VIOLET_DEEP,
    accent_fill_disabled=mix(MP_VIOLET_STRONG, _ON, 0.28), accent_text_disabled=mix(MP_TEXT, _ON, 0.45),
    accent_tint=mix(MP_VIOLET_STRONG, _ON, 0.22), busy_row=mix(MP_VIOLET_STRONG, _ON, 0.30),
    tooltip_bg=MP_TEXT, tooltip_text=MP_SURFACE,
    ok_bg=mix(MP_SUCCESS_STRONG, _ON, 0.20), ok_text=MP_SUCCESS,
    warn_bg=mix(MP_WARN_STRONG, _ON, 0.16), warn_text=MP_WARN,
    bad_bg=mix(MP_ERROR_STRONG, _ON, 0.20), bad_text=MP_ERROR,
    info_bg=mix(MP_BLUE_STRONG, _ON, 0.22), info_text=MP_BLUE,
    neutral_bg=MP_SURFACE_HIGH, neutral_text=MP_TEXT_SECONDARY,
    quiet_bg=mix(MP_INFO, _ON, 0.08), quiet_text=MP_TEXT_SECONDARY,
    wanted_bg=mix(MP_VIOLET_STRONG, _ON, 0.22), wanted_text=MP_VIOLET,
    untracked_bg=mix(MP_TEAL, _ON, 0.16), untracked_text=MP_TEAL,
    warn_line=mix(MP_WARN, _ON, 0.35), warn_hover=mix(MP_WARN_STRONG, _ON, 0.26), bad_fill=MP_ERROR_LIGHT,
    bad_fill_hover=MP_ERROR_STRONG_LIGHT, bad_line=mix(MP_ERROR, _ON, 0.40), caution_text=MP_CAUTION,
    error_text=MP_ERROR,
)

PALETTES: Dict[str, Palette] = {LIGHT.name: LIGHT, DARK.name: DARK}

#: The text tokens and the backgrounds each is drawn on (the contrast test checks every pair at AA in Dark).
TEXT_ON: Dict[str, Tuple[str, ...]] = {
    "text": ("background", "surface", "surface_subtle", "raised", "hover", "pressed", "selected", "accent_tint"),
    "text_2": ("background", "surface", "surface_subtle", "hover", "selected"),
    "muted": ("background", "surface", "surface_subtle", "raised", "hover", "selected"),
    "muted_2": ("background", "surface", "surface_subtle", "hover", "selected"),
    "accent": ("background", "surface", "surface_subtle", "hover", "accent_tint", "selected"),
    "accent_hover": ("surface", "surface_subtle", "accent_tint"),
    "accent_text": ("accent_fill", "accent_fill_hover", "accent_fill_hover_2"),
    "tooltip_text": ("tooltip_bg",),
    "ok_text": ("ok_bg", "surface"), "warn_text": ("warn_bg", "surface", "selected"), "bad_text": ("bad_bg", "surface"),
    "info_text": ("info_bg", "surface"), "neutral_text": ("neutral_bg",), "quiet_text": ("quiet_bg",),
    "wanted_text": ("wanted_bg",), "untracked_text": ("untracked_bg", "surface"), "caution_text": ("surface", "selected"),
    "error_text": ("surface", "selected"),
}
#: White labels on the danger fills (checked separately: ``accent_text`` is the white of every filled button).
FILL_LABELS: Tuple[Tuple[str, str], ...] = (("accent_text", "bad_fill"), ("accent_text", "bad_fill_hover"))

# --- the current palette --------------------------------------------------------------------------------------

_current: Palette = LIGHT


def current() -> Palette:
    """The palette in force (Light until :func:`set_current` - the theme sets it at start and on a switch)."""
    return _current


def set_current(palette: Palette) -> Palette:
    global _current
    _current = palette
    return palette


# --- the Appearance setting -----------------------------------------------------------------------------------

KEY_APPEARANCE = "ui.appearance"
APPEARANCE_LIGHT, APPEARANCE_DARK, APPEARANCE_SYSTEM = "light", "dark", "system"
APPEARANCES = (APPEARANCE_LIGHT, APPEARANCE_DARK, APPEARANCE_SYSTEM)
APPEARANCE_LABELS = {APPEARANCE_LIGHT: "Light", APPEARANCE_DARK: "Dark", APPEARANCE_SYSTEM: "Follow the system"}
#: Today's look until the owner chooses (2026-10-13).
DEFAULT_APPEARANCE = APPEARANCE_LIGHT
#: The container's base image (jlesage/baseimage-gui) switches its window frames, file manager and web page to dark
#: with ``DARK_MODE=1``; Qt hears nothing from it, so Follow the system reads it where Qt says nothing.
ENV_DARK_MODE = "DARK_MODE"


def load_appearance(store: Any) -> str:
    """The stored choice (``light`` / ``dark`` / ``system``); the default for nothing stored or an unknown value. Never
    raises: a database that cannot be read keeps today's look."""
    try:
        value = store.get_setting(KEY_APPEARANCE, None)
    except Exception:  # noqa: BLE001 - the look must not stop the app
        return DEFAULT_APPEARANCE
    return value if value in APPEARANCES else DEFAULT_APPEARANCE


def save_appearance(store: Any, choice: str) -> str:
    if choice not in APPEARANCES:
        raise ValueError(f"not an appearance: {choice!r}")
    store.set_setting(KEY_APPEARANCE, choice)
    return choice


def _env_dark(env: Optional[Mapping[str, str]]) -> bool:
    value = (os.environ if env is None else env).get(ENV_DARK_MODE, "")
    return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")


def resolve(choice: str, system: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> Palette:
    """The palette for a choice. *system* is what the platform says (``"dark"`` / ``"light"``, None when it says
    nothing - Qt's ``ColorScheme.Unknown``). Follow the system with nothing said is Light, unless the container asks for
    dark (``DARK_MODE=1``)."""
    if choice == APPEARANCE_DARK:
        return DARK
    if choice == APPEARANCE_SYSTEM:
        if system == "dark":
            return DARK
        if system is None and _env_dark(env):
            return DARK
    return LIGHT


__all__ = [
    "APPEARANCES", "APPEARANCE_DARK", "APPEARANCE_LABELS", "APPEARANCE_LIGHT", "APPEARANCE_SYSTEM", "DARK",
    "DEFAULT_APPEARANCE", "KEY_APPEARANCE", "LIGHT", "PALETTES", "Palette", "TEXT_ON", "TOKEN_NAMES", "contrast",
    "current", "load_appearance", "mix", "resolve", "save_appearance", "set_current",
]
