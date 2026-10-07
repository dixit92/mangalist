"""The Downloads list (offscreen Qt, fake backend)."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.gui.downloads_dialog import DownloadsDialog  # noqa: E402

from .conftest import FakeBackend, qapp, record, wait_until  # noqa: E402,F401


def test_lists_records_newest_first_with_status_wording(qapp):
    backend = FakeBackend(records=[
        record(1, status=S.FILED), record(2, series_id=8, status=S.FAILED, error="no space left", title="Other v01",
                                          wanted=("1",)), record(3, series_id=9, status=S.REMOVED)])
    dlg = DownloadsDialog(backend, series_name=lambda i: {7: "Example Series"}.get(i, f"Series #{i}"))
    wait_until(qapp, lambda: dlg.records)
    rows = [[dlg.table.item(r, c).text() for c in range(6)] for r in range(dlg.table.rowCount())]
    assert [r[3] for r in rows] == ["Filed v03-v05 - done", "Failed: no space left", "Filed v03-v05 - seeding"]
    assert rows[2][:3] == ["Example Series", "Example Series v03-05", "v03-v05"] and rows[0][0] == "Series #9"
    assert threading.get_ident() not in backend.threads
    assert "Updated: 2026-10-07T10:05" in dlg.table.item(0, 3).toolTip()


def test_empty_list_is_explicit_and_refresh_picks_up_new_records(qapp):
    backend = FakeBackend()
    dlg = DownloadsDialog(backend)
    wait_until(qapp, lambda: dlg.btn_refresh.isEnabled())
    assert dlg.table.rowCount() == 0 and "Nothing has been sent" in dlg.status_label.text()
    backend.record_list.append(record(1))
    assert dlg.refresh()
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    assert dlg.table.item(0, 3).text() == "Sent" and dlg.status_label.text() == ""


def test_read_error_is_shown(qapp):
    backend = FakeBackend()

    def broken(series_id=None):
        from mangalist.gui.downloads_backend import BackendError

        raise BackendError("the downloads table is unreadable")

    backend.records = broken
    dlg = DownloadsDialog(backend)
    wait_until(qapp, lambda: dlg.btn_refresh.isEnabled())
    assert "Could not load the downloads: the downloads table is unreadable" in dlg.status_label.text()


def test_check_now_runs_the_check_off_the_ui_thread_then_reloads(qapp):
    backend = FakeBackend(records=[record(1, status=S.SENT)])
    dlg = DownloadsDialog(backend, series_name=lambda i: "Example Series")
    wait_until(qapp, lambda: dlg.btn_check.isEnabled())
    backend.record_list = [record(1, status=S.FILED)]          # what the check changed
    assert dlg.check_now() and not dlg.btn_check.isEnabled() and not dlg.btn_refresh.isEnabled()
    wait_until(qapp, lambda: dlg.btn_check.isEnabled() and dlg.table.rowCount() == 1
               and dlg.table.item(0, 3).text() == "Filed v03-v05 - seeding")
    assert backend.checks == 1 and threading.get_ident() not in backend.threads
    assert dlg.status_label.text() == "Checked now: 1 checked: 1 filed, 0 removed, 0 failed, 0 waiting."


def test_check_now_failure_is_shown_and_the_list_kept(qapp):
    backend = FakeBackend(records=[record(1, status=S.SENT)])
    dlg = DownloadsDialog(backend, series_name=lambda i: "Example Series")
    wait_until(qapp, lambda: dlg.btn_check.isEnabled())
    backend.check_error = "qBittorrent could not be reached"
    dlg.check_now()
    wait_until(qapp, lambda: dlg.btn_check.isEnabled())
    assert "Could not check the downloads: qBittorrent could not be reached" in dlg.status_label.text()
    assert dlg.table.rowCount() == 1
