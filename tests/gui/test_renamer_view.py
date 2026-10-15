"""The Renamer window and its place in the main window (offscreen Qt, a real database over a made-up library in a
temporary folder, the FAKE namer). Nothing depends on thread timing: every background call runs on the test's thread."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mangalist import renamer as rn  # noqa: E402
from mangalist.gui.download_widgets import ROLE_CHIPS, ROLE_SUB  # noqa: E402
from mangalist.gui.renamer_view import (  # noqa: E402
    NOT_AVAILABLE, PILOT_LIBRARY, PILOT_SERIES, SCOPE_ALL, SCOPE_ROOT, STATUS_COLORS, RenamerWindow, Scope)
from mangalist.gui.table_model import COL_RENAME, COL_STATE  # noqa: E402
from mangalist.renamer import COLLISION, RENAME, UNCHANGED, Renamer  # noqa: E402

from ..renamer.conftest import fmd2, make_library, payload  # noqa: E402
from ..renamer.fakes import FakeNamer, Recorder  # noqa: E402
from .conftest import qapp  # noqa: E402,F401
from .test_library_picker import MANGA, MANHWA, lib  # noqa: E402,F401


def sync_run(fn, on_done, on_error):
    """The window's runner, on this thread."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        on_error(f"unexpected error ({type(exc).__name__})")
        return None
    on_done(result)
    return None


def sync_call(fn, on_done=None, on_error=None, on_finished=None):
    """``background.start_call`` on this thread."""
    sync_run(fn, on_done or (lambda r: None), on_error or (lambda m: None))
    if on_finished is not None:
        on_finished()
    return None


@pytest.fixture
def db():
    from mangalist import store

    store.reset_stores()
    return store.get_store()


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "library" / "Manga"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def made(db, library):
    root = make_library(db, library, {
        "Series A": {fmd2(1, "0001", "Start", "G"): payload("a1"), fmd2(2, "0002", "Next", "G"): payload("a2"),
                     "omake.cbz": payload("ax")},
        "Series C": {fmd2(5, "0005", "Same", "G"): payload("c1"), fmd2(9, "0005", "Same", "G"): payload("c2")},
    })
    return root


def _renamer(db, hooks=None, **kw):
    hooks = hooks or Recorder()
    return Renamer(db, namer=kw.pop("namer", FakeNamer()), title_for=lambda *a: None, rescan=hooks.rescan,
                   request_scans=hooks.request_scans, **kw)


def _window(qapp, renamer, scope, answers=None):
    asked = []

    def confirm(title, text):
        asked.append((title, text))
        return answers.pop(0) if answers else True

    win = RenamerWindow(renamer, scope, run=sync_run, confirm=confirm)
    win.asked = asked
    return win


def _preview_rows(win):
    return [(win.preview.topLevelItem(i).text(0), win.preview.topLevelItem(i).text(1),
             win.preview.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)) for i in range(win.preview.topLevelItemCount())]


def _names(folder: Path):
    return sorted(p.name for p in folder.iterdir())


def test_the_dry_run_summary_the_preview_and_the_notes(qapp, db, made):
    win = _window(qapp, _renamer(db), Scope.all())
    try:
        assert not win.busy and win.dry_run is not None
        assert "2 files to rename in 1 series" in win.summary_label.text()
        assert "2 name collisions" in win.summary_label.text()
        assert "FMD2 names 4" in win.patterns_label.text()
        assert rn.ANALYSIS_WARNING in win.warning_label.text()
        assert win.btn_apply.text() == "Rename 2 files…" and win.btn_apply.isEnabled()
        assert win.pilot_note.text() == PILOT_LIBRARY and win.btn_library.isHidden()
        # series with renames first; its old -> new, coloured
        first = win.series_list.item(0)
        assert first.text() == "Series A"                               # the title alone on its line ...
        assert "to rename" in first.data(ROLE_SUB)                      # ... the counts under it (owner, 2026-10-10)
        chips = [win.series_list.item(i).data(ROLE_CHIPS) for i in range(win.series_list.count())]
        assert [("2 collisions", "bad")] in chips                       # collisions as a red chip
        assert all(not (win.series_list.item(i).data(ROLE_SUB) or "").count("collision")
                   for i in range(win.series_list.count()))             # ... not repeated in the grey line
        rows = _preview_rows(win)
        assert (fmd2(1, "0001", "Start", "G"), "Ch. 0001.00 (Start) [G].cbz", RENAME) in rows
        assert ("omake.cbz", "(as it is)", "left alone") in rows
        item = win.preview.topLevelItem(0)
        assert item.foreground(0).color().name() == STATUS_COLORS[RENAME]
        assert item.text(2).startswith("will be renamed")                # a preview, not a result (owner, 2026-10-10)
        assert win.preview.headerItem().text(2) == "What happens"
        # the collision series offers the duplicates review
        win.series_list.setCurrentRow(1)
        assert {r[2] for r in _preview_rows(win)} == {COLLISION}
        assert not win.btn_duplicates.isHidden()
        asked = []
        win.duplicates_requested.connect(asked.append)
        win.btn_duplicates.click()
        assert asked == [str(Path(made.path) / "Series C")]
        notes = [win.notes.topLevelItem(i).text(0) for i in range(win.notes.topLevelItemCount())]
        assert notes == ["Left alone (1)", "Name collisions (both files keep their names) (2)"]
        assert win.tabs.tabText(1) == "To check (3)"
    finally:
        win.deleteLater()


