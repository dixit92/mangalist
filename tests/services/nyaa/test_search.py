from __future__ import annotations

from mangalist.downloads.contracts import NyaaCandidate, VolumeSearch
from mangalist.services.nyaa import NyaaSearch

from .conftest import FakeClient, feed, fixture_bytes, info_hash, item


def titles(results):
    return [c.title for c in results]


def search(client, names, missing, held=(), **kw):
    return NyaaSearch(client, **kw).search(list(names), list(missing), list(held))


def test_implements_the_contract():
    s: VolumeSearch = NyaaSearch(FakeClient({}))
    assert s.search([], [], []) == []


# --- series match -------------------------------------------------------------------------------------

def test_a_spin_off_sharing_a_prefix_is_rejected():
    client = FakeClient({"berserk": fixture_bytes("berserk.xml")})
    out = search(client, ["Berserk"], ["41", "42"], [str(i) for i in range(1, 41)])
    assert sorted(titles(out)) == ["Berserk v41 (2022) (Digital) (LuCaZ)", "Berserk v42 (2025) (Digital) (LuCaZ)"]
    assert not any("Gluttony" in t or "Guidebook" in t for t in titles(out))


def test_the_series_name_is_compared_after_normalisation():
    client = FakeClient({"dr stone": feed(
        item("DR.STONE v03 (Digital) (Group)"), item("Dr. Stone - Volume 04"), item("The Dr Stone v05"),
        item("Dr Stone Reboot Byakuya v01 (Digital)"), item("Stone v06"))})
    out = search(client, ["Dr. Stone"], ["3", "4", "5", "6"])
    assert sorted(c.vol_from for c in out) == ["3", "4", "5"]


def test_edition_words_do_not_stop_a_match_but_numbers_do():
    client = FakeClient({"title": feed(item("Title Omnibus v01 (Digital)"), item("Title 2 v01 (Digital)"),
                                       item("Title (Manga) v02 (Digital)"))})
    out = search(client, ["Title"], ["1", "2"])
    assert sorted(titles(out)) == ["Title (Manga) v02 (Digital)", "Title Omnibus v01 (Digital)"]


def test_chapter_releases_are_dropped():
    client = FakeClient({"vinland saga": fixture_bytes("vinland-saga.xml")})
    out = search(client, ["Vinland Saga"], ["3", "14", "29"], keep_no_coverage=True)
    assert not any("Chapter" in c.title or "4chan" in c.title for c in out)


# --- queries ------------------------------------------------------------------------------------------

def test_the_first_two_names_are_always_asked_and_merged():
    # English releases and romanised ones are named differently: one name alone misses half of them.
    client = FakeClient({"blue example": feed(item("Blue Example v03 (Digital) (One)")),
                         "ao no example": feed(item("Ao no Example v01-02 (Digital) (Pack)")),
                         "aoex": feed(item("Aoex v04 (Digital)"))})
    out = search(client, ["Blue Example", "Ao no Example", "Aoex"], ["1", "2", "3", "4"])
    assert [q for q, _ in client.calls] == ["Blue Example", "Ao no Example"]   # the third: only while nothing found
    assert sorted(titles(out)) == ["Ao no Example v01-02 (Digital) (Pack)", "Blue Example v03 (Digital) (One)"]


def test_the_next_title_is_tried_when_the_first_has_no_usable_result():
    client = FakeClient({"shingeki no kyojin": feed(item("Shingeki no Kyojin v01 (Digital)", seeders=0),
                                                    item("Shingeki no Kyojin Before the Fall v01")),
                         "attack on titan": feed(item("Attack on Titan v02 (Digital)"))})
    out = search(client, ["Shingeki no Kyojin", "Attack on Titan"], ["1", "2"])
    assert [q for q, _ in client.calls] == ["Shingeki no Kyojin", "Attack on Titan"]
    assert titles(out) == ["Attack on Titan v02 (Digital)"]


def test_a_result_for_any_alternative_name_is_the_series():
    client = FakeClient({"series a": feed(), "series b": feed(item("Series B v01 (Digital)"))})
    assert titles(search(client, ["Series A", "Series B"], ["1"])) == ["Series B v01 (Digital)"]


def test_duplicate_and_empty_titles_cost_no_request_and_the_category_is_passed_on():
    client = FakeClient({})
    search(client, ["Berserk", "BERSERK", "", "  ", "berserk!"], ["1"], category="3_3")
    assert client.calls == [("Berserk", "3_3")]


def test_the_number_of_queries_is_capped():
    client = FakeClient({})
    search(client, [f"Alt {i}" for i in range(9)], ["1"], max_queries=3)
    assert len(client.calls) == 3


def test_queries_drop_punctuation_and_editions():
    client = FakeClient({})
    search(client, ["Re:Zero - Starting Life (Digital) [Manga]"], ["1"])
    assert client.calls[0][0] == "Re Zero Starting Life"


def test_the_same_torrent_from_two_queries_is_listed_once():
    h = info_hash("same")
    client = FakeClient({"one": feed(item("Other v09 (Digital)", hash=h, seeders=0)),
                         "two": feed(item("Two v01 (Digital)", hash=h), item("Two v01 (Digital)", hash=h))})
    out = search(client, ["One", "Two"], ["1"])
    assert [c.info_hash for c in out] == [h]


