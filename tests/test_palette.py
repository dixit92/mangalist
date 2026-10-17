"""The colour tokens and the Appearance setting, without Qt: Light is the approved mockup exactly, Dark has every token,
its text reads at WCAG AA on the surfaces it is drawn on, and Follow the system with nothing said is Light."""

from __future__ import annotations

import re

import pytest

from mangalist.gui import palette
from mangalist.gui.palette import DARK, LIGHT, TEXT_ON, TOKEN_NAMES, contrast

#: The colours the app drew before Dark existed (the 2026-10-08 mockup, gui/theme.py and the other GUI files).
TODAY = {
    "background": "#f3f3f1", "surface": "#ffffff", "surface_subtle": "#fafaf8", "raised": "#ffffff",
    "hover": "#f6f6f4", "pressed": "#ededea", "selected": "#eef3fd", "text": "#161616", "text_2": "#3a3a38",
    "muted": "#5a5a57", "muted_2": "#6b6b67", "disabled": "#9a9a96", "faint": "#8a8a86", "border": "#dcdcd8",
    "border_soft": "#e3e3df", "row_rule": "#efefec", "control": "#c4c4bf", "control_strong": "#a9a9a4",
    "shadow": "#8e8e8a", "arrow_off": "#b4b4b0", "accent": "#1f4fb8", "accent_fill": "#1f4fb8",
    "accent_text": "#ffffff", "accent_hover": "#163a87", "accent_fill_hover": "#163a87",
    "accent_fill_hover_2": "#1a43a0", "accent_fill_disabled": "#a9b9dc", "accent_text_disabled": "#ffffff",
    "accent_tint": "#e8eefb",
    "busy_row": "#dce6fa", "tooltip_bg": "#161616", "tooltip_text": "#ffffff",
    "ok_bg": "#e7f3ec", "ok_text": "#1d5e36", "warn_bg": "#fdf0e3", "warn_text": "#8a3f00", "bad_bg": "#fbeaea",
    "bad_text": "#8b1d1d", "info_bg": "#e8eefb", "info_text": "#1f4fb8", "neutral_bg": "#ededea",
    "neutral_text": "#4a4a47", "quiet_bg": "#eef0f2", "quiet_text": "#3a3f45", "wanted_bg": "#f1ecfa",
    "wanted_text": "#5a2d91", "warn_line": "#e8c9a4", "warn_hover": "#fae4cc", "bad_fill": "#8b1d1d",
    "bad_fill_hover": "#6e1616", "bad_line": "#d9a3a3", "caution_text": "#9a5b00", "error_text": "#b71c1c",
}
#: Tokens added after Dark (2026-10-15): Not tracked's own badge.
ADDED = {"untracked_bg": "#e3f2ef", "untracked_text": "#00655a"}


#: Tokens recoloured since (2026-10-16, owner answer 81 "another hue"): the Wanted badge, violet before.
RECOLOURED = {"wanted_bg": "#fbe8f6", "wanted_text": "#86206b"}


def test_light_is_todays_look_except_what_was_recoloured():
    assert LIGHT.tokens() == {**TODAY, **ADDED, **RECOLOURED}


def test_every_token_is_in_both_palettes_as_a_colour():
    assert set(LIGHT.tokens()) == set(DARK.tokens()) == set(TOKEN_NAMES) == set(TODAY) | set(ADDED)
    for pal in (LIGHT, DARK):
        for name, value in pal.tokens().items():
            assert re.fullmatch(r"#[0-9a-f]{6}", value), (pal.name, name, value)
    assert DARK.dark and not LIGHT.dark
    # the badge kinds the GUI asks for exist in both
    for kind in ("ok", "warn", "bad", "info", "neutral", "quiet", "wanted", "untracked"):
        assert len(LIGHT.badge(kind)) == len(DARK.badge(kind)) == 2


def test_dark_is_dark():
    for name in ("background", "surface", "surface_subtle", "raised", "hover", "pressed", "selected"):
        assert palette.luminance(getattr(DARK, name)) < 0.05, name
    assert palette.luminance(DARK.text) > 0.7


@pytest.mark.parametrize("text,backgrounds", sorted(TEXT_ON.items()))
def test_dark_text_reads_at_wcag_aa(text, backgrounds):
    for bg in backgrounds:
        ratio = contrast(getattr(DARK, text), getattr(DARK, bg))
        assert ratio >= 4.5, f"{text} on {bg}: {ratio:.2f}"


def test_white_labels_on_dark_fills_read_at_aa():
    for text, fill in palette.FILL_LABELS:
        assert contrast(getattr(DARK, text), getattr(DARK, fill)) >= 4.5, fill


