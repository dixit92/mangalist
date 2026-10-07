"""The find-volumes dialog (offscreen Qt, fake backend): the header, the ranked table, the light-novel toggle, the
ambiguous-target gate on Send, the confirmation text, and the explicit empty / error states."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.contracts import Placement  # noqa: E402
from mangalist.gui import nyaa_dialog  # noqa: E402
from mangalist.gui.nyaa_dialog import NyaaDialog  # noqa: E402

from .conftest import FakeBackend, candidate, qapp, target, wait_until  # noqa: E402,F401

AMBIGUOUS = Placement("/lib/Example Series", None, "volumes live in two folders",
                      ("/lib/Example Series/Volumes", "/lib/Example Series/Digital"))


def _open(qapp, backend, confirm=None, open_url=None, **kw):
    dlg = NyaaDialog(backend, target(**kw), confirm=confirm or (lambda p, t: True), open_url=open_url)
    wait_until(qapp, lambda: dlg._search_state in ("done", "error") and (dlg._placement or dlg._placement_error))
    return dlg


def _cells(dlg, row):
    return [dlg.table.item(row, c).text() for c in range(dlg.table.columnCount())]


def test_header_shows_series_missing_volumes_and_folder(qapp):
    dlg = _open(qapp, FakeBackend())
    assert "Example Series" in dlg.title_label.text()
    assert dlg.missing_label.text() == "v03-v05, v09"
    assert dlg.folder_label.text() == "/lib/Example Series" and not dlg.folder_combo.isVisibleTo(dlg)
    assert "series folder" in dlg.folder_reason.text()


def test_search_and_placement_run_off_the_ui_thread_with_the_titles_and_volumes(qapp):
    backend = FakeBackend()
    _open(qapp, backend)
    assert backend.searched == [(("Example Series", "Exemplar"), ("3", "4", "5", "9"), ("1", "2"))]
    assert backend.threads and threading.get_ident() not in backend.threads


def test_results_table_columns_rank_order_and_reasons_tooltip(qapp):
    first = candidate()
    second = candidate("Example Series v03 (Print)", info_hash="b" * 40, vol_from="3", vol_to="3", covers_missing=("3",),
                       covers_held=("2",), digital=False, group=None, trusted=False, seeders=2, reasons=("Print",))
    dlg = _open(qapp, FakeBackend(results=[first, second]))
    assert dlg.table.rowCount() == 2
    assert [dlg.table.horizontalHeaderItem(c).text() for c in range(10)] == [
        "Title", "Volumes", "Covers missing", "Already held", "Digital", "Group", "Size", "Seeders", "Trusted",
        "Published"]
    assert _cells(dlg, 0) == ["Example Series v03-05 (Digital) (Group)", "v03-v05", "v03-v05", "", "yes", "Group",
                              "700.0 MB", "12", "yes", "2026-09-30"]
    assert _cells(dlg, 1)[1:5] == ["v03", "v03", "v02", ""] and _cells(dlg, 1)[7:9] == ["2", ""]   # backend order kept
    assert "Digital release" in dlg.table.item(0, 0).toolTip() and "Print" in dlg.table.item(1, 5).toolTip()
    assert not dlg.table.isSortingEnabled()
    assert dlg.selected_candidate() is first                    # the best one starts selected


def test_light_novels_hidden_until_toggled(qapp):
    novel = candidate("Example Series Light Novel v03 (EPUB)", info_hash="c" * 40, not_comic=True)
    dlg = _open(qapp, FakeBackend(results=[candidate(), novel]))
    assert not dlg.novels_check.isChecked()
    assert [dlg.table.isRowHidden(r) for r in (0, 1)] == [False, True]
    assert "1 light novel(s) hidden" in dlg.search_label.text()
    dlg.novels_check.setChecked(True)
    assert [dlg.table.isRowHidden(r) for r in (0, 1)] == [False, False]


def test_only_light_novels_says_so_and_nothing_is_sendable(qapp):
    dlg = _open(qapp, FakeBackend(results=[candidate(not_comic=True)]))
    assert "Only light novels" in dlg.search_label.text()
    assert dlg.selected_candidate() is None and not dlg.btn_send.isEnabled()


def test_empty_result_is_explicit(qapp):
    dlg = _open(qapp, FakeBackend(results=[]))
    assert dlg.table.rowCount() == 0 and "No releases found" in dlg.search_label.text()
    assert not dlg.btn_send.isEnabled() and dlg.btn_retry.isEnabled()


def test_search_error_is_surfaced_and_can_be_retried(qapp):
    backend = FakeBackend()
    backend.search_error = "nyaa answered 503"
    dlg = _open(qapp, backend)
    assert "search failed: nyaa answered 503" in dlg.search_label.text() and dlg.table.rowCount() == 0
    backend.search_error = None
    dlg.btn_retry.click()
    wait_until(qapp, lambda: dlg._search_state == "done")
    assert dlg.table.rowCount() == 1 and "failed" not in dlg.search_label.text()


def test_unexpected_search_errors_show_only_the_type(qapp):
    backend = FakeBackend()

    def boom(titles, missing, held):
        raise KeyError("http://user:secret@host/")

    backend.search = boom
    dlg = _open(qapp, backend)
    assert "unexpected error (KeyError)" in dlg.search_label.text() and "secret" not in dlg.search_label.text()


def test_placement_error_blocks_send(qapp):
    backend = FakeBackend()
    backend.placement_error = "the series has no scanned files"
    dlg = _open(qapp, backend)
    assert "no scanned files" in dlg.folder_label.text()
    assert not dlg.btn_send.isEnabled() and "could not be worked out" in dlg.btn_send.toolTip()


def test_ambiguous_target_gates_send_until_a_folder_is_chosen(qapp):
    backend = FakeBackend(placement=AMBIGUOUS)
    dlg = _open(qapp, backend)
    assert dlg.folder_combo.isVisibleTo(dlg) and dlg.folder_combo.count() == 3
    assert dlg.selected_candidate() is not None
    assert not dlg.btn_send.isEnabled() and "Choose the target folder" in dlg.btn_send.toolTip()
    assert not dlg.send_selected() and backend.sent == []
    dlg.folder_combo.setCurrentIndex(2)
    assert dlg.btn_send.isEnabled() and dlg.chosen_target_dir() == "/lib/Example Series/Digital"
    dlg.folder_combo.setCurrentIndex(0)
    assert not dlg.btn_send.isEnabled()                               # un-choosing closes the gate again


def test_send_confirms_with_series_volumes_and_folder_then_reports_success(qapp):
    texts = []
    backend = FakeBackend(placement=AMBIGUOUS)
    dlg = _open(qapp, backend, confirm=lambda p, t: texts.append(t) or True)
    dlg.folder_combo.setCurrentIndex(1)
    assert dlg.send_selected()
    assert "Example Series" in texts[0] and "v03-v05" in texts[0] and "/lib/Example Series/Volumes" in texts[0]
    assert "Example Series v03-05 (Digital) (Group)" in texts[0]
    wait_until(qapp, lambda: dlg.sent_records)
    assert backend.sent == [(7, "a" * 40, ("3", "4", "5"), "/lib/Example Series/Volumes")]
    assert "Sent to qBittorrent" in dlg.status_label.text()
    assert not dlg.btn_send.isEnabled() and "already sent" in dlg.btn_send.toolTip()      # no double send


def test_declining_the_confirmation_sends_nothing(qapp):
    backend = FakeBackend()
    dlg = _open(qapp, backend, confirm=lambda p, t: False)
    assert not dlg.send_selected() and backend.sent == [] and not dlg._sending


def test_send_error_is_shown_and_send_stays_available(qapp):
    backend = FakeBackend()
    backend.send_error = "qBittorrent refused the login"
    dlg = _open(qapp, backend)
    assert dlg.send_selected()
    wait_until(qapp, lambda: not dlg._sending)
    assert "Could not send it: qBittorrent refused the login" in dlg.status_label.text()
    assert dlg.btn_send.isEnabled() and not dlg.sent_records


def test_release_that_holds_no_missing_volume_cannot_be_sent(qapp):
    held_only = candidate("Example Series v01-02", vol_from="1", vol_to="2", covers_missing=(), covers_held=("1", "2"))
    dlg = _open(qapp, FakeBackend(results=[held_only]))
    assert not dlg.btn_send.isEnabled() and "none of the missing volumes" in dlg.btn_send.toolTip()


def test_release_without_volume_numbers_is_picked_for_all_missing_and_says_so(qapp):
    texts = []
    pack = candidate("Example Series Complete Pack", vol_from=None, vol_to=None, covers_missing=(), is_pack=True)
    backend = FakeBackend(results=[pack])
    dlg = _open(qapp, backend, confirm=lambda p, t: texts.append(t) or True)
    assert dlg.table.item(0, 1).text() == "?" and dlg.btn_send.isEnabled()
    dlg.send_selected()
    wait_until(qapp, lambda: dlg.sent_records)
    assert backend.sent[0][2] == ("3", "4", "5", "9") and "does not say which volumes" in texts[0]


def test_open_nyaa_page_only_for_http_links(qapp):
    opened = []
    dlg = _open(qapp, FakeBackend(), open_url=opened.append)
    assert dlg.open_selected_page() and opened == ["https://nyaa.example/view/aaaaaa"]
    odd = candidate(info_hash="d" * 40)
    odd = type(odd)(**{**odd.__dict__, "view_url": "file:///etc/passwd"})
    dlg2 = _open(qapp, FakeBackend(results=[odd]), open_url=opened.append)
    assert not dlg2.open_selected_page() and len(opened) == 1


def test_closing_during_a_search_drops_its_result(qapp):
    backend = FakeBackend()
    backend.gate = threading.Event()
    dlg = NyaaDialog(backend, target(), confirm=lambda p, t: True)
    wait_until(qapp, lambda: dlg._placement is not None)
    dlg.reject()                                                      # search still running
    backend.gate.set()
    wait_until(qapp, lambda: not dlg._calls or all(c.isFinished() for c in dlg._calls))
    qapp.processEvents()
    assert dlg.table.rowCount() == 0                                  # the late result was not applied


def test_default_confirm_is_a_question_box(qapp, monkeypatch):
    seen = {}

    def fake_question(parent, title, text, buttons, default):
        seen.update(title=title, text=text)
        return nyaa_dialog.QMessageBox.No

    monkeypatch.setattr(nyaa_dialog.QMessageBox, "question", staticmethod(fake_question))
    assert nyaa_dialog._default_confirm(None, "Send it?") is False and seen["title"] == "Send to qBittorrent"
