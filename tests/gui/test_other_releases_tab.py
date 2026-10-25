"""Search releases... (other releases, owner 2026-10-24): the Download tab opens the releases card with Other releases
open - for a To get series, and for a series with nothing missing (not in To get); the To get row menu offers it; a
row whose search found only other releases shows "No releases"; the List's row menu leads there. Offscreen Qt, fake
backends, made-up names."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from mangalist.gui import download_rules as dr  # noqa: E402
from mangalist.gui.download_tab import DownloadTab  # noqa: E402
from mangalist.gui.series_actions import SEARCH_RELEASES  # noqa: E402
from mangalist.gui.shell import GROUP_VOLUMES, WantedSeries  # noqa: E402
from mangalist.gui.volumes_target import VolumeTarget  # noqa: E402

from .conftest import FakeBackend, candidate, qapp, wait_until  # noqa: E402,F401
from .test_volumes_window import DONE, OWN, QUEST, _row, make_window  # noqa: E402,F401

OTHER = dataclasses.replace(candidate("Made Up Saga v01-02 (Digital) (New Group)", info_hash="f" * 40, vol_from="1",
                                      vol_to="2", covers_missing=(), covers_held=("1", "2")), other=True)


class AllBackend(FakeBackend):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.all_searched = []

    def search_all(self, titles, missing, held):
        self.all_searched.append((tuple(titles), tuple(missing), tuple(held)))
        return list(self.results)


@pytest.fixture(autouse=True)
def _close_tabs():
    made = []
    original = DownloadTab.__init__

    def tracking(self, *a, **kw):
        original(self, *a, **kw)
        made.append(self)

    DownloadTab.__init__ = tracking
    yield
    DownloadTab.__init__ = original
    for tab in made:
        tab.stop()
        tab.deleteLater()


GAPS = WantedSeries(21, "/lib/Made Up Saga", "Made Up Saga", GROUP_VOLUMES, "Vol. 3", missing=("3",), held=("1", "2"),
                    titles=("Made Up Saga",), findable=True)


def make(qapp, backend):
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0)
    tab.resize(1200, 800)
    tab.set_wanted([GAPS])
    return tab


def settle(qapp, tab):
    wait_until(qapp, lambda: tab._running is None and not tab._queue and not tab._delay.isActive()
               and tab.releases._search_state in ("done", "error"))


def test_a_series_with_nothing_missing_opens_with_its_other_releases(qapp):
    backend = AllBackend(results=[OTHER])
    tab = make(qapp, backend)
    target = VolumeTarget(series_id=22, folder="/lib/Made Up Done", title="Made Up Done", titles=("Made Up Done",),
                          missing=(), held=("1", "2"))
    assert tab.search_releases(target)
    wait_until(qapp, lambda: tab.releases._search_state == "done")
    panel = tab.releases
    assert tab.current_folder() == "/lib/Made Up Done" and panel.target.browse
    assert backend.all_searched[-1] == (("Made Up Done",), (), ("1", "2"))
    assert panel.others_open() and panel.other_table.rowCount() == 1
    assert "Nothing missing" in panel.subtitle_label.text()
    panel.btn_retry.click()                             # Search again: the same search, not a To get one
    wait_until(qapp, lambda: len(backend.all_searched) == 2 and panel._search_state == "done")
    assert backend.all_searched[-1][1] == ()


def test_a_to_get_series_opens_with_other_releases_open_and_its_chip_says_no_releases(qapp):
    backend = AllBackend(results=[OTHER])
    tab = make(qapp, backend)
    tab.show_group(GROUP_VOLUMES)
    tab.select_series(GAPS.folder)
    settle(qapp, tab)
    assert tab._states[GAPS.folder] == dr.SEARCH_NONE                 # other releases only: nothing fills the gap
    assert not tab.releases.others_open()                              # collapsed by default
    assert "No release on nyaa holds a missing volume" in tab.releases.results_note.text()
    target = tab._target_for(tab._by_folder[GAPS.folder])
    assert tab.search_releases(target)
    assert tab.releases.others_open() and not tab.releases.target.browse
    assert len(backend.all_searched) == 1                              # the result it had: no second search


def test_the_to_get_row_menu_offers_search_releases(qapp):
    backend = AllBackend(results=[OTHER])
    tab = make(qapp, backend)
    tab.show_group(GROUP_VOLUMES)
    chosen = []

    def run(menu, pos):
        act = next(a for a in menu.actions() if a.text() == SEARCH_RELEASES)
        chosen.append(act.isEnabled())
        return act

    tab._exec_menu = run
    item = tab._items[GAPS.folder]
    tab._on_tree_menu(tab.tree.visualItemRect(item).center())
    settle(qapp, tab)
    assert chosen == [True] and tab.releases.others_open()


# --- the List's row menu --------------------------------------------------------------------------------------

def _ids():
    return {str(Path("/lib") / t): i for i, t in enumerate((QUEST, "Review Series", OWN, DONE), start=1)}


def test_the_list_row_menu_searches_releases_of_a_series_with_no_gaps(make_window, qapp):
    backend = AllBackend(series_ids=_ids(), results=[OTHER])
    win = make_window(backend)
    win._rebuild_wanted()
    menus = []
    win._exec_menu = lambda menu, pos: menus.append(menu) or None
    for title in (DONE, OWN):
        win._select_source_row(_row(win, title))
        rect = win._table.visualRect(win._proxy.mapFromSource(win._model.index(_row(win, title), 2)))
        win._on_context_menu(rect.center())
    done, own = ({a.text(): a for a in m.actions()}[SEARCH_RELEASES] for m in menus)
    assert done.isEnabled() and "also when nothing is missing" in done.toolTip()
    assert not own.isEnabled() and "can be searched on nyaa" in own.toolTip()
    folder = str(Path("/lib") / DONE)
    assert win.search_releases_for(folder) == ""
    tab = win._download_tab
    assert win._pages.currentWidget() is tab and tab.current_folder() == folder
    wait_until(qapp, lambda: tab.releases._search_state == "done")
    assert backend.all_searched[-1][1] == () and tab.releases.others_open()
    assert "can be searched on nyaa" in win.search_releases_for(str(Path("/lib") / OWN))
