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


def chip(dlg, row):
    return dlg.table.cellWidget(row, 2).findChild(QLabel, "statusChip")


def reason(dlg, row):
    detail = dlg.table.cellWidget(row, 2).findChild(QLabel, "statusDetail")
    return detail.full_text() if detail is not None else ""


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
    dlg.toggle_finished()                                                       # the finished one too
    rows = [[dlg.table.item(r, 0).text(), dlg.table.item(r, 1).text(), chip(dlg, r).text(), chip(dlg, r).property("badge")]
            for r in range(dlg.table.rowCount())]
    assert [r[2] for r in rows] == ["Filed v03-v05 - done", "Failed", "Filed v03-v05 - seeding"]
    assert [reason(dlg, r) for r in range(3)] == ["", "no space left", ""]
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
    assert dlg.table.cellWidget(0, 2).findChild(QLabel, "statusChip").text() == "Downloading" and dlg.status_label.text() == ""


def test_finished_downloads_are_hidden_until_asked(qapp):
    """Owner, 2026-10-10: "do they just stay in the In progress table? They are not in progress anymore"."""
    backend = FakeBackend(records=[record(1, status=S.FILED), record(2, series_id=8, status=S.FAILED, error="no space"),
                                   record(3, series_id=9, status=S.REMOVED), record(4, series_id=9, status=S.CANCELLED)])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.records)
    assert dlg.table.rowCount() == 2 and [r.id for r in dlg.shown_records()] == [2, 1]     # failed stays in view
    assert dlg.btn_finished.isVisibleTo(dlg) and dlg.btn_finished.text() == "Show finished (2)"
    dlg.btn_finished.click()
    assert dlg.table.rowCount() == 4 and dlg.btn_finished.text() == "Hide finished (2)"
    dlg.btn_finished.click()
    assert dlg.table.rowCount() == 2


def test_only_finished_downloads_say_nothing_is_in_progress(qapp):
    backend = FakeBackend(records=[record(3, series_id=9, status=S.REMOVED)])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.records)
    assert dlg.table.rowCount() == 0 and dlg.status_label.text() == "Nothing is in progress."
    assert dlg.btn_finished.text() == "Show finished (1)"


def test_no_finished_downloads_no_button(qapp):
    backend = FakeBackend(records=[record(1, status=S.SENT)])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.records)
    assert not dlg.btn_finished.isVisibleTo(dlg)


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
               and dlg.table.cellWidget(0, 2).findChild(QLabel, "statusChip").text() == "Filed v03-v05 - seeding")
    assert finished == [True]
    assert backend.checks == 1 and threading.get_ident() not in backend.threads
    assert dlg.status_label.text() == "Checked now: torrents: 1 checked: 1 filed, 0 removed, 0 failed, 0 waiting."


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
    assert chip(dlg, 1).text() == "Filed v03-v05" and "stopped before its seed goal" in reason(dlg, 1)
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


class _TitledBackend(FakeBackend):
    """Names series like the real adapter: ``int()`` of every id it is given."""

    def series_titles(self, series_ids):
        return {int(i): f"Folder {int(i)}" for i in series_ids}


def test_a_record_whose_series_was_forgotten_does_not_stop_the_list(qapp):
    # owner's box, 2026-10-13: a forgotten series leaves its records with no series id (ON DELETE SET NULL); the title
    # lookup's int(None) failed every refresh and the In progress list stayed as it was for hours
    backend = _TitledBackend(records=[record(1, status=S.SENT), record(2, series_id=None, status=S.SENT)])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.records)
    names = sorted(dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount()))
    assert names == ["(series no longer in the library)", "Folder 7"]


def _menu_on(dlg, row, pick=None):
    """Open the row menu of *row*; returns {entry: enabled} (None: no menu) and picks *pick*."""
    seen = []

    def answer(menu, pos):
        acts = {a.text(): a for a in menu.actions()}
        seen.append({t: a.isEnabled() for t, a in acts.items()})
        return acts.get(pick) if pick else None
    dlg._exec_menu = answer
    dlg._on_context_menu(dlg.table.visualItemRect(dlg.table.item(row, 0)).center())
    return seen[0] if seen else None


