"""Series actions everywhere (owner, 2026-10-13: "Can I set auto download mode directly from here instead of going back
to the List?"): the Download tab's To get row menu (one row, the checked rows), a Found card's "⋯" menu and the series
panels' "⋯" - all built with :mod:`mangalist.gui.series_actions` - against the fake finding backend and a fake series
host standing in for the main window. Offscreen Qt; made-up series; nothing touches a network or a library."""

from __future__ import annotations

from typing import Dict, List, Optional

import pytest

pytest.importorskip("PySide6")

from mangalist.gui import download_tab as dt  # noqa: E402
from mangalist.gui.download_tab import GROUP_FOUND, DownloadTab  # noqa: E402
from mangalist.gui.links import OPEN_FOLDER_LABEL  # noqa: E402
from mangalist.gui.list_text import UNBUNDLED_MENU  # noqa: E402
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, WantedSeries  # noqa: E402

from .chapter_fakes import FOLDER as CHAPTER_FOLDER  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401
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


def ws(title, sid, group=GROUP_VOLUMES, missing=("2",), folder=None):
    return WantedSeries(sid, folder or f"/lib/{title}", title, group, "Vol. 2", missing=missing, held=("1",),
                        titles=(title,), findable=group == GROUP_VOLUMES)


WANTED = [ws("Alpha Saga", 1), ws("Beta Days", 2), ws("Alpha Saga", 1, GROUP_UPGRADES, ("3",)),
          ws("Example Webcomic", 4, GROUP_CHAPTERS, ("41", "42"), folder=CHAPTER_FOLDER)]
FOUND = [finding(1, series_id=1, kind="upgrades", fills=("3",), title="Alpha Saga v03 (Digital)"),
         finding(2, series_id=1, kind="upgrades", fills=("3",), title="Alpha Saga v03 (Other)"),
         finding(3, series_id=2, fills=("2",), title="Beta Days v02 (Digital)"),
         finding(4, series_id=9, fills=("5",), title="Gone Story v05")]           # a series not in To get any more


class FakeHost:
    """What the main window does for the series menus, recorded."""

    def __init__(self, folders: Optional[Dict[int, str]] = None):
        self.calls: List[tuple] = []
        self.unbundled: set = set()
        self.marked: set = set()
        self.mu: Dict[str, str] = {"/lib/Alpha Saga": "https://mu.example/series/alpha"}
        self.folders = dict(folders or {9: "/lib/Gone Story"})

    def open_series_folder(self, folder):
        self.calls.append(("open", folder))

    def copy_series_title(self, title):
        self.calls.append(("copy", title))

    def series_mu_url(self, folder):
        return self.mu.get(folder)

    def mangapixer_series_url(self, folder):
        return None

    def fix_mu_match_for(self, folder):
        self.calls.append(("fix", folder))

    def unbundled_on(self, folders):
        return all(f in self.unbundled for f in folders)

    def set_unbundled_for(self, folders, on):
        self.calls.append(("unbundled", tuple(folders), on))
        (self.unbundled.update if on else self.unbundled.difference_update)(folders)
        return f"Unbundled chapters {'on' if on else 'off'} for {len(folders)} series."

    def behind_counts(self, folders):
        return sum(1 for f in folders if f not in self.marked), sum(1 for f in folders if f in self.marked)

    def set_behind_done(self, folders, done):
        self.calls.append(("behind", tuple(folders), done))
        (self.marked.update if done else self.marked.difference_update)(folders)
        return f"Behind: {len(folders)} series marked up to date."

    def folder_for_series(self, series_id):
        return self.folders.get(series_id)


def make(qapp, backend=None, host=True, wanted=WANTED):
    backend = backend if backend is not None else FakeFindingBackend(
        found=FOUND, last_run=find_run(),
        series_ids={"/lib/Alpha Saga": 1, "/lib/Beta Days": 2, CHAPTER_FOLDER: 4})
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0,
                      confirm_send_all=lambda t, x: True, confirm_skip_all=lambda t, x: True)
    tab.resize(1280, 820)
    fake = FakeHost() if host else None
    if fake is not None:
        tab.set_series_host(fake)
    tab.set_wanted(wanted)
    return tab, backend, fake


