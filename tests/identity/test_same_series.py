"""Same series in two folders (mangalist.same_series): a series matched to one record in two folders - in one library
folder or across two - is a duplicate; parts of one franchise folder are not; parts split across libraries are a
notice. Only trustworthy matches count. Synthetic trees and made-up names only."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangalist import mu_cache, same_series, wanted
from mangalist.store.series import link_key

from ..states.helpers import item
from .conftest import make_archive, scan


def _series(root: Path, rel: str, n: int = 2) -> None:
    name = rel.rsplit("/", 1)[-1]
    for v in range(1, n + 1):
        make_archive(root / Path(*rel.split("/")) / f"{name} v{v:02}.cbz", seed=f"{root.name}/{rel}/{v}")


def _link(root, rel, mu_id, *, confirmed=True, band=None):
    folder = Path(link_key(Path(root.path).resolve(), rel))
    mu_cache.save_entry(folder, mu_id, f"Record {mu_id}", "https://example.invalid/s", None, mu_confirmed=confirmed,
                        mu_band=band)


@pytest.fixture
def two(db, library, library2):
    r1 = db.add_root(str(library), "Manga-Ongoing")
    r2 = db.add_root(str(library2), "Other-Ongoing")
    return r1, r2


def _where(pair):
    return tuple(c.where for c in pair.copies)


def test_two_top_level_folders_across_libraries_are_a_duplicate(db, library, library2, two):
    r1, r2 = two
    _series(library, "Summoned Partner")
    _series(library2, "I Summoned a Partner")
    _series(library, "Unrelated Tale")
    scan(db)
    _link(r1, "Summoned Partner", 111)
    _link(r2, "I Summoned a Partner", 111)
    _link(r1, "Unrelated Tale", 222)
    report = same_series.find_same_series(db)
    assert [(_where(p), p.kind, p.across_libraries) for p in report.pairs] == [
        (("Manga-Ongoing / Summoned Partner", "Other-Ongoing / I Summoned a Partner"), "duplicate", True)]
    assert report.splits == []
    pair = report.pairs[0]
    assert [(m.name, k.name) for m, k in pair.merge_choices()] == [("I Summoned a Partner", "Summoned Partner"),
                                                                    ("Summoned Partner", "I Summoned a Partner")]
    assert pair.a.files == 2 and pair.a.link == "Own match: Confirmed"
    # scoped to one library, the pair still shows: its twin lies in the other one
    assert len(report.scoped([r2.id]).pairs) == 1 and len(report.scoped([999]).pairs) == 0
    assert set(report.others_by_folder()) == set(pair.folders)


def test_two_top_level_folders_in_one_library_are_a_duplicate(db, library, two):
    r1, _r2 = two
    _series(library, "Score of Love")
    _series(library, "Score of Love (old)")
    scan(db)
    _link(r1, "Score of Love", 333)
    _link(r1, "Score of Love (old)", 333, confirmed=False, band="auto")
    report = same_series.find_same_series(db)
    assert [_where(p) for p in report.pairs] == [("Manga-Ongoing / Score of Love", "Manga-Ongoing / Score of Love (old)")]
    assert report.pairs[0].b.link == "Own match: Auto"


def test_parts_of_one_franchise_folder_are_never_listed(db, library, two):
    r1, _r2 = two
    _series(library, "Little Senpai/Blue Arc")
    _series(library, "Little Senpai/Red Arc")
    _series(library, "Wandering Blade")
    _series(library, "Wandering Blade/Art Books", 1)
    scan(db)
    rels = {s.rel_path for s in db.list_series(r1.id)}
    assert {"Little Senpai/Blue Arc", "Little Senpai/Red Arc", "Wandering Blade", "Wandering Blade/Art Books"} <= rels
    for rel in ("Little Senpai/Blue Arc", "Little Senpai/Red Arc"):
        _link(r1, rel, 444)
    for rel in ("Wandering Blade", "Wandering Blade/Art Books"):
        _link(r1, rel, 555)
    report = same_series.find_same_series(db)
    assert report.pairs == [] and report.splits == []


def test_a_series_folder_with_a_same_named_folder_inside_is_a_duplicate(db, library, two):
    r1, _r2 = two
    _series(library, "Elder Knight Tale")
    _series(library, "Elder Knight Tale/Elder Knight Tale", 1)
    scan(db)
    assert db.get_series(r1.id, "Elder Knight Tale/Elder Knight Tale") is not None
    _link(r1, "Elder Knight Tale", 666)
    _link(r1, "Elder Knight Tale/Elder Knight Tale", 666)
    report = same_series.find_same_series(db)
    assert [(_where(p), p.kind) for p in report.pairs] == [
        (("Manga-Ongoing / Elder Knight Tale", "Manga-Ongoing / Elder Knight Tale/Elder Knight Tale"), "nested")]
    # only the inner copy goes into its parent, never the parent into the folder inside it
    assert [(m.rel_path, k.rel_path) for m, k in report.pairs[0].merge_choices()] == [
        ("Elder Knight Tale/Elder Knight Tale", "Elder Knight Tale")]


def test_franchise_parts_split_across_libraries_are_a_notice_not_a_duplicate(db, library, library2, two):
    r1, r2 = two
    _series(library, "Town Where You Are/Main")
    _series(library2, "Town Where You Are/Side")
    _series(library2, "Mirror Saga Extras")                 # a top-level folder next to the parts in another library
    _series(library, "Mirror Saga/First")
    _series(library, "Mirror Saga/Second")
    scan(db)
    _link(r1, "Town Where You Are/Main", 777)
    _link(r2, "Town Where You Are/Side", 777)
    for root, rel in ((r2, "Mirror Saga Extras"), (r1, "Mirror Saga/First"), (r1, "Mirror Saga/Second")):
        _link(root, rel, 888)
    report = same_series.find_same_series(db)
    assert report.pairs == []
    assert [(s.title, sorted(c.where for c in s.copies)) for s in report.splits] == [
        ("Record 777", ["Manga-Ongoing / Town Where You Are/Main", "Other-Ongoing / Town Where You Are/Side"]),
        ("Record 888", ["Manga-Ongoing / Mirror Saga/First", "Manga-Ongoing / Mirror Saga/Second",
                        "Other-Ongoing / Mirror Saga Extras"]),
    ]


@pytest.mark.parametrize("mu_id, state", [(999, "review"), (None, "unmatched"), (None, "not_a_work")])
def test_only_trustworthy_matches_count(db, library, library2, two, mu_id, state):
    r1, r2 = two
    _series(library, "Doubtful Match")
    _series(library2, "Doubtful Match")
    scan(db)
    _link(r1, "Doubtful Match", 999)
    _link(r2, "Doubtful Match", mu_id, confirmed=False, band=state)
    assert same_series.find_same_series(db).pairs == []
    _link(r2, "Doubtful Match", 999, confirmed=False, band="auto")
    assert len(same_series.find_same_series(db).pairs) == 1


def test_mangapixers_match_counts_and_a_subseries_inherits_it(db, library, library2, two, monkeypatch):
    """MangaPixer's item wins for a folder it knows (Confirmed / Auto); a part inherits its franchise's item - so the
    parts of one franchise folder share it and are not listed, while the same record in another library is."""
    r1, r2 = two
    _series(library, "Quest Saga")
    _series(library, "Quest Saga/Side Stories", 1)
    _series(library2, "Quest Saga Complete")
    _series(library2, "Needs Look")
    scan(db)
    linked = item()
    review = item(link={"state": "NeedsReview"})
    by_name = {"Quest Saga": linked, "Quest Saga Complete": linked, "Needs Look": review}

    def fake_item(_resolver, _root_id, _root_path, folder, **_kw):
        parts = Path(folder).parts
        for name in reversed(parts):                         # its own item, else the one of the folder above
            if name in by_name:
                return by_name[name]
        return None

    monkeypatch.setattr(wanted, "open_resolver", lambda db: object())
    monkeypatch.setattr(wanted, "mangapixer_item", fake_item)
    _link(r2, "Needs Look", 90000000001)                     # the own link is ignored: MangaPixer knows the folder
    report = same_series.find_same_series(db)
    assert [(_where(p), p.key) for p in report.pairs] == [
        (("Manga-Ongoing / Quest Saga", "Other-Ongoing / Quest Saga Complete"), "mu:90000000001")]
    assert report.pairs[0].a.link == "MangaPixer: Confirmed" and report.pairs[0].a.origin_status == "Ongoing"
    assert report.splits == []


def test_same_name_ignores_case_punctuation_and_an_english_title():
    assert same_series.same_name("Elder Knight: Tale!", "elder knight tale")
    assert same_series.same_name("Romaji Name [English Name]", "Romaji Name")
    assert not same_series.same_name("Blue Arc", "Red Arc")
