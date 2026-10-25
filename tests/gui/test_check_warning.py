"""The Download tab says a series' source keeps failing in the scheduled chapter check (owner-approved 2026-10-24): a
chip on its Missing chapters row and a line, with the reason and "Change source…", in its chapters panel; both go when
the series is read again. A fake backend, offscreen Qt."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist.gui import download_tab as dt  # noqa: E402
from mangalist.gui.download_tab import DownloadTab  # noqa: E402
from mangalist.gui.shell import GROUP_CHAPTERS, WantedSeries  # noqa: E402

from .chapter_fakes import FOLDER  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401
from .finding_fakes import FakeFindingBackend  # noqa: E402

SERIES = WantedSeries(4, FOLDER, "Example Webcomic", GROUP_CHAPTERS, "Ch. 41-44", missing=("41", "42", "43", "44"),
                      held=("40",), titles=("Example Webcomic",), findable=False, reason="needs Suwayomi")
OTHER = WantedSeries(5, "/lib/Other Webcomic", "Other Webcomic", GROUP_CHAPTERS, "Ch. 3", missing=("3",), held=("1",),
                     titles=("Other Webcomic",), findable=False, reason="needs Suwayomi")
TEXT = ("The scheduled chapter check could not read Some Source for this series in 3 checks in a row (Cloudflare wall). "
        "Change source… picks another.")


class WarningBackend(FakeFindingBackend):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.warnings = {}

    def chapter_check_warnings(self):
        return dict(self.warnings)


@pytest.fixture
def tabs():
    made = []
    yield made
    for tab in made:
        tab.stop()
        tab.deleteLater()


def make(qapp, tabs):
    backend = WarningBackend(series_ids={FOLDER: 4, "/lib/Other Webcomic": 5})
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0)
    tabs.append(tab)
    tab.resize(1200, 800)
    tab.set_wanted([SERIES, OTHER])
    tab.show_group(GROUP_CHAPTERS)
    return tab, backend


def settle(qapp, tab):
    wait_until(qapp, lambda: not tab.chapters._calls and tab._running is None and not tab._delay.isActive())


def test_a_failing_series_gets_a_chip_and_a_line_in_its_panel_and_others_do_not(qapp, tabs):
    tab, backend = make(qapp, tabs)
    assert tab.status_of(FOLDER) == "" or dt.CHECK_FAILING_CHIP not in tab.status_of(FOLDER)
    backend.warnings = {4: TEXT}
    tab.reload_found()
    assert dt.CHECK_FAILING_CHIP in tab.status_of(FOLDER)
    assert dt.CHECK_FAILING_CHIP not in tab.status_of("/lib/Other Webcomic")
    assert tab.check_warning_of(FOLDER) == TEXT and tab.check_warning_of("/lib/Other Webcomic") == ""
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert tab.chapters.check_warning.isVisibleTo(tab.chapters) and tab.chapters.check_warning.text() == TEXT
    assert "Change source…" in tab.chapters.check_warning.text()
    tab.focus("/lib/Other Webcomic")
    settle(qapp, tab)
    assert not tab.chapters.check_warning.isVisibleTo(tab.chapters)


def test_the_warning_goes_when_the_series_is_read_again(qapp, tabs):
    tab, backend = make(qapp, tabs)
    backend.warnings = {4: TEXT}
    tab.reload_found()
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert tab.chapters.check_warning.isVisibleTo(tab.chapters)
    backend.warnings = {}
    tab.reload_found()
    assert dt.CHECK_FAILING_CHIP not in tab.status_of(FOLDER)
    assert not tab.chapters.check_warning.isVisibleTo(tab.chapters)


def test_a_backend_without_warnings_is_fine(qapp, tabs):
    backend = FakeFindingBackend(series_ids={FOLDER: 4})
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0)
    tabs.append(tab)
    tab.set_wanted([SERIES])
    tab.reload_found()
    assert tab.check_warning_of(FOLDER) == ""
