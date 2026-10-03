"""Result model: decimal-exact unit numbers, ranges, kinds."""

from __future__ import annotations

from decimal import Decimal

import pytest

from manga_list.parsing import Kind, ParsedName, UnitRange, to_decimal


@pytest.mark.parametrize("text, expected", [
    ("12", "12"), ("0012", "12"), ("12.5", "12.5"), ("0012.5", "12.5"), ("291.999", "291.999"),
    ("3.10", "3.10"), ("0", "0"), ("0000", "0"), ("0.5", "0.5"), ("10.50", "10.50"),
])
def test_to_decimal_keeps_every_decimal_digit(text, expected):
    d = to_decimal(text)
    assert isinstance(d, Decimal)
    assert str(d) == expected


@pytest.mark.parametrize("text", [None, "", "  ", "abc", "nan", "inf"])
def test_to_decimal_rejects_non_numbers(text):
    assert to_decimal(text) is None


def test_unit_range_single_and_range():
    one = UnitRange.of("0012.5")
    assert one.start == one.end == Decimal("12.5")
    assert not one.is_range
    assert str(one) == "12.5"
    assert one.whole == 12 and one.fraction == ".5"
    rng = UnitRange.of("10", "12")
    assert rng.is_range and str(rng) == "10-12"
    assert UnitRange.of("3.10").fraction == ".10"
    assert UnitRange.of("7").fraction == ""


def test_unit_range_is_never_float():
    r = UnitRange.of("291.999")
    assert type(r.start) is Decimal and type(r.end) is Decimal
    assert r.start != Decimal(291.999)          # the float would not be exact


def test_unit_range_maybe():
    assert UnitRange.maybe(None) is None
    assert UnitRange.maybe("4", None) == UnitRange.of("4")
    with pytest.raises(ValueError):
        UnitRange.of("x")


@pytest.mark.parametrize("kind, legacy", [
    (Kind.CHAPTER, "chapter"), (Kind.BOTH, "chapter"), (Kind.VOLUME, "volume"), (Kind.UNKNOWN, "ambiguous"),
])
def test_legacy_kind(kind, legacy):
    assert ParsedName(name="x", kind=kind).legacy_kind == legacy


def test_is_fraction_and_has_units():
    assert ParsedName(name="x", chapter=UnitRange.of("12.5")).is_fraction
    assert not ParsedName(name="x", chapter=UnitRange.of("12")).is_fraction
    assert not ParsedName(name="x").has_units
    assert ParsedName(name="x", number=UnitRange.of("1")).has_units
