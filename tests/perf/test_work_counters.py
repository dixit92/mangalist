"""Deterministic "work counter" tests on the synthetic library (UI performance stage 1, research note "Recommended tests
and budgets"): instead of timing the UI they count the work an interaction does - state evaluations, ``data()`` calls,
inventories built, database connections, rows and widgets made - so they hold on every CI machine and fail when a change
brings a whole-library cost back onto the UI thread. Offscreen Qt, the fake finding backend, made-up series; nothing on
disk but the empty library folders."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mangalist.gui.download_tab import GROUP_FOUND  # noqa: E402
from mangalist.gui.table_model import COL_TITLE, MangaTableModel  # noqa: E402
from mangalist.states import State  # noqa: E402

from ..gui.conftest import qapp, wait_until  # noqa: E402,F401
from ..gui.finding_fakes import FakeFindingBackend, find_run  # noqa: E402
from ..gui.test_dark_mode import themed  # noqa: E402,F401
from ..gui.test_auto_modes_window import _sync  # noqa: E402
from .fixture_library import N_SERIES, build  # noqa: E402


class _Counting(MangaTableModel):
    """The table model, counting its ``data()`` calls."""

    calls = 0

    def data(self, index, role=0):
        self.calls += 1
        return super().data(index, role)


@pytest.fixture(scope="module")
def warm_entries(qapp):
    return build().entries


@pytest.fixture(scope="module")
def win(qapp, tmp_path_factory):
    """The main window over the synthetic library: 700 series in 7 libraries, 150 findings, the Download tab on. One
    window for the module (building it computes 700 states); the tests leave it as they found it."""
    import importlib
    import pkgutil

    import mangalist.gui as gui
    from mangalist import paths
    from mangalist.gui import background as bg
    from mangalist.gui import main_window as mw

    base = tmp_path_factory.mktemp("work-counters")
    lib = build(base=base / "libraries")
    backend = FakeFindingBackend(found=lib.findings, last_run=find_run(), series_ids=dict(lib.series_ids))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(paths.ENV_DATA_DIR, str(base / "data"))
        patch.setenv("MANGALIST_DOWNLOADS", "1")
        patch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: backend)
        for module in pkgutil.iter_modules(gui.__path__):              # see test_auto_modes_window's make_window
            importlib.import_module(f"mangalist.gui.{module.name}")
        with pytest.MonkeyPatch.context() as runner:
            runner.setattr(bg, "start_call", _sync)
            w = mw.MainWindow()
        ids = {}
        for number, path in lib.roots.items():
            path.mkdir(parents=True)
            ids[number] = w._db.add_root(str(path), f"Library {number}").id
        for e in lib.entries:
            e.root_id = ids[e.root_id]
        w._show_roots()
        w._set_rows(lib.entries)
        w._refresh_derived()
        w._download_tab.reload_found()
        w.test_backend, w.test_lib, w.test_roots = backend, lib, ids
        yield w
        w.close()
        w.deleteLater()


def _widgets() -> int:
    return len(QApplication.allWidgets())


def _all_states(model) -> None:
    for row in range(model.rowCount()):
        model.state_at(row)


# --- the List's filter box ----------------------------------------------------------------------------------------

def test_a_keystroke_reads_three_cheap_columns_and_computes_no_state(qapp, warm_entries):
    from mangalist.gui.main_window import _SortProxy

    model = _Counting(warm_entries)
    proxy = _SortProxy()
    proxy.setSourceModel(model)
    model.calls = 0
    proxy.set_text_filter("amber")
    assert 0 < proxy.rowCount() < N_SERIES
    assert model.calls <= 3 * model.rowCount() and model.evaluations == 0        # cold states: none computed
    assert all("amber" in proxy.data(proxy.index(r, COL_TITLE)).casefold() for r in range(proxy.rowCount()))
    proxy.set_text_filter("the amber")                                          # the alternative title
    assert proxy.rowCount() > 0 and model.evaluations == 0
    proxy.set_text_filter("chapters")                                           # the Kind
    assert proxy.rowCount() > 0 and model.evaluations == 0
    _all_states(model)
    proxy.set_state_filter("wanted")
    before = model.evaluations
    proxy.set_text_filter("silver")
    assert model.evaluations == before                                          # warm states are kept


def test_typing_filters_once_after_the_last_key(win):
    applied = []
    original = win._proxy.set_text_filter
    win._proxy.set_text_filter = lambda text: applied.append(text) or original(text)
    QTest.keyClicks(win._filter_edit, "amb")
    assert applied == [] and win._proxy.rowCount() == N_SERIES                 # not on every key
    # waits for the debounce itself, not a fixed time (a slow CI runner fired it after the fixed wait)
    wait_until(QApplication.instance(), lambda: bool(applied))
    QTest.qWait(win._FILTER_DELAY_MS)                                           # and nothing more follows
    assert applied == ["amb"] and 0 < win._proxy.rowCount() < N_SERIES
    win._filter_edit.clear()                                                    # clearing applies at once
    assert applied[-1] == "" and win._proxy.rowCount() == N_SERIES


# --- the series' states ------------------------------------------------------------------------------------------

def test_a_library_switch_with_warm_states_computes_nothing_and_makes_no_widget(win):
    model = win._model
    for root in list(win.test_roots.values()) + [None]:                       # every library once: states warm
        win._top.set_library(root, emit=True)
    before, widgets = model.evaluations, _widgets()
    for root in list(win.test_roots.values()) + [None]:
        win._top.set_library(root, emit=True)
        assert model.evaluations == before
    assert _widgets() == widgets


def test_a_rescanned_library_computes_only_its_series(win):
    model = win._model
    _all_states(model)
    root = win.test_roots[3]
    fresh = [e for e in build(base=win.test_lib.roots[1].parent).entries if e.root_id == 3]
    for e in fresh:
        e.root_id = root
    before = model.evaluations
    win._set_rows([e for e in model.entries() if e.root_id != root] + fresh)   # what a scan of library 3 hands over
    win._refresh_derived()
    _all_states(model)
    assert model.evaluations - before == len(fresh)


def test_rename_counts_and_columns_the_state_does_not_read_keep_the_states(qapp, warm_entries):
    model = MangaTableModel(warm_entries)
    _all_states(model)
    before = model.evaluations
    model.set_scope(2)
    model.set_root_names({r: f"Library {r}" for r in range(1, 8)})
    model.set_duplicate_file_counts({str(warm_entries[0].folder): (1, 0)})
    model.set_mode_chips({str(warm_entries[1].folder): "Auto"})
    model.set_mu_processing_row(5)
    model.set_mu_processing_row(None)
    model.set_scope(None)
    _all_states(model)
    assert model.evaluations == before
    model.set_rename_counts({str(e.folder): 2 for e in warm_entries[:3]})       # three series changed: three again
    _all_states(model)
    assert model.evaluations == before + 3 and model.state_at(0).rename_pending
    model.forget_states([warm_entries[10]])                                    # changed in place: computed again
    model.set_entries(list(warm_entries))                                       # a reset keeps the others' states
    _all_states(model)
    assert model.evaluations == before + 4


def test_a_changed_row_is_still_computed_again(qapp, warm_entries):
    model = MangaTableModel(warm_entries[:20])
    _all_states(model)
    row = next(r for r in range(20) if model.state_at(r).gaps)
    was = model.state_at(row).state
    e = model.entry_at(row)
    e.behind_override = "done"
    try:
        model.dataChanged.emit(model.index(row, 13), model.index(row, 13))     # the Behind column
        assert model.state_at(row).state == State.UP_TO_DATE
    finally:
        e.behind_override = None
        model.refresh_states()
    assert model.state_at(row).state == was != State.UP_TO_DATE


def test_the_wanted_list_with_warm_caches_builds_no_inventory_and_opens_no_connection(win, monkeypatch):
    from mangalist import inventory, wanted
    from mangalist.store.db import StoreBase

    win._rebuild_wanted(force=True)
    built, connections = [], []
    original_inventory, original_connect = inventory.inventory_of_entry, StoreBase.connect
    monkeypatch.setattr(inventory, "inventory_of_entry", lambda *a, **k: built.append(1) or original_inventory(*a, **k))

    def connect(self, *a, **k):
        if threading.current_thread() is threading.main_thread():
            connections.append(1)
        return original_connect(self, *a, **k)

    monkeypatch.setattr(StoreBase, "connect", connect)
    worked = []
    original_wanted = wanted.series_wanted
    monkeypatch.setattr(wanted, "series_wanted", lambda *a, **k: worked.append(1) or original_wanted(*a, **k))
    win._rebuild_wanted(force=True)
    assert built == [] and connections == [] and worked == []                 # nothing worked out again
    assert len(win._wanted_series) >= 50                                     # a long list to get


# --- Found ------------------------------------------------------------------------------------------------------

def test_found_is_not_built_while_hidden(win):
    found = win._download_tab.found
    assert found.model.rowCount() == 0 and found.model.inserted == 0           # the List tab is shown
    assert len(found.rows) == 150                                               # built when asked for
    assert len(found.findChildren(QWidget)) < 40                                # no widget per finding


def test_an_unchanged_wanted_list_or_reload_touches_no_found_row(win, monkeypatch):
    tab, backend = win._download_tab, win.test_backend
    tab.show_group(GROUP_FOUND)
    found = tab.found
    assert len(found.rows) == 150
    names = []
    original = backend.series_titles if hasattr(backend, "series_titles") else None
    if original is not None:
        monkeypatch.setattr(backend, "series_titles", lambda ids: names.append(ids) or original(ids))
    before = (found.model.inserted, found.model.removed, found.model.changed, found.model.resets, _widgets())
    for _ in range(3):
        tab.set_wanted(list(win._wanted_series))
        tab.reload_found()
        win._rebuild_wanted(force=True)
    assert len(found.rows) == 150
    assert (found.model.inserted, found.model.removed, found.model.changed, found.model.resets, _widgets()) == before
    assert names == [] or all(not ids for ids in names)


def test_a_theme_switch_does_not_rebuild_found(win, themed, monkeypatch):
    from mangalist.gui import theme

    found = win._download_tab.found
    assert len(found.rows) == 150
    before = (found.model.inserted, found.model.removed, found.model.changed, found.model.resets,
              len(found.list.findChildren(QWidget)))
    styles = []
    original = type(themed).setStyle
    monkeypatch.setattr(type(themed), "setStyle", lambda app, style: styles.append(style) or original(style))
    theme.apply_theme(themed, "dark")
    theme.apply_theme(themed, "light")
    assert (found.model.inserted, found.model.removed, found.model.changed, found.model.resets,
            len(found.list.findChildren(QWidget))) == before
    assert styles == []                                                        # the style is set once, not per switch


# --- the MangaUpdates lookup, saving the layout ---------------------------------------------------------------------

class _FakeWorker:
    made = []

    def __init__(self, pairs):
        from PySide6.QtCore import QObject, Signal

        class _Signals(QObject):
            entry_started = Signal(int)
            entry_updated = Signal(object, int)
            finished = Signal()

        self._s = _Signals()
        self.entry_started, self.entry_updated, self.finished = (self._s.entry_started, self._s.entry_updated,
                                                                 self._s.finished)
        self.pairs = pairs
        _FakeWorker.made.append(self)

    def moveToThread(self, thread):
        pass

    def run(self):
        self.finished.emit()

    def deleteLater(self):
        pass

    def abort(self):
        pass


def test_the_mangaupdates_lookup_asks_mangapixer_off_the_ui_thread(win, monkeypatch):
    from mangalist.gui import main_window as mw

    monkeypatch.setattr(mw, "MuWorker", _FakeWorker)
    _FakeWorker.made = []
    calls = []
    monkeypatch.setattr(win, "_background", lambda fn, ok, fail, done=None: calls.append((fn, ok, fail)))
    entries = win._model.entries()[:50]
    win._mp_items.clear()
    win._start_mu_lookup(entries)
    assert len(calls) == 1 and _FakeWorker.made == []          # nothing resolved on the UI thread, nothing started
    fn, ok, _fail = calls[0]
    known = fn()                                               # what the background thread runs: MangaPixer knows none
    win._start_mu_lookup(entries[:5])                          # a newer start: the older answer is dropped
    ok(known)
    assert _FakeWorker.made == []
    calls[-1][1](calls[-1][0]())
    assert [len(w.pairs) for w in _FakeWorker.made] == [5]
    win._stop_mu()
    win._mp_items.update({str(e.folder): None for e in entries})     # resolved already: no background call
    calls.clear()
    win._start_mu_lookup(entries)
    assert calls == [] and len(_FakeWorker.made[-1].pairs) == 50


def test_a_splitter_drag_is_saved_once_it_stops(win, monkeypatch):
    from mangalist.gui import main_window as mw

    saved = []
    original = mw.config.save_values
    monkeypatch.setattr(mw.config, "save_values", lambda values: saved.append(dict(values)) or original(values))
    for pos in (500, 510, 520, 530):
        win._list.splitter.splitterMoved.emit(pos, 1)
    assert saved == []
    wait_until(QApplication.instance(), lambda: bool(saved))                   # the debounce, however slow the runner
    QTest.qWait(win._SAVE_DELAY_MS)                                             # and no second save follows
    assert saved == [{win._CFG_SPLITTER: win._list.splitter.sizes()}]          # that key alone, once


def test_the_download_tab_reads_the_settings_once(qapp, monkeypatch):
    from mangalist.gui import download_tab as dt

    loads = []
    original = dt.config.load
    monkeypatch.setattr(dt.config, "load", lambda: loads.append(1) or original())
    tab = dt.DownloadTab(FakeFindingBackend(), confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0,
                         autostart=False)
    try:
        assert loads == [1]
    finally:
        tab.stop()
        tab.deleteLater()


# --- UI performance stage 2: states in the background, rows replaced, sort keys --------------------------------------

class _Jobs:
    """A background runner the test drives: each job waits in ``pending`` until ``run_all`` runs it (its function as
    the worker thread would, then its result as the UI thread gets it)."""

    def __init__(self):
        self.pending = []

    def __call__(self, fn, on_done=None, on_error=None, on_finished=None):
        self.pending.append((fn, on_done, on_finished))
        return _Done()

    def run_all(self, between=None):
        while self.pending:
            fn, on_done, on_finished = self.pending.pop(0)
            result = fn()
            if between is not None:
                between()
            if on_done is not None:
                on_done(result)
            if on_finished is not None:
                on_finished()


class _Done:
    def abandon(self):
        pass


@pytest.fixture
def deferred(win, monkeypatch):
    """The window with its states computed "in the background" (the test's runner) - as the app runs."""
    jobs = _Jobs()
    monkeypatch.setattr(win._model, "deferred", True)
    monkeypatch.setattr(win, "_states_runner", jobs)
    yield jobs
    jobs.run_all()
    win._model.deferred = False


def _wanted_has(win, entries) -> bool:
    """Has the To get list been worked out for these very entries (not the folders' previous ones)?"""
    return any(win._wanted_cache.get(id(e), (None,))[0] is e for e in entries)


def _fresh_library(win, number):
    fresh = [e for e in build(base=win.test_lib.roots[1].parent).entries if e.root_id == number]
    for e in fresh:
        e.root_id = win.test_roots[number]
    return fresh


def test_a_rescanned_library_is_computed_off_the_ui_thread_and_pushed_in_one_step(win, deferred, monkeypatch):
    from mangalist import wanted
    from mangalist.gui.table_model import COL_ENGLISH, COL_STATE

    model = win._model
    _all_states(model)
    root = win.test_roots[4]
    fresh = _fresh_library(win, 4)
    before = model.evaluations
    resets, changes = [], []
    model.modelReset.connect(lambda: resets.append(1))
    model.dataChanged.connect(lambda tl, br, roles=(): changes.append((tl.row(), br.row(), tl.column(), br.column())))
    try:
        win._set_rows([e for e in model.entries() if e.root_id != root] + fresh)      # what the library's scan hands over
        win._refresh_derived()
        rows = [r for r in range(model.rowCount()) if model.entry_at(r).root_id == root]
        for r in rows:                              # painting, filtering, sorting and counting compute nothing here
            for col in range(model.columnCount()):
                model.data(model.index(r, col))
        win._proxy.set_state_filter("wanted")
        win._table.sortByColumn(COL_STATE, Qt.DescendingOrder)
        win._update_counts()
        win._top.set_library(root, emit=True)
        assert model.evaluations == before and resets == []
        assert model.states_pending(rows) and not _wanted_has(win, fresh)
        QApplication.processEvents()                # the model asked (queued): one computation for the library
        assert len(deferred.pending) == 1
        worked = []
        original = wanted.series_wanted
        monkeypatch.setattr(wanted, "series_wanted", lambda *a, **k: worked.append(1) or original(*a, **k))
        changes.clear()

        def ui_side():                              # from here on the UI thread: no To get entry is worked out here
            worked.clear()

        deferred.run_all(between=ui_side)
        assert worked == [] and model.evaluations == before
        pushes = [c for c in changes if c[2] <= COL_STATE <= c[3]]
        assert pushes == [(rows[0], rows[-1], COL_TITLE, COL_ENGLISH)]           # one dataChanged over the library
        assert not model.states_pending() and _wanted_has(win, fresh)
        check = MangaTableModel(fresh)                                          # the same states as computed at once
        check.set_state_providers(knowledge_for=model._knowledge_for, inventory_for=model._inventory_for,
                                  needs_kind_for=model._needs_kind_for, unbundled_for=model._unbundled_for)
        for e in fresh:
            got = model.ready_state_of(e)
            assert got == check.state_at(check.entries().index(e))
    finally:
        win._proxy.set_state_filter(None)
        win._table.sortByColumn(COL_TITLE, Qt.AscendingOrder)
        win._top.set_library(None, emit=True)


def test_a_changed_series_keeps_showing_its_state_until_the_new_one_is_pushed(win, deferred):
    model = win._model
    _all_states(model)
    row = next(r for r in range(model.rowCount()) if model.state_at(r).gaps)
    e = model.entry_at(row)
    was = model.state_at(row)
    before = model.evaluations
    e.behind_override = "done"
    try:
        model.dataChanged.emit(model.index(row, 13), model.index(row, 13))     # the Behind column
        assert model.shown_state_at(row) is was and model.evaluations == before    # shown until it is computed
        QApplication.processEvents()
        deferred.run_all()
        assert model.shown_state_at(row).state == State.UP_TO_DATE and model.evaluations == before
    finally:
        e.behind_override = None
        model.forget_states([e])
        QApplication.processEvents()
        deferred.run_all()
    assert model.state_at(row).state == was.state


def test_a_result_computed_from_old_inputs_is_dropped(qapp, warm_entries):
    model = MangaTableModel(warm_entries[:10])
    model.deferred = True
    request = model.take_states_request()
    assert len(request) == 10 and model.take_states_request() == []            # in flight: not asked twice
    computed = model.compute_states(request)
    model.set_rename_counts({str(warm_entries[2].folder): 3})                  # changed while it was computed
    rows = model.push_states(computed)
    assert 2 not in rows and model.ready_state_at(2) is None and len(rows) == 9
    again = model.take_states_request()
    assert [e for e, _g, _n in again] == [warm_entries[2]]
    model.push_states(model.compute_states(again))
    assert model.ready_state_at(2).rename_pending and model.evaluations == 0


def test_a_librarys_rescan_replaces_its_rows_only(qapp, warm_entries):
    from mangalist.gui.main_window import _SortProxy

    shown = sorted(warm_entries, key=lambda e: e.root_id)          # the window's rows: library by library
    model = MangaTableModel(shown)
    proxy = _SortProxy()
    proxy.setSourceModel(model)
    proxy.sort(COL_TITLE)
    _all_states(model)
    events = []
    model.modelReset.connect(lambda: events.append("reset"))
    model.rowsRemoved.connect(lambda _p, first, last: events.append(("removed", first, last)))
    model.rowsInserted.connect(lambda _p, first, last: events.append(("inserted", first, last)))
    rows = [r for r, e in enumerate(shown) if e.root_id == 5]
    fresh = [e for e in build().entries if e.root_id == 5]
    keys = []
    original = MangaTableModel._sort_value
    model._sort_value = lambda row, e, col: keys.append(row) or original(model, row, e, col)
    before = model.evaluations
    model.set_entries(shown[:rows[0]] + fresh + shown[rows[-1] + 1:])
    assert events == [("removed", rows[0], rows[-1]), ("inserted", rows[0], rows[0] + len(fresh) - 1)]
    assert len(keys) <= len(fresh)                  # the other libraries' rows keep their sort keys
    assert proxy.rowCount() == len(shown) - len(rows) + len(fresh)
    titles = [proxy.data(proxy.index(r, COL_TITLE), Qt.UserRole) for r in range(proxy.rowCount())]
    assert titles == sorted(titles)
    _all_states(model)
    assert model.evaluations - before == len(fresh)                            # the others kept their states


def test_sorting_reads_each_rows_key_once(qapp, warm_entries):
    from mangalist.gui.main_window import _SortProxy
    from mangalist.gui.table_model import COL_STATE

    model = _Counting(warm_entries)
    proxy = _SortProxy()
    proxy.setSourceModel(model)
    _all_states(model)
    model.calls = 0
    proxy.sort(COL_STATE, Qt.DescendingOrder)
    proxy.sort(COL_TITLE)
    first = model.calls
    proxy.sort(COL_STATE)
    proxy.sort(COL_TITLE, Qt.DescendingOrder)
    assert first == 0 and model.calls == 0          # data() is not asked per comparison: the keys are kept per row


def test_the_ui_thread_keeps_one_store_connection(tmp_path, monkeypatch):
    from mangalist.store import Store

    db = Store(tmp_path / "x.db", import_legacy=False)
    db.ensure_initialized()
    opened = []
    original = Store._raw_connect
    monkeypatch.setattr(Store, "_raw_connect", lambda self: opened.append(threading.current_thread().name)
                        or original(self))
    db.list_roots()
    db.list_roots()
    assert len(opened) == 2                         # not asked for: one connection per call, as before
    db.keep_ui_connection = True
    opened.clear()
    for _ in range(5):
        db.list_roots()
        db.set_setting("k", 1)
    assert len(opened) == 1 and db.get_setting("k") == 1
    with db.connect() as con:                       # a call inside another opens its own (the outer's transaction)
        db.list_roots()
        con.execute("SELECT 1")
    assert len(opened) == 2
    worker = threading.Thread(target=db.list_roots)
    worker.start()
    worker.join()
    assert len(opened) == 3                         # another thread: its own short connection
    db.release_ui_connection()
    db.list_roots()
    assert len(opened) == 4
    db.release_ui_connection()


def test_settings_build_the_shown_page_only(qapp, tmp_path, monkeypatch):
    from mangalist import store
    from mangalist.gui import settings_dialog as sd

    store.reset_stores()
    dlg = sd.SettingsDialog(None, store.get_store(), None)
    try:
        assert [type(p).__name__ for p in dlg.pages.built()] == ["LibraryPage"]
        dlg.show_section(sd.SECTION_LOGGING)
        assert len(dlg.pages.built()) == 2 and dlg.stack.currentIndex() == sd.DIALOG_SECTIONS.index(sd.SECTION_LOGGING)
        assert dlg.pages[sd.SECTION_MATCHING] is dlg.pages[sd.SECTION_MATCHING]
        assert set(dlg.pages) == set(sd.DIALOG_SECTIONS) and len(dlg.pages.built()) == 3
    finally:
        dlg.reject()
        dlg.deleteLater()
