"""The chapter arrivals pass with a FAKE Suwayomi and a FAKE namer: every transition, every refusal, idempotent, a file
is never lost (Suwayomi deletes its copy only after the library file is verified)."""

from __future__ import annotations

import errno
import os
from decimal import Decimal as D
from pathlib import Path

import pytest

from mangalist.downloads import chapters as chm
from mangalist.downloads.chapter_arrivals import (
    LazyNaming, expected_path, find_cbz, read_comicinfo, run_chapter_arrivals, suwayomi_name,
)
from mangalist.downloads.contracts import TOOL_SUWAYOMI, QueuedChapter, DownloadStatus as S
from mangalist.store.downloads import DownloadLedger

from .fakes import data, scan
from .suwayomi_fakes import MANGA, MANGADEX, FakeNamer, FakeSuwayomi, chapter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "suwayomi"


@pytest.fixture
def cseries(db, tmp_path):
    root = tmp_path / "library" / "Manga"
    folder = root / "Series C"
    folder.mkdir(parents=True)
    for n in (1, 2, 3):
        (folder / f"{n:04d} [Ch. {n:04d} - Title {n} [Alpha Scans]].cbz").write_bytes(data(f"c{n}"))
    r = db.add_root(str(root), "Manga")
    scan(db)
    return db.get_series(r.id, "Series C").id, folder


@pytest.fixture
def cledger(db):
    return DownloadLedger(db, tool=TOOL_SUWAYOMI)


@pytest.fixture
def downloads(tmp_path) -> Path:
    d = tmp_path / "suwayomi" / "downloads"
    (d / "mangas").mkdir(parents=True)
    return d


@pytest.fixture
def suwa(downloads):
    return FakeSuwayomi(downloads, chapters=[chapter(4, "4"), chapter(5, "5", "Beta Group"),
                                             chapter(6, "10.5", None, name="Vol.2 Ch.10.5")])


@pytest.fixture
def sent(cledger, cseries, suwa):
    sid, folder = cseries
    out = chm.send_chapters(suwa, cledger, sid, chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX), suwa.chapter_list,
                            str(folder))
    return out.records


def run(suwa, cledger, downloads, namer=None, **kw):
    return run_chapter_arrivals(suwa, cledger, download_dir=str(downloads), namer=namer or FakeNamer(), **kw)


def status(cledger, rec):
    return cledger.get(rec.id).status


# --- Suwayomi's own rules ----------------------------------------------------------------------------------------

def test_suwayomi_name_rule():
    assert suwayomi_name("Alpha Scans_Vol.1 Ch.1 - Title") == "Alpha Scans_Vol.1 Ch.1 - Title"
    assert suwayomi_name(' .Who: are/you? "x" <y> * | \\ .') == 'Who_ are_you_ _x_ _y_ _ _ _'
    assert suwayomi_name("...") == "(invalid)"
    assert len(suwayomi_name("あ" * 200).encode()) <= 240


def test_recorded_layout_and_comicinfo(tmp_path):
    # the recorded download: <downloads>/mangas/MangaDex (EN)/<title>/<scanlator>_<chapter name>.cbz
    path = expected_path("/dl", "MangaDex (EN)", "Example Manga", "Vol.1 Ch.1 - Example Title 1", "Alpha Scans")
    # the download folder is a local path of the machine MangaList runs on: its own separators (CI runs Windows too)
    assert path == os.path.join("/dl", "mangas", "MangaDex (EN)", "Example Manga",
                                "Alpha Scans_Vol.1 Ch.1 - Example Title 1.cbz")
    assert expected_path("/dl", "S", "T", "Ch.2", None) == os.path.join("/dl", "mangas", "S", "T", "Ch.2.cbz")
    import zipfile

    cbz = tmp_path / "c.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("001.png", b"x")
        zf.writestr("ComicInfo.xml", (FIXTURES / "ComicInfo.xml").read_bytes())
    info = read_comicinfo(str(cbz))
    assert (info.number, info.title, info.series, info.translator) == \
        ("1", "Vol.1 Ch.1 - Example Title 1", "Example Manga", "Alpha Scans")
    assert info.web.endswith("0000000c0001")
    assert read_comicinfo(str(tmp_path / "missing.cbz")) is None


# --- the life of a chapter download ---------------------------------------------------------------------------------

