"""Roots: per-root settings, validation, exclusions, removal."""

from __future__ import annotations

import pytest

from manga_list.store import RootError


def test_add_root_defaults(db, library):
    root = db.add_root(str(library))
    assert root.id is not None
    assert root.name == "Manga"                 # display name defaults to the folder name
    assert root.enforce_naming == "ask"         # C2: ask by default
    assert root.origin_hint is None and root.staging_folder is None
    assert db.list_roots() == [root]


def test_settings_round_trip(db, tmp_path):
    a = tmp_path / "A"
    a.mkdir()
    root = db.add_root(str(a), "Webcomics", origin_hint="webcomic", enforce_naming="automatic",
                       staging_folder=str(tmp_path / "staging"), exclusions=["@Oneshots/**", "*.txt"])
    got = db.get_root(root.id)
    assert (got.name, got.origin_hint, got.enforce_naming) == ("Webcomics", "webcomic", "automatic")
    assert got.staging_folder == str(tmp_path / "staging")
    assert got.exclusions == ["@Oneshots/**", "*.txt"]
    got.enforce_naming = "off"
    got.origin_hint = None
    got.staging_folder = None
    got.exclusions = ["*.txt"]
    db.update_root(got)
    again = db.get_root(root.id)
    assert (again.enforce_naming, again.origin_hint, again.staging_folder, again.exclusions) == \
        ("off", None, None, ["*.txt"])


@pytest.mark.parametrize("kwargs", [{"origin_hint": "novel"}, {"enforce_naming": "always"},
                                    {"exclusions": ["  "]}, {"exclusions": ["a/../b"]}])
def test_invalid_settings_are_refused(db, library, kwargs):
    with pytest.raises(RootError):
        db.add_root(str(library), **kwargs)
    assert db.list_roots() == []


def test_duplicate_and_nested_roots_are_refused(db, library):
    db.add_root(str(library))
    with pytest.raises(RootError):
        db.add_root(str(library))
    with pytest.raises(RootError):
        db.add_root(str(library / "Series A"))
    with pytest.raises(RootError):
        db.add_root(str(library.parent))
    sibling = library.parent / "Manhwa"
    sibling.mkdir()
    db.add_root(str(sibling))
    assert [r.name for r in db.list_roots()] == ["Manga", "Manhwa"]


def test_remove_root_forgets_its_series_only(db, library):
    root = db.add_root(str(library))
    from manga_list.store import SeriesSeen
    db.record_scan(root.id, library, [SeriesSeen("Series A", "v1:1:x", 1)])
    assert len(db.list_series(root.id)) == 1
    db.remove_root(root.id)
    assert db.list_roots() == [] and db.list_series() == []
    assert library.is_dir()  # nothing on disk is touched


def test_root_for_path(db, library):
    root = db.add_root(str(library))
    assert db.root_for_path(library / "Series A" / "x.cbz").id == root.id
    assert db.root_for_path(library.parent / "Elsewhere") is None


def test_set_exclusions_normalises_and_dedups(db, library):
    root = db.add_root(str(library))
    assert db.set_exclusions(root.id, ["/@Oneshots/**", "@Oneshots/**", "Extras\\Raw", "./*.txt"]) == \
        ["@Oneshots/**", "Extras/Raw", "*.txt"]
