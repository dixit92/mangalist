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
    ("Re:Made Up", "Re Made Up: The Story", "ReMade Up [The Story]"),     # colons dropped; the repeated romaji too
    ("Re:Made Up", "The Story: Re Made Up", "ReMade Up [The Story - Re Made Up]"),     # colons dropped, as animefetch
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


LONG = "Made Up Romaji wa Hontou ni: Nagai Nagai Fukudai ga Tsuzuku Monogatari desu"


def test_a_long_romaji_without_an_official_short_name_stays_whole():
    """Owner, 2026-10-16: "a long romaji stays full if official name isn't there" - never cut at a subtitle."""
    assert len(LONG) > fs.LONG_TITLE
    whole = "Made Up Romaji wa Hontou ni - Nagai Nagai Fukudai ga Tsuzuku Monogatari desu"   # the colon reads " - "
    assert fs.short_romaji(LONG) == whole
    assert fs.scheme_name(LONG, "The Made Up Story") == f"{whole} [The Made Up Story]"
    assert fs.short_romaji(LONG, "The Made Up Story", ["The Made Up Story", "Nagai Monogatari desu yo ne"]) == whole
    assert fs.short_romaji("Made Up: Subtitle", None, ["Made Up"]) == "Made Up - Subtitle"   # not long: never shortened


@pytest.mark.parametrize("alts, english, short", [
    (["Made Up Romaji"], None, "Made Up Romaji"),                              # the romaji's own head
    (["NagaMono"], "The Made Up Story", "NagaMono"),                           # one coined word
    (["Tsuzuku no Monogatari"], None, "Tsuzuku no Monogatari"),                # reads as romaji
    (["Made Up Is Long"], None, None),                                         # reads as English (is, ...)
    (["The Made Up Story"], "The Made Up Story", None),                        # the English title itself
    (["Some Odd Tale"], None, None),                                           # two words or more, nothing of the romaji
    (["MURN"], None, None),                                                    # an acronym
    (["Mono"], None, "Mono"),                                                  # four letters: enough
    (["Nai"], None, None),                                                     # fewer: not a name
    (["Made Up Romaji (AUTHOR Name)"], None, None),                            # a disambiguation, not a name
    (["Made Up Romaji wa Hontou ni Nagai Nagai Fukudai ga"], None, None),      # five words or more: not short
    (["メイドアップ"], None, None),                                              # not ASCII
    ([], "MADEUP: The Made Up Story", "Madeup"),                               # English head (BOFURI rule), title case
    ([], "Odd Tale: The Made Up Story", None),                                 # an English head unrelated to the romaji
    (["Madeup-sama: Love Story"], "Love Story", "Madeup-sama"),                # an alternative "<head>: <English>"
    (["Made Up Hero: Is It Long?"], None, None),                               # head of an English title, 3 words
])
def test_an_official_short_name_replaces_a_long_romaji(alts, english, short):
    got = fs.short_name_candidates(LONG, english, alts)
    assert (got[0] if got else None) == short
    assert fs.short_romaji(LONG, english, alts) == (short or fs.clean_part(LONG))


def test_the_short_name_choice_is_deterministic():
    """The relation to the romaji first (its head, then romaji words / a shared word, then one coined word), then the
    shortest, then alphabetical - whatever order MangaUpdates lists them in."""
    alts = ["NagaMono", "Tsuzuku no Monogatari", "Made Up Romaji", "Fukudai Hero", "Hontou Monogatari", "Zeta"]
    order = ["Made Up Romaji", "Fukudai Hero", "Hontou Monogatari", "Tsuzuku no Monogatari", "Zeta", "NagaMono"]
    assert list(fs.short_name_candidates(LONG, None, alts)) == order
    assert list(fs.short_name_candidates(LONG, None, list(reversed(alts)))) == order
    assert fs.short_name_candidates(LONG, None, ["Nagai Y", "Nagai X"])[0] == "Nagai X"   # same tier and length


def test_the_folder_name_with_a_short_name_keeps_the_english_bracket():
    alts = ["NagaMono", "The Made Up Story"]
    assert fs.scheme_name(LONG, "The Made Up Story", alt_titles=alts) == "NagaMono [The Made Up Story]"
    # the short name IS the English title (answer 86, 2026-10-16): that name alone, no bracket, never the long romaji
    assert fs.scheme_name(LONG, "NagaMono", alt_titles=["NagaMono", "Naga-Mono"]) == "NagaMono"
    titles = fs.titles_for("NagaMono [The Made Up Story]", LONG, alts)
    assert titles.name() == "NagaMono [The Made Up Story]"                     # stable: a renamed folder reads back
    assert fs.titles_for("NagaMono", LONG, alts).english is None               # the folder's name is the short name
    fits = fs.windows_fits("/data/Manga", "SERVER")
    assert fs.scheme_name(LONG, "E" * 300, fits=fits, alt_titles=alts).startswith("NagaMono [E")


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


