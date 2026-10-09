"""The qBittorrent settings panel and its dialog (offscreen Qt, fake backend): defaults, the write-only password, validation, the
connection test off the UI thread, and the error paths."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLineEdit  # noqa: E402

from mangalist.gui.downloads_backend import BackendError, QbtSettings  # noqa: E402
from mangalist.gui.qbittorrent_dialog import (  # noqa: E402
    PASSWORD_EMPTY_HINT, PASSWORD_STORED_HINT, QbittorrentDialog, QbittorrentPanel, normalize_url,
)

from .conftest import FakeBackend, qapp, wait_until  # noqa: E402,F401


def test_loads_the_saved_settings_and_never_shows_the_password(qapp):
    backend = FakeBackend()
    dlg = QbittorrentPanel(backend)
    assert dlg.url_edit.text() == "http://qbt.example:8080" and dlg.user_edit.text() == "owner"
    assert not dlg.tls_check.isChecked() and dlg.remove_check.isChecked()
    assert dlg.save_path_edit.text() == "/data/appdata/torrents/mangalist"
    assert dlg.password_edit.echoMode() == QLineEdit.EchoMode.Password
    assert dlg.password_edit.text() == "" and dlg.password_edit.placeholderText() == PASSWORD_STORED_HINT
    assert "Leave empty to keep" in PASSWORD_STORED_HINT


def test_fresh_install_defaults(qapp):
    backend = FakeBackend()
    backend.settings = QbtSettings()
    dlg = QbittorrentPanel(backend)
    assert dlg.url_edit.text() == "" and dlg.tls_check.isChecked() and dlg.remove_check.isChecked()
    assert dlg.save_path_edit.text() == "/data/appdata/torrents/mangalist"
    assert dlg.password_edit.placeholderText() == PASSWORD_EMPTY_HINT


def test_remove_completed_is_explained(qapp):
    dlg = QbittorrentPanel(FakeBackend())
    text = dlg.remove_help.text()
    assert "Sonarr / Radarr" in text and "seed goal" in text and "\"mangalist\" category" in text


def test_save_keeps_the_stored_password_when_the_field_is_empty(qapp):
    backend = FakeBackend()
    dlg = QbittorrentPanel(backend)
    dlg.url_edit.setText(" http://qbt.example:8080/ ")
    dlg.remove_check.setChecked(False)
    assert dlg.save() and dlg.saved
    settings, password = backend.saved[0]
    assert password is None and settings.base_url == "http://qbt.example:8080" and not settings.remove_completed


def test_a_new_password_is_sent_once_then_cleared_from_the_field(qapp):
    backend = FakeBackend()
    backend.settings = QbtSettings(base_url="http://qbt.example:8080")
    dlg = QbittorrentPanel(backend)
    dlg.password_edit.setText("hunter2-example")
    assert dlg.save()
    assert backend.saved[0][1] == "hunter2-example"
    assert dlg.password_edit.text() == "" and dlg.password_edit.placeholderText() == PASSWORD_STORED_HINT
    dlg2 = QbittorrentPanel(backend)                                  # reopened: still hidden
    assert dlg2.password_edit.text() == "" and dlg2.password_edit.placeholderText() == PASSWORD_STORED_HINT
    assert "hunter2" not in dlg.status_label.text() + dlg2.status_label.text() + repr(backend.settings)


@pytest.mark.parametrize("text, ok", [("http://192.168.1.10:8080", True), ("https://qbt.example/", True),
                                      ("192.168.1.10:8080", False), ("ftp://x", False), ("http://", False), ("  ", False)])
def test_address_validation(text, ok):
    if ok:
        assert normalize_url(text) == text.rstrip("/")
    else:
        with pytest.raises(ValueError):
            normalize_url(text)


def test_invalid_input_is_reported_and_nothing_is_saved(qapp):
    backend = FakeBackend()
    dlg = QbittorrentPanel(backend)
    dlg.url_edit.setText("qbt.example")
    assert not dlg.save() and "http://" in dlg.status_label.text() and backend.saved == []
    dlg.url_edit.setText("http://qbt.example:8080")
    dlg.save_path_edit.setText("  ")
    assert not dlg.save() and "download folder" in dlg.status_label.text() and backend.saved == []


def test_test_connection_runs_off_the_ui_thread_with_the_typed_values(qapp):
    backend = FakeBackend()
    dlg = QbittorrentPanel(backend)
    dlg.url_edit.setText("http://other.example:9090")
    dlg.password_edit.setText("typed-secret")
    assert dlg.test_connection() and not dlg.btn_test.isEnabled()
    wait_until(qapp, lambda: dlg.btn_test.isEnabled())
    settings, password = backend.tested[0]
    assert settings.base_url == "http://other.example:9090" and password == "typed-secret"
    assert backend.threads and threading.get_ident() not in backend.threads
    assert "Connected. qBittorrent v5.2.4." in dlg.status_label.text() and "typed-secret" not in dlg.status_label.text()
    assert backend.saved == []                                         # a test does not save
    assert dlg.password_edit.text() == "typed-secret"                  # and does not eat the typing


def test_test_connection_error_is_shown(qapp):
    backend = FakeBackend()
    backend.test_error = "wrong username or password"
    dlg = QbittorrentPanel(backend)
    dlg.test_connection()
    wait_until(qapp, lambda: dlg.btn_test.isEnabled())
    assert "Connection failed: wrong username or password" in dlg.status_label.text()


def test_unexpected_test_error_shows_only_the_type(qapp):
    backend = FakeBackend()

    def boom(settings, password):
        raise ValueError("login http://owner:topsecret@qbt.example failed")

    backend.test_connection = boom
    dlg = QbittorrentPanel(backend)
    dlg.test_connection()
    wait_until(qapp, lambda: dlg.btn_test.isEnabled())
    assert "unexpected error (ValueError)" in dlg.status_label.text() and "topsecret" not in dlg.status_label.text()


def test_save_error_keeps_the_dialog_open(qapp):
    backend = FakeBackend()

    def refuse(settings, password):
        raise BackendError("the secrets table is locked")

    backend.save_settings = refuse
    dlg = QbittorrentDialog(backend)
    dlg.accept()
    assert "Could not save: the secrets table is locked" in dlg.panel.status_label.text() and not dlg.saved
    assert dlg.result() == 0


def test_accept_saves_and_closes(qapp):
    backend = FakeBackend()
    dlg = QbittorrentDialog(backend)
    dlg.accept()
    assert dlg.saved and dlg.result() == 1 and len(backend.saved) == 1


def test_the_panel_says_when_it_saved_and_when_the_test_worked(qapp):
    backend = FakeBackend()
    panel = QbittorrentPanel(backend)
    seen = []
    panel.saved_changes.connect(lambda: seen.append("saved"))
    panel.tested.connect(lambda v: seen.append(v))
    assert panel.save()
    panel.test_connection()
    wait_until(qapp, lambda: panel.btn_test.isEnabled())
    assert seen == ["saved", "v5.2.4"]
