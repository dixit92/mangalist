"""The carry-over from Suwayomi to the built-in extension host (2026-10-23): a fake Suwayomi (in memory, read-only) and
the fake host over real HTTP with MangaList's real host client and settings store. Made-up names."""

from __future__ import annotations

import json

import pytest

from mangalist import store
from mangalist.downloads.contracts import CHAPTER_BACKEND_EXTENSIONS
from mangalist.services.extensions import carry_over as co
from mangalist.services.extensions.client import ExtensionHostClient
from mangalist.store.downloads import META_SUWAYOMI_SERIES, SuwayomiSettings

from mangalist.services.suwayomi.client import SuwayomiRepo

from .carry_fakes import LOST, LOST_ID, MANGADEX, WEEB, FakeSuwayomiCarry, raw_choice, set_suwayomi_choice
from .fake_host import KEI_URL, MANGADEX_EN, MANGADEX_PKG, SECOND_URL, WEEB_ID, WEEB_PKG, FakeChapter, FakeHost, FakeManga

MD_URL = "/manga/00000000-0000-4000-8000-0000000c0000"


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "host", auto_download=False).start()
    h.add_manga(MANGADEX_EN, FakeManga(MD_URL, "Example Manga", [FakeChapter("/chapter/c1", "Ch. 1", 1.0, "Alpha")]))
    yield h
    h.stop()


@pytest.fixture
def suwayomi():
    s = FakeSuwayomiCarry()
    s.add(11, MANGADEX, MD_URL, "Example Manga")
    s.add(12, WEEB, "/series/weeb-1", "Weeb Story")
    s.add(13, LOST, "/lost/1", "Lost Story")
    return s


def choose(db):
    set_suwayomi_choice(db, 1, 11, MANGADEX_EN, "Example Manga", "mangadex-id")
    set_suwayomi_choice(db, 2, 12, WEEB_ID, "Weeb Story (picked)", "confirmed", group="Team W")
    set_suwayomi_choice(db, 3, 13, LOST_ID, "Lost Story", "confirmed")
    set_suwayomi_choice(db, 4, 99, MANGADEX_EN, "Gone From Suwayomi", "mangadex-id")


def run(db, suwayomi, host, **kw):
    client = ExtensionHostClient(host.url, timeout=10)
    try:
        return co.carry_over(db, suwayomi, client, pause=0, **kw)
    finally:
        client.close()


def meta_rows(db):
    with db.connect() as con:
        return sorted(tuple(r) for r in con.execute("SELECT key, value FROM meta").fetchall())


def statuses(report):
    return {r.series_id: r.status for r in report.series}


def test_carries_every_choice_installs_what_is_missing_and_reports_the_rest(db, suwayomi, host):
    choose(db)
    before = {k: db.get_meta(f"{META_SUWAYOMI_SERIES}{k}") for k in (1, 2, 3, 4)}
    seen = []
    report = run(db, suwayomi, host, progress=lambda done, total, text: seen.append((done, total, text)))
    assert statuses(report) == {1: co.CARRIED, 2: co.CARRIED, 3: co.NO_SOURCE, 4: co.UNKNOWN}
    assert report.counts() == {co.CARRIED: 2, co.ALREADY: 0, co.NO_SOURCE: 1, co.UNKNOWN: 1, co.FAILED: 0}
    # the extensions the choices need were installed in the host (MangaDex, Weeb); the lost one is not listed
    assert set(host.installed) == {MANGADEX_PKG, WEEB_PKG}
    states = {e.pkg: e.state for e in report.extensions}
    assert states == {MANGADEX_PKG: co.EXT_INSTALLED, WEEB_PKG: co.EXT_INSTALLED, LOST.extension: co.EXT_NOT_LISTED}
    # each carried series has the host's manga id, with the same how, title and source
    md = raw_choice(db, 1)
    host_md = host.manga_ids[(MANGADEX_EN, MD_URL)]
    assert (md["source_id"], md["manga_id"], md["manga_title"], md["how"]) == (MANGADEX_EN, host_md, "Example Manga",
                                                                               "mangadex-id")
    assert md["carried_from"]["suwayomi_manga_id"] == 11
    weeb = raw_choice(db, 2)
    assert (weeb["manga_id"], weeb["manga_title"], weeb["how"]) == (host.manga_ids[(WEEB_ID, "/series/weeb-1")],
                                                                    "Weeb Story (picked)", "confirmed")
    assert "group" not in weeb                          # the group carries over by itself (series_choice)
    assert raw_choice(db, 3) is None and raw_choice(db, 4) is None
    # the host registered by (source, url), Suwayomi's title for a manga it never saw
    assert host.mangas[host.manga_ids[(WEEB_ID, "/series/weeb-1")]]["title"] == "Weeb Story"
    # Suwayomi's choices are untouched (rollback stays possible)
    assert {k: db.get_meta(f"{META_SUWAYOMI_SERIES}{k}") for k in (1, 2, 3, 4)} == before
    # the backend reads it: with the built-in extensions chosen, the series has its host manga (and Suwayomi's group)
    settings = SuwayomiSettings(db)
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    assert settings.series_choice(2)["manga_id"] == weeb["manga_id"] and settings.series_choice(2)["group"] == "Team W"
    # the reasons are readable; progress ran to the end
    lost = next(r for r in report.series if r.series_id == 3)
    assert "Lost Source" in lost.message and "not in the host's repositories" in lost.message
    assert seen[-1][0] == seen[-1][1] == 4
    text = "\n".join(report.lines())
    assert "Carried 2" in text and "source not installed 1" in text and "Suwayomi does not know the manga 1" in text
    assert "Extensions installed: MangaDex" in text and "Gone From Suwayomi" in text


