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
from mangalist.same_series import FranchiseSplit, SamePair, SameSeriesReport, SeriesCopy  # noqa: E402
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
    assert any("No series is held in two folders" in t for t in texts(view))
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
    def boom(db, root_ids=None):
        raise RuntimeError("secret")
    monkeypatch.setattr(duplicates_view, "find_duplicate_files", boom)
    view = views.scanned()
    assert "Could not look for duplicates" in view.status.text() and "secret" not in view.status.text()


def test_a_scan_in_flight_when_the_view_is_stopped_is_dropped(views, pair, monkeypatch):
    gate = threading.Event()
    real = find_duplicate_files

    def slow(db, root_ids=None):
        gate.wait(5)
        return real(db, root_ids=root_ids)
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

    def maybe_slow(db, root_ids=None):
        calls.append(1)
        if len(calls) == 1:
            gate.wait(5)
            return []                    # the first scan's (stale) answer
        return real(db, root_ids=root_ids)
    monkeypatch.setattr(duplicates_view, "find_duplicate_files", maybe_slow)
    view = views.make()
    view.refresh()
    wait_until(views.qapp, lambda: calls)    # the first scan is inside (and held) before the second starts: the
    view.refresh()                           # threads' start order alone would not say which one is held
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

def _copy(library, rel, series_id=0, *, files=1, status="Ongoing", link="Own match: Confirmed", confirmed=True):
    return SeriesCopy(series_id=series_id, root_id=library.root.id, root_name=library.root.name, rel_path=rel,
                      folder=str(library.dir / rel), files=files, title="Example Record", link=link,
                      origin_status=status, confirmed=confirmed)


def _confirm(library, rel, mu_id=4242):
    from pathlib import Path

    from mangalist import mu_cache
    from mangalist.store.series import link_key

    mu_cache.save_entry(Path(link_key(library.dir.resolve(), rel)), mu_id, "Example Record", "https://example.invalid/s",
                        None, mu_confirmed=True)


def test_same_series_in_two_folders_side_by_side(views, lib, tmp_path):
    other = Library(lib.db, tmp_path / "other", "Other")
    lib.add(S, "Series v01.cbz", size=1024, mtime_ns=T0)
    lib.add(S, "Series v02.cbz", size=2048, mtime_ns=T0 + 1_000_000_000)
    other.add("Example Series (2)", "Series v01.cbz", size=4096, mtime_ns=T0)
    lib.scan()
    other.scan()
    opened, shown = [], []
    view = views.make(open_folder=opened.append)
    view.show_in_list.connect(shown.append)
    one, two = _copy(lib, S, status="Complete"), _copy(other, "Example Series (2)", link="MangaPixer: Auto")
    split = FranchiseSplit("mu:2", "Split Saga", (_copy(lib, "Split Saga/Blue Arc"), _copy(other, "Split Saga/Red Arc")))
    view.set_same_series(SameSeriesReport([SamePair("mu:1", "Example Record", one, two)], [split]))
    text = " ".join(texts(view))
    assert "SAME SERIES IN TWO FOLDERS" in text and "in two libraries" in text
    assert "Manga" in text and "Other" in text                       # the libraries
    assert "3.0 KB" in text and "4.0 KB" in text                      # the sizes
    assert "Complete" in text and "MangaPixer: Auto" in text          # what tells the copies apart
    assert [b.text() for b in view.findChildren(QPushButton)].count("Show in list") == 2
    button(view, "Show in list").click()
    button(view, "Open folder").click()
    assert shown == [one.folder] and opened == [one.folder]
    merge = view.merge_buttons
    assert len(merge) == 1 and merge[0].isEnabled() and [a.text() for a in merge[0].menu().actions()] == [
        f"Keep Manga / {S} (move Example Series (2)'s files into it)",
        "Keep Other / Example Series (2) (move Example Series's files into it)"]
    # the franchise split: a notice with Open folder only, never a merge
    assert "PARTS OF ONE FRANCHISE IN DIFFERENT LIBRARIES  ·  1" in text and "Split Saga" in text
    assert view.splits_heading.isVisibleTo(view) and len(view.merge_buttons) == 1
    assert [b.text() for b in view.findChildren(QPushButton)].count("Open folder") == 4
    view.set_library_scope([other.root.id])                          # the twin lies in the other library: still shown
    assert len(view.same_series.pairs) == 1 and len(view.same_series.splits) == 1
    view.set_library_scope([999])
    view._render_series()                                            # (a hidden view renders when it is shown)
    assert view.same_series.pairs == [] and any("No series is held in two folders" in t for t in texts(view))
    assert not view.splits_heading.isVisibleTo(view)