def test_apply_asks_with_the_counts_renames_and_undo_puts_back(qapp, db, made):
    hooks = Recorder()
    win = _window(qapp, _renamer(db, hooks), Scope.root(made.id, "Manga"), answers=[False, True, True])
    changed = []
    win.library_changed.connect(changed.append)
    folder = Path(made.path) / "Series A"
    before = _names(folder)
    try:
        win.apply()                                         # the owner says Cancel
        assert _names(folder) == before and changed == []
        title, text = win.asked[0]
        assert title == "Rename 2 files?" and "Rename 2 files in 1 series (1 batch)?" in text
        assert "1 file(s) are left alone and 2 name collisions are not renamed" in text
        assert rn.ANALYSIS_WARNING in text
        win.apply()                                         # yes
        assert _names(folder) == sorted(["Ch. 0001.00 (Start) [G].cbz", "Ch. 0002.00 (Next) [G].cbz", "omake.cbz"])
        assert changed == [[made.id]]
        assert "2 files renamed in 1 batch." in win.result_label.text()
        assert rn.guided_roots(db) == {made.id}             # a library-wide apply starts its guided conversion
        assert win.dry_run.count(RENAME) == 0 and not win.btn_apply.isEnabled()      # looked again afterwards
        assert win.btn_undo.isEnabled()
        win.undo_last()                                     # yes
        assert _names(folder) == before
        assert changed == [[made.id], [made.id]]
        assert "back under their old names" in win.result_label.text()
        assert win.dry_run.count(RENAME) == 2 and not win.btn_undo.isEnabled()
    finally:
        win.deleteLater()


def test_the_pilot_series_then_its_library(qapp, db, made):
    sid = db.get_series(made.id, "Series A").id
    win = _window(qapp, _renamer(db), Scope.series([sid], "Series A"))
    try:
        assert win.heading.text() == "Rename to the scheme: Series A" and win.pilot_note.text() == PILOT_SERIES
        assert win.series_list.count() == 1 and not win.btn_library.isHidden()
        win.apply()
        assert "check its read marks" in win.result_label.text()
        assert rn.guided_roots(db) == set()                 # a pilot is not the library's conversion
        win.widen_to_library()
        assert win.scope.kind == SCOPE_ROOT and win.scope.ids == (made.id,)
        assert win.heading.text() == "Rename library: Manga" and win.btn_library.isHidden()
        assert win.series_list.count() == 2
    finally:
        win.deleteLater()


def test_without_the_naming_module_nothing_can_be_renamed(qapp, db, made, monkeypatch):
    from mangalist import renamer as renamer_module

    def missing(self):                                              # a build without the naming scheme
        raise renamer_module.NamingUnavailable("the naming scheme is not in this build")

    monkeypatch.setattr(renamer_module.DefaultNamer, "_module", missing)
    win = _window(qapp, Renamer(db, rescan=None, request_scans=None), Scope.all())
    try:
        assert win.summary_label.text() == NOT_AVAILABLE
        assert not win.btn_apply.isEnabled() and win.series_list.count() == 0
    finally:
        win.deleteLater()


def test_a_failure_is_shown_and_the_window_stays_usable(qapp, db, made):
    class Broken(FakeNamer):
        def limits(self, windows_server):
            raise RuntimeError("boom")

    win = _window(qapp, _renamer(db, namer=Broken()), Scope.all())
    try:
        assert "Something went wrong: unexpected error (RuntimeError)" in win.result_label.text()
        assert not win.busy and not win.btn_apply.isEnabled()
    finally:
        win.deleteLater()