def test_suwayomi_is_only_read(db, suwayomi, host):
    choose(db)
    run(db, suwayomi, host)
    assert {c[0] for c in suwayomi.calls} == {"sources", "installed_extensions", "extension_repos", "mangas_by_id"}
    (asked,) = [c[1] for c in suwayomi.calls if c[0] == "mangas_by_id"]
    assert asked == (11, 12, 13, 99)


def test_a_second_run_carries_nothing_new(db, suwayomi, host):
    choose(db)
    run(db, suwayomi, host)
    first = {k: raw_choice(db, k) for k in (1, 2)}
    registered = len(host.registered)
    suwayomi.calls.clear()
    again = run(db, suwayomi, host)
    assert statuses(again) == {1: co.ALREADY, 2: co.ALREADY, 3: co.NO_SOURCE, 4: co.UNKNOWN}
    assert {k: raw_choice(db, k) for k in (1, 2)} == first
    assert len(host.registered) == registered                       # nothing registered again
    assert {e.pkg: e.state for e in again.extensions}[MANGADEX_PKG] == co.EXT_PRESENT
    (asked,) = [c[1] for c in suwayomi.calls if c[0] == "mangas_by_id"]
    assert asked == (13, 99)                                        # only what is still not carried
    assert again.summary().startswith("Carried 0, already there 2")


def test_never_overwrites_a_choice_made_on_the_host(db, suwayomi, host):
    choose(db)
    SuwayomiSettings(db).set_backend(CHAPTER_BACKEND_EXTENSIONS)
    SuwayomiSettings(db).set_series_choice(1, source_id=MANGADEX_EN, manga_id=777, manga_title="Picked On Host",
                                           how="confirmed")
    SuwayomiSettings(db).set_series_choice(2, group="Host Group")        # a group only: no manga there yet
    report = run(db, suwayomi, host)
    assert statuses(report)[1] == co.ALREADY
    assert raw_choice(db, 1) == {"source_id": MANGADEX_EN, "manga_id": 777, "manga_title": "Picked On Host",
                                 "how": "confirmed"}
    assert (MANGADEX_EN, MD_URL) not in host.registered
    two = raw_choice(db, 2)
    assert statuses(report)[2] == co.CARRIED and two["group"] == "Host Group" and isinstance(two["manga_id"], int)


def test_the_store_write_is_insert_if_absent(db):
    s = SuwayomiSettings(db)
    assert s.carry_series_choice(5, {"manga_id": 1, "how": "confirmed"}) is True
    assert s.carry_series_choice(5, {"manga_id": 2, "how": "mangadex-id"}) is False
    assert raw_choice(db, 5) == {"manga_id": 1, "how": "confirmed"}
    db.set_meta("extensions.series.6", "not json")
    assert s.carry_series_choice(6, {"manga_id": 3}) is True and raw_choice(db, 6) == {"manga_id": 3}
    db.set_meta("extensions.series.7", None)                            # forgotten
    assert s.carry_series_choice(7, {"manga_id": 4}) is True
    assert sorted(s.extensions_series_choices()) == [5, 6, 7]


def test_dry_run_writes_nothing_anywhere(db, suwayomi, host):
    choose(db)
    snapshot = meta_rows(db)
    report = run(db, suwayomi, host, dry_run=True)
    assert report.dry_run
    assert statuses(report) == {1: co.CARRIED, 2: co.CARRIED, 3: co.NO_SOURCE, 4: co.UNKNOWN}
    assert {e.pkg: e.state for e in report.extensions}[WEEB_PKG] == co.EXT_WOULD_INSTALL
    assert host.installed == {} and host.registered == [] and host.manga_ids == {}
    assert all(m == "GET" for m, _ in host.requests)
    assert meta_rows(db) == snapshot
    assert report.lines()[0].startswith("Would carry over from Suwayomi: Would carry 2")
    assert "Extensions to install: MangaDex" in "\n".join(report.lines())


