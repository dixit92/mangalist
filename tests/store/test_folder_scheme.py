"""The series-folder name scheme ``<romaji> [<English>]`` (``mangalist.folder_scheme``; owner, 2026-10-15, answer 6:
"Always use "<romaji> [<English>]", similar to animefetch"). Pure functions; made-up titles only."""

from __future__ import annotations

import pytest

from mangalist import folder_scheme as fs
from mangalist.store.names import windows_name_problem


@pytest.mark.parametrize("romaji, english, name", [
    ("Made Up Romaji", "Made Up English", "Made Up Romaji [Made Up English]"),
    ("Made Up Romaji", None, "Made Up Romaji"),                          # no English title: just the romaji
    ("Made Up Romaji", "", "Made Up Romaji"),
    ("Made Up Romaji", "made-up ROMAJI", "Made Up Romaji"),              # the romaji again, written differently
    ("Made Up Rōmaji", "Made Up Romaji", "Made Up Rōmaji"),              # accents folded for the comparison
    ("Re:Made Up", "Re Made Up: The Story", "ReMade Up [Re Made Up The Story]"),     # colons dropped, as animefetch
    ('Made/Up \\ "Odd" <Title>? *', None, "Made-Up Odd Title"),
    ("Made Up [Tag] Romaji", "English [Note]", "Made Up (Tag) Romaji [English (Note)]"),  # brackets stay unambiguous
    ("  Spaced   out  ", None, "Spaced out"),
    ("Ends with dots...", "English.", "Ends with dots [English]"),
    (".Hidden Romaji", None, "Hidden Romaji"),                           # a hidden folder would never be scanned
    ("CON", None, "CON_"),                                               # a Windows reserved name
    ("", "Made Up English", None),                                       # no romaji: no name
    ("???", None, None),
])
def test_scheme_name(romaji, english, name):
    assert fs.scheme_name(romaji, english) == name
    if name:
        assert windows_name_problem(name) is None


def test_a_long_romaji_title_is_shortened_to_its_main_part():
    long = "Made Up Romaji wa Hontou ni: Nagai Nagai Fukudai ga Tsuzuku Monogatari desu"
    assert len(long) > fs.LONG_TITLE
    assert fs.short_romaji(long) == "Made Up Romaji wa Hontou ni"
    assert fs.scheme_name(long, "The Made Up Story") == "Made Up Romaji wa Hontou ni [The Made Up Story]"
    assert fs.scheme_name(long, "Made Up Romaji wa Hontou ni") == "Made Up Romaji wa Hontou ni"   # = its short form
    short = "Made Up: Subtitle"                                          # not long: kept whole (colon dropped)
    assert fs.short_romaji(short) == "Made Up Subtitle"
    no_separator = "Made Up Romaji " + "Nagai " * 10
    assert fs.short_romaji(no_separator) == no_separator.strip()
    assert fs.short_romaji("12: " + "Nagai " * 10) == ("12 " + "Nagai " * 10).strip()   # a head of digits is no name


def test_the_length_rule_cuts_the_english_part_first_then_the_romaji():
    romaji, english = "R" * 100, "E" * 200
    name = fs.scheme_name(romaji, english)
    assert len(name.encode("utf-8")) <= fs.MAX_FOLDER_BYTES
    assert name.startswith("R" * 100 + " [E") and name.endswith(fs.ELLIPSIS + "]")
    assert fs.scheme_name("R" * 300) == "R" * 252 + fs.ELLIPSIS                # 255 bytes: "…" takes three
    tight = fs.fit_name("Made Up Romaji", "Made Up English", fits=lambda n: len(n) <= 16)
    assert tight == "Made Up Romaji"
    assert fs.fit_name("Made Up Romaji", None, fits=lambda n: len(n) <= 8) == "Made Up" + fs.ELLIPSIS


def test_the_windows_path_rule_leaves_room_for_a_chapter_file():
    fits = fs.windows_fits("/data/share/" + "L" * 150, "SERVER")
    name = fs.scheme_name("Made Up Romaji", "E" * 120, fits=fits)
    full = "\\\\SERVER\\share\\" + "L" * 150 + "\\" + name + "\\" + fs.PROBE_FILE
    assert len(full) <= 259 and name.startswith("Made Up Romaji [E") and name.endswith(fs.ELLIPSIS + "]")
    assert fs.windows_fits("/data/share/x", None)("N" * 255) and not fs.windows_fits("/data/share/x", None)("N" * 256)


