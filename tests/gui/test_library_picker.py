"""The Library picker, rows per root as they are scanned, and the one-root rescan (offscreen Qt, a real database with
real folders of made-up series). Nothing here depends on thread timing: the scan worker is driven on the test's own
thread, so a root's rows are looked at exactly between one root and the next."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mangalist import config  # noqa: E402
from mangalist.gui import lanes  # noqa: E402
from mangalist.gui.main_window import _count_by_folder  # noqa: E402
from mangalist.gui.table_model import COL_LIBRARY  # noqa: E402
from mangalist.identity.backfill import backfill_signatures  # noqa: E402
from mangalist.knowledge import from_mangapixer_item  # noqa: E402
from mangalist.scanner import scan_one_root  # noqa: E402
from mangalist.states import InventorySnapshot  # noqa: E402

from ..identity.conftest import make_archive as unique_archive  # noqa: E402
from ..states.helpers import TODAY, item  # noqa: E402
from .conftest import FakeBackend, qapp, wait_until  # noqa: E402,F401
from .test_shell_window import FakeDownloadTab, FakeDuplicatesView  # noqa: E402

MANGA, MANHWA, COMICS = "Manga", "Manhwa", "Comics"
GAPPY = {"Example Quest", "Korean Quest"}          # series with gaps (volumes 2.. are missing)


def _series(root: Path, name: str, volumes: int = 1) -> None:
    for v in range(1, volumes + 1):
        unique_archive(root / name / f"{name} v{v:02}.cbz", seed=f"{root.name}/{name}/{v}", size=4000 + v)


class Lib:
    """Library folders on disk, a window over them, and the helpers the tests share."""

    def __init__(self, tmp_path, make_window):
        self.tmp = tmp_path
        self.dirs = {name: tmp_path / "libs" / name for name in (MANGA, MANHWA, COMICS)}
        for d in self.dirs.values():
            d.mkdir(parents=True)
        self.make_window = make_window

    def root(self, win, name):
        return next(r for r in win._db.list_roots() if r.name == name)

    def titles(self, win):
        return sorted(e.title for e in win._model.entries())

    def shown(self, win):
        return sorted(win._model.entry_at(win._proxy.mapToSource(win._proxy.index(r, 0)).row()).title
                      for r in range(win._proxy.rowCount()))

    def pick(self, win, name):
        """The owner picks *name* ("All libraries" too) in the dropdown."""
        picker = win._top.library_picker
        index = picker.findText(name)
        assert index >= 0, name
        picker.setCurrentIndex(index)
        picker.activated.emit(index)

    def scan_synchronously(self, win, roots=None):
        """What ``_start_scan`` does, with the worker run on this thread: every signal reaches the window in order."""
        from mangalist.gui import main_window as mw

        roots = list(roots) if roots is not None else win._roots()
        win._scan_applied, win._scan_pos = set(), (1, len(roots), roots[0].name)
        win._forget_vanished_roots()
        worker = mw.ScanWorker(roots, win._db)
        worker.root_started.connect(win._on_root_started)
        worker.root_scanned.connect(win._on_root_scanned)
        worker.finished.connect(win._on_scan_finished)
        worker.failed.connect(win._on_scan_failed)
        worker.run()
        win._stop_signatures()
        return worker


@pytest.fixture
def lib(qapp, tmp_path, monkeypatch):
    from mangalist import store
    from mangalist.gui import main_window as mw

    made = []

    def make_window(*, downloads=False, dupes=None, finder=None):
        store.reset_stores()
        backend = FakeBackend()
        monkeypatch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: backend if downloads else None)
        monkeypatch.setattr(lanes, "download_tab_class", lambda: FakeDownloadTab)
        monkeypatch.setattr(lanes, "duplicates_view_class", lambda: dupes or FakeDuplicatesView)
        monkeypatch.setattr(lanes, "open_settings_function", lambda: (lambda parent, db, backend, section=None: None))
        monkeypatch.setattr(lanes, "find_duplicate_files_function", lambda: finder or (lambda db: []))
        win = mw.MainWindow()
        made.append(win)
        win._model.set_state_providers(
            knowledge_for=lambda e: from_mangapixer_item(item()) if e.title in GAPPY else None,
            inventory_for=lambda e: InventorySnapshot(held_volumes=["1"]), needs_kind_for=lambda e: False, today=TODAY)
        return win

    yield Lib(tmp_path, make_window)
    for win in made:
        win.close()
        win.deleteLater()


def _two_libraries(lib, **kw):
    """Manga: Example Quest + Twin Series; Manhwa: Korean Quest + Twin Series (old) - the twins share a MU title."""
    _series(lib.dirs[MANGA], "Example Quest", 2)
    _series(lib.dirs[MANGA], "Twin Series")
    _series(lib.dirs[MANGA], "Twin Series (copy)")
    _series(lib.dirs[MANHWA], "Korean Quest", 2)
    _series(lib.dirs[MANHWA], "Twin Series (old)")
    win = lib.make_window(**kw)
    win._db.add_root(str(lib.dirs[MANGA]), MANGA)
    win._db.add_root(str(lib.dirs[MANHWA]), MANHWA)
    win._show_roots()
    lib.scan_synchronously(win)
    for e in win._model.entries():                      # the same MangaUpdates series in two folders of Manga, one of Manhwa
        if e.title.startswith("Twin Series"):
            e.mu_id, e.mu_title, e.mu_band = 1, "Twin", "auto"
    win._model.set_entries(win._model.entries())
    win._refresh_derived()
    return win


# --- the picker ---------------------------------------------------------------------------------------------------


def test_the_picker_lists_all_libraries_then_each_root_and_hides_itself_with_one(lib):
    win = lib.make_window()
    picker = win._top.library_picker
    assert picker.isHidden() and [picker.itemText(i) for i in range(picker.count())] == ["All libraries"]
    win._db.add_root(str(lib.dirs[MANGA]), MANGA)
    win._show_roots()
    assert picker.isHidden()                                       # one root: nothing to pick
    win._db.add_root(str(lib.dirs[MANHWA]), MANHWA)
    win._db.add_root(str(lib.dirs[COMICS]), COMICS)
    win._show_roots()
    assert not picker.isHidden()
    assert [picker.itemText(i) for i in range(picker.count())] == ["All libraries", MANGA, MANHWA, COMICS]
    assert picker.itemData(2, Qt.ItemDataRole.ToolTipRole) == str(lib.dirs[MANHWA])
    assert picker.itemData(2) == lib.root(win, MANHWA).id and win._top.current_library() is None


def test_picking_a_library_filters_the_table_the_counts_and_the_status(lib):
    win = _two_libraries(lib)
    lst = win._list
    assert lib.shown(win) == ["Example Quest", "Korean Quest", "Twin Series", "Twin Series (copy)", "Twin Series (old)"]
    assert lst.chips[None].count == 5 and lst.counts_label.text() == "5 series"

    lib.pick(win, MANHWA)
    assert lib.shown(win) == ["Korean Quest", "Twin Series (old)"]
    assert win._library == lib.root(win, MANHWA).id and win._model.scope() == win._library
    assert lst.chips[None].count == 2 and lst.counts_label.text() == "2 series"
    assert lst.chips["Missing volumes"].count == 1                  # only Korean Quest has gaps here
    assert lst.chips["Can't tell"].count == 1 and lst.chips["Complete"].count == 0
    assert win._status_label.text() == "Showing all entries (Manhwa)"

    lst.chips["Missing volumes"].click()
    assert lib.shown(win) == ["Korean Quest"] and win._status_label.text() == "Showing 1 series: Missing volumes (Manhwa)"

    lib.pick(win, "All libraries")
    assert lib.shown(win) == ["Example Quest", "Korean Quest"] and lst.chips["Missing volumes"].count == 2
    lst.chips[None].click()
    assert len(lib.shown(win)) == 5 and win._library is None


def test_the_choice_is_remembered_and_comes_back_in_the_next_window(lib):
    win = _two_libraries(lib)
    lib.pick(win, MANHWA)
    assert config.load()["list_library"] == str(lib.dirs[MANHWA])
    again = lib.make_window()
    again._show_roots()
    assert again._top.library_name() == MANHWA and again._library == lib.root(again, MANHWA).id
    assert again._model.scope() == again._library
    lib.pick(again, "All libraries")
    assert config.load()["list_library"] == ""                    # "all" is written too: the store keeps old keys
    third = lib.make_window()
    third._show_roots()
    assert third._library is None and third._top.library_name() == "All libraries"


def test_a_remembered_library_that_is_gone_falls_back_to_all(lib):
    win = _two_libraries(lib)
    lib.pick(win, MANHWA)
    win._db.remove_root(lib.root(win, MANHWA).id)
    win._show_roots()
    assert win._top.library_name() == "All libraries" and win._library is None
    assert win._top.library_picker.isHidden()                      # one root left: no picker, every series shows


def test_the_duplicates_chip_counts_inside_the_picked_library(lib, qapp):
    win = _two_libraries(lib, finder=lambda db: [])
    lst = win._list
    twins = lambda: sorted(len(d.folders) for d in win._model.duplicate_series())  # noqa: E731
    assert twins() == [3] and lst.chips["duplicates"].count == 1    # one series in 3 folders: all libraries
    lib.pick(win, MANGA)
    assert twins() == [2] and lst.chips["duplicates"].count == 1
    lib.pick(win, MANHWA)
    assert twins() == [] and lst.chips["duplicates"].count == 0     # its only twin lies in another library
    assert win._model.is_duplicate(next(r for r in range(win._model.rowCount())
                                        if win._model.entry_at(r).title == "Twin Series (old)")) is False


def test_the_duplicates_view_gets_only_the_picked_librarys_series(lib):
    class Scoped(FakeDuplicatesView):
        scopes = []

        def set_library_scope(self, root_ids):
            self.scopes.append(root_ids)

    win = _two_libraries(lib, dupes=Scoped)
    win._list.chips["duplicates"].click()
    assert [len(g.folders) for g in win._duplicates_view.groups] == [3]
    lib.pick(win, MANGA)
    assert [len(g.folders) for g in win._duplicates_view.groups] == [2]
    assert win._duplicates_view.scopes[-1] == [lib.root(win, MANGA).id]
    lib.pick(win, "All libraries")
    assert win._duplicates_view.scopes[-1] is None and [len(g.folders) for g in win._duplicates_view.groups] == [3]


def test_the_duplicate_file_count_follows_the_library(lib, qapp):
    manga_quest = str(lib.dirs[MANGA].resolve() / "Example Quest")
    manhwa_quest = str(lib.dirs[MANHWA].resolve() / "Korean Quest")

    class Group:
        def __init__(self, folder, kind):
            self.folder, self.kind = folder, kind

    groups = [Group(manga_quest, "volume"), Group(manga_quest, "chapter"), Group(manhwa_quest, "chapter")]
    win = _two_libraries(lib, finder=lambda db: groups)
    win._on_duplicate_files_counted(_count_by_folder(groups))
    assert win._list.chips["duplicates"].count == 1 + 3
    lib.pick(win, MANHWA)
    assert win._scoped_file_groups() == 1 and win._list.chips["duplicates"].count == 0 + 1
    lib.pick(win, MANGA)
    assert win._scoped_file_groups() == 2 and win._list.chips["duplicates"].count == 1 + 2


def test_the_download_list_follows_the_library(lib):
    win = _two_libraries(lib, downloads=True)
    tab = win._download_tab
    assert sorted(w.title for w in tab.wanted) == ["Example Quest", "Korean Quest"]
    lib.pick(win, MANHWA)
    assert [w.title for w in tab.wanted] == ["Korean Quest"] and win._top.tab_download.badge == 1
    lib.pick(win, MANGA)
    assert [w.title for w in tab.wanted] == ["Example Quest"]
    lib.pick(win, "All libraries")
    assert len(tab.wanted) == 2


def test_a_series_picked_from_elsewhere_switches_to_its_library(lib):
    win = _two_libraries(lib)
    lib.pick(win, MANGA)
    win.show_folder_in_list(str(lib.dirs[MANHWA].resolve() / "Korean Quest"))      # e.g. from the Download tab
    assert win._top.library_name() == MANHWA and win._library == lib.root(win, MANHWA).id
    sel = win._table.selectionModel().selectedRows()
    assert [win._model.entry_at(win._proxy.mapToSource(i).row()).title for i in sel] == ["Korean Quest"]


# --- the Library column ---------------------------------------------------------------------------------------------


def test_the_library_column_is_hidden_until_chosen_and_names_the_root(lib):
    win = _two_libraries(lib)
    header = win._table.horizontalHeader()
    assert header.isSectionHidden(COL_LIBRARY)
    model = win._model
    rows = {model.entry_at(r).title: model.data(model.index(r, COL_LIBRARY)) for r in range(model.rowCount())}
    assert rows["Example Quest"] == MANGA and rows["Korean Quest"] == MANHWA
    assert model.headerData(COL_LIBRARY, Qt.Orientation.Horizontal) == "Library"
    win._toggle_column(COL_LIBRARY)
    assert not header.isSectionHidden(COL_LIBRARY) and "Library" not in config.load()["list_hidden_columns"]
    assert config.load()["list_columns_version"] == 2
    asc = [model.data(model.index(r, COL_LIBRARY), Qt.ItemDataRole.UserRole) for r in range(model.rowCount())]
    assert set(asc) == {"manga", "manhwa"}


def test_a_hidden_list_saved_before_the_column_existed_keeps_it_hidden(lib):
    cfg = config.load()
    cfg["list_hidden_columns"] = ["Subfolders", "Vol %"]           # the old build's list: no "Library" in it
    config.save(cfg)
    win = lib.make_window()
    assert "Library" in win._hidden_columns() and win._table.horizontalHeader().isSectionHidden(COL_LIBRARY)
    win._toggle_column(COL_LIBRARY)                                # chosen: from now on the list is complete
    again = lib.make_window()
    assert not again._table.horizontalHeader().isSectionHidden(COL_LIBRARY)


# --- rows per root as they are scanned ------------------------------------------------------------------------------


def test_each_roots_rows_appear_before_the_next_root_is_read(lib):
    _series(lib.dirs[MANGA], "Example Quest")
    _series(lib.dirs[MANHWA], "Korean Quest")
    _series(lib.dirs[COMICS], "Comic Series")
    win = lib.make_window()
    for name in (MANGA, MANHWA, COMICS):
        win._db.add_root(str(lib.dirs[name]), name)
    win._show_roots()
    seen = []

    from mangalist.gui import main_window as mw

    worker = mw.ScanWorker(win._roots(), win._db)
    win._scan_applied = set()
    worker.root_started.connect(win._on_root_started)
    worker.root_scanned.connect(win._on_root_scanned)
    # connected after the window's slots: it sees the table right after each step
    worker.root_started.connect(lambda n, c, name: seen.append(("start", name, lib.titles(win))))
    worker.root_scanned.connect(lambda rs, renamed: seen.append(("done", rs.root_name, lib.titles(win))))
    worker.finished.connect(win._on_scan_finished)
    worker.run()
    win._stop_signatures()
    assert seen == [
        ("start", MANGA, []), ("done", MANGA, ["Example Quest"]),
        ("start", MANHWA, ["Example Quest"]), ("done", MANHWA, ["Example Quest", "Korean Quest"]),
        ("start", COMICS, ["Example Quest", "Korean Quest"]), ("done", COMICS, ["Comic Series", "Example Quest", "Korean Quest"]),
    ]
    assert win._status_label.text().startswith("Scanned 3 folder(s)") and win._last_scan is not None


def test_the_database_has_each_root_when_its_rows_are_shown(lib):
    _series(lib.dirs[MANGA], "Example Quest")
    _series(lib.dirs[MANHWA], "Korean Quest")
    win = lib.make_window()
    for name in (MANGA, MANHWA):
        win._db.add_root(str(lib.dirs[name]), name)
    win._show_roots()
    from mangalist.gui import main_window as mw

    worker = mw.ScanWorker(win._roots(), win._db)
    recorded = []
    worker.root_scanned.connect(lambda rs, renamed: recorded.append(
        (rs.root_name, {r.name: [s.rel_path for s in win._db.list_series(r.id)] for r in win._db.list_roots()})))
    worker.run()
    assert recorded == [(MANGA, {MANGA: ["Example Quest"], MANHWA: []}),
                        (MANHWA, {MANGA: ["Example Quest"], MANHWA: ["Korean Quest"]})]


def test_the_top_bar_names_the_root_being_read(lib):
    win = _two_libraries(lib)
    win._thread = object()                                         # a scan is "running" (this test drives the slots)
    try:
        win._on_root_started(1, 2, MANGA)
        assert win._top.status.text() == "Scanning Manga (1 of 2)…"
        assert win._status_label.text() == "Scanning Manga…"
        win._on_progress(3, 8, "Manga: Example Quest")
        assert win._status_label.text() == "Scanning (3/8): Manga: Example Quest"
        win._on_root_started(2, 2, MANHWA)
        assert win._top.status.text() == "Scanning Manhwa (2 of 2)…"
        assert win._progress.maximum() == 0                        # busy again until the next root's first folder
        win._scan_pos = (1, 1, MANHWA)                             # a one-root rescan
        win._show_roots()
        assert win._top.status.text() == "Scanning Manhwa…"
        win._on_root_scanned(scan_one_root(lib.root(win, MANHWA)), [])
        assert win._status_label.text() == "Manhwa: 2 series"
    finally:
        win._thread = None
    win._show_roots()
    assert "scanning" not in win._top.status.text() and "scanned " in win._top.status.text()


def test_one_root_shows_no_root_name_while_scanning(lib):
    _series(lib.dirs[MANGA], "Example Quest")
    win = lib.make_window()
    win._db.add_root(str(lib.dirs[MANGA]), MANGA)
    win._thread = object()
    try:
        win._scan_pos = (1, 1, MANGA)
        win._show_roots()
        assert win._top.status.text() == "Manga · scanning…"
    finally:
        win._thread = None


def test_a_root_replaces_only_its_own_rows_and_keeps_the_selection(lib):
    win = _two_libraries(lib)
    win.show_folder_in_list(str(lib.dirs[MANGA].resolve() / "Example Quest"))
    scroll = win._table.verticalScrollBar()
    before = lib.titles(win)
    _series(lib.dirs[MANHWA], "Brand New Series")
    (lib.dirs[MANHWA] / "Twin Series (old)" / "Twin Series (old) v01.cbz").unlink()
    (lib.dirs[MANHWA] / "Twin Series (old)").rmdir()
    win._scan_applied = set()
    rs = scan_one_root(lib.root(win, MANHWA))
    win._on_root_scanned(rs, [])
    assert lib.titles(win) == sorted(set(before) - {"Twin Series (old)"} | {"Brand New Series"})
    assert [e.title for e in win._model.entries() if e.root_id == lib.root(win, MANGA).id] == \
        ["Example Quest", "Twin Series", "Twin Series (copy)"]                  # the other root: the same entries
    sel = win._table.selectionModel().selectedRows()
    assert [win._model.entry_at(win._proxy.mapToSource(i).row()).title for i in sel] == ["Example Quest"]
    assert scroll.value() == 0


def test_an_unreadable_root_keeps_its_old_rows_until_the_scan_ends(lib):
    win = _two_libraries(lib)
    manhwa = lib.root(win, MANHWA)
    for f in list(lib.dirs[MANHWA].rglob("*.cbz")):
        f.unlink()
    for d in sorted(lib.dirs[MANHWA].glob("*")):
        d.rmdir()
    lib.dirs[MANHWA].rmdir()                                       # the share went away
    rs = scan_one_root(manhwa)
    assert rs.error
    win._scan_applied = set()
    win._on_root_scanned(rs, [])
    assert "Korean Quest" in lib.titles(win)                       # not dropped while the scan is still going
    assert win._status_label.text() == "Manhwa: not reachable"
    from mangalist.scanner import LibraryScan

    win._on_scan_finished(LibraryScan(roots=[rs]))
    assert "Korean Quest" not in lib.titles(win) and "Example Quest" in lib.titles(win)
    assert "1 root(s) not reachable" in win._status_label.text()


def test_roots_removed_in_settings_leave_the_table_when_the_next_scan_starts(lib):
    win = _two_libraries(lib)
    win._db.remove_root(lib.root(win, MANHWA).id)
    assert "Korean Quest" in lib.titles(win)                       # until a scan starts
    win._forget_vanished_roots()
    assert lib.titles(win) == ["Example Quest", "Twin Series", "Twin Series (copy)"]


def test_the_end_of_the_scan_does_not_put_the_rows_in_a_second_time(lib):
    win = _two_libraries(lib)
    shown = win._model.entries()
    from mangalist.scanner import scan_and_record_library

    result = scan_and_record_library(win._roots(), win._db)        # fresh entries, as if the worker had read them
    win._scan_applied = {r.root_id for r in result.roots}          # ... and the window already showed these roots
    win._on_scan_finished(result)
    assert all(a is b for a, b in zip(win._model.entries(), shown)) and len(win._model.entries()) == len(shown)
    assert win._scan_applied == set()                              # used up: a finish with no start in between still works

    win._scan_applied = set()                                      # a finish for roots never shown puts them in
    win._on_scan_finished(result)
    assert all(a is not b for a, b in zip(win._model.entries(), shown))
    assert lib.titles(win) == sorted(e.title for e in shown)


# --- the Rescan menu and a one-root rescan --------------------------------------------------------------------------


def test_the_rescan_arrow_lists_all_libraries_then_each_root(lib):
    win = lib.make_window()
    top = win._top
    assert top.btn_rescan_menu.isHidden()
    win._db.add_root(str(lib.dirs[MANGA]), MANGA)
    win._show_roots()
    assert top.btn_rescan_menu.isHidden()                          # one root: nothing to choose
    win._db.add_root(str(lib.dirs[MANHWA]), MANHWA)
    win._db.add_root(str(lib.dirs[COMICS]), COMICS)
    win._show_roots()
    assert not top.btn_rescan_menu.isHidden()
    acts = [a for a in top.rescan_menu.actions() if not a.isSeparator()]
    assert [a.text() for a in acts] == ["All libraries", MANGA, MANHWA, COMICS]
    assert acts[2].toolTip() == str(lib.dirs[MANHWA]) and acts[2].data() == lib.root(win, MANHWA).id

    scans = []
    win._start_scan = lambda roots=None: scans.append(None if roots is None else [r.name for r in roots])
    acts[2].trigger()
    acts[0].trigger()
    top.btn_rescan.click()
    assert scans == [[MANHWA], None, None]


def test_the_rescan_buttons_wait_while_a_scan_runs(lib, qapp):
    _series(lib.dirs[MANGA], "Example Quest")
    _series(lib.dirs[MANHWA], "Korean Quest")
    win = lib.make_window()
    win._db.add_root(str(lib.dirs[MANGA]), MANGA)
    win._db.add_root(str(lib.dirs[MANHWA]), MANHWA)
    win._show_roots()
    win._start_signatures = lambda: None
    win._start_scan([lib.root(win, MANHWA)])
    assert not win._top.btn_rescan.isEnabled() and not win._top.btn_rescan_menu.isEnabled()
    wait_until(qapp, lambda: win._thread is None, timeout=30)
    assert win._top.btn_rescan.isEnabled() and win._top.btn_rescan_menu.isEnabled()
    assert lib.titles(win) == ["Korean Quest"]                     # only the rescanned root was read
    assert win._status_label.text() == "Scanned 1 folder(s) in Manhwa"
    assert [s.rel_path for s in win._db.list_series(lib.root(win, MANGA).id)] == []     # and recorded


def test_a_one_root_rescan_reads_and_records_only_that_root(lib, qapp):
    win = _two_libraries(lib)
    manga, manhwa = lib.root(win, MANGA), lib.root(win, MANHWA)
    stale = {a.rel_path: a.status for a in win._db.list_archives(root_id=manga.id)}
    (lib.dirs[MANGA] / "Example Quest" / "Example Quest v01.cbz").unlink()      # gone, but Manga is not rescanned
    _series(lib.dirs[MANHWA], "Korean Quest", 3)                                # a new volume in Manhwa
    win._start_signatures = lambda: None
    win._start_scan([manhwa])
    wait_until(qapp, lambda: win._thread is None, timeout=30)
    assert {a.rel_path: a.status for a in win._db.list_archives(root_id=manga.id)} == stale
    quest = next(e for e in win._model.entries() if e.title == "Korean Quest")
    assert quest.n_files == 3
    assert next(e for e in win._model.entries() if e.title == "Example Quest").n_files == 2      # as of the last full scan
    assert win._status_label.text() == "Scanned 2 folder(s) in Manhwa"

    win._start_scan([manga])
    wait_until(qapp, lambda: win._thread is None, timeout=30)
    assert next(e for e in win._model.entries() if e.title == "Example Quest").n_files == 1
    assert win._db.archive_at(manga.id, "Example Quest/Example Quest v01.cbz").status == "missing"


def test_a_series_moved_to_another_root_keeps_its_data_through_the_windows_scan(lib):
    win = _two_libraries(lib)
    manga, manhwa = lib.root(win, MANGA), lib.root(win, MANHWA)
    old = lib.dirs[MANGA].resolve() / "Example Quest"
    new = lib.dirs[MANHWA].resolve() / "Example Quest"
    win._persist_examined = lambda: None
    win._cfg["examined"] = [str(old)]
    config.save(win._cfg)
    win._db.set_setting("examined", [str(old)])
    series_id = win._db.get_series(manga.id, "Example Quest").id
    backfill_signatures(win._db, per_file_delay=0)
    old.rename(new)                                                # a move between the two library folders
    lib.scan_synchronously(win)
    assert win._db.get_series(manhwa.id, "Example Quest").id == series_id          # carried, not "missing + new"
    assert win._db.get_series(manga.id, "Example Quest") is None
    assert win._update_missing_count() == 0 and not win._top.btn_missing.isVisibleTo(win)
    moved = next(e for e in win._model.entries() if e.title == "Example Quest")
    assert moved.root_id == manhwa.id and moved.examined and str(new) in win._cfg["examined"]
    assert "1 renamed / moved series kept their data" in win._status_label.text()


def test_the_picker_lives_in_the_top_bar_for_both_tabs(lib):
    # owner, 2026-10-09: the Download tab following the List's picker was not clear - one picker, in the top bar
    win = lib.make_window()
    for name in (MANGA, MANHWA):
        win._db.add_root(str(lib.dirs[name]), name)
    win._show_roots()
    assert win._top.picker_shown() and win._top.library_label.text() == "Library"
    assert not hasattr(win._list, "library_picker")