def test_a_pair_with_an_auto_match_is_listed_but_merge_is_disabled_with_the_reason(views, lib, tmp_path):
    other = Library(lib.db, tmp_path / "other", "Other")
    view = views.make()
    auto = _copy(other, "Example Series (2)", link="MangaPixer: Auto", confirmed=False)
    view.set_same_series(SameSeriesReport([SamePair("mu:1", "Example Record", _copy(lib, S), auto)]))
    merge = view.merge_buttons[0]
    assert not merge.isEnabled()
    assert "confirm the match of Other / Example Series (2) first" in merge.toolTip()
    assert any(t.startswith("Merge is not offered: confirm the match of Other / Example Series (2) first")
               for t in texts(view))


def test_a_nested_copy_offers_one_merge_only(views, lib):
    view = views.make()
    outer, inner = _copy(lib, "Elder Tale"), _copy(lib, "Elder Tale/Elder Tale")
    view.set_same_series(SameSeriesReport([SamePair("mu:1", "Elder Tale", outer, inner, "nested")]))
    assert "a copy inside the series folder" in " ".join(texts(view))
    assert [a.text() for a in view.merge_buttons[0].menu().actions()] == [
        "Keep Manga / Elder Tale (move Elder Tale's files into it)"]


def test_a_series_folder_that_was_never_scanned_shows_dashes(views, lib):
    view = views.make()
    view.set_same_series(SameSeriesReport([SamePair("mu:1", "Unscanned", _copy(lib, "Unscanned"), _copy(lib, "Other"))]))
    assert "-" in texts(view)


@pytest.fixture
def twins(lib, tmp_path):
    other = Library(lib.db, tmp_path / "other", "Other")
    lib.add(S, "Series v01.cbz", size=10, mtime_ns=T0)
    other.add("Example Series (EN)", "Series v02.cbz", size=20, mtime_ns=T0)
    lib.scan()
    other.scan()
    _confirm(lib, S)
    _confirm(other, "Example Series (EN)")
    return lib, other


def test_merge_into_moves_the_files_then_undo_moves_them_back(views, twins, qapp):
    lib, other = twins
    asked, merged = [], []
    view = views.make(confirm_merge=lambda check: asked.append(check) or True,
                      confirm_undo=lambda record: asked.append(record) or True)
    view.folders_merged.connect(lambda paths, kept: merged.append((sorted(paths), kept)))
    gone, kept = str(lib.dir / S), str(other.dir / "Example Series (EN)")
    view.merge(gone, kept)
    wait_until(qapp, lambda: not view._merging and not view._scanning)
    assert asked[0].moves and sorted(os.listdir(kept)) == ["Series v01.cbz", "Series v02.cbz"]
    assert not os.path.exists(gone)                                  # emptied, so removed
    assert merged == [(sorted([gone, kept]), kept)]
    assert "Merged 1 item of Manga / Example Series into Other / Example Series (EN)" in view.report.text()
    assert view.undo_merge_button.isVisibleTo(view)
    assert view.undo_merge_button.text() == "Undo last merge (1 item back to Example Series)"
    view.undo_merge_button.click()
    wait_until(qapp, lambda: not view._merging and not view._scanning)
    assert os.listdir(gone) == ["Series v01.cbz"] and os.listdir(kept) == ["Series v02.cbz"]
    assert merged[-1] == (sorted([gone, kept]), "") and "Moved 1 item back" in view.report.text()
    assert not view.undo_merge_button.isVisibleTo(view)


def test_merge_refused_while_a_download_is_in_progress(views, twins):
    lib, other = twins
    with lib.db.connect() as con:
        con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status) VALUES"
                    " ('2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', ?, '{}', 'qbittorrent', 'sent')",
                    (lib.series_id(S),))
    asked = []
    view = views.make(confirm_merge=lambda check: asked.append(check) or True)
    view.merge(str(lib.dir / S), str(other.dir / "Example Series (EN)"))
    wait_until(views.qapp, lambda: not view._merging)
    assert asked == [] and "Not merged: a download is in progress" in view.report.text()
    assert os.path.exists(lib.dir / S / "Series v01.cbz")


