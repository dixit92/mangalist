"""The whole chapter flow through the real backend (:class:`mangalist.downloads.adapter.Backend`) and the real Suwayomi
client, answered by Suwayomi-Server v2.4.2366's RECORDED GraphQL answers (``tests/fixtures/suwayomi``; a fake HTTP session,
no network): connect -> find the series (MangaDex by the id MangaPixer links; another source by title only once the owner
confirmed the match) -> the chapters panel's lookup (missing chapters, groups, the default group, a per-series choice) ->
send (ledger records with tool ``suwayomi``, outside the torrent budget) -> the To get chips and the In progress rows ->
arrivals (the finished CBZ found in Suwayomi's download folder by its path rule, its ComicInfo read, filed into the series
folder through the journal under the injected namer's name; Suwayomi's copy deleted; a rescan and a MangaPixer scan
requested). Made-up names only."""

from __future__ import annotations

import copy
import json
import logging
import os
import zipfile
from pathlib import Path

import pytest

from mangalist.downloads import chapters as chm
from mangalist.downloads.adapter import Backend
from mangalist.downloads.budget import counts
from mangalist.downloads.chapter_arrivals import expected_path
from mangalist.downloads.contracts import TOOL_SUWAYOMI, DownloadStatus as S
from mangalist.gui.download_rules import chips_text, in_progress, merge_batches, row_chips, CHAPTER_CHIPS
from mangalist.gui.downloads_backend import BackendError, SuwayomiSettingsView
from mangalist.gui.volumes_target import status_text
from mangalist.services.suwayomi import SuwayomiClient
from mangalist.store.mangapixer import MangaPixerCache, Mapping

from ..services.mangapixer.conftest import folder as mp_item
from ..services.suwayomi.conftest import PASSWORD, FakeSession, recorded
from .fakes import data, scan
from .suwayomi_fakes import MD_ID, FakeNamer

SERIES = "Series C"
MANGADEX_EN = "2499283573021220255"
OTHER = "1000000000000000001"           # a made-up id for a title-only source (Weeb Central, MangaFire, ...)


class Lib:
    id, display_name, kind = "lib0", "Manga", "manga"
    folder_count = item_count = last_scan_at = None


@pytest.fixture
def world(db, tmp_path):
    """A series folder holding chapters 3-4 by Alpha Scans (missing 1-2), Suwayomi's download folder, a recorded
    Suwayomi and the backend over them."""
    root = tmp_path / "library" / "Manga"
    sdir = root / SERIES
    sdir.mkdir(parents=True)
    for n in (3, 4):
        (sdir / f"{n:04d} [Ch. {n:04d} - Title {n} [Alpha Scans]].cbz").write_bytes(data(f"c{n}"))
    r = db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(r.id, SERIES).id
    downloads = tmp_path / "appdata" / "suwayomi" / "downloads"
    (downloads / "mangas").mkdir(parents=True)
    session = FakeSession()
    session.answers["MangaListQueue"] = "status_idle.json"
    conns = []
    namer = FakeNamer()

    def factory(conn):
        conns.append(conn)
        return SuwayomiClient(conn, session=session)

    backend = Backend(db, chapter_client_factory=factory, namer=namer)
    return {"db": db, "sid": sid, "dir": sdir, "root": r, "downloads": downloads, "session": session,
            "backend": backend, "conns": conns, "namer": namer}


def link_mangadex(db, root_id, mangadex_id=MD_ID):
    """MangaPixer's export item for the series (Confirmed) with its MangaDex companion id."""
    cache = MangaPixerCache(db)
    item = mp_item("n01", [SERIES])
    item["companions"]["mangadex"] = mangadex_id
    cache.save_libraries([Lib()])
    cache.apply_page("lib0", [], [item])
    cache.save_mapping(Mapping(root_id=root_id, library_id="lib0"))
    return cache


def connect(world, password=PASSWORD):
    world["backend"].save_suwayomi(SuwayomiSettingsView(base_url="http://192.0.2.10:4567", username="owner",
                                                        download_dir=str(world["downloads"])), password)


