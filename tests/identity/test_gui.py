"""Missing series dialog and the main window's identity wiring: "Missing (N)" only when N > 0, the background
signature worker with its status-bar progress, examined marks following carried series (offscreen Qt)."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mangalist import mu_cache  # noqa: E402
from mangalist.identity import carry  # noqa: E402

from .conftest import make_archive, scan, sign  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _missing_empty_folder(db, library):
    """An empty (wanted) folder with an own link, renamed: missing, nothing to recognise it by."""
    (library / "Wanted").mkdir()
    make_archive(library / "Other Series" / "Other v01.cbz")
    root = db.add_root(str(library))
    first = scan(db)
    folder = next(e.folder for e in first.entries if e.folder.name == "Wanted")
    mu_cache.save_entry(folder, 8, "Wanted Title", "", None, mu_confirmed=True)
    db.set_setting("examined", [str(folder)])
    (library / "Wanted").rename(library / "Wanted (Renamed)")
    scan(db)
    return root, folder


def test_dialog_lists_reattaches_and_forgets(qapp, db, library):
    from mangalist.gui.missing_series_dialog import MissingSeriesDialog

    root, folder = _missing_empty_folder(db, library)
    offered = []

    def choose(parent, missing, targets):
        offered.append([t.rel_path for t in targets])
        return next(t.id for t in targets if t.rel_path == "Wanted (Renamed)")

    dlg = MissingSeriesDialog(db, choose=choose, confirm=lambda p, t: True)
    assert dlg.table.rowCount() == 1
    assert [dlg.table.item(0, c).text() for c in range(6)] == ["Wanted", "Manga", dlg.table.item(0, 2).text(),
                                                               "MU 8", "", "yes"]
    assert dlg.reattach_selected()
    assert sorted(offered[0]) == ["Other Series", "Wanted (Renamed)"]       # live folders without own data
    assert dlg.table.rowCount() == 0 and dlg.changed
    assert dlg.renamed == [(str(folder), str(folder.with_name("Wanted (Renamed)")))]
    assert mu_cache.load_entry(folder.with_name("Wanted (Renamed)"))["mu_id"] == 8
    assert "re-attached" in dlg.status_label.text()


def test_dialog_forget_asks_first(qapp, db, library):
    from mangalist.gui.missing_series_dialog import MissingSeriesDialog

    root, folder = _missing_empty_folder(db, library)
    answers = [False, True]
    texts = []

    def confirm(parent, text):
        texts.append(text)
        return answers.pop(0)

    dlg = MissingSeriesDialog(db, confirm=confirm)
    assert not dlg.forget_selected() and dlg.table.rowCount() == 1         # declined: nothing forgotten
    assert "its MangaUpdates link" in texts[0] and "its examined mark" in texts[0]
    assert dlg.forget_selected() and dlg.table.rowCount() == 0
    assert dlg.forgotten == [str(folder)] and carry.missing_count(db) == 0
    assert mu_cache.load_entry(folder) is None


def test_picker_filters_targets(qapp, db, library):
    from mangalist.gui.missing_series_dialog import TargetPicker

    _missing_empty_folder(db, library)
    (m,) = carry.missing_series(db)
    picker = TargetPicker(m, carry.live_series(db, without_own_data=True))
    assert picker.list.count() == 2
    picker.filter_edit.setText("renamed")
    assert picker.list.count() == 1 and picker.selected_id() is not None


def test_main_window_missing_button_and_examined_follow(qapp, db, library):
    from mangalist.gui.main_window import MainWindow, ScanWorker, SignatureWorker

    for i in range(3):
        make_archive(library / "Old Title" / f"Old Title v{i + 1:02}.cbz", size=2000 + i)
    (library / "Wanted").mkdir()
    db.add_root(str(library))
    win = MainWindow()
    try:
        def run_scan():
            got = []
            worker = ScanWorker(db.list_roots(), db)
            worker.finished.connect(got.append)
            worker.run()
            win._stop_signatures()
            win._on_scan_finished(got[0])
            win._stop_signatures()

        assert not win._missing_action.isVisible()
        run_scan()
        folder = next(win._model.entry_at(r).folder for r in range(win._model.rowCount())
                      if win._model.entry_at(r).folder.name == "Old Title")
        win._set_examined_for_rows([r for r in range(win._model.rowCount())
                                    if win._model.entry_at(r).folder == folder], True)
        mu_cache.save_entry(folder, 5, "Linked", "", None, mu_confirmed=True)
        res = []
        sw = SignatureWorker(db)
        sw.finished.connect(res.append)
        sw.run()
        assert res[0].signed == 3 and db.unsigned_count() == 0

        (library / "Old Title").rename(library / "New Title")
        (library / "Wanted").rename(library / "Wanted 2")
        run_scan()
        assert win._btn_missing.text() == "Missing (1)" and win._missing_action.isVisible()
        new = folder.with_name("New Title")
        row = next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).folder == new)
        assert win._model.entry_at(row).examined and win._model.entry_at(row).mu_id == 5
        assert "renamed / moved series kept their data" in win._status_label.text()

        dlg = win._make_missing_dialog()
        dlg._confirm = lambda p, t: True
        assert dlg.forget_selected()
        win._after_missing_dialog(dlg)
        assert win._btn_missing.text() == "Missing (0)" and not win._missing_action.isVisible()
    finally:
        win.close()
        win.deleteLater()


def test_signatures_run_in_a_background_thread_after_a_scan(qapp, db, library):
    from mangalist.gui.main_window import MainWindow, ScanWorker

    for i in range(4):
        make_archive(library / "Series A" / f"a{i}.cbz", size=1500 + i)
    db.add_root(str(library))
    win = MainWindow()
    try:
        got = []
        worker = ScanWorker(db.list_roots(), db)
        worker.finished.connect(got.append)
        worker.run()
        win._on_scan_finished(got[0])
        assert win._sig_thread is not None and win._sig_label.text().startswith("Signing archives")
        deadline = time.monotonic() + 20
        while win._sig_thread is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert win._sig_thread is None and db.unsigned_count() == 0
        assert win._sig_label.isHidden()
    finally:
        win.close()
        win.deleteLater()


def test_background_carry_after_signing_refreshes_the_table(qapp, db, library):
    """A move recognised only after the fact (when the backfill signed the new copy) shows up in the table."""
    from mangalist.gui.main_window import MainWindow

    make_archive(library / "Series A" / "a.cbz", size=1700)
    db.add_root(str(library))
    scan(db)
    sign(db)
    win = MainWindow()
    try:
        from mangalist.identity.backfill import BackfillResult
        from mangalist.identity.carry import CarryResult

        res = BackfillResult(signed=1, carries=[CarryResult(1, 1, str(library / "X"), str(library / "Y"), "archives",
                                                            moved=["row"])])
        win._on_signatures_finished(res)
        assert "recognised after signing" in win._status_label.text()
    finally:
        win.close()
        win.deleteLater()
