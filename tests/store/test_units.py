"""Units: exact decimal strings, ranges, replace per archive."""

from __future__ import annotations

import pytest

from manga_list.store import SeriesSeen, Unit, UnitError
from manga_list.store.units import exact_number


@pytest.mark.parametrize("value, expected", [("12", "12"), ("0003", "3"), ("3.99", "3.99"), ("0.5", "0.5"),
                                             (7, "7"), ("002.10", "2.10"), (None, None)])
def test_exact_number(value, expected):
    assert exact_number(value) == expected


@pytest.mark.parametrize("bad", [1.5, "-1", "1e3", "abc", "1.", ".5", "1,5"])
def test_inexact_or_invalid_numbers_are_refused(bad):
    with pytest.raises(UnitError):
        exact_number(bad)


@pytest.fixture
def series_id(db, library):
    root = db.add_root(str(library))
    db.record_scan(root.id, library, [SeriesSeen("Series A", "fp", 2)])
    return db.get_series(root.id, "Series A").id


def test_replace_and_list(db, series_id):
    vol = "Series A v01 (Group).cbz"
    db.replace_units(series_id, vol, [
        Unit(rel_path=vol, kind="volume", vol_from="01", ch_from="1", ch_to="8", group_name="Group", idx="01"),
    ])
    db.replace_units(series_id, "Chapters/c009.5.cbz", [
        Unit(rel_path="Chapters/c009.5.cbz", kind="extra", ch_from="9.5", title="Side story"),
    ])
    units = db.list_units(series_id)
    assert [(u.rel_path, u.kind, u.vol_from, u.ch_from, u.ch_to, u.idx) for u in units] == [
        ("Chapters/c009.5.cbz", "extra", None, "9.5", None, None),
        (vol, "volume", "1", "1", "8", "01"),
    ]
    # Replacing one archive leaves the others.
    db.replace_units(series_id, vol, [Unit(rel_path=vol, kind="volume", vol_from="1", vol_to="2")])
    assert [(u.vol_from, u.vol_to) for u in db.list_units(series_id, vol)] == [("1", "2")]
    assert db.delete_units(series_id) == 2
    assert db.list_units(series_id) == []


@pytest.mark.parametrize("unit", [
    Unit(rel_path="a.cbz", kind="novel"),
    Unit(rel_path="a.cbz", kind="chapter", ch_from="5", ch_to="4"),
    Unit(rel_path="a.cbz", kind="chapter", ch_to="4"),
    Unit(rel_path="", kind="chapter"),
])
def test_invalid_units_change_nothing(db, series_id, unit):
    db.replace_units(series_id, "a.cbz", [Unit(rel_path="a.cbz", kind="chapter", ch_from="1")])
    with pytest.raises(UnitError):
        db.replace_units(series_id, "a.cbz", [unit])
    assert [u.ch_from for u in db.list_units(series_id)] == ["1"]


def test_units_go_with_their_series(db, library, series_id):
    db.replace_units(series_id, "a.cbz", [Unit(rel_path="a.cbz", kind="chapter", ch_from="1")])
    db.remove_root(db.list_roots()[0].id)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0
