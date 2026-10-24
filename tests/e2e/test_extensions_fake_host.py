"""The built-in extensions backend end to end against the FAKE host (an in-process HTTP server implementing the /compat
contract; ``tests/services/extensions/fake_host.py``): MangaList's real client, GUI backend, send path, downloads job and
chapter arrivals pass, no JVM, no network beyond the loopback. Runs in CI; the live cases are
``test_extensions_live.py`` (opt-in)."""

from __future__ import annotations

import os

import pytest

from mangalist.downloads.adapter import Backend
from mangalist.downloads.contracts import CHAPTER_BACKEND_EXTENSIONS, CHAPTER_BACKEND_SUWAYOMI, DownloadStatus
from mangalist.gui.downloads_backend import BackendError
from mangalist.store.downloads import ExtensionHostConfig

from ..services.extensions.fake_host import (
    MANGADEX_EN, MANGADEX_PKG, WEEB_ID, WEEB_PKG, FakeChapter, FakeHost, FakeManga,
)
from .harness import assert_filed, choose_builtin, download_one_chapter, library_with_series

SERIES = "Example Series"


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "exthost").start()
    yield h
    h.stop()


def catalog(host: FakeHost) -> None:
    host.add_manga(MANGADEX_EN, FakeManga("/manga/00000000-0000-4000-8000-0000000c0000", "Example Series", [
        FakeChapter("/chapter/c1", "Vol.1 Ch.1 - Example Title 1", 1.0, "Alpha Scans", pages=4),
        FakeChapter("/chapter/c2", "Vol.1 Ch.2 - Example Title 2", 2.0, "Alpha Scans"),
        FakeChapter("/chapter/c2b", "Vol.1 Ch.2 - Example Title 2", 2.0, "Beta Group")]))
    host.add_manga(WEEB_ID, FakeManga("/series/example", "Example Series: The Webcomic", [
        FakeChapter("/series/example/12-5", "Chapter 12.5", 12.5, None, pages=2)]))


def test_one_chapter_from_send_to_filed_through_the_built_in_extensions(db, tmp_path, host):
    catalog(host)
    sid = library_with_series(db, tmp_path, SERIES)
    backend = Backend(db)
    assert backend.chapter_backend() == CHAPTER_BACKEND_SUWAYOMI and not backend.suwayomi_ready()
    choose_builtin(backend, host.url)
    assert backend.suwayomi_ready()                           # the host has an address: chapters can be looked up
    # The Extensions page installs MangaDex.
    rows = {r.pkg: r for r in backend.extension_repo()}
    assert not rows[MANGADEX_PKG].installed
    backend.install_extension(MANGADEX_PKG)
    assert backend.extension_repo()[0].pkg == MANGADEX_PKG and backend.extension_repo()[0].installed

    out = download_one_chapter(db, backend, sid, source_id=MANGADEX_EN, titles=(SERIES,), number="1")
    assert_filed(out, "1")
    assert out.filed.name == "Ch. 0001.00 Vol. 001 (Example Title 1) [Alpha Scans].cbz"
    assert out.pages == ["001.png", "002.png", "003.png", "004.png"]
    assert out.filed.parent == tmp_path / "library" / "Manga" / SERIES
    assert backend.load_extension_host().downloads_path == str(host.downloads_path)    # learned from the host
    assert not (host.downloads_path / "mangas" / "MangaDex (EN)").exists()           # emptied folders gone too
    assert any("chapters: 1 checked: 1 filed" in said for said in out.checks)
    # The series' choice is the host's own (its manga ids are not Suwayomi's)
    assert backend.suwayomi.series_choice(sid)["manga_id"] == 1
    backend.suwayomi.set_backend(CHAPTER_BACKEND_SUWAYOMI)
    assert backend.suwayomi.series_choice(sid) == {}


def test_a_title_only_source_with_a_decimal_chapter_and_no_group(db, tmp_path, host):
    catalog(host)
    host.installed[WEEB_PKG] = "1.4.3"
    sid = library_with_series(db, tmp_path, SERIES)
    backend = Backend(db)
    choose_builtin(backend, host.url)
    out = download_one_chapter(db, backend, sid, source_id=WEEB_ID, titles=(SERIES,), number="12.5",
                               pick_candidate=lambda m: m.manga.url == "/series/example")
    assert_filed(out, "12.5")
    assert out.filed.name == "Ch. 0012.50.cbz"


