"""The filter box's other names (``mangalist.alias_search``): MangaPixer's name key, typed field and alt-title rules, the
creator match, and the filter that puts them together. No Qt, no network. The parity cases are taken from MangaPixer's
own tests (``DeclaredFactsTests``, ``FacetSearchSyntaxTests``, ``AltTitleSearchTests``); the names are made up."""

from __future__ import annotations

import pytest

from mangalist.alias_search import (
    Creator, CreatorIndex, ParsedQuery, TextFilter, alt_title_hit, creator_hint, creator_hit, creator_words,
    distinct_titles, hint_text, name_key, name_keys, parse_query,
)

# --- the name key (DeclaredFactsComparer.NameKey / NameKeys) -----------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("ODA, Eiichiro", "eiichiro oda"),
    ("Oda  Eiichiro", "eiichiro oda"),
    ("Émile", "emile"),
    ("---", ""),
    ("", ""),
    (None, ""),
    ("Eiichiro Oda", "eiichiro oda"),
    ("Naoki  URASAWA!", "naoki urasawa"),
    ("Pen Kaya (Pen Kana)", "kana kaya pen pen"),
])
def test_the_name_key_is_folded_and_order_free(name, expected):
    assert name_key(name) == expected


def test_a_name_in_brackets_is_every_name_in_it():
    # MangaUpdates writes an author's alias in brackets: "Pen Kaya (Pen Kana)" is both names (MangaPixer's CreatorsConflict)
    assert set(name_keys("Pen Kaya (Pen Kana)")) == {"kana kaya pen pen", "kaya pen", "kana pen"}
    assert set(name_keys("Pen Kaya [Pen Kana]")) == set(name_keys("Pen Kaya (Pen Kana)"))
    assert name_keys("Eiichiro Oda") == ("eiichiro oda",)
    assert name_keys("---") == ()


def test_a_spelling_variant_is_another_name():
    assert name_key("Oda Eiichirou") != name_key("Oda Eiichiro")        # no fuzzy matching


# --- the typed field (FacetSearchSyntax.Parse) -------------------------------------------------------------------


def shape(text):
    parsed = parse_query(text)
    return list(parsed.creators), parsed.free


@pytest.mark.parametrize("text, creators, free", [
    ('creator:"Naoki Urasawa"', ["Naoki Urasawa"], ""),
    ("creator:Urasawa", ["Urasawa"], ""),
    ('Creator:"Naoki Urasawa" monster', ["Naoki Urasawa"], "monster"),
    ("creator: Urasawa", ["Urasawa"], ""),
    ('creator: "Naoki Urasawa" x', ["Naoki Urasawa"], "x"),
    ("creators:Urasawa", ["Urasawa"], ""),
    ('creator:"unterminated quote', ["unterminated quote"], ""),
    ("pluto creator:Urasawa", ["Urasawa"], "pluto"),
    ('creator:Urasawa creator:"Naoki Kawaguchi"', ["Urasawa", "Naoki Kawaguchi"], ""),
])
def test_typed_creator_fields(text, creators, free):
    assert shape(text) == (creators, free)


@pytest.mark.parametrize("text, free", [
    ("Re:Zero", "Re:Zero"),
    ("series:Pluto", "series:Pluto"),                           # only creator: is a typed field here
    ("genre:mystery", "genre:mystery"),                          # MangaPixer's other fields are not MangaList's
    ("creator:", "creator:"),
    ('creator:""', 'creator:""'),
    ('"quoted text" plain', '"quoted text" plain'),
    ("   ", ""),
    ("Pluto  Saga", "Pluto Saga"),
    ("", ""),
    (None, ""),
])
def test_everything_else_stays_free_text(text, free):
    assert shape(text) == ([], free)


def test_at_most_ten_fields_keep_the_rest_as_text():
    parsed = parse_query(" ".join(f"creator:c{i}" for i in range(1, 13)))
    assert len(parsed.creators) == 10
    assert parsed.free == "creator:c11 creator:c12"


def test_an_overlong_value_is_text():
    value = "a" * 201
    assert parse_query("creator:" + value) == ParsedQuery((), "creator:" + value)


# --- alternative titles (series_search: a trigram table, a literal case-insensitive substring) -------------------------


TITLES = ("Delicious in Dungeon", "Delicious Dungeon Extra Long Alias", "Dungeon Meshi", "ダンジョン飯")


def test_an_alt_title_is_found_by_a_case_insensitive_substring():
    assert alt_title_hit("meshi", TITLES) == "Dungeon Meshi"
    assert alt_title_hit("ダンジョン", TITLES) == "ダンジョン飯"
    assert alt_title_hit("nothing", TITLES) is None