@pytest.mark.parametrize("text, english, romaji", [
    ("The Made Up Story", True, False),
    ("I Want a Made Up Thing", True, False),
    ("Made Up Kanojo no Hanashi", False, True),
    ("Made-Up-sama wa Tensai", False, True),
    ("Made Up Leveling", False, False),                                  # neither: no telling words
    ("No Made Up Thing", False, True),                                   # "no" is a particle first
])
def test_english_or_romaji_by_their_words(text, english, romaji):
    assert fs.looks_english(text) is english
    assert fs.romaji_like(text) is romaji


@pytest.mark.parametrize("folder, romaji, alts, top, english", [
    ("Old Romaji [Made Up English]", "New Romaji", (), True, "Made Up English"),       # the owner's bracket
    ("Some Name [Made Up English]", "New Romaji", (), False, "Made Up English"),       # ... in a subseries too
    ("The Made Up Story", "Made Up Monogatari", (), True, "The Made Up Story"),        # the folder reads as English
    ("Made Up Leveling", "Made Up Hitori Level", ("Made Up Leveling",), True, "Made Up Leveling"),  # an alternative
    ("Made Up Leveling", "Made Up Hitori Level", (), True, None),        # neither English nor an alternative
    ("Made Up no Hanashi", "Made Up no Monogatari", ("Made Up no Hanashi",), True, None),   # another romanisation
    ("made-up monogatari", "Made Up Monogatari", (), True, None),        # the romaji itself
    ("Side 1 - The Made Up Story", "Made Up Gaiden", (), False, None),   # a subseries: only its bracket counts
    ("Made Up [Clench] (Some Author)", "Made Up (SOME Author)", (), True, "Clench"),   # a bracket inside the name
    ("Made Up Skill [Waza] de Katsu", "Made Up Skill Waza de Katsu", (), True, None),  # ... that is the title itself
    ("Made Up no Nakama (Banished from the Made Up Party)", "Made Up no Nakama ja Nai", (), True,
     "Banished from the Made Up Party"),                                 # an English "(...)" after the romaji
    ("The Made Up (Friend's) Story", "Made Up Monogatari", (), True, "The Made Up (Friend's) Story"),
    ("Some Author] The Made Up Story", "The Made Up Story", (), True, None),   # the rest of a cut "[Author]" tag
    ("Some Author] The Made Up Story", "Made Up Monogatari", (), True, "The Made Up Story"),
])
def test_english_from_the_folder(folder, romaji, alts, top, english):
    assert fs.english_from_folder(folder, romaji, alts, top_level=top) == english


def test_titles_for_a_folder_and_the_name_is_stable():
    t = fs.titles_for("Made Up Leveling", "Made Up Hitori Level", ("Made Up Leveling",), source="MangaPixer")
    assert (t.romaji, t.english, t.source) == ("Made Up Hitori Level", "Made Up Leveling", "MangaPixer")
    name = t.name()
    assert name == "Made Up Hitori Level [Made Up Leveling]"
    assert fs.titles_for(name, "Made Up Hitori Level", ("Made Up Leveling",)).name() == name   # renamed: unchanged
    assert fs.titles_for("Made Up Old", "Made Up Hitori Level", english="Made Up English").english == "Made Up English"
    assert fs.titles_for("Anything", None) is None and fs.titles_for("Anything", "  ") is None


def test_english_candidates_for_add_series():
    alts = ["The Made Up Story", "Made Up Monogatari", "メイドアップ", "the made-up story!", "A Made Up Tale"]
    assert fs.english_candidates(alts, "Made Up Monogatari") == ["The Made Up Story", "A Made Up Tale"]
    assert fs.english_candidates(alts, "Made Up Monogatari", limit=1) == ["The Made Up Story"]


def test_parse_folder_matches_the_scanners_reading():
    from mangalist.classifier import parse_folder_name

    for name in ("Made Up [English]", "Made Up", "Made Up [Tag] Rest", "Made Up [A] [B]"):
        assert fs.parse_folder(name) == parse_folder_name(name)