def test_no_install_leaves_the_series_whose_extension_is_missing(db, suwayomi, host):
    choose(db)
    host.installed[MANGADEX_PKG] = "1.4.200"
    report = run(db, suwayomi, host, install=False)
    assert statuses(report) == {1: co.CARRIED, 2: co.NO_SOURCE, 3: co.NO_SOURCE, 4: co.UNKNOWN}
    assert WEEB_PKG not in host.installed
    assert {e.pkg: e.state for e in report.extensions}[WEEB_PKG] == co.EXT_NOT_INSTALLED


def test_an_extension_that_cannot_be_installed_is_reported_and_its_series_wait(db, suwayomi, host):
    choose(db)
    host.install_errors[WEEB_PKG] = (400, "the signature of weeb.apk does not match the repository's signing key")
    report = run(db, suwayomi, host)
    weeb = next(e for e in report.extensions if e.pkg == WEEB_PKG)
    assert weeb.state == co.EXT_FAILED and "signature" in weeb.message
    assert statuses(report)[2] == co.NO_SOURCE and statuses(report)[1] == co.CARRIED
    assert "Extension could not be installed: Weeb Example (1 series): the signature" in "\n".join(report.lines())
    # fixed later: the next run carries it
    del host.install_errors[WEEB_PKG]
    assert statuses(run(db, suwayomi, host))[2] == co.CARRIED


def test_the_suwayomi_repositories_the_host_lacks_are_added_and_remembered(db, suwayomi, host):
    suwayomi.repos = [SuwayomiRepo(KEI_URL, "Keiyoushi"), SuwayomiRepo(SECOND_URL, "Second"),
                      SuwayomiRepo("https://not-a-repo.example/index.json", "Broken")]
    report = run(db, suwayomi, host, dry_run=True)
    assert report.repos_added == [SECOND_URL, "https://not-a-repo.example/index.json"]
    assert list(host.repositories) == [KEI_URL]
    report = run(db, suwayomi, host)
    assert report.repos_added == [SECOND_URL]
    assert "not an extension repository" in report.repos_failed["https://not-a-repo.example/index.json"]
    assert list(host.repositories) == [KEI_URL, SECOND_URL]
    assert SuwayomiSettings(db).extension_host().repos == (KEI_URL, SECOND_URL)      # the host's next start
    assert run(db, suwayomi, host).repos_added == []                                    # idempotent


def test_the_allowed_sources_extensions_are_installed_too(db, suwayomi, host):
    SuwayomiSettings(db).set_sources([MANGADEX_EN, WEEB_ID])
    set_suwayomi_choice(db, 1, 11, MANGADEX_EN, "Example Manga")
    report = run(db, suwayomi, host)
    assert set(host.installed) == {MANGADEX_PKG, WEEB_PKG}
    assert {e.pkg: e.series for e in report.extensions} == {MANGADEX_PKG: 1, WEEB_PKG: 0}


def test_a_failing_registration_is_failed_and_the_host_going_away_stops_the_run(db, suwayomi, host, monkeypatch):
    choose(db)
    host.installed.update({MANGADEX_PKG: "1.4.200", WEEB_PKG: "1.4.3"})
    calls = []
    real = ExtensionHostClient.register_manga

    def flaky(self, source_id, url, title=""):
        calls.append(url)
        if url == MD_URL:
            from mangalist.services.extensions.client import ExtensionHostError

            raise ExtensionHostError("the host's database is locked", 500)
        return real(self, source_id, url, title)

    monkeypatch.setattr(ExtensionHostClient, "register_manga", flaky)
    report = run(db, suwayomi, host)
    one = next(r for r in report.series if r.series_id == 1)
    assert one.status == co.FAILED and "locked" in one.message
    assert statuses(report)[2] == co.CARRIED and raw_choice(db, 1) is None
    # the host goes away: the rest is failed without a request each
    from mangalist.services.extensions.client import HostUnreachable

    set_suwayomi_choice(db, 5, 12, WEEB_ID, "Weeb Again")
    set_suwayomi_choice(db, 6, 11, MANGADEX_EN, "Example Again")

    def down(self, source_id, url, title=""):
        calls.append(url)
        raise HostUnreachable("the extension host could not be reached at x (ConnectionError)")

    monkeypatch.setattr(ExtensionHostClient, "register_manga", down)
    calls.clear()
    report = run(db, suwayomi, host)
    assert [r.status for r in report.series if r.series_id in (1, 5, 6)] == [co.FAILED] * 3
    assert len(calls) == 1


