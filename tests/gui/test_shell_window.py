"""The shell's seams (offscreen Qt, fakes for lanes B and C, made-up series): the top bar, lane B's Download tab and
Settings, lane C's duplicates view (and the Duplicates chip without it), the collapsible details panel, the start
without an empty table, and a scan stopped when the window closes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mangalist import config  # noqa: E402
from mangalist.gui import lanes  # noqa: E402
from mangalist.gui.shell import DuplicateSeries, SettingsResult  # noqa: E402
from mangalist.knowledge import from_mangapixer_item  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402
from mangalist.states import InventorySnapshot  # noqa: E402

from ..states.helpers import TODAY, item  # noqa: E402
from .conftest import FakeBackend, qapp, wait_until  # noqa: E402,F401

LIB = Path("/lib")
QUEST, TWIN_A, TWIN_B, PLAIN = "Example Quest", "Twin Series", "Twin Series (old)", "Plain Series"


def _entry(title, mu=None):
    folder = LIB / title
    e = MangaEntry(folder=folder, title=title, english_title=None,
                   files=[FileHit(folder / f"{title} v01.cbz", 1000, 0, has_volume=True)])
    if mu:
        e.mu_id, e.mu_title, e.mu_band = 1, mu, "auto"
    return e


class FakeDownloadTab(QWidget):
    """Lane B's interface (gui/shell.py)."""

    count_changed = Signal(int)
    show_in_list = Signal(str)

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.wanted = []
        self.focused = []
        self.stopped = False

    def set_wanted(self, series):
        self.wanted = list(series)
        self.count_changed.emit(len(self.wanted))

    def focus(self, folder):
        self.focused.append(folder)

    def stop(self):
        self.stopped = True


class FakeDuplicatesView(QWidget):
    """Lane C's interface (gui/shell.py)."""

    show_in_list = Signal(str)
    files_deleted = Signal(list)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.groups = None
        self.refreshed = 0
        self.blocked = []

    def set_blocked(self, reason):
        self.blocked.append(reason)

    def set_series_duplicates(self, groups):
        self.groups = list(groups)

    def refresh(self):
        self.refreshed += 1


@pytest.fixture
def make_window(qapp, monkeypatch):
    from mangalist import store
    from mangalist.gui import main_window as mw

    made = []

    def make(*, downloads=False, tab=None, dupes=None, settings=None, finder=None, entries=True):
        tab = tab or FakeDownloadTab
        dupes = dupes or FakeDuplicatesView
        settings = settings or (lambda parent, db, backend, section=None: None)
        finder = finder or (lambda db: [])
        store.reset_stores()
        backend = FakeBackend(series_ids={str(LIB / t): i for i, t in enumerate((QUEST, TWIN_A, TWIN_B, PLAIN), 1)})
        monkeypatch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: backend if downloads else None)
        monkeypatch.setattr(lanes, "download_tab_class", lambda: tab)
        monkeypatch.setattr(lanes, "duplicates_view_class", lambda: dupes)
        monkeypatch.setattr(lanes, "open_settings_function", lambda: settings)
        monkeypatch.setattr(lanes, "find_duplicate_files_function", lambda: finder)
        win = mw.MainWindow()
        made.append(win)
        if entries:
            win._model.set_state_providers(
                knowledge_for=lambda e: from_mangapixer_item(item()) if e.title == QUEST else None,
                inventory_for=lambda e: InventorySnapshot(held_volumes=["1"]), needs_kind_for=lambda e: False,
                today=TODAY)
            win._on_scan_finished([_entry(QUEST), _entry(TWIN_A, mu="Twin"), _entry(TWIN_B, mu="twin "),
                                   _entry(PLAIN)])
            win._stop_signatures()
            win._refresh_derived()
        return win

    yield make
    for win in made:
        win.close()
        win.deleteLater()


