"""parse_name: layer precedence, per-series context, the misread classes, robustness, no Qt."""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path, PureWindowsPath

import pytest

from manga_list.parsing import (
    FMD2_CHAPTER_SCHEME,
    Kind,
    Layer,
    ParseContext,
    Template,
    parse_name,
    parse_names,
)


@pytest.mark.parametrize("name, layer", [
    ("0012 [Vol. 3 Ch. 12.5 - Title [Group]].cbz", Layer.FMD2),
    ("Some Series - 0012 [Ch. 0012].cbz", Layer.FMD2),
    ("Some Series v01 (2019) (Digital) (Grp).cbz", Layer.RELEASE),
    ("Some Series c012 (2020) (Grp).cbz", Layer.RELEASE),
    ("Vol. 01.cbz", Layer.GENERIC),
    ("Ch. 003.cbz", Layer.GENERIC),
    ("Some Series Vol. 1 Ch 3.cbz", Layer.GENERIC),
    ("01.cbz", Layer.BARE),
    ("Some Series 01.cbz", Layer.BARE),
    ("Some Series.cbz", Layer.NONE),
    ("", Layer.NONE),
])
def test_layer_order_without_context(name, layer):
    assert parse_name(name).layer is layer


def test_scheme_layer_comes_first_and_is_exact():
    ctx = ParseContext(schemes=FMD2_CHAPTER_SCHEME)
    own = parse_name("0012 [Vol. 0003 Ch. 0012.5 - Title [Group]].cbz", ctx)
    assert own.layer is Layer.SCHEME
    assert (own.index, str(own.volume), str(own.chapter), own.title, own.group) == (12, "3", "12.5", "Title", "Group")
    # FMD2's own output (unpadded chapter) does not fit the scheme exactly: layer 2 reads it.
    fmd2 = parse_name("0012 [Vol. 3 Ch. 12.5 - Title [Group]].cbz", ctx)
    assert fmd2.layer is Layer.FMD2
    assert (fmd2.index, str(fmd2.volume), str(fmd2.chapter), fmd2.title, fmd2.group) == \
        (own.index, str(own.volume), str(own.chapter), own.title, own.group)


def test_scheme_for_volumes_and_several_schemes():
    ctx = ParseContext(schemes=("%I4 [Ch. %C4%CF]", Template("%TE v%V2 (%Y) (%ED) (%G)"), "%O"))
    v = parse_name("Some Series v05 (2021) (Digital) (Grp).cbz", ctx)
    assert (v.layer, v.kind, str(v.volume), v.series, v.year, v.edition, v.group) == \
        (Layer.SCHEME, Kind.VOLUME, "5", "Some Series", 2021, "Digital", "Grp")
    c = parse_name("0003 [Ch. 0003].cbz", ctx)
    assert (c.layer, c.kind, str(c.chapter), c.index) == (Layer.SCHEME, Kind.CHAPTER, "3", 3)
    # %O is not invertible: such a name falls through to the other layers.
    assert parse_name("Some Series v05 (Digital) (Grp).cbz", ctx).layer is Layer.RELEASE


def test_scheme_without_units():
    r = parse_name("Some Series - Title.cbz", ParseContext(schemes="%T - %CT"))
    assert (r.layer, r.kind, r.series, r.title) == (Layer.SCHEME, Kind.UNKNOWN, "Some Series", "Title")


@pytest.mark.parametrize("name, ch, vol", [
    # The misread classes of PD "Evidence" / DD B3, as synthetic names.
    ("0045 [Vol. 5 Ch. 45 - Episode 3 [Group]].cbz", "45", "5"),
    ("0046 [Vol. 5 Ch. 45.5 - Extra Chapter 2 [Group]].cbz", "45.5", "5"),
    ("0310 [Vol. 30 Ch. 291.999 - Title [Group]].cbz", "291.999", "30"),
    ("0002 [Ch. 0 - 4th Year Anniversary [Group]].cbz", "0", None),
    ("0101 [Ch. 99 - Title [Group]].cbz", "99", None),           # index != chapter
    ("0060 [Ch. 60 - Back to Vol. 2 [Group]].cbz", "60", None),  # a volume token in the title
])
def test_misread_classes(name, ch, vol):
    r = parse_name(name)
    assert r.layer is Layer.FMD2
    assert str(r.chapter) == ch
    assert (str(r.volume) if r.volume else None) == vol
    assert r.chapter.start == Decimal(ch)


def test_decimals_are_exact_end_to_end():
    for text in ("12.5", "291.999", "3.10", "0.1", "100.25"):
        r = parse_name(f"0001 [Ch. {text}].cbz")
        assert str(r.chapter) == text and isinstance(r.chapter.start, Decimal)
    assert str(parse_name("Some Series c3.10 (Grp).cbz").chapter) == "3.10"


def test_ranges():
    assert str(parse_name("Some Series v01-03 (2019) (Digital) (Grp).cbz").volume) == "1-3"
    assert str(parse_name("0010 [Ch. 10-12].cbz").chapter) == "10-12"
    assert parse_name("0010 [Ch. 10-12].cbz").chapter.is_range


def test_path_like_names_use_the_file_name():
    r = parse_name(Path("some") / "dir" / "0007 [Ch. 7].cbz")
    assert (r.name, str(r.chapter)) == ("0007 [Ch. 7].cbz", "7")
    r = parse_name(PureWindowsPath(r"C:\x\0008 [Ch. 8].cbz"))
    assert str(r.chapter) == "8"


def test_a_backslash_in_a_string_name_stays_in_the_name():
    r = parse_name("0008 [Ch. 8 - A\\B [G]].cbz")
    assert (str(r.chapter), r.title, r.group) == ("8", "A\\B", "G")


def test_parse_names():
    rs = parse_names(["01.cbz", "02.cbz"], ParseContext(kind_hint="chapters"))
    assert [str(r.chapter) for r in rs] == ["1", "2"]


def test_extension_handling():
    assert parse_name("0001 [Ch. 1].CBZ").layer is Layer.FMD2
    assert parse_name("0001 [Ch. 1].cbr").layer is Layer.FMD2
    assert parse_name("0001 [Ch. 1.5]").chapter.start == Decimal("1.5")


@pytest.mark.parametrize("name", [
    "", " ", ".", ".cbz", "[", "]", "[]", "0000 [", "0001 []]", "0001 [[[]]]", "{}", "%", "v", "c", "-",
    "0001 [Ch. ]", "0001 [Vol. Ch.]", "Some Series v (Digital)", "(2019)", "9" * 400, "0001 [" + "[" * 300 + "]",
    "\x00\x1f", "Title v01-", "Title c1-c", "0001 [Ch. 1-]", "\u3000\uff11\uff12",
])
def test_never_raises(name):
    r = parse_name(name)
    assert r.name == name
    for scheme in (FMD2_CHAPTER_SCHEME, "%T v%V2{ (%Y)}"):
        parse_name(name, ParseContext(schemes=scheme, kind_hint="chapters", series_title="Title"))


def test_importing_the_parser_does_not_load_qt():
    code = "import sys, manga_list.parsing; assert 'PySide6' not in sys.modules, 'PySide6 loaded'"
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, "-c", code], check=True, cwd=root)