def test_merge_cancelled_moves_nothing(views, twins):
    lib, other = twins
    view = views.make(confirm_merge=lambda check: False)
    view.merge(str(lib.dir / S), str(other.dir / "Example Series (EN)"))
    wait_until(views.qapp, lambda: not view._merging)
    assert view.status.text() == "Nothing was merged." and os.path.exists(lib.dir / S / "Series v01.cbz")


def test_a_file_in_a_subfolder_shows_its_place(views, lib):
    lib.add(S, "Season 2/Series c003.cbz", mtime_ns=T0)
    lib.add(S, "Season 2/Series c003 [2].cbz", mtime_ns=T0 + 1)
    lib.scan()
    view = views.scanned()
    shown = " ".join(texts(view))
    assert "in Season 2" in shown
    assert os.path.join("Season 2", "Series c003.cbz") in " ".join(
        row.name.full_text() for row in view._rows.values())


# --- one series at a time, MangaPixer links -----------------------------------------------------------------------

@pytest.fixture
def two_series(lib):
    a_old = lib.add(S, "Series c001.cbz", size=10, mtime_ns=T0)
    a_new = lib.add(S, "Series c001 [2].cbz", size=20, mtime_ns=T0 + 5_000_000_000)
    b_old = lib.add("Other Series", "Other v01.cbz", size=10, mtime_ns=T0)
    b_new = lib.add("Other Series", "Other v01 [2].cbz", size=20, mtime_ns=T0 + 5_000_000_000)
    lib.scan()
    return lib, (a_old, a_new), (b_old, b_new)


def test_a_series_own_apply_deletes_only_that_series(views, two_series):
    _lib, (a_old, a_new), (b_old, b_new) = two_series
    view = views.scanned()
    assert view.apply_button.text() == "Apply (2)"
    own = [b for b in view.findChildren(QPushButton) if b.text() == "Apply for this series (1)"]
    assert len(own) == 2                                            # one per series card
    deleted = []
    view.files_deleted.connect(deleted.append)
    own[0].click()                                                  # the cards follow the series titles
    wait_until(views.qapp, lambda: deleted)
    assert [len(f) for f in views.asked] == [1]                     # the confirmation listed that series' file only
    assert deleted == [[str(a_old)]] and not a_old.exists()         # "Example Series" sorts first
    assert b_old.exists() and a_new.exists() and b_new.exists()


def test_focus_shows_one_series_and_its_apply_stays_inside_it(views, two_series):
    _lib, (a_old, _a_new), (b_old, _b_new) = two_series
    view = views.scanned()
    view.focus_series(str(a_old.parent))
    assert {g.folder for g, _rows in view._by_group} == {str(a_old.parent)}
    assert view.apply_button.text() == "Apply (1)" and view.show_all_button.isVisibleTo(view)
    assert any("IN THIS SERIES" in t for t in texts(view))
    view.apply()
    wait_until(views.qapp, lambda: not view._applying and not view._scanning)
    assert not a_old.exists() and b_old.exists()
    assert view._by_group == [] and any("This series has no duplicate files." in t for t in texts(view))
    view.show_all_button.click()
    assert {g.folder for g, _rows in view._by_group} == {str(b_old.parent)}  # the other series is back


def test_open_in_mangapixer_on_a_known_series_only(views, two_series, monkeypatch):
    _lib, (a_old, _a), (_b, _b2) = two_series
    known = str(a_old.parent)
    view = views.scanned()
    assert not [b for b in view.findChildren(QPushButton) if b.text() == "Open in MangaPixer"]
    view.set_series_link(lambda folder: "http://mangapixer.example:8080/series/abc123" if folder == known else None)
    links = [b for b in view.findChildren(QPushButton) if b.text() == "Open in MangaPixer"]
    assert len(links) == 1 and "/series/abc123" in links[0].toolTip()
    opened = []
    monkeypatch.setattr(duplicates_view, "open_link", lambda url: opened.append(url) or True)
    links[0].click()
    assert opened == ["http://mangapixer.example:8080/series/abc123"]


def test_without_a_browser_the_link_is_copied_and_said(views, pair, monkeypatch):
    from mangalist.gui import links

    monkeypatch.setattr(links.QDesktopServices, "openUrl", staticmethod(lambda url: False))
    view = views.scanned()
    view.open_link("http://mangapixer.example:8080/series/abc123")
    assert QApplication.clipboard().text() == "http://mangapixer.example:8080/series/abc123"
    assert any("link is copied" in t for t in texts(view))


