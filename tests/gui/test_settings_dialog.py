"""The Settings dialog (offscreen Qt, a real database in a temporary folder, fake backend and fake MangaPixer client):
its five sections, the live status of the connected services, the roots editor with Save / Cancel, the stored
switches, write-only secrets, and what ``open_settings`` reports as changed."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit  # noqa: E402

from mangalist import config, store  # noqa: E402
from mangalist.downloads.options import KEY_MU_AUTOSTART, KEY_SCAN_AFTER_FILING, NyaaOptions, get_flag, load_nyaa_options  # noqa: E402,E501
from mangalist.gui import settings_dialog as sd  # noqa: E402
from mangalist.gui.downloads_backend import BackendError, QbtSettings  # noqa: E402
from mangalist.gui.settings_dialog import SettingsDialog, open_settings  # noqa: E402
from mangalist.gui.shell import (  # noqa: E402
    SECTION_AUTOMATION,
    SECTION_LIBRARY,
    SECTION_MATCHING,
    SECTION_SERVICES,
    SECTION_SOURCES,
    SECTIONS,
    SettingsResult,
)
from mangalist.services.mangapixer import client as mpc  # noqa: E402
from mangalist.services.mangapixer import open_cache  # noqa: E402

from .conftest import FakeBackend, qapp, wait_until  # noqa: E402,F401

TOKEN = "mpx_SecretTokenDoNotShow_0123456789"
PASSWORD = "hunter2-example"


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def cache(db):
    return open_cache(db)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library" / "Manga"
    for name in ("Series A", "Series B"):
        (root / name).mkdir(parents=True)
    return root


class FakeMp:
    def __init__(self, refuse=False, down=False):
        self.refuse, self.down, self.closed = refuse, down, False

    def ping(self):
        if self.refuse:
            raise mpc.TokenRejected("the token was refused", 401)
        if self.down:
            raise mpc.ConnectionFailed("cannot reach https://mangapixer.example (ConnectionError)")

    def libraries(self):
        return [object(), object()]

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _close_dialogs():
    made = []
    original = SettingsDialog.__init__

    def tracking(self, *a, **kw):
        original(self, *a, **kw)
        made.append(self)

    SettingsDialog.__init__ = tracking
    yield
    SettingsDialog.__init__ = original
    for dlg in made:
        dlg.reject()
        dlg.deleteLater()


def make(qapp, db, cache, backend="default", mp=None, section=None, **kw):
    backend = FakeBackend() if backend == "default" else backend
    mp = mp or FakeMp()
    info = kw.pop("info", lambda parent, title, text: None)
    dlg = SettingsDialog(None, db, backend, section, client_factory=lambda url, token, verify: mp, info=info,
                         cache=cache, env=kw.pop("env", {}), **kw)
    return dlg, backend


def connect_mangapixer(cache):
    cache.set_connection(base_url="https://mangapixer.example", token=TOKEN)


def settle(qapp, dlg):
    wait_until(qapp, lambda: not dlg.pages[SECTION_SERVICES]._calls)


def all_text(widget):
    out = []
    for w in widget.findChildren(QLabel):
        out.append(w.text() + w.toolTip())
    for w in widget.findChildren(QLineEdit):
        out.append(w.text() + w.placeholderText())
    return "\n".join(out)


# --- the frame ----------------------------------------------------------------------------------------------


def test_five_sections_in_the_mockups_order_with_their_hints(qapp, db, cache):
    dlg, _ = make(qapp, db, cache)
    assert [k for k, _t, _h in sd.NAV] == list(SECTIONS) == [SECTION_LIBRARY, SECTION_SERVICES, SECTION_SOURCES,
                                                              SECTION_MATCHING, SECTION_AUTOMATION]
    assert [(t, h) for _k, t, h in sd.NAV] == [
        ("Library", "Roots, file naming"), ("Connected services", "MangaPixer, qBittorrent, Suwayomi"),
        ("Download sources", "nyaa, Suwayomi sources"), ("Matching", "MangaUpdates"),
        ("Automation", "Schedules, Remove Completed")]
    assert dlg.current == SECTION_LIBRARY and dlg.windowTitle() == "Settings"
    assert dlg._nav[SECTION_LIBRARY].property("current") is True
    dlg.show_section(SECTION_MATCHING)
    assert dlg.stack.currentIndex() == 3 and dlg._nav[SECTION_LIBRARY].property("current") is False
    assert dlg._nav[SECTION_MATCHING].property("current") is True
    dlg.show_section("nonsense")
    assert dlg.current == SECTION_MATCHING


def test_it_opens_on_the_asked_section(qapp, db, cache):
    dlg, _ = make(qapp, db, cache, section=SECTION_AUTOMATION)
    assert dlg.current == SECTION_AUTOMATION


def test_clicking_a_section_entry_switches(qapp, db, cache):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    dlg, _ = make(qapp, db, cache)
    dlg.show()
    QTest.mouseClick(dlg._nav[SECTION_SOURCES], Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert dlg.current == SECTION_SOURCES


def test_open_settings_returns_what_changed(qapp, db, cache, monkeypatch):
    seen = {}

    def fake_exec(self):
        seen["dlg"] = self
        self._flag("roots")
        self._flag("downloads")
        return 0

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    result = open_settings(None, db, FakeBackend(), SECTION_SOURCES)
    assert result == SettingsResult(roots_changed=True, mangapixer_changed=False, downloads_changed=True)
    assert seen["dlg"].current == SECTION_SOURCES
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: 0)
    assert open_settings(None, db, None) == SettingsResult()


def test_without_a_backend_downloads_are_said_to_be_off(qapp, db, cache):
    dlg, _ = make(qapp, db, cache, backend=None)
    services = dlg.pages[SECTION_SERVICES]
    assert services.qbt_card.badge.text() == "Downloads off" and not services.qbt_card.btn_primary.isEnabled()
    sources = dlg.pages[SECTION_SOURCES]
    assert sources.nyaa_badge.text() == "Downloads off" and not sources.on_check.isEnabled()
    automation = dlg.pages[SECTION_AUTOMATION]
    assert not automation.remove_check.isEnabled()


# --- Library -------------------------------------------------------------------------------------------------


def test_library_lists_the_roots_with_their_series_and_mangapixer_library(qapp, db, cache, library):
    root = db.add_root(str(library), "Manga-Concluded")
    db.connect().__enter__()                                   # (the store opens connections per use)
    dlg, _ = make(qapp, db, cache)
    page = dlg.pages[SECTION_LIBRARY]
    assert "MangaList files downloads only into these" in page.lead_label.text()
    text = all_text(page)
    assert "Manga-Concluded" in text and str(library) in text and "0 series" in text
    assert "File naming - one naming scheme per root, applied by the renamer (phase 2)." in text
    assert root.id is not None


def test_library_says_which_mangapixer_library_each_root_is_part_of(qapp, db, cache, library, tmp_path):
    from mangalist.services.mangapixer import mapping as mp_map
    from mangalist.store.mangapixer import Mapping

    class Lib:
        def __init__(self, id, name, kind="manga"):
            self.id, self.display_name, self.kind = id, name, kind
            self.folder_count = self.item_count = self.last_scan_at = None

    whole = db.add_root(str(library), "Manga-Ongoing")
    inside = db.add_root(str(tmp_path / "other" / "M" / "Manga"), "Other manga")
    mine = db.add_root(str(tmp_path / "comics"), "Comics")
    fresh = db.add_root(str(tmp_path / "new"), "New")
    assert "MangaPixer" not in all_text(make(qapp, db, cache)[0].pages[SECTION_LIBRARY])   # not connected: no line
    cache.set_connection(base_url="mangapixer.example:8080", token="t")
    cache.save_libraries([Lib("ongoing", "Manga-Ongoing"), Lib("other", "Other", None)])
    cache.save_mapping(Mapping(root_id=whole.id, library_id="ongoing", prefix=[], matched=290, unmatched=6))
    cache.save_mapping(Mapping(root_id=inside.id, library_id="other", prefix=["M", "Manga"], matched=12, unmatched=0))
    mp_map.set_manual_mapping(cache, mine.id, None)
    text = all_text(make(qapp, db, cache)[0].pages[SECTION_LIBRARY])
    assert "MangaPixer: Manga-Ongoing · 290 of 296 series" in text
    assert "MangaPixer: Other › M/Manga · 12 of 12 series" in text
    assert "MangaPixer: not paired (your choice)" in text
    assert "MangaPixer: not paired yet - it pairs after the next scan" in text            # the new root
    assert fresh.id is not None


def test_library_with_no_roots_says_what_to_do(qapp, db, cache):
    dlg, _ = make(qapp, db, cache)
    assert "No roots yet" in all_text(dlg.pages[SECTION_LIBRARY])


def test_edit_a_root_saves_only_on_save(qapp, db, cache, library):
    db.add_root(str(library), "First")
    dlg, _ = make(qapp, db, cache)
    page = dlg.pages[SECTION_LIBRARY]
    flags = []
    page.roots_changed.connect(lambda: flags.append(True))
    editor = page.edit_root(0)
    editor.name_edit.setText("Renamed")
    editor.name_edit.textEdited.emit("Renamed")
    editor.pattern_edit.setText("@Oneshots/**")
    editor.add_pattern()
    page.cancel_edit()
    assert [r.name for r in db.list_roots()] == ["First"] and flags == [] and page.editor is None
    editor = page.edit_root(0)
    editor.name_edit.setText("Renamed")
    editor.name_edit.textEdited.emit("Renamed")
    editor.pattern_edit.setText("@Oneshots/**")
    editor.add_pattern()
    assert page.save_edit() and flags == [True]
    assert [(r.name, r.exclusions) for r in db.list_roots()] == [("Renamed", ["@Oneshots/**"])]
    assert "Renamed" in all_text(page) and dlg.result_data().roots_changed


def test_add_a_root_asks_for_a_folder_then_saves(qapp, db, cache, library, tmp_path):
    dlg, _ = make(qapp, db, cache, browse=lambda parent, title, start: str(library))
    page = dlg.pages[SECTION_LIBRARY]
    editor = page.add_root()
    assert editor is not None and editor.root_list.count() == 1
    assert page.save_edit()
    assert [r.path for r in db.list_roots()] == [str(library)]
    page.browse = None
    dlg2, _ = make(qapp, db, cache, browse=lambda *a: "")
    assert dlg2.pages[SECTION_LIBRARY].add_root() is None                      # the folder picker was cancelled


def test_a_bad_root_is_refused_with_the_reason_and_nothing_is_added(qapp, db, cache, library):
    db.add_root(str(library), "First")
    dlg, _ = make(qapp, db, cache, browse=lambda *a: str(library / "Series A"))
    page = dlg.pages[SECTION_LIBRARY]
    editor = page.add_root()
    assert editor is not None and "overlaps" in editor.error_label.text()
    assert editor.root_list.count() == 1                                          # the overlapping folder was not added
    assert page.save_edit() and len(db.list_roots()) == 1


# --- Connected services ------------------------------------------------------------------------------------


def test_services_cards_before_anything_is_set_up(qapp, db, cache):
    backend = FakeBackend()
    backend.settings = QbtSettings()
    dlg, _ = make(qapp, db, cache, backend)
    dlg.show_section(SECTION_SERVICES)
    page = dlg.pages[SECTION_SERVICES]
    assert [c.name_label.text() for c in (page.mp_card, page.qbt_card, page.suwayomi_card)] == [
        "MangaPixer", "qBittorrent", "Suwayomi"]
    assert page.mp_card.badge.text() == "Not set up" and page.qbt_card.badge.text() == "Not set up"
    assert page.suwayomi_card.badge.text() == "Not set up" and page.suwayomi_card.badge.property("badge") == "muted"
    assert not page.mp_card.btn_secondary.isEnabled() and not page.qbt_card.btn_secondary.isEnabled()
    assert page.mp_card.used_label.text() == "Used by: Matching, Automation"
    assert page.qbt_card.used_label.text() == "Used by: nyaa"
    assert page.suwayomi_card.used_label.text() == "Used by: Suwayomi sources (chapters)"
    assert not page.suwayomi_card.btn_primary.isEnabled()


def test_connected_status_is_checked_live_off_the_ui_thread(qapp, db, cache):
    connect_mangapixer(cache)
    mp = FakeMp()
    dlg, backend = make(qapp, db, cache, mp=mp)
    page = dlg.pages[SECTION_SERVICES]
    assert page.mp_card.badge.text() == "Not checked"                           # nothing is called until it is shown
    dlg.show_section(SECTION_SERVICES)
    settle(qapp, dlg)
    assert page.mp_card.badge.text() == "Connected" and page.mp_card.badge.property("badge") == "ok" and mp.closed
    assert page.qbt_card.badge.text() == "Connected v5.2.4" and page.qbt_card.badge.property("badge") == "ok"
    assert "qbt.example:8080" in page.qbt_card.detail_label.text() and "/data/appdata/torrents/mangalist" in page.qbt_card.detail_label.text()
    assert backend.tested and backend.tested[0][1] is None                      # the stored password, never a typed one
    n = len(backend.tested)
    dlg.show_section(SECTION_LIBRARY)
    dlg.show_section(SECTION_SERVICES)
    settle(qapp, dlg)
    assert len(backend.tested) == n                                              # only the first showing checks
    assert page.test_qbittorrent()
    settle(qapp, dlg)
    assert len(backend.tested) == n + 1


def test_a_refused_token_and_an_unreachable_server_are_told_apart(qapp, db, cache):
    connect_mangapixer(cache)
    dlg, _ = make(qapp, db, cache, mp=FakeMp(refuse=True))
    dlg.show_section(SECTION_SERVICES)
    settle(qapp, dlg)
    page = dlg.pages[SECTION_SERVICES]
    assert page.mp_card.badge.text() == "Token refused" and page.mp_card.badge.property("badge") == "bad"
    assert "refused the token" in page.mp_card.note_label.text() and TOKEN not in all_text(dlg)
    dlg2, _ = make(qapp, db, cache, mp=FakeMp(down=True))
    dlg2.show_section(SECTION_SERVICES)
    settle(qapp, dlg2)
    page2 = dlg2.pages[SECTION_SERVICES]
    assert page2.mp_card.badge.text() == "Not reachable" and "cannot reach" in page2.mp_card.note_label.text()


def test_a_stored_rejection_shows_before_any_call(qapp, db, cache):
    connect_mangapixer(cache)
    cache.mark_token_rejected()
    dlg, _ = make(qapp, db, cache)
    assert dlg.pages[SECTION_SERVICES].mp_card.badge.text() == "Token refused"


def test_qbittorrent_failure_is_shown(qapp, db, cache):
    backend = FakeBackend()
    backend.test_error = "wrong username or password"
    dlg, _ = make(qapp, db, cache, backend)
    dlg.show_section(SECTION_SERVICES)
    settle(qapp, dlg)
    card = dlg.pages[SECTION_SERVICES].qbt_card
    assert card.badge.text() == "Not connected" and "wrong username or password" in card.note_label.text()


def test_the_mangapixer_card_lists_libraries_and_root_mapping_and_warns_about_scans(qapp, db, cache, library):
    from mangalist.store.mangapixer import Mapping

    connect_mangapixer(cache)
    root = db.add_root(str(library), "Manga-Concluded")
    cache.save_libraries([mpc.Library("lib0", "Manga", kind="manga"), mpc.Library("lib1", "Comics", kind="comic")])
    cache.save_mapping(Mapping(root_id=root.id, library_id="lib0", prefix=(), manual=False))
    dlg, _ = make(qapp, db, cache)
    card = dlg.pages[SECTION_SERVICES].mp_card
    text = card.detail_label.text()
    assert "https://mangapixer.example · token: stored" in text and TOKEN not in text
    assert "Libraries: Manga, Comics (kind skipped)" in text
    assert "Roots: Manga-Concluded → Manga" in text
    assert card.note_label.isHidden()
    cache.scan_forbidden_at = lambda: "2026-10-08T10:00:00Z"                      # MangaPixer said 403 to a scan request
    dlg.pages[SECTION_SERVICES].refresh()
    assert not card.note_label.isHidden() and "cannot request library scans" in card.note_label.text()
    assert "Request library scans" in card.note_label.text()


def test_suwayomi_what_is_it_explains_and_set_up_waits(qapp, db, cache):
    told = []
    dlg, _ = make(qapp, db, cache, info=lambda parent, title, text: told.append((title, text)))
    page = dlg.pages[SECTION_SERVICES]
    page.suwayomi_card.btn_secondary.click()
    assert told and told[0][0] == "Suwayomi" and "Missing chapters" in told[0][1]
    assert page.suwayomi_card.btn_primary.toolTip() == "Connecting Suwayomi comes in a later version"


def test_edit_mangapixer_opens_its_panel_and_tells_the_shell_when_it_changed(qapp, db, cache):
    dlg, _ = make(qapp, db, cache)
    page = dlg.pages[SECTION_SERVICES]
    page.mp_card.btn_primary.click()
    panel = page.mp_panel
    assert panel is not None and page.stack.currentWidget() is page.editor_page
    panel.url_edit.setText("https://mangapixer.example")
    panel.token_edit.setText(TOKEN)
    assert panel.save_connection()
    assert dlg.result_data().mangapixer_changed
    assert panel.token_edit.text() == "" and TOKEN not in all_text(dlg)
    page.close_editor()
    assert page.mp_panel is None and page.stack.currentIndex() == 0
    assert "token: stored" in page.mp_card.detail_label.text() and TOKEN not in all_text(dlg)


def test_edit_qbittorrent_saves_with_a_write_only_password(qapp, db, cache):
    dlg, backend = make(qapp, db, cache)
    page = dlg.pages[SECTION_SERVICES]
    page.qbt_card.btn_primary.click()
    panel = page.qbt_panel
    assert panel is not None and panel.password_edit.text() == "" and panel.password_edit.echoMode() == QLineEdit.EchoMode.Password
    panel.url_edit.setText("http://qbt.example:9090")
    panel.password_edit.setText(PASSWORD)
    assert panel.save()
    assert backend.saved[-1][1] == PASSWORD and not dlg.pages[SECTION_SERVICES].stack.currentIndex()
    assert dlg.result_data().downloads_changed
    assert PASSWORD not in all_text(dlg)
    page.edit_qbittorrent()
    page.close_editor()                                                          # Cancel: nothing more saved
    assert len(backend.saved) == 1


# --- Download sources ---------------------------------------------------------------------------------------


def test_nyaa_options_are_shown_wired_or_fixed_and_stored_as_changed(qapp, db, cache):
    dlg, _ = make(qapp, db, cache)
    page = dlg.pages[SECTION_SOURCES]
    assert page.nyaa_badge.text() == "Ready" and page.nyaa_badge.property("badge") == "ok"
    assert page.on_check.isChecked() and page.english_check.isChecked() and not page.raw_check.isChecked()
    assert page.novels_check.isChecked() and not page.trusted_check.isChecked()
    for fixed in (page.digital_check, page.seeders_check):
        assert fixed.isChecked() and not fixed.isEnabled() and fixed.toolTip().startswith("Always on")
    page.raw_check.setChecked(True)
    page.trusted_check.setChecked(True)
    page.novels_check.setChecked(False)
    assert load_nyaa_options(db) == NyaaOptions(english=True, raw=True, hide_light_novels=False, trusted_only=True)
    assert dlg.result_data().downloads_changed
    page.english_check.setChecked(False)
    page.raw_check.setChecked(False)                                              # never a search of nothing
    assert page.english_check.isChecked() and load_nyaa_options(db).english
    page.on_check.setChecked(False)
    assert page.nyaa_badge.text() == "Off" and not load_nyaa_options(db).enabled
    again = SettingsDialog(None, db, FakeBackend(), cache=cache, env={})
    assert not again.pages[SECTION_SOURCES].on_check.isChecked()                   # it is stored
    again.reject()
    again.deleteLater()


def test_nyaa_needs_qbittorrent(qapp, db, cache):
    backend = FakeBackend()
    backend.settings = QbtSettings()
    dlg, _ = make(qapp, db, cache, backend)
    page = dlg.pages[SECTION_SOURCES]
    assert page.nyaa_badge.text() == "Needs qBittorrent" and page.nyaa_badge.property("badge") == "warn"
    backend.settings = QbtSettings(base_url="http://qbt.example:8080")
    page.on_show()
    assert page.nyaa_badge.text() == "Ready"


def test_suwayomi_sources_wait_for_suwayomi_and_point_to_the_services(qapp, db, cache):
    dlg, _ = make(qapp, db, cache, section=SECTION_SOURCES)
    page = dlg.pages[SECTION_SOURCES]
    assert "needs Suwayomi" in all_text(page) and "Suwayomi not set up" in all_text(page)
    page.btn_suwayomi.click()
    assert dlg.current == SECTION_SERVICES


# --- Matching and Automation -------------------------------------------------------------------------------


def test_matching_has_only_the_switch_that_works_and_it_is_the_old_auto_start_mu(qapp, db, cache):
    dlg, _ = make(qapp, db, cache)
    page = dlg.pages[SECTION_MATCHING]
    assert [box.text() for box in page.findChildren(QCheckBox)] == [page.mu_check.text()]
    assert not page.mu_check.isChecked() and "MangaUpdates" in page.mu_check.text()
    page.mu_check.setChecked(True)
    assert get_flag(db, KEY_MU_AUTOSTART) is True
    assert config.load()["mu_autostart"] is True                                  # what the window reads after a scan


def test_automation_shows_the_container_schedules_read_only(qapp, db, cache):
    dlg, _ = make(qapp, db, cache, env={"MANGALIST_RESCAN_SCHEDULE": "daily@02:15"})
    page = dlg.pages[SECTION_AUTOMATION]
    assert dict((what, label.text()) for what, label in page.schedule_labels) == {
        "Rescan the library": "daily 02:15", "Sync with MangaPixer": "daily 03:15",
        "File finished downloads": "every hour (and Check now)"}
    editors = [w for w in page.findChildren(QLineEdit)                  # read-only: no editor for the schedules
               if w is not page.holding_edit]                                    # (replaced chapters' own)
    assert editors == []
    assert "changed there" in all_text(page)
    assert "Automatic downloads" in all_text(page) and "next phase" in all_text(page)


def test_remove_completed_goes_to_the_backend_and_back(qapp, db, cache):
    dlg, backend = make(qapp, db, cache)
    page = dlg.pages[SECTION_AUTOMATION]
    assert page.remove_check.isChecked() and page.remove_check.isEnabled()
    page.remove_check.setChecked(False)
    assert backend.settings.remove_completed is False and backend.saved[-1][1] is None      # the fallback route
    assert dlg.result_data().downloads_changed
    calls = []
    backend.set_remove_completed = calls.append
    page.remove_check.setChecked(True)
    assert calls == [True]


def test_remove_completed_failure_is_shown_and_undone(qapp, db, cache):
    dlg, backend = make(qapp, db, cache)

    def refuse(on):
        raise BackendError("the settings table is locked")

    backend.set_remove_completed = refuse
    page = dlg.pages[SECTION_AUTOMATION]
    page.remove_check.setChecked(False)
    assert page.remove_check.isChecked() and "locked" in page.status_label.text()
    assert not dlg.result_data().downloads_changed


def test_ask_mangapixer_to_rescan_is_a_stored_switch_and_warns_when_the_token_cannot(qapp, db, cache):
    dlg, _ = make(qapp, db, cache)
    page = dlg.pages[SECTION_AUTOMATION]
    assert page.scan_check.isChecked() and page.scan_note.isHidden()
    page.scan_check.setChecked(False)
    assert get_flag(db, KEY_SCAN_AFTER_FILING) is False
    cache.scan_forbidden_at = lambda: "2026-10-08T10:00:00Z"
    page.on_show()
    assert not page.scan_note.isHidden() and "cannot request library scans" in page.scan_note.text()


def test_no_secret_is_in_any_text_of_the_dialog(qapp, db, cache):
    connect_mangapixer(cache)
    backend = FakeBackend()
    dlg, _ = make(qapp, db, cache, backend)
    for key in SECTIONS:
        dlg.show_section(key)
    settle(qapp, dlg)
    text = all_text(dlg) + " ".join(b.text() for b in dlg.findChildren(QCheckBox))
    assert TOKEN not in text and PASSWORD not in text and "mpx_" not in text


def test_sync_now_is_on_the_mangapixer_card_not_only_under_edit(qapp, db, cache, monkeypatch):
    from mangalist.services.mangapixer import sync as mp_sync

    synced = []

    def fake_sync_all(c, client=None, manual=False, **kw):
        synced.append((c is cache, manual))
        return mp_sync.SyncResult(status="ok", message="2 libraries, 5 items")
    monkeypatch.setattr(mp_sync, "sync_all", fake_sync_all)
    dlg, _ = make(qapp, db, cache, section=SECTION_SERVICES)
    page = dlg.pages[SECTION_SERVICES]
    assert page.mp_sync.text() == "Sync now" and not page.mp_sync.isEnabled()     # not connected yet
    cache.set_connection(base_url="mangapixer.example:8080", token="t")
    page.refresh()
    assert page.mp_sync.isEnabled()
    changed = []
    page.mangapixer_changed.connect(lambda: changed.append(1))
    assert page.sync_mangapixer()
    assert page.mp_sync.text() == "Syncing..." and not page.mp_sync.isEnabled()
    wait_until(qapp, lambda: not page._syncing)
    assert synced == [(True, True)] and changed == [1]
    assert page.mp_sync.isEnabled() and "Sync: 2 libraries, 5 items" in page.mp_card.note_label.text()
