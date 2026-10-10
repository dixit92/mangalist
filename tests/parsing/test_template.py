"""The minimal naming-template engine: lexing, render, exact parse-back, round trips."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mangalist.parsing import (
    FMD2_CHAPTER_SCHEME,
    FMD2_VOLUME_SCHEME,
    ParseContext,
    TOKENS,
    Template,
    TemplateError,
    TokenSpec,
    UnitRange,
    compile_template,
    parse_name,
    register_token,
    windows_safe,
)

FMD2 = compile_template(FMD2_CHAPTER_SCHEME)


@pytest.mark.parametrize("values, expected", [
    ({"index": 12, "volume": 3, "chapter": Decimal("12.5"), "title": "Some Title", "group": "Team"},
     "0012 [Vol. 0003 Ch. 0012.5 - Some Title [Team]]"),
    ({"index": 1, "chapter": "1"}, "0001 [Ch. 0001]"),
    ({"index": 7, "chapter": "3.10", "group": "G"}, "0007 [Ch. 0003.10 [G]]"),
    ({"index": 12345, "chapter": "291.999"}, "12345 [Ch. 0291.999]"),
    ({"index": 2, "volume": "1", "chapter": "0", "title": "Prologue"}, "0002 [Vol. 0001 Ch. 0000 - Prologue]"),
])
def test_render_default_scheme(values, expected):
    assert FMD2.render(values) == expected


def test_optional_group_dropped_when_a_token_inside_is_empty():
    t = compile_template("%T{ v%V2}{ (%Y)}")
    assert t.render({"series": "Title"}) == "Title"
    assert t.render({"series": "Title", "volume": 4}) == "Title v04"
    assert t.render({"series": "Title", "volume": 4, "year": 2020}) == "Title v04 (2020)"
    assert t.render({"series": "Title", "volume": 4, "year": ""}) == "Title v04"


def test_nested_groups():
    t = compile_template("%T{ - %CT{ [%G]}}")
    assert t.render({"series": "A", "title": "B", "group": "C"}) == "A - B [C]"
    assert t.render({"series": "A", "title": "B"}) == "A - B"
    assert t.render({"series": "A", "group": "C"}) == "A"
    assert t.parse("A - B [C]") == {"series": "A", "title": "B", "group": "C"}
    assert t.parse("A") == {"series": "A"}


def test_literal_braces_and_percent():
    t = compile_template("{{%T}} 100%% v%V")
    assert t.render({"series": "X", "volume": 2}) == "{X} 100% v2"
    assert t.parse("{X} 100% v2") == {"series": "X", "volume": "2"}


@pytest.mark.parametrize("bad", ["%Q", "{%T", "%T}", "{ {%T}", "%"])
def test_bad_templates(bad):
    with pytest.raises(TemplateError):
        Template(bad)


def test_longest_token_name_wins():
    t = compile_template("%C4%CF - %CT - %TE")
    assert t.parse("0012.5 - A Title - English") == {
        "chapter_whole": "0012", "chapter_fraction": ".5", "title": "A Title", "series_english": "English"}


@pytest.mark.parametrize("stem, ok", [
    ("0012 [Ch. 0012]", True),
    ("12345 [Ch. 12345]", True),       # wider than the width, no leading zero
    ("0012 [Ch. 12]", False),          # narrower than the width
    ("00012 [Ch. 0012]", False),       # wider WITH a leading zero: not this template's output
    ("0012 [Ch. 0012 - ]", False),     # empty title
    ("0012 [Ch. 0012 -  Title]", False),  # text tokens never start with a space
    ("0012 [Vol. 0003 Ch. 0012]", True),
    ("0012 [Ch. 0012.5 [Group]]", True),
])
def test_parse_back_is_exact(stem, ok):
    assert (FMD2.parse(stem) is not None) is ok


def test_parse_back_fields():
    assert FMD2.parse("0012 [Vol. 0003 Ch. 0012.5 - Episode 3 [Team [X]]]") == {
        "index": "0012", "volume": "0003", "chapter_whole": "0012", "chapter_fraction": ".5",
        "title": "Episode 3", "group": "Team [X]"}


def test_title_with_brackets_and_a_group():
    assert FMD2.parse("0001 [Ch. 0001 - Title [Part 2] [Group]]")["title"] == "Title [Part 2]"
    assert FMD2.parse("0001 [Ch. 0001 - Title [Part 2] [Group]]")["group"] == "Group"


ROUND_TRIP = [
    {"index": 12, "volume": "3", "chapter": "12.5", "title": "Episode 3", "group": "Team [X]"},
    {"index": 1, "chapter": "1"},
    {"index": 99, "chapter": "291.999", "title": "Extra Chapter 2"},
    {"index": 4, "volume": "10.5", "chapter": "3.10", "group": "G 2"},
    {"index": 5, "chapter": "0", "title": "4th Year Anniversary"},
    {"index": 6, "chapter": "7", "title": "Vol. 2 and Ch. 9 in a title"},
]


@pytest.mark.parametrize("values", ROUND_TRIP)
def test_round_trip_through_parse_name(values):
    stem = FMD2.render(values)
    r = parse_name(stem + ".cbz", ParseContext(schemes=FMD2_CHAPTER_SCHEME))
    assert r.layer.value == "scheme"
    assert r.index == values["index"]
    assert str(r.chapter) == str(Decimal(values["chapter"]))
    assert (str(r.volume) if r.volume else None) == values.get("volume")
    assert r.title == values.get("title")
    assert r.group == values.get("group")


def test_render_is_windows_safe_and_parse_back_reads_the_safe_text():
    stem = FMD2.render({"index": 3, "chapter": "3", "title": 'What? A "Test": 1/2 <x> | y*', "group": "A\\B"})
    assert not any(c in stem for c in '<>:"/\\|?*')
    assert FMD2.parse(stem)["title"] == "What_ A _Test__ 1_2 _x_ _ y_"
    assert windows_safe("a:b", replacement="-") == "a-b"
    raw = FMD2.render({"index": 3, "chapter": "3", "title": "a:b"}, sanitize=None)
    assert raw == "0003 [Ch. 0003 - a:b]"


def test_duplicate_tokens_must_agree():
    # The phase-2 C1 preset: the chapter number doubles as the index.
    t = compile_template("%C4 [{Vol. %V4 }Ch. %C%CF{ - %CT}{ [%G]}]")
    assert t.render({"chapter": "12.5", "volume": 2}) == "0012 [Vol. 0002 Ch. 12.5]"
    assert t.parse("0012 [Vol. 0002 Ch. 12.5]")["chapter_whole"] == "0012"
    assert t.parse("0013 [Ch. 12.5]") is None


def test_volume_presets():
    assert not compile_template(FMD2_VOLUME_SCHEME).invertible
    t = compile_template("%TE v%V2{ (%Y)} (%ED) (%G)")
    stem = t.render({"series_english": "Some Series", "volume": 5, "year": 2021, "edition": "Digital",
                     "group": "Grp"})
    assert stem == "Some Series v05 (2021) (Digital) (Grp)"
    assert t.parse(stem) == {"series_english": "Some Series", "volume": "05", "year": "2021",
                             "edition": "Digital", "group": "Grp"}
    assert t.parse("Some Series v05 (Digital-Compilation) (Grp)")["edition"] == "Digital-Compilation"


def test_register_token_extends_the_table():
    spec = TokenSpec("ZQ", "zq", lambda w: r"[a-z]+", lambda v, w: str(v.get("zq") or ""))
    register_token(spec)
    try:
        t = compile_template("%ZQ-%C3")
        assert t.render({"zq": "abc", "chapter": 7}) == "abc-007"
        assert t.parse("abc-007") == {"zq": "abc", "chapter_whole": "007"}
        with pytest.raises(TemplateError):
            register_token(spec)
        with pytest.raises(TemplateError):
            register_token(TokenSpec("Z9", "z", lambda w: "", lambda v, w: ""))
    finally:
        del TOKENS["ZQ"]


def test_compile_template_caches():
    assert compile_template("%T v%V2") is compile_template("%T v%V2")
    t = Template("%T")
    assert compile_template(t) is t
    assert t == Template("%T") and hash(t) == hash(Template("%T"))


# --- MangaList's own scheme tokens (renamer cycle, owner 2026-10-10) ---------------------------------------------

@pytest.mark.parametrize("values, expected", [
    ({"chapter": "102"}, "Ch. 0102.00"),
    ({"chapter": Decimal("10.5")}, "Ch. 0010.50"),
    ({"chapter": "3.10"}, "Ch. 0003.10"),
    ({"chapter": "291.999"}, "Ch. 0291.999"),
    ({"chapter": "10", "chapter_end": "12"}, "Ch. 0010.00-0012.00"),
    ({"chapter": UnitRange.of("10", "12.5")}, "Ch. 0010.00-0012.50"),
])
def test_two_decimal_chapter_tokens(values, expected):
    assert compile_template("Ch. %CN4{-%CE4}").render(values) == expected


def test_volume_end_token():
    t = compile_template("%T - Vol. %V3{-%VE3}")
    assert t.render({"series": "S", "volume": 1, "volume_end": 3}) == "S - Vol. 001-003"
    assert t.render({"series": "S", "volume": UnitRange.of(1, 3)}) == "S - Vol. 001-003"
    assert t.render({"series": "S", "volume": 1}) == "S - Vol. 001"
    assert t.parse("S - Vol. 001-003") == {"series": "S", "volume": "001", "volume_end": "003"}


def test_two_decimal_tokens_parse_exactly():
    t = compile_template("Ch. %CN4{-%CE4}")
    assert t.parse("Ch. 0102.00") == {"chapter": "0102.00"}
    assert t.parse("Ch. 0010.00-0012.50") == {"chapter": "0010.00", "chapter_end": "0012.50"}
    for stem in ("Ch. 0102", "Ch. 0102.0", "Ch. 102.00", "Ch. 00102.00"):
        assert t.parse(stem) is None, stem
    assert t.parse("Ch. 10200.00") == {"chapter": "10200.00"}


def test_new_tokens_leave_the_old_ones_alone():
    t = compile_template(FMD2_CHAPTER_SCHEME)
    assert t.render({"index": 1, "chapter": "12.5", "title": "CT"}) == "0001 [Ch. 0012.5 - CT]"
    assert compile_template("%C%CF").render({"chapter": "3.10"}) == "3.10"
