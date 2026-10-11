"""The Download tab's automatic-downloads side (offscreen Qt, the fake finding backend): the Found chip and view (rows,
Send / Skip, Send all asks first, the empty state with the last pass), the badge, Find now (off the UI thread, busy, the
summary, a pass already running says so) and the bulk find (checked series or the group shown, then Found). Made-up
series; nothing touches a network or a library."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.gui import download_tab as dt  # noqa: E402
from mangalist.gui.download_tab import GROUP_FOUND, PANEL_FOUND, DownloadTab  # noqa: E402
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_VOLUMES, WantedSeries  # noqa: E402

from .conftest import FakeBackend, qapp, wait_until  # noqa: E402,F401
from .finding_fakes import FakeFindingBackend, find_run, finding  # noqa: E402


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


def ws(title, sid, group=GROUP_VOLUMES, missing=("2",)):
    return WantedSeries(sid, f"/lib/{title}", title, group, "Vol. 2", missing=missing, held=("1",), titles=(title,),
                        findable=group == GROUP_VOLUMES)


WANTED = [ws("Alpha Saga", 1), ws("Beta Days", 2), ws("Gamma Webcomic", 3, GROUP_CHAPTERS)]
FOUND = [finding(1, series_id=1, fills=("2",), title="Alpha Saga v02 (Digital)"),
         finding(2, series_id=2, fills=("2", "3"), size_bytes=None, reason="a pack whose files cannot be narrowed to "
                                                                            "the missing volumes"),
         finding(3, series_id=3, kind="chapters", fills=("41", "42"), size_bytes=None, title="Ch. 41-42 · Alpha Scans")]


def make(qapp, backend=None, wanted=WANTED, asked=None, **kw):
    backend = backend if backend is not None else FakeFindingBackend(found=FOUND, last_run=find_run())
    answers = asked if asked is not None else []

    def confirm_send_all(title, text):
        answers.append((title, text))
        return getattr(confirm_send_all, "yes", True)          # the owner's answer (a test sets .yes)

    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0,
                      confirm_send_all=confirm_send_all, **kw)
    tab.resize(1280, 820)
    if wanted is not None:
        tab.set_wanted(wanted)
    return tab, backend, confirm_send_all, answers


def settle(qapp, tab):
    wait_until(qapp, lambda: not tab.find_running() and not tab.found.busy())


def test_the_found_chip_counts_waiting_findings_and_the_badge_adds_them(qapp):
    tab, backend, _c, _a = make(qapp)
    counts = []
    tab.count_changed.connect(counts.append)
    tab.set_wanted(WANTED)
    assert counts[-1] == 3 + 3                                          # 3 series to get + 3 findings waiting
    assert tab.group_chips[GROUP_FOUND].count == 3 and tab.group_chips[GROUP_FOUND].text() == "Found"
    assert tab.current_group() == GROUP_VOLUMES                         # the To get groups come first
    tab.show_group(GROUP_FOUND)
    assert tab.panels.currentIndex() == PANEL_FOUND and tab.footer.isHidden()
    heads = [tab.tree.topLevelItem(i).data(0, dt.ROLE_HEADER) for i in range(tab.tree.topLevelItemCount())]
    names = [tab.tree.topLevelItem(i).text(0) for i in range(1, tab.tree.topLevelItemCount())]
    assert heads[0] == "FOUND · 3 SERIES" and names == ["Alpha Saga", "Beta Days", "Gamma Webcomic"]
    assert tab.tree.topLevelItem(3).data(0, dt.ROLE_SUB) == "ch 41-42"
    rows = tab.found.rows
    assert [r.name_label.text() for r in rows.values()] == ["Alpha Saga", "Beta Days", "Gamma Webcomic"]
    beta = rows[2]
    assert beta.gets_label.text() == "Gets v02-v03" and beta.size_label.text() == "size not known"
    assert beta.reason_label.text() == "Waits because a pack whose files cannot be narrowed to the missing volumes"
    assert beta.kind_pill.text() == "Missing volumes" and rows[3].kind_pill.text() == "Missing chapters"
    assert tab.found.count_label.text() == "3 waiting · 700 MB + 2 of size not known"
    tab.show_group(GROUP_VOLUMES)                                       # back to a To get group: the releases panel
    assert tab.panels.currentIndex() == dt.PANEL_RELEASES and not tab.footer.isHidden()


def test_picking_a_series_in_the_found_list_shows_only_its_findings(qapp):
    tab, _b, _c, _a = make(qapp)
    tab.show_group(GROUP_FOUND)
    tab.tree.setCurrentItem(tab.tree.topLevelItem(2))                   # Beta Days
    assert tab.found.series_filter() == 2 and list(tab.found.rows) == [2]
    assert tab.found.title_label.text() == "Found for Beta Days" and not tab.found.btn_show_all.isHidden()
    tab.found.btn_show_all.click()
    assert tab.found.series_filter() is None and len(tab.found.rows) == 3 and tab.found.btn_show_all.isHidden()


def test_send_runs_off_the_ui_thread_and_the_row_leaves(qapp):
    tab, backend, _c, _a = make(qapp)
    tab.show_group(GROUP_FOUND)
    tab.found.rows[1].btn_send.click()
    settle(qapp, tab)
    assert backend.sent_findings == [1] and backend.threads[-1] != threading.get_ident()
    assert 1 not in tab.found.rows and tab.group_chips[GROUP_FOUND].count == 2
    assert tab.found.status_label.text() == "Alpha Saga, v02: sent to qBittorrent"


def test_a_refused_send_says_why_and_keeps_the_row(qapp):
    backend = FakeFindingBackend(found=FOUND)
    backend.send_finding_error[1] = "qBittorrent could not be reached"
    tab, _b, _c, _a = make(qapp, backend)
    tab.found.send(1)
    settle(qapp, tab)
    assert tab.found.status_label.text() == "Could not send v02 of Alpha Saga: qBittorrent could not be reached"
    assert tab.found.status_label.property("tone") == "bad" and 1 in tab.found.rows


def test_skip_is_quick_and_the_finding_is_not_offered_again(qapp):
    tab, backend, _c, _a = make(qapp)
    tab.found.rows[3].btn_skip.click()
    assert backend.skipped_findings == [3] and 3 not in tab.found.rows
    assert "not offered again" in tab.found.status_label.text()


def test_send_all_asks_first_with_the_count_and_size(qapp):
    tab, backend, confirm, answers = make(qapp)
    confirm.yes = False
    assert tab.found.btn_send_all.text() == "Send all 3…"
    assert not tab.found.send_all() and backend.sent_findings == []
    title, text = answers[-1]
    assert title == "Send all 3?" and "2 torrents (700 MB plus 1 of a size not known yet)" in text
    confirm.yes = True
    assert tab.found.send_all()
    settle(qapp, tab)
    assert backend.sent_findings == [1, 2, 3] and tab.found.status_label.text() == "Sent 3 of 3."
    assert tab.found.rows == {} and not tab.found.empty.isHidden()


def test_the_empty_state_says_nothing_waits_and_when_the_last_pass_ran(qapp):
    tab, _b, _c, _a = make(qapp, FakeFindingBackend(last_run=find_run()))
    tab.show_group(GROUP_FOUND)
    assert not tab.found.empty.isHidden() and tab.found.empty_title.text() == "Nothing found waiting for you"
    assert tab.found.empty_text.text().startswith("Last find pass: ")
    assert "(scheduled) - 12 series searched" in tab.found.empty_text.text()
    tab2, _b, _c, _a = make(qapp, FakeFindingBackend())
    assert tab2.found.empty_text.text() == "No find pass has run yet."


def test_find_now_runs_off_the_ui_thread_with_a_busy_state_and_the_summary(qapp):
    backend = FakeFindingBackend()
    backend.find_gate = threading.Event()
    backend.new_on_find = [finding(9, series_id=1, fills=("2",))]
    tab, _b, _c, _a = make(qapp, backend)
    assert not tab.downloads.btn_find_now.isHidden()
    tab.downloads.btn_find_now.click()
    assert tab.find_running() and not tab.downloads.btn_find_now.isEnabled()
    assert tab.downloads.btn_find_now.text() == "Finding…" and not tab.found.busy_row.isHidden()
    assert not tab.find_now()                                           # a pass already running says so
    assert "already running" in tab.downloads.status_label.text()
    backend.find_gate.set()
    settle(qapp, tab)
    assert backend.find_calls == [None] and backend.threads[0] != threading.get_ident()
    assert tab.downloads.btn_find_now.isEnabled() and tab.downloads.btn_find_now.text() == "Find now"
    wait_until(qapp, lambda: "Find now: 12 series searched" in tab.downloads.status_label.text())
    assert tab.group_chips[GROUP_FOUND].count == 1 and tab.found.busy_row.isHidden()


def test_a_failed_find_pass_says_why(qapp):
    backend = FakeFindingBackend()
    backend.find_error = "a find pass is already running in the container"
    tab, _b, _c, _a = make(qapp, backend)
    tab.find_now()
    settle(qapp, tab)
    assert tab.found.status_label.text() == "Could not run the find pass: a find pass is already running in the container"


def test_bulk_find_covers_the_checked_series_or_the_group_shown_then_opens_found(qapp):
    backend = FakeFindingBackend()
    tab, _b, _c, _a = make(qapp, backend)
    assert tab.btn_bulk.text() == "Find releases for 2 series"          # nothing checked: the Volumes group shown
    tab.set_checked("/lib/Beta Days")
    assert tab.btn_bulk.text() == "Find releases for 1 series" and tab.btn_find.text() == "Search selected on nyaa"
    tab.btn_bulk.click()
    assert tab.current_group() == GROUP_FOUND and tab.panels.currentIndex() == PANEL_FOUND
    settle(qapp, tab)
    assert backend.find_calls == [(2,)] and backend.sent == []
    assert tab.found.status_label.text().startswith("Bulk find: 1 series searched")


def test_without_the_backend_methods_there_is_no_found_chip_nor_find_now(qapp):
    tab, _b, _c, _a = make(qapp, FakeBackend())
    assert GROUP_FOUND not in tab.group_chips and tab.downloads.btn_find_now.isHidden() and tab.btn_bulk.isHidden()
    assert tab.badge_count() == 3 and not tab.find_now() and not tab.find_for([1])


def test_the_found_group_is_remembered(qapp):
    tab, _b, _c, _a = make(qapp)
    tab.show_group(GROUP_FOUND)
    tab2, _b, _c, _a = make(qapp)
    assert tab2.current_group() == GROUP_FOUND and tab2.panels.currentIndex() == PANEL_FOUND
