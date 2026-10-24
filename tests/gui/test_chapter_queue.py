"""The chapter download queue view (transition fixes E2, 2026-10-24; offscreen Qt): the chosen program's queue - state,
progress, tries - joined with MangaList's own records, read off the UI thread; Retry / Cancel... as buttons for the failed
rows MangaList has a record of; the words follow the backend in force; the bar under the In progress list opens it. The
real backend reads the in-process fake host's queue."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QHeaderView, QLabel  # noqa: E402

from mangalist import store  # noqa: E402
from mangalist.downloads.adapter import Backend  # noqa: E402
from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.downloads.contracts import QueuedChapter  # noqa: E402
from mangalist.gui import chapter_queue as cq  # noqa: E402
from mangalist.gui.download_tab import DownloadTab  # noqa: E402
from mangalist.store.downloads import ExtensionHostConfig  # noqa: E402

from ..services.extensions.fake_host import FakeHost  # noqa: E402
from .chapter_fakes import SwitchableChapterBackend, chapter_record  # noqa: E402
from .conftest import FakeBackend, qapp, wait_until  # noqa: E402,F401

ITEMS = [QueuedChapter(101, "DOWNLOADING", 0.4, 1), QueuedChapter(102, "QUEUED"), QueuedChapter(103, "ERROR", 0.0, 4),
         QueuedChapter(999, "QUEUED")]


def backend_with_queue(key="suwayomi", items=ITEMS):
    backend = SwitchableChapterBackend(key)
    backend.queue_items = list(items)
    backend.record_list = [chapter_record(101, 4, "41"), chapter_record(102, 4, "42"), chapter_record(103, 4, "43"),
                           chapter_record(7, 4, "40", status=S.FILED)]
    backend.titles = {4: "Example Webcomic"}
    backend.series_titles = lambda ids: {i: "Example Webcomic" for i in ids}
    return backend


@pytest.fixture
def dialogs():
    made = []
    yield made
    for d in made:
        d.done(0)
        d.deleteLater()


def open_queue(qapp, dialogs, backend, **kw):
    dlg = cq.ChapterQueueDialog(backend, **kw)
    dialogs.append(dlg)
    dlg.load()
    wait_until(qapp, lambda: not dlg._calls)
    return dlg


def cells(dlg):
    t = dlg.table
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


# --- the rows --------------------------------------------------------------------------------------------------------

def test_rows_join_the_queue_with_the_records_and_the_series_names():
    backend = backend_with_queue()
    rows = cq.queue_rows(ITEMS, backend.record_list, {4: "Example Webcomic"})
    assert [(r.chapter_id, r.series, r.chapter, r.state_text, r.progress_text, r.tries) for r in rows] == [
        (101, "Example Webcomic", "41", "Downloading", "40%", 1), (102, "Example Webcomic", "42", "Waiting", "", 0),
        (103, "Example Webcomic", "43", "Failed", "", 4), (999, "", "", "Waiting", "", 0)]
    assert [r.failed for r in rows] == [False, False, True, False] and rows[3].record_id is None
    # a filed record's chapter has left the queue: its id is not claimed for a new entry with the same number
    filed = chapter_record(999, 4, "9", status=S.FILED)
    assert cq.queue_rows([QueuedChapter(999, "QUEUED")], [filed], {4: "x"})[0].series == ""
    assert cq.summary_text(rows) == "1 downloading, 2 waiting, 1 failed" and cq.summary_text([]) == "The queue is empty."


def test_a_state_the_program_adds_is_shown_as_it_is():
    rows = cq.queue_rows([QueuedChapter(1, "PAUSED"), QueuedChapter(2, "")], [], {})
    assert [r.state_text for r in rows] == ["Paused", "?"]
    assert cq.summary_text(rows) == "1 paused, 1 ?"


# --- the dialog -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ("suwayomi", "extensions"))
def test_the_dialog_lists_the_queue_in_the_programs_words(qapp, dialogs, key):
    backend = backend_with_queue(key)
    dlg = open_queue(qapp, dialogs, backend)
    program = "Built-in extensions" if key == "extensions" else "Suwayomi"
    assert dlg.title_label.text() == f"Chapter download queue · {program}"
    assert cells(dlg) == [["Example Webcomic", "ch 41", "Downloading", "40%", "1"],
                          ["Example Webcomic", "ch 42", "Waiting", "", ""],
                          ["Example Webcomic", "ch 43", "Failed", "", "4"],
                          ["(not sent by MangaList)", "id 999", "Waiting", "", ""]]
    assert dlg.summary_label.text() == "1 downloading, 2 waiting, 1 failed"
    texts = "\n".join(w.text() + w.toolTip() for w in dlg.findChildren(QLabel)) + dlg.table.item(2, cq.COL_STATE).toolTip()
    assert ("Suwayomi" not in texts) == (key == "extensions")
    assert dlg.btn_close.text() == "Close" and not dlg.status_label.text()
    # it was read off the UI thread
    assert threading.get_ident() not in backend.threads


def test_the_columns_are_the_owners_to_resize(qapp, dialogs):
    dlg = open_queue(qapp, dialogs, backend_with_queue())
    header = dlg.table.horizontalHeader()
    assert all(header.sectionResizeMode(c) == QHeaderView.ResizeMode.Interactive for c in range(dlg.table.columnCount()))


def test_a_queue_that_cannot_be_read_says_why(qapp, dialogs):
    backend = backend_with_queue("extensions")
    backend.queue_error = "Suwayomi could not be reached"
    dlg = open_queue(qapp, dialogs, backend)
    assert dlg.table.rowCount() == 0 and dlg.status_label.text() == "The extension host could not be reached"
    assert dlg.btn_reload.isEnabled() and not dlg.btn_retry.isEnabled()
    backend.queue_error = None
    dlg.btn_reload.click()
    wait_until(qapp, lambda: not dlg._calls)
    assert dlg.table.rowCount() == 4 and dlg.status_label.text() == ""


def test_retry_and_cancel_are_buttons_for_the_failed_rows_with_a_record(qapp, dialogs):
    backend = backend_with_queue()
    asked = []
    dlg = open_queue(qapp, dialogs, backend, confirm=lambda parent, text: asked.append(text) or True)
    assert not dlg.btn_retry.isEnabled() and not dlg.btn_cancel.isEnabled()          # nothing selected
    dlg.table.selectRow(0)                                                           # a downloading row
    assert not dlg.btn_retry.isEnabled()
    dlg.table.selectRow(3)                                                           # failed? no: waiting, no record
    assert not dlg.btn_retry.isEnabled()
    dlg.table.selectRow(2)                                                           # the failed one
    assert dlg.btn_retry.isEnabled() and dlg.btn_cancel.isEnabled()
    changed = []
    dlg.changed.connect(lambda: changed.append(1))
    assert dlg.retry_selected()
    wait_until(qapp, lambda: not dlg._calls)
    assert backend.retried == [(103,)] and changed
    assert dlg.status_label.text() == "Retried 1 chapter."                          # kept over the reload that follows
    dlg.table.selectRow(2)
    assert dlg.cancel_selected()
    wait_until(qapp, lambda: not dlg._calls)
    assert backend.cancelled == [(103,)] and len(asked) == 1
    assert "out of Suwayomi's download queue" in asked[0] and "1 chapter " in asked[0]


def test_cancel_asks_first_and_no_means_nothing_happens(qapp, dialogs):
    backend = backend_with_queue("extensions")
    asked = []
    dlg = open_queue(qapp, dialogs, backend, confirm=lambda parent, text: asked.append(text) or False)
    dlg.table.selectRow(2)
    assert not dlg.cancel_selected()
    assert asked and "the extension host's download queue" in asked[0] and "Suwayomi" not in asked[0]
    assert getattr(backend, "cancelled", []) == []


# --- the bar under In progress -------------------------------------------------------------------------------------

def test_the_bar_is_shown_when_the_backend_can_read_a_queue(qapp, dialogs):
    plain = cq.ChapterQueueBar(FakeBackend())
    assert not cq.queue_available(FakeBackend()) and plain.isHidden() and plain.open_queue() is None
    bar = cq.ChapterQueueBar(backend_with_queue("extensions"))
    try:
        assert not bar.isHidden() and "as the extension host reports it now" in bar.btn_open.toolTip()
        dlg = bar.open_queue()
        dialogs.append(dlg)
        wait_until(qapp, lambda: not dlg._calls)
        assert dlg.table.rowCount() == 4 and bar.dialog is dlg
    finally:
        bar.deleteLater()


def test_the_download_tab_has_the_bar_under_in_progress(qapp):
    backend = backend_with_queue()
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0)
    try:
        assert tab.queue_bar.btn_open.text() == "Chapter queue…" and not tab.queue_bar.isHidden()
        assert tab.queue_bar.parentWidget() is tab.downloads.parentWidget()          # in the In progress panel
        backend.key = "extensions"
        tab.refresh()
        tab._on_records([])
        assert "the extension host" in tab.queue_bar.btn_open.toolTip()
    finally:
        tab.stop()
        tab.deleteLater()


# --- the real backend --------------------------------------------------------------------------------------------

def test_the_real_backend_reads_the_hosts_queue(qapp, dialogs, tmp_path):
    store.reset_stores()
    db = store.get_store()
    host = FakeHost(tmp_path / "exthost", auto_download=False).start()
    try:
        backend = Backend(db)
        backend.save_extension_host(ExtensionHostConfig(url=host.url))
        backend.set_chapter_backend("extensions")
        host.queue += [{"chapterId": 31, "state": "ERROR", "progress": 0.0, "tries": 4},
                       {"chapterId": 32, "state": "DOWNLOADING", "progress": 0.5, "tries": 1}]
        assert cq.queue_available(backend)
        dlg = open_queue(qapp, dialogs, backend)
        assert cells(dlg) == [["(not sent by MangaList)", "id 31", "Failed", "", "4"],
                              ["(not sent by MangaList)", "id 32", "Downloading", "50%", "1"]]
        assert dlg.title_label.text().endswith("Built-in extensions")
        host.queue.clear()
        dlg.btn_reload.click()
        wait_until(qapp, lambda: not dlg._calls)
        assert dlg.table.rowCount() == 0 and dlg.summary_label.text() == "The queue is empty."
    finally:
        host.stop()
