"""Root -> library mapping by folder names, and a folder's effective item (own / inherited / DontMatch)."""

from __future__ import annotations

import unicodedata

from mangalist.services.mangapixer import mapping as mp_map
from mangalist.services.mangapixer.resolve import Resolver, resolve
from mangalist.store.mangapixer import Mapping

from .conftest import add_root_with_series, folder, load_fixture


class _Lib:
    def __init__(self, id, name, kind="manga"):
        self.id, self.display_name, self.kind = id, name, kind
        self.folder_count = self.item_count = self.last_scan_at = None


def _seed(cache, libs):
    """libs: {library id: (kind, [items])}"""
    cache.save_libraries([_Lib(lid, lid.title(), kind) for lid, (kind, _) in libs.items()])
    for lid, (_, items) in libs.items():
        cache.apply_page(lid, [], items)


LIBRARY = [
    folder("n01", ["Shonen", "Alpha Quest"]),
    folder("n02", ["Shonen", "Beta Story"]),
    folder("n03", ["Shonen", "Gamma"], state="Auto"),
    folder("n04", ["Seinen", "Delta"]),
    folder("n05", ["Seinen", "Franchise"], state="Confirmed"),
    folder("n06", ["Seinen", "Franchise", "Side Story"], state="NeedsReview"),
    folder("n07", ["Seinen", "Blocked"], state="DontMatch"),
    folder("n08", ["Shonen"], state="Confirmed"),
]


def test_root_is_a_subfolder_of_a_library(cache, db, tmp_path):
    _seed(cache, {"manga": ("manga", LIBRARY), "other": ("manhwa", [folder("x1", ["Alpha Quest"])])})
    root = add_root_with_series(db, tmp_path, "Shonen", ["Alpha Quest", "Beta Story", "Gamma", "Not In MP"])
    p = mp_map.propose(cache, [s.rel_path for s in db.list_series(root.id)])
    assert (p.library_id, p.prefix) == ("manga", ["Shonen"])
    # "Not In MP" still inherits the Shonen folder's own link (an ancestor above the root).
    assert (p.matched, p.unmatched) == (4, 0)


def test_root_is_the_library_root_and_counts(cache, db, tmp_path):
    _seed(cache, {"manga": ("manga", LIBRARY[:7])})
    rels = ["Shonen/Alpha Quest", "Shonen/Beta Story", "Seinen/Delta", "Seinen/Franchise", "Seinen/Unknown"]
    root = add_root_with_series(db, tmp_path, "All", rels)
    out = mp_map.refresh_auto_mappings(cache)
    m = out[root.id]
    assert (m.library_id, m.prefix, m.manual) == ("manga", [], False)
    assert (m.matched, m.unmatched) == (4, 1)
    assert cache.mapping(root.id).matched == 4


def test_case_insensitive_fallback_and_nfc(cache, db, tmp_path):
    nfd = unicodedata.normalize("NFD", "Café Stories")
    _seed(cache, {"manga": ("manga", [folder("n1", ["Café Stories"]), folder("n2", ["Second SERIES"])])})
    root = add_root_with_series(db, tmp_path, "M", [nfd, "second series"])
    p = mp_map.propose(cache, [nfd, "second series"])
    assert (p.library_id, p.matched, p.unmatched) == ("manga", 2, 0)
    cache.save_mapping(Mapping(root.id, "manga", []))
    r1 = resolve(cache, root.id, nfd)
    r2 = resolve(cache, root.id, "second series")
    assert (r1.node_id, r1.match) == ("n1", "exact")
    assert (r2.node_id, r2.match) == ("n2", "casefold")


def test_exact_case_wins_over_the_fallback(cache, db, tmp_path):
    _seed(cache, {"manga": ("manga", [folder("lower", ["series"]), folder("upper", ["Series"])])})
    root = add_root_with_series(db, tmp_path, "M", ["Series"])
    cache.save_mapping(Mapping(root.id, "manga", []))
    assert resolve(cache, root.id, "Series").node_id == "upper"


def test_best_library_wins_and_no_match_maps_nothing(cache, db, tmp_path):
    _seed(cache, {
        "a": ("manga", [folder("a1", ["One"]), folder("a2", ["Two"])]),
        "b": ("manhwa", [folder("b1", ["One"]), folder("b2", ["Two"]), folder("b3", ["Three"])]),
        "c": ("comic", [folder("c1", ["One"]), folder("c2", ["Two"]), folder("c3", ["Three"]), folder("c4", ["Four"])]),
    })
    p = mp_map.propose(cache, ["One", "Two", "Three", "Four"])
    assert (p.library_id, p.matched, p.unmatched) == ("b", 3, 1)        # the comic library is skipped by kind
    p = mp_map.propose(cache, ["Nothing Here"])
    assert (p.library_id, p.matched, p.unmatched) == (None, 0, 1)