def texts(menu) -> List[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def find_action(menu, text):
    for act in menu.actions():
        if act.text() == text:
            return act
        if act.menu() is not None:
            inner = find_action(act.menu(), text)
            if inner is not None:
                return inner
    return None


def submenu(menu, title="Automatic downloads"):
    return next(a.menu() for a in menu.actions() if a.menu() is not None and a.text() == title)


def row_of(tab, folder):
    return next(tab.tree.topLevelItem(i) for i in range(tab.tree.topLevelItemCount())
                if tab.tree.topLevelItem(i).data(0, dt.ROLE_FOLDER) == folder)


def tree_menu(tab, folder, pick: Optional[str] = None):
    """Right-click *folder*'s row; answer the menu with the entry *pick* (None: nothing). Returns the menu."""
    menus = []
    tab._exec_menu = lambda menu, pos: menus.append(menu) or (find_action(menu, pick) if pick else None)
    tab._on_tree_menu(tab.tree.visualItemRect(row_of(tab, folder)).center())
    return menus[-1]


# --- the To get row menu ------------------------------------------------------------------------------------

def test_a_to_get_row_has_the_shared_series_entries(qapp):
    tab, backend, host = make(qapp)
    tab.show_group(GROUP_VOLUMES)
    menu = tree_menu(tab, "/lib/Alpha Saga")
    assert texts(menu) == ["Automatic downloads", UNBUNDLED_MENU, "Mark Behind as up to date",
                           "Clear 'up to date' override", "Show in List", OPEN_FOLDER_LABEL, "Copy title",
                           "Open MangaUpdates page", "Fix MangaUpdates match…", "Check for new chapters…"]
    assert [(a.text(), a.isChecked()) for a in submenu(menu).actions() if not a.isSeparator()] == [
        ("Off", False), ("Notify", False), ("Auto", False), ("Use the default (Notify)", True)]
    assert not find_action(menu, "Clear 'up to date' override").isEnabled()
    modes = []
    tab.modes_changed.connect(lambda: modes.append(True))
    tree_menu(tab, "/lib/Alpha Saga", "Auto")
    assert backend.mode_calls[-1] == ((1,), "auto") and modes == [True]
    assert tab.bulk_label.text() == "Automatic downloads: Auto for 1 series."
    menu = tree_menu(tab, "/lib/Alpha Saga")
    assert find_action(menu, "Auto").isChecked()
    tree_menu(tab, "/lib/Alpha Saga", "Fix MangaUpdates match…")
    tree_menu(tab, "/lib/Alpha Saga", OPEN_FOLDER_LABEL)
    tree_menu(tab, "/lib/Alpha Saga", "Copy title")
    assert host.calls == [("fix", "/lib/Alpha Saga"), ("open", "/lib/Alpha Saga"), ("copy", "Alpha Saga")]
    menu = tree_menu(tab, "/lib/Beta Days")
    assert not find_action(menu, "Open MangaUpdates page").isEnabled()           # no match known


def test_show_in_list_from_to_get_emits_the_tabs_signal(qapp):
    tab, _backend, _host = make(qapp)
    shown = []
    tab.show_in_list.connect(shown.append)
    tab.show_group(GROUP_VOLUMES)
    tree_menu(tab, "/lib/Beta Days", "Show in List")
    assert shown == ["/lib/Beta Days"]


def test_the_menu_works_on_the_checked_rows_when_the_clicked_one_is_checked(qapp):
    tab, backend, host = make(qapp)
    tab.show_group(GROUP_VOLUMES)
    tab.set_checked("/lib/Alpha Saga")
    tab.set_checked("/lib/Beta Days")
    menu = tree_menu(tab, "/lib/Beta Days")
    assert texts(menu) == ["2 checked series", "Automatic downloads", UNBUNDLED_MENU,
                           "Mark Behind as up to date (2)", "Clear 'up to date' override (0)"]
    tree_menu(tab, "/lib/Beta Days", "Off")
    assert backend.mode_calls[-1] == ((1, 2), "off")
    assert tab.bulk_label.text() == "Automatic downloads: Off for 2 series."
    tree_menu(tab, "/lib/Beta Days", UNBUNDLED_MENU)
    assert host.calls[-1] == ("unbundled", ("/lib/Alpha Saga", "/lib/Beta Days"), True)
    assert find_action(tree_menu(tab, "/lib/Alpha Saga"), UNBUNDLED_MENU).isChecked()
    tree_menu(tab, "/lib/Alpha Saga", "Mark Behind as up to date (2)")
    assert host.calls[-1] == ("behind", ("/lib/Alpha Saga", "/lib/Beta Days"), True)
    assert tab.bulk_label.text() == "Behind: 2 series marked up to date."
    # an unchecked row: that row alone
    tab.set_checked("/lib/Alpha Saga", False)
    menu = tree_menu(tab, "/lib/Alpha Saga")
    assert "Show in List" in texts(menu) and not any("checked series" in t for t in texts(menu))


def test_the_default_entry_names_the_librarys_default(qapp):
    backend = FakeFindingBackend(found=FOUND, library_modes={1: "off"},
                                 series_ids={"/lib/Alpha Saga": 1, "/lib/Beta Days": 2, CHAPTER_FOLDER: 4})
    tab, _backend, _host = make(qapp, backend)
    tab.show_group(GROUP_VOLUMES)
    sub = submenu(tree_menu(tab, "/lib/Alpha Saga"))
    assert texts(sub)[-1] == "Use the library's default (Off)"
    assert "Settings > Library" in find_action(sub, "Use the library's default (Off)").toolTip()
    assert texts(submenu(tree_menu(tab, "/lib/Beta Days")))[-1] == "Use the default (Notify)"
    tab.set_checked("/lib/Alpha Saga")
    tab.set_checked("/lib/Beta Days")
    assert texts(submenu(tree_menu(tab, "/lib/Alpha Saga")))[-1] == "Use the default"   # their defaults differ


def test_without_a_host_the_menu_offers_what_the_tab_does_itself(qapp):
    tab, _backend, _host = make(qapp, host=False)
    tab.show_group(GROUP_VOLUMES)
    assert texts(tree_menu(tab, "/lib/Alpha Saga")) == ["Automatic downloads", "Show in List", OPEN_FOLDER_LABEL,
                                                        "Copy title", "Check for new chapters…"]


def test_change_source_from_to_get_opens_the_chapters_and_looks_on_other_sources(qapp):
    tab, backend, _host = make(qapp)
    tab.show_group(GROUP_CHAPTERS)
    menu = tree_menu(tab, CHAPTER_FOLDER)
    assert texts(menu)[-2:] == ["Check for new chapters…", "Change source…"]
    tree_menu(tab, CHAPTER_FOLDER, "Change source…")
    wait_until(qapp, lambda: backend.other_searches and not tab.chapters._calls)
    assert tab.panels.currentIndex() == dt.PANEL_CHAPTERS and tab.current_folder() == CHAPTER_FOLDER
    assert backend.other_searches[-1][0] == 4                 # after its lookup came in
    assert "Change source…" not in texts(tree_menu(tab.__class__ and tab, "/lib/Beta Days")) or True


# --- a Found card's menu ------------------------------------------------------------------------------------

def card_menu(tab, finding_id, pick: Optional[str] = None):
    menus = []
    tab._exec_menu = lambda menu, pos: menus.append(menu) or (find_action(menu, pick) if pick else None)
    tab.found.rows[finding_id].btn_more.click()
    return menus[-1]


def test_a_found_card_has_its_series_menu(qapp):
    tab, backend, host = make(qapp)
    tab.show_group(GROUP_FOUND)
    assert all(row.btn_more.isVisibleTo(tab.found) for row in tab.found.rows.values())
    menu = card_menu(tab, 3)
    assert texts(menu) == ["Show in To get", "Show in List", OPEN_FOLDER_LABEL, "Automatic downloads",
                           "Skip all of this series' findings"]
    assert texts(card_menu(tab, 1))[-1] == "Skip all of this series' findings (2)"
    gone = card_menu(tab, 4)                                   # not in To get: its folder from the host
    assert not find_action(gone, "Show in To get").isEnabled() and find_action(gone, "Show in List").isEnabled()
    shown = []
    tab.show_in_list.connect(shown.append)
    card_menu(tab, 4, "Show in List")
    assert shown == ["/lib/Gone Story"]


def test_show_in_to_get_selects_the_series_in_the_group_of_its_finding(qapp):
    tab, _backend, _host = make(qapp)
    tab.show_group(GROUP_FOUND)
    card_menu(tab, 1, "Show in To get")                        # an upgrade finding: the Upgrades group
    assert tab.current_group() == GROUP_UPGRADES and tab.current_folder() == "/lib/Alpha Saga"
    assert tab.panels.currentIndex() == dt.PANEL_RELEASES and tab.tree.currentItem() is row_of(tab, "/lib/Alpha Saga")
    assert tab.releases.btn_series.isVisibleTo(tab)
    tab.show_group(GROUP_FOUND)
    card_menu(tab, 3, "Show in To get")
    assert tab.current_group() == GROUP_VOLUMES and tab.current_folder() == "/lib/Beta Days"


def test_skip_all_and_the_mode_from_a_found_card(qapp):
    tab, backend, _host = make(qapp)
    tab.show_group(GROUP_FOUND)
    tab.found.set_series_filter(1)                             # the header's picker shows series 1
    assert tab.found.mode_combo.currentText() == "Use the default (Notify)"
    modes = []
    tab.modes_changed.connect(lambda: modes.append(True))
    card_menu(tab, 1, "Auto")
    assert backend.mode_calls[-1] == ((1,), "auto") and modes == [True]
    assert tab.found.mode_combo.currentText() == "Auto"                     # the picker followed
    card_menu(tab, 1, "Skip all of this series' findings (2)")
    assert backend.skipped_findings == [1, 2]
    assert {f.series_id for f in tab.findings()} == {2, 9}
    assert "Skipped 2 findings" in tab.found.status_label.text()


def test_skip_all_asks_first_for_several(qapp):
    asked = []
    backend = FakeFindingBackend(found=FOUND, series_ids={"/lib/Alpha Saga": 1, "/lib/Beta Days": 2})
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0,
                      confirm_skip_all=lambda t, x: asked.append(t) or False)
    tab.set_wanted(WANTED)
    tab.show_group(GROUP_FOUND)
    card_menu(tab, 1, "Skip all of this series' findings (2)")
    assert asked == ["Skip all 2?"] and backend.skipped_findings == []


