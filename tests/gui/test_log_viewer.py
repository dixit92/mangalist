"""The log viewer window: both logs, newest at the bottom, following (and pausing), the level / area / search filters,
older lines on request (into the rotated copies), copying, the log folder, big files off the UI thread, one window.

Synthetic log files in a temporary folder; reads run at once on the test's thread (``run=_sync_run``) except where a
test is about the background thread, and then each read is waited for before the next starts."""

from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from mangalist import log_config  # noqa: E402
from mangalist.gui import log_tail, log_viewer  # noqa: E402
from mangalist.gui.download_style import BADGES  # noqa: E402
from mangalist.gui.log_viewer import LogViewer, _sync_run, open_log_viewer  # noqa: E402

from .conftest import qapp, wait_until  # noqa: E402,F401


def line(n: int, level: str = "INFO", logger: str = "mangalist.scanner", text: str = "") -> str:
    return f"2026-10-10 12:{n // 60 % 60:02d}:{n % 60:02d}  {level:<8}  {logger}  {text or f'line {n:06d}'}"


def write(path: Path, lines, mode: str = "w") -> None:
    with open(path, mode, encoding="utf-8", newline="\n") as fh:
        fh.write("".join(f"{x}\n" for x in lines))


def dispose(qapp, *widgets) -> None:
    for w in widgets:
        if w is not None:
            w.close()
            w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


APP_LINES = [
    line(1, "DEBUG", "mangalist.scanner", "looking at a folder"),
    line(2, "INFO", "mangalist.scanner", "Scan done: 12 series"),
    line(3, "WARNING", "mangalist.services.suwayomi.client", "Suwayomi did not answer; trying again"),
    line(4, "ERROR", "mangalist.renamer", "could not rename a file"),
    "Traceback (most recent call last):",
    "OSError: disk full",
    line(5, "INFO", "mangalist.services.mangapixer.client", "MangaPixer rescan asked"),
]


