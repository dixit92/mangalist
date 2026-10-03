"""Exclusion patterns and the live preview of what they hide."""

from __future__ import annotations

import pytest

from manga_list.store.exclusions import ExclusionSet, InvalidPattern, list_tree, normalize_pattern, preview

from .conftest import make_archive


@pytest.mark.parametrize("pattern, rel, is_dir, expected", [
    ("@Oneshots", "@Oneshots", True, True),
    ("@Oneshots", "Series/@Oneshots", True, True),         # no slash: any depth
    ("@Oneshots", "@Oneshots Extra", True, False),
    ("@Oneshots/**", "@Oneshots", True, True),             # the folder itself, so it is pruned
    ("@Oneshots/**", "@Oneshots/A one-shot.cbz", False, True),
    ("@Oneshots/**", "Series/@Oneshots/x.cbz", False, False),  # anchored at the root
    ("*.txt", "Series/notes.txt", False, True),
    ("*.txt", "Series/notes.TXT", False, True),            # case-insensitive
    ("*.txt", "Series/x.cbz", False, False),
    ("Scans/", "Series/Scans", True, True),                # trailing slash: folders only
    ("Scans/", "Series/Scans", False, False),
    ("Series A/Extras", "Series A/Extras", True, True),
    ("Series A/Extras", "Series B/Extras", True, False),
    ("**/Raw/*.zip", "Series/Raw/a.zip", False, True),
    ("**/Raw/*.zip", "Raw/a.zip", False, True),
    ("**/Raw/*.zip", "Series/Raw/sub/a.zip", False, False),
    ("Vol ??.cbz", "S/Vol 01.cbz", False, True),
    ("[!A]*", "Bxx", True, True),
    ("[!A]*", "Axx", True, False),
])
def test_pattern_rules(pattern, rel, is_dir, expected):
    assert ExclusionSet([pattern]).excludes(rel, is_dir) is expected


def test_root_itself_is_never_excluded():
    assert not ExclusionSet(["**"]).excludes("", True)
    assert ExclusionSet(["**"]).excludes("anything", True)


@pytest.mark.parametrize("bad", ["", "   ", "/", "a//b", "../x", "a/./b", "[abc"])
def test_invalid_patterns(bad):
    with pytest.raises(InvalidPattern):
        normalize_pattern(bad)
    ex = ExclusionSet([bad, "*.txt"])
    assert ex.patterns == ["*.txt"] and [p for p, _ in ex.invalid] == [bad]


def test_preview_lists_top_most_hidden_items(library):
    make_archive(library / "@Oneshots" / "One.cbz")
    make_archive(library / "@Oneshots" / "Two.cbz")
    make_archive(library / "Series A" / "Series A v01.cbz")
    make_archive(library / "Series A" / "readme.txt")
    make_archive(library / "Loose.cbz")
    listing = list_tree(library)
    items = preview(listing, ["@Oneshots/**", "*.txt"])
    assert [(i.rel_path, i.is_dir, i.hidden_below, i.pattern) for i in items] == [
        ("@Oneshots", True, 2, "@Oneshots/**"),
        ("Series A/readme.txt", False, 0, "*.txt"),
    ]
    assert preview(listing, []) == []
    assert preview(listing, ["nothing-matches"]) == []


def test_list_tree_is_bounded(library):
    for i in range(30):
        make_archive(library / f"S{i:02d}" / "a.cbz")
    listing = list_tree(library, max_entries=10)
    assert listing.truncated and len(listing.entries) == 10
    shallow = list_tree(library, max_depth=1)
    assert all("/" not in rel for rel, _ in shallow.entries)


def test_list_tree_of_a_missing_root(tmp_path):
    listing = list_tree(tmp_path / "gone")
    assert listing.entries == [] and listing.error