# --- the official English title (owner, 2026-10-16, answer 76) --------------------------------------------------------

def test_the_official_english_title_comes_before_the_folders():
    t = fs.titles_for("Made Up Folder [Folder English]", "Made Up Romaji", official="The Official English")
    assert (t.english, t.english_from) == ("The Official English", fs.ENGLISH_OFFICIAL)
    assert t.name() == "Made Up Romaji [The Official English]"
    folder = fs.titles_for("Made Up Folder [Folder English]", "Made Up Romaji", english="MangaDex English")
    assert (folder.english, folder.english_from) == ("Folder English", fs.ENGLISH_FOLDER)     # then the folder's
    other = fs.titles_for("Made Up Romaji", "Made Up Romaji", english="MangaDex English")
    assert (other.english, other.english_from) == ("MangaDex English", fs.ENGLISH_OTHER)      # then MangaDex's
    none = fs.titles_for("Made Up Romaji", "Made Up Romaji", official="  ")
    assert (none.english, none.english_from) == (None, "")
    renamed = t.name()                                              # stable once renamed
    assert fs.titles_for(renamed, "Made Up Romaji", official="The Official English").name() == renamed


# --- answer 86: "As long as it doesn't become "Tearmoon Empire [Tearmoon Empire]"" ---------------------------------

LONG_ROMAJI = "Made Up Kingdom Monogatari: Dantoudai kara Hajimaru, Hime no Tensei Gyakuten Story"   # over 50


@pytest.mark.parametrize("romaji, english, alts, name", [
    ("Made Up Empire", "Made-Up EMPIRE", (), "Made Up Empire"),               # the full romaji is the English title
    ("Made Up Empire", "Made Up Empire!", (), "Made Up Empire"),              # punctuation only
    ("Made Up Empire", "Made Up Empire Story", (), "Made Up Empire [Made Up Empire Story]"),   # a longer title stays
    (LONG_ROMAJI, "Made Up Kingdom", ("Made Up Kingdom",), "Made Up Kingdom"),   # the short name IS the English
    (LONG_ROMAJI, "The Princess of Made Up Kingdom", ("Made Up Kingdom",),
     "Made Up Kingdom [The Princess of Made Up Kingdom]"),
])
def test_no_bracket_when_the_romaji_part_is_the_english_title(romaji, english, alts, name):
    assert fs.scheme_name(romaji, english, alt_titles=alts) == name


def test_the_short_name_equal_to_the_english_title_is_never_doubled():
    # an English-looking alternative title chosen as the short name, and the English title is the same title
    assert "Made Up Kingdom" in fs.short_name_candidates(LONG_ROMAJI, None, ("Made Up Kingdom",))
    for english in ("Made Up Kingdom", "made up kingdom", "Made-Up Kingdom."):
        name = fs.scheme_name(LONG_ROMAJI, english, alt_titles=("Made Up Kingdom",))
        assert "[" not in name


# --- answer 85: "Cut the English for those." ----------------------------------------------------------------------

def test_name_room_is_what_the_path_limit_leaves():
    root = "/data/Made Up Library"
    file = "Vol. 001 [Made Up Group].cbz"
    room = fs.name_room(root, file)
    assert room == fs.MAX_PATH_CHARS - len(root) - len(file) - 2
    assert fs.name_room(root + "/", file) == room                    # a trailing slash is not counted twice
    assert fs.name_room(None, file) is None and fs.name_room(root, "") is None


def test_a_path_over_255_characters_drops_the_english_part_whole():
    romaji, english = "Made Up Romaji", "The Made Up English Title"
    full = f"{romaji} [{english}]"
    root = "/data/" + "r" * 40
    file = "Ch. 0001.00 (" + "t" * (fs.MAX_PATH_CHARS - len(root) - len(full) - 2 - len("Ch. 0001.00 () [G].cbz")) \
        + ") [G].cbz"
    assert len(f"{root}/{full}/{file}") == fs.MAX_PATH_CHARS        # exactly 255: kept
    assert fs.scheme_name(romaji, english, max_chars=fs.name_room(root, file)) == full
    longer = file.replace("(t", "(tt", 1)                            # 256: the English part goes, never cut with "…"
    assert fs.scheme_name(romaji, english, max_chars=fs.name_room(root, longer)) == romaji
    assert fs.scheme_name(romaji, english, max_chars=None) == full  # no path limit known: kept
    assert fs.titles_for("Folder", romaji, official=english).name(max_chars=5) == romaji   # the romaji is never cut by it


# --- integrator, 2026-10-16: the owner's folder style, no needless renames ------------------------------------------

