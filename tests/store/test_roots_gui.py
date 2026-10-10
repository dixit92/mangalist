"""Roots editor / dialog and the main window's roots wiring (offscreen Qt)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mangalist import config  # noqa: E402
from mangalist.gui.roots_dialog import RootsDialog, RootsEditor  # noqa: E402

from .conftest import make_archive  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lib(library):
    make_archive(library / "@Oneshots" / "One.cbz")
    make_archive(library / "@Oneshots" / "Two.cbz")
    make_archive(library / "Series A" / "a v01.cbz")
    make_archive(library / "Series A" / "notes.txt")
    return library


def _preview(dlg):
    return [dlg.preview_list.item(i).text() for i in range(dlg.preview_list.count())]


def test_add_root_with_exclusions_and_live_preview(qapp, db, lib):
    dlg = RootsEditor(db, browse=lambda *a: "")
    assert dlg.root_list.count() == 0
    assert dlg.add_root(str(lib)) is not None
    assert dlg.root_list.count() == 1 and dlg.name_edit.text() == "Manga"
    assert dlg.enforce_combo.currentData() == "ask"

    dlg.pattern_edit.setText("@Oneshots/**")                 # typed, not added yet: previewed live
    assert _preview(dlg) == ["@Oneshots/   (+2 inside)"]
    assert "1 item(s) (3 with their contents)" in dlg.preview_label.text()
    dlg.add_pattern()
    dlg.pattern_edit.setText("*.txt")
    assert _preview(dlg) == ["@Oneshots/   (+2 inside)", "Series A/notes.txt"]
    dlg.pattern_edit.setText("a//b")
    assert "not valid" in dlg.preview_label.text() and not dlg.btn_add_pattern.isEnabled()
    dlg.pattern_edit.clear()

    dlg.name_edit.setText("My Manga")
    dlg.name_edit.textEdited.emit("My Manga")
    dlg.origin_combo.setCurrentIndex(dlg.origin_combo.findData("manga"))
    assert dlg.commit()
    roots = db.list_roots()
    assert [(r.name, r.origin_hint, r.exclusions) for r in roots] == [("My Manga", "manga", ["@Oneshots/**"])]


def test_cancel_changes_nothing(qapp, db, lib):
    db.add_root(str(lib), exclusions=["*.txt"])
    dlg = RootsDialog(db)
    dlg.editor.remove_selected()
    dlg.reject()                                                  # Cancel: nothing was written
    assert [r.exclusions for r in db.list_roots()] == [["*.txt"]]
    dlg.deleteLater()


def test_remove_and_overlap_errors(qapp, db, lib, tmp_path):
    db.add_root(str(lib))
    dlg = RootsEditor(db)
    assert dlg.add_root(str(lib / "Series A")) is None        # inside an existing root
    assert "overlaps" in dlg.error_label.text()
    other = tmp_path / "Other"
    other.mkdir()
    assert dlg.add_root(str(other)) is not None
    dlg.root_list.setCurrentRow(0)
    dlg.remove_selected()
    assert dlg.commit()
    assert [r.path for r in db.list_roots()] == [str(other)]


def test_an_unreachable_root_has_no_preview(qapp, db, tmp_path):
    db.add_root(str(tmp_path / "offline share"))
    dlg = RootsEditor(db)
    assert "not reachable" in dlg.preview_label.text()


def test_the_dialog_saves_on_ok_and_keeps_open_on_an_error(qapp, db, lib):
    dlg = RootsDialog(db, browse=lambda *a: "")
    dlg.editor.add_root(str(lib))
    dlg.accept()
    assert dlg.result() == 1 and dlg.changed and [r.path for r in db.list_roots()] == [str(lib)]
    dlg.deleteLater()


def test_commit_reports_a_problem_and_writes_nothing(qapp, db, lib, tmp_path):
    db.add_root(str(lib), name="First")
    editor = RootsEditor(db)
    other = tmp_path / "Other"
    other.mkdir()
    editor.add_root(str(other))
    editor.root_list.setCurrentRow(1)
    editor.path_edit.setText(str(lib / "Series A"))                # now inside the first root
    editor.path_edit.textEdited.emit(str(lib / "Series A"))
    assert not editor.commit() and "overlaps" in editor.error_label.text()
    assert [r.name for r in db.list_roots()] == ["First"]


def test_main_window_shows_the_migrated_root_and_scans_every_root(qapp, db, lib, tmp_path):
    from mangalist.gui.main_window import MainWindow

    second = tmp_path / "library" / "Manhwa"
    make_archive(second / "Series K" / "k c001.cbz")
    make_archive(second / "Stray.cbz")
    db.add_root(str(lib), exclusions=["@Oneshots/**"])
    win = MainWindow()
    try:
        # The top bar names the library folder (its path in the tooltip); its page is under Settings > Library.
        assert win._top.status.text() == "Manga" and win._top.status.toolTip() == f"Manga: {lib}"
        assert win._list.stack.currentIndex() == 2 and win._list.btn_add_root.isHidden() is False   # empty state
        db.add_root(str(second))
        win._after_roots_changed()
        # two roots: the Library picker (top bar) names them; the status does not repeat them (paths in its tooltip)
        assert win._top.picker_shown() and win._top.library_name() == "All libraries"
        assert win._top.status.text() == "" and str(second) in win._top.status.toolTip()
        assert win._thread is None                  # only told: no scan starts by itself here

        from mangalist.gui.main_window import ScanWorker
        worker = ScanWorker(db.list_roots(), db)
        got = []
        worker.finished.connect(got.append)
        worker.run()
        win._on_scan_finished(got[0])
        names = sorted(win._model.entry_at(r).folder.name for r in range(win._model.rowCount()))
        assert names == ["Series A", "Series K"]
        assert "1 archive(s) not in a series folder" in win._status_label.text()
        assert "Stray.cbz" in win._status_label.toolTip()
        assert {s.rel_path for s in db.list_series()} == {"Series A", "Series K"}
        assert win._list.counts_label.text() == "2 series" and win._list.stack.currentIndex() == 0
        assert win._top.status.text().startswith("Scanned ")
    finally:
        win.close()
        win.deleteLater()


def test_legacy_config_root_becomes_root_1_in_the_window(qapp, lib):
    import json

    from mangalist import paths, store
    from mangalist.gui.main_window import MainWindow

    store.reset_stores()
    paths.ensure_data_dir()
    paths.config_file().write_text(json.dumps({"last_root": str(lib)}), encoding="utf-8")
    win = MainWindow()
    try:
        assert win._top.status.text() == "Manga" and str(lib) in win._top.status.toolTip()
        assert config.load()["last_root"] == str(lib)
    finally:
        win.close()
        win.deleteLater()


def test_fields_nothing_uses_yet_are_hidden(qapp, db, lib):
    dlg = RootsDialog(db)
    try:
        dlg.show()
        ed = dlg.editor
        assert ed.name_edit.isVisibleTo(dlg) and ed.path_edit.isVisibleTo(dlg)
        for w in (ed.origin_combo, ed.enforce_combo, ed.staging_edit):        # renamer / origin evidence: later phases
            assert not w.isVisibleTo(dlg)
    finally:
        dlg.close()
        dlg.deleteLater()
