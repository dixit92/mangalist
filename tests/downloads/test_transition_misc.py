"""Smaller pieces of the Suwayomi -> built-in extensions transition fixes (2026-10-24): the chapter gate's state per
program (audit B9), the host's names on Windows and long paths (B10), the switch while a pass runs (B5), the other
program's records in the downloads job (C2), the host database's id (B4), the container's locale (D1). Made-up names."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mangalist.downloads import chapter_gate, host_identity
from mangalist.downloads.chapter_arrivals import expected_path, long_path, suwayomi_name
from mangalist.downloads.chapter_backend import backend_of, name_of_backend, record_extra
from mangalist.downloads.contracts import (
    CHAPTER_BACKEND_EXTENSIONS as EXT,
    CHAPTER_BACKEND_SUWAYOMI as SUW,
    TOOL_SUWAYOMI,
    DownloadStatus as S,
    SuwayomiConnection,
)
from mangalist.store.downloads import DownloadLedger, SuwayomiSettings

from .suwayomi_fakes import MANGA, MANGADEX, FakeSuwayomi, chapter

REPO = Path(__file__).resolve().parents[2]


def test_the_gate_keeps_each_programs_state_apart(db):
    from datetime import datetime, timezone

    at = datetime(2026, 10, 24, tzinfo=timezone.utc)
    chapter_gate.save(db, 7, chapter_gate.Kept(2432, "md-uuid", "c1", "x", at))                 # Suwayomi's manga
    assert chapter_gate.load(db, 7, EXT).suwayomi_manga == 2432          # no host state yet: the shared one ...
    chapter_gate.save(db, 7, chapter_gate.Kept(73, "md-uuid", "c1", "x", at), EXT)               # ... until it has one
    assert chapter_gate.load(db, 7).suwayomi_manga == 2432 and chapter_gate.load(db, 7, EXT).suwayomi_manga == 73
    assert chapter_gate.load(db, 7, SUW).suwayomi_manga == 2432


def test_names_on_windows_for_the_hosts_files():
    assert suwayomi_name("CON") == "CON" and suwayomi_name("CON", windows=True) == "_CON"
    assert suwayomi_name("nul.txt", windows=True) == "_nul.txt" and suwayomi_name("CONAN", windows=True) == "CONAN"
    path = expected_path("/dl", "MangaDex (EN)", "AUX", "Ch.1", None, windows=True)
    assert path == os.path.join("/dl", "mangas", "MangaDex (EN)", "_AUX", "Ch.1.cbz")
    long = "C:\\dl\\" + "a" * 300
    assert long_path(long, nt=True) == "\\\\?\\" + long or not os.path.isabs(long)
    assert long_path("C:\\short", nt=True) == "C:\\short" and long_path(long, nt=False) == long
    assert long_path("\\\\server\\share\\" + "b" * 300, nt=True).startswith("\\\\?\\UNC\\") or os.name != "nt"


def test_no_switch_while_a_find_pass_holds_the_programs_client(db):
    from mangalist.downloads import finder
    from mangalist.downloads.adapter import Backend
    from mangalist.gui.downloads_backend import BackendError

    backend = Backend(db)
    with finder._PASS:
        with pytest.raises(BackendError, match="a find pass is running"):
            backend.set_chapter_backend(EXT)
    backend.set_chapter_backend(EXT)
    assert backend.chapter_backend() == EXT


def test_the_other_programs_records_wait_without_failing_the_job(db, tmp_path, cseries):
    """Host chosen; a Suwayomi chapter filed before the switch still waits for Suwayomi to delete its copy, and Suwayomi
    is stopped: the job says so, and is not an error."""
    from mangalist.downloads import chapters as chm
    from mangalist.headless.downloads_job import chapters_pass
    from mangalist.headless.jobs import JobContext

    sid, folder = cseries
    downloads = tmp_path / "suwayomi" / "downloads"
    (downloads / "mangas").mkdir(parents=True)
    suwa = FakeSuwayomi(downloads, chapters=[chapter(4, "4")])
    ledger = DownloadLedger(db, tool=TOOL_SUWAYOMI)
    (rec,) = chm.send_chapters(suwa, ledger, sid, chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX), suwa.chapter_list,
                               str(folder)).records
    assert rec.backend == SUW
    settings = SuwayomiSettings(db)
    settings.save_connection(SuwayomiConnection(base_url="http://suwayomi.invalid:4567", download_dir=str(downloads)))
    settings.set_backend(EXT)
    suwa.unreachable = True
    asked = []

    def factory(conn):
        asked.append(conn.backend)
        return suwa

    report, note = chapters_pass(JobContext(), DownloadLedger(db), chapter_client_factory=factory)
    assert asked == [SUW] and note is None and report.error is None
    assert report.waiting and "could not be asked" in report.waiting[0][1]
    assert ledger.get(rec.id).status == S.SENT


@pytest.fixture
def cseries(db, tmp_path):
    from .fakes import data, scan

    root = tmp_path / "library" / "Manga"
    folder = root / "Series C"
    folder.mkdir(parents=True)
    (folder / "0001 [Ch. 0001].cbz").write_bytes(data("c1"))
    r = db.add_root(str(root), "Manga")
    scan(db)
    return db.get_series(r.id, "Series C").id, folder


class Host:
    backend = EXT

    def __init__(self, instance):
        self._instance = instance

    def instance(self):
        if isinstance(self._instance, Exception):
            raise self._instance
        return self._instance


def test_the_host_database_id_is_adopted_then_compared(db):
    assert host_identity.check(db, FakeSuwayomi()) is None and host_identity.known(db) == ""   # Suwayomi: nothing
    assert host_identity.check(db, Host("")) is None and host_identity.known(db) == ""        # an old host: nothing
    assert host_identity.check(db, Host(ConnectionError("down"))) is None
    assert host_identity.check(db, Host("db-1")) is None and host_identity.known(db) == "db-1"
    SuwayomiSettings(db).set_series_choice(3, backend=EXT, manga_id=5, source_id="1", manga_url="/m/5", how="confirmed")
    assert host_identity.check(db, Host("db-1")) is None
    assert SuwayomiSettings(db).series_choice(3, EXT)["manga_id"] == 5
    said = host_identity.check(db, Host("db-2"))
    assert said and "1 series choice" in said
    choice = SuwayomiSettings(db).series_choice(3, EXT)
    assert "manga_id" not in choice and choice["manga_url"] == "/m/5" and choice["stale_manga_id"]["manga_id"] == 5
    assert record_extra(Host("db-2")) == {"host_instance": "db-2"} and record_extra(FakeSuwayomi()) == {}


def test_the_program_of_a_client():
    assert backend_of(Host("x")) == EXT and backend_of(FakeSuwayomi()) == SUW and backend_of(object()) == SUW
    assert name_of_backend(EXT) == "the extension host" and name_of_backend("") == "Suwayomi"


def test_the_image_runs_everything_in_a_utf8_locale_and_downloads_onto_the_data_share():
    text = (REPO / "packaging" / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "LANG=C.UTF-8 \\" in text and "LC_ALL=C.UTF-8 \\" in text                       # audit D1
    assert "MANGALIST_EXTENSION_DOWNLOADS=/data/appdata/" in text                           # audit B6