def test_manual_override_survives_refresh(cache, db, tmp_path):
    _seed(cache, {"a": ("manga", [folder("a1", ["One"])]), "b": ("manga", [folder("b1", ["Other"])])})
    root = add_root_with_series(db, tmp_path, "M", ["One"])
    mp_map.refresh_auto_mappings(cache)
    assert cache.mapping(root.id).library_id == "a"
    m = mp_map.set_manual_mapping(cache, root.id, "b")
    assert (m.manual, m.matched, m.unmatched) == (True, 0, 1)
    mp_map.refresh_auto_mappings(cache)
    assert (cache.mapping(root.id).library_id, cache.mapping(root.id).manual) == ("b", True)
    mp_map.set_manual_mapping(cache, root.id, None)                  # "do not use MangaPixer"
    assert resolve(cache, root.id, "One") is None
    m = mp_map.clear_manual_mapping(cache, root.id)
    assert (m.library_id, m.manual) == ("a", False)


def test_resolution_own_inherited_and_dont_match_stop(cache, db, tmp_path):
    _seed(cache, {"manga": ("manga", LIBRARY)})
    root = add_root_with_series(db, tmp_path, "Seinen", [])
    cache.save_mapping(Mapping(root.id, "manga", ["Seinen"]))
    own = resolve(cache, root.id, "Franchise")
    assert (own.node_id, own.inherited, own.link_state) == ("n05", False, "Confirmed")
    assert own.record is not None and own.item["trail"] == ["Seinen", "Franchise"]
    sub = resolve(cache, root.id, "Franchise/Side Story")
    assert (sub.node_id, sub.inherited, sub.link_state, sub.record) == ("n06", False, "NeedsReview", None)
    deeper = resolve(cache, root.id, "Franchise/Volume Extras")
    assert (deeper.node_id, deeper.inherited, deeper.source_trail) == ("n05", True, ("Seinen", "Franchise"))
    # Below a DontMatch folder: the DontMatch row, never the link further up.
    blocked = resolve(cache, root.id, "Blocked/Part 2")
    assert (blocked.node_id, blocked.inherited, blocked.link_state, blocked.dont_match) == \
        ("n07", True, "DontMatch", True)
    assert resolve(cache, root.id, "Unlinked") is None


def test_resolution_through_the_prefix(cache, db, tmp_path):
    _seed(cache, {"manga": ("manga", LIBRARY)})
    root = add_root_with_series(db, tmp_path, "Shonen", [])
    cache.save_mapping(Mapping(root.id, "manga", ["Shonen"]))
    r = resolve(cache, root.id, "Unknown Series")
    assert (r.node_id, r.inherited) == ("n08", True)               # the Shonen folder's own link


def test_resolution_needs_a_usable_mapping(cache, db, tmp_path):
    _seed(cache, {"comics": ("comic", [folder("c1", ["One"])]), "manga": ("manga", [folder("m1", ["One"])])})
    root = add_root_with_series(db, tmp_path, "C", ["One"])
    assert resolve(cache, root.id, "One") is None                   # no mapping
    cache.save_mapping(Mapping(root.id, "comics", []))
    assert resolve(cache, root.id, "One") is None                   # kind skipped, no override
    cache.save_mapping(Mapping(root.id, "comics", [], manual=True, any_kind=True))
    assert resolve(cache, root.id, "One").node_id == "c1"
    cache.save_mapping(Mapping(root.id, "manga", []))
    cache.mark_library_gone("manga")
    assert resolve(cache, root.id, "One") is None                   # MangaPixer no longer lists it


def test_resolver_over_the_contract_sample(cache, db, tmp_path):
    sample = load_fixture()
    from mangalist.services.mangapixer.sync import is_folder

    cache.save_libraries([_Lib("lib0manga", "Manga")])
    cache.apply_page("lib0manga", [], [i for i in sample["items"] if is_folder(i)])
    root = add_root_with_series(db, tmp_path, "Manga", ["Shonen/Synthetic Quest", "Shonen/Synthetic Saga (Complete)",
                                                        "Misc/Something", "Doujin/Synthetic Circle - Short Story"])
    mp_map.refresh_auto_mappings(cache)
    m = cache.mapping(root.id)
    assert (m.library_id, m.prefix, m.matched, m.unmatched) == ("lib0manga", [], 3, 1)
    res = Resolver(cache).resolve_many(root.id, [s.rel_path for s in db.list_series(root.id)])
    assert res["Shonen/Synthetic Quest"].record["externalId"] == "10000000001"
    assert res["Shonen/Synthetic Quest"].item["volumes"]["items"][2]["chapters"]["to"] == "24.5"
    assert res["Misc/Something"].link_state == "DontMatch" and res["Misc/Something"].inherited
    assert res["Doujin/Synthetic Circle - Short Story"] is None       # archive items never count
