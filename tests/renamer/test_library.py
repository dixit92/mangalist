"""The renamer over a made-up library in a temporary folder: dry run, apply in journal batches, undo, rename pending and
the automatic pass. Every rename happens inside the test's own temporary folder."""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from pathlib import Path

import pytest

from mangalist import renamer as rn
from mangalist.renamer import ANALYSIS_WARNING, COLLISION, LEFT_ALONE, PLAN_REASON, RENAME, UNCHANGED, Renamer
from mangalist.store.journal import Journal
from mangalist.store.lock import RootLock

from .conftest import fmd2, make_library, payload, rescan
from .fakes import FakeNamer, Recorder


def _names(folder: Path):
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def _contents(folder: Path):
    return sorted(p.read_bytes() for p in folder.iterdir() if p.is_file())


@pytest.fixture
def two_series(db, library):
    root = make_library(db, library, {
        "Series A": {fmd2(1, "0001", "Start", "Group A"): payload("a1"), fmd2(2, "0002", "Next", "Group A"): payload("a2"),
                     "Ch. 0003.00 (Third) [Group A].cbz": payload("a3"), "omake.cbz": payload("a-extra")},
        "Series B": {"Series B v01 (2020) (Digital) (Rel).cbz": payload("b1"),
                     "Series B v02 (2021) (Digital) (Rel).cbz": payload("b2")},
    })
    return root


def _sid(db, root, title):
    return db.get_series(root.id, title).id


def test_dry_run_counts_and_lists(renamer, two_series, caplog):
    caplog.set_level(logging.INFO, logger="mangalist.renamer")
    dry = renamer.dry_run()
    assert [r.name for r in dry.roots] == ["Manga"]
    assert dry.count(RENAME) == 4 and dry.count(UNCHANGED) == 1 and dry.count(LEFT_ALONE) == 1
    assert dry.count(COLLISION) == 0
    assert dry.patterns["fmd2"] == 2 and dry.patterns["release"] == 2
    left = dry.files_where(lambda f: f.status == LEFT_ALONE)
    assert [f.name for _s, f in left] == ["omake.cbz"]
    assert ANALYSIS_WARNING in dry.warnings                 # MangaList cannot tell what MangaPixer analysed
    assert "4 file(s) to rename in 2 series" in dry.summary()
    assert any("dry run of Manga: 4 to rename" in r.getMessage() for r in caplog.records)
    # nothing changed on disk
    assert "omake.cbz" in _names(Path(two_series.path) / "Series A")
    assert fmd2(1, "0001", "Start", "Group A") in _names(Path(two_series.path) / "Series A")


def test_volume_names_use_the_title_given(db, namer, hooks, two_series):
    r = Renamer(db, namer=namer, title_for=lambda root, series: "Series Bee" if series.rel_path == "Series B" else None,
                rescan=hooks.rescan, request_scans=hooks.request_scans)
    p = r.preview_series(_sid(db, two_series, "Series B"))
    assert [f.target for f in p.renames] == ["Series Bee - Vol. 001 [Rel].cbz", "Series Bee - Vol. 002 [Rel].cbz"]
    # without a title from MangaPixer: the folder's name
    p = r.preview_series(_sid(db, two_series, "Series A"))
    assert p.series_title == "Series A"


def test_chapter_volumes_from_the_volume_list(db, hooks, two_series):
    r = Renamer(db, namer=FakeNamer(volumes={Decimal(1): Decimal(1)}), title_for=lambda *a: None, rescan=None,
                request_scans=None)
    p = r.preview_series(_sid(db, two_series, "Series A"))
    assert "Ch. 0001.00 Vol. 001 (Start) [Group A].cbz" in [f.target for f in p.renames]


def test_the_windows_server_reaches_the_namer(db, renamer, namer, two_series):
    rn.set_windows_server(db, "\\\\SMIT-SERVER")
    assert rn.windows_server(db) == "SMIT-SERVER"
    renamer.preview_series(_sid(db, two_series, "Series A"))
    assert namer.limit_servers[-1] == "SMIT-SERVER"
    rn.set_windows_server(db, "")
    assert rn.windows_server(db) is None


def test_apply_renames_through_the_journal_and_undo_puts_back(db, renamer, hooks, two_series, caplog):
    caplog.set_level(logging.INFO, logger="mangalist.renamer")
    folder = Path(two_series.path) / "Series A"
    before_names, before_data = _names(folder), _contents(folder)
    preview = renamer.preview_series(_sid(db, two_series, "Series A"))
    [batch] = renamer.batches([preview])
    res = renamer.apply(batch)
    assert res.status == "applied" and res.renamed == 2 and res.plan_id is not None
    assert _names(folder) == sorted(["Ch. 0001.00 (Start) [Group A].cbz", "Ch. 0002.00 (Next) [Group A].cbz",
                                     "Ch. 0003.00 (Third) [Group A].cbz", "omake.cbz"])
    assert _contents(folder) == before_data                 # renames only: every file's bytes unchanged
    plan = Journal(db).get_plan(res.plan_id)
    assert plan.reason == PLAN_REASON and plan.root_path == os.path.normpath(two_series.path)
    assert hooks.rescans == [[two_series.id]] and hooks.scan_requests == [[preview.series_id]]
    assert "recorded" in res.after and "MangaPixer" in res.after
    assert any(f"plan {res.plan_id} applied: 2 file(s)" in r.getMessage() for r in caplog.records)
    # the rescan recorded the new names: nothing is pending any more
    assert not renamer.preview_series(preview.series_id).renames

    last = renamer.last_undoable()
    assert last is not None and last.plan_id == res.plan_id and last.files == 2 and last.done == 2
    undo = renamer.undo(res.plan_id)
    assert undo.status == "undone" and undo.renamed == 2
    assert _names(folder) == before_names and _contents(folder) == before_data
    assert renamer.last_undoable() is None
    assert hooks.rescans[-1] == [two_series.id] and hooks.scan_requests[-1] == [preview.series_id]