def test_the_shortest_matching_title_is_the_one_shown():
    # ORDER BY length(title), title
    assert alt_title_hit("delicious", TITLES) == "Delicious in Dungeon"
    assert alt_title_hit("dungeon", ("Zed Dungeon", "A Dungeon", "B Dungeon")) == "A Dungeon"


def test_fewer_than_three_characters_match_no_alt_title():
    assert alt_title_hit("du", TITLES) is None                  # trigram: a shorter query matches nothing
    assert alt_title_hit("dun", TITLES) == "Dungeon Meshi"
    assert alt_title_hit("飯", TITLES) is None


def test_the_match_folds_case_only():
    assert alt_title_hit("cafe", ("Café Latte",)) is None       # not diacritics, as in the trigram tokenizer
    assert alt_title_hit("café", ("CAFÉ LATTE",)) == "CAFÉ LATTE"
    assert alt_title_hit("in  dungeon", TITLES) is None         # nor spacing
    assert alt_title_hit("straße".casefold(), ("Die STRASSE",)) == "Die STRASSE"      # casefold: ß is ss


def test_the_folded_titles_a_row_keeps_give_the_same_answer():
    folded = tuple(t.casefold() for t in TITLES)
    assert alt_title_hit("meshi", TITLES, folded) == alt_title_hit("meshi", TITLES)


def test_distinct_titles_drops_empty_and_repeated():
    assert distinct_titles([" A ", "a", "", None, "B"]) == ("A", "B")


# --- creators ---------------------------------------------------------------------------------------------------


URASAWA = Creator.of("Naoki Urasawa", ["浦沢直樹", "Urasawa Naoki"])
KAZU = Creator.of("DUBU (Redice Studio)", ["JANG Seong-rak"])


def hit(typed, creators=(URASAWA, KAZU)):
    got = creator_hit(creator_words(typed), creators)
    return None if got is None else (got[0].name, got[1])


def test_a_creator_is_found_by_the_whole_name_in_any_order_or_form():
    assert hit("Naoki Urasawa") == ("Naoki Urasawa", "Naoki Urasawa")
    assert hit("URASAWA, Naoki") == ("Naoki Urasawa", "Naoki Urasawa")
    assert hit("naoki   urasawa") == ("Naoki Urasawa", "Naoki Urasawa")


def test_a_word_or_the_start_of_a_word_finds_the_creator_as_typed():
    assert hit("Urasawa")[0] == "Naoki Urasawa"
    assert hit("uras")[0] == "Naoki Urasawa"
    assert hit("urasawa nao")[0] == "Naoki Urasawa"
    assert hit("sawa") is None                                   # a word's start, not any part
    assert hit("Urasawa Kenji") is None
    assert hit("Someone Else") is None


def test_a_creator_is_found_by_their_other_names():
    assert hit("浦沢直樹") == ("Naoki Urasawa", "浦沢直樹")
    assert hit("Jang Seong rak") == ("DUBU (Redice Studio)", "JANG Seong-rak")
    # the credited spelling and the author record's main name both count
    assert hit("dubu")[0] == "DUBU (Redice Studio)"
    assert hit("redice studio")[0] == "DUBU (Redice Studio)"


def test_a_value_without_letters_matches_nothing():
    assert creator_words("---") == ()
    assert hit("---") is None
    assert hit("") is None


def test_the_hint_names_the_creator_and_the_alias_that_matched():
    assert creator_hint(URASAWA, "Naoki Urasawa") == "by Naoki Urasawa"
    assert creator_hint(URASAWA, "浦沢直樹") == "by Naoki Urasawa (aka 浦沢直樹)"


def test_the_index_builds_creators_from_the_stored_records():
    index = CreatorIndex(
        {"1": {"at": "x", "a": [[7, "Naoki Urasawa", "author"], [None, "Free Artist", "artist"]]},
         "2": {"at": "x", "a": []}},
        {"7": {"at": "x", "n": ["Urasawa Naoki", "浦沢直樹"]}})
    assert [c.name for c in index.creators_of(1)] == ["Naoki Urasawa", "Free Artist"]
    assert "浦沢直樹" in index.creators_of("1")[0].names
    assert index.creators_of(2) == () and index.known(2)
    assert index.creators_of(3) == () and not index.known(3)
    assert index.creators_of(None) == () and not index.known(None)
    assert index.known_count([1, 2, 3]) == 2
    assert not CreatorIndex() and index