@pytest.fixture
def logs(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    write(folder / "mangalist.log", APP_LINES)
    return folder


@pytest.fixture
def make(qapp, logs):
    made = []

    def factory(**kw):
        kw.setdefault("log_dir", logs)
        kw.setdefault("run", _sync_run)
        kw.setdefault("poll_ms", 0)
        viewer = LogViewer(**kw)
        made.append(viewer)
        return viewer

    yield factory
    for viewer in made:
        wait_until(qapp, lambda v=viewer: not v._busy or v._closed)
    dispose(qapp, *made)


def shown(viewer):
    return [row[0] for row in viewer.model.rows]


def test_it_opens_on_the_app_log_with_the_newest_line_at_the_bottom(make):
    viewer = make()
    assert viewer.log_key == "app" and viewer.log_buttons["app"].isChecked()
    assert shown(viewer) == APP_LINES
    assert viewer.view.currentIndex().row() in (-1, len(APP_LINES) - 1)
    assert viewer.info_label.text() == f"mangalist.log - {log_tail.size_text(viewer.path.stat().st_size)} - 7 lines loaded"
    assert "Following" in viewer.status_label.text() and viewer.btn_follow.text() == "Pause"
    assert viewer.btn_older.isHidden(), "the whole file is loaded"
    assert not viewer.btn_copy.isEnabled()


def test_warnings_and_errors_are_coloured_like_the_badges_and_markers_are_italic(make):
    viewer = make()
    model = viewer.model
    colour = lambda row, role: model.data(model.index(row, 0), role)  # noqa: E731
    assert colour(2, Qt.ItemDataRole.BackgroundRole).color().name() == BADGES["warn"][0]
    assert colour(2, Qt.ItemDataRole.ForegroundRole).color().name() == BADGES["warn"][1]
    for row in (3, 4, 5):                                                # the error and its traceback
        assert colour(row, Qt.ItemDataRole.BackgroundRole).color().name() == BADGES["bad"][0]
    assert colour(1, Qt.ItemDataRole.BackgroundRole) is None and colour(1, Qt.ItemDataRole.ForegroundRole) is None
    assert colour(0, Qt.ItemDataRole.ForegroundRole).color().name() == "#6b6b67"     # debug is grey
    model.append([(log_tail.MARKER_ROTATED, 0, True)])
    assert model.data(model.index(7, 0), Qt.ItemDataRole.FontRole).italic()


def test_the_level_area_and_search_filters(make):
    viewer = make()
    assert [viewer.area_combo.itemText(i) for i in range(viewer.area_combo.count())] == [
        "All areas", "Scanning", "MangaPixer", "Suwayomi", "Renamer"], "only the areas the log has, in a fixed order"
    viewer.level_combo.setCurrentIndex(1)
    assert shown(viewer) == APP_LINES[2:6]
    viewer.level_combo.setCurrentIndex(2)
    assert shown(viewer) == APP_LINES[3:6], "the traceback stays with its error"
    viewer.level_combo.setCurrentIndex(0)
    viewer.area_combo.setCurrentIndex(viewer.area_combo.findData("Scanning"))
    assert shown(viewer) == APP_LINES[:2]
    assert "7 lines loaded, 2 shown" in viewer.info_label.text()
    viewer.area_combo.setCurrentIndex(0)
    viewer.search.setText("DISK FULL")
    assert viewer._search_timer.isActive()
    viewer.rebuild()                                                     # what the debounce timer does
    assert shown(viewer) == APP_LINES[3:6]
    viewer.search.setText("nothing like this")
    viewer.rebuild()
    assert shown(viewer) == [] and viewer.stack.currentWidget() is viewer.empty_label
    assert viewer.empty_label.text() == log_viewer.NO_MATCH_TEXT
    viewer.search.clear()
    viewer.rebuild()
    assert viewer.stack.currentWidget() is viewer.view


def test_new_lines_follow_at_the_bottom_and_a_cut_traceback_joins_its_record(make, logs):
    viewer = make()
    viewer.resize(900, 300)
    viewer.show()
    write(logs / "mangalist.log", [line(i) for i in range(10, 80)], "a")
    viewer.poll()
    assert shown(viewer)[-1] == line(79)
    bar = viewer.view.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum(), "kept at the bottom"
    viewer.level_combo.setCurrentIndex(2)                                # errors only
    write(logs / "mangalist.log", [line(80, "ERROR", text="boom"), "Traceback (most recent call last):"], "a")
    viewer.poll()
    write(logs / "mangalist.log", ['  File "x.py", line 1', line(81, "INFO")], "a")
    viewer.poll()
    assert shown(viewer)[-3:] == [line(80, "ERROR", text="boom"), "Traceback (most recent call last):",
                                  '  File "x.py", line 1'], "the rest of the traceback came with the next poll"
    viewer.poll()
    assert shown(viewer)[-1] == '  File "x.py", line 1', "nothing new, nothing added"


def test_a_view_scrolled_up_stays_where_it_is(make, logs):
    write(logs / "mangalist.log", [line(i) for i in range(200)])
    viewer = make()
    viewer.resize(900, 300)
    viewer.show()
    bar = viewer.view.verticalScrollBar()
    bar.setValue(10)
    write(logs / "mangalist.log", [line(i) for i in range(200, 220)], "a")
    viewer.poll()
    assert shown(viewer)[-1] == line(219) and bar.value() == 10


def test_pause_holds_new_lines_back_and_follow_shows_them(make, logs):
    viewer = make()
    viewer.btn_follow.click()
    assert not viewer.following and viewer.btn_follow.text() == "Follow"
    write(logs / "mangalist.log", [line(i) for i in range(10, 13)], "a")
    viewer.poll()
    assert shown(viewer) == APP_LINES and "3 new lines waiting" in viewer.status_label.text()
    assert viewer.status_label.property("tone") == "warn"
    viewer.btn_follow.click()
    assert viewer.following and shown(viewer)[-3:] == [line(10), line(11), line(12)]
    assert "Following" in viewer.status_label.text()


def test_the_scheduled_jobs_log_before_and_after_it_is_written(make, logs):
    viewer = make()
    viewer.log_buttons["jobs"].click()
    assert viewer.log_key == "jobs" and viewer.path.name == "headless.log"
    assert viewer.stack.currentWidget() is viewer.empty_label
    assert "Nothing has been written to headless.log yet" in viewer.empty_label.text()
    assert viewer.info_label.text() == "headless.log - not written yet" and viewer.btn_older.isHidden()
    viewer.poll()
    assert viewer.model.rowCount() == 0
    write(logs / "headless.log", [line(1, "INFO", "mangalist.headless", "Runner started"),
                                  line(2, "WARNING", "mangalist.headless.downloads_job", "qBittorrent is not set up")])
    viewer.poll()
    assert shown(viewer)[0].endswith("Runner started") and viewer.stack.currentWidget() is viewer.view
    assert [viewer.area_combo.itemText(i) for i in range(viewer.area_combo.count())] == [
        "All areas", "Downloads", "Scheduled jobs"]
    viewer.log_buttons["app"].click()
    assert shown(viewer) == APP_LINES


def test_rotation_while_following_shows_every_line_once_with_a_marker(make, logs):
    path = logs / "mangalist.log"
    path.unlink()
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2500, backupCount=3, encoding="utf-8")
    handler.setFormatter(log_config.RedactingFormatter(log_config._FMT, datefmt=log_config._DATE_FMT))
    log = logging.getLogger("mangalist.scanner.test_viewer_rotation")
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        log.info("message %04d", 0)
        viewer = make()
        for i in range(1, 120):
            log.info("message %04d", i)
            if i % 10 == 0:
                viewer.poll()
        viewer.poll()
    finally:
        log.removeHandler(handler)
        handler.close()
    lines = shown(viewer)
    assert lines.count(log_tail.MARKER_ROTATED) >= 3
    numbers = [int(t.rsplit(" ", 1)[1]) for t in lines if t != log_tail.MARKER_ROTATED]
    assert numbers == list(range(120))


