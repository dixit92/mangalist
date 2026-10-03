"""mangalist.inventory: held units, exact decimals, the volume-list merge, duplicates (pure, no I/O)."""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal

import pytest

from mangalist.inventory import (
    MAX_RANGE_SPAN,
    Inventory,
    Span,
    compute_inventory,
    expand,
    format_spans,
    inventory_of_parsed,
    ranges,
    read_volume_list,
    to_number,
    units_from_parsed,
)
from mangalist.parsing import ParseContext, parse_name
from mangalist.store.units import Unit

D = Decimal


def inv(names, volume_list=None, hint=None):
    ctx = ParseContext(kind_hint=hint) if hint else None
    return inventory_of_parsed([(n, parse_name(n, ctx)) for n in names], volume_list)


# --- numbers and spans ----------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("12", D("12")), ("0012", D("12")), ("12.5", D("12.5")), ("291.999", D("291.999")), ("3.10", D("3.10")),
    (7, D("7")), (D("0004.50"), D("4.50")), ("", None), (None, None), ("abc", None), ("-1", None),
    (True, None),
])
def test_to_number_is_exact(value, expected):
    got = to_number(value)
    assert got == expected
    if expected is not None:
        assert str(got) == str(expected)          # every digit kept, no exponent


def test_floats_are_refused():
    with pytest.raises(TypeError):
        to_number(12.5)
    with pytest.raises(TypeError):
        compute_inventory([], [{"volume": 5.0, "chapters": None}])


def test_expand_ranges():
    assert expand(D("10"), D("12")) == ((D("10"), D("11"), D("12")), True)
    assert expand(D("12.5"), D("14")) == ((D("12.5"), D("13"), D("14")), True)
    assert expand(D("3"), None) == ((D("3"),), True)
    assert expand(D("12"), D("10"))[0] == (D("10"), D("11"), D("12"))      # backwards: read forwards
    nums, whole = expand(D("1"), D(MAX_RANGE_SPAN + 10))
    assert not whole and nums == (D("1"), D(MAX_RANGE_SPAN + 10))


def test_ranges_keep_fractions_apart():
    spans = ranges([D("1"), D("2"), D("3"), D("3.5"), D("4"), D("7"), D("12.5")])
    assert format_spans(spans) == "1-4, 3.5, 7, 12.5"
    assert spans[0] == Span(D("1"), D("4"))
    assert D("2") in spans[0] and "3.5" in spans[0] and "5" not in spans[0]


# --- held units from names ------------------------------------------------------------------------

def test_volumes_and_chapters_held():
    i = inv(["Title v01-03 (2019) (Digital) (Grp).cbz", "Title v05 (2020) (Digital) (Grp).cbz",
             "0040 [Vol. 6 Ch. 40 - Some Title [Team]].cbz", "0041 [Vol. 6 Ch. 41].cbz"])
    assert i.volumes == (D("1"), D("2"), D("3"), D("5"))
    assert i.volumes_text == "1-3, 5"
    assert i.chapters == (D("40"), D("41"))
    assert i.highest_volume == D("5")           # the chapters' "Vol. 6" is not a held volume
    assert i.highest_chapter == D("41")
    assert i.has_volume("02") and not i.has_volume(6)
    assert i.extras == () and i.unknown == () and i.duplicates == {}


def test_fmd2_titles_are_not_read_for_numbers():
    i = inv(["0003 [Ch. 3 - Episode 3 Is Not Ch. 99 [Group 7]].cbz",
             "0005 [Ch. 4 - Extra Chapter 2].cbz",
             "0001 [Ch. 0 - 4th Year Anniversary].cbz",
             "0300 [Ch. 291.999 - Title].cbz"])
    assert i.chapters == (D("0"), D("3"), D("4"), D("291.999"))
    assert i.highest_chapter == D("291.999")
    assert str(i.highest_chapter) == "291.999"


def test_fractional_chapters_and_extras():
    i = inv(["0012 [Ch. 12].cbz", "0013 [Ch. 12.5 - Side Story].cbz", "0014 [Ch. 13].cbz",
             "0015 [Ch. Extra - Bonus [G]].cbz", "0123 [Omake].cbz"])
    assert i.chapters == (D("12"), D("12.5"), D("13"))
    assert i.chapters_text == "12-13, 12.5"
    assert i.has_chapter("12.5") and i.has_chapter("12.50") and not i.has_chapter("12.25")
    assert i.extras == ("0015 [Ch. Extra - Bonus [G]].cbz", "0123 [Omake].cbz")


def test_chapter_ranges_are_held_whole():
    i = inv(["Title c010-012.cbz", "0020 [Ch. 20-22].cbz"])
    assert i.chapters == tuple(D(n) for n in ("10", "11", "12", "20", "21", "22"))
    assert i.chapters_text == "10-12, 20-22"