def test_a_file_changed_since_the_preview_is_skipped(db, renamer, two_series):
    folder = Path(two_series.path) / "Series A"
    preview = renamer.preview_series(_sid(db, two_series, "Series A"))
    os.rename(folder / fmd2(2, "0002", "Next", "Group A"), folder / fmd2(2, "0002", "Renamed by hand", "Group A"))
    res = renamer.apply(renamer.batches([preview])[0])
    assert res.renamed == 1
    assert res.skipped and "changed since the preview" in res.skipped[0][1]
    assert fmd2(2, "0002", "Renamed by hand", "Group A") in _names(folder)


def test_a_busy_root_renames_nothing(db, renamer, two_series):
    folder = Path(two_series.path) / "Series A"
    before = _names(folder)
    preview = renamer.preview_series(_sid(db, two_series, "Series A"))
    lock = RootLock(two_series.path)
    lock.acquire()
    try:
        res = renamer.apply(renamer.batches([preview])[0])
    finally:
        lock.release()
    assert res.status == "busy" and res.renamed == 0
    assert _names(folder) == before


def test_a_swap_goes_through_a_temporary_name(db, hooks, library):
    root = make_library(db, library, {"Series S": {"a.cbz": payload("one"), "b.cbz": payload("two")}})
    namer = FakeNamer(overrides={"a.cbz": "b.cbz", "b.cbz": "a.cbz"})
    r = Renamer(db, namer=namer, title_for=lambda *a: None, rescan=None, request_scans=None)
    folder = library / "Series S"
    preview = r.preview_series(db.get_series(root.id, "Series S").id)
    assert [f.status for f in preview.files] == [RENAME, RENAME]
    res = r.apply(r.batches([preview])[0])
    assert res.status == "applied" and res.renamed == 2
    assert (folder / "a.cbz").read_bytes() == payload("two") and (folder / "b.cbz").read_bytes() == payload("one")
    assert _names(folder) == ["a.cbz", "b.cbz"]
    assert r.history()[0].files == 2
    assert r.undo(res.plan_id).status == "undone"
    assert (folder / "a.cbz").read_bytes() == payload("one")


def test_collisions_are_never_renamed(db, renamer, library, caplog):
    caplog.set_level(logging.INFO, logger="mangalist.renamer")
    root = make_library(db, library, {"Series C": {fmd2(5, "0005", "Same", "G"): payload("c1"),
                                                   fmd2(9, "0005", "Same", "G"): payload("c2"),
                                                   fmd2(6, "0006", "Six", "G"): payload("c3")}})
    dry = renamer.dry_run(root_ids=[root.id])
    assert dry.count(COLLISION) == 2 and dry.count(RENAME) == 1
    assert any(r.levelno == logging.WARNING and "collision" in r.getMessage() for r in caplog.records)
    res = renamer.apply_all(renamer.batches(dry.series))
    assert [x.renamed for x in res] == [1]
    assert sorted(_names(library / "Series C")) == sorted([fmd2(5, "0005", "Same", "G"), fmd2(9, "0005", "Same", "G"),
                                                          "Ch. 0006.00 (Six) [G].cbz"])


def test_apply_all_rescans_once_at_the_end(db, hooks, two_series):
    r = Renamer(db, namer=FakeNamer(), title_for=lambda *a: None, rescan=hooks.rescan,
                request_scans=hooks.request_scans, batch_size=1)
    dry = r.dry_run()
    batches = r.batches(dry.series)
    assert len(batches) == 2                                  # a series is never split, even past the size
    results = r.apply_all(batches)
    assert [x.status for x in results] == ["applied", "applied"]
    assert hooks.rescans == [[two_series.id]] and len(hooks.scan_requests) == 1
    assert sorted(hooks.scan_requests[0]) == sorted(s.series_id for s in dry.series if s.renames)


def test_roots_set_to_off_are_not_in_the_full_dry_run(db, renamer, two_series):
    two_series.enforce_naming = "off"
    db.update_root(two_series)
    assert renamer.dry_run().roots == []
    assert renamer.dry_run(root_ids=[two_series.id]).count(RENAME) == 4      # named on purpose: still looked at