# --- the main window ---------------------------------------------------------------------------------------------


def _fmd2_series(root: Path, name: str, chapters=(1, 2)) -> None:
    for c in chapters:
        p = root / name / fmd2(c, f"{c:04d}", f"Part {name[-1]}{c}", "G")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload(f"{root.name}/{name}/{c}"))


def _wire(win, hooks=None):
    hooks = hooks or Recorder()
    win._background = sync_call
    win._renamer_runner = sync_run
    win._make_renamer = lambda: Renamer(win._db, namer=FakeNamer(), title_for=lambda *a: None, rescan=None,
                                        request_scans=hooks.request_scans)
    return hooks


def _row_of(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def test_rename_pending_shows_in_the_list_and_the_row_menu_opens_the_pilot(lib):
    _fmd2_series(lib.dirs[MANGA], "Series P")
    (lib.dirs[MANGA] / "Series Q").mkdir()
    (lib.dirs[MANGA] / "Series Q" / "Ch. 0001.00 [G].cbz").write_bytes(payload("q"))
    win = lib.make_window()
    _wire(win)
    win._db.add_root(str(lib.dirs[MANGA]), MANGA)
    win._show_roots()
    lib.scan_synchronously(win)
    model = win._model
    p, q = _row_of(win, "Series P"), _row_of(win, "Series Q")
    assert model.rename_count_at(p) == 2 and model.rename_count_at(q) == 0
    assert "Rename pending" in model.data(model.index(p, COL_STATE))
    assert "Rename pending: 2 files" in model.data(model.index(p, COL_STATE), Qt.ItemDataRole.ToolTipRole)
    assert model.data(model.index(p, COL_RENAME)) == "2 files" and model.data(model.index(q, COL_RENAME)) == ""
    assert win._table.horizontalHeader().isSectionHidden(COL_RENAME)      # a column to choose; the flag shows anyway
    assert win._list._counts.get("rename") == 1
    win._proxy.set_state_filter("rename")
    assert lib.shown(win) == ["Series P"]
    win._proxy.set_state_filter(None)

    menus = []

    def answer(menu, pos):
        acts = {a.text(): a for a in menu.actions()}
        menus.append(list(acts))
        return acts["Rename to the scheme (2 files)…"]

    win._exec_menu = answer
    win._select_source_row(p)
    rect = win._table.visualRect(win._proxy.mapFromSource(model.index(p, 2)))
    win._on_context_menu(rect.center())
    assert "Rename its whole library…" in menus[0]
    rw = win._renamer_window
    assert rw is not None and rw.scope.kind == "series" and rw.scope.label == "Series P"
    assert rw.dry_run.count(RENAME) == 2
    scans = []
    win._start_scan = lambda roots=None, scopes=None: scans.append(([r.id for r in roots] if roots else None, scopes))
    rw._confirm = lambda title, text: True
    rw.apply()
    root = lib.root(win, MANGA)
    assert scans == [([root.id], {root.id: {"Series P"}})]   # the window rescans the folder renamed in (the journal)
    assert sorted(p.name for p in (lib.dirs[MANGA] / "Series P").iterdir()) == [
        "Ch. 0001.00 (Part P1) [G].cbz", "Ch. 0002.00 (Part P2) [G].cbz"]
    rw.close()


def test_rename_library_takes_the_library_picked_or_every_library(lib):
    _fmd2_series(lib.dirs[MANGA], "Series P")
    _fmd2_series(lib.dirs[MANHWA], "Series K")
    win = lib.make_window()
    _wire(win)
    win._db.add_root(str(lib.dirs[MANGA]), MANGA)
    win._db.add_root(str(lib.dirs[MANHWA]), MANHWA)
    win._show_roots()
    lib.scan_synchronously(win)
    win._list.btn_rename.click()
    rw = win._renamer_window
    assert rw.scope.kind == SCOPE_ALL and rw.dry_run.count(RENAME) == 4
    lib.pick(win, MANHWA)
    win._list.btn_rename.click()
    assert win._renamer_window is rw                         # one window, aimed at the library picked
    assert rw.scope == Scope.root(lib.root(win, MANHWA).id, MANHWA) and rw.dry_run.count(RENAME) == 2
    rw.close()


def test_series_already_in_the_scheme_are_hidden_until_asked_for(qapp, db, tmp_path):
    # owner, 2026-10-10: after renaming a library every series said a small "in the scheme"
    folder = tmp_path / "library2" / "Manga"
    folder.mkdir(parents=True)
    root = make_library(db, folder, {
        "Series A": {fmd2(1, "0001", "Start", "G"): payload("a1")},
        "Series D": {"Ch. 0001.00 (Done) [G].cbz": payload("d1")},
    })
    win = _window(qapp, _renamer(db), Scope.root(root.id, "Manga"))
    try:
        titles = lambda: [win.series_list.item(i).text() for i in range(win.series_list.count())]  # noqa: E731
        assert titles() == ["Series A"]
        assert win.show_done.text() == "Show series already in the scheme (1)" and win.show_done.isEnabled()
        win.series_list.setCurrentRow(0)
        win.show_done.setChecked(True)
        assert titles() == ["Series A", "Series D"] and win.series_list.currentRow() == 0     # the selection stays
        win.show_done.setChecked(False)
        win.apply()                                         # Series A renamed: nothing left to list
        assert titles() == [] and win.show_done.text() == "Show series already in the scheme (2)"
        assert win.preview.topLevelItemCount() == 0
    finally:
        win.deleteLater()


def test_after_a_rename_the_preview_waits_for_the_rescan(qapp, db, made):
    # owner, 2026-10-10: right after a library's rename every renamed file showed under its old name, "left alone: the
    # file is gone since the last scan" - the preview had looked before the rescan
    from ..renamer.conftest import rescan

    win = _window(qapp, _renamer(db), Scope.root(made.id, "Manga"))
    win.rescan_follows = True
    changed = []
    win.library_changed.connect(changed.append)
    try:
        win.apply()
        assert changed == [[made.id]]
        assert "Rescanning the library" in win.busy_label.text() and win.series_list.count() == 0
        assert not win.busy_bar.isHidden() and win.busy                # the busy bar shows the wait (owner, 2026-10-11)
        assert not win.btn_undo.isEnabled() and not win.btn_apply.isEnabled()
        rescan(db)                                          # the host's rescan
        win.library_rescanned([999])                        # another library: still waiting
        assert "Rescanning the library" in win.busy_label.text()
        win.library_rescanned([made.id])
        assert win.busy_bar.isHidden() and not win.busy
        assert win.dry_run.count(RENAME) == 0 and win.dry_run.count(UNCHANGED) == 2      # the new names, in the scheme
        assert not any("gone" in (f.reason or "") for s in win.dry_run.series for f in s.files)
        assert win.btn_undo.isEnabled()
    finally:
        win.deleteLater()


def test_bare_numbers_can_be_answered_chapters_in_the_renamer(qapp, db, tmp_path):
    # owner, 2026-10-11: "You should be able to set unknown as chapters or volumes from within the renamer"
    folder = tmp_path / "library3" / "Manga"
    folder.mkdir(parents=True)
    root = make_library(db, folder, {"Bare Series": {"Bare Series 001 Read Online.cbz": payload("b1"),
                                                     "Bare Series 002 Read Online.cbz": payload("b2"),
                                                     "Bare Series Ch.003.cbz": payload("b3")}})
    win = _window(qapp, _renamer(db, namer=None), Scope.root(root.id, "Manga"))
    try:
        s = win._current_series()
        assert [f.name for f in s.needs_kind] == ["Bare Series 001 Read Online.cbz", "Bare Series 002 Read Online.cbz"]
        assert not win.kind_bar.isHidden() and "2 files" in win.kind_label.text()
        assert all("chapters or volumes?" in f.reason for f in s.needs_kind)
        win.btn_kind_chapters.click()
        assert db.get_series(root.id, "Bare Series").kind_hint == "chapters"
        after = {os.path.basename(f.path): f.target for f in win.dry_run.series[0].files}
        assert after["Bare Series 001 Read Online.cbz"] == "Ch. 0001.00.cbz" and win.kind_bar.isHidden()
        assert "bare numbers are chapters" in win.result_label.text()
    finally:
        win.deleteLater()


def test_answering_shows_the_wait_until_the_rescan(qapp, db, tmp_path):
    # owner, 2026-10-11: "I clicked on 'chapters' here and the page was blank with no indication it was refreshing"
    from ..renamer.conftest import rescan

    folder = tmp_path / "library4" / "Manga"
    folder.mkdir(parents=True)
    root = make_library(db, folder, {"Bare Series": {"Bare Series 001 Read Online.cbz": payload("w1")}})
    win = _window(qapp, _renamer(db, namer=None), Scope.root(root.id, "Manga"))
    win.rescan_follows = True
    try:
        assert not win.kind_bar.isHidden()
        win.btn_kind_chapters.click()
        assert win.busy and not win.busy_bar.isHidden() and "Rescanning the library" in win.busy_label.text()
        assert win.kind_bar.isHidden() and not win.btn_apply.isEnabled()
        rescan(db)
        win.library_rescanned([root.id])
        assert not win.busy and win.dry_run.count(RENAME) == 1
    finally:
        win.deleteLater()


def test_a_left_alone_file_can_be_given_its_chapter_from_the_row_menu(qapp, db, tmp_path):
    # owner, 2026-10-11: "I still can't set volume or chapter in the rename window" - a name cut off after "Ch"
    folder = tmp_path / "library4" / "Manga"
    folder.mkdir(parents=True)
    cut = "0001 [Vol. 0001 Ch.cbz"
    root = make_library(db, folder, {"Cut Series": {cut: payload("c1"), fmd2(2, "0002", "Next", "G"): payload("c2")}})
    win = _window(qapp, _renamer(db, namer=None), Scope.root(root.id, "Manga"))
    try:
        win.show_done.setChecked(True)
        rows = {win.preview.topLevelItem(i).text(0): win.preview.topLevelItem(i) for i in
                range(win.preview.topLevelItemCount())}
        assert "right-click to set its chapter or volume" in rows[cut].text(2)
        menus = []
        win._exec_menu = lambda menu, pos: menus.append([a.text() for a in menu.actions()]) or menu.actions()[0]
        win._ask_units = lambda name, current: ("chapter", "1", "")
        win._on_preview_menu(win.preview.visualItemRect(rows[cut]).center())
        assert menus == [["Set chapter / volume…"]]
        after = {os.path.basename(f.path): f for f in win.dry_run.series[0].files}
        assert after[cut].status == RENAME and after[cut].target == "Ch. 0001.00 Vol. 001.cbz"
        assert "chapter 1" in win.result_label.text()
        rows = {win.preview.topLevelItem(i).text(0): win.preview.topLevelItem(i) for i in
                range(win.preview.topLevelItemCount())}
        assert "number set by you" in rows[cut].text(2)
        win._on_preview_menu(win.preview.visualItemRect(rows[cut]).center())         # set again: Forget is offered
        assert menus[-1] == ["Set chapter / volume…", "Forget the number I set"]
    finally:
        win.deleteLater()


def test_the_units_dialog_checks_what_is_typed(qapp):
    from mangalist.gui.renamer_view import UnitsDialog
    from mangalist.renamer import UNIT_VOLUME, FileUnits
    from decimal import Decimal

    dlg = UnitsDialog("x.cbz", FileUnits(UNIT_VOLUME, Decimal(3), Decimal(4)))
    assert dlg.rb_volume.isChecked() and dlg.numbers.text() == "3-4" and not dlg.volume.isEnabled()
    dlg.numbers.setText("4-3")
    dlg._accept()
    assert "smaller number" in dlg.error.text() and dlg.result() == 0
    dlg.rb_chapter.setChecked(True)
    dlg.numbers.setText("12.5")
    dlg.volume.setText("2")
    dlg._accept()
    assert dlg.result() == 1 and dlg.answer() == ("chapter", "12.5", "2")


def test_the_first_library_rename_asks_once_to_rename_automatically(qapp, db, made):
    # owner, 2026-10-12: "The user should be prompted to enable this after they have done their first library renaming
    # for a given library"
    sid = db.get_series(made.id, "Series A").id
    asked = []
    pilot = _window(qapp, _renamer(db), Scope.series([sid], "Series A"))
    try:
        pilot._ask_automatic = lambda name: asked.append(name) or True
        pilot.apply()
        assert asked == []                                          # a pilot series: not yet
    finally:
        pilot.deleteLater()
    (Path(made.path) / "Series A" / fmd2(3, "0003", "Third", "G")).write_bytes(payload("a3"))
    from ..renamer.conftest import rescan

    rescan(db)
    win = _window(qapp, _renamer(db), Scope.root(made.id, "Manga"))
    try:
        win._ask_automatic = lambda name: asked.append(name) or True
        win.apply()
        assert asked == ["Manga"] and db.get_root(made.id).enforce_naming == rn.ENFORCE_AUTOMATIC
    finally:
        win.deleteLater()
    db.update_root(__import__("dataclasses").replace(db.get_root(made.id), enforce_naming="ask"))
    (Path(made.path) / "Series A" / fmd2(4, "0004", "Fourth", "G")).write_bytes(payload("a4"))
    rescan(db)
    again = _window(qapp, _renamer(db), Scope.root(made.id, "Manga"))
    try:
        again._ask_automatic = lambda name: asked.append(name) or True
        again.apply()
        assert asked == ["Manga"]                                   # asked once per library, whatever the answer
    finally:
        again.deleteLater()


def test_a_series_folder_not_in_the_scheme_is_shown_and_renamed_by_the_owners_rename(qapp, db, tmp_path):
    # owner, 2026-10-15 (answer 6): "Folders not in this scheme are rename candidates"
    library = tmp_path / "folders" / "Manga"
    library.mkdir(parents=True)
    root = make_library(db, library, {"Old Made Up Folder": {"Ch. 0001.00 [G].cbz": payload("f1")}})
    hooks = Recorder()
    r = Renamer(db, namer=FakeNamer(), rescan=hooks.rescan, request_scans=hooks.request_scans,
                title_for=lambda root_, series: ("Made Up Romaji", ("The Made Up English",))
                if series.rel_path in ("Old Made Up Folder", "Made Up Romaji") else None)
    win = _window(qapp, r, Scope.root(root.id, "Manga"))
    try:
        assert "1 series folder not named" in win.summary_label.text()
        assert win.btn_apply.text() == "Rename 1 folder…" and win.btn_apply.isEnabled()
        assert "folder renamed" in win.series_list.item(0).data(ROLE_SUB)
        rows = _preview_rows(win)
        assert rows[0] == ("Old Made Up Folder/", "Made Up Romaji/", RENAME)        # the folder's own row, first
        assert "keeps its data" in win.preview.topLevelItem(0).text(2)
        assert rn.FOLDER_NOTE in win.warning_label.text()
        win.apply()
        title, text = win.asked[0]
        assert title == "Rename 1 folder?" and "Old Made Up Folder  ->  Made Up Romaji" in text
        assert (library / "Made Up Romaji").is_dir() and not (library / "Old Made Up Folder").exists()
        assert "1 series folder renamed to the scheme." in win.result_label.text()
        assert not win.dry_run.folders_to_rename and not win.btn_apply.isEnabled()  # looked again: in the scheme
        win.undo_last()
        assert (library / "Old Made Up Folder").is_dir()
    finally:
        win.deleteLater()


def test_the_automatic_mode_texts_say_folders_are_never_renamed_automatically():
    from mangalist.gui.renamer_view import LEAD, automatic_text
    from mangalist.gui.roots_dialog import ENFORCE_TIP

    for text in (LEAD, automatic_text("Manga"), ENFORCE_TIP):
        assert "never" in text and "<romaji> [<English>]" in text
        assert "Folder names and series-title changes wait for your Rename" in text


def test_title_changes_are_marked_in_the_preview_and_listed_to_check(qapp, db, tmp_path):
    library = tmp_path / "titled" / "Manga"
    library.mkdir(parents=True)
    root = make_library(db, library, {"Vol Romaji [Vol English]": {"Vol Romaji - Vol. 001.cbz": payload("t1")}})
    r = Renamer(db, rescan=None, request_scans=None,
                title_for=lambda root_, series: ("Vol Romaji", ()) if series.rel_path.startswith("Vol Romaji") else None)
    win = _window(qapp, r, Scope.root(root.id, "Manga"))
    try:
        row = [win.preview.topLevelItem(i) for i in range(win.preview.topLevelItemCount())
               if win.preview.topLevelItem(i).text(0) == "Vol Romaji - Vol. 001.cbz"][0]
        assert row.text(1) == "Vol Romaji [Vol English] - Vol. 001.cbz" and "new series title" in row.text(2)
        assert win.notes.topLevelItem(0).text(0).startswith("New series title")
        win.apply()
        assert "renamed only because of the new series title" in win.asked[0][1]
        assert (library / "Vol Romaji [Vol English]" / "Vol Romaji [Vol English] - Vol. 001.cbz").exists()
    finally:
        win.deleteLater()
