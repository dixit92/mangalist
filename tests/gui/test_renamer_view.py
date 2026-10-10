"""The Renamer window and its place in the main window (offscreen Qt, a real database over a made-up library in a
temporary folder, the FAKE namer). Nothing depends on thread timing: every background call runs on the test's thread."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mangalist import renamer as rn  # noqa: E402
from mangalist.gui.download_widgets import ROLE_CHIPS, ROLE_SUB  # noqa: E402
from mangalist.gui.renamer_view import (  # noqa: E402
    NOT_AVAILABLE, PILOT_LIBRARY, PILOT_SERIES, SCOPE_ALL, SCOPE_ROOT, STATUS_COLORS, RenamerWindow, Scope)
from mangalist.gui.table_model import COL_RENAME, COL_STATE  # noqa: E402
from mangalist.renamer import COLLISION, RENAME, Renamer  # noqa: E402

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
        assert first.data(ROLE_CHIPS) == [("2 collisions", "bad")]      # collisions as a red chip
        rows = _preview_rows(win)
        assert (fmd2(1, "0001", "Start", "G"), "Ch. 0001.00 (Start) [G].cbz", RENAME) in rows
        assert ("omake.cbz", "(as it is)", "left alone") in rows
        item = win.preview.topLevelItem(0)
        assert item.foreground(0).color().name() == STATUS_COLORS[RENAME]
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
    win._start_scan = lambda roots=None: scans.append([r.id for r in roots] if roots else None)
    rw._confirm = lambda title, text: True
    rw.apply()
    root = lib.root(win, MANGA)
    assert scans == [[root.id]]                              # the window rescans the library renamed in
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
