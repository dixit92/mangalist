"""Chapter downloads through Suwayomi: names, the series' group, the rows, finding the series, sending (a FAKE
Suwayomi; the ledger's chapter records; chapters are outside the download budget)."""

from __future__ import annotations

import os
from decimal import Decimal as D
from pathlib import Path

import pytest

from mangalist.downloads import chapters as chm
from mangalist.downloads.budget import counts, state_of
from mangalist.downloads.contracts import TOOL_SUWAYOMI, DownloadStatus as S, SuwayomiManga
from mangalist.store.downloads import DownloadError, DownloadLedger, SuwayomiSettings

from .fakes import candidate, data, scan
from .suwayomi_fakes import MANGA, MANGADEX, MD_ID, WEEB, FakeSuwayomi, chapter


@pytest.fixture
def cseries(db, tmp_path):
    """(series id, folder) of 'Series C': chapters 1-3 by Alpha Scans as FMD2 names, after a recorded scan."""
    root = tmp_path / "library" / "Manga"
    folder = root / "Series C"
    folder.mkdir(parents=True)
    for n in (1, 2, 3):
        (folder / f"{n:04d} [Ch. {n:04d} - Title {n} [Alpha Scans]].cbz").write_bytes(data(f"c{n}"))
    r = db.add_root(str(root), "Manga")
    scan(db)
    return db.get_series(r.id, "Series C").id, folder


@pytest.fixture
def cledger(db) -> DownloadLedger:
    return DownloadLedger(db, tool=TOOL_SUWAYOMI)


# --- names --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Vol.1 Ch.2 - Sailor Shock", ("1", "Sailor Shock")),
    ("Vol.8 Ch.953.5", ("8", None)),
    ("Vol.8 Ch.953.6 - Omake + Inserts", ("8", "Omake + Inserts")),
    ("Chapter 102", (None, None)),
    ("Chapter 102: The Return", (None, "The Return")),
    ("Ch. 10.5 - Side Story", (None, "Side Story")),
    ("The Beginning", (None, "The Beginning")),
    ("", (None, None)),
])
def test_split_chapter_name(name, expected):
    assert chm.split_chapter_name(name) == expected


# --- the series' group -------------------------------------------------------------------------------------------

def test_default_group_order_of_rules():
    held = {D(1): ["Alpha Scans"], D(2): ["Alpha Scans"], D(3): ["Alpha Scans"]}
    available = {"Alpha Scans": 2, "Beta Group": 5}
    assert chm.default_group(available, held, [D(4), D(5)]).group == "Alpha Scans"            # the folder's usual group
    assert "usual" in chm.default_group(available, held, [D(4)]).reason
    assert chm.default_group(available, held, [D(4)], stored="beta group").group == "Beta Group"   # the owner's choice
    assert chm.default_group(available, {}, [D(4)]).group == "Beta Group"                     # the most chapters
    assert chm.default_group({"Gamma": 1}, held, [D(4)]).group == "Gamma"                     # the only one
    assert chm.default_group({"": 3}, held, [D(4)]).group is None                             # no groups named


def test_default_group_follows_the_nearest_neighbours():
    held = {D(1): ["Old Group"], D(2): ["Old Group"], D(9): ["New Group"], D(10): ["New Group"]}
    choice = chm.default_group({"Old Group": 3, "New Group": 3}, held, [D(11), D(12)])
    assert choice.group == "New Group"


# --- the rows --------------------------------------------------------------------------------------------------------

def test_chapter_rows_one_copy_per_group_default_first():
    chs = [chapter(1, "4", "Alpha Scans"), chapter(2, "4", "Beta Group"), chapter(3, "5", "Beta Group"),
           chapter(4, "5", "Beta Group", upload="1600000000000"),      # a later re-upload of the same group wins
           chapter(5, "6", None)]
    rows = chm.chapter_rows(chs, ["4", "5", "6", "7"], "Alpha Scans")
    assert [r.number for r in rows] == ["4", "5", "6", "7"]
    assert [c.id for c in rows[0].options] == [1, 2] and rows[0].default.id == 1
    assert [c.id for c in rows[1].options] == [4]
    assert rows[2].default.id == 5 and rows[2].default.scanlator is None
    assert not rows[3].available and rows[3].default is None
    assert chm.group_counts(chs, [D(4), D(5), D(6)]) == {"Alpha Scans": 1, "Beta Group": 2, "": 1}


