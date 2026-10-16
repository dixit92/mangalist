"""The renamer and SERIES FOLDERS (owner, 2026-10-15, answer 6: "Always use "<romaji> [<English>]", similar to
animefetch. Folders not in this scheme are rename candidates."): a top-level series folder not named by the scheme is a
rename candidate - shown, counted in Rename pending, renamed by the owner's Rename as ONE journal step (the series keeps
its identity), never by the automatic pass. Made-up titles in a temporary library; the FAKE namer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mangalist import renamer as rn
from mangalist.renamer import COLLISION, LEFT_ALONE, RENAME, UNCHANGED, Renamer
from mangalist.store.journal import Journal

from .conftest import fmd2, make_library, payload, rescan
from .fakes import FakeNamer, Recorder

TITLES = {"Old Folder Name": ("Made Up Romaji", ("The Made Up English",)),
          "Made Up Romaji": ("Made Up Romaji", ("The Made Up English",)),       # the same record after the rename
          "Kept Romaji [Kept English]": ("Kept Romaji", ()),
          "Volume Folder": ("Made Up Volumes", ())}


def titles(root, series):
    return TITLES.get(series.rel_path.rsplit("/", 1)[-1]) or TITLES.get(series.rel_path)


@pytest.fixture
def lib(db, library):
    return make_library(db, library, {
        "Old Folder Name": {fmd2(1, "0001", "Start", "G"): payload("o1"), "Ch. 0002.00 [G].cbz": payload("o2")},
        "Kept Romaji [Kept English]": {"Ch. 0001.00 [G].cbz": payload("k1")},
        "No Title Yet": {"Ch. 0001.00 [G].cbz": payload("n1")},
    })


def make(db, hooks=None, **kw):
    hooks = hooks or Recorder()
    return Renamer(db, namer=kw.pop("namer", FakeNamer()), title_for=kw.pop("title_for", titles), rescan=hooks.rescan,
                   request_scans=hooks.request_scans, **kw)


def _sid(db, root, rel):
    return db.get_series(root.id, rel).id


def test_the_dry_run_shows_the_folder_to_rename(db, lib):
    dry = make(db).dry_run()
    by = {s.title: s for s in dry.series}
    old = by["Old Folder Name"]
    assert (old.folder_status, old.folder_target) == (RENAME, "Made Up Romaji")   # the folder name is no English
    assert old.new_folder == os.path.join(lib.path, "Made Up Romaji") and old.changes == 2
    assert by["Kept Romaji [Kept English]"].folder_status == UNCHANGED             # already the scheme
    no_title = by["No Title Yet"]
    assert no_title.folder_status == LEFT_ALONE and no_title.folder_reason == rn.FOLDER_NO_TITLE
    assert [s.title for s in dry.folders_to_rename] == ["Old Folder Name"]
    assert "1 series folder(s) to rename" in dry.summary() and rn.FOLDER_NOTE in dry.warnings


def test_an_english_folder_name_is_kept_as_the_english_title(db, library):
    root = make_library(db, library, {"The Made Up English": {"Ch. 0001.00 [G].cbz": payload("e1")}})
    r = make(db, title_for=lambda root_, series: ("Made Up Romaji", ()))
    s = r.preview_series(_sid(db, root, "The Made Up English"))
    assert s.folder_target == "Made Up Romaji [The Made Up English]" and s.folder_rename


def test_rename_pending_counts_the_folder_as_one(db, lib):
    from mangalist.scanner import scan_library

    entries = scan_library(db.list_roots(), db=db).entries
    pending = {Path(k).name: n for k, n in make(db).pending_counts(entries).items()}
    assert pending["Old Folder Name"] == 1 + 1                   # its fmd2 file and its folder
    assert "Kept Romaji [Kept English]" not in pending           # named by the scheme, files and folder
    assert "No Title Yet" not in pending                         # no title: nothing pending for the folder
    lib.enforce_naming = "off"
    db.update_root(lib)
    assert make(db).pending_counts(entries) == {}                # a library set to Off never counts


def test_the_owners_rename_renames_the_folder_in_one_step_and_the_series_keeps_its_identity(db, lib):
    hooks = Recorder()
    r = make(db, hooks)
    sid = _sid(db, lib, "Old Folder Name")
    dry = r.dry_run(series_ids=[sid])
    batches = r.batches(dry.series, folders=True)
    assert len(batches) == 1 and batches[0].n_folders == 1 and batches[0].n_files == 1
    res = r.apply(batches[0])
    assert res.status == "applied" and res.renamed == 1 and res.folders == 1
    assert "1 series folder(s) renamed" in res.message
    new = Path(lib.path) / "Made Up Romaji"
    assert new.is_dir() and not (Path(lib.path) / "Old Folder Name").exists()
    assert sorted(p.name for p in new.iterdir()) == ["Ch. 0001.00 (Start) [G].cbz", "Ch. 0002.00 [G].cbz"]
    series = db.series_for_folder(str(new))
    assert series is not None and series.id == sid                # the same series row, at the new folder
    plan = Journal(db).get_plan(res.plan_id)
    assert [s.is_dir for s in plan.steps] == [False, True]        # the files first, then the folder: one step
    assert hooks.scan_requests == [[sid]]
    again = r.dry_run(series_ids=[sid]).series[0]
    assert again.folder_status == UNCHANGED and not again.renames  # in the scheme now


def test_undo_puts_the_folder_back(db, lib):
    r = make(db)
    sid = _sid(db, lib, "Old Folder Name")
    res = r.apply(r.batches(r.dry_run(series_ids=[sid]).series, folders=True)[0])
    assert r.last_undoable().plan_id == res.plan_id
    back = r.undo(res.plan_id)
    assert back.status == "undone" and back.folders == 1 and back.renamed == 1 and back.series_ids == [sid]
    old = Path(lib.path) / "Old Folder Name"
    assert old.is_dir() and fmd2(1, "0001", "Start", "G") in os.listdir(old)
    assert db.series_for_folder(str(old)).id == sid


def test_without_folders_a_batch_renames_files_only(db, lib):
    r = make(db)
    sid = _sid(db, lib, "Old Folder Name")
    res = r.apply(r.batches(r.dry_run(series_ids=[sid]).series)[0])
    assert res.renamed == 1 and res.folders == 0
    assert (Path(lib.path) / "Old Folder Name").is_dir()


def test_the_automatic_pass_never_renames_a_folder(db, lib):
    lib.enforce_naming = "automatic"
    db.update_root(lib)
    r = make(db)
    r.mark_guided([lib.id])
    report = r.automatic_pass()
    assert report.renamed == 1                                   # the file, in its old folder
    assert (Path(lib.path) / "Old Folder Name").is_dir() and not (Path(lib.path) / "Made Up Romaji").exists()
    rescan(db)
    still = make(db).dry_run(root_ids=[lib.id])
    assert [s.title for s in still.folders_to_rename] == ["Old Folder Name"]   # still a candidate for the owner
    assert make(db).automatic_pass().renamed == 0


def test_a_folder_name_taken_by_another_folder_is_a_collision(db, lib):
    (Path(lib.path) / "made up romaji").mkdir()                  # another folder, in another case
    s = make(db).preview_series(_sid(db, lib, "Old Folder Name"))
    assert s.folder_status == COLLISION and "already in the library" in s.folder_reason and not s.folder_rename


def test_two_series_wanting_one_name_both_keep_theirs(db, library):
    root = make_library(db, library, {"First Copy": {"Ch. 0001.00 [G].cbz": payload("f1")},
                                      "Second Copy": {"Ch. 0001.00 [G].cbz": payload("s1")}})
    r = make(db, title_for=lambda root_, series: ("Made Up Twin", ()))
    dry = r.dry_run(root_ids=[root.id])
    assert {s.folder_status for s in dry.series} == {COLLISION}
    assert all("2 series folders would be named" in s.folder_reason for s in dry.series)
    assert r.batches(dry.series, folders=True) == []


def test_two_series_of_one_batch_never_take_one_name(db, library):
    root = make_library(db, library, {"First Copy": {"Ch. 0001.00 [G].cbz": payload("f1")},
                                      "Second Copy": {"Ch. 0001.00 [G].cbz": payload("s1")}})
    r = make(db, title_for=lambda root_, series: ("Made Up Twin", ()))
    previews = [r.preview_series(_sid(db, root, rel)) for rel in ("First Copy", "Second Copy")]   # one at a time
    assert all(p.folder_rename for p in previews)
    res = r.apply(r.batches(previews, folders=True)[0])
    assert res.folders == 1 and any("takes the name" in why for _n, why in res.skipped)
    assert (Path(root.path) / "Made Up Twin").is_dir() and (Path(root.path) / "Second Copy").is_dir()


def test_a_case_only_difference_is_renamed(db, library):
    root = make_library(db, library, {"made up romaji": {"Ch. 0001.00 [G].cbz": payload("c1")}})
    r = make(db, title_for=lambda root_, series: ("Made Up Romaji", ()))
    s = r.preview_series(_sid(db, root, "made up romaji"))
    assert s.folder_status == RENAME and s.folder_target == "Made Up Romaji"
    res = r.apply(r.batches([s], folders=True)[0])
    assert res.folders == 1 and "Made Up Romaji" in os.listdir(root.path)


def test_a_name_the_library_excludes_is_left_alone(db, library):
    root = make_library(db, library, {"Old Folder Name": {"Ch. 0001.00 [G].cbz": payload("x1")}},
                        exclusions=["Made Up*"])
    s = make(db).preview_series(_sid(db, root, "Old Folder Name"))
    assert s.folder_status == LEFT_ALONE and "excludes" in s.folder_reason


def test_subseries_folders_are_never_renamed(db, library):
    root = make_library(db, library, {"Old Folder Name": {"Ch. 0001.00 [G].cbz": payload("p1"),
                                                          "Side 1 - Spin Off/Ch. 0001.00 [G].cbz": payload("p2")}})
    r = make(db, title_for=lambda root_, series: ("Made Up Gaiden", ()) if "/" in series.rel_path else
             ("Made Up Romaji", ()))
    dry = r.dry_run(root_ids=[root.id])
    sub = [s for s in dry.series if "Side 1" in s.folder]
    if sub:                                                      # the scanner keeps it a subseries of its parent
        assert sub[0].folder_status is None
    parent = next(s for s in dry.series if s.title == "Old Folder Name")
    assert parent.folder_target == "Made Up Romaji"


def test_volume_names_carry_no_series_title_and_ignore_a_new_match(db, library):
    # owner, 2026-10-16: "Vol. 001 [group].cbz" - the folder carries the title, so a new match changes no volume name
    root = make_library(db, library, {"Volume Folder": {"Volume Folder v01 (Digital).cbz": payload("v1")}})
    sid = _sid(db, root, "Volume Folder")
    r = make(db)
    s = r.preview_series(sid)
    assert s.series_title == "Made Up Volumes"
    assert s.renames[0].target == "Vol. 001.cbz" and not s.renames[0].held     # renamed before this change too
    TITLES["Volume Folder"] = ("Made Up Volumes Switched", ())     # e.g. MangaPixer's Switch to MangaUpdates
    try:
        assert make(db).preview_series(sid).renames[0].target == "Vol. 001.cbz"
    finally:
        TITLES["Volume Folder"] = ("Made Up Volumes", ())
    bare = Renamer(db, namer=FakeNamer(), title_for=lambda *a: None, rescan=None, request_scans=None)
    assert bare.preview_series(sid).series_title == "Volume Folder"   # no title: the folder's name, as before


def test_filing_names_volumes_in_the_new_format(db, library):
    root = make_library(db, library, {"Kept Romaji [Kept English]": {"Ch. 0001.00 [G].cbz": payload("k1")}})
    series = db.get_series(root.id, "Kept Romaji [Kept English]")
    assert make(db).scheme_name(root, series, "Release v03 (Digital).cbz") == "Vol. 003.cbz"
    real_namer = Renamer(db, title_for=titles, rescan=None, request_scans=None)       # the real naming module
    assert real_namer.scheme_name(root, series, "Release v03 (2021) (Digital) (Made-Group).cbz") == \
        "Vol. 003 [Made-Group].cbz"
    assert real_namer.scheme_name(root, series, "Release v12.5 (Digital) (Made-Group).cbz") == \
        "Vol. 012.5 [Made-Group].cbz"


def test_the_default_titles_come_from_a_confirmed_own_match(db, library):
    from mangalist import mu_cache

    root = make_library(db, library, {"Old Folder Name": {"Ch. 0001.00 [G].cbz": payload("m1")}})
    folder = Path(root.path) / "Old Folder Name"
    mu_cache.save_entry(folder, 77, "Made Up Matched", "https://example.invalid/77", None, mu_confirmed=True,
                        mu_associated=["Made Up Matched", "The Made Up Match"])
    r = Renamer(db, namer=FakeNamer(), rescan=None, request_scans=None)       # the default title lookup
    s = r.preview_series(_sid(db, root, "Old Folder Name"))
    assert s.folder_target == "Made Up Matched" and s.series_title == "Made Up Matched"
    mu_cache.set_mu_confirmed(folder, False)                      # an unconfirmed review match is no title
    s = Renamer(db, namer=FakeNamer(), rescan=None, request_scans=None).preview_series(_sid(db, root, "Old Folder Name"))
    assert s.folder_status == LEFT_ALONE and s.series_title == "Old Folder Name"


def test_plan_folder_rules():
    names = {"taken name", "old"}
    assert rn.plan_folder("Old", "Old", names=names) == (UNCHANGED, "")
    assert rn.plan_folder("Old", None, names=names) == (LEFT_ALONE, rn.FOLDER_NO_TITLE)
    assert rn.plan_folder("Old", "Taken Name", names=names)[0] == COLLISION
    assert rn.plan_folder("Old", "OLD", names=names) == (RENAME, "")          # its own name in another case
    assert rn.plan_folder("Old", "Bad: Name", names=names)[0] == LEFT_ALONE
    assert rn.plan_folder("Old", "New", names=names, excluded=lambda n: n == "New")[0] == LEFT_ALONE
    assert rn.plan_folder("Old", "New", names=names) == (RENAME, "")


# --- renames the new volume format causes wait for the owner (owner, 2026-10-16) ------------------------------------

HELD = {"Vol Romaji [Vol English]": ("Vol Romaji", ()), "Plain Romaji": ("Plain Romaji", ())}
TITLED = {
    "Vol Romaji - Vol. 001.cbz": payload("t1"),                      # the old format with the record's title
    "Vol Romaji [Vol English] - Vol. 002.cbz": payload("t2"),        # the old format with the scheme title: format only
    "Vol Romaji [Vol English] - Vol. 3.cbz": payload("t3"),          # the old format + padding: renamed before too
    "Vol Romaji v04 (Digital) (Made Group).cbz": payload("t4"),      # a release name a new series title held before
    fmd2(5, "0005", "Made Up Day", "G"): payload("t5"),              # a chapter: no series title in its name
}
PLAIN = {
    "Plain Romaji v01 (Digital) (G).cbz": payload("p1"),             # a release name: renamed before too
    "Plain Romaji - Vol. 002.cbz": payload("p2"),                    # the old format: format only
    "Vol. 3.cbz": payload("p3"),                                     # neither: a padding fix
    "Vol. 004 [G].cbz": payload("p4"),                               # already the new format
    "Vol. 005 [no group].cbz": payload("p5"),                        # the new format, another fix: automatic
}


@pytest.fixture
def titled(db, library):
    """Two series of a library set to Rename automatically, named by the REAL naming module: one whose scheme title
    (``Vol Romaji [Vol English]``) differs from the title its volumes were named by before (the record's ``Vol
    Romaji``), one whose title never changed (``Plain Romaji``)."""
    root = make_library(db, library, {"Vol Romaji [Vol English]": dict(TITLED), "Plain Romaji": dict(PLAIN)})
    root.enforce_naming = "automatic"
    db.update_root(root)
    return root


def real(db, hooks=None):
    hooks = hooks or Recorder()
    return Renamer(db, title_for=lambda root, series: HELD.get(series.rel_path), rescan=hooks.rescan,
                   request_scans=hooks.request_scans)


def test_format_only_volume_renames_are_marked_and_wait_for_the_owner(db, titled):
    s = real(db).preview_series(_sid(db, titled, "Vol Romaji [Vol English]"))
    by = {f.name: f for f in s.renames}
    assert by["Vol Romaji - Vol. 001.cbz"].format_held                         # only the format (and title) changes
    assert by["Vol Romaji [Vol English] - Vol. 002.cbz"].format_held           # only the format changes
    assert by["Vol Romaji v04 (Digital) (Made Group).cbz"].format_held         # waited before too: held whole
    assert not by["Vol Romaji [Vol English] - Vol. 3.cbz"].held                # padding: automatic, once, to the end
    assert not by[fmd2(5, "0005", "Made Up Day", "G")].held
    assert {f.name: f.target for f in s.renames} == {
        "Vol Romaji - Vol. 001.cbz": "Vol. 001.cbz", "Vol Romaji [Vol English] - Vol. 002.cbz": "Vol. 002.cbz",
        "Vol Romaji [Vol English] - Vol. 3.cbz": "Vol. 003.cbz",
        "Vol Romaji v04 (Digital) (Made Group).cbz": "Vol. 004 [Made Group].cbz",
        fmd2(5, "0005", "Made Up Day", "G"): "Ch. 0005.00 (Made Up Day) [G].cbz"}
    plain = {f.name: f for f in real(db).preview_series(_sid(db, titled, "Plain Romaji")).files}
    assert plain["Plain Romaji - Vol. 002.cbz"].format_held
    assert sorted(n for n, f in plain.items() if f.status == RENAME and not f.held) == sorted([
        "Plain Romaji v01 (Digital) (G).cbz", "Vol. 3.cbz", "Vol. 005 [no group].cbz"])
    assert plain["Vol. 004 [G].cbz"].status == UNCHANGED
    assert rn.TITLE_NOTE in real(db).dry_run(root_ids=[titled.id]).warnings


def test_a_namer_without_the_old_names_holds_every_volume_out_of_the_new_format(tmp_path):
    from mangalist.parsing import parse_name

    class Plain(FakeNamer):
        titled_target_name = None

    files = []
    for name in ("Made Up v01 (Digital).cbz", "Vol. 2.cbz", "Vol. 002 [no group].cbz", fmd2(3, "0003")):
        (tmp_path / name).write_bytes(b"x")
        files.append((str(tmp_path / name), parse_name(name)))
    by = {os.path.basename(f.path): f for f in rn.plan_files(files, namer=Plain(), series_title="Made Up")}
    assert by["Made Up v01 (Digital).cbz"].format_held and by["Vol. 2.cbz"].format_held      # cannot tell: held
    assert not by["Vol. 002 [no group].cbz"].held and not by[fmd2(3, "0003")].held   # already the format / a chapter
    assert rn.in_volume_format("Vol. 001 [G].cbz") and rn.in_volume_format("Season 2 - Vol. 001.cbz")
    assert not rn.in_volume_format("Made Up - Vol. 001 [G].cbz") and not rn.in_volume_format("Vol. 1.cbz")


def test_the_automatic_pass_skips_format_changes_and_still_fixes_the_rest(db, titled):
    r = real(db)
    r.mark_guided([titled.id])
    report = r.automatic_pass()
    names = sorted(os.listdir(Path(titled.path) / "Vol Romaji [Vol English]"))
    plain = sorted(os.listdir(Path(titled.path) / "Plain Romaji"))
    assert report.renamed == 5 and report.held == 4
    assert "4 file(s) in the new volume name format wait" in report.summary()
    assert names == sorted(["Vol Romaji - Vol. 001.cbz", "Vol Romaji [Vol English] - Vol. 002.cbz", "Vol. 003.cbz",
                            "Vol Romaji v04 (Digital) (Made Group).cbz", "Ch. 0005.00 (Made Up Day) [G].cbz"])
    assert plain == sorted(["Vol. 001 [G].cbz", "Plain Romaji - Vol. 002.cbz", "Vol. 003.cbz", "Vol. 004 [G].cbz",
                            "Vol. 005.cbz"])
    rescan(db)
    pending = real(db).dry_run(root_ids=[titled.id]).series
    assert sum(len(s.held_renames) for s in pending) == 4 and not any(s.auto_renames for s in pending)
    assert real(db).automatic_pass().renamed == 0                               # still Rename pending, for the owner


def test_the_owners_rename_applies_the_format_changes(db, titled):
    r = real(db)
    sid = _sid(db, titled, "Vol Romaji [Vol English]")
    dry = r.dry_run(series_ids=[sid])
    assert r.batches(dry.series)[0].n_files == 2                        # without the owner: only the other fixes
    res = r.apply(r.batches(dry.series, folders=True)[0])               # the window's Rename
    assert res.renamed == 5
    assert sorted(os.listdir(Path(titled.path) / "Vol Romaji [Vol English]")) == sorted([
        "Vol. 001.cbz", "Vol. 002.cbz", "Vol. 003.cbz", "Vol. 004 [Made Group].cbz",
        "Ch. 0005.00 (Made Up Day) [G].cbz"])
    rescan(db)
    again = real(db).preview_series(sid)
    assert not again.renames and all(f.status == UNCHANGED for f in again.files)    # stable: read back exactly
