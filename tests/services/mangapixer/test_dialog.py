"""The MangaPixer settings panel and its dialog (offscreen Qt) against the local fake server."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from mangalist.gui.mangapixer_dialog import TOKEN_STORED_HINT, MangaPixerDialog, MangaPixerPanel  # noqa: E402
from mangalist.services.mangapixer.client import MangaPixerClient  # noqa: E402

from .conftest import TOKEN, add_root_with_series, folder  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _factory(sleeps):
    return lambda url, token, verify: MangaPixerClient(url, token, verify=verify, sleep=sleeps.append, timeout=5)


def _wait(dlg, seconds=20):
    end = time.monotonic() + seconds
    while dlg.syncing and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not dlg.syncing


def test_token_is_masked_and_never_shown_again(qapp, cache, fake, sleeps):
    dlg = MangaPixerPanel(cache, client_factory=_factory(sleeps))
    assert dlg.token_edit.echoMode() == QLineEdit.EchoMode.PasswordEchoOnEdit
    dlg.url_edit.setText(fake.url)
    dlg.token_edit.setText(TOKEN)
    assert dlg.test_connection()
    assert "Connected" in dlg.status_label.text() and "1 libraries" in dlg.status_label.text()
    assert dlg.save_connection()
    assert cache.token() == TOKEN
    assert dlg.token_edit.text() == "" and dlg.token_edit.placeholderText() == TOKEN_STORED_HINT
    again = MangaPixerPanel(cache, client_factory=_factory(sleeps))
    assert again.token_edit.text() == "" and TOKEN not in again.status_label.text()
    assert again.test_connection()                          # uses the stored token


def test_wrong_token_is_reported_once(qapp, cache, fake, sleeps):
    dlg = MangaPixerPanel(cache, client_factory=_factory(sleeps))
    dlg.url_edit.setText(fake.url)
    dlg.token_edit.setText("mpx_wrong")
    assert not dlg.test_connection()
    assert "refused the token" in dlg.status_label.text()
    assert len(fake.requests) == 1 and "mpx_wrong" not in dlg.status_label.text()


def test_bad_address(qapp, cache, sleeps):
    dlg = MangaPixerPanel(cache, client_factory=_factory(sleeps))
    dlg.url_edit.setText("ftp://nowhere")
    assert not dlg.save_connection() and "http" in dlg.status_label.text()


def test_sync_now_mapping_and_override(qapp, cache, fake, sleeps, db, tmp_path):
    fake.items["lib0manga"] = [folder("n1", ["Shonen", "One"]), folder("n2", ["Shonen", "Two"])]
    fake.libraries.append({"id": "lib1", "displayName": "Comics", "kind": "comic"})
    fake.items["lib1"] = [folder("c1", ["One"])]
    root = add_root_with_series(db, tmp_path, "Shonen", ["One", "Two", "Three"])
    dlg = MangaPixerPanel(cache, client_factory=_factory(sleeps))
    dlg.url_edit.setText(fake.url)
    dlg.token_edit.setText(TOKEN)
    assert dlg.sync_now()
    _wait(dlg)
    assert dlg.last_result.status == "ok", dlg.last_result.message
    assert dlg.sync_label.text().startswith("Last sync:")
    assert dlg.lib_table.rowCount() == 2
    assert dlg.lib_table.item(1, 3).text() == "no (kind)"
    assert [dlg.map_table.item(0, c).text() for c in range(5)] == ["Shonen", "Manga (automatic)", "Shonen", "2", "1"]

    dlg.map_table.selectRow(0)
    dlg.lib_combo.setCurrentIndex(dlg.lib_combo.findData("lib1"))
    dlg.prefix_edit.clear()
    dlg.any_kind_check.setChecked(True)
    assert dlg.apply_override()
    m = cache.mapping(root.id)
    assert (m.library_id, m.manual, m.any_kind) == ("lib1", True, True)
    assert dlg.map_table.item(0, 1).text() == "Comics (manual)"
    dlg.map_table.selectRow(0)
    dlg.lib_combo.setCurrentIndex(dlg.lib_combo.findData("__auto__"))
    assert dlg.apply_override()
    assert cache.mapping(root.id).library_id == "lib0manga" and not cache.mapping(root.id).manual
    dlg.stop()


def test_table_columns_can_be_resized_and_headers_are_not_cut(qapp, cache, sleeps):
    from PySide6.QtWidgets import QHeaderView

    dlg = MangaPixerPanel(cache, client_factory=_factory(sleeps))
    dlg.resize(900, 600)
    dlg.show()
    qapp.processEvents()
    for table in (dlg.lib_table, dlg.map_table):
        header = table.horizontalHeader()
        assert header.stretchLastSection()
        for col in range(table.columnCount()):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
        for col in range(table.columnCount() - 1):      # the last one fills the rest
            assert header.sectionSize(col) >= header.sectionSizeHint(col), table.horizontalHeaderItem(col).text()
    dlg.close()


def test_the_panel_says_what_changed_and_warns_when_the_token_cannot_request_scans(qapp, cache, fake, sleeps):
    panel = MangaPixerPanel(cache, client_factory=_factory(sleeps))
    changed = []
    panel.changed.connect(lambda: changed.append(True))
    panel.url_edit.setText(fake.url)
    panel.token_edit.setText(TOKEN)
    assert panel.save_connection() and changed == [True]
    assert not panel.scan_label.isVisibleTo(panel)
    cache.scan_forbidden_at = lambda: "2026-10-08T10:00:00Z"        # MangaPixer answered 403 to a scan request
    panel._load()
    assert panel.scan_label.isVisibleTo(panel) and "cannot request library scans" in panel.scan_label.text()
    panel.forget_token()
    assert len(changed) == 2


def test_the_dialog_wraps_the_panel(qapp, cache, sleeps):
    dlg = MangaPixerDialog(cache, client_factory=_factory(sleeps))
    assert dlg.panel.url_edit.text() == "" and dlg.windowTitle() == "MangaPixer source"
    dlg.reject()
    dlg.deleteLater()