# --- finding the series ----------------------------------------------------------------------------------------------

def test_allowed_sources_default_to_mangadex_then_the_owners_order():
    installed = [WEEB, MANGADEX]
    assert chm.allowed_sources(installed, None) == [MANGADEX]
    assert chm.allowed_sources(installed, [WEEB.id, "404", MANGADEX.id]) == [WEEB, MANGADEX]


def test_find_by_mangadex_id_needs_no_confirmation():
    fake = FakeSuwayomi()
    fake.mangas[f"{MANGADEX.id}:id:{MD_ID}"] = [MANGA]
    found = chm.find_manga(fake, [MANGADEX, WEEB], mangadex_id=MD_ID, titles=["Example Manga"])
    assert found.match.how == chm.HOW_MANGADEX and found.match.confirmed and found.match.manga.id == 1
    assert ("search", WEEB.id, "Example Manga") not in fake.calls          # no title search when MangaDex has it


def test_a_result_with_another_id_is_not_a_match():
    fake = FakeSuwayomi()
    fake.mangas[f"{MANGADEX.id}:id:{MD_ID}"] = [SuwayomiManga(9, "Other", "/manga/ffff", MANGADEX.id)]
    other = SuwayomiManga(5, "Example Manga", "/series/example", WEEB.id)
    fake.mangas[f"{WEEB.id}:Example Manga"] = [other]
    found = chm.find_manga(fake, [MANGADEX, WEEB], mangadex_id=MD_ID, titles=["Example Manga"])
    assert found.match is None
    assert [(c.source.id, c.manga.id, c.how, c.confirmed) for c in found.candidates] == \
        [(WEEB.id, 5, chm.HOW_TITLE, False)]
    assert "MangaDex has no manga" in found.notes[0]


def test_without_an_id_every_source_is_searched_by_title_with_confirmation():
    fake = FakeSuwayomi()
    fake.mangas[f"{MANGADEX.id}:Alt Title"] = [MANGA]
    found = chm.find_manga(fake, [MANGADEX, WEEB], mangadex_id=None, titles=["Example Manga", "Alt Title", "Third"])
    assert [(c.source.id, c.how) for c in found.candidates] == [(MANGADEX.id, chm.HOW_TITLE)]
    assert ("search", MANGADEX.id, "Third") not in fake.calls       # two titles at most


def test_a_stored_confirmed_match_is_used_without_searching():
    fake = FakeSuwayomi()
    stored = {"source_id": WEEB.id, "manga_id": 5, "manga_title": "Example Manga", "how": chm.HOW_CONFIRMED}
    found = chm.find_manga(fake, [MANGADEX, WEEB], mangadex_id=MD_ID, titles=["x"], stored=stored)
    assert found.match.source == WEEB and found.match.manga.id == 5 and fake.calls == []
    # a source the owner no longer allows: searched again
    found = chm.find_manga(fake, [MANGADEX], mangadex_id=None, titles=["x"], stored=stored)
    assert found.match is None


# --- the lookup ----------------------------------------------------------------------------------------------------

def test_build_lookup_marks_queued_and_in_hand(cledger, cseries):
    sid, folder = cseries
    fake = FakeSuwayomi(chapters=[chapter(4, "4"), chapter(5, "5", "Beta Group"), chapter(6, "6")])
    from mangalist.downloads.contracts import QueuedChapter

    fake.queued[6] = QueuedChapter(6, "QUEUED")
    match = chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX)
    held = {D(1): ["Alpha Scans"], D(2): ["Alpha Scans"], D(3): ["Alpha Scans"]}
    look = chm.build_lookup(fake, series_id=sid, missing=["4", "5", "6", "7"], match=match, held=held, stored_group=None)
    assert look.group.group == "Alpha Scans" and look.queued_in_suwayomi == ("6",)
    assert [r.number for r in look.available] == ["4", "5", "6"] and look.not_available == ["7"]
    assert look.source_name == "MangaDex (EN)" and look.manga_title == "Example Manga"