def test_the_whole_life_of_a_chapter_download(cledger, sent, suwa, downloads, cseries):
    _, folder = cseries
    rec = sent[0]
    report = run(suwa, cledger, downloads)
    assert status(cledger, rec) == S.SENT and report.waiting and not report.filed
    src = suwa.finish(4, data=data("chapter four"))
    namer = FakeNamer({D(4): D(1)})                     # MangaPixer's volume list puts chapter 4 in volume 1
    report = run(suwa, cledger, downloads, namer)
    assert rec.id in report.downloaded and rec.id in report.filed and rec.id in report.removed
    done = cledger.get(rec.id)
    assert done.status == S.REMOVED
    name = "Ch. 0004.00 Vol. 001 (Example Title 4) [Alpha Scans].cbz"
    assert done.filed_files == (name,)
    with open(folder / name, "rb") as f:
        import zipfile

        assert zipfile.ZipFile(f).read("001.png") == data("chapter four")
    assert not src.exists()                             # Suwayomi deleted its copy, after the library file was there
    assert ("delete_downloaded", [4]) in suwa.calls
    assert namer.calls[0]["folder"] == str(folder) and namer.calls[0]["group"] == "Alpha Scans"
    again = run(suwa, cledger, downloads, namer)        # idempotent: nothing left to do for it
    assert rec.id not in again.filed and cledger.get(rec.id).status == S.REMOVED


def test_the_volume_comes_from_the_name_when_the_volume_list_has_none(cledger, sent, suwa, downloads, cseries):
    _, folder = cseries
    suwa.finish(6)
    run(suwa, cledger, downloads, FakeNamer())
    rec = cledger.get(sent[2].id)
    assert rec.filed_files == ("Ch. 0010.50 Vol. 002.cbz",)      # no title, no group: the source names none


def test_waits_while_queued_and_notes_an_error(cledger, sent, suwa, downloads):
    suwa.queued[4] = QueuedChapter(4, "DOWNLOADING", 0.5)
    suwa.queued[5] = QueuedChapter(5, "ERROR", 0.0, 3)
    report = run(suwa, cledger, downloads)
    waiting = dict(report.waiting)
    assert "downloading in Suwayomi (50%)" in waiting[sent[0].id]
    assert "3 tries" in waiting[sent[1].id] and "3 tries" in cledger.get(sent[1].id).error
    assert cledger.get(sent[1].id).status == S.SENT


def test_gone_from_the_queue_without_a_download_fails(cledger, sent, suwa, downloads):
    suwa.queued.pop(4)
    suwa.chapter_list = [c for c in suwa.chapter_list if c.id != 5]      # Suwayomi no longer knows chapter 5
    report = run(suwa, cledger, downloads)
    assert status(cledger, sent[0]) == S.FAILED and "no longer in Suwayomi's download queue" in \
        cledger.get(sent[0].id).error
    assert status(cledger, sent[1]) == S.FAILED and "no longer knows" in cledger.get(sent[1].id).error
    assert len(report.failed) == 2


def test_suwayomi_down_changes_nothing(cledger, sent, suwa, downloads):
    suwa.unreachable = True
    report = run(suwa, cledger, downloads)
    assert report.error and "Suwayomi could not be asked" in report.error
    assert all(status(cledger, r) == S.SENT for r in sent)


def test_found_by_its_url_when_the_title_changed(cledger, sent, suwa, downloads, cseries):
    suwa.finish(4, title="Example Manga (Renamed)")         # Suwayomi's manga title changed after the send
    run(suwa, cledger, downloads)
    assert cledger.get(sent[0].id).status in (S.FILED, S.REMOVED)


def test_a_folder_of_images_is_refused_with_the_fix(cledger, sent, suwa, downloads):
    suwa.finish(4, as_folder=True)
    run(suwa, cledger, downloads)
    rec = cledger.get(sent[0].id)
    assert rec.status == S.FAILED and "Download as CBZ" in rec.error


def test_another_chapter_in_the_cbz_is_never_filed(cledger, sent, suwa, downloads, cseries):
    _, folder = cseries
    before = sorted(os.listdir(folder))
    src = suwa.finish(4, number="44")
    run(suwa, cledger, downloads)
    rec = cledger.get(sent[0].id)
    assert rec.status == S.FAILED and "is chapter 44, not 4" in rec.error
    assert sorted(os.listdir(folder)) == before and src.exists()


def test_a_name_taken_or_a_chapter_held_is_refused(cledger, sent, suwa, downloads, cseries):
    _, folder = cseries
    (folder / "Ch. 0004.00 Vol. 001 (Example Title 4) [Alpha Scans].cbz").write_bytes(b"mine")
    suwa.finish(4)
    run(suwa, cledger, downloads)
    rec = cledger.get(sent[0].id)
    assert rec.status == S.FAILED and "already in the target folder" in rec.error
    assert (folder / "Ch. 0004.00 Vol. 001 (Example Title 4) [Alpha Scans].cbz").read_bytes() == b"mine"
    (folder / "0005 [Ch. 0005 - Other [Gamma]].cbz").write_bytes(b"other copy")     # arrived meanwhile
    suwa.finish(5)
    run(suwa, cledger, downloads)
    rec = cledger.get(sent[1].id)
    assert rec.status == S.FAILED and "chapter 5 is already in the folder" in rec.error