# --- filters ------------------------------------------------------------------------------------------

def test_zero_seeders_are_dropped():
    client = FakeClient({"s": feed(item("S v01 (Digital)", seeders=0), item("S v02 (Digital)", seeders=1))})
    assert titles(search(client, ["S"], ["1", "2"])) == ["S v02 (Digital)"]


def test_light_novels_are_hidden_by_default_and_flagged_when_included():
    client = FakeClient({"overlord": fixture_bytes("overlord.xml")})
    hidden = search(client, ["Overlord"], ["3"], ["1"])
    assert hidden and not any(c.not_comic for c in hidden)
    # the recorded feed has few seeded rows today; build the same shapes by hand for the second half
    hand = FakeClient({"overlord": feed(item("Overlord LN Vols 01-12 [Yen Press] [EPUB] [Tagged]"),
                                        item("Overlord (Manga) Vol.03"))})
    shown = search(hand, ["Overlord"], ["3"], include_not_comic=True)
    assert sorted(c.not_comic for c in shown) == [False, True]
    novel = next(c for c in shown if c.not_comic)
    assert any("light novel" in r for r in novel.reasons)
    assert titles(search(hand, ["Overlord"], ["3"])) == ["Overlord (Manga) Vol.03"]


def test_releases_that_fill_no_missing_volume_are_dropped_unless_kept():
    client = FakeClient({"s": feed(item("S v01 (Digital)"), item("S v05 (Digital)"))})
    assert titles(search(client, ["S"], ["5"], ["1"])) == ["S v05 (Digital)"]
    both = search(client, ["S"], ["5"], ["1"], keep_no_coverage=True)
    assert titles(both) == ["S v05 (Digital)", "S v01 (Digital)"]


def test_without_a_missing_list_nothing_is_dropped_for_coverage():
    client = FakeClient({"s": feed(item("S v01 (Digital)"), item("S v05 (Digital)"))})
    assert len(search(client, ["S"], [], [])) == 2


# --- the candidate ------------------------------------------------------------------------------------

def test_candidate_fields():
    h = info_hash("c")
    client = FakeClient({"s": feed(item("[Grp] S v03-v05 (2020) (Digital) (Tm)", seeders=12, leechers=3, downloads=99,
                                        trusted=True, hash=h, size="1.5 GiB"))})
    (c,) = search(client, ["S"], ["3", "4", "9"], ["5", "6"])
    assert isinstance(c, NyaaCandidate)
    assert (c.vol_from, c.vol_to, c.digital, c.group, c.is_pack, c.not_comic) == ("3", "5", True, "Tm", True, False)
    assert c.covers_missing == ("3", "4") and c.covers_held == ("5",)
    assert (c.seeders, c.leechers, c.downloads, c.trusted, c.remake) == (12, 3, 99, True, False)
    assert c.info_hash == h and c.size_bytes == int(1.5 * 1024 ** 3) and c.category == "3_1"
    assert c.published == "2026-08-03T19:48:36+00:00"
    assert c.view_url.startswith("https://nyaa.si/view/") and c.torrent_url.endswith(".torrent")
    assert c.magnet == f"magnet:?xt=urn:btih:{h}"
    assert c.reasons[0] == "covers 2 missing volumes (3, 4)"
    assert "includes 1 volume you already have (5)" in c.reasons
    assert "Digital" in c.reasons and "trusted uploader" in c.reasons and "12 seeders" in c.reasons


def test_a_range_covers_whole_numbers_and_its_ends_only():
    client = FakeClient({"s": feed(item("S v12-13 (Digital)"))})
    (c,) = search(client, ["S"], ["11", "12", "12.5", "13", "14"])
    assert c.covers_missing == ("12", "13")
    client = FakeClient({"s": feed(item("S v12.5 (Digital)"))})
    (c,) = search(client, ["S"], ["12", "12.5"])
    assert c.covers_missing == ("12.5",)


def test_volume_strings_keep_the_callers_spelling():
    client = FakeClient({"s": feed(item("S v01-03 (Digital)"))})
    (c,) = search(client, ["S"], ["01", "2"], ["03"])
    assert c.covers_missing == ("01", "2") and c.covers_held == ("03",)


# --- the D4 order -------------------------------------------------------------------------------------

def test_more_missing_volumes_beat_everything_else():
    client = FakeClient({"s": feed(
        item("S v01 (Digital)", seeders=999, trusted=True),
        item("S v01-03 (Group)", seeders=1),
        item("S v01-02 (Digital)", seeders=500, trusted=True))})
    assert titles(search(client, ["S"], ["1", "2", "3"])) == ["S v01-03 (Group)", "S v01-02 (Digital)",
                                                                "S v01 (Digital)"]


def test_digital_beats_scans_then_trusted_then_seeders():
    client = FakeClient({"s": feed(
        item("S v01 (Scan)", seeders=900, trusted=True),
        item("S v01 (Digital) (A)", seeders=2),
        item("S v01 (Digital) (B)", seeders=50),
        item("S v01 (Digital) (C)", seeders=1, trusted=True))})
    out = search(client, ["S"], ["1"])
    assert [c.group for c in out] == ["C", "B", "A", "Scan"]


