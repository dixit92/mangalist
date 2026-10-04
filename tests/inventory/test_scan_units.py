"""The layered parser in the scan: units stored per series, re-scans, the stored kind answer (C12)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from mangalist.models import FileHit, MangaEntry, Verdict
from mangalist.scanner import answer_series_kind, apply_kind_hint, record_library_scan, scan_library, scan_root
from mangalist.store import schema
from mangalist.store.series import SeriesKindError
from mangalist.store.units import Unit

from .conftest import make_archive

D = Decimal
MB = 1024 * 1024


def _scan(db):
    result = scan_library(db.list_roots())
    record_library_scan(db, result)
    return result


def _entry(result, name):
    return next(e for e in result.entries if e.folder.name == name)


def _units(db, root, rel):
    s = db.get_series(root.id, rel)
    return [(u.rel_path, u.kind, u.vol_from, u.vol_to, u.ch_from, u.ch_to, u.num_from, u.parser)
            for u in db.list_units(s.id)]


# --- schema ---------------------------------------------------------------------------------------

def test_schema_2_adds_the_kind_answer_and_bare_numbers(db):
    assert db.schema_version() == schema.SCHEMA_VERSION >= 2
    with db.connect() as con:
        series_cols = {r["name"] for r in con.execute("PRAGMA table_info(series)")}
        unit_cols = {r["name"] for r in con.execute("PRAGMA table_info(units)")}
    assert "kind_hint" in series_cols and {"num_from", "num_to"} <= unit_cols


# --- scan -> units --------------------------------------------------------------------------------

def test_scan_stores_every_archives_units(db, library):
    make_archive(library / "Series A" / "Series A v01-02 (2019) (Digital) (Grp).cbz")
    make_archive(library / "Series A" / "Chapters" / "0012 [Vol. 3 Ch. 12.5 - Episode 3 [Team]].cbz")
    make_archive(library / "Series B" / "0123 [Omake].cbz")
    make_archive(library / "Series B" / "01.cbz")
    root = db.add_root(str(library))
    _scan(db)
    assert _units(db, root, "Series A") == [
        ("Chapters/0012 [Vol. 3 Ch. 12.5 - Episode 3 [Team]].cbz", "chapter", "3", "3", "12.5", "12.5", None, "fmd2"),
        ("Series A v01-02 (2019) (Digital) (Grp).cbz", "volume", "1", "2", None, None, None, "release"),
    ]
    assert _units(db, root, "Series B") == [
        ("01.cbz", "unknown", None, None, None, None, "1", "bare"),
        ("0123 [Omake].cbz", "extra", None, None, None, None, None, "fmd2"),
    ]
    stored = db.list_units(db.get_series(root.id, "Series A").id)
    assert stored[0].group_name == "Team" and stored[0].title == "Episode 3" and stored[0].idx == "12"


def test_rescan_replaces_and_removes(db, library):
    make_archive(library / "Series A" / "0001 [Ch. 1].cbz")
    make_archive(library / "Series A" / "0002 [Ch. 2].cbz")
    make_archive(library / "Series A" / "0003 [Ch. 3].cbz")
    root = db.add_root(str(library))
    _scan(db)
    sid = db.get_series(root.id, "Series A").id
    before = {u.rel_path: u.id for u in db.list_units(sid)}

    (library / "Series A" / "0002 [Ch. 2].cbz").unlink()                       # gone
    (library / "Series A" / "0003 [Ch. 3].cbz").rename(library / "Series A" / "0003 [Ch. 3.5].cbz")
    make_archive(library / "Series A" / "0004 [Ch. 4].cbz")                    # new
    _scan(db)
    after = db.list_units(sid)
    assert [(u.rel_path, u.ch_from) for u in after] == [
        ("0001 [Ch. 1].cbz", "1"), ("0003 [Ch. 3.5].cbz", "3.5"), ("0004 [Ch. 4].cbz", "4")]
    assert after[0].id == before["0001 [Ch. 1].cbz"]                            # unchanged: left alone


def test_sync_units_counts_and_validates(db, library):
    make_archive(library / "Series A" / "0001 [Ch. 1].cbz")
    root = db.add_root(str(library))
    _scan(db)
    sid = db.get_series(root.id, "Series A").id
    good = {"0001 [Ch. 1].cbz": [Unit(rel_path="0001 [Ch. 1].cbz", kind="chapter", ch_from="1", ch_to="1",
                                      idx="1", parser="fmd2", file_size=10)]}
    assert db.sync_units({sid: good}).unchanged == 1
    from mangalist.store import UnitError

    with pytest.raises(UnitError):     # a float is refused before anything is written
        db.sync_units({sid: {"b.cbz": [Unit(rel_path="b.cbz", kind="chapter", ch_from=2.5)]}})
    assert len(db.list_units(sid)) == 1


def test_a_vanished_series_loses_its_units(db, library):
    make_archive(library / "Series A" / "0001 [Ch. 1].cbz", 11)
    make_archive(library / "Series B" / "0001 [Ch. 1].cbz", 12)
    root = db.add_root(str(library))
    _scan(db)
    sid = db.get_series(root.id, "Series B").id
    (library / "Series B" / "0001 [Ch. 1].cbz").unlink()
    (library / "Series B").rmdir()
    _scan(db)
    assert db.get_series(root.id, "Series B").status == "missing"
    assert db.list_units(sid) == []
    assert len(db.list_units(db.get_series(root.id, "Series A").id)) == 1


def test_an_offline_root_keeps_its_units(db, library):
    make_archive(library / "Series A" / "0001 [Ch. 1].cbz")
    root = db.add_root(str(library))
    _scan(db)
    (library / "Series A" / "0001 [Ch. 1].cbz").unlink()
    (library / "Series A").rmdir()
    library.rmdir()
    _scan(db)
    assert len(db.list_units(db.get_series(root.id, "Series A").id)) == 1


def test_a_franchise_parent_does_not_hold_its_subseries_units(db, library):
    make_archive(library / "Franchise" / "Franchise v01 (2019) (Digital) (G).cbz")
    make_archive(library / "Franchise" / "Part Two" / "Part Two v01 (2020) (Digital) (G).cbz")
    make_archive(library / "Franchise" / "Part Two" / "Part Two v02 (2020) (Digital) (G).cbz")
    root = db.add_root(str(library))
    result = _scan(db)
    parent = _entry(result, "Franchise")
    assert parent.n_files == 3                                      # counts as before
    assert parent.inventory().volumes == (D("1"),)                  # its own archive only
    assert [u[0] for u in _units(db, root, "Franchise")] == ["Franchise v01 (2019) (Digital) (G).cbz"]
    assert len(_units(db, root, "Franchise/Part Two")) == 2


# --- the kind answer (C12) ------------------------------------------------------------------------

def test_bare_numbers_need_the_kind_once(db, library):
    for n in ("01", "02", "03"):
        make_archive(library / "Series N" / f"{n}.cbz", 40 * MB // 1000)
    make_archive(library / "Series F" / "0001 [Ch. 1].cbz")
    root = db.add_root(str(library))
    result = _scan(db)
    e = _entry(result, "Series N")
    assert e.needs_kind and e.kind_hint is None
    assert [f.path.name for f in e.unknown_kind_files] == ["01.cbz", "02.cbz", "03.cbz"]
    assert e.verdict == Verdict.UNKNOWN
    assert any("volumes or chapters?" in r for r in e.reasons)
    assert not _entry(result, "Series F").needs_kind

    assert answer_series_kind(db, e, "volumes")
    assert db.get_series(root.id, "Series N").kind_hint == "volumes"
    assert not e.needs_kind and e.kind_hint == "volumes"            # re-parsed in place
    assert e.verdict == Verdict.VOLUMES and e.n_volume_files == 3
    assert e.inventory().volumes == (D("1"), D("2"), D("3"))
    assert [u[1:4] for u in _units(db, root, "Series N")] == [
        ("volume", "1", "1"), ("volume", "2", "2"), ("volume", "3", "3")]


def test_the_stored_answer_changes_later_scans(db, library):
    for n in ("01", "02"):
        make_archive(library / "Series N" / f"Series N {n}.cbz")
    root = db.add_root(str(library))
    _scan(db)
    assert db.set_series_kind(root.id, "Series N", "chapters")

    result = _scan(db)                    # main window path: scan without db, record applies the answer
    e = _entry(result, "Series N")
    assert e.kind_hint == "chapters" and not e.needs_kind
    assert e.verdict == Verdict.CHAPTERS
    assert e.max_disk_chapter == 2.0 and e.highest_chapter == D("2")
    assert [u[1] for u in _units(db, root, "Series N")] == ["chapter", "chapter"]

    direct = scan_library(db.list_roots(), db=db)                     # hints read while parsing
    assert _entry(direct, "Series N").kind_hint == "chapters"
    assert _entry(direct, "Series N").inventory().chapters == (D("1"), D("2"))

    db.set_series_kind(root.id, "Series N", None)                     # forgotten: asked again
    assert _entry(_scan(db), "Series N").needs_kind


def test_the_answer_follows_a_renamed_folder(db, library):
    make_archive(library / "Old Name" / "01.cbz", 101)
    make_archive(library / "Old Name" / "02.cbz", 102)
    root = db.add_root(str(library))
    _scan(db)
    db.set_series_kind(root.id, "Old Name", "volumes")
    from mangalist.identity.backfill import backfill_signatures

    backfill_signatures(db, per_file_delay=0)            # the archives are signed before the rename
    (library / "Old Name").rename(library / "New Name")
    result = _scan(db)
    e = _entry(result, "New Name")
    assert db.get_series(root.id, "New Name").kind_hint == "volumes"
    assert e.kind_hint == "volumes" and e.inventory().volumes == (D("1"), D("2"))
    assert [u[1] for u in _units(db, root, "New Name")] == ["volume", "volume"]


def test_the_hint_never_overrides_a_stated_kind(db, library):
    make_archive(library / "Series M" / "01.cbz")
    make_archive(library / "Series M" / "0005 [Ch. 5].cbz")
    root = db.add_root(str(library))
    _scan(db)
    db.set_series_kind(root.id, "Series M", "volumes")
    e = _entry(_scan(db), "Series M")
    assert e.inventory().volumes == (D("1"),) and e.inventory().chapters == (D("5"),)


def test_kind_values_are_validated(db, library):
    make_archive(library / "Series A" / "01.cbz")
    root = db.add_root(str(library))
    _scan(db)
    with pytest.raises(SeriesKindError):
        db.set_series_kind(root.id, "Series A", "omnibus")
    assert db.set_series_kind(root.id, "Series A", "Volume")           # singular / any case accepted
    assert db.series_kind(root.id, "Series A") == "volumes"
    assert db.series_kind_hints(root.id) == {"Series A": "volumes"}
    assert not db.set_series_kind(root.id, "No Such Folder", "chapters")
    assert db.set_series_kind_for_folder(library / "Series A", "chapters")
    assert db.series_kind(root.id, "Series A") == "chapters"


def test_answer_for_a_folder_outside_the_roots_still_reparses(db, tmp_path):
    folder = tmp_path / "elsewhere" / "Loose Series"
    make_archive(folder / "01.cbz")
    e = scan_root(folder.parent)[0]
    assert e.needs_kind
    assert answer_series_kind(db, e, "chapters") is False
    assert not e.needs_kind and e.inventory().chapters == (D("1"),)


def test_scan_root_takes_kind_hints(library):
    make_archive(library / "Series N" / "05.cbz")
    make_archive(library / "Franchise" / "Part One" / "07.cbz")
    entries = scan_root(library, kind_hints={"Series N": "volumes", "Franchise/Part One": "chapters"})
    by = {e.folder.name: e for e in entries}
    assert by["Series N"].inventory().volumes == (D("5"),)
    assert by["Part One"].inventory().chapters == (D("7"),)
    assert apply_kind_hint(by["Series N"], None).needs_kind


# --- visible behaviour of the entry ---------------------------------------------------------------

def test_fmd2_titles_no_longer_raise_the_highest_chapter(library):
    make_archive(library / "Series A" / "0001 [Ch. 1 - Episode 30].cbz")
    make_archive(library / "Series A" / "0002 [Ch. 2 - Extra Chapter 20].cbz")
    make_archive(library / "Series A" / "0003 [Ch. 0 - 4th Year Anniversary].cbz")
    e = scan_root(library)[0]
    assert e.max_disk_chapter == 2.0                                   # today's regex read 30
    assert e.highest_chapter == D("2")


def test_three_decimal_chapters_are_exact(library):
    make_archive(library / "Series A" / "0291 [Ch. 291.999 - Title].cbz")
    e = scan_root(library)[0]
    assert e.highest_chapter == D("291.999") and str(e.highest_chapter) == "291.999"
    assert e.files[0].parsed.chapter.start == D("291.999")


def test_unit_less_fmd2_extras_count_as_chapters(library):
    for i in range(1, 4):
        make_archive(library / "Series A" / f"000{i} [Ch. {i}].cbz")
    make_archive(library / "Series A" / "0123 [Omake].cbz")
    e = scan_root(library)[0]
    assert e.n_chapter_files == 4 and e.n_ambiguous == 0               # before: 3 chapters + 1 ambiguous
    assert e.inventory().extras == ("0123 [Omake].cbz",)


def test_volume_with_loose_chapters_counts_as_a_volume_file(library):
    make_archive(library / "Series A" / "Series A v10 + 085-086 (2021) (Digital) (G).cbz")
    e = scan_root(library)[0]
    assert e.files[0].kind == "volume" and e.files[0].unit_kind == "both"
    assert e.max_disk_chapter == 86.0 and e.max_disk_volume == 10.0


def test_hits_without_a_parse_keep_todays_reading():
    hit = FileHit(path=Path("/x/Vol. 1 Ch 3.cbz"), size=0, depth=0, has_volume=True, has_chapter=True)
    e = MangaEntry(folder=Path("/x"), title="X", english_title=None, files=[hit])
    assert hit.kind == "chapter" and hit.unit_kind == "chapter" and not hit.needs_kind
    assert e.max_disk_chapter == 3.0 and e.max_disk_volume == 1.0
    assert e.inventory().chapters == (D("3"),)                          # parsed on demand


def test_file_order_does_not_depend_on_the_filesystem(tmp_path, monkeypatch):
    # os.walk lists in the filesystem's order (ext4 hashes names); the scan must not pass it on.
    import os

    import mangalist.scanner as scanner_mod

    for n in ("02", "10", "01", "Extra"):
        make_archive(tmp_path / "lib" / "Series R" / f"{n}.cbz")
    real_walk = os.walk

    def reversed_walk(top, *a, **k):
        for d, dirnames, filenames in real_walk(top, *a, **k):
            dirnames.reverse()
            filenames.sort(reverse=True)
            yield d, dirnames, filenames

    monkeypatch.setattr(scanner_mod.os, "walk", reversed_walk)
    (entry,) = scan_root(tmp_path / "lib")
    assert [f.path.name for f in entry.files] == ["01.cbz", "02.cbz", "10.cbz", "Extra.cbz"]