def finish(world, number: int):
    """Suwayomi finished chapter *number*: its CBZ where v2.4.2366 writes it, with its recorded ComicInfo."""
    name = f"Vol.1 Ch.{number} - Example Title {number}"
    path = Path(expected_path(str(world["downloads"]), "MangaDex (EN)", "Example Manga", name, "Alpha Scans"))
    path.parent.mkdir(parents=True, exist_ok=True)
    info = recorded("ComicInfo.xml").replace("<Number>1</Number>", f"<Number>{number}</Number>").replace(
        "Vol.1 Ch.1 - Example Title 1", name).replace("c0001", f"c000{number}")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.png", f"page of chapter {number}")
        zf.writestr("ComicInfo.xml", info)
    return path


def with_other_source():
    """The recorded sources plus one title-only source (made up)."""
    answer = json.loads(recorded("sources_all.json"))
    answer["data"]["sources"]["nodes"].append({
        "id": OTHER, "name": "Weeb Example", "lang": "en", "displayName": "Weeb Example", "contentWarning": "SAFE",
        "supportsLatest": True, "extension": {"pkgName": "eu.kanade.tachiyomi.extension.en.weebexample",
                                              "name": "Weeb Example"}})
    return answer


def titles_on_other_source():
    answer = json.loads(recorded("search_title.json"))
    for m in answer["data"]["fetchSourceManga"]["mangas"]:
        m["sourceId"] = OTHER
    return answer


# --- connect --------------------------------------------------------------------------------------------------------

def test_connect_test_and_the_password_stays_write_only(world, caplog):
    backend, session = world["backend"], world["session"]
    assert not backend.suwayomi_ready() and backend.load_suwayomi() == SuwayomiSettingsView()
    with caplog.at_level(logging.DEBUG):
        connect(world)
        view = backend.load_suwayomi()
        assert backend.suwayomi_ready()
        assert view.base_url == "http://192.0.2.10:4567" and view.username == "owner" and view.has_password
        assert view.download_dir == str(world["downloads"])
        check = backend.test_suwayomi(view, None)            # None: the stored password
    assert check.version == "v2.4.2366" and check.download_as_cbz and check.folder_found is True
    assert not check.flaresolverr and any("FlareSolverr" in n and "8191" in n for n in check.notes())
    assert session.names()[-1] == "MangaListSettings" and session.requests[-1]["auth"] == ("owner", PASSWORD)
    assert PASSWORD not in caplog.text and PASSWORD not in repr(view) and PASSWORD not in repr(world["conns"][-1])
    # Saving again without a password keeps the stored one; the address is checked.
    backend.save_suwayomi(view, "")
    assert backend.load_suwayomi().has_password
    with pytest.raises(BackendError):
        backend.save_suwayomi(SuwayomiSettingsView(base_url="ftp://nope"), None)


def test_test_connection_names_a_wrong_download_folder_and_a_refused_login(world, tmp_path):
    backend, session = world["backend"], world["session"]
    view = SuwayomiSettingsView(base_url="http://192.0.2.10:4567", download_dir=str(tmp_path / "elsewhere"))
    check = backend.test_suwayomi(view, None)
    assert check.folder_found is False and any("no \"mangas\" folder" in n for n in check.notes())
    session.status = 401
    with pytest.raises(BackendError, match="refused the login"):
        backend.test_suwayomi(view, "wrong")


def test_sources_mangadex_first_by_default_then_the_owners_choice(world):
    backend, session = world["backend"], world["session"]
    connect(world)
    session.answers["MangaListSources"] = with_other_source()
    listed = backend.suwayomi_sources()
    assert [(s.display_name, on) for s, on in listed] == [("MangaDex (EN)", True), ("MangaDex (JA)", True),
                                                          ("Weeb Example", False)]
    assert "Local source" not in [s.display_name for s, _ in listed]
    backend.set_suwayomi_sources([OTHER, MANGADEX_EN])
    assert [(s.display_name, on) for s, on in backend.suwayomi_sources()] == [
        ("Weeb Example", True), ("MangaDex (EN)", True), ("MangaDex (JA)", False)]


