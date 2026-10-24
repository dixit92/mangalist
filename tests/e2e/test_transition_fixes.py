"""The Suwayomi -> built-in extensions transition fixes (2026-10-24) end to end against the FAKE host: a replaced host
database is noticed and its ids found again (audit B4), the switch guard and FILED records (C2, B1), a waiting chapter
finding sent after a switch (B3), a match found before a switch (B5), and the carry-back to Suwayomi (B8). MangaList's
real client, backend, ledger and arrivals pass; made-up names."""

from __future__ import annotations

import pytest

from mangalist.downloads import host_identity
from mangalist.downloads.adapter import Backend
from mangalist.downloads.contracts import (
    CHAPTER_BACKEND_EXTENSIONS as EXT,
    CHAPTER_BACKEND_SUWAYOMI as SUW,
    DownloadStatus as S,
)
from mangalist.gui.downloads_backend import BackendError
from mangalist.services.extensions.client import ExtensionHostClient

from ..services.extensions.fake_host import MANGADEX_EN, MANGADEX_PKG, FakeHost
from .harness import assert_filed, choose_builtin, download_one_chapter, library_with_series
from .test_extensions_fake_host import SERIES, catalog

URL = "/manga/00000000-0000-4000-8000-0000000c0000"


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "exthost").start()
    yield h
    h.stop()


@pytest.fixture
def ready(db, tmp_path, host):
    """The host chosen, MangaDex installed, chapter 1 filed through a confirmed title match: (backend, series id)."""
    catalog(host)
    host.installed[MANGADEX_PKG] = "1.4.200"
    sid = library_with_series(db, tmp_path, SERIES)
    backend = Backend(db)
    choose_builtin(backend, host.url)
    out = download_one_chapter(db, backend, sid, source_id=MANGADEX_EN, titles=(SERIES,), number="1")
    assert_filed(out, "1")
    return backend, sid


def test_a_replaced_host_database_is_noticed_and_the_manga_found_again(db, host, ready):
    backend, sid = ready
    choice = backend.suwayomi.series_choice(sid)
    assert choice["manga_id"] == 1 and choice["manga_url"] == URL and choice["host_instance"] == "fake-host-db-1"
    assert host_identity.known(db) == "fake-host-db-1"
    # a chapter on its way when the database goes
    host.fail_urls.add("/chapter/c2")
    lookup = backend.chapter_lookup(sid, ("2",), (SERIES,))
    pick = next(c for c in lookup.rows[0].options if c.scanlator == "Alpha Scans")
    (rec,) = backend.send_chapters(sid, lookup.match, [pick], lookup.placement.target_dir, lookup.manga_title,
                                   lookup.source_name).records
    # host.db deleted: ids start at 1 again - another manga gets 1 first
    host.reset_database()
    ExtensionHostClient(host.url).register_manga(MANGADEX_EN, "/manga/another-one", "Another Manga")
    lookup = backend.chapter_lookup(sid, ("2",), (SERIES,))
    assert lookup.match is not None and lookup.match.manga.id == 2 and lookup.match.manga.url == URL
    choice = backend.suwayomi.series_choice(sid)
    assert choice["manga_id"] == 2 and choice["host_instance"] == "fake-host-db-2" and "stale_manga_id" not in choice
    got = backend.chapter_ledger.get(rec.id)
    assert got.status == S.FAILED and got.error == host_identity.RESET_NOTE
    assert host_identity.known(db) == "fake-host-db-2"


def test_without_the_check_the_old_id_names_another_manga(db, host, ready, monkeypatch):
    """Mutation check of B4: the stored id 1 would ask for the other manga."""
    backend, sid = ready
    host.reset_database()
    ExtensionHostClient(host.url).register_manga(MANGADEX_EN, "/manga/another-one", "Another Manga")
    monkeypatch.setattr(host_identity, "check", lambda *_a, **_k: None)
    lookup = backend.chapter_lookup(sid, ("2",), (SERIES,))
    assert lookup.match.manga.id == 1                           # the wrong manga: what the check prevents


def test_switching_back_needs_no_impossible_action(db, host, ready):
    backend, sid = ready
    host.fail_urls.add("/chapter/c2")
    lookup = backend.chapter_lookup(sid, ("2",), (SERIES,))
    pick = next(c for c in lookup.rows[0].options if c.scanlator == "Alpha Scans")
    (rec,) = backend.send_chapters(sid, lookup.match, [pick], lookup.placement.target_dir, lookup.manga_title,
                                   lookup.source_name).records
    with pytest.raises(BackendError, match="still in progress with the extension host"):
        backend.set_chapter_backend(SUW)
    # the guard's advice works for a chapter that is not stuck (yet)
    assert backend.cancel_chapter_downloads([rec.id]) == 1
    backend.set_chapter_backend(SUW)
    assert backend.chapter_backend() == SUW


def test_a_filed_record_does_not_hold_the_switch_and_is_finished_by_its_program(db, host, ready, monkeypatch):
    backend, sid = ready
    host.auto_download = False
    lookup = backend.chapter_lookup(sid, ("2",), (SERIES,))
    pick = next(c for c in lookup.rows[0].options if c.scanlator == "Alpha Scans")
    (rec,) = backend.send_chapters(sid, lookup.match, [pick], lookup.placement.target_dir, lookup.manga_title,
                                   lookup.source_name).records
    host.run_downloads()
    deletes = []
    real = host._compat

    def no_delete(method, path, parts, body):
        if path == "/chapters/delete-downloaded":
            deletes.append(body)
            return 500, {"error": "busy"}
        return real(method, path, parts, body)

    monkeypatch.setattr(host, "_compat", no_delete)
    backend.check_now()
    assert backend.chapter_ledger.get(rec.id).status == S.FILED       # the host did not delete its copy yet
    backend.set_chapter_backend(SUW)                                   # FILED only: allowed (audit C2)
    monkeypatch.setattr(host, "_compat", real)
    said = backend.check_now()                                         # Suwayomi is not set up: the host finishes it
    assert backend.chapter_ledger.get(rec.id).status == S.REMOVED, said


def test_a_match_found_before_a_switch_is_not_sent_to_the_other_program(db, host, ready):
    backend, sid = ready
    lookup = backend.chapter_lookup(sid, ("2",), (SERIES,))
    pick = lookup.rows[0].options[0]
    backend.suwayomi.set_backend(SUW)                                   # switched while the panel was open
    with pytest.raises(BackendError, match="look the series up again"):
        backend.send_chapters(sid, lookup.match, [pick], lookup.placement.target_dir)
    with pytest.raises(BackendError, match="look the series up again"):
        backend.confirm_match(sid, lookup.match)
    assert backend.suwayomi.series_choice(sid, SUW) == {}


def test_the_backend_asks_for_the_hosts_queue(db, host, ready):
    backend, _sid = ready
    assert backend.chapter_queue() == []
