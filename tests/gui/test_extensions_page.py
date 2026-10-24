"""Settings > Connected services with the built-in extensions (offscreen Qt): the chapter backend chooser, the host's card
(live status, Test, Edit), the Extensions page (the repository's list, Install / Update / Uninstall / Source settings
as BUTTONS - the owner uses an iPad) and the source settings editor (list, switch, multi-select, text, checkbox). The
REAL GUI backend talks to the in-process fake host over the loopback: no JVM, no network."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QCheckBox, QComboBox, QHeaderView, QLabel, QPushButton  # noqa: E402

from mangalist import store  # noqa: E402
from mangalist.downloads.adapter import Backend  # noqa: E402
from mangalist.gui.extensions_page import ExtensionsPanel, SourcePreferencesPanel  # noqa: E402
from mangalist.gui.settings_services import NOT_IN_USE, ServicesPage  # noqa: E402
from mangalist.store.downloads import ExtensionHostConfig  # noqa: E402

from ..services.extensions.fake_host import MANGADEX_EN, MANGADEX_PKG, WEEB_PKG, FakeHost  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401


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
    b = Backend(db)
    b.save_extension_host(ExtensionHostConfig(url=host.url))
    return b


def page_for(backend, db, told=None) -> ServicesPage:
    return ServicesPage(db, backend, None, info=lambda parent, title, text: (told if told is not None else []).append((title, text)))


def settle(qapp, widget) -> None:
    calls = getattr(widget, "_calls")
    wait_until(qapp, lambda: not (calls if isinstance(calls, list) else calls.calls))


def texts(widget) -> str:
    return "\n".join(w.text() for w in widget.findChildren(QLabel))


def test_the_chooser_switches_backends_and_the_cards_say_which_is_in_use(qapp, db, backend, host):
    told = []
    page = page_for(backend, db, told)
    assert page.chooser.isVisibleTo(page) and page.ext_card.isVisibleTo(page)
    assert page.backend_combo.currentData() == "suwayomi"
    assert [page.backend_combo.itemText(i) for i in range(page.backend_combo.count())] == ["Suwayomi",
                                                                                          "Built-in extensions"]
    assert page.ext_card.badge.text() == NOT_IN_USE and host.url in page.ext_card.detail_label.text()
    page.ext_info.click()
    assert told and told[0][0] == "Built-in extensions" and "Suwayomi" in told[0][1]
    changed = []
    page.downloads_changed.connect(lambda: changed.append(1))
    assert page.choose_backend("extensions")
    assert backend.chapter_backend() == "extensions" and changed
    assert page.suwayomi_card.badge.text() == NOT_IN_USE
    settle(qapp, page)                                          # choosing it tests the host at once
    assert page.ext_card.badge.text() == "Connected 0.2.0-fake" and page.ext_card.badge.property("badge") == "ok"
    assert "No extension is installed yet" in page.ext_card.note_label.text()
    assert str(host.downloads_path) in page.ext_card.detail_label.text()
    assert page.choose_backend("suwayomi") and page.ext_card.badge.text() == NOT_IN_USE
    assert page.suwayomi_card.badge.text() != NOT_IN_USE
    page.stop()


def test_switching_is_refused_while_chapters_are_in_progress(qapp, db, backend, monkeypatch):
    page = page_for(backend, db)
    monkeypatch.setattr(backend.chapter_ledger, "active", lambda: [object()])
    assert not page.choose_backend("extensions")
    assert "Not switched" in page.backend_note.text() and "still in progress with Suwayomi" in page.backend_note.text()
    assert page.backend_combo.currentData() == "suwayomi" and backend.chapter_backend() == "suwayomi"
    page.stop()


def test_the_host_down_is_said_on_its_card(qapp, db, backend):
    backend.save_extension_host(ExtensionHostConfig(url="http://127.0.0.1:9"))
    backend.set_chapter_backend("extensions")
    page = page_for(backend, db)
    page.on_show()
    settle(qapp, page)
    assert page.ext_card.badge.text() == "Not connected" and page.ext_card.badge.property("badge") == "bad"
    assert "could not be reached" in page.ext_card.note_label.text() and "starts by itself" in page.ext_card.note_label.text()
    page.stop()


def test_edit_tests_what_is_typed_and_saves(qapp, db, backend, host, tmp_path):
    page = page_for(backend, db)
    panel = page.edit_extension_host()
    assert page.stack.currentIndex() == 1 and panel.url_edit.text() == host.url
    assert "8191" in texts(panel) and panel.webview_check.isChecked()
    panel.flare_edit.setText("http://192.168.2.50:8191")
    panel.webview_check.setChecked(False)
    assert panel.test_connection()
    settle(qapp, panel)
    assert "Connected: extension host 0.2.0-fake" in panel.status_label.text()
    assert backend.load_extension_host().flaresolverr_url == ""                # nothing stored before Save
    panel.url_edit.setText("ftp://nope")
    assert not panel.save() and "Could not save" in panel.status_label.text()
    panel.url_edit.setText(host.url)
    assert panel.save()
    saved = backend.load_extension_host()
    assert saved.flaresolverr_url == "http://192.168.2.50:8191" and saved.webview is False
    assert page.stack.currentIndex() == 0                                    # saved: back to the cards
    page.stop()


def test_extensions_page_lists_and_installs_with_buttons(qapp, db, backend, host):
    host.repo.append(type(host.repo[0])("eu.kanade.tachiyomi.extension.ja.rawexample", "Raw Example", "1.4.1", "ja",
                                        [("77", "Raw Example", "ja")]))
    page = page_for(backend, db)
    panel = page.open_extensions()
    assert isinstance(panel, ExtensionsPanel) and page.stack.currentIndex() == 1
    settle(qapp, panel)
    table = panel.table
    header = table.horizontalHeader()
    assert all(header.sectionResizeMode(c) == QHeaderView.ResizeMode.Interactive for c in range(table.columnCount()))
    names = [table.item(r, 0).text() for r in range(table.rowCount())]
    assert names == ["MangaDex", "Weeb Example"]                             # English and multi-language only
    panel.all_langs_check.setChecked(True)
    assert table.rowCount() == 3
    panel.all_langs_check.setChecked(False)
    # The buttons are there without a right-click: nothing selected, nothing to do.
    buttons = {b.text(): b for b in panel.findChildren(QPushButton)}
    assert {"Install", "Update", "Uninstall", "Source settings", "Update all", "Reload"} <= set(buttons)
    assert not any(b.isEnabled() for b in (panel.btn_install, panel.btn_update, panel.btn_uninstall, panel.btn_prefs))
    assert panel.select(MANGADEX_PKG)
    assert panel.btn_install.isEnabled() and not panel.btn_uninstall.isEnabled()
    changed = []
    panel.changed.connect(lambda: changed.append(1))
    panel.btn_install.click()
    settle(qapp, panel)
    assert host.installed == {MANGADEX_PKG: "1.4.200"} and changed
    assert "Installed MangaDex from Keiyoushi." in panel.status_label.text()
    assert panel.selected().pkg == MANGADEX_PKG and panel.selected().installed
    assert table.item(0, 4).text() == "Installed"
    assert panel.btn_uninstall.isEnabled() and panel.btn_prefs.isEnabled() and not panel.btn_install.isEnabled()
    # A newer version in the repository: Update (and Update all).
    host.repo[0].version = "1.4.201"
    panel.reload()
    settle(qapp, panel)
    assert table.item(0, 4).text() == "Update: 1.4.200 → 1.4.201" and panel.btn_update_all.isEnabled()
    panel.select(MANGADEX_PKG)
    panel.btn_update.click()
    settle(qapp, panel)
    assert host.installed[MANGADEX_PKG] == "1.4.201" and table.item(0, 4).text() == "Installed"
    # Filter and installed only.
    panel.filter_edit.setText("weeb")
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == ["Weeb Example"]
    panel.filter_edit.setText("")
    panel.installed_check.setChecked(True)
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == ["MangaDex"]
    # Uninstall.
    panel.select(MANGADEX_PKG)
    panel.btn_uninstall.click()
    settle(qapp, panel)
    assert host.installed == {} and "Removed MangaDex." in panel.status_label.text()
    page.stop()


def test_a_failed_install_says_the_hosts_reason(qapp, db, backend, host):
    host.repo = [r for r in host.repo if r.pkg == WEEB_PKG]
    panel = ExtensionsPanel(backend)
    settle(qapp, panel)
    panel.select(WEEB_PKG)
    host.repo.clear()                                            # gone from the repository meanwhile
    panel.btn_install.click()
    settle(qapp, panel)
    assert panel.status_label.text().startswith("Weeb Example: no extension") and panel.btn_install.isEnabled()
    panel.stop()


def test_source_settings_edit_every_kind(qapp, db, backend, host):
    host.installed[MANGADEX_PKG] = "1.4.200"
    panel = ExtensionsPanel(backend)
    settle(qapp, panel)
    panel.select(MANGADEX_PKG)
    prefs = panel.open_preferences()
    assert isinstance(prefs, SourcePreferencesPanel) and prefs.source_id == MANGADEX_EN     # English first
    settle(qapp, prefs)
    assert [p.key for p in prefs.prefs] == ["coverQuality", "dataSaver", "contentRating", "blockedGroups", "altTitles"]
    combo = prefs.editors["coverQuality"]
    assert isinstance(combo, QComboBox) and [combo.itemText(i) for i in range(combo.count())] == [
        "Original", "Medium", "Low"]
    combo.setCurrentIndex(1)
    combo.activated.emit(1)
    settle(qapp, prefs)
    assert host.preferences[MANGADEX_EN][0]["currentValue"] == ".512.jpg"
    switch = prefs.editors["dataSaver"]
    assert isinstance(switch, QCheckBox) and not switch.isChecked()
    switch.setChecked(True)
    settle(qapp, prefs)
    assert host.preferences[MANGADEX_EN][1]["currentValue"] is True
    multi = prefs.editors["contentRating"]
    assert [b.isChecked() for b in multi.boxes] == [True, True, False]
    multi.boxes[2].setChecked(True)
    settle(qapp, prefs)
    assert host.preferences[MANGADEX_EN][2]["currentValue"] == ["safe", "suggestive", "erotica"]
    text = prefs.editors["blockedGroups"]
    text.edit.setText("Some Group")
    text.apply.click()
    settle(qapp, prefs)
    assert host.preferences[MANGADEX_EN][3]["currentValue"] == "Some Group"
    assert "Blocked groups: saved." in prefs.status_label.text()
    box = prefs.editors["altTitles"]
    assert box.isChecked()
    box.setChecked(False)
    settle(qapp, prefs)
    assert host.preferences[MANGADEX_EN][4]["currentValue"] is False
    # the other language's source has its own (none here)
    prefs.source_combo.setCurrentIndex(prefs.source_combo.findData("5"))
    settle(qapp, prefs)
    assert prefs.prefs == [] and "no settings" in prefs.status_label.text()
    prefs.btn_close.click()
    assert panel.prefs_panel is None
    panel.stop()