# --- find, look up, send, file ----------------------------------------------------------------------------------

def test_the_whole_flow_from_mangadex_id_to_the_filed_chapters(world, caplog):
    db, backend, session, sid, sdir = world["db"], world["backend"], world["session"], world["sid"], world["dir"]
    cache = link_mangadex(db, world["root"].id)
    connect(world)

    # The chapters panel's lookup: found by the MangaDex id MangaPixer links, no confirmation needed.
    lookup = backend.chapter_lookup(sid, ("1", "2"), (SERIES,))
    assert lookup.error is None and lookup.match is not None and lookup.match.how == chm.HOW_MANGADEX
    search = next(r for r in session.requests if r["name"] == "MangaListSearch")
    assert search["variables"]["query"] == f"id:{MD_ID}" and search["variables"]["source"] == MANGADEX_EN
    assert lookup.manga_title == "Example Manga" and lookup.source_name == "MangaDex (EN)"
    assert [r.number for r in lookup.rows] == ["1", "2"] and [r.number for r in lookup.available] == ["1", "2"]
    assert lookup.group.group == "Alpha Scans"
    assert lookup.placement.target_dir == str(sdir) and not lookup.candidates
    assert backend.suwayomi.series_choice(sid)["how"] == chm.HOW_MANGADEX      # remembered for the series

    # Send: one record per chapter, tool 'suwayomi', SENT, outside the torrent budget.
    picks = [row.default for row in lookup.rows]
    outcome = backend.send_chapters(sid, lookup.match, picks, str(sdir), lookup.manga_title, lookup.source_name)
    assert outcome.error is None and len(outcome.records) == 2
    assert {r.tool for r in outcome.records} == {TOOL_SUWAYOMI} and {r.status for r in outcome.records} == {S.SENT}
    assert [r.wanted_chapters for r in outcome.records] == [("1",), ("2",)]
    assert [r.info_hash for r in outcome.records] == ["1", "2"]                # Suwayomi's chapter ids
    enqueue = next(r for r in session.requests if r["name"] == "MangaListEnqueue")
    assert enqueue["variables"]["ids"] == [1, 2]
    assert not any(counts(r) for r in backend.records()) and backend.budget_status().used_bytes == 0
    assert backend.ledger.all_records() == []           # the torrent ledger does not see them

    # The To get row's chips and the In progress list: one Send shows once.
    records = backend.records(sid)
    assert chips_text(row_chips(records, None, CHAPTER_CHIPS)) == "Downloading ch 1-2"
    (row,) = in_progress(merge_batches(records))
    assert status_text(row) == "Downloading" and row.title.startswith("Ch. 1-2 · Alpha Scans · MangaDex (EN)")

    # A second lookup marks the chapters as in hand: they are not offered again.
    again = backend.chapter_lookup(sid, ("1", "2"), (SERIES,))
    assert all(r.in_hand is not None for r in again.rows)

    # Still downloading in Suwayomi: the check waits.
    session.answers["MangaListQueue"] = "status_running.json"
    session.answers["MangaListChaptersById"] = {"data": {"chapters": {"nodes": [
        {**n, "isDownloaded": False} for n in json.loads(recorded("downloaded.json"))["data"]["chapters"]["nodes"]]}}}
    first = backend.check_now()
    assert "chapters: 2 checked: 0 filed, 0 failed, 2 waiting" in first
    assert {r.status for r in backend.records(sid)} == {S.SENT}

    # Finished: the CBZs are in Suwayomi's download folder; the next check files them under the scheme's names.
    session.answers["MangaListQueue"] = "status_idle.json"
    session.answers["MangaListChaptersById"] = "downloaded.json"
    sources = [finish(world, 1), finish(world, 2)]
    with caplog.at_level(logging.DEBUG):
        message = backend.check_now()
    assert "chapters: 2 checked: 2 filed, 0 failed, 0 waiting" in message and "rescan ok" in message
    names = sorted(os.listdir(sdir))
    assert "Ch. 0001.00 Vol. 001 (Example Title 1) [Alpha Scans].cbz" in names
    assert "Ch. 0002.00 Vol. 001 (Example Title 2) [Alpha Scans].cbz" in names
    assert world["namer"].calls[0]["group"] == "Alpha Scans" and world["namer"].calls[0]["folder"] == str(sdir)
    with zipfile.ZipFile(sdir / "Ch. 0001.00 Vol. 001 (Example Title 1) [Alpha Scans].cbz") as zf:
        assert zf.read("001.png") == b"page of chapter 1"
    # Suwayomi deleted its copy (asked once the library file was verified); the records are done.
    delete = [r for r in session.requests if r["name"] == "MangaListDeleteDownloaded"]
    assert delete and sorted(i for r in delete for i in r["variables"]["ids"]) == [1, 2]
    assert {r.status for r in backend.records(sid)} == {S.REMOVED}
    assert all(r.filed_files for r in backend.records(sid))
    assert chips_text(row_chips(backend.records(sid), None, CHAPTER_CHIPS)) == "Filed ch 1-2"
    # Filed through the journal, the rescan saw the files, a MangaPixer scan of the library is asked for.
    with db.connect() as con:
        plans = con.execute("SELECT reason, status FROM journal_plans").fetchall()
    assert [tuple(p) for p in plans] == [("chapter arrival", "done")] * 2 or (
        len(plans) == 2 and {p[0] for p in plans} == {"chapter arrival"}), [tuple(p) for p in plans]
    assert "lib0" in cache.pending_scans()
    assert all(p.exists() for p in sources)             # (recorded answers: nothing here deletes Suwayomi's files)
    assert PASSWORD not in caplog.text and PASSWORD not in message


