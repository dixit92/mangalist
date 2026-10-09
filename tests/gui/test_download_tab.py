"""The Download tab (offscreen Qt, fake backend): the "To get" groups and filter, selecting a series searches it once
and keeps the result, a series that cannot be searched says why, the bulk search runs one series after another, a row
shows what is going on with its series, and nothing is sent without the panel's confirmation."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.gui import download_tab as dt  # noqa: E402
from mangalist.gui.download_tab import DownloadTab  # noqa: E402
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, WantedSeries  # noqa: E402

from .conftest import FakeBackend, candidate, qapp, record, wait_until  # noqa: E402,F401


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


def ws(title, gaps="Vol. 2", group=GROUP_VOLUMES, sid=None, missing=("2",), findable=None, reason="", **kw):
    sid = sid if sid is not None else (abs(hash(title)) % 1000) + 10
    return WantedSeries(sid, f"/lib/{title}", title, group, gaps, missing=missing, held=("1",), titles=(title, title + " Alt"),
                        findable=group == GROUP_VOLUMES if findable is None else findable, reason=reason, **kw)


WANTED = [ws("Vinland Saga", "Vol. 14", sid=1), ws("Frieren", "Vol. 12-13", sid=2, missing=("12", "13")),
          ws("Oshi no Ko", "Vol. 15", sid=3),
          ws("Example Webcomic", "Ch. 41-44", GROUP_CHAPTERS, sid=4, reason="needs Suwayomi"),
          ws("Spy x Family", "Upgrade vol. 13", GROUP_UPGRADES, sid=5)]


def make(qapp, backend=None, wanted=WANTED, **kw):
    backend = backend or FakeBackend()
    tab = DownloadTab(backend, confirm=kw.pop("confirm", lambda p, t: True), refresh_ms=0, search_delay_ms=0, **kw)
    tab.resize(1200, 800)
    if wanted is not None:
        tab.set_wanted(wanted)
    return tab, backend


def rows(tab):
    """(group header texts, series titles) in list order."""
    heads, titles = [], []
    for i in range(tab.tree.topLevelItemCount()):
        item = tab.tree.topLevelItem(i)
        (heads if item.data(0, dt.ROLE_HEADER) else titles).append(item.data(0, dt.ROLE_HEADER) or item.text(0))
    return heads, titles


def settle(qapp, tab):
    wait_until(qapp, lambda: tab._running is None and not tab._queue and not tab._delay.isActive())


def test_the_groups_counts_notes_and_rows(qapp):
    tab, _ = make(qapp)
    counts = []
    tab.count_changed.connect(counts.append)
    tab.set_wanted(WANTED)
    assert counts == [5]
    heads, titles = rows(tab)
    assert heads == ["MISSING VOLUMES · 3", "MISSING CHAPTERS · 1", "UPGRADES · 1"]
    assert titles == ["Frieren", "Oshi no Ko", "Vinland Saga", "Example Webcomic", "Spy x Family"]
    assert [tab.tree.topLevelItem(i).data(0, dt.ROLE_ASIDE) for i in (0, 4, 6)] == ["nyaa", "needs Suwayomi", "coming later"]
    frieren = tab._items["/lib/Frieren"]
    assert frieren.data(0, dt.ROLE_SUB) == "Vol. 12-13" and frieren.checkState(0) == Qt.CheckState.Unchecked
    assert tab.count_label.text() == "5 series" and tab.selected_label.text() == "0 selected"
    assert not tab.btn_find.isEnabled()


def test_the_filter_narrows_the_list_and_the_count(qapp):
    tab, _ = make(qapp)
    tab.filter_edit.setText("frie")
    heads, titles = rows(tab)
    assert titles == ["Frieren"] and heads[0] == "MISSING VOLUMES · 1" and heads[1] == "MISSING CHAPTERS · 0"
    assert tab.count_label.text() == "1 of 5 series"
    tab.filter_edit.clear()
    assert len(rows(tab)[1]) == 5


def test_checking_series_counts_them_and_survives_a_filter(qapp):
    tab, _ = make(qapp)
    tab.set_checked("/lib/Frieren")
    tab.set_checked("/lib/Oshi no Ko")
    assert tab.selected_label.text() == "2 selected" and tab.btn_find.isEnabled()
    tab.filter_edit.setText("vinland")
    assert tab.selected_label.text() == "2 selected"                       # hidden but still checked
    tab.filter_edit.clear()
    assert tab._items["/lib/Frieren"].checkState(0) == Qt.CheckState.Checked
    tab.set_checked("/lib/Frieren", False)
    assert tab.selected_label.text() == "1 selected"


def test_focus_selects_a_series_and_searches_it_off_the_ui_thread(qapp):
    tab, backend = make(qapp)
    tab.focus("/lib/Vinland Saga")
    settle(qapp, tab)
    wait_until(qapp, lambda: tab.releases.table.rowCount() == 1)
    assert tab.current_folder() == "/lib/Vinland Saga" and tab.releases.title_label.text() == "Vinland Saga"
    assert backend.searched == [(("Vinland Saga", "Vinland Saga Alt"), ("2",), ("1",))]
    assert threading.get_ident() not in backend.threads
    assert tab.status_of("/lib/Vinland Saga") == "Releases ready"
    assert tab.releases.btn_send.isEnabled()


def test_a_result_is_kept_and_a_second_selection_does_not_search_again(qapp):
    tab, backend = make(qapp)
    tab.focus("/lib/Vinland Saga")
    settle(qapp, tab)
    tab.focus("/lib/Frieren")
    settle(qapp, tab)
    assert len(backend.searched) == 2
    tab.focus("/lib/Vinland Saga")
    settle(qapp, tab)
    assert len(backend.searched) == 2 and tab.releases.table.rowCount() == 1 and tab.releases.title_label.text() == "Vinland Saga"


def test_search_again_asks_nyaa_again(qapp):
    tab, backend = make(qapp)
    tab.focus("/lib/Vinland Saga")
    settle(qapp, tab)
    backend.results = [candidate(info_hash="b" * 40), candidate(info_hash="c" * 40)]
    tab.releases.btn_retry.click()
    settle(qapp, tab)
    wait_until(qapp, lambda: tab.releases.table.rowCount() == 2)
    assert len(backend.searched) == 2


def test_selecting_with_the_mouse_waits_a_moment_before_searching(qapp):
    backend = FakeBackend()
    tab = DownloadTab(backend, refresh_ms=0, search_delay_ms=60)
    tab.set_wanted(WANTED)
    tab.tree.setCurrentItem(tab._items["/lib/Frieren"])
    tab.tree.setCurrentItem(tab._items["/lib/Oshi no Ko"])                 # arrowing through the list
    assert backend.searched == [] and tab._delay.isActive()
    wait_until(qapp, lambda: len(backend.searched) == 1)
    settle(qapp, tab)
    assert backend.searched[0][0][0] == "Oshi no Ko"                       # only where it came to rest


def test_a_series_that_cannot_be_searched_says_why_and_asks_nothing(qapp):
    tab, backend = make(qapp)
    tab.focus("/lib/Example Webcomic")
    assert tab.releases.title_label.text() == "Example Webcomic"
    assert "Suwayomi" in tab.releases.message_text.text() and tab.releases.subtitle_label.text() == "Wanted: Ch. 41-44"
    assert tab.releases.message_action.text() == "Open Settings" and not tab.releases.btn_send.isEnabled()
    sections = []
    tab.settings_requested.connect(sections.append)
    tab.releases.message_action.click()
    assert sections == ["services"]
    tab.focus("/lib/Spy x Family")
    assert "later version" in tab.releases.message_text.text() and not tab.releases.message_action.isVisibleTo(tab)
    tab.set_wanted(WANTED + [ws("Unlicensed", reason="Not licensed in English", findable=False)])
    tab.focus("/lib/Unlicensed")
    assert tab.releases.message_text.text() == "Not licensed in English"
    assert backend.searched == []                                           # nothing was asked of nyaa


def test_a_folder_that_was_not_scanned_yet_says_so(qapp):
    backend = FakeBackend()
    tab, _ = make(qapp, backend, wanted=[WantedSeries(None, "/lib/New", "New", GROUP_VOLUMES, "Vol. 2", missing=("2",),
                                                      titles=("New",), findable=True)])
    tab.focus("/lib/New")
    assert "rescan first" in tab.releases.message_text.text() and backend.searched == []
    backend.series_ids["/lib/New"] = 77                                     # the next set_wanted finds it
    tab.set_wanted(list(tab._wanted))
    tab.focus("/lib/New")
    settle(qapp, tab)
    assert backend.searched and tab.releases.table.rowCount() == 1


def test_bulk_search_runs_one_after_another_and_marks_each(qapp):
    tab, backend = make(qapp)
    for folder in ("/lib/Frieren", "/lib/Oshi no Ko", "/lib/Vinland Saga", "/lib/Example Webcomic"):
        tab.set_checked(folder)
    order, active = [], []
    original = backend.search

    def tracking(titles, missing, held):
        active.append(threading.active_count())
        order.append(titles[0])
        return original(titles, missing, held)

    backend.search = tracking
    assert tab.find_selected() == 3                                          # the chapter series is skipped
    assert "Queued" in {tab.status_of(f) for f in ("/lib/Oshi no Ko", "/lib/Vinland Saga")} | {"Searching..."}
    settle(qapp, tab)
    assert order == ["Frieren", "Oshi no Ko", "Vinland Saga"]                # the list's order, one at a time
    assert [tab.status_of(f) for f in ("/lib/Frieren", "/lib/Oshi no Ko", "/lib/Vinland Saga")] == ["Releases ready"] * 3
    assert tab.status_of("/lib/Example Webcomic") == ""
    assert "Searched 3" in tab.bulk_label.text() and "1 skipped" in tab.bulk_label.text()
    tab.focus("/lib/Oshi no Ko")                                             # selecting one shows its results
    assert tab.releases.table.rowCount() == 1 and len(backend.searched) == 3


def test_bulk_never_sends_anything(qapp):
    tab, backend = make(qapp)
    tab.set_checked("/lib/Frieren")
    tab.set_checked("/lib/Oshi no Ko")
    tab.find_selected()
    settle(qapp, tab)
    assert backend.sent == [] and not hasattr(tab, "send_all")
    assert [b.text() for b in tab.findChildren(type(tab.btn_find)) if "all" in b.text().lower()] == []


def test_bulk_skips_what_is_already_searched_and_says_so(qapp):
    tab, backend = make(qapp)
    tab.set_checked("/lib/Frieren")
    tab.find_selected()
    settle(qapp, tab)
    assert tab.find_selected() == 0 and "already searched" in tab.bulk_label.text() and len(backend.searched) == 1


def test_a_failed_search_is_marked_and_shown_in_the_panel(qapp):
    tab, backend = make(qapp)
    backend.search_error = "nyaa answered 503"
    tab.set_checked("/lib/Frieren")
    tab.find_selected()
    settle(qapp, tab)
    assert tab.status_of("/lib/Frieren") == "Search failed"
    tab.focus("/lib/Frieren")
    assert "nyaa answered 503" in tab.releases.message_text.text()
    backend.search_error = None
    tab.releases.btn_retry.click()
    settle(qapp, tab)
    wait_until(qapp, lambda: tab.status_of("/lib/Frieren") == "Releases ready")


def test_no_results_is_marked_no_releases(qapp):
    tab, _ = make(qapp, FakeBackend(results=[]))
    tab.focus("/lib/Frieren")
    settle(qapp, tab)
    assert tab.status_of("/lib/Frieren") == "No releases"


def test_new_wanted_volumes_drop_the_old_result(qapp):
    tab, backend = make(qapp)
    tab.focus("/lib/Frieren")
    settle(qapp, tab)
    assert tab.status_of("/lib/Frieren") == "Releases ready"
    changed = [ws("Frieren", "Vol. 13", sid=2, missing=("13",))] + [w for w in WANTED if w.title != "Frieren"]
    tab.set_wanted(changed)
    settle(qapp, tab)                                                        # it is the selected one: searched again
    assert backend.searched[-1][1] == ("13",) and len(backend.searched) == 2
    tab.set_wanted([w for w in changed if w.title != "Frieren"])
    assert "/lib/Frieren" not in tab._results and tab.current_folder() is None or tab.current_folder() != "/lib/Frieren"


def test_rows_show_what_the_downloads_say(qapp):
    backend = FakeBackend(records=[record(1, series_id=3, status=S.SENT, wanted=("15",)),
                                   record(2, series_id=2, status=S.FILED, wanted=("12", "13"))])
    tab, _ = make(qapp, backend)
    wait_until(qapp, lambda: tab.status_of("/lib/Oshi no Ko") == "Sent")
    assert tab.status_of("/lib/Frieren") == "Filed v12-v13 - seeding"
    assert tab.downloads.table.rowCount() == 2
    assert tab.downloads.name_of(backend.record_list[0]) == "Oshi no Ko"       # the wanted series name the rows


def test_sending_from_the_tab_confirms_then_the_row_and_the_list_update(qapp):
    texts = []
    tab, backend = make(qapp, confirm=lambda p, t: texts.append(t) or True)
    tab.focus("/lib/Oshi no Ko")
    settle(qapp, tab)
    wait_until(qapp, lambda: tab.releases.btn_send.isEnabled())
    assert tab.releases.send_selected()
    assert "Series: Oshi no Ko" in texts[0] and "Target folder: /lib/Example Series" in texts[0]
    wait_until(qapp, lambda: tab.status_of("/lib/Oshi no Ko") == "Sent" and tab.downloads.table.rowCount() == 1)
    assert backend.sent[0][0] == 3 and backend.sent[0][3] == "/lib/Example Series"


def test_check_now_reloads_the_list_and_the_rows(qapp):
    backend = FakeBackend(records=[record(1, series_id=3, status=S.SENT, wanted=("15",))])
    tab, _ = make(qapp, backend)
    wait_until(qapp, lambda: tab.status_of("/lib/Oshi no Ko") == "Sent")
    backend.record_list = [record(1, series_id=3, status=S.FILED, wanted=("15",))]
    tab.downloads.check_now()
    wait_until(qapp, lambda: tab.status_of("/lib/Oshi no Ko") == "Filed v15 - seeding")
    assert backend.checks == 1


def test_show_in_list_and_stop(qapp):
    tab, backend = make(qapp)
    seen = []
    tab.show_in_list.connect(seen.append)
    tab.releases.show_message("x", "y")
    tab.focus("/lib/Frieren")
    tab.stop()
    settle(qapp, tab)
    assert not tab.btn_find.isEnabled()
    assert tab._queue == type(tab._queue)()                                  # nothing more is started after stop()