def test_apply_shows_it_is_working_until_the_rescan_and_the_reread_are_done(views, pair, monkeypatch):
    _lib, older, _newer = pair
    gate = threading.Event()
    real = duplicates_view.discard_duplicates

    def held(db, selections):
        gate.wait(5)
        return real(db, selections)
    monkeypatch.setattr(duplicates_view, "discard_duplicates", held)
    view = views.scanned()
    view.show()
    deleted = []
    view.files_deleted.connect(lambda paths: (deleted.append(paths), view.set_blocked("the library is being rescanned")))
    assert not view.busy_bar.isVisibleTo(view) and view._body.isEnabled()
    view.apply()                                                   # confirmed by the fixture
    assert view.busy_bar.isVisibleTo(view) and not view._body.isEnabled()
    assert view.status.text() == "Deleting 1 file..." and not view.apply_button.isEnabled()
    gate.set()
    wait_until(views.qapp, lambda: deleted and not view._scanning)
    assert view.status.text() == "Rescanning the library..." and view.busy_bar.isVisibleTo(view)  # the shell's rescan
    assert not view._body.isEnabled() and not older.exists()
    view.set_blocked(None)                                         # the rescan finished; the shell re-reads
    view.refresh()
    assert view.status.text() == "Looking for duplicate files..." and view.busy_bar.isVisibleTo(view)
    wait_until(views.qapp, lambda: not view._scanning)
    assert not view.busy_bar.isVisibleTo(view) and view._body.isEnabled() and view.status.text() == ""


def test_an_apply_that_deletes_nothing_does_not_stay_busy(views, pair):
    lib, older, _newer = pair
    view = views.scanned()
    view.show()
    older.write_bytes(b"changed")                                  # refused: it changed since the list was made
    view.apply()
    wait_until(views.qapp, lambda: not view._applying and not view._scanning)
    assert not view.busy_bar.isVisibleTo(view) and view._body.isEnabled()


def test_apply_to_selected_deletes_only_in_the_ticked_series(views, two_series):
    _lib, (a_old, a_new), (b_old, b_new) = two_series
    view = views.scanned()
    view.show()
    assert not view.apply_selected_button.isVisibleTo(view)             # nothing ticked
    view.pick_series([str(b_old.parent)])
    assert view.apply_selected_button.isVisibleTo(view)
    assert view.apply_selected_button.text() == "Apply to selected (1 series, 1 file)"
    deleted = []
    view.files_deleted.connect(deleted.append)
    view.apply_selected_button.click()
    wait_until(views.qapp, lambda: deleted and not view._scanning)
    assert [len(f) for f in views.asked] == [1] and deleted == [[str(b_old)]]
    assert a_old.exists() and a_new.exists() and not b_old.exists() and b_new.exists()
    assert view._picked == set() and not view.apply_selected_button.isVisibleTo(view)   # its series left the list


def test_apply_to_selected_covers_every_ticked_series(views, two_series):
    _lib, (a_old, _a_new), (b_old, _b_new) = two_series
    view = views.scanned()
    view.pick_series([str(a_old.parent), str(b_old.parent)])
    assert view.apply_selected_button.text() == "Apply to selected (2 series, 2 files)"
    view.apply(series=set(view._picked))
    wait_until(views.qapp, lambda: not view._applying and not view._scanning)
    assert [len(f) for f in views.asked] == [2] and not a_old.exists() and not b_old.exists()


def test_the_library_scope_limits_the_duplicate_files(views, lib, db, tmp_path):
    from tests.duplicates.conftest import Library

    lib.add(S, "Series c001.cbz", size=10, mtime_ns=T0)
    lib.add(S, "Series c001 [2].cbz", size=20, mtime_ns=T0 + 1)
    lib.scan()
    other = Library(db, tmp_path / "other-share", "Manhwa")
    other.add("Other Series", "Other c001.cbz", size=10, mtime_ns=T0)
    other.add("Other Series", "Other c001 [2].cbz", size=20, mtime_ns=T0 + 1)
    other.scan()
    view = views.scanned()
    assert {g.title for g in view.file_groups} == {S, "Other Series"}
    view.set_library_scope([other.root.id])
    view.refresh()
    wait_until(views.qapp, lambda: not view._scanning)
    assert {g.title for g in view.file_groups} == {"Other Series"}
    view.set_library_scope(None)
    view.refresh()
    wait_until(views.qapp, lambda: not view._scanning)
    assert len(view.file_groups) == 2