def test_unreachable_programs_stop_before_anything_is_written(db, suwayomi, host):
    choose(db)
    suwayomi.unreachable = True
    with pytest.raises(co.CarryOverError, match="Suwayomi could not be asked"):
        run(db, suwayomi, host)
    suwayomi.unreachable = False
    client = ExtensionHostClient("http://127.0.0.1:9", timeout=2)
    with pytest.raises(co.CarryOverError, match="extension host could not be asked"):
        co.carry_over(db, suwayomi, client, pause=0)
    assert raw_choice(db, 1) is None and host.installed == {}


def test_counts_for_the_button_and_the_stored_clients(db, suwayomi, host):
    assert co.has_suwayomi_choices(db) == 0
    choose(db)
    db.set_meta(f"{META_SUWAYOMI_SERIES}9", json.dumps({"group": "Only A Group"}))     # no manga: nothing to carry
    assert co.has_suwayomi_choices(db) == 4 and co.not_yet_carried(db) == 4
    run(db, suwayomi, host)
    assert co.not_yet_carried(db) == 2
    with pytest.raises(co.CarryOverError, match="Suwayomi is not set up"):
        co.clients_from_store(db)
    from mangalist.downloads.contracts import SuwayomiConnection

    SuwayomiSettings(db).save_connection(SuwayomiConnection(base_url="http://suwayomi.example:4567"))
    suw, hc = co.clients_from_store(db, host_url=host.url)
    assert suw.base_url == "http://suwayomi.example:4567" and hc.base_url == host.url


def test_over_the_real_suwayomi_client_only_queries_are_sent(db, host):
    """The real SuwayomiClient on a fake GraphQL session: every operation the carry-over sends is a query."""
    from mangalist.downloads.contracts import SuwayomiConnection
    from mangalist.services.suwayomi import SuwayomiClient

    from ..suwayomi.conftest import FakeSession

    session = FakeSession()
    session.answers["MangaListSources"] = {"data": {"sources": {"nodes": [
        {"id": MANGADEX_EN, "name": "MangaDex", "lang": "en", "displayName": "MangaDex (EN)",
         "extension": {"pkgName": MANGADEX_PKG, "name": "MangaDex"}}]}}}
    session.answers["MangaListMangasById"] = lambda v: {"data": {"mangas": {"nodes": [
        {"id": i, "title": "Example Manga", "url": MD_URL, "sourceId": MANGADEX_EN, "inLibrary": True}
        for i in v["ids"] if i == 11]}}}
    set_suwayomi_choice(db, 1, 11, MANGADEX_EN, "Example Manga")
    client = SuwayomiClient(SuwayomiConnection(base_url="http://suwayomi.example:4567"), session=session)
    report = run(db, client, host)
    assert statuses(report) == {1: co.CARRIED}
    assert all(r["query"].startswith("query ") for r in session.requests)
    assert {r["name"] for r in session.requests} == {"MangaListSources", "MangaListInstalledExtensions",
                                                     "MangaListExtensionStores", "MangaListMangasById"}


def test_the_command_line_prints_the_report(db, suwayomi, host, monkeypatch, capsys, tmp_path):
    from mangalist import paths
    from mangalist.services.extensions.__main__ import main

    choose(db)
    monkeypatch.setattr(co, "_host_up", lambda _db, _url: None)        # the host on demand: tests/.../test_on_demand.py
    monkeypatch.setattr(co, "clients_from_store",
                        lambda _db, host_url=None, timeout=120.0: (suwayomi, ExtensionHostClient(host_url or host.url)))
    assert main(["carry-over", "--dry-run", "--db", str(paths.db_file())]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Would carry over from Suwayomi: Would carry 2") and "Extensions to install" in out
    assert host.installed == {} and raw_choice(db, 1) is None
    assert main(["--url", host.url, "carry-over", "--every-series"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Did carry over from Suwayomi: Carried 2") and "-> host manga" in out
    assert raw_choice(db, 1) is not None
    assert main(["carry-over", "--db", str(tmp_path / "nothing.db")]) == 2
    assert not (tmp_path / "nothing.db").exists()
    monkeypatch.setattr(co, "clients_from_store", lambda *a, **k: (_ for _ in ()).throw(co.CarryOverError("no")))
    assert main(["carry-over"]) == 1 and "error: no" in capsys.readouterr().err