def test_a_title_match_on_another_source_waits_for_the_owners_confirmation(world):
    backend, session, sid, sdir = world["backend"], world["session"], world["sid"], world["dir"]
    connect(world)                                         # no MangaPixer link: no MangaDex id
    session.answers["MangaListSources"] = with_other_source()
    session.answers["MangaListSearch"] = titles_on_other_source()
    backend.set_suwayomi_sources([MANGADEX_EN, OTHER])
    lookup = backend.chapter_lookup(sid, ("1", "2"), (SERIES, "Series C Alt"))
    assert lookup.match is None and lookup.rows == []
    assert any("no MangaDex id" in n for n in lookup.notes)
    assert lookup.candidates and {c.how for c in lookup.candidates} == {chm.HOW_TITLE}
    assert not any(r["name"] == "MangaListChapters" for r in session.requests)     # nothing listed before confirming
    other = [c for c in lookup.candidates if c.source.id == OTHER]
    assert other and other[0].manga.title == "Example Manga"
    with pytest.raises(BackendError, match="not been confirmed"):
        backend.send_chapters(sid, other[0], [], str(sdir))
    # The owner confirms: remembered for the series, the chapters are listed from then on without a search.
    backend.confirm_match(sid, other[0])
    session.requests.clear()
    confirmed = backend.chapter_lookup(sid, ("1", "2"), (SERIES,))
    assert confirmed.match is not None and confirmed.match.how == chm.HOW_CONFIRMED
    assert confirmed.match.source.id == OTHER and [r.number for r in confirmed.rows] == ["1", "2"]
    assert "MangaListSearch" not in session.names()
    # "Not this series?": forgotten, looked up from scratch again.
    backend.forget_match(sid)
    assert backend.chapter_lookup(sid, ("1", "2"), (SERIES,)).match is None