@pytest.mark.parametrize("romaji, english, alts, name", [
    ("Madeupgami", "Madeupgami: Stray Made Up", (), "Madeupgami [Stray Made Up]"),      # the romaji repeated: dropped
    ("Madeupgami", "MADEUPGAMI - Stray Made Up", (), "Madeupgami [Stray Made Up]"),     # any case, " - "
    ("Madeupgami", "Madeupgami ~Stray Made Up~", (), "Madeupgami [Stray Made Up]"),
    ("Madeupgami", "Madeupgami～Stray Made Up", (), "Madeupgami [Stray Made Up]"),
    ("Made-Up-sama wa Kokurasetai: Tensai-tachi no Nagai Fukudai Monogatari desu yo",
     "Made-Up-sama: Love is Made Up", ("Made-Up-sama",), "Made-Up-sama [Love is Made Up]"),   # the short name repeated
    ("Grand Made Up", "Grand Made Up Dreaming", (), "Grand Made Up [Grand Made Up Dreaming]"),   # goes on: kept whole
    ("Madeupgami", "Madeupgami:", (), "Madeupgami"),                                    # nothing left: no bracket
    ("Madeupgami", "Madeupgamiland: Stray", (), "Madeupgami [Madeupgamiland - Stray]"),   # not the same word
])
def test_a_repeated_title_is_stripped_from_the_english_part(romaji, english, alts, name):
    assert fs.scheme_name(romaji, english, alt_titles=alts) == name
    assert fs.titles_for(name, romaji, alts, official=english).name() == name       # stable once renamed


def test_the_fallback_english_is_stripped_too():
    t = fs.titles_for("Madeupgami [Madeupgami - Stray Made Up]", "Madeupgami")
    assert t.english_from == fs.ENGLISH_FOLDER and t.name() == "Madeupgami [Stray Made Up]"


def test_typography_is_folded_to_plain_ascii():
    assert fs.fold_typography("Let’s “Go” – Now—Then… End") == \
        "Let's \"Go\" - Now-Then... End"
    assert fs.scheme_name("Made Up Romaji", "Let’s Run a Made Up Inn — Again") == \
        "Made Up Romaji [Let's Run a Made Up Inn - Again]"
    assert fs.scheme_name("Made’s Romaji") == "Made's Romaji"
    folder = "Made Up Romaji [Let's Run a Made Up Inn]"                 # the owner's plain apostrophe: no rename
    assert fs.titles_for(folder, "Made Up Romaji", official="Let’s Run a Made Up Inn").name() == folder


def test_an_official_english_title_that_is_the_romaji_counts_as_none():
    t = fs.titles_for("Made Up Sumi [Snow and Made Up Ink]", "Made Up Sumi", official="Made-Up SUMI")
    assert (t.english, t.english_from) == ("Snow and Made Up Ink", fs.ENGLISH_FOLDER)
    other = fs.titles_for("Made Up Sumi", "Made Up Sumi", official="Made Up Sumi", english="Made Up MangaDex English")
    assert (other.english, other.english_from) == ("Made Up MangaDex English", fs.ENGLISH_OTHER)
    assert fs.titles_for("Made Up Sumi", "Made Up Sumi", official="Made Up Sumi").name() == "Made Up Sumi"


def test_the_folders_spelling_of_the_official_title_is_kept():
    folder = "Made Up Maou-sama! [The Made Up Devil Is a Part Timer!]"     # official: other casing and a hyphen
    t = fs.titles_for(folder, "Made Up Maou-sama!", official="The Made Up Devil is a Part-Timer!")
    assert t.english_from == fs.ENGLISH_OFFICIAL and t.name() == folder
    t = fs.titles_for("Madeupgami [Stray Made Up]", "Madeupgami", official="Madeupgami: STRAY MADE-UP")
    assert t.name() == "Madeupgami [Stray Made Up]"                       # the remainder is the folder's title too
    t = fs.titles_for("Madeupgami [Another Made Up]", "Madeupgami", official="Madeupgami: Stray Made Up")
    assert t.name() == "Madeupgami [Stray Made Up]"                       # a different title: the official one


def test_an_official_title_that_is_the_short_name_counts_as_none():
    folder = "NagaMono [NagaMono - The Made Up Story]"
    t = fs.titles_for(folder, LONG, ["NagaMono"], official="NagaMono")
    assert t.english_from == fs.ENGLISH_FOLDER and t.name() == "NagaMono [The Made Up Story]"
    assert fs.titles_for("NagaMono", LONG, ["NagaMono"], official="NagaMono").name() == "NagaMono"


@pytest.mark.parametrize("raw, clean", [
    ("Made Up Secret: Actually, I Am", "Made Up Secret - Actually, I Am"),     # a subtitle colon reads as " - "
    ("Made:Up Zero", "MadeUp Zero"),                                            # inside a word it goes (animefetch)
    ("Made Up Ii?: Gaiden", "Made Up Ii - Gaiden"),
])
def test_a_subtitle_colon_becomes_a_dash(raw, clean):
    # the owner's own folders write subtitles with " - " (2026-10-16)
    assert fs.clean_part(raw) == clean
