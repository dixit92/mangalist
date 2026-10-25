"""Other releases (owner, 2026-10-24): the releases that fill no missing volume are kept apart, never "usable"."""

from __future__ import annotations

from mangalist.services.nyaa import NyaaSearch

from .conftest import FakeClient, feed, item


def search(client, names, missing, held=(), **kw):
    others = kw.pop("others", True)
    return NyaaSearch(client, **kw).search(list(names), list(missing), list(held), others=others)


HELD = [str(i) for i in range(1, 15)]


def test_a_collection_of_held_volumes_is_an_other_release_after_the_usable_ones():
    client = FakeClient({"made up title": feed(item("Made Up Title v01-14 + 110-203 (Digital) (Group)", seeders=9),
                                               item("Made Up Title v15 (Digital)", seeders=2),
                                               item("Made Up Title v03 (Scans)", seeders=30))})
    out = search(client, ["Made Up Title"], ["15"], HELD)
    assert [(c.title, c.other) for c in out] == [
        ("Made Up Title v15 (Digital)", False),
        ("Made Up Title v01-14 + 110-203 (Digital) (Group)", True),     # the others ranked among themselves
        ("Made Up Title v03 (Scans)", True)]
    collection = out[1]
    assert collection.covers_missing == () and collection.covers_held == tuple(HELD)


def test_without_others_the_search_is_as_before():
    client = FakeClient({"made up title": feed(item("Made Up Title v01-14 (Digital)"))})
    assert search(client, ["Made Up Title"], ["15"], HELD, others=False) == []


def test_other_releases_do_not_stop_the_next_titles_being_tried():
    client = FakeClient({"first name": feed(item("First Name v01-14 (Digital)")),
                         "second name": feed(item("Second Name v02 (Digital)")),
                         "third name": feed(item("Third Name v15 (Digital)")),
                         "fourth name": feed()})
    out = search(client, ["First Name", "Second Name", "Third Name", "Fourth Name"], ["15"], HELD)
    # nothing usable after the first two: the third is asked, and it fills v15 - the fourth is not asked
    assert [q for q, _ in client.calls] == ["First Name", "Second Name", "Third Name"]
    assert [(c.title, c.other) for c in out] == [("Third Name v15 (Digital)", False),
                                                 ("First Name v01-14 (Digital)", True),
                                                 ("Second Name v02 (Digital)", True)]


def test_other_releases_cost_no_request_beyond_the_cap():
    client = FakeClient({f"alt {i}": feed(item(f"Alt {i} v01 (Digital)")) for i in range(6)})
    out = search(client, [f"Alt {i}" for i in range(6)], ["15"], HELD, max_queries=3)
    assert len(client.calls) == 3
    assert all(c.other for c in out) and len(out) == 3


def test_without_a_missing_list_a_release_of_held_volumes_only_is_other_and_a_new_volume_is_usable():
    # a series with no gaps ("Search releases..."): a volume not held is still a find; the rest are other releases
    client = FakeClient({"made up title": feed(item("Made Up Title v01-14 (Digital)"),
                                               item("Made Up Title v15 (Digital)"))})
    out = search(client, ["Made Up Title"], [], HELD)
    assert [(c.title, c.other) for c in out] == [("Made Up Title v15 (Digital)", False),
                                                 ("Made Up Title v01-14 (Digital)", True)]


def test_uncertain_releases_stay_usable_never_other():
    client = FakeClient({"made up title": feed(item("Made Up Title (2019-2021) (Digital) (Pack)"),
                                               item("Made Up Title Deluxe Edition v01-03"))})
    out = search(client, ["Made Up Title"], ["15"], HELD)
    assert out and not any(c.other for c in out)


def test_zero_seeders_and_chapter_releases_are_still_dropped():
    client = FakeClient({"made up title": feed(item("Made Up Title v01-14 (Digital)", seeders=0),
                                               item("Made Up Title - Chapter 120"))})
    assert search(client, ["Made Up Title"], ["15"], HELD) == []