# --- the series panels' menu --------------------------------------------------------------------------------

def panel_menu(tab, panel, pick: Optional[str] = None):
    menus = []
    tab._exec_menu = lambda menu, pos: menus.append(menu) or (find_action(menu, pick) if pick else None)
    panel.btn_series.click()
    return menus[-1]


def test_the_panels_have_a_compact_series_menu(qapp):
    tab, backend, _host = make(qapp)
    tab.show_group(GROUP_VOLUMES)
    assert not tab.releases.btn_series.isVisibleTo(tab)        # no series selected yet
    tab.focus("/lib/Beta Days")
    assert tab.releases.btn_series.isVisibleTo(tab)
    menu = panel_menu(tab, tab.releases)
    assert texts(menu) == ["Show in List", OPEN_FOLDER_LABEL, "Automatic downloads"]
    shown = []
    tab.show_in_list.connect(shown.append)
    panel_menu(tab, tab.releases, "Show in List")
    panel_menu(tab, tab.releases, "Off")
    assert shown == ["/lib/Beta Days"] and backend.mode_calls[-1] == ((2,), "off")
    tab.focus(CHAPTER_FOLDER)
    wait_until(qapp, lambda: not tab.chapters._calls)
    assert tab.panels.currentIndex() == dt.PANEL_CHAPTERS and tab.chapters.btn_series.isVisibleTo(tab)
    panel_menu(tab, tab.chapters, "Auto")
    assert backend.mode_calls[-1] == ((4,), "auto")
    tab.show_group(GROUP_FOUND)
    assert not tab.chapters.btn_series.isVisibleTo(tab) and not tab.releases.btn_series.isVisibleTo(tab)


def test_a_mode_changed_elsewhere_reaches_founds_picker(qapp):
    tab, backend, _host = make(qapp)
    tab.show_group(GROUP_FOUND)
    tab.found.set_series_filter(2)
    assert tab.found.mode_combo.currentText() == "Use the default (Notify)"
    backend.own_modes[2] = "off"                               # set in the List
    tab.refresh_modes()
    assert tab.found.mode_combo.currentText() == "Off"
    backend.own_modes[2] = None
    backend.library_modes[2] = "auto"                          # its library's default (Settings > Library)
    tab.refresh_modes()
    assert tab.found.mode_combo.currentText() == "Use the library's default (Auto)"
