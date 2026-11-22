"""Replacing held chapters with another source's or group's copy, by hand (owner, 2026-11-22): which held files a
source's chapter replaces, and the filing that moves them to the holding folder before the new copy takes their place
(:mod:`mangalist.downloads.replace_held`)."""

from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from mangalist import upgrades
from mangalist.downloads import chapters as chm
from mangalist.downloads import replace_held as rh
from mangalist.downloads.chapter_arrivals import run_chapter_arrivals
from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.store.downloads import TOOL_SUWAYOMI, DownloadLedger
from mangalist.store.replacements import ReplacementStore

from .fakes import data, scan
from .suwayomi_fakes import MANGA, MANGADEX, FakeNamer, FakeSuwayomi, chapter


def held(*numbers):
    return [rh.HeldFile(f"/lib/S/Ch. {n}.cbz", f"Ch. {n}.cbz", D(n)) for n in numbers]


def test_the_rows_pair_the_sources_chapters_with_the_files_held():
    source = [chapter(1, "1", "New Group"), chapter(2, "2", "New Group"), chapter(3, "33", "New Group"),
              chapter(4, "34", "New Group"), chapter(5, "40.1", "New Group"), chapter(6, "41", "New Group"),
              chapter(7, "2", "Other Group"), chapter(8, "160", "New Group")]
    rows = rh.rows_for(source, held("1", "2", "33.1", "33.2", "34.1", "40", "100"),
                       ranges=[(D(151), D(165), "Ch. 0151.00-0165.00.cbz")])
    by = {r.number: r for r in rows}
    assert list(by) == ["1", "2", "33", "34", "40.1", "160"]           # 41: not held, the panel's own (missing)
    assert by["1"].offered and [f.rel for f in by["1"].held] == ["Ch. 1.cbz"] and "file held" in by["1"].note
    assert [ch.scanlator for ch in by["2"].options] == ["New Group", "Other Group"]       # the source's main group
    assert [f.rel for f in by["33"].held] == ["Ch. 33.1.cbz", "Ch. 33.2.cbz"] and "2 parts held (33.1, 33.2)" \
        in by["33"].note                                                 # a whole for the parts held
    assert [f.rel for f in by["34"].held] == ["Ch. 34.1.cbz"]           # parts held so far: the whole replaces them
    assert not by["40.1"].offered and "in parts" in by["40.1"].note      # never a part for a whole held
    assert not by["160"].offered and "151-165" in by["160"].note         # a file of several chapters: kept
    assert by["33"].folder == "/lib/S"


@pytest.fixture
def parts_series(db, tmp_path):
    root = tmp_path / "library" / "Manga"
    folder = root / "Series P"
    folder.mkdir(parents=True)
    for name in ("Ch. 0003.00.cbz", "Ch. 0004.10.cbz", "Ch. 0004.20.cbz"):
        (folder / name).write_bytes(data(name))
    r = db.add_root(str(root), "Manga")
    scan(db)
    upgrades.set_holding_folder(db, str(tmp_path / "appdata" / "replaced"))
    return db.get_series(r.id, "Series P").id, folder


def test_a_whole_chapter_from_another_source_replaces_the_parts_held(db, parts_series, tmp_path):
    sid, folder = parts_series
    downloads = tmp_path / "suwayomi" / "downloads"
    (downloads / "mangas").mkdir(parents=True)
    suwa = FakeSuwayomi(downloads, chapters=[chapter(4, "4", "New Group"), chapter(5, "5", "New Group")])
    ledger = DownloadLedger(db, tool=TOOL_SUWAYOMI)
    rows = rh.rows_for(suwa.chapter_list, rh.held_files(db, sid))
    (row,) = rows                                                        # 5 is not held: not a replacement
    assert row.number == "4" and [f.rel for f in row.held] == ["Ch. 0004.10.cbz", "Ch. 0004.20.cbz"]
    match = chm.MangaMatch(MANGADEX, MANGA, chm.HOW_CONFIRMED)
    out = rh.send_replacements(suwa, ledger, sid, match, [(row.default, [f.path for f in row.held])],
                               manga_title=MANGA.title, since=0)
    (rec,) = out.records                                                 # sent although chapter 4 is held
    assert rec.status == S.SENT and rec.target_dir == str(folder)
    assert ledger.request(rec.id)[rh.KEY_REPLACES_FILES] == [str(folder / "Ch. 0004.10.cbz"),
                                                             str(folder / "Ch. 0004.20.cbz")]
    os.rename(folder / "Ch. 0004.20.cbz", folder / "Ch. 0004.20 (renamed).cbz")   # renamed by hand (not journaled)
    suwa.finish(4, data=data("the whole chapter four"))
    report = run_chapter_arrivals(suwa, ledger, download_dir=str(downloads), namer=FakeNamer())
    # the renamed part is not where the record says (not journaled): only the other is held, the filing still goes on
    assert rec.id in report.filed
    (batch,) = ReplacementStore(db).with_status("held")
    assert [f.rel for f in batch.files] == ["Ch. 0004.10.cbz"] and batch.series_id == sid
    assert not (folder / "Ch. 0004.10.cbz").exists()
    filed = ledger.get(rec.id).filed_files
    assert len(filed) == 1 and (folder / filed[0]).exists()


def test_both_parts_go_to_the_holding_folder_and_come_back_when_the_new_copy_cannot_be_filed(db, parts_series,
                                                                                           tmp_path):
    sid, folder = parts_series
    downloads = tmp_path / "suwayomi" / "downloads"
    (downloads / "mangas").mkdir(parents=True)
    suwa = FakeSuwayomi(downloads, chapters=[chapter(4, "4", "New Group")])
    ledger = DownloadLedger(db, tool=TOOL_SUWAYOMI)
    (row,) = rh.rows_for(suwa.chapter_list, rh.held_files(db, sid))
    match = chm.MangaMatch(MANGADEX, MANGA, chm.HOW_CONFIRMED)
    (rec,) = rh.send_replacements(suwa, ledger, sid, match, [(row.default, [f.path for f in row.held])]).records
    suwa.finish(4, data=data("the whole chapter four"))
    report = run_chapter_arrivals(suwa, ledger, download_dir=str(downloads), namer=FakeNamer())
    assert rec.id in report.filed
    (batch,) = ReplacementStore(db).with_status("held")
    assert sorted(f.rel for f in batch.files) == ["Ch. 0004.10.cbz", "Ch. 0004.20.cbz"]
    assert sorted(p.name for p in folder.iterdir()) == sorted(["Ch. 0003.00.cbz", *ledger.get(rec.id).filed_files])
    restored = upgrades.restore_batch(db, batch.id)                       # Restore from the Download tab
    assert restored.status == "restored" and (folder / "Ch. 0004.10.cbz").exists() and \
        (folder / "Ch. 0004.20.cbz").exists()