def test_a_failed_torrent_row_can_be_dismissed(qapp):
    """Owner, 2026-10-15: "Dismiss" on failed rows - marked cancelled, out of In progress."""
    backend = FakeBackend(records=[record(1, status=S.FAILED, error="already in the folder"), record(2, status=S.SENT)])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.table.rowCount() == 2)
    assert _menu_on(dlg, 0) == {"Remove now…": False}                         # a running one: no Dismiss
    assert _menu_on(dlg, 1, pick="Dismiss") == {"Dismiss": True}
    wait_until(qapp, lambda: dlg._call is None and dlg.table.rowCount() == 1)
    assert backend.dismissed == [(1,)] and threading.get_ident() not in backend.threads
    assert dlg.status_label.text().startswith("Dismissed: Example Series v03-05 (Series #7). It is under Show finished.")
    dlg.toggle_finished()
    assert dlg.table.cellWidget(1, 2).findChild(QLabel, "statusChip").text() == "Cancelled"


def test_a_failed_chapter_row_dismisses_every_chapter_record_it_shows(qapp):
    from .chapter_fakes import chapter_record

    failed = [chapter_record(i, 7, str(40 + i), status=S.FAILED, error="already in the folder") for i in (1, 2, 3)]
    other = chapter_record(4, 7, "44", status=S.FAILED, error="Suwayomi could not download it", batch="b2")
    backend = FakeBackend(records=failed + [other])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.table.rowCount() == 2)                       # one row per Send and failure
    row = next(r for r in range(2) if "41-43" in dlg.table.item(r, 1).text())
    assert _menu_on(dlg, row, pick="Dismiss") == {"Dismiss": True}
    wait_until(qapp, lambda: dlg._call is None and dlg.table.rowCount() == 1)
    assert sorted(backend.dismissed[0]) == [1, 2, 3] and backend.record_list[3].status == S.FAILED


def test_without_dismiss_in_the_backend_a_failed_row_has_no_menu(qapp, monkeypatch):
    monkeypatch.delattr(FakeBackend, "dismiss_downloads")
    backend = FakeBackend(records=[record(1, status=S.FAILED, error="no space left")])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    assert _menu_on(dlg, 0) is None and not dlg.dismiss(backend.record_list[0])


def test_the_same_known_titles_again_rebuild_nothing(qapp, monkeypatch):
    """A library switch sends the host's names again: unchanged, the table is not rebuilt (it cost 40-60 ms)."""
    backend = FakeBackend(records=[record(1, status=S.SENT)])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    built = []
    real = dlg._show
    monkeypatch.setattr(dlg, "_show", lambda records: built.append(1) or real(records))
    dlg.set_known_titles({7: "Example Series"})
    assert built == [1] and dlg.table.item(0, 0).text() == "Example Series"
    dlg.set_known_titles({7: "Example Series"})                                 # the same names: nothing built
    assert built == [1]
    dlg.set_known_titles({7: "Renamed Series"})
    assert built == [1, 1] and dlg.table.item(0, 0).text() == "Renamed Series"


def test_a_long_reason_never_squeezes_the_chip(qapp):
    # owner, 2026-10-18: "the chip should not be cut off" - a long error in the chip showed only its middle
    long = "the chapter's CBZ is not in Suwayomi's download folder (looked for Some Group_Vol.5 Ch.25.cbz)"
    backend = FakeBackend(records=[record(1, status=S.FAILED, error=long)])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    dlg.resize(1300, 300)
    dlg.show()
    qapp.processEvents()
    c, detail = chip(dlg, 0), dlg.table.cellWidget(0, 2).findChild(QLabel, "statusDetail")
    assert c.text() == "Failed" and c.width() >= c.sizeHint().width()                     # the chip is whole
    assert detail.full_text() == long and long in c.toolTip()
    assert detail.text() == long or detail.text().endswith("\u2026")                     # cut at its end only
    assert detail.text().startswith("the chapter") or detail.text() == "\u2026"
    header = dlg.table.horizontalHeader()
    assert header.sectionSize(2) == 420                                       # room for a reason beside the chip
    dlg.close()