def test_volume_with_loose_chapters():
    i = inv(["Title v10 + 085-086 (2021) (Digital) (Grp).cbz"])
    assert i.volumes == (D("10"),)
    assert i.chapters == (D("85"), D("86"))


def test_bare_numbers_are_unknown_until_answered():
    names = ["01.cbz", "02.cbz", "Title 03.cbz"]
    i = inv(names)
    assert i.volumes == () and i.chapters == ()
    assert i.unknown == tuple(names)
    assert inv(names, hint="volumes").volumes == (D("1"), D("2"), D("3"))
    assert inv(names, hint="chapters").chapters == (D("1"), D("2"), D("3"))
    assert inv(names, hint="chapters").unknown == ()


def test_duplicates_list_every_archive_of_a_unit():
    i = inv(["Title c010-012.cbz", "Title c011 (Other Group).cbz", "0011 [Ch. 11 [Third]].cbz",
             "Title v02 (2019) (Digital) (A).cbz", "Title v02 (2019) (Digital) (B).cbz"])
    assert i.duplicates == {
        ("chapter", D("11")): ("Title c010-012.cbz", "Title c011 (Other Group).cbz", "0011 [Ch. 11 [Third]].cbz"),
        ("volume", D("2")): ("Title v02 (2019) (Digital) (A).cbz", "Title v02 (2019) (Digital) (B).cbz"),
    }
    assert i.files[("chapter", D("10"))] == ("Title c010-012.cbz",)
    assert i.chapters.count(D("11")) == 1                          # held once


def test_equal_numbers_are_one_unit_first_spelling_kept():
    units = [Unit(rel_path="a.cbz", kind="chapter", ch_from="12.50", ch_to="12.50"),
             Unit(rel_path="b.cbz", kind="chapter", ch_from="12.5", ch_to="12.5")]
    i = compute_inventory(units)
    assert len(i.chapters) == 1 and str(i.chapters[0]) == "12.50"
    assert i.duplicates == {("chapter", D("12.5")): ("a.cbz", "b.cbz")}


# --- the volume-list merge ------------------------------------------------------------------------

VOLUME_LIST = [
    {"volume": "1", "chapters": {"from": "1", "to": "8"}},
    {"volume": "2", "chapters": {"from": "9", "to": "17"}},
    {"volume": "3", "chapters": None},
    {"volume": "5", "chapters": {"from": "38", "to": "46"}, "title": "ignored"},
]


def test_a_volume_collects_its_chapters():
    i = inv(["Title v01 (2019) (Digital) (G).cbz", "Title v02 (2019) (Digital) (G).cbz",
             "Title v05 (2019) (Digital) (G).cbz", "0047 [Ch. 47].cbz"], VOLUME_LIST)
    assert i.volume_list_given
    # Intervals merge only where they overlap: 8 and 9 stay apart (8.5 is in neither volume).
    assert i.covered == (Span(D("1"), D("8")), Span(D("9"), D("17")), Span(D("38"), D("46")))
    assert not i.has_chapter("8.5")
    assert i.coverage[D("5")] == Span(D("38"), D("46"))
    assert i.has_chapter(40) and i.has_chapter("8") and i.has_chapter("47")
    assert not i.has_chapter("18")
    assert i.held_chapters_text == "1-17, 38-47"
    assert i.highest_chapter == D("47")
    assert i.volumes_unknown_coverage == ()


def test_fractional_chapter_inside_a_covered_volume():
    i = inv(["Title v05 (2019) (Digital) (G).cbz"], VOLUME_LIST)
    assert i.has_chapter("45.5")                  # a covered span is an interval
    assert not i.has_chapter("46.5")


def test_unknown_coverage():
    i = inv(["Title v03 (2019) (Digital) (G).cbz", "Title v04 (2019) (Digital) (G).cbz",
             "Title v05 (2019) (Digital) (G).cbz"], VOLUME_LIST)
    assert i.volumes_unknown_coverage == (D("3"), D("4"))   # null in the list / not in the list
    assert i.coverage == {D("3"): None, D("4"): None, D("5"): Span(D("38"), D("46"))}
    assert not i.has_chapter("1")


def test_without_a_volume_list_every_volume_has_unknown_coverage():
    i = inv(["Title v01 (2019) (Digital) (G).cbz", "0010 [Ch. 10].cbz"])
    assert not i.volume_list_given and i.coverage == {} and i.covered == ()
    assert i.volumes_unknown_coverage == (D("1"),)
    assert not i.has_chapter("2")


def test_loose_chapters_a_volume_collects_count_once():
    i = inv(["Title v05 (2019) (Digital) (G).cbz", "0040 [Ch. 40].cbz", "0040 [Ch. 40.5].cbz",
             "0050 [Ch. 50].cbz"], VOLUME_LIST)
    assert i.loose_in_volumes == (D("40"), D("40.5"))
    assert i.held_chapters_text == "38-46, 50"


