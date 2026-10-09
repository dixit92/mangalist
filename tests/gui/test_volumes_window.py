"""The downloads inside the main window (offscreen Qt, fake backend): nothing exists without downloads; with them the
Download tab gets every series with gaps ("Find volumes on nyaa..." findable only for a MangaPixer-matched series
licensed in English), the row menu and the details panel lead to it, Settings reaches qBittorrent / Downloads, and a
series' download status shows in the details panel."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.knowledge import from_mangapixer_item, from_own_matcher  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402
from mangalist.states import InventorySnapshot  # noqa: E402

from ..states.helpers import TODAY, item  # noqa: E402
from .conftest import FakeBackend, qapp, record, wait_until  # noqa: E402,F401

QUEST = "Example Quest"        # MangaPixer-matched, holds volume 1: volumes 2-3 are out and missing
REVIEW = "Review Series"       # MangaPixer: needs review
OWN = "Own Matcher Series"     # no MangaPixer link
DONE = "Finished Series"       # MangaPixer-matched, holds volumes 1-3: nothing known missing (still searchable)
NEW = "Unscanned Series"       # matched with missing volumes but not in the store yet

HELD = {QUEST: ["1"], REVIEW: ["1"], OWN: ["1"], DONE: ["1", "2", "3"], NEW: ["1"]}
KNOWLEDGE = {
    QUEST: lambda: from_mangapixer_item(item()),
    REVIEW: lambda: from_mangapixer_item(item(link={"state": "NeedsReview"}, record=None, completion=None,
                                              volumes=None)),
    DONE: lambda: from_mangapixer_item(item()),
    NEW: lambda: from_mangapixer_item(item()),
}


def _entry(title: str) -> MangaEntry:
    folder = Path("/lib") / title
    return MangaEntry(folder=folder, title=title, english_title=None,
                      files=[FileHit(folder / f"{title} v01.cbz", 1000, 0, has_volume=True)])


@pytest.fixture
def make_window(qapp, monkeypatch):
    from mangalist.gui import main_window as mw

    made = []

    def make(backend=None, downloads=True):
        if downloads:
            monkeypatch.setenv("MANGALIST_DOWNLOADS", "1")
            monkeypatch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: backend)
        else:
            monkeypatch.delenv("MANGALIST_DOWNLOADS", raising=False)
        win = mw.MainWindow()
        made.append(win)
        entries = [_entry(t) for t in (QUEST, REVIEW, OWN, DONE, NEW)]
        win._model.set_entries(entries)
        win._model.set_state_providers(
            knowledge_for=lambda e: KNOWLEDGE[e.title]() if e.title in KNOWLEDGE else None,
            inventory_for=lambda e: InventorySnapshot(held_volumes=HELD[e.title]),
            needs_kind_for=lambda e: False, today=TODAY)
        return win

    yield make
    for win in made:
        win.close()
        win.deleteLater()


def _row(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def _backend():
    ids = {str(Path("/lib") / t): i for i, t in enumerate((QUEST, REVIEW, OWN, DONE), start=1)}      # NEW is not scanned
    return FakeBackend(series_ids=ids)


def test_nothing_of_the_volumes_gui_exists_without_downloads(make_window):
    win = make_window(downloads=False)
    assert win._volumes is None and win._volumes_backend is None and win._download_tab is None
    assert not win._top.tab_download.isVisibleTo(win) and win._pages.count() == 1
    assert not win._detail._lbl_download.isVisibleTo(win._detail)
    labels = [a.text() for a in win._settings_menu.actions()]
    assert "qBittorrent…" not in labels and "Downloads…" not in labels


def test_settings_reach_qbittorrent_and_downloads(make_window):
    win = make_window(_backend())
    opened = []
    win._volumes.run_qbittorrent = lambda parent, backend: opened.append("qbt")
    win._volumes.run_downloads = lambda parent, backend, name: opened.append("downloads")
    acts = {a.text(): a for a in win._settings_menu.actions()}
    assert [t for t in acts if t.endswith("…")] == ["Library folders…", "Add a library folder…", "MangaPixer…",
                                                    "qBittorrent…", "Downloads…"]
    acts["qBittorrent…"].trigger()
    acts["Downloads…"].trigger()
    assert opened == ["qbt", "downloads"]


@pytest.mark.parametrize("title, enabled, why", [
    (QUEST, True, ""),
    (REVIEW, False, "MangaPixer"),
    (OWN, False, "not linked there"),
    (DONE, True, ""),
    (NEW, False, "rescan first"),
])
def test_find_volumes_enable_rules(make_window, title, enabled, why):
    win = make_window(_backend())
    got = win._volumes.availability(_row(win, title))
    assert got.enabled is enabled and why in got.reason
    if title == DONE:                   # nothing known missing: nyaa's results are compared with the held volumes
        assert got.target.missing == () and got.target.held == ("1", "2", "3")
    elif enabled:
        assert got.target.missing == ("2", "3") and got.target.held == ("1",)
        assert got.target.series_id == 1 and got.target.folder == str(Path("/lib") / QUEST)


def _wanted(win):
    win._rebuild_wanted()
    return {(w.title, w.group): w for w in win._download_tab.series()}


def test_the_download_tab_gets_every_series_with_gaps(make_window):
    win = make_window(_backend())
    got = _wanted(win)
    # REVIEW (not matched yet) and OWN (own matcher, nothing looked up) have nothing to compare: no gaps, not listed.
    assert set(got) == {(QUEST, "volumes"), (NEW, "volumes")}
    quest = got[(QUEST, "volumes")]
    assert quest.findable and quest.reason == "" and quest.missing == ("2", "3") and quest.held == ("1",)
    assert quest.series_id == 1 and quest.folder == str(Path("/lib") / QUEST) and quest.gaps == "Vol. 2-3"
    assert quest.titles[0] == "Example Quest"
    assert not got[(NEW, "volumes")].findable and "rescan first" in got[(NEW, "volumes")].reason
    assert win._top.tab_download.badge == 2                                             # count_changed -> the badge


def test_row_menu_leads_to_the_download_tab(make_window):
    win = make_window(_backend())
    win._rebuild_wanted()
    menus = []
    win._exec_menu = lambda menu, pos: menus.append(menu) or None
    for title in (QUEST, OWN):
        win._select_source_row(_row(win, title))
        rect = win._table.visualRect(win._proxy.mapFromSource(win._model.index(_row(win, title), 2)))
        win._on_context_menu(rect.center())
    acts = [{a.text(): a for a in m.actions()} for m in menus]
    assert acts[0]["Get the missing volumes…"].isEnabled()
    off = acts[1]["Get the missing volumes…"]
    assert not off.isEnabled() and "no gaps" in off.toolTip()
    assert win.get_missing(str(Path("/lib") / QUEST))
    assert win._top.current() == 1 and win._pages.currentWidget() is win._download_tab
    assert win._download_tab._selected().title == QUEST


def test_the_placeholder_tab_opens_the_dialog_for_a_findable_series(make_window):
    win = make_window(_backend())
    opened = []
    win._volumes.run_nyaa = lambda parent, backend, target: opened.append(target) or type("D", (), {})()
    win._rebuild_wanted()
    tab = win._download_tab
    tab.focus(str(Path("/lib") / NEW))
    assert not tab.btn_find.isEnabled() and "rescan first" in tab.btn_find.toolTip()
    tab.focus(str(Path("/lib") / QUEST))
    assert tab.btn_find.isEnabled()
    tab.btn_find.click()
    assert [t.title for t in opened] == [QUEST]
    shown = []
    tab.show_in_list.connect(shown.append)
    tab.btn_show.click()
    assert shown == [str(Path("/lib") / QUEST)]


def test_download_status_shows_in_the_detail_panel(make_window, qapp):
    backend = _backend()
    backend.record_list = [record(1, series_id=1, status=S.SENT), record(2, series_id=1, status=S.FILED),
                           record(3, series_id=4, status=S.FAILED, error="no space left", wanted=("2",))]
    win = make_window(backend)
    wait_until(qapp, lambda: win._volumes.records)
    assert win._detail._lbl_download.isVisibleTo(win._detail) is False      # no series selected yet
    win._select_source_row(_row(win, QUEST))
    assert win._detail._lbl_download.isVisibleTo(win._detail)
    assert win._detail._lbl_download.text() == "Filed v03-v05 - seeding"   # the newest record of the series wins
    assert "Target folder" in win._detail._lbl_download.toolTip()
    win._volumes.show_in_detail(_row(win, OWN))
    assert win._detail._lbl_download.text() == "-"
    assert win._volumes.status_for_row(_row(win, DONE)) == ("Failed: no space left", win._volumes.status_for_row(
        _row(win, DONE))[1])


def test_records_refresh_after_a_send_updates_the_details(make_window, qapp):
    backend = _backend()
    win = make_window(backend)
    wait_until(qapp, lambda: backend.threads)                      # the startup read has begun (and is maybe running)
    row = _row(win, QUEST)
    win._select_source_row(row)
    assert win._volumes.status_for_row(row) is None
    backend.record_list.append(record(1, series_id=1))
    assert win._volumes.refresh_records()                          # queued behind a running read, or started
    wait_until(qapp, lambda: win._volumes.status_for_row(row) is not None)
    assert win._detail._lbl_download.text() == "Sent"


def test_series_lookup_errors_disable_instead_of_crashing(make_window):
    backend = _backend()

    def broken(folder):
        raise RuntimeError("database is locked")

    backend.series_id_for = broken
    win = make_window(backend)
    got = win._volumes.availability(_row(win, QUEST))
    assert not got.enabled and "rescan first" in got.reason


def test_find_volumes_for_a_disabled_row_only_says_why(make_window):
    win = make_window(_backend())
    opened = []
    win._volumes.run_nyaa = lambda *a: opened.append(a)
    assert not win._volumes.open_find_volumes(_row(win, OWN))
    assert opened == [] and "not linked" in win._status_label.text()


def test_close_stops_the_refresh_timer(make_window):
    win = make_window(_backend())
    assert win._volumes._timer.isActive()
    win.close()
    assert not win._volumes._timer.isActive()


def test_a_refresh_asked_for_during_a_read_runs_right_after_it(make_window, qapp):
    import threading

    backend = _backend()
    reads = []
    gate = threading.Event()
    original = backend.records

    def slow_records(series_id=None):
        reads.append(1)
        if len(reads) == 1:
            gate.wait(10)
        return original(series_id)

    backend.records = slow_records
    win = make_window(backend)
    wait_until(qapp, lambda: reads)                                 # the startup read is running (held)
    backend.record_list.append(record(1, series_id=1))
    assert win._volumes.refresh_records() and win._volumes._again   # queued, not dropped
    gate.set()
    wait_until(qapp, lambda: len(reads) == 2 and win._volumes._call is None)
    assert win._volumes.status_for_row(_row(win, QUEST))[0] == "Sent"