def _row(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def _selected(win):
    return [win._model.entry_at(win._proxy.mapToSource(i).row()).title
            for i in win._table.selectionModel().selectedRows()]


def test_top_bar_without_downloads(make_window):
    win = make_window()
    top = win._top
    assert not top.tab_download.isVisibleTo(win) and top.tab_list.isChecked()
    assert top.status.text().startswith("No library folder · scanned ")
    assert not win._btn_missing.isVisibleTo(win) and not win._missing_action.isVisible()
    assert win._list.counts_label.text() == "4 series"
    assert win._list.kinds_label.text() == "0 volume folders · 0 chapter folders · 0 both · 4 unknown"


def test_lane_bs_download_tab_gets_the_list_and_the_badge(make_window):
    win = make_window(downloads=True, tab=FakeDownloadTab)
    tab = win._download_tab
    assert isinstance(tab, FakeDownloadTab) and tab.backend is win._volumes_backend
    assert win._top.tab_download.isVisibleTo(win)
    assert [(w.title, w.group, w.findable) for w in tab.wanted] == [(QUEST, "volumes", True)]
    assert win._top.tab_download.badge == 1
    sent = tab.wanted
    win._model.refresh_states()                                      # nothing changed: not sent again
    win._refresh_derived()
    assert tab.wanted is sent
    win._select_source_row(_row(win, QUEST))
    assert win._detail.btn_get.isVisibleTo(win._detail) and win._detail.btn_get.text() == "Get the missing volumes"
    win._detail.btn_get.click()
    assert tab.focused == [str(LIB / QUEST)] and win._pages.currentWidget() is tab and win._top.current() == 1
    win._select_source_row(_row(win, PLAIN))                         # (from the List tab) nothing missing: no button
    assert not win._detail.btn_get.isVisibleTo(win._detail)
    tab.show_in_list.emit(str(LIB / QUEST))
    assert win._top.current() == 0 and _selected(win) == [QUEST]
    win.close()
    assert tab.stopped


def test_lane_bs_settings_rescan_and_reread(make_window, monkeypatch):
    calls = []

    def open_settings(parent, db, backend, section=None):
        calls.append((parent, db, backend, section))
        cfg = config.load()
        cfg["mu_autostart"] = True                  # the dialog saved a setting this window also keeps
        config.save(cfg)
        return SettingsResult(roots_changed=True, mangapixer_changed=True)

    win = make_window(settings=open_settings)
    scans, menus = [], []
    win._start_scan = lambda roots=None: scans.append(roots)
    monkeypatch.setattr(win, "_roots", lambda: [SimpleNamespace(name="Library", path="/lib")])
    win._exec_menu = lambda menu, pos: menus.append(menu)
    win._mp_items = {"x": None}
    win._top.btn_settings.click()
    assert calls == [(win, win._db, None, None)] and menus == []        # the dialog, never a menu
    assert scans == [None] and win._mp_items == {}
    assert win._cfg["mu_autostart"] is True
    win._persist_examined()                         # a later save of the window's settings keeps the dialog's
    assert config.load()["mu_autostart"] is True


def test_lane_cs_view_takes_the_tables_place(make_window, qapp):
    win = make_window(dupes=FakeDuplicatesView, finder=lambda db: ["group 1", "group 2"])
    view = win._duplicates_view
    assert isinstance(view, FakeDuplicatesView) and view.db is win._db
    wait_until(qapp, lambda: win._list.chips["duplicates"].count == 3)  # 1 series + 2 files' numbers (lane C)
    win._list.chips["duplicates"].click()
    assert win._list.stack.currentIndex() == 1 and view.refreshed == 1
    assert view.groups == [DuplicateSeries(title="Twin", folders=(str(LIB / TWIN_A), str(LIB / TWIN_B)))]
    view.show_in_list.emit(str(LIB / TWIN_B))
    assert win._list.current_filter() is None and win._list.stack.currentIndex() == 0 and _selected(win) == [TWIN_B]
    scans = []
    win._start_scan = lambda roots=None: scans.append(roots)
    view.files_deleted.emit(["/lib/Twin Series/Twin v01 (copy).cbz"])
    assert scans == [None] and "1 duplicate file(s) deleted" in win._status_label.text()


class FocusingDuplicatesView(FakeDuplicatesView):
    """Lane C's view with its 2026.10.6 extras: one series at a time, MangaPixer links."""

    def __init__(self, db, parent=None):
        super().__init__(db, parent)
        self.focus = []
        self.link = None

    def focus_series(self, folder):
        self.focus.append(folder)

    def set_series_link(self, link):
        self.link = link


def _group(folder, kind):
    from mangalist.gui.shell import DuplicateFile, DuplicateGroup

    f = DuplicateFile(path=str(Path(folder) / "x.cbz"), size=1, modified="2026-01-01T00:00:00.000000+00:00", group=None)
    return DuplicateGroup(series_id=1, folder=str(folder), title=Path(folder).name, kind=kind, number="1", files=(f, f))


def test_the_duplicates_column_counts_each_series_and_review_opens_on_it(make_window, qapp):
    from mangalist.gui.table_model import COL_DUPE, column_label

    quest = LIB / QUEST
    groups = [_group(quest, "volume"), _group(quest, "chapter"), _group(quest, "chapter")]
    win = make_window(dupes=FocusingDuplicatesView, finder=lambda db: groups)
    wait_until(qapp, lambda: win._dupe_file_groups == 3)
    model = win._model
    text = {model.entry_at(r).title: model.data(model.index(r, COL_DUPE)) for r in range(model.rowCount())}
    assert text[QUEST] == "1 vol. · 2 ch." and text[PLAIN] == ""
    assert text[TWIN_A] == "+1 folder"                                    # the same series in another folder
    assert "held by more than one file" in model.data(model.index(_row(win, QUEST), COL_DUPE), Qt.ToolTipRole)
    assert column_label(COL_DUPE) == "Duplicates" and column_label(0, menu=True) == "Examined"
    view = win._duplicates_view
    assert view.link == win.mangapixer_series_url
    win.review_duplicates(str(quest))
    assert win._list.current_filter() == "duplicates" and view.focus == [str(quest)]
    win._list.set_filter(None, emit=True)
    win._list.chips["duplicates"].click()                                 # the chip itself: every series again
    assert view.focus == [str(quest), None]


def test_the_mangapixer_link_is_the_series_node_on_the_connected_server(make_window, monkeypatch):
    from mangalist.services.mangapixer import open_cache

    win = make_window()
    open_cache(win._db).set_connection(base_url="mangapixer.example:8080")
    monkeypatch.setattr(win, "_mp_item_for", lambda e: {"nodeId": "a1b2c3"} if e.title == QUEST else None)
    assert win.mangapixer_series_url(str(LIB / QUEST)) == "http://mangapixer.example:8080/series/a1b2c3"
    assert win.mangapixer_series_url(str(LIB / PLAIN)) is None            # MangaPixer does not know it
    assert win.mangapixer_series_url("/elsewhere") is None


def test_links_without_a_browser_are_copied(make_window, monkeypatch):
    from mangalist.gui import links

    monkeypatch.setattr(links.QDesktopServices, "openUrl", staticmethod(lambda url: False))
    win = make_window()
    win._open_url("https://www.mangaupdates.com/series/abc")
    assert QApplication.clipboard().text() == "https://www.mangaupdates.com/series/abc"
    assert "link copied" in win._status_label.text()


def test_details_panel_collapses_and_is_remembered(make_window):
    win = make_window()
    lst = win._list
    win.show()
    assert lst.details_visible() and not lst.btn_details.isVisible()
    lst.details.btn_close.click()
    assert not lst.details_visible() and lst.btn_details.isVisible()
    assert config.load()["details_panel"] is False
    again = make_window(entries=False)
    assert not again._list.details_visible()
    lst.btn_details.click()
    assert lst.details_visible() and config.load()["details_panel"] is True


def test_start_without_a_library_shows_how_to_add_one(make_window):
    win = make_window(entries=False)
    assert not win.start_initial_scan()
    lst = win._list
    assert lst.stack.currentIndex() == 2 and lst.empty_title.text() == "No library folder yet"
    added = []
    win._on_choose_root = lambda: added.append(1)
    lst.btn_add_root.click()
    assert added == [1]


def test_start_scans_the_library_in_the_background(make_window, qapp, tmp_path):
    lib = tmp_path / "Library"
    for name in ("Series A", "Series B"):
        (lib / name).mkdir(parents=True)
        (lib / name / f"{name} v01.cbz").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    win = make_window(entries=False)
    win._db.add_root(str(lib))
    win._start_signatures = lambda: None
    assert win.start_initial_scan()
    assert win._list.stack.currentIndex() == 2 and win._list.empty_title.text() == "Scanning your library…"
    assert "scanning…" in win._top.status.text() and not win._btn_rescan.isEnabled()
    wait_until(qapp, lambda: win._thread is None, timeout=30)
    assert win._model.rowCount() == 2 and win._list.stack.currentIndex() == 0
    assert win._duplicates_view.blocked == ["the library is being rescanned", None]   # its busy state follows the scan
    assert win._top.status.text().startswith("Library · scanned ") and win._btn_rescan.isEnabled()


def test_a_scan_stopped_on_close_records_nothing(qapp, monkeypatch):
    from mangalist.gui import main_window as mw

    seen, shown = [], []

    def fake_scan(roots, db=None, *, progress=None, on_start=None, on_root=None):
        on_start(1, 1, "Library")
        for i, name in enumerate(("Series A", "Series B", "Series C"), 1):
            progress(i, 3, name)
            seen.append(name)
            if i == 1:
                worker.stop()                   # the window closes while the first folder is read
        raise AssertionError("not reached")

    monkeypatch.setattr(mw, "scan_and_record_library", fake_scan)
    worker = mw.ScanWorker(["a root"], db=object())
    got = []
    for signal in (worker.finished, worker.failed, worker.root_scanned):
        signal.connect(lambda *a: got.append(a))
    worker.root_started.connect(lambda *a: shown.append(a))
    worker.run()
    assert seen == ["Series A"] and got == [] and shown == [(1, 1, "Library")]


def test_mu_lookup_button_turns_into_stop_while_running(make_window):
    win = make_window()
    lst = win._list
    lst.set_mu_running(True)
    assert lst.btn_mu_start.isHidden() and not lst.btn_mu_stop.isHidden() and lst.btn_mu_stop.isEnabled()
    win._on_mu_stop()
    assert not lst.btn_mu_start.isHidden() and lst.btn_mu_stop.isHidden()


def test_missing_series_button_appears_with_missing_series(make_window, monkeypatch):
    from mangalist.identity import carry

    win = make_window()
    monkeypatch.setattr(carry, "missing_count", lambda db: 2)
    assert win._update_missing_count() == 2
    assert win._btn_missing.text() == "Missing (2)" and win._missing_action.isVisible()
    assert not win._btn_missing.isHidden()


class ReplacingDownloadTab(FakeDownloadTab):
    """Lane B's tab with the volumes cycle's replaced-chapters signal (lane A)."""

    library_changed = Signal(list)


def test_restore_move_or_delete_of_replaced_chapters_rescans(make_window):
    win = make_window(downloads=True, tab=ReplacingDownloadTab)
    scans = []
    win._start_scan = lambda roots=None: scans.append(roots)
    win._download_tab.library_changed.emit(["/lib/Series A"])
    assert scans == [None] and "Chapter files changed in 1 series - rescanning" in win._status_label.text()