def test_volume_range_file_with_a_list():
    i = inv(["Title v01-03 (2019) (Digital) (G).cbz"], VOLUME_LIST)
    assert i.volumes == (D("1"), D("2"), D("3"))
    assert i.held_chapters_text == "1-17"
    assert i.volumes_unknown_coverage == (D("3"),)


def test_volume_list_reading_is_tolerant():
    listed, notes = read_volume_list([
        {"volume": "7", "chapters": {"from": "50", "to": "58"}},
        {"volume": "7", "chapters": {"from": "1", "to": "2"}},       # listed twice: first wins
        {"volume": None},
        "not an object",
        {"volume": "8", "chapters": {"from": "60"}},                   # one end only
        {"volume": "9", "chapters": {"to": "70.5"}},
        {"volume": "10", "chapters": {"from": "80", "to": "79"}},      # backwards: unknown
        {"volume": 11, "chapters": {"from": 90, "to": "99"}},          # ints are exact
    ])
    assert listed == {D("7"): Span(D("50"), D("58")), D("8"): Span(D("60"), D("60")),
                      D("9"): Span(D("70.5"), D("70.5")), D("10"): None, D("11"): Span(D("90"), D("99"))}
    assert len(notes) == 3
    i = inv(["Title v07 (2019) (Digital) (G).cbz"], [{"volume": None}, {"volume": "7", "chapters": None}])
    assert i.notes and i.volumes_unknown_coverage == (D("7"),)


# --- unit rows ------------------------------------------------------------------------------------

def test_units_from_parsed_shapes():
    def rows(name, hint=None):
        return [(u.kind, u.vol_from, u.vol_to, u.ch_from, u.ch_to, u.num_from, u.num_to, u.parser)
                for u in units_from_parsed(name, parse_name(name, ParseContext(kind_hint=hint)))]

    assert rows("Title v01-03 (2019) (Digital) (G).cbz") == [("volume", "1", "3", None, None, None, None, "release")]
    assert rows("0012 [Vol. 3 Ch. 12.5 - T [G]].cbz") == [("chapter", "3", "3", "12.5", "12.5", None, None, "fmd2")]
    assert rows("Title v10 + 085-086 (2021) (Digital) (G).cbz") == [
        ("volume", "10", "10", None, None, None, None, "release"),
        ("chapter", None, None, "85", "86", None, None, "release")]
    assert rows("0123 [Omake].cbz") == [("extra", None, None, None, None, None, None, "fmd2")]
    assert rows("07.cbz") == [("unknown", None, None, None, None, "7", "7", "bare")]
    assert rows("07.cbz", "volumes") == [("volume", "7", "7", None, None, None, None, "bare")]
    assert rows("Readme.cbz") == [("unknown", None, None, None, None, None, None, "none")]
    u = units_from_parsed("Sub/0012 [Ch. 12 - Title [Grp]].cbz", parse_name("0012 [Ch. 12 - Title [Grp]].cbz"), 99)[0]
    assert (u.rel_path, u.group_name, u.title, u.idx, u.file_size) == (
        "Sub/0012 [Ch. 12 - Title [Grp]].cbz", "Grp", "Title", "12", 99)


def test_units_are_valid_store_rows():
    for name in ["Title v01-03 (2019) (Digital) (G).cbz", "0012 [Vol. 3 Ch. 291.999].cbz", "07.cbz",
                 "0123 [Omake].cbz", "Title v10 + 085-086.cbz", "nothing here.cbz"]:
        for u in units_from_parsed(name, parse_name(name)):
            assert u.normalized().kind == u.kind


def test_compute_inventory_from_stored_rows():
    units = [Unit(rel_path="v01.cbz", kind="volume", vol_from="1", vol_to="1"),
             Unit(rel_path="c009.cbz", kind="chapter", ch_from="9", ch_to="9", vol_from="2", vol_to="2"),
             Unit(rel_path="x.cbz", kind="oneshot"), Unit(rel_path="y.cbz", kind="unknown", num_from="4"),
             Unit(rel_path="z.cbz", kind="volume")]               # a volume row without a number
    i = compute_inventory(units)
    assert i.volumes == (D("1"),) and i.chapters == (D("9"),)
    assert i.extras == ("x.cbz",) and i.unknown == ("y.cbz", "z.cbz")
    assert isinstance(i, Inventory) and not i.is_empty and compute_inventory([]).is_empty


def test_no_float_anywhere_in_an_inventory():
    i = inv(["Title v01-02 (2019) (Digital) (G).cbz", "0012 [Ch. 12.5].cbz"], VOLUME_LIST)
    values = list(i.volumes) + list(i.chapters) + [i.highest_volume, i.highest_chapter]
    values += [s.start for s in i.covered] + [s.end for s in i.covered]
    assert all(isinstance(v, Decimal) for v in values)


def test_inventory_module_does_not_need_qt():
    code = ("import sys, mangalist.inventory, mangalist.scanner, mangalist.store; "
            "assert 'PySide6' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], check=True)