def test_rename_pending_from_scan_entries(db, renamer, two_series, library):
    from mangalist.scanner import scan_library

    entries = scan_library(db.list_roots(), db=db).entries
    pending = renamer.pending_counts(entries)
    by_name = {Path(k).name: v for k, v in pending.items()}
    assert by_name == {"Series A": 2, "Series B": 2}
    two_series.enforce_naming = "off"
    db.update_root(two_series)
    assert renamer.pending_counts(entries) == {}


def test_without_the_naming_module_nothing_is_pending(db, two_series, monkeypatch):
    from mangalist.scanner import scan_library

    from mangalist import renamer as renamer_module

    def missing(self):                                              # a build without the naming scheme
        raise renamer_module.NamingUnavailable("the naming scheme is not in this build")

    monkeypatch.setattr(renamer_module.DefaultNamer, "_module", missing)
    r = Renamer(db, title_for=lambda *a: None, rescan=None, request_scans=None)
    assert not r.available()
    assert r.pending_counts(scan_library(db.list_roots(), db=db).entries) == {}
    two_series.enforce_naming = "automatic"
    db.update_root(two_series)
    assert r.automatic_pass().skipped == "the naming scheme is not in this build"


def test_automatic_waits_for_the_guided_start(db, renamer, two_series, caplog):
    caplog.set_level(logging.INFO, logger="mangalist.renamer")
    assert renamer.automatic_pass().skipped == "no library renames automatically"
    two_series.enforce_naming = "automatic"
    db.update_root(two_series)
    report = renamer.automatic_pass()
    assert report.waiting == ["Manga"] and report.renamed == 0
    assert any("only after you start its conversion" in r.getMessage() for r in caplog.records)
    renamer.mark_guided([two_series.id])
    assert rn.guided_roots(db) == {two_series.id}
    report = renamer.automatic_pass()
    assert report.renamed == 4 and not report.waiting
    assert "4 file(s) renamed" in report.summary()
    assert renamer.automatic_pass().renamed == 0              # everything in the scheme now


def test_automatic_respects_its_limit_and_unanalysed_series(db, hooks, two_series):
    two_series.enforce_naming = "automatic"
    db.update_root(two_series)
    sid_b = _sid(db, two_series, "Series B")
    r = Renamer(db, namer=FakeNamer(), title_for=lambda *a: None, rescan=hooks.rescan, request_scans=hooks.request_scans,
                analysed=lambda db_, series: False if series.id == sid_b else None, batch_size=1)
    r.mark_guided([two_series.id])
    report = r.automatic_pass(limit=2)
    assert report.renamed == 2
    assert _names(Path(two_series.path) / "Series B") == ["Series B v01 (2020) (Digital) (Rel).cbz",
                                                          "Series B v02 (2021) (Digital) (Rel).cbz"]
    dry = r.dry_run()
    assert rn.NOT_ANALYSED_WARNING.format(n=1) in dry.warnings


def test_history_lists_only_renamer_plans(db, renamer, two_series, tmp_path):
    other = Path(two_series.path) / "Series B" / "Series B v01 (2020) (Digital) (Rel).cbz"
    plan = Journal(db).plan("something else", [(str(other), str(other.with_name("x.cbz")))], root_path=two_series.path)
    assert plan.id
    assert renamer.history() == []
    preview = renamer.preview_series(_sid(db, two_series, "Series A"))
    res = renamer.apply(renamer.batches([preview])[0])
    assert [b.plan_id for b in renamer.history()] == [res.plan_id]
    assert renamer.undo(plan.id).message.endswith("is not a renamer batch")


def test_no_files_recorded_says_rescan(db, renamer, library):
    root = db.add_root(str(library), "Manga")
    (library / "Series N").mkdir()
    rescan(db)
    preview = renamer.preview_series(db.get_series(root.id, "Series N").id)
    assert preview.error and "rescan" in preview.error


def test_a_series_mangapixer_has_not_analysed_is_not_renamed(db, hooks, two_series):
    sid = _sid(db, two_series, "Series A")
    verdict = {"analysed": None}
    r = Renamer(db, namer=FakeNamer(), title_for=lambda *a: None, rescan=None, request_scans=None,
                analysed=lambda db_, series: verdict["analysed"])
    preview = r.preview_series(sid)
    verdict["analysed"] = False                             # MangaPixer turns out not to have analysed them
    res = r.apply(r.batches([preview])[0])
    assert res.renamed == 0 and res.status == "nothing"
    assert all("has not analysed" in why for _n, why in res.skipped) and len(res.skipped) == 2


def test_links_are_left_alone(tmp_path):
    from mangalist.parsing import parse_name
    from mangalist.renamer import plan_files

    real = tmp_path / fmd2(1, "0001")
    real.write_bytes(b"x")
    link = tmp_path / fmd2(2, "0002")
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("no symbolic links here")
    plans = {p.name: p for p in plan_files([(str(real), parse_name(real.name)), (str(link), parse_name(link.name))],
                                           namer=FakeNamer(), series_title="S")}
    assert plans[real.name].status == RENAME
    assert plans[link.name].status == LEFT_ALONE and "link" in plans[link.name].reason
