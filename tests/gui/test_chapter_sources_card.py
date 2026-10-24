"""Settings > Download sources > Chapter sources (transition fixes A1, A6, B7; 2026-10-24; offscreen Qt).

- A1: the card is "Chapter sources" under either backend, names the program in use, and has the chapter backend chooser -
  the same setting and guard as Connected services' (the REAL backend, the in-process fake host); the two pages and the
  nav follow each other.
- A6: the status chip says what the read of the sources found (Connected / Not reachable / Starting...), not "Ready" for a
  stored address; a host chosen a moment ago is "Starting..." (and is tried again) instead of a red failure.
- B7: the allowed-sources list is shared by both programs - editing it keeps the ticked sources only the other program has
  installed - and it is read again when the program is switched."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from mangalist import store  # noqa: E402
from mangalist.downloads.adapter import Backend  # noqa: E402
from mangalist.downloads.contracts import DownloadStatus  # noqa: E402
from mangalist.gui import settings_sections as ss  # noqa: E402
from mangalist.gui import start_window  # noqa: E402
from mangalist.gui.downloads_backend import BackendError, SuwayomiSettingsView  # noqa: E402
from mangalist.gui.settings_dialog import SettingsDialog  # noqa: E402
from mangalist.gui.shell import SECTION_SERVICES, SECTION_SOURCES  # noqa: E402
from mangalist.store.downloads import ExtensionHostConfig, SuwayomiSettings  # noqa: E402

from ..downloads.suwayomi_fakes import MANGADEX, WEEB  # noqa: E402
from ..services.extensions.fake_host import FakeHost  # noqa: E402
from .chapter_fakes import SwitchableChapterBackend  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401

SUWA_ONLY = replace(WEEB, id="77", name="Suwayomi Only", display_name="Suwayomi Only")
HOST_ONLY = replace(WEEB, id="88", name="Host Only", display_name="Host Only")
SUWAYOMI_HAS = [MANGADEX, SUWA_ONLY, WEEB]
HOST_HAS = [MANGADEX, WEEB, HOST_ONLY]
BOTH = ("suwayomi", "extensions")


class Sources:
    """A chapter client that lists the sources it is given and, for the host, does everything else for real."""

    def __init__(self, installed, real=None):
        self._installed = installed
        self._real = real

    def sources(self):
        return list(self._installed)

    def close(self):
        if self._real is not None:
            self._real.close()

    def __getattr__(self, name):
        if self._real is None:
            raise AttributeError(name)
        return getattr(self._real, name)


def factory(conn):
    if conn.builtin:
        from mangalist.downloads.chapter_backend import client_from_connection

        return Sources(HOST_HAS, client_from_connection(conn))
    return Sources(SUWAYOMI_HAS)


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "exthost").start()
    yield h
    h.stop()


@pytest.fixture
def backend(db, host):
    b = Backend(db, chapter_client_factory=factory)
    b.save_extension_host(ExtensionHostConfig(url=host.url))
    b.save_suwayomi(SuwayomiSettingsView(base_url="http://192.0.2.10:4567", download_dir="/data/suwayomi"), None)
    return b


@pytest.fixture
def dialogs():
    made = []
    yield made
    for d in made:
        d.done(0)
        d.deleteLater()


def open_dialog(qapp, dialogs, db, backend, section=SECTION_SOURCES):
    dlg = SettingsDialog(None, db, backend, section, cache=None, env={}, info=lambda *a: None)
    dialogs.append(dlg)
    return dlg


def settled(qapp, dlg):
    pages = [p for p in dlg.pages.built()]

    def idle():
        return all(not getattr(p, "_calls", []) for p in pages) and all(
            getattr(p, "_sources_call", None) is None for p in pages)

    wait_until(qapp, idle)


def rows(page):
    return [(page.sources_list.item(i).text().split("  ·  ")[0],
             page.sources_list.item(i).checkState() == Qt.CheckState.Checked) for i in range(page.sources_list.count())]


def stored(db):
    return SuwayomiSettings(db).sources()


# --- A1: the card, its words and its chooser -----------------------------------------------------------------------

@pytest.mark.parametrize("key", BOTH)
def test_the_card_is_chapter_sources_and_names_the_program(qapp, dialogs, db, backend, key):
    backend.set_chapter_backend(key)
    dlg = open_dialog(qapp, dialogs, db, backend)
    page = dlg.pages[SECTION_SOURCES]
    dlg.show_section(SECTION_SOURCES)
    settled(qapp, dlg)
    label = "Suwayomi" if key == "suwayomi" else "Built-in extensions"
    assert page.sources_program.text() == f"Chapters · {label}"
    assert page.title_label.text() == "Download sources"
    texts = "\n".join(w.text() for w in page.findChildren(QLabel))
    assert "Chapter sources" in texts and "needs Suwayomi" not in texts and "Suwayomi sources" not in texts
    if key == "extensions":
        assert "extension host" in page.sources_explain.text()
        assert "Suwayomi" not in page.sources_explain.text() and "Suwayomi" not in page.btn_sources_reload.toolTip()
        assert "the extension host" in page.budget_help.text() and "(Suwayomi)" not in page.budget_help.text()
    else:
        assert "installed in Suwayomi" in page.sources_explain.text()
        assert "Chapter downloads (Suwayomi) do not count" in page.budget_help.text()
    # the nav says the same
    assert dlg._nav[SECTION_SOURCES].hint.text() == "nyaa, chapter sources"
    assert dlg._nav[SECTION_SERVICES].hint.text() == f"MangaPixer, qBittorrent, {label}"


def test_the_chooser_is_on_the_card_and_is_the_same_setting_as_connected_services(qapp, dialogs, db, backend, host):
    dlg = open_dialog(qapp, dialogs, db, backend)
    services = dlg.pages[SECTION_SERVICES]                      # built: it must follow
    page = dlg.pages[SECTION_SOURCES]
    dlg.show_section(SECTION_SOURCES)
    settled(qapp, dlg)
    assert page.chooser.isVisibleTo(page) and page.backend_combo.currentData() == "suwayomi"
    assert [page.backend_combo.itemText(i) for i in range(page.backend_combo.count())] == ["Suwayomi",
                                                                                          "Built-in extensions"]
    assert rows(page) == [("MangaDex (EN)", True), ("Suwayomi Only", False), ("Weeb Example", False)]
    assert page.suwayomi_badge.text() == "Connected"
    # choose the in-house method here
    assert page.choose_backend("extensions")
    assert backend.chapter_backend() == "extensions" and dlg.result_data().downloads_changed
    assert services.backend_combo.currentData() == "extensions"             # Connected services follows
    assert dlg._nav[SECTION_SERVICES].hint.text() == "MangaPixer, qBittorrent, Built-in extensions"
    assert page.sources_program.text() == "Chapters · Built-in extensions"
    assert page.sources_list.count() == 0                                    # the other program's list is gone at once
    settled(qapp, dlg)
    assert rows(page) == [("MangaDex (EN)", True), ("Host Only", False), ("Weeb Example", False)]
    assert page.suwayomi_badge.text() == "Connected"
    assert services.ext_card.badge.text() == "Connected 0.2.0-fake"          # the host was tested by the switch
    assert services.suwayomi_card.badge.text() == "Not in use"
    # and back, from Connected services' own chooser
    assert services.choose_backend("suwayomi")
    assert page.backend_combo.currentData() == "suwayomi" and page.sources_program.text() == "Chapters · Suwayomi"
    settled(qapp, dlg)
    assert rows(page) == [("MangaDex (EN)", True), ("Suwayomi Only", False), ("Weeb Example", False)]
    assert dlg._nav[SECTION_SERVICES].hint.text() == "MangaPixer, qBittorrent, Suwayomi"


def test_the_chooser_on_the_card_has_the_same_guard(qapp, dialogs, db, backend, monkeypatch):
    dlg = open_dialog(qapp, dialogs, db, backend)
    page = dlg.pages[SECTION_SOURCES]
    dlg.show_section(SECTION_SOURCES)
    settled(qapp, dlg)
    on_its_way = SimpleNamespace(status=DownloadStatus.SENT, backend="suwayomi")   # lane F's guard reads both
    monkeypatch.setattr(backend.chapter_ledger, "active", lambda: [on_its_way])
    assert not page.choose_backend("extensions")
    assert "Not switched" in page.backend_note.text() and "still in progress with Suwayomi" in page.backend_note.text()
    assert page.backend_combo.currentData() == "suwayomi" and backend.chapter_backend() == "suwayomi"
    assert not dlg.result_data().downloads_changed
    assert page.sources_list.count() == 3                                    # nothing was dropped or reloaded


def test_without_a_chapter_backend_choice_there_is_no_chooser(qapp, dialogs, db):
    class Plain(SwitchableChapterBackend):
        chapter_backend = None                                               # an older backend

    dlg = open_dialog(qapp, dialogs, db, Plain())
    page = dlg.pages[SECTION_SOURCES]
    assert not page.chooser.isVisibleTo(page) and page.sources_program.text() == "Chapters · Suwayomi"


# --- A6: the chip ---------------------------------------------------------------------------------------------------

def test_the_chip_is_not_set_up_checking_connected_or_not_reachable(qapp, dialogs, db):
    backend = SwitchableChapterBackend(connected=False)
    dlg = open_dialog(qapp, dialogs, db, backend)
    page = dlg.pages[SECTION_SOURCES]
    assert page.suwayomi_badge.text() == "Suwayomi not set up" and page.suwayomi_badge.property("badge") == "warn"
    backend.view = SuwayomiSettingsView(base_url="http://192.0.2.10:4567")      # an address is stored: nothing proved yet
    page._show_suwayomi_state()
    assert page.suwayomi_badge.text() == "Not checked" and page.suwayomi_badge.property("badge") == "muted"
    backend.sources_error = "Suwayomi could not be reached at http://192.0.2.10:4567 (ConnectionError)"
    page.load_suwayomi_sources()
    assert page.suwayomi_badge.text() == "Checking..."
    wait_until(qapp, lambda: page._sources_call is None)
    assert page.suwayomi_badge.text() == "Not reachable" and page.suwayomi_badge.property("badge") == "bad"
    assert "Could not read them" in page.sources_note.text()
    backend.sources_error = None
    page.btn_sources_reload.click()
    wait_until(qapp, lambda: page._sources_call is None)
    assert page.suwayomi_badge.text() == "Connected" and page.suwayomi_badge.property("badge") == "ok"


def test_a_host_down_long_after_the_switch_is_not_reachable_at_once(qapp, dialogs, db):
    backend = SwitchableChapterBackend("extensions")
    backend.sources_for["extensions"] = lambda: (_ for _ in ()).throw(BackendError("The extension host could not be reached"))
    dlg = open_dialog(qapp, dialogs, db, backend)
    page = dlg.pages[SECTION_SOURCES]
    dlg.show_section(SECTION_SOURCES)
    wait_until(qapp, lambda: page._sources_call is None)
    assert page.suwayomi_badge.text() == "Not reachable" and page.suwayomi_badge.property("badge") == "bad"
    assert not page._retry_timer.isActive()


def test_a_host_chosen_a_moment_ago_is_starting_and_is_tried_again(qapp, dialogs, db, monkeypatch):
    monkeypatch.setattr(start_window, "RETRY_MS", 60_000)     # long: no retry can run while "Starting..." is checked
    up = {"yes": False}

    def host_rows():
        if not up["yes"]:
            raise BackendError("The extension host could not be reached")
        return [(MANGADEX, True)]

    backend = SwitchableChapterBackend()
    backend.sources_for["extensions"] = host_rows
    dlg = open_dialog(qapp, dialogs, db, backend)
    page = dlg.pages[SECTION_SOURCES]
    dlg.show_section(SECTION_SOURCES)
    wait_until(qapp, lambda: page._sources_call is None)
    assert page.choose_backend("extensions")
    wait_until(qapp, lambda: page._sources_call is None)
    assert page.suwayomi_badge.text() == "Starting..." and page.suwayomi_badge.property("badge") == "muted"
    assert "starting" in page.sources_note.text() and page._retry_timer.isActive()
    up["yes"] = True                                                         # the JVM came up
    page._retry_timer.start(10)                                              # the retry, now
    wait_until(qapp, lambda: page.suwayomi_badge.text() == "Connected")
    assert rows(page) == [("MangaDex (EN)", True)] and not page._retry_timer.isActive()
    # once the window has passed, a failure is a failure
    up["yes"] = False
    page._start.close()
    page.load_suwayomi_sources()
    wait_until(qapp, lambda: page._sources_call is None)
    assert page.suwayomi_badge.text() == "Not reachable"


def test_the_services_card_says_starting_not_a_red_failure_right_after_the_switch(qapp, dialogs, db, backend, host,
                                                                                    monkeypatch):
    monkeypatch.setattr(start_window, "RETRY_MS", 60_000)     # long: no retry can run while "Starting..." is checked
    backend.save_extension_host(ExtensionHostConfig(url="http://127.0.0.1:9"))        # nothing listens yet
    dlg = open_dialog(qapp, dialogs, db, backend, section=SECTION_SERVICES)
    services = dlg.pages[SECTION_SERVICES]
    settled(qapp, dlg)
    assert services.choose_backend("extensions")
    wait_until(qapp, lambda: not services._calls)
    assert services.ext_card.badge.text() == "Starting..." and services.ext_card.badge.property("badge") == "muted"
    assert "keeps checking" in services.ext_card.note_label.text()
    backend.save_extension_host(ExtensionHostConfig(url=host.url))                 # the host is up
    services._retry_timer.start(10)                                                 # the retry, now
    wait_until(qapp, lambda: services.ext_card.badge.text().startswith("Connected"))
    assert services.ext_card.badge.property("badge") == "ok" and not services._retry_timer.isActive()
    # a later failure (the window is closed) is red at once, as before
    backend.save_extension_host(ExtensionHostConfig(url="http://127.0.0.1:9"))
    services.test_extension_host()
    wait_until(qapp, lambda: not services._calls)
    assert services.ext_card.badge.text() == "Not connected" and services.ext_card.badge.property("badge") == "bad"


# --- B7: one list for two programs ----------------------------------------------------------------------------------

def test_merge_allowed_keeps_the_sources_the_list_does_not_show_in_their_place():
    merge = ss.merge_allowed
    # a source only the other program has installed stays after the one that preceded it
    assert merge(["a", "x", "b", "y"], {"a", "b", "c"}, ["a", "b"]) == ["a", "x", "b", "y"]
    assert merge(["a", "x", "b"], {"a", "b"}, ["b", "a"]) == ["b", "a", "x"]    # reordered: x follows a, its predecessor
    assert merge(["x", "a"], {"a"}, ["a"]) == ["x", "a"]                       # one at the very front
    assert merge(["a", "x"], {"a"}, []) == ["x"]                               # its predecessor unticked: it stays
    assert merge(None, {"a"}, ["a"]) == ["a"] and merge([], {"a"}, []) == []
    assert merge(["a", "a", "x"], {"a"}, ["a"]) == ["a", "x"]                  # no duplicates


def test_editing_the_list_of_one_program_keeps_the_other_programs_sources(qapp, dialogs, db, backend):
    SuwayomiSettings(db).set_sources([MANGADEX.id, SUWA_ONLY.id, WEEB.id])    # chosen under Suwayomi
    dlg = open_dialog(qapp, dialogs, db, backend)
    page = dlg.pages[SECTION_SOURCES]
    dlg.show_section(SECTION_SOURCES)
    settled(qapp, dlg)
    assert rows(page) == [("MangaDex (EN)", True), ("Suwayomi Only", True), ("Weeb Example", True)]
    assert page.choose_backend("extensions")
    settled(qapp, dlg)
    # the host has not installed "Suwayomi Only": it is not listed, and it is not in the way
    assert rows(page) == [("MangaDex (EN)", True), ("Weeb Example", True), ("Host Only", False)]
    page.sources_list.item(2).setCheckState(Qt.CheckState.Checked)             # allow Host Only
    assert stored(db) == [MANGADEX.id, SUWA_ONLY.id, WEEB.id, HOST_ONLY.id]
    page.sources_list.item(1).setCheckState(Qt.CheckState.Unchecked)           # stop using Weeb Example
    assert stored(db) == [MANGADEX.id, SUWA_ONLY.id, HOST_ONLY.id]
    page.sources_list.setCurrentRow(2)
    page.btn_up.click()                                                        # Host Only before the unticked Weeb Example
    assert stored(db) == [MANGADEX.id, SUWA_ONLY.id, HOST_ONLY.id]             # the Suwayomi-only choice is still there
    page.sources_list.setCurrentRow(1)
    page.btn_up.click()                                                        # Host Only before MangaDex: the order is the host's
    assert stored(db) == [HOST_ONLY.id, MANGADEX.id, SUWA_ONLY.id]
    # back to Suwayomi: its list is read again and its own choice is intact
    assert page.choose_backend("suwayomi")
    settled(qapp, dlg)
    assert rows(page) == [("Suwayomi Only", True), ("MangaDex (EN)", True), ("Weeb Example", False)] or \
        rows(page) == [("MangaDex (EN)", True), ("Suwayomi Only", True), ("Weeb Example", False)]
    assert HOST_ONLY.id in stored(db)                                          # and the host's stays for later


def test_the_list_is_read_again_after_a_switch_made_on_connected_services(qapp, dialogs, db, backend):
    dlg = open_dialog(qapp, dialogs, db, backend, section=SECTION_SERVICES)
    services = dlg.pages[SECTION_SERVICES]
    page = dlg.pages[SECTION_SOURCES]
    dlg.show_section(SECTION_SOURCES)
    settled(qapp, dlg)
    assert [n for n, _t in rows(page)] == ["MangaDex (EN)", "Suwayomi Only", "Weeb Example"]
    dlg.show_section(SECTION_SERVICES)
    assert services.choose_backend("extensions")
    settled(qapp, dlg)
    dlg.show_section(SECTION_SOURCES)                                          # same Settings session: no stale list
    assert [n for n, _t in rows(page)] == ["MangaDex (EN)", "Host Only", "Weeb Example"]
    # a tick now stores the host's list merged, never the other program's rows
    page.sources_list.item(1).setCheckState(Qt.CheckState.Checked)             # Host Only
    assert HOST_ONLY.id in stored(db) and "Suwayomi Only" not in [n for n, _t in rows(page)]


def test_a_read_that_was_in_flight_during_a_switch_is_dropped(qapp, dialogs, db):
    import threading

    gate = threading.Event()
    backend = SwitchableChapterBackend()

    def slow_suwayomi():
        gate.wait(5)
        return [(MANGADEX, True), (SUWA_ONLY, False)]

    backend.sources_for["suwayomi"] = slow_suwayomi
    backend.sources_for["extensions"] = [(MANGADEX, True), (HOST_ONLY, False)]
    dlg = open_dialog(qapp, dialogs, db, backend)
    page = dlg.pages[SECTION_SOURCES]
    page.load_suwayomi_sources()
    assert page._sources_call is not None
    assert page.choose_backend("extensions")                                   # while Suwayomi's answer is awaited
    gate.set()
    wait_until(qapp, lambda: page._sources_call is None)
    assert [n for n, _t in rows(page)] == ["MangaDex (EN)", "Host Only"]
