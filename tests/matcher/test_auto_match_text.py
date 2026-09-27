"""Port of MangaPixer 1.26.1 ``tests/MangaPixer.Core.Tests/Metadata/AutoMatch/AutoMatchTextTests.cs``
(creator hints and provider disambiguators; all names synthetic)."""

from __future__ import annotations

import pytest

from manga_list.matcher.auto_match_text import creator_hints, disambiguator_tag


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Until We Meet [Family Given] .cbz", ["Family Given"]),
        ("Hop Step! [Onlyname].cbz", ["Onlyname"]),
        ("Some Words [Joined Hands] (Family Given)", ["Joined Hands", "Family Given"]),
        ("Some Words [Joined Hands] (Family Given) (2019)", ["Joined Hands", "Family Given"]),
        ("[Family Given] Some Words", ["Family Given"]),
        ("(C99) [Circle Name (Family Given)] Some Words (Parody)", ["Circle Name", "Family Given", "Parody"]),
        ("Family Given] Some Words.cbz", ["Family Given"]),
        ("Some Words [Family Given.cbz", ["Family Given"]),
        ("Some Words (2019)", []),
        ("Some Words (Digital)", []),
        ("Some Words (Vol. 3)", []),
        ("[Only A Tag]", []),
        ("", []),
    ],
)
def test_creator_hints_are_the_bracketed_and_unmatched_names(name, expected):
    assert list(creator_hints(name)) == expected


@pytest.mark.parametrize(
    ("title", "tag"),
    [
        ("Sprout (FAMILY Given)", "FAMILY Given"),
        ("Sprout", None),
        ("Sprout (2019)", None),
    ],
)
def test_disambiguator_tag_is_the_provider_author_suffix(title, tag):
    assert disambiguator_tag(title) == tag
