"""The Download tab's Missing chapters group once Suwayomi is connected (offscreen Qt, a fake backend with the Suwayomi
extras): the group says "Suwayomi", selecting a series opens the chapters panel in the releases panel's place, the row
chips follow the lookup and the series' chapter downloads (and only those), sending lands in the In progress list as one
row per Send, and the volume groups are untouched."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.gui import download_tab as dt  # noqa: E402
from mangalist.gui.download_tab import DownloadTab  # noqa: E402
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_VOLUMES, WantedSeries  # noqa: E402

from .chapter_fakes import FOLDER, FakeChapterBackend, chapter_record  # noqa: E402
from .conftest import qapp, record, wait_until  # noqa: E402,F401

CHAPTER_SERIES = WantedSeries(4, FOLDER, "Example Webcomic", GROUP_CHAPTERS, "Ch. 41-44",
                              missing=("41", "42", "43", "44"), held=("40",), titles=("Example Webcomic",),
                              findable=False, reason="needs Suwayomi")
VOLUME_SERIES = WantedSeries(3, "/lib/Oshi no Ko", "Oshi no Ko", GROUP_VOLUMES, "Vol. 15", missing=("15",),
                             held=("1",), titles=("Oshi no Ko",), findable=True)


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


def make(qapp, backend=None, wanted=(CHAPTER_SERIES, VOLUME_SERIES)):
    backend = backend or FakeChapterBackend(series_ids={FOLDER: 4, "/lib/Oshi no Ko": 3})
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0)
    tab.resize(1200, 800)
    tab.set_wanted(list(wanted))
    return tab, backend


def settle(qapp, tab):
    wait_until(qapp, lambda: not tab.chapters._calls and tab._running is None and not tab._delay.isActive())


def test_the_group_is_searchable_once_suwayomi_is_connected(qapp):
    tab, backend = make(qapp)
    tab.show_group(GROUP_CHAPTERS)
    header = tab.tree.topLevelItem(0)
    assert header.data(0, dt.ROLE_HEADER) == "MISSING CHAPTERS · 1" and header.data(0, dt.ROLE_ASIDE) == "Suwayomi"
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert tab.panels.currentIndex() == dt.PANEL_CHAPTERS
    assert backend.lookups == [(4, ("41", "42", "43", "44"), ("Example Webcomic",))]
    assert tab.chapters.table.rowCount() == 4 and tab.status_of(FOLDER) == "Chapters ready"
    assert tab.chapter_lookup_of(FOLDER) is not None
    # A second selection shows the kept lookup; nothing is asked again.
    tab.focus("/lib/Oshi no Ko")
    settle(qapp, tab)
    assert tab.panels.currentIndex() == dt.PANEL_RELEASES
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert len(backend.lookups) == 1 and tab.panels.currentIndex() == dt.PANEL_CHAPTERS


def test_without_suwayomi_the_group_still_points_to_the_services(qapp):
    backend = FakeChapterBackend(connected=False, series_ids={FOLDER: 4})
    tab, _ = make(qapp, backend)
    tab.show_group(GROUP_CHAPTERS)
    assert tab.tree.topLevelItem(0).data(0, dt.ROLE_ASIDE) == "needs Suwayomi"
    tab.focus(FOLDER)
    assert tab.panels.currentIndex() == dt.PANEL_RELEASES and "not set up" in tab.releases.message_text.text()
    assert tab.releases.message_action.text() == "Open Settings" and backend.lookups == []


def test_a_series_not_scanned_yet_says_rescan_first(qapp):
    backend = FakeChapterBackend(series_ids={})
    tab, _ = make(qapp, backend, wanted=[WantedSeries(None, FOLDER, "Example Webcomic", GROUP_CHAPTERS, "Ch. 41-44",
                                                      missing=("41",), titles=("Example Webcomic",))])
    tab.focus(FOLDER)
    assert "rescan first" in tab.releases.message_text.text() and backend.lookups == []


def test_a_failed_lookup_marks_the_row(qapp):
    backend = FakeChapterBackend(series_ids={FOLDER: 4})
    backend.lookup_error = "Suwayomi could not be reached at http://192.0.2.10:4567 (ConnectionError)"
    tab, _ = make(qapp, backend)
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert tab.status_of(FOLDER) == "Lookup failed"


def test_sending_chapters_updates_the_row_and_the_in_progress_list(qapp):
    tab, backend = make(qapp)
    backend.record_list.append(record(1, series_id=3, status=S.SENT, wanted=("15",)))
    tab.downloads.refresh()
    wait_until(qapp, lambda: tab.status_of("/lib/Oshi no Ko") == "Downloading v15")
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert tab.chapters.send_selected()
    settle(qapp, tab)
    assert backend.chapter_sends and [p[0] for p in backend.chapter_sends[0][2]] == [41, 42, 43]
    wait_until(qapp, lambda: tab.downloads.table.rowCount() == 2)
    # One row for the Send in the In progress list, in the chapter wording; the torrent row is unchanged.
    texts = sorted(tab.downloads.table.item(r, 1).text() for r in range(2))
    assert texts[0].startswith("Ch. 41-43 · Alpha Scans + Beta Group · MangaDex (EN)")
    # The chapter row's chips are its chapter downloads; the volume row's are its torrents only.
    wait_until(qapp, lambda: tab.status_of(FOLDER).startswith("Downloading ch 41-43"))
    tab.show_group(GROUP_VOLUMES)
    assert tab.status_of("/lib/Oshi no Ko") == "Downloading v15"
    assert tab.releases._downloads_for(4) == [] and len(tab.chapters._downloads_for(4)) == 3


def test_the_in_progress_list_has_no_torrent_menu_for_chapters(qapp, monkeypatch):
    backend = FakeChapterBackend(series_ids={FOLDER: 4})
    backend.record_list.append(chapter_record(5, 4, "41", status=S.FILED))
    tab, _ = make(qapp, backend)
    tab.downloads.refresh()
    wait_until(qapp, lambda: tab.downloads.table.rowCount() == 1)
    menus = []
    monkeypatch.setattr(tab.downloads, "_exec_menu", lambda menu, pos: menus.append(menu))
    rect = tab.downloads.table.visualItemRect(tab.downloads.table.item(0, 0))
    tab.downloads._on_context_menu(rect.center())
    assert menus == []
    tab.show_group(GROUP_CHAPTERS)
    assert tab.status_of(FOLDER) == "Filed ch 41"