def test_older_lines_load_on_request_then_from_the_rotated_copy(make, logs):
    write(logs / "mangalist.log.1", [line(i) for i in range(0, 100)])
    write(logs / "mangalist.log", [line(i) for i in range(100, 300)])
    viewer = make(chunk_bytes=3000)
    first = shown(viewer)
    assert first[-1] == line(299) and len(first) < 60
    assert viewer.btn_older.isEnabled() and not viewer.btn_older.isHidden()
    assert "and 1 older file" in viewer.info_label.text()
    assert "Older lines are not loaded yet" in viewer.info_label.toolTip()
    rounds = 0
    while not viewer.btn_older.isHidden():
        viewer.btn_older.click()
        rounds += 1
        assert rounds < 30
    lines = shown(viewer)
    assert lines.count(log_tail.MARKER_ROTATED) == 1
    assert [t for t in lines if t != log_tail.MARKER_ROTATED] == [line(i) for i in range(300)]
    assert lines.index(log_tail.MARKER_ROTATED) == 100
    assert f"{301:,} lines loaded" in viewer.info_label.text()


def test_copy_selected_lines_with_the_button_and_ctrl_c(make):
    copied = []
    viewer = make(copy_text=copied.append)
    sel = viewer.view.selectionModel()
    for row in (4, 1, 2):
        sel.select(viewer.model.index(row, 0), QItemSelectionModel.SelectionFlag.Select)
    assert viewer.btn_copy.isEnabled()
    viewer.btn_copy.click()
    assert copied == ["\n".join([APP_LINES[1], APP_LINES[2], APP_LINES[4]])], "in the log's order"
    assert viewer.status_label.text() == "Copied 3 lines." and viewer.status_label.property("tone") == "ok"
    sel.clearSelection()
    sel.select(viewer.model.index(0, 0), QItemSelectionModel.SelectionFlag.Select)
    QTest.keyClick(viewer.view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    # Qt keeps Ctrl "held" after a modified key click; let it go, or later tests' clicks extend selections
    QTest.keyRelease(viewer.view, Qt.Key.Key_Control)
    assert copied[-1] == APP_LINES[0]
    sel.clearSelection()
    assert viewer.copy_selected() == 0 and len(copied) == 2


def test_open_the_log_folder_and_say_when_it_cannot(make, logs):
    opened = []
    viewer = make(open_folder=lambda folder: opened.append(folder) or True)
    assert viewer.open_log_folder() and opened == [str(logs)]
    viewer = make(open_folder=lambda folder: False)
    assert not viewer.open_log_folder()
    assert str(logs) in viewer.status_label.text() and viewer.status_label.property("tone") == "bad"


def test_a_read_that_fails_says_why(make, logs, monkeypatch):
    viewer = make()

    def refuse(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(log_tail, "read_new", refuse)
    viewer.poll()
    assert viewer.status_label.text() == "Could not read the log: could not read mangalist.log: Permission denied."
    assert viewer.status_label.property("tone") == "bad" and not viewer._busy
    assert shown(viewer) == APP_LINES


def test_a_50_mb_log_is_read_off_the_ui_thread_and_only_its_end(make, logs, qapp):
    block = "".join(f"{line(i, 'WARNING' if i % 50 == 0 else 'INFO', text='x' * 60 + f' {i:06d}')}\n"
                    for i in range(1000))
    with open(logs / "mangalist.log", "w", encoding="utf-8") as fh:
        for _ in range(50 * 1024 * 1024 // len(block) + 1):
            fh.write(block)
    assert (logs / "mangalist.log").stat().st_size > 50 * 1024 * 1024
    began = time.monotonic()
    viewer = make(run=None)                                              # the real background thread
    assert time.monotonic() - began < 2.0 and viewer._busy, "the window is up before the file is read"
    wait_until(qapp, lambda: not viewer._busy, timeout=30)
    rows = viewer.model.rowCount()
    assert 0 < rows < 50_000 and shown(viewer)[-1].endswith(" 000999")
    size = log_tail.size_text((logs / "mangalist.log").stat().st_size)
    assert viewer.btn_older.isEnabled() and not viewer.btn_older.isHidden()
    assert viewer.info_label.text() == f"mangalist.log - {size} - {rows:,} lines loaded"
    viewer.level_combo.setCurrentIndex(1)
    assert 0 < viewer.model.rowCount() < rows
    with open(logs / "mangalist.log", "a", encoding="utf-8") as fh:
        fh.write(line(2000, "ERROR", text="the newest") + "\n")
    viewer.poll()                                                        # also through the thread
    wait_until(qapp, lambda: not viewer._busy)
    assert shown(viewer)[-1] == line(2000, "ERROR", text="the newest")


def test_closing_stops_the_timer_and_drops_a_read_in_flight(make, qapp):
    calls = []

    def later(fn, on_done=None, on_error=None, on_finished=None):
        calls.append((fn, on_done))
        return None

    viewer = make(run=later, poll_ms=1000)
    assert viewer._timer.isActive() and len(calls) == 1
    fn, on_done = calls[0]
    viewer.close()
    assert not viewer._timer.isActive() and viewer._closed
    on_done(fn())                                                        # the read finishes after the close
    assert viewer.model.rowCount() == 0


def test_a_switch_drops_the_answer_for_the_log_shown_before(make):
    calls = []

    def later(fn, on_done=None, on_error=None, on_finished=None):
        calls.append((fn, on_done))

    viewer = make(run=later)
    viewer.show_log("jobs")
    calls[0][1](calls[0][0]())                                           # the app log's answer arrives late
    assert viewer.model.rowCount() == 0 and not viewer._loaded
    calls[1][1](calls[1][0]())
    assert viewer.stack.currentWidget() is viewer.empty_label and viewer.log_key == "jobs"


def test_one_viewer_at_a_time_and_it_moves_to_a_new_owner(qapp, logs):
    first_owner, second_owner = QWidget(), QWidget()
    viewer = None
    try:
        viewer = open_log_viewer(first_owner, log_dir=logs, run=_sync_run, poll_ms=0)
        assert viewer.isVisible() and viewer.parentWidget() is first_owner and viewer.isWindow()
        assert log_viewer.current_viewer() is viewer
        again = open_log_viewer(second_owner, log_dir=logs)
        assert again is viewer and viewer.parentWidget() is second_owner and viewer.isVisible()
        viewer.move_to(None)
        assert viewer.parentWidget() is None and viewer.isVisible() and shown(viewer) == APP_LINES
        viewer.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert log_viewer.current_viewer() is None, "closed means deleted: the next one is new"
        viewer = None
    finally:
        dispose(qapp, viewer, first_owner, second_owner)


def test_close_button_and_esc_close_it_and_it_is_a_dialog_type_window(qapp, logs):
    # owner, 2026-10-17: "how do I exit the log viewer" - in the container a normal window is maximized without a title
    # bar; a dialog-type window gets one with a close button, and the viewer has its own Close and Esc too
    owner = QWidget()
    viewer = None
    try:
        viewer = open_log_viewer(owner, log_dir=logs, run=_sync_run, poll_ms=0)
        assert viewer.windowType() == Qt.WindowType.Dialog and viewer.isWindow()
        assert viewer.windowFlags() & Qt.WindowType.WindowCloseButtonHint
        assert viewer.btn_close.isVisible() and viewer.btn_close.text() == "Close"
        viewer.btn_close.click()
        assert viewer._closed and not viewer.isVisible()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert log_viewer.current_viewer() is None
        viewer = open_log_viewer(owner, log_dir=logs, run=_sync_run, poll_ms=0)
        viewer.activateWindow()
        viewer.search.setFocus()
        wait_until(qapp, lambda: viewer.isActiveWindow())
        QTest.keyClick(viewer.search, Qt.Key.Key_Escape)
        assert viewer._closed and not viewer.isVisible(), "Esc closes it, also from the search box"
        viewer.move_to(None)
        assert viewer.windowType() == Qt.WindowType.Dialog, "a new owner keeps the dialog type"
        viewer = None
    finally:
        dispose(qapp, viewer, owner)