def test_the_series_group_is_remembered_and_wins_over_the_default(world):
    backend, sid = world["backend"], world["sid"]
    link_mangadex(world["db"], world["root"].id)
    connect(world)
    lookup = backend.chapter_lookup(sid, ("331", "332"), (SERIES,))
    assert lookup.groups == {"Gamma Scans": 2, "Beta Group": 1} and lookup.group.group == "Gamma Scans"
    (row332,) = [r for r in lookup.rows if r.number == "332"]
    assert [c.scanlator for c in row332.options] == ["Gamma Scans", "Beta Group"]
    backend.set_series_group(sid, "Beta Group")
    chosen = backend.chapter_lookup(sid, ("331", "332"), (SERIES,))
    assert chosen.group.group == "Beta Group" and "your choice" in chosen.group.reason
    (row332,) = [r for r in chosen.rows if r.number == "332"]
    assert row332.default.scanlator == "Beta Group"
    backend.set_series_group(sid, None)
    assert backend.chapter_lookup(sid, ("331", "332"), (SERIES,)).group.group == "Gamma Scans"


def test_nothing_allowed_or_not_set_up_says_so(world):
    backend, sid = world["backend"], world["sid"]
    with pytest.raises(BackendError, match="not set up"):
        backend.chapter_lookup(sid, ("1",), (SERIES,))
    connect(world)
    backend.set_suwayomi_sources([])
    lookup = backend.chapter_lookup(sid, ("1",), (SERIES,))
    assert lookup.error and "no Suwayomi source is allowed" in lookup.error


def test_without_the_naming_module_nothing_is_moved_and_the_record_says_why(world, monkeypatch):
    from mangalist.downloads.chapter_arrivals import LazyNaming

    db, session, sid, sdir = world["db"], world["session"], world["sid"], world["dir"]
    link_mangadex(db, world["root"].id)
    backend = Backend(db, chapter_client_factory=lambda conn: SuwayomiClient(conn, session=session))  # default namer
    world["backend"] = backend
    connect(world)
    monkeypatch.setattr(LazyNaming, "module", "mangalist.naming_not_in_this_build")
    lookup = backend.chapter_lookup(sid, ("1",), (SERIES,))
    backend.send_chapters(sid, lookup.match, [lookup.rows[0].default], str(sdir), lookup.manga_title,
                          lookup.source_name)
    session.answers["MangaListChaptersById"] = "downloaded.json"
    src = finish(world, 1)
    before = sorted(os.listdir(sdir))
    message = backend.check_now()
    assert "1 waiting" in message and sorted(os.listdir(sdir)) == before and src.exists()
    (rec,) = backend.records(sid)
    assert rec.status == S.DOWNLOADED and "naming scheme is not available" in (rec.error or "")
    assert status_text(rec).startswith("Downloaded - the naming scheme is not available")
    assert "MangaListDeleteDownloaded" not in session.names()


def test_the_flow_files_under_the_real_naming_scheme(world):
    """Integrator check at merge (2026-10-10): the default namer is lane A's real :mod:`mangalist.naming` - the same flow
    files the chapters under the scheme's own names (no fake namer)."""
    import re

    db, session, sid, sdir = world["db"], world["session"], world["sid"], world["dir"]
    backend = Backend(db, chapter_client_factory=lambda conn: SuwayomiClient(conn, session=session))   # default namer
    world["backend"] = backend
    link_mangadex(db, world["root"].id)
    connect(world)
    lookup = backend.chapter_lookup(sid, ("1", "2"), (SERIES,))
    outcome = backend.send_chapters(sid, lookup.match, [row.default for row in lookup.rows], str(sdir),
                                    lookup.manga_title, lookup.source_name)
    assert outcome.error is None
    session.answers["MangaListQueue"] = "status_idle.json"
    session.answers["MangaListChaptersById"] = "downloaded.json"
    finish(world, 1), finish(world, 2)
    message = backend.check_now()
    assert "chapters: 2 checked: 2 filed, 0 failed, 0 waiting" in message, message
    scheme = re.compile(r"^Ch\. 000[12]\.00( Vol\. \d{3})? \(Example Title [12]\) \[Alpha Scans\]\.cbz$")
    filed = [n for n in os.listdir(sdir) if n.startswith("Ch. ")]
    assert len(filed) == 2 and all(scheme.match(n) for n in filed), filed