def test_no_download_folder_waits(cledger, sent, suwa, tmp_path):
    suwa.download_dir = tmp_path / "elsewhere"
    suwa.finish(4)
    report = run_chapter_arrivals(suwa, cledger, download_dir="", namer=FakeNamer())
    assert cledger.get(sent[0].id).status == S.DOWNLOADED
    assert any("download folder is not set" in why for _i, why in report.waiting)


def test_without_the_naming_module_it_waits(cledger, sent, suwa, downloads, monkeypatch):
    class Missing(LazyNaming):
        module = "mangalist.no_such_naming_module"

    suwa.finish(4)
    report = run(suwa, cledger, downloads, Missing())
    assert cledger.get(sent[0].id).status == S.DOWNLOADED
    assert any("naming scheme is not available" in why for _i, why in report.waiting)


def test_a_name_the_scheme_refuses_fails_with_the_reason_and_moves_nothing(cledger, sent, suwa, downloads, cseries):
    class Refusing(FakeNamer):
        def chapter_file_name(self, chapter, **kwargs):
            raise ValueError("a chapter number cannot be negative")

    src = suwa.finish(4)
    report = run(suwa, cledger, downloads, Refusing())
    rec = cledger.get(sent[0].id)
    assert rec.status == S.FAILED and "naming scheme gave no name" in rec.error and src.exists()
    assert not report.errors and report.failed


def test_a_copy_when_no_hard_link_is_possible(cledger, sent, suwa, downloads, cseries, monkeypatch):
    real_link = os.link

    def no_link(src, dst, *a, **k):
        if "suwayomi" in str(src):
            raise OSError(errno.EXDEV, "cross-device link")
        return real_link(src, dst, *a, **k)

    monkeypatch.setattr(os, "link", no_link)
    suwa.finish(4)
    report = run(suwa, cledger, downloads)
    rec = cledger.get(sent[0].id)
    assert rec.copied and rec.id in report.copied and rec.status == S.REMOVED


def test_suwayomi_keeps_its_copy_until_the_library_file_is_verified(cledger, sent, suwa, downloads, cseries):
    _, folder = cseries
    suwa.delete_error = RuntimeError("busy")
    src = suwa.finish(4)
    run(suwa, cledger, downloads)
    rec = cledger.get(sent[0].id)
    assert rec.status == S.FILED and "did not delete its copy yet" in rec.error and src.exists()
    os.remove(folder / rec.filed_files[0])              # the library file went missing meanwhile
    suwa.delete_error = None
    run(suwa, cledger, downloads)
    rec = cledger.get(sent[0].id)
    assert rec.status == S.FILED and "no longer in the library" in rec.error and src.exists()


def test_never_deletes_when_the_download_folder_overlaps_a_root(db, cledger, sent, suwa, downloads, cseries):
    db.add_root(str(downloads.parent), "Oops")
    suwa.finish(4)
    run(suwa, cledger, downloads)
    rec = cledger.get(sent[0].id)
    assert rec.status == S.FILED and "overlaps the library root" in rec.error
    assert not any(c[0] == "delete_downloaded" for c in suwa.calls)


def test_a_plan_left_by_a_crash_is_settled(cledger, sent, suwa, downloads, cseries):
    from mangalist.store import Journal

    _, folder = cseries
    src = suwa.finish(4)
    rec = cledger.set_status(sent[0].id, S.DOWNLOADED, expect=(S.SENT,))
    journal = Journal(cledger.store)
    dst = folder / "Ch. 0004.00 Vol. 001 (Example Title 4) [Alpha Scans].cbz"
    plan = journal.plan_links("chapter arrival", [(str(src), str(dst))], root_path=str(folder.parent))
    cledger.attach_plan(rec.id, plan.id)            # the crash: the plan written, never applied
    run(suwa, cledger, downloads)
    done = cledger.get(rec.id)
    assert done.status == S.REMOVED and dst.exists() and done.filed_files == (dst.name,)


def test_find_cbz_explains_a_wrong_folder(tmp_path):
    path, why = find_cbz(str(tmp_path), {"chapter_name": "Ch.1", "source_name": "S", "manga_title": "T"})
    assert path is None and "no \"mangas\" folder" in why
