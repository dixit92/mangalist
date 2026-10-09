"""The duplicates view: sections, defaults, Keep / Discard, the confirmed Apply, the lock refusal. Fakes and temp folders only."""

from __future__ import annotations

import os
import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from mangalist.duplicates import find_duplicate_files, human_size  # noqa: E402
from mangalist.gui import duplicates_view  # noqa: E402
from mangalist.gui.duplicates_view import ConfirmDiscardDialog, DuplicatesView  # noqa: E402
from mangalist.gui.shell import DuplicateSeries  # noqa: E402
from mangalist.store.lock import LOCK_NAME, RootLock  # noqa: E402
from tests.duplicates.conftest import Library, db, lib  # noqa: E402,F401
from tests.gui.conftest import wait_until  # noqa: E402

S = "Example Series"
T0 = 1_700_000_000_000_000_000


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def drop(qapp, widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


class Views:
    """Makes views over a db and deletes them at the end (PySide6 6.12 crashes on exit for widgets that outlive the app)."""

    def __init__(self, qapp, db):
        self.qapp, self.db, self.made = qapp, db, []
        self.asked = []

    def make(self, answer=True, **kw):
        def confirm(files):
            self.asked.append(list(files))
            return answer
        view = DuplicatesView(self.db, confirm=confirm, **kw)
        self.made.append(view)
        return view

    def scanned(self, **kw):
        view = self.make(**kw)
        view.refresh()
        wait_until(self.qapp, lambda: not view._scanning)
        return view


@pytest.fixture
def views(qapp, db):
    v = Views(qapp, db)
    yield v
    for view in v.made:
        view.stop()
        drop(qapp, view)


@pytest.fixture
def pair(lib):
    older = lib.add(S, "Series c001.cbz", size=10, mtime_ns=T0)
    newer = lib.add(S, "Series c001 [2].cbz", size=20, mtime_ns=T0 + 5_000_000_000)
    lib.scan()
    return lib, older, newer


def texts(view):
    return [label.text() for label in view.findChildren(QLabel)]


def button(view, text):
    return next(b for b in view.findChildren(QPushButton) if b.text() == text)


# --- the files section --------------------------------------------------------------------------------------------

def test_an_empty_library_says_so_and_cannot_apply(views):
    view = views.scanned()
    assert any("No number is held by more than one file" in t for t in texts(view))
    assert any("No series is held in more than one folder" in t for t in texts(view))
    assert not view.apply_button.isEnabled()


def test_the_default_keeps_the_newest_and_discards_the_other(views, pair):
    _lib, older, newer = pair
    seen = []
    view = views.make()
    view.groups_found.connect(seen.append)
    view.refresh()
    wait_until(views.qapp, lambda: seen)
    assert seen == [1]
    assert view.discarded_paths() == [str(older)]
    assert view.apply_button.isEnabled() and view.apply_button.text() == "Apply (1)"
    assert any("1 file to discard (10 B)" in t for t in texts(view))
    shown = " ".join(texts(view))
    assert "Chapter 1" in shown and "2 files" in shown and "newest" in shown and "largest" in shown
    assert human_size(20) in shown


def test_the_last_copy_of_a_number_cannot_be_discarded(views, pair):
    _lib, older, newer = pair
    view = views.scanned()
    assert view.set_discard(str(newer), True) is False
    assert view.discarded_paths() == [str(older)]
    assert any("Keep at least one" in t for t in texts(view))
    # but moving the choice is fine: keep the old one, discard the new one
    assert view.set_discard(str(older), False) is True
    assert view.set_discard(str(newer), True) is True
    assert view.discarded_paths() == [str(newer)]


def test_three_copies_can_all_but_one_be_discarded(views, lib):
    paths = [lib.add(S, n, size=s, mtime_ns=T0 + i) for i, (n, s) in
             enumerate((("Series c001.cbz", 5), ("Series c001 [2].cbz", 6), ("Series c001 [3].cbz", 7)))]
    lib.scan()
    view = views.scanned()
    assert view.discarded_paths() == [str(paths[1]), str(paths[0])]       # the newest stays
    assert view.set_discard(str(paths[2]), True) is False


def test_apply_lists_every_file_and_does_nothing_when_the_owner_says_no(views, pair):
    _lib, older, newer = pair
    view = views.scanned(answer=False)
    view.apply()
    assert [[f.path for f in files] for files in views.asked] == [[str(older)]]
    assert older.exists() and newer.exists()
    assert any("Nothing was deleted" in t for t in texts(view))


def test_apply_deletes_after_the_confirmation_and_tells_the_shell(views, pair):
    _lib, older, newer = pair
    view = views.scanned()
    deleted = []
    view.files_deleted.connect(deleted.append)
    view.apply()
    wait_until(views.qapp, lambda: deleted)
    assert deleted == [[str(older)]]
    assert not older.exists() and newer.exists()
    wait_until(views.qapp, lambda: not view._scanning)
    assert view.file_groups == [] and view.discarded_paths() == []
    assert any("Deleted 1 file." in t for t in texts(view))
    assert not view.apply_button.isEnabled()


def test_apply_refuses_while_a_root_is_locked_and_does_not_even_ask(views, pair):
    lib, older, _newer = pair
    view = views.scanned()
    with RootLock(lib.dir, host="another-host", pid=1):
        view.apply()
    assert views.asked == [] and older.exists()
    assert view.report.isVisibleTo(view) and "another-host" in view.report.text()


def test_a_lock_taken_after_the_confirmation_still_refuses(views, pair):
    lib, older, _newer = pair
    view = views.scanned()
    lock = RootLock(lib.dir, host="another-host", pid=1)

    def confirm(files):
        lock.acquire()           # a scan's filing starts while the dialog is open
        return True
    view._confirm = confirm
    view.apply()
    wait_until(views.qapp, lambda: not view._applying)
    lock.release()
    assert older.exists()
    assert "busy" in view.report.text() and str(older) in view.report.text()


def test_the_shell_can_block_deleting_while_it_scans(views, pair):
    _lib, older, _newer = pair
    view = views.scanned()
    view.set_blocked("a scan is running")
    assert not view.apply_button.isEnabled()
    assert any("a scan is running" in t for t in texts(view))
    view.apply()
    assert views.asked == [] and older.exists()
    view.set_blocked(None)
    assert view.apply_button.isEnabled()


def test_a_file_that_changed_since_the_list_is_skipped_and_reported(views, pair):
    _lib, older, newer = pair
    view = views.scanned()
    older.write_bytes(b"y" * 99)
    deleted = []
    view.files_deleted.connect(deleted.append)
    view.apply()
    wait_until(views.qapp, lambda: "changed" in view.report.text())
    wait_until(views.qapp, lambda: not view._scanning)
    assert older.exists() and deleted == []


def test_choices_survive_a_refresh(views, lib):
    paths = [lib.add(S, n, size=s, mtime_ns=T0 + i) for i, (n, s) in
             enumerate((("Series c001.cbz", 5), ("Series c001 [2].cbz", 6), ("Series c001 [3].cbz", 7)))]
    lib.scan()
    view = views.scanned()
    view.set_discard(str(paths[2]), False)
    view.set_discard(str(paths[1]), False)       # keep the two newest, discard the oldest
    assert view.discarded_paths() == [str(paths[0])]
    view.refresh()
    wait_until(views.qapp, lambda: not view._scanning)
    assert view.discarded_paths() == [str(paths[0])]


def test_a_scan_that_fails_is_shown(views, monkeypatch):
    def boom(db):
        raise RuntimeError("secret")
    monkeypatch.setattr(duplicates_view, "find_duplicate_files", boom)
    view = views.scanned()
    assert "Could not look for duplicates" in view.status.text() and "secret" not in view.status.text()


def test_a_scan_in_flight_when_the_view_is_stopped_is_dropped(views, pair, monkeypatch):
    gate = threading.Event()
    real = find_duplicate_files

    def slow(db):
        gate.wait(5)
        return real(db)
    monkeypatch.setattr(duplicates_view, "find_duplicate_files", slow)
    view = views.make()
    seen = []
    view.groups_found.connect(seen.append)
    view.refresh()
    assert view._scanning and not view.apply_button.isEnabled()
    view.stop()
    gate.set()
    for _ in range(50):
        views.qapp.processEvents()
        threading.Event().wait(0.01)
    assert seen == []


def test_only_the_newest_scan_counts(views, pair, monkeypatch):
    gate = threading.Event()
    real = find_duplicate_files
    calls = []

    def maybe_slow(db):
        calls.append(1)
        if len(calls) == 1:
            gate.wait(5)
            return []                    # the first scan's (stale) answer
        return real(db)
    monkeypatch.setattr(duplicates_view, "find_duplicate_files", maybe_slow)
    view = views.make()
    view.refresh()
    view.refresh()
    wait_until(views.qapp, lambda: view.file_groups)
    gate.set()
    for _ in range(30):
        views.qapp.processEvents()
        threading.Event().wait(0.01)
    assert len(view.file_groups) == 1


# --- the confirmation dialog --------------------------------------------------------------------------------------

def test_the_confirmation_lists_every_file_and_cancel_is_the_default(qapp, pair):
    lib, older, newer = pair
    (group,) = find_duplicate_files(lib.db)
    dialog = ConfirmDiscardDialog(list(group.files))
    try:
        assert dialog.list.count() == 2
        assert all(str(p) in " ".join(dialog.list.item(i).text() for i in range(2)) for p in (older, newer))
        assert dialog.cancel_button.isDefault() and not dialog.delete_button.isDefault()
        assert "Delete 2 files" in dialog.delete_button.text()
        assert any("no holding folder" in l.text() for l in dialog.findChildren(QLabel))
    finally:
        drop(qapp, dialog)


# --- the series section -------------------------------------------------------------------------------------------

def test_series_in_more_than_one_folder_side_by_side(views, lib, tmp_path):
    other = Library(lib.db, tmp_path / "other", "Other")
    lib.add(S, "Series v01.cbz", size=1024, mtime_ns=T0)
    lib.add(S, "Series v02.cbz", size=2048, mtime_ns=T0 + 1_000_000_000)
    other.add("Example Series (2)", "Series v01.cbz", size=4096, mtime_ns=T0)
    lib.scan()
    other.scan()
    opened, shown = [], []
    view = views.make(open_folder=opened.append)
    view.show_in_list.connect(shown.append)
    one, two = str(lib.dir / S), str(other.dir / "Example Series (2)")
    view.set_series_duplicates([DuplicateSeries(S, (one, two))])
    text = " ".join(texts(view))
    assert "SERIES IN MORE THAN ONE FOLDER" in text and "2 folders" in text
    assert "Manga" in text and "Other" in text                       # the roots
    assert "3.0 KB" in text and "4.0 KB" in text                      # the sizes
    assert [b.text() for b in view.findChildren(QPushButton)].count("Show in list") == 2
    button(view, "Show in list").click()
    button(view, "Open folder").click()
    assert shown == [one] and opened == [one]
    view.set_series_duplicates([])
    assert any("No series is held in more than one folder" in t for t in texts(view))


def test_a_series_folder_that_was_never_scanned_shows_dashes(views, lib):
    view = views.make()
    view.set_series_duplicates([DuplicateSeries("Unscanned", (str(lib.dir / "Unscanned"), str(lib.dir / "Other")))])
    assert "-" in texts(view)


def test_a_file_in_a_subfolder_shows_its_place(views, lib):
    lib.add(S, "Season 2/Series c003.cbz", mtime_ns=T0)
    lib.add(S, "Season 2/Series c003 [2].cbz", mtime_ns=T0 + 1)
    lib.scan()
    view = views.scanned()
    shown = " ".join(texts(view))
    assert "in Season 2" in shown
    assert os.path.join("Season 2", "Series c003.cbz") in " ".join(
        row.name.full_text() for row in view._rows.values())
