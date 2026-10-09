"""The downloads list ("In progress") and the dialog around it (offscreen Qt, fake backend)."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel  # noqa: E402

from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.gui.downloads_dialog import DownloadsDialog  # noqa: E402
from mangalist.gui.downloads_list import DownloadsList  # noqa: E402

from .conftest import FakeBackend, qapp, record, wait_until  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _close_lists():
    made = []
    original = DownloadsList.__init__

    def tracking(self, *a, **kw):
        original(self, *a, **kw)
        made.append(self)

    DownloadsList.__init__ = tracking
    yield
    DownloadsList.__init__ = original
    for item in made:
        item.stop()
        item.deleteLater()


def test_lists_records_newest_first_with_status_wording(qapp):
    backend = FakeBackend(records=[
        record(1, status=S.FILED), record(2, series_id=8, status=S.FAILED, error="no space left", title="Other v01",
                                          wanted=("1",)), record(3, series_id=9, status=S.REMOVED)])
    dlg = DownloadsList(backend, series_name=lambda i: {7: "Example Series"}.get(i, f"Series #{i}"))
    wait_until(qapp, lambda: dlg.records)
    rows = [[dlg.table.item(r, 0).text(), dlg.table.item(r, 1).text(), dlg.table.cellWidget(r, 2).findChild(QLabel).text(),
             dlg.table.cellWidget(r, 2).findChild(QLabel).property("badge")] for r in range(dlg.table.rowCount())]
    assert [r[2] for r in rows] == ["Filed v03-v05 - done", "Failed: no space left", "Filed v03-v05 - seeding"]
    assert [r[3] for r in rows] == ["done", "bad", "ok"]                      # the badge colour of each status
    assert rows[2][:2] == ["Example Series", "Example Series v03-05"] and rows[0][0] == "Series #9"
    assert threading.get_ident() not in backend.threads
    assert "Updated: 2026-10-07T10:05" in dlg.table.item(0, 0).toolTip()
    assert [dlg.table.horizontalHeaderItem(c).text() for c in range(4)] == ["SERIES", "RELEASE", "STATUS", "UPDATED"]


def test_empty_list_is_explicit_and_refresh_picks_up_new_records(qapp):
    backend = FakeBackend()
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.btn_refresh.isEnabled())
    assert dlg.table.rowCount() == 0 and "Nothing has been sent" in dlg.status_label.text()
    backend.record_list.append(record(1))
    assert dlg.refresh()
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    assert dlg.table.cellWidget(0, 2).findChild(QLabel).text() == "Sent" and dlg.status_label.text() == ""


def test_read_error_is_shown(qapp):
    backend = FakeBackend()

    def broken(series_id=None):
        from mangalist.gui.downloads_backend import BackendError

        raise BackendError("the downloads table is unreadable")

    backend.records = broken
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.btn_refresh.isEnabled())
    assert "Could not load the downloads: the downloads table is unreadable" in dlg.status_label.text()


def test_check_now_runs_the_check_off_the_ui_thread_then_reloads(qapp):
    backend = FakeBackend(records=[record(1, status=S.SENT)])
    dlg = DownloadsList(backend, series_name=lambda i: "Example Series")
    wait_until(qapp, lambda: dlg.btn_check.isEnabled())
    backend.record_list = [record(1, status=S.FILED)]          # what the check changed
    finished = []
    dlg.check_finished.connect(finished.append)
    assert dlg.check_now() and not dlg.btn_check.isEnabled() and not dlg.btn_refresh.isEnabled()
    wait_until(qapp, lambda: dlg.btn_check.isEnabled() and dlg.table.rowCount() == 1 and finished
               and dlg.table.cellWidget(0, 2).findChild(QLabel).text() == "Filed v03-v05 - seeding")
    assert finished == [True]
    assert backend.checks == 1 and threading.get_ident() not in backend.threads
    assert dlg.status_label.text() == "Checked now: 1 checked: 1 filed, 0 removed, 0 failed, 0 waiting."


def test_check_now_failure_is_shown_and_the_list_kept(qapp):
    backend = FakeBackend(records=[record(1, status=S.SENT)])
    dlg = DownloadsList(backend, series_name=lambda i: "Example Series")
    wait_until(qapp, lambda: dlg.btn_check.isEnabled())
    backend.check_error = "qBittorrent could not be reached"
    dlg.check_now()
    wait_until(qapp, lambda: dlg.btn_check.isEnabled() and dlg._call is None)
    assert "Could not check the downloads: qBittorrent could not be reached" in dlg.status_label.text()
    assert dlg.table.rowCount() == 1


def test_names_and_next_check_come_from_the_backend_when_it_has_them(qapp):
    backend = FakeBackend(records=[record(1, series_id=7)])
    backend.series_titles = lambda ids: {7: "Folder Name"}
    backend.next_check = lambda: "2026-10-08T11:21:00+00:00"
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.records)
    assert dlg.table.item(0, 0).text() == "Folder Name"
    assert dlg.next_label.text().startswith("Next automatic check ")
    dlg.set_known_titles({8: "Other"})
    assert dlg.table.item(0, 0).text() == "Folder Name"                      # the backend's name wins


def test_the_dialog_wraps_the_list(qapp):
    backend = FakeBackend(records=[record(1)])
    dlg = DownloadsDialog(backend, series_name=lambda i: "Example Series")
    wait_until(qapp, lambda: dlg.records)
    assert dlg.list.table.item(0, 0).text() == "Example Series" and dlg.windowTitle() == "Downloads"
    dlg.reject()
    dlg.deleteLater()


def test_remove_now_from_the_row_menu_after_a_yes(qapp):
    stopped = record(1, status=S.FILED, error="stopped before its seed goal (ratio 0.40 of 2)")
    backend = FakeBackend(records=[stopped, record(2, status=S.SENT)])
    asked = []
    dlg = DownloadsList(backend, confirm_remove=lambda rec: asked.append(rec.id) or True)
    wait_until(qapp, lambda: dlg.table.rowCount() == 2)
    assert "stopped before its seed goal" in dlg.table.cellWidget(1, 2).findChild(QLabel).text()
    menus = []

    def answer(menu, pos):
        acts = {a.text(): a for a in menu.actions()}
        menus.append({t: a.isEnabled() for t, a in acts.items()})
        return acts["Remove now…"]
    dlg._exec_menu = answer
    sent_row = dlg.table.visualItemRect(dlg.table.item(0, 0)).center()
    dlg._on_context_menu(sent_row)                                   # newest first: the SENT one is row 0
    filed_row = dlg.table.visualItemRect(dlg.table.item(1, 0)).center()
    dlg._on_context_menu(filed_row)
    assert menus == [{"Remove now…": False}, {"Remove now…": True}] and asked == [1]
    wait_until(qapp, lambda: dlg._call is None and dlg.btn_refresh.isEnabled())
    assert backend.removed_now == [1] and "Removed from qBittorrent" in dlg.status_label.text()


def test_remove_now_asks_and_a_no_removes_nothing(qapp):
    backend = FakeBackend(records=[record(1, status=S.FILED)])
    dlg = DownloadsList(backend, confirm_remove=lambda rec: False)
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    assert not dlg.remove_now(backend.record_list[0]) and not getattr(backend, "removed_now", [])