def test_remakes_are_ranked_down():
    client = FakeClient({"s": feed(item("S v01 (Digital) (A)", seeders=3, remake=True),
                                   item("S v01 (Digital) (B)", seeders=1))})
    out = search(client, ["S"], ["1"])
    assert [c.group for c in out] == ["B", "A"]
    assert "remake (ranked down)" in out[1].reasons


def test_a_trusted_remake_still_ranks_like_a_trusted_upload_minus_the_penalty():
    client = FakeClient({"s": feed(item("S v01 (Digital) (A)", remake=True, trusted=True),
                                   item("S v01 (Digital) (B)"), item("S v01 (Digital) (C)", trusted=True))})
    assert [c.group for c in search(client, ["S"], ["1"])] == ["C", "A", "B"]


def test_rank_is_a_single_descending_number():
    client = FakeClient({"s": feed(item("S v01-02 (Digital)"), item("S v01 (Digital)"), item("S v02 (Scan)"))})
    out = search(client, ["S"], ["1", "2"])
    assert [c.rank for c in out] == sorted((c.rank for c in out), reverse=True)


def test_ties_prefer_the_newer_upload():
    client = FakeClient({"s": feed(
        item("S v01 (Digital) (Old)", date="Mon, 01 Jan 2024 10:00:00 -0000"),
        item("S v01 (Digital) (New)", date="Mon, 01 Jan 2026 10:00:00 -0000"))})
    assert [c.group for c in search(client, ["S"], ["1"])] == ["New", "Old"]


def test_uncertain_releases_rank_between_known_fills_and_known_nothing():
    client = FakeClient({"s": feed(
        item("S (2013-2025) (Digital) (Pack)"), item("S v05 (Omnibus Edition) (Digital)"),
        item("S v01 (Digital) (One)"), item("S v09 (Digital) (Held)"))})
    out = search(client, ["S"], ["1"], ["9"], keep_no_coverage=True)
    assert [c.group for c in out[:1]] == ["One"]
    assert out[-1].group == "Held"
    middle = out[1:-1]
    assert len(middle) == 2 and all(not c.covers_missing for c in middle)
    reasons = " ".join(r for c in middle for r in c.reasons)
    assert "pack without volume numbers" in reasons and "Omnibus edition" in reasons


def test_a_pack_without_numbers_is_flagged_and_has_no_volumes():
    client = FakeClient({"vinland saga": fixture_bytes("vinland-saga.xml")})
    out = search(client, ["Vinland Saga"], ["3", "14", "29"])
    pack = next(c for c in out if c.title.startswith("Vinland Saga (2013-2025)"))
    assert pack.is_pack and pack.vol_from is None and pack.vol_to is None and pack.covers_missing == ()
    assert out[0].title.startswith("Vinland Saga v29")          # the only release that certainly fills a volume


# --- no missing list (MangaList cannot tell which English volumes are out) -----------------------------


def test_without_a_missing_list_the_volumes_not_held_count():
    client = FakeClient({"s": feed(
        item("S v01-03 (Digital) (Pack)"), item("S v04 (Digital) (Four)"), item("S v02 (Digital) (Held)"),
        item("S (2013-2025) (Digital) (Numberless)"))})
    out = search(client, ["S"], [], ["1", "2"])
    by_group = {c.group: c for c in out}
    assert "Held" not in by_group                                   # only a volume already held: dropped
    assert by_group["Pack"].covers_missing == ("3",) and by_group["Pack"].covers_held == ("1", "2")
    assert by_group["Four"].covers_missing == ("4",)
    assert "volume you do not have" in " ".join(by_group["Four"].reasons)
    assert by_group["Numberless"].covers_missing == ()              # unknown contents: kept, ranked below
    assert out[-1].group == "Numberless"


def test_without_a_missing_list_or_holdings_every_volume_counts():
    client = FakeClient({"s": feed(item("S v01-03 (Digital) (Pack)"), item("S v02.5 (Digital) (Half)"))})
    out = {c.group: c for c in search(client, ["S"], [], [])}
    assert out["Pack"].covers_missing == ("1", "2", "3") and out["Half"].covers_missing == ("2.5",)


def test_a_title_giving_two_names_matches_either():
    client = FakeClient({"blue example": feed(
        item("Ao no Example | Blue Example v01-31 + 151-158 (2011-2024) (Digital) (Group)"),
        item("Ao no Example / Blue Example Gaiden v05 (Digital) (Gaiden)"),   # the second name is a spin-off
        item("Blue Example | Red Example v02 (Digital) (Crossover)"),         # the second name is another series
        item("Blue Example / Ao no Example v03 (Digital) (Three)"))})
    out = {c.group: c for c in search(client, ["Blue Example", "Ao no Example"], [], ["1"])}
    assert set(out) == {"Group", "Three"}
    assert out["Group"].vol_from == "1" and out["Group"].vol_to == "31" and len(out["Group"].covers_missing) == 30
