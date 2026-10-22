"""Extension updates and repositories in the GUI (lane U, 2026-10-22; offscreen Qt): the repository list editor in
Built-in extensions > Edit (Add - checked by the host first - and Remove, as buttons), Auto-update, the Extensions page's
Repository column and filter (the same extension in two repositories), the update notice on the Built-in extensions card
(with Check for updates) and in the Download tab (Open Extensions), the window's check at start, and the Settings
dialog opening on the Extensions page. The REAL GUI backend talks to the in-process fake host: no JVM, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mangalist import store  # noqa: E402
from mangalist.downloads import extension_updates as eu  # noqa: E402
from mangalist.downloads.adapter import Backend  # noqa: E402
from mangalist.downloads.contracts import CHAPTER_BACKEND_EXTENSIONS  # noqa: E402
from mangalist.gui.download_tab import DownloadTab  # noqa: E402
from mangalist.gui.extension_notice import SECTION_EXTENSIONS  # noqa: E402
from mangalist.gui.extensions_page import ExtensionHostPanel, ExtensionsPanel  # noqa: E402
from mangalist.gui.settings_dialog import SettingsDialog  # noqa: E402
from mangalist.gui.settings_services import ServicesPage  # noqa: E402
from mangalist.services.extensions import ExtensionHostClient  # noqa: E402
from mangalist.store.downloads import ExtensionHostConfig  # noqa: E402

from ..services.extensions.fake_host import (  # noqa: E402
    KEI_URL, MANGADEX_EN, MANGADEX_PKG, SECOND_KEY, SECOND_URL, WEEB_PKG, FakeHost, RepoEntry,
)
from .conftest import qapp, wait_until  # noqa: E402,F401


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "exthost", auto_download=False).start()
    yield h
    h.stop()


@pytest.fixture
def backend(db, host):
    b = Backend(db)
    b.save_extension_host(ExtensionHostConfig(url=host.url))
    b.set_chapter_backend(CHAPTER_BACKEND_EXTENSIONS)
    return b


def settle(qapp, widget) -> None:
    calls = getattr(widget, "_calls")
    wait_until(qapp, lambda: not (calls if isinstance(calls, list) else calls.calls))


def stored_check(db, host, **versions):
    host.installed.update(versions)
    client = ExtensionHostClient(host.url, timeout=10)
    try:
        return eu.run_check(db, client)
    finally:
        client.close()


def second_repository_with_a_duplicate(host) -> None:
    """The second repository lists MangaDex too (a newer build) and one of its own."""
    host.repo.append(RepoEntry(MANGADEX_PKG, "MangaDex", "1.4.300", "all", [(MANGADEX_EN, "MangaDex", "en")],
                               repo=SECOND_URL))
    host.repo.append(RepoEntry("eu.kanade.tachiyomi.extension.en.secondonly", "Second Only", "1.4.2", "en",
                               [("9100", "Second Only", "en")], repo=SECOND_URL))


# --- the repository list --------------------------------------------------------------------------------------------

def test_the_repository_list_adds_checked_repositories_and_removes_with_buttons(qapp, db, backend, host):
    panel = ExtensionHostPanel(backend)
    editor = panel.repos_editor
    settle(qapp, editor)
    assert [editor.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(editor.list.count())] == [KEI_URL]
    assert "Keiyoushi · 2 extensions · key 9add655a…" in editor.list.item(0).text()
    assert not editor.btn_add.isEnabled() and not editor.btn_remove.isEnabled()         # nothing typed / selected
    # not a repository: the host refuses it, nothing changes
    editor.url_edit.setText("https://example.invalid/page.html")
    assert editor.btn_add.isEnabled()
    editor.btn_add.click()
    settle(qapp, editor)
    assert editor.status_label.text().startswith("Not added:") and "not an extension repository" in \
        editor.status_label.text()
    assert list(host.repositories) == [KEI_URL] and backend.load_extension_host().repos is None
    # a real one: listed, selected, remembered for the host's next start
    editor.url_edit.setText(SECOND_URL)
    editor.btn_add.click()
    settle(qapp, editor)
    assert list(host.repositories) == [KEI_URL, SECOND_URL]
    assert editor.status_label.text().startswith("Added Second:") and SECOND_KEY[:8] in editor.status_label.text()
    assert editor.selected_url() == SECOND_URL and editor.url_edit.text() == ""
    assert backend.load_extension_host().repos == (KEI_URL, SECOND_URL)
    # Remove the selected one
    assert editor.btn_remove.isEnabled()
    editor.btn_remove.click()
    settle(qapp, editor)
    assert list(host.repositories) == [KEI_URL] and backend.load_extension_host().repos == (KEI_URL,)
    assert "Removed Second. Extensions installed from it stay installed" in editor.status_label.text()
    # Save keeps the list (it is not part of the form) and stores Auto-update
    assert not panel.auto_update_check.isChecked() and backend.extension_auto_update() is False
    panel.auto_update_check.setChecked(True)
    assert panel.save()
    assert backend.extension_auto_update() is True and backend.load_extension_host().repos == (KEI_URL,)
    panel.stop()


def test_the_repository_list_says_when_the_host_is_down(qapp, db):
    b = Backend(db)
    b.suwayomi.save_extension_host(ExtensionHostConfig(url="http://127.0.0.1:9", repos=(KEI_URL,)))
    panel = ExtensionHostPanel(b)
    settle(qapp, panel.repos_editor)
    editor = panel.repos_editor
    assert "could not be read" in editor.status_label.text() and editor.list.item(0).text() == KEI_URL
    panel.stop()


# --- the Extensions page: Repository column and filter ----------------------------------------------------------------

def test_the_same_extension_in_two_repositories_is_two_rows_and_installs_from_the_one_chosen(qapp, db, backend, host):
    second_repository_with_a_duplicate(host)
    backend.add_extension_repository(SECOND_URL)
    panel = ExtensionsPanel(backend)
    settle(qapp, panel)
    table = panel.table
    assert [table.horizontalHeaderItem(c).text().casefold() for c in range(table.columnCount())] == [
        "extension", "language", "repository", "version", "status"]
    shown = [(table.item(r, 0).text(), table.item(r, 2).text(), table.item(r, 3).text()) for r in range(table.rowCount())]
    assert shown == [("MangaDex", "Keiyoushi", "1.4.200"), ("MangaDex", "Second", "1.4.300"),
                     ("Second Only", "Second", "1.4.2"), ("Weeb Example", "Keiyoushi", "1.4.3")]
    assert "Keiyoushi, Second" in panel.repo_label.text() and panel.repo_combo.isVisibleTo(panel)
    # the repository filter
    assert [panel.repo_combo.itemText(i) for i in range(panel.repo_combo.count())] == [
        "All repositories", "Keiyoushi", "Second"]
    assert panel.set_repository_filter(SECOND_URL)
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == ["MangaDex", "Second Only"]
    # install MangaDex from the second repository
    assert panel.select(MANGADEX_PKG, SECOND_URL)
    panel.btn_install.click()
    settle(qapp, panel)
    assert host.installed == {MANGADEX_PKG: "1.4.300"} and host.installed_from[MANGADEX_PKG] == SECOND_URL
    assert "Installed MangaDex from Second." in panel.status_label.text()
    panel.set_repository_filter("")
    status = {(table.item(r, 0).text(), table.item(r, 2).text()): table.item(r, 4).text()
              for r in range(table.rowCount())}
    assert status[("MangaDex", "Second")] == "Installed"
    assert status[("MangaDex", "Keiyoushi")] == "Installed from Second"
    assert panel.select(MANGADEX_PKG, KEI_URL)
    assert not panel.btn_install.isEnabled() and not panel.btn_update.isEnabled()      # only from Second
    assert not panel.btn_uninstall.isEnabled()
    assert panel.select(MANGADEX_PKG, SECOND_URL) and panel.btn_uninstall.isEnabled()
    # the second repository removed: MangaDex stays installed, with no updates
    backend.remove_extension_repository(SECOND_URL)
    panel.reload()
    settle(qapp, panel)
    rows = {(table.item(r, 0).text(), table.item(r, 2).text()): table.item(r, 4).text() for r in range(table.rowCount())}
    assert rows[("MangaDex", "Second")] == "Installed (its repository was removed: no updates)"
    panel.stop()


# --- the notices -------------------------------------------------------------------------------------------------------

def test_the_built_in_extensions_card_says_what_the_last_check_found_and_checks_on_demand(qapp, db, backend, host):
    page = ServicesPage(db, backend, None, info=lambda *a: None)
    try:
        assert "not checked yet" in page.extension_updates_text()
        stored_check(db, host, **{MANGADEX_PKG: "1.4.199"})
        page.refresh()
        assert page.extension_updates_text().startswith("1 extension update: MangaDex 1.4.199 → 1.4.200")
        assert page.ext_updates_label.property("tone") == "warn"
        host.installed[MANGADEX_PKG] = "1.4.200"                    # updated meanwhile; Check for updates asks now
        assert page.check_extension_updates()
        settle(qapp, page)
        assert page.extension_updates_text().startswith("Extensions up to date (1 installed)")
        # Suwayomi chosen: no notice
        backend.set_chapter_backend("suwayomi")
        page.refresh()
        assert page.extension_updates_text() == ""
    finally:
        page.stop()


def test_the_download_tab_says_how_many_updates_wait_and_opens_the_extensions(qapp, db, backend, host):
    stored_check(db, host, **{MANGADEX_PKG: "1.4.199", WEEB_PKG: "1.4.1"})
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0, autostart=False)
    try:
        tab.refresh_extension_notice()
        bar = tab.extensions_bar
        assert bar.isVisibleTo(tab)
        assert bar.text_label.text() == "2 extension updates: MangaDex 1.4.200, Weeb Example 1.4.3"
        asked = []
        tab.settings_requested.connect(asked.append)
        bar.open_button.click()
        assert asked == [SECTION_EXTENSIONS]
        # Auto-update did them: the line says so
        backend.set_extension_auto_update(True)
        backend.check_extension_updates()
        tab.refresh_extension_notice()
        assert bar.text_label.text() == "2 extensions updated automatically: MangaDex 1.4.200, Weeb Example 1.4.3"
        # Suwayomi chosen: hidden
        backend.set_chapter_backend("suwayomi")
        tab.refresh_extension_notice()
        assert not bar.isVisibleTo(tab)
    finally:
        tab.stop()
        tab.deleteLater()


def test_the_window_checks_at_start_when_the_stored_result_is_older_than_the_schedule(qapp, db, backend, host):
    host.installed[WEEB_PKG] = "1.4.1"
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0, autostart=False)
    try:
        assert tab.check_extension_updates_if_due()                 # never checked: due
        wait_until(qapp, lambda: tab._ext_check_call is None)
        assert eu.read_check(db).count == 1 and tab.extensions_bar.isVisibleTo(tab)
        requests = len(host.requests)
        assert tab.check_extension_updates_if_due()                 # checked a moment ago: not due, the host not asked
        wait_until(qapp, lambda: tab._ext_check_call is None)
        assert [r for r in host.requests[requests:] if r[1] == "/extensions"] == []
        # a day old: asked again
        old = eu.read_check(db)
        old.checked_at = (datetime.now(timezone.utc) - timedelta(days=1, minutes=1)).isoformat()
        eu.save_check(db, old)
        assert tab.check_extension_updates_if_due()
        wait_until(qapp, lambda: tab._ext_check_call is None)
        assert ("GET", "/extensions") in host.requests[requests:]
    finally:
        tab.stop()
        tab.deleteLater()


def test_the_settings_dialog_opens_on_the_extensions_page(qapp, db, backend, host):
    dlg = SettingsDialog(None, db, backend, SECTION_EXTENSIONS, env={}, info=lambda *a: None)
    try:
        page = dlg.pages["services"]
        assert dlg.current == "services" and isinstance(page.extensions_panel, ExtensionsPanel)
        settle(qapp, page.extensions_panel)
    finally:
        dlg.done(0)
        dlg.deleteLater()
