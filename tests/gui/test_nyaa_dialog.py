"""The releases panel and the find-volumes dialog around it (offscreen Qt, fake backend): the header, the ranked table,
the ambiguous-target gate on Send, the confirmation text, and the explicit empty / error states."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.contracts import Placement  # noqa: E402
from mangalist.gui import releases_panel  # noqa: E402
from mangalist.gui.nyaa_dialog import NyaaDialog  # noqa: E402
from mangalist.gui.releases_panel import ReleasesPanel  # noqa: E402

from .conftest import FakeBackend, candidate, qapp, target, wait_until  # noqa: E402,F401

AMBIGUOUS = Placement("/lib/Example Series", None, "volumes live in two folders",
                      ("/lib/Example Series/Volumes", "/lib/Example Series/Digital"))


@pytest.fixture(autouse=True)
def _close_panels():
    made = []
    original = ReleasesPanel.__init__

    def tracking(self, *a, **kw):
        original(self, *a, **kw)
        made.append(self)

    ReleasesPanel.__init__ = tracking
    yield
    ReleasesPanel.__init__ = original
    for panel in made:
        panel.stop()
        panel.deleteLater()


def _open(qapp, backend, confirm=None, open_url=None, **kw):
    dlg = ReleasesPanel(backend, confirm=confirm or (lambda p, t: True), open_url=open_url)
    dlg.open_target(target(**kw))
    wait_until(qapp, lambda: dlg._search_state in ("done", "error") and (dlg._placement or dlg._placement_error))
    return dlg


def _cells(dlg, row):
    return [dlg.table.item(row, c).text() for c in range(dlg.table.columnCount())]


def test_header_shows_series_missing_volumes_and_folder(qapp):
    dlg = _open(qapp, FakeBackend())
    assert dlg.title_label.text() == "Example Series"
    assert "v03-v05, v09" in dlg.subtitle_label.text()
    assert "files go to" in dlg.subtitle_label.text() and "Example Series/" in dlg.subtitle_label.text()
    assert "series folder" in dlg.subtitle_label.text() and not dlg.folder_row.isVisibleTo(dlg)
    assert dlg.subtitle_label.toolTip() == "/lib/Example Series"
    assert dlg.search_label.text() == "nyaa \u00b7 1 release"


def test_search_and_placement_run_off_the_ui_thread_with_the_titles_and_volumes(qapp):
    backend = FakeBackend()
    _open(qapp, backend)
    assert backend.searched == [(("Example Series", "Exemplar"), ("3", "4", "5", "9"), ("1", "2"))]
    assert backend.threads and threading.get_ident() not in backend.threads


def test_results_table_columns_rank_order_and_reasons(qapp):
    first = candidate()
    second = candidate("Example Series v03 (Print)", info_hash="b" * 40, vol_from="3", vol_to="3", covers_missing=("3",),
                       covers_held=("2",), digital=False, group=None, trusted=False, seeders=2, reasons=("Print",))
    dlg = _open(qapp, FakeBackend(results=[first, second]))
    assert dlg.table.rowCount() == 2
    assert [dlg.table.horizontalHeaderItem(c).text() for c in range(8)] == [
        "", "RELEASE", "FILLS", "YOU HAVE", "PUBLISHED", "SOURCE", "SIZE", "SEEDERS"]
    published = first.published[:10]
    assert _cells(dlg, 0) == ["", "Example Series v03-05 (Digital) (Group)", "v03-v05", "-", published, "nyaa",
                              "700.0 MB", "12"]
    assert _cells(dlg, 1)[2:4] == ["v03", "v02"] and _cells(dlg, 1)[7] == "2"            # backend order kept
    assert dlg.table.item(0, 1).data(releases_panel.ROLE_SUB) == "Digital release, Covers 3 missing volumes, trusted uploader"
    assert dlg.table.item(1, 1).data(releases_panel.ROLE_SUB) == "Print"
    assert "Digital release" in dlg.table.item(0, 1).toolTip() and "Group: Group" in dlg.table.item(0, 1).toolTip()
    assert not dlg.table.isSortingEnabled()
    assert dlg.selected_candidate() is first                    # the best one starts selected


def test_a_light_novel_is_shown_with_its_flag_when_the_backend_returns_it(qapp):
    novel = candidate("Example Series Light Novel v03 (EPUB)", info_hash="c" * 40, not_comic=True)
    dlg = _open(qapp, FakeBackend(results=[candidate(), novel]))
    assert dlg.table.rowCount() == 2                            # whether novels come back is the backend's (Settings) rule
    assert "light novel" in dlg.table.item(1, 1).data(releases_panel.ROLE_SUB)


def test_empty_result_is_explicit(qapp):
    dlg = _open(qapp, FakeBackend(results=[]))
    assert dlg.table.rowCount() == 0 and "No releases found" in dlg.message_text.text()
    assert dlg.stack.currentIndex() == releases_panel.PAGE_MESSAGE
    assert not dlg.btn_send.isEnabled() and dlg.btn_retry.isEnabled()


def test_search_error_is_surfaced_and_can_be_retried(qapp):
    backend = FakeBackend()
    backend.search_error = "nyaa answered 503"
    dlg = _open(qapp, backend)
    assert "search failed: nyaa answered 503" in dlg.message_text.text() and dlg.table.rowCount() == 0
    backend.search_error = None
    dlg.btn_retry.click()
    wait_until(qapp, lambda: dlg._search_state == "done")
    assert dlg.table.rowCount() == 1 and dlg.stack.currentIndex() == releases_panel.PAGE_RESULTS


def test_unexpected_search_errors_show_only_the_type(qapp):
    backend = FakeBackend()

    def boom(titles, missing, held):
        raise KeyError("http://user:secret@host/")

    backend.search = boom
    dlg = _open(qapp, backend)
    assert "unexpected error (KeyError)" in dlg.message_text.text() and "secret" not in dlg.message_text.text()


def test_placement_error_blocks_send(qapp):
    backend = FakeBackend()
    backend.placement_error = "the series has no scanned files"
    dlg = _open(qapp, backend)
    assert "no scanned files" in dlg.folder_label.text() and dlg.folder_row.isVisibleTo(dlg)
    assert not dlg.btn_send.isEnabled() and "could not be worked out" in dlg.btn_send.toolTip()


def test_ambiguous_target_gates_send_until_a_folder_is_chosen(qapp):
    backend = FakeBackend(placement=AMBIGUOUS)
    dlg = _open(qapp, backend)
    assert dlg.folder_row.isVisibleTo(dlg) and dlg.folder_combo.count() == 3
    assert dlg.selected_candidate() is not None
    assert not dlg.btn_send.isEnabled() and "Choose the target folder" in dlg.btn_send.toolTip()
    assert not dlg.send_selected() and backend.sent == []
    dlg.folder_combo.setCurrentIndex(2)
    assert dlg.btn_send.isEnabled() and dlg.chosen_target_dir() == "/lib/Example Series/Digital"
    assert "Example Series/Digital/" in dlg.subtitle_label.text()
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
    assert dlg.table.item(0, 2).text() == "?" and dlg.table.item(0, 3).text() == "?" and dlg.btn_send.isEnabled()
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
    dlg = ReleasesPanel(backend, confirm=lambda p, t: True)
    dlg.open_target(target())
    wait_until(qapp, lambda: dlg._placement is not None)
    dlg.stop()                                                        # search still running
    backend.gate.set()
    qapp.processEvents()
    import time
    time.sleep(0.2)
    qapp.processEvents()
    assert dlg.table.rowCount() == 0                                  # the late result was not applied


def test_the_dialog_wraps_the_panel_and_reports_what_was_sent(qapp):
    backend = FakeBackend()
    dlg = NyaaDialog(backend, target(), confirm=lambda p, t: True)
    wait_until(qapp, lambda: dlg.panel._search_state == "done" and dlg.panel._placement)
    assert dlg.panel.title_label.text() == "Example Series" and dlg.windowTitle() == "Find volumes on nyaa"
    assert dlg.panel.send_selected()
    wait_until(qapp, lambda: dlg.sent_records)
    assert backend.sent[0][0] == 7
    dlg.reject()
    dlg.deleteLater()


def test_searching_state_and_no_target_message(qapp):
    backend = FakeBackend()
    backend.gate = threading.Event()
    panel = ReleasesPanel(backend)
    assert not panel.btn_send.isEnabled() and "Select a series" in panel.message_text.text()
    panel.open_target(target())
    assert panel.search_label.text() == "Searching nyaa..." and not panel.btn_retry.isEnabled()
    assert "Still looking up" in panel.btn_send.toolTip() or "Select a release" in panel.btn_send.toolTip()
    backend.gate.set()
    wait_until(qapp, lambda: panel._search_state == "done")


def test_managed_panel_shows_what_the_host_found_and_asks_the_host_to_search_again(qapp):
    from mangalist.gui.releases_panel import SearchOutcome

    panel = ReleasesPanel(FakeBackend(), managed=True, source_label="nyaa \u00b7 English")
    asked = []
    panel.search_again.connect(lambda: asked.append(True))
    placement = Placement("/lib/Example Series", "/lib/Example Series/Volumes", "volumes live in \"Volumes\"")
    panel.show_outcome(SearchOutcome(target(), placement=placement, candidates=[candidate()]))
    assert panel.table.rowCount() == 1 and panel.btn_send.isEnabled()
    assert panel.search_label.text() == "nyaa \u00b7 English \u00b7 1 release"
    assert "Example Series/Volumes/" in panel.subtitle_label.text()
    panel.btn_retry.click()
    assert asked == [True]                                            # managed: the host runs the search
    panel.show_outcome(SearchOutcome(target(), placement=placement, candidates=None, error="nyaa answered 503"))
    assert "nyaa answered 503" in panel.message_text.text() and panel.table.rowCount() == 0
    panel.show_message("Example Webcomic", "Needs Suwayomi.", "Open Settings", "services")
    sections = []
    panel.settings_requested.connect(sections.append)
    panel.message_action.click()
    assert sections == ["services"] and panel.title_label.text() == "Example Webcomic"
    assert not panel.btn_send.isEnabled() and not panel.btn_retry.isVisibleTo(panel)


def test_default_confirm_is_a_question_box(qapp, monkeypatch):
    seen = {}

    def fake_question(parent, title, text, buttons, default):
        seen.update(title=title, text=text)
        return releases_panel.QMessageBox.No

    monkeypatch.setattr(releases_panel.QMessageBox, "question", staticmethod(fake_question))
    assert releases_panel._default_confirm(None, "Send it?") is False and seen["title"] == "Send to qBittorrent"


def test_the_published_column_fits_a_whole_date_once_shown(qapp):
    """The owner's screen showed "2021-11-...": the column is sized from the table's own (styled) font."""
    from PySide6.QtGui import QFontMetricsF

    dlg = _open(qapp, FakeBackend())
    dlg.table.setColumnWidth(releases_panel.COL_DATE, 40)                # as narrow as a large font makes 100 px
    dlg.resize(1400, 600)
    dlg.show()
    wait_until(qapp, lambda: dlg._fitted)
    need = QFontMetricsF(dlg.table.font()).horizontalAdvance("2026-09-30")
    assert dlg.table.columnWidth(releases_panel.COL_DATE) >= need + 2 * releases_panel.DATE_PAD
    dlg.close()