def test_a_download_the_host_gives_up_on_waits_and_retry_puts_it_back(db, tmp_path, host):
    catalog(host)
    host.installed[MANGADEX_PKG] = "1.4.200"
    host.fail_urls.add("/chapter/c2")
    sid = library_with_series(db, tmp_path, SERIES)
    backend = Backend(db)
    choose_builtin(backend, host.url)
    backend.test_extension_host()
    backend.set_suwayomi_sources([MANGADEX_EN])
    lookup = backend.chapter_lookup(sid, ("2",), (SERIES,))
    backend.confirm_match(sid, lookup.candidates[0])
    lookup = backend.chapter_lookup(sid, ("2",), (SERIES,))
    pick = next(c for c in lookup.rows[0].options if c.scanlator == "Alpha Scans")
    sent = backend.send_chapters(sid, lookup.match, [pick], lookup.placement.target_dir, lookup.manga_title,
                                 lookup.source_name)
    rid = sent.records[0].id
    from ..services.extensions.fake_host import wait_for

    assert wait_for(lambda: any(q["state"] == "ERROR" for q in host.queue))
    said = backend.check_now()
    rec = backend.chapter_ledger.get(rid)
    assert rec.status == DownloadStatus.SENT and "waiting" in said
    # In progress offers Retry for it: stuck by its state, whatever the note's words (transition audit B1)
    from mangalist.downloads.chapter_retry import is_stuck

    assert is_stuck(rec) and rec.error.startswith("The extension host could not download it")
    # Switching backends now is refused: a chapter download is still with the host.
    with pytest.raises(BackendError, match="still in progress with the extension host"):
        backend.set_chapter_backend(CHAPTER_BACKEND_SUWAYOMI)
    host.fail_urls.clear()
    backend.retry_chapter_downloads([rid])
    assert wait_for(lambda: not host.queue)
    backend.check_now()
    assert backend.chapter_ledger.get(rid).status == DownloadStatus.REMOVED


def test_the_host_down_is_said_and_nothing_changes(db, tmp_path, host):
    sid = library_with_series(db, tmp_path, SERIES)
    backend = Backend(db)
    choose_builtin(backend, "http://127.0.0.1:9")            # nothing listens there
    with pytest.raises(BackendError, match="extension host could not be reached"):
        backend.test_extension_host()
    with pytest.raises(BackendError, match="extension host could not be reached"):
        backend.chapter_lookup(sid, ("1",), (SERIES,))
    with pytest.raises(BackendError, match="extension host could not be reached"):
        backend.extension_repo()


def test_the_extensions_page_calls(db, host):
    backend = Backend(db)
    choose_builtin(backend, host.url)
    backend.install_extension(MANGADEX_PKG)
    host.repo[0].version = "1.4.201"                          # the repository has a newer MangaDex
    (md,) = [r for r in backend.extension_repo() if r.pkg == MANGADEX_PKG]
    assert md.installed and md.has_update and md.installed_version == "1.4.200"
    assert backend.update_extension(MANGADEX_PKG).version == "1.4.201"
    assert not [r for r in backend.extension_repo() if r.pkg == MANGADEX_PKG][0].has_update
    prefs = backend.source_preferences(MANGADEX_EN)
    assert [p.kind for p in prefs] == ["list", "switch", "multiselect", "edittext", "checkbox"]
    assert prefs[2].value == ("safe", "suggestive") and prefs[1].value is False
    after = backend.set_source_preference(MANGADEX_EN, prefs[1], True)
    assert after[1].value is True and host.preferences[MANGADEX_EN][1]["currentValue"] is True
    after = backend.set_source_preference(MANGADEX_EN, prefs[2], ["safe"])
    assert after[2].value == ("safe",)
    backend.uninstall_extension(MANGADEX_PKG)
    assert not any(r.installed for r in backend.extension_repo())
    with pytest.raises(BackendError):
        backend.uninstall_extension(MANGADEX_PKG)


def test_settings_check_addresses_and_the_download_folder_override(db, tmp_path, host):
    backend = Backend(db)
    with pytest.raises(BackendError):
        backend.save_extension_host(ExtensionHostConfig(url="ftp://nope"))
    with pytest.raises(BackendError, match="FlareSolverr"):
        backend.save_extension_host(ExtensionHostConfig(flaresolverr_url="192.168.2.50:8191"))
    seen = tmp_path / "seen-by-mangalist"
    seen.mkdir()
    backend.save_extension_host(ExtensionHostConfig(url=host.url + "/", download_dir=str(seen)))
    assert backend.load_extension_host().url == host.url
    check = backend.test_extension_host()
    assert check.downloads_path == str(host.downloads_path) and check.files_from == str(seen)
    assert check.folder_found is True and any("No extension is installed" in n for n in check.notes())
    backend.set_chapter_backend(CHAPTER_BACKEND_EXTENSIONS)
    conn = backend.suwayomi.connection()
    assert conn.builtin and conn.download_dir == str(seen) and conn.base_url == host.url