def test_held_chapter_groups_and_placement(db, cseries):
    sid, folder = cseries
    assert chm.held_chapter_groups(db, sid) == {D(1): ["Alpha Scans"], D(2): ["Alpha Scans"], D(3): ["Alpha Scans"]}
    p = chm.chapter_placement_for(db, sid)
    assert p.target_dir == str(folder) and not p.ambiguous


def test_chapter_placement_rules(tmp_path):
    from mangalist.store.units import Unit

    s = tmp_path / "S"
    (s / "Chapters").mkdir(parents=True)
    (s / "Extra").mkdir()
    one = chm.chapter_placement(str(s), [Unit("Chapters/c1.cbz", "chapter"), Unit("v01.cbz", "volume")])
    assert one.target_dir == str(s / "Chapters")
    spread = chm.chapter_placement(str(s), [Unit("Chapters/c1.cbz", "chapter"), Unit("c2.cbz", "chapter"),
                                            Unit("Chapters/c3.cbz", "chapter")])
    assert spread.ambiguous and spread.options == (str(s / "Chapters"), str(s))
    assert chm.chapter_placement(str(s), []).target_dir == str(s)
    assert chm.chapter_placement(str(tmp_path / "gone"), []).ambiguous


# --- sending ---------------------------------------------------------------------------------------------------------

def test_send_records_first_then_enqueues(cledger, cseries):
    sid, folder = cseries
    fake = FakeSuwayomi(chapters=[chapter(4, "4"), chapter(5, "5")])
    match = chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX)
    out = chm.send_chapters(fake, cledger, sid, match, fake.chapter_list, str(folder))
    assert [r.status for r in out.records] == [S.SENT, S.SENT] and not out.skipped
    assert ("add_to_library", 1) in fake.calls and ("enqueue", [4, 5]) in fake.calls
    rec = out.records[0]
    assert rec.tool == TOOL_SUWAYOMI and rec.info_hash == "4" and rec.wanted_chapters == ("4",)
    assert rec.wanted_volumes == () and rec.source == "MangaDex (EN)" and rec.group == "Alpha Scans"
    assert rec.batch and out.records[1].batch == rec.batch
    req = cledger.request(rec.id)
    assert req["manga_title"] == "Example Manga" and req["chapter_name"] == "Vol.1 Ch.4 - Example Title 4"
    assert "Sent ch 4-5 to Suwayomi" in out.text()
    again = chm.send_chapters(fake, cledger, sid, match, fake.chapter_list, str(folder))     # in hand: skipped
    assert again.records == [] and len(again.skipped) == 2
    assert cledger.chapters_in_hand(sid) == {"4", "5"}


def test_a_refused_enqueue_fails_every_record_of_the_send(cledger, cseries):
    sid, folder = cseries
    fake = FakeSuwayomi(chapters=[chapter(4, "4")])
    fake.enqueue_error = RuntimeError("Suwayomi: source is down")
    out = chm.send_chapters(fake, cledger, sid, chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX), fake.chapter_list,
                            str(folder))
    assert [r.status for r in out.records] == [S.FAILED] and "source is down" in out.records[0].error
    assert cledger.chapters_in_hand(sid) == set()          # a failed send may be sent again


def test_send_refuses_an_unconfirmed_match_and_a_missing_folder(cledger, cseries, tmp_path):
    sid, folder = cseries
    fake = FakeSuwayomi(chapters=[chapter(4, "4")])
    with pytest.raises(chm.ChapterSendRefused):
        chm.send_chapters(fake, cledger, sid, chm.MangaMatch(WEEB, MANGA, chm.HOW_TITLE), fake.chapter_list, str(folder))
    with pytest.raises(chm.ChapterSendRefused):
        chm.send_chapters(fake, cledger, sid, chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX), fake.chapter_list,
                          str(tmp_path / "gone"))
    assert fake.calls == []


# --- the ledger: tools apart ---------------------------------------------------------------------------------------

