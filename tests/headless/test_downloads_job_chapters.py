"""The ``downloads`` job with chapter downloads (the Suwayomi MVP): a chapter arrivals pass when chapter records are in
progress, the after-filing step sees the chapters filed, the Suwayomi password never reaches the result or the log, and
without chapter records nothing about the job changes."""

from __future__ import annotations

import logging

from mangalist import store
from mangalist.downloads import chapters as chm
from mangalist.downloads.contracts import TOOL_SUWAYOMI, QbtConnection, SuwayomiConnection
from mangalist.headless.downloads_job import make_downloads_job
from mangalist.headless.jobs import JobContext
from mangalist.store.downloads import DownloadLedger, SuwayomiSettings

from ..downloads.fakes import HASH, FakeQbt, candidate, data, scan
from ..downloads.suwayomi_fakes import MANGA, MANGADEX, FakeNamer, FakeSuwayomi, chapter

SECRET = "suwa-not-a-real-password"


def _setup(tmp_path):
    store.reset_stores()
    db = store.get_store()
    root = tmp_path / "library" / "Manga"
    (root / "Series C").mkdir(parents=True)
    (root / "Series C" / "0001 [Ch. 0001 - One [Alpha Scans]].cbz").write_bytes(data("c1"))
    r = db.add_root(str(root))
    scan(db)
    ledger = DownloadLedger(db)
    downloads = tmp_path / "suwayomi"
    (downloads / "mangas").mkdir(parents=True)
    suwa = FakeSuwayomi(downloads, chapters=[chapter(2, "2")])
    sid = db.get_series(r.id, "Series C").id
    return ledger, suwa, sid, root / "Series C", downloads


def _send(ledger, suwa, sid, folder):
    return chm.send_chapters(suwa, ledger.for_tool(TOOL_SUWAYOMI), sid, chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX),
                             suwa.chapter_list, str(folder)).records[0]


def test_chapters_only_files_and_runs_the_after_step(tmp_path, caplog):
    ledger, suwa, sid, folder, downloads = _setup(tmp_path)
    SuwayomiSettings(ledger.store).save_connection(SuwayomiConnection("http://suwa:4567", "owner", SECRET,
                                                                      str(downloads)))
    rec = _send(ledger, suwa, sid, folder)
    suwa.finish(2)
    seen, afters = [], []
    job = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: None,
                             chapter_client_factory=lambda conn: seen.append(conn) or suwa, namer=FakeNamer(),
                             after=lambda ctx, led, report: afters.append(tuple(report.filed)) or "after ran",
                             replaced=None)
    with caplog.at_level(logging.DEBUG):
        result = job(JobContext())
    assert result.status == "ok", result.message
    assert result.message.startswith("chapters: 1 checked: 1 filed, 0 failed, 0 waiting") and "after ran" in result.message
    assert result.extra["chapters_filed"] == 1 and afters == [(rec.id,)]
    assert ledger.for_tool(None).get(rec.id).status == "removed"
    assert seen[0].password == SECRET and seen[0].download_dir == str(downloads)
    assert SECRET not in caplog.text and SECRET not in repr(result)


def test_after_pass_finds_chapter_records(tmp_path):
    ledger, suwa, sid, folder, downloads = _setup(tmp_path)
    SuwayomiSettings(ledger.store).save_connection(SuwayomiConnection("http://suwa:4567", download_dir=str(downloads)))
    _send(ledger, suwa, sid, folder)
    suwa.finish(2)
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: None,
                                chapter_client_factory=lambda conn: suwa, namer=FakeNamer(), replaced=None)(JobContext())
    assert "rescan ok" in result.message
    from decimal import Decimal

    assert any(u.kind == "chapter" and Decimal(u.ch_from) == 2 for u in ledger.store.list_units(sid))


def test_chapters_without_a_suwayomi_connection_are_skipped_with_the_reason(tmp_path):
    ledger, suwa, sid, folder, downloads = _setup(tmp_path)
    _send(ledger, suwa, sid, folder)
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: None,
                                chapter_client_factory=lambda conn: suwa)(JobContext())
    assert result.status == "skipped" and result.message == "chapters: no Suwayomi connection set up"


def test_suwayomi_down_is_an_error_result(tmp_path):
    ledger, suwa, sid, folder, downloads = _setup(tmp_path)
    SuwayomiSettings(ledger.store).save_connection(SuwayomiConnection("http://suwa:4567", download_dir=str(downloads)))
    _send(ledger, suwa, sid, folder)
    suwa.unreachable = True
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: None,
                                chapter_client_factory=lambda conn: suwa, replaced=None, after=None)(JobContext())
    assert result.status == "error" and "Suwayomi could not be asked" in result.message


def test_both_tools_in_one_run(tmp_path):
    ledger, suwa, sid, folder, downloads = _setup(tmp_path)
    SuwayomiSettings(ledger.store).save_connection(SuwayomiConnection("http://suwa:4567", download_dir=str(downloads)))
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", "pw"))
    _send(ledger, suwa, sid, folder)
    qbt = FakeQbt(tmp_path / "torrents")
    qbt.save_root.mkdir()
    ledger.create(sid, candidate(), ["2"], str(folder))
    qbt.put(HASH, "Pack", {"Series C v02.cbz": data("v02")}, state="downloading", progress=0.5)
    afters = []
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt,
                                chapter_client_factory=lambda conn: suwa, namer=FakeNamer(), replaced=None,
                                after=lambda ctx, led, report: afters.append(tuple(report.filed)))(JobContext())
    assert result.status == "ok"
    assert result.message.startswith("1 checked: 0 filed, 0 removed, 0 failed, 1 waiting")
    assert "; chapters: 1 checked: 0 filed, 0 failed, 1 waiting" in result.message
    assert result.extra["checked"] == 1 and result.extra["chapters_checked"] == 1 and afters == [()]


def test_no_chapter_records_change_nothing(tmp_path):
    ledger, suwa, sid, folder, downloads = _setup(tmp_path)
    SuwayomiSettings(ledger.store).save_connection(SuwayomiConnection("http://suwa:4567", download_dir=str(downloads)))
    result = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: None,
                                chapter_client_factory=lambda conn: suwa)(JobContext())
    assert result.message.startswith("no downloads in progress") and suwa.calls == []