def test_a_broken_stored_credit_is_skipped():
    index = CreatorIndex({"1": {"a": [None, [], [5], [5, ""], [5, "Real Name"]]}})
    assert [c.name for c in index.creators_of(1)] == ["Real Name"]


# --- the filter box ------------------------------------------------------------------------------------------------


def test_a_row_passes_by_its_own_titles_without_a_hint():
    flt = TextFilter.of("amber")
    assert flt.match("amber garden\nthe amber garden\nvolumes") == ()
    assert flt.match("silver\n\nvolumes", TITLES) is None


def test_a_row_passing_only_by_an_alt_title_says_so():
    flt = TextFilter.of("Delicious")
    assert flt.match("dungeon meshi\n\nvolumes", TITLES, [t.casefold() for t in TITLES]) == ("aka Delicious in Dungeon",)


def test_a_row_without_alt_titles_is_found_by_its_own_titles_only():
    assert TextFilter.of("meshi").match("dungeon meshi\n\nvolumes") == ()      # nothing known, nothing hidden
    assert TextFilter.of("delicious").match("dungeon meshi\n\nvolumes") is None


def test_creator_values_must_all_be_found_among_the_creators():
    credits = lambda: (URASAWA, KAZU)           # noqa: E731
    assert TextFilter.of("creator:Urasawa").match("monster", creators=credits) == ("by Naoki Urasawa",)
    assert TextFilter.of('creator:浦沢直樹').match("monster", creators=credits) == ("by Naoki Urasawa (aka 浦沢直樹)",)
    assert TextFilter.of("creator:Urasawa creator:Jang").match("m", creators=credits) == (
        "by Naoki Urasawa", "by DUBU (Redice Studio) (aka JANG Seong-rak)")
    assert TextFilter.of("creator:Urasawa creator:Nobody").match("m", creators=credits) is None
    assert TextFilter.of("creator:Urasawa").match("m", creators=lambda: ()) is None     # no creators known: no match


def test_free_text_and_creator_both_apply():
    credits = lambda: (URASAWA,)                # noqa: E731
    flt = TextFilter.of("creator:Urasawa monster")
    assert flt.match("monster\n\nvolumes", creators=credits) == ("by Naoki Urasawa",)
    assert flt.match("pluto\n\nvolumes", creators=credits) is None
    both = TextFilter.of("creator:Urasawa meshi")
    assert both.match("pluto", ["Dungeon Meshi"], ["dungeon meshi"], credits) == ("aka Dungeon Meshi", "by Naoki Urasawa")


def test_creators_are_asked_only_when_one_was_typed():
    def boom():
        raise AssertionError("creators asked")

    assert TextFilter.of("monster").match("monster", creators=boom) == ()
    assert TextFilter.of("monster").match("pluto", creators=boom) is None


def test_an_empty_filter_passes_everything_and_a_plain_search_is_unchanged():
    assert TextFilter.of("").empty and TextFilter.of("   ").empty
    assert TextFilter.of("").match("anything") == ()
    assert TextFilter.of("  Amber ").needle == "amber"
    assert TextFilter.of("Re:Zero").needle == "re:zero"                          # not a field


def test_hints_are_joined_in_one_line():
    assert hint_text(["aka X", "by Y"]) == "aka X  ·  by Y"
    assert hint_text([]) == ""


# --- the creators MangaPixer exports (record.creators) ------------------------------------------------------------


def test_the_exported_creators_are_read_with_their_other_names():
    from mangalist.knowledge import from_mangapixer_item

    from ..states.helpers import item

    creators = [{"name": "Naoki Urasawa", "role": "Author, Artist", "muAuthorId": 7, "otherNames": ["浦沢直樹", " "]},
                {"name": "Free Artist", "role": "Artist", "muAuthorId": None, "otherNames": []},
                {"name": "", "role": "Artist"}, "junk", {"name": "No Others", "role": "Author"}]
    knowledge = from_mangapixer_item(item(record__creators=creators))
    assert knowledge.creators == (("Naoki Urasawa", ("浦沢直樹",)), ("Free Artist", ()), ("No Others", ()))
    assert from_mangapixer_item(item(record__creators=[])).creators == ()          # exported, none credited
    assert from_mangapixer_item(item()).creators is None                           # an older MangaPixer: not exported
    assert from_mangapixer_item(item(record__creators="x")).creators is None
    made = alias.creators_from_export(knowledge.creators)
    assert hit_names(made, "浦沢直樹") == "Naoki Urasawa"


def hit_names(creators, typed):
    got = creator_hit(creator_words(typed), creators)
    return None if got is None else got[0].name


import mangalist.alias_search as alias  # noqa: E402