def test_tools_are_kept_apart(db, cledger, cseries, ledger):
    sid, folder = cseries
    fake = FakeSuwayomi(chapters=[chapter(4, "4")])
    chm.send_chapters(fake, cledger, sid, chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX), fake.chapter_list,
                      str(folder))
    torrent = ledger.create(sid, candidate(), ["2"], str(folder))
    assert [r.id for r in ledger.all_records()] == [torrent.id]          # qBittorrent's ledger: as before
    assert [r.tool for r in cledger.all_records()] == [TOOL_SUWAYOMI]
    assert ledger.get(cledger.all_records()[0].id) is None
    every = DownloadLedger(db, tool=None)
    assert sorted(r.tool for r in every.all_records()) == ["qbittorrent", "suwayomi"]
    assert len(every.for_series(sid)) == 2 and len(ledger.active()) == 1 and len(cledger.active()) == 1
    with pytest.raises(DownloadError):
        every.create(sid, candidate("cd" * 20), ["2"], str(folder))
    with pytest.raises(DownloadError):
        ledger.create_chapters(sid, [chapter(9, "9")], source=MANGADEX, manga=MANGA, target_dir=str(folder), batch="b")
    with pytest.raises(DownloadError):
        DownloadLedger(db, tool="sabnzbd")


def test_chapter_downloads_are_outside_the_budget(db, cledger, cseries):
    sid, folder = cseries
    fake = FakeSuwayomi(chapters=[chapter(4, "4")])
    out = chm.send_chapters(fake, cledger, sid, chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX), fake.chapter_list,
                            str(folder))
    rec = out.records[0]
    for status in (S.SENT, S.DOWNLOADED, S.FILED, S.FAILED):
        from dataclasses import replace

        assert not counts(replace(rec, status=status, size_bytes=10 ** 12))
    state = state_of([rec], 1)
    assert state.used_bytes == 0 and state.queued == () and state.unknown_sizes == 0


# --- Suwayomi's settings ---------------------------------------------------------------------------------------------

def test_connection_is_kept_out_of_settings(db):
    from mangalist import config  # noqa: F401 - config.load() copies every settings key around
    from mangalist.downloads.contracts import SuwayomiConnection

    store = SuwayomiSettings(db)
    assert store.connection() is None
    store.save_connection(SuwayomiConnection("http://suwa:4567", "owner", "pw-secret", "/data/suwayomi/downloads"))
    conn = store.connection()
    assert (conn.base_url, conn.username, conn.password, conn.download_dir) == \
        ("http://suwa:4567", "owner", "pw-secret", "/data/suwayomi/downloads")
    assert "pw-secret" not in str(db.all_settings())
    assert store.sources() is None
    store.set_sources([MANGADEX.id, WEEB.id, MANGADEX.id, " "])
    assert store.sources() == [MANGADEX.id, WEEB.id]
    assert store.set_series_choice(7, group="Beta Group", source_id=WEEB.id) == {"group": "Beta Group",
                                                                                 "source_id": WEEB.id}
    assert store.set_series_choice(7, group=None) == {"source_id": WEEB.id}
    assert SuwayomiSettings(db).series_choice(7) == {"source_id": WEEB.id}
    store.forget_connection()
    assert store.connection() is None


def test_the_default_is_mangadex_in_the_owners_languages_not_every_language():
    """Owner, 2026-10-10: every MangaDex language came ticked - "I have to manually untick all the others"."""
    from dataclasses import replace

    french = replace(MANGADEX, id="4505830566611664829", display_name="MangaDex (FR)", lang="fr")
    japanese = replace(MANGADEX, id="1411768577036936240", display_name="MangaDex (JA)", lang="ja")
    installed = [french, WEEB, japanese, MANGADEX]
    assert chm.allowed_sources(installed, None) == [MANGADEX]                       # English only, by default
    assert chm.allowed_sources(installed, None, ("all", "en", "ja")) == [MANGADEX, japanese]   # nyaa's Raw ticked
    assert chm.allowed_sources(installed, [french.id]) == [french]                  # the owner's choice stands


def test_reset_forgets_the_owners_source_choice(tmp_path):
    from mangalist import store

    store.reset_stores()
    settings = SuwayomiSettings(store.get_store())
    settings.set_sources(["1", "2"])
    assert settings.sources() == ["1", "2"]
    settings.reset_sources()
    assert settings.sources() is None