def test_dark_comes_from_mangapixers_theme():
    # MangaPixer's dark :root tokens, taken as they are (web/src/styles.scss)
    assert (DARK.background, DARK.surface_subtle, DARK.surface, DARK.raised) == (
        "#121212", "#14141c", "#1c1c26", "#2a2a36")
    assert (DARK.text, DARK.text_2, DARK.muted) == ("#e6e6ee", "#c8c8d4", "#9a9aa8")
    # its default violet accent (owner, 2026-10-15), not the blue Dark first shipped with
    assert (DARK.accent, DARK.accent_fill, DARK.accent_hover) == ("#b39dff", "#7c4dff", "#d8ccff")
    assert DARK.info_text == "#9ecaff"                                      # the info badge stays blue
    assert (DARK.ok_text, DARK.warn_text, DARK.bad_text) == ("#81c784", "#ffcc80", "#ff8a80")
    assert palette.mix("#ffffff", "#000000", 0.5) == "#808080"


def test_contrast_is_wcags():
    assert round(contrast("#000000", "#ffffff"), 1) == 21.0
    assert contrast("#777777", "#777777") == 1.0


@pytest.mark.parametrize("choice,system,env,expected", [
    ("light", "dark", {}, LIGHT),
    ("dark", "light", {}, DARK),
    ("dark", None, {}, DARK),
    ("system", "dark", {}, DARK),
    ("system", "light", {"DARK_MODE": "1"}, LIGHT),        # the platform's word wins
    ("system", None, {}, LIGHT),                          # nothing said (the container's web GUI): Light
    ("system", None, {"DARK_MODE": "0"}, LIGHT),
    ("system", None, {"DARK_MODE": "1"}, DARK),           # the container asks for dark
    ("nonsense", "dark", {}, LIGHT),
])
def test_resolve(choice, system, env, expected):
    assert palette.resolve(choice, system, env) is expected


class FakeStore:
    def __init__(self, values=None, broken=False):
        self.values = dict(values or {})
        self.broken = broken

    def get_setting(self, key, default=None):
        if self.broken:
            raise OSError("database locked")
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


def test_the_setting_is_stored_and_read_back_with_light_as_the_default():
    store = FakeStore()
    assert palette.load_appearance(store) == "light" == palette.DEFAULT_APPEARANCE
    palette.save_appearance(store, "dark")
    assert store.values == {"ui.appearance": "dark"} and palette.load_appearance(store) == "dark"
    palette.save_appearance(store, "system")
    assert palette.load_appearance(store) == "system"
    with pytest.raises(ValueError):
        palette.save_appearance(store, "sepia")
    assert palette.load_appearance(FakeStore({"ui.appearance": "sepia"})) == "light"
    assert palette.load_appearance(FakeStore(broken=True)) == "light"


def test_the_current_palette_starts_light_and_can_be_set():
    assert palette.current() is LIGHT
    try:
        assert palette.set_current(DARK) is DARK and palette.current() is DARK
    finally:
        palette.set_current(LIGHT)


def test_not_tracked_has_its_own_badge_in_both_palettes():
    for pal in (LIGHT, DARK):
        others = {pal.badge(k) for k in ("ok", "warn", "bad", "info", "neutral", "quiet", "wanted")}
        assert pal.badge("untracked") not in others, pal.name
        assert contrast(pal.untracked_text, pal.untracked_bg) >= 4.5, pal.name
        assert contrast(pal.untracked_text, pal.surface) >= 4.5, pal.name


def _hue(color: str) -> float:
    import colorsys

    r, g, b = (v / 255 for v in palette._rgb(color))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


def _apart(a: str, b: str) -> float:
    d = abs(_hue(a) - _hue(b)) % 360
    return min(d, 360 - d)


@pytest.mark.parametrize("pal", [LIGHT, DARK], ids=["light", "dark"])
def test_the_wanted_badge_has_a_hue_of_its_own(pal):
    """Owner, 2026-10-16: the Wanted badge was the violet of Dark's accent ("another hue"). Its text is at least 40
    degrees of hue from the accent and from every other coloured badge (the greys and the blue-grey Up to date aside),
    and it reads at AA on its own badge in both palettes."""
    wanted = pal.wanted_text
    assert contrast(wanted, pal.wanted_bg) >= 4.5
    assert _apart(wanted, pal.accent) >= 40
    for kind in ("ok", "warn", "bad", "info", "untracked"):
        assert _apart(wanted, pal.badge(kind)[1]) >= 40, kind
        assert _apart(pal.wanted_bg, pal.badge(kind)[0]) >= 40, kind
    assert pal.wanted_text != pal.accent and pal.wanted_bg != pal.accent_tint
