"""Upgrades in the GUI (offscreen Qt, fakes): the Upgrades group searches nyaa and sends the upgrade volumes, a series in
two nyaa groups is one search, the line above the releases says what happens to the replaced chapters, the
replaced-chapters line and its review (Restore, Move, Keep, Delete only after the confirmation listing every file,
Cancel the default), and Settings > Automation's replaced-chapters choices (a holding folder inside a root refused)."""

from __future__ import annotations

import os
import threading
from dataclasses import replace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog  # noqa: E402

from mangalist import store, upgrades  # noqa: E402
from mangalist.gui import download_tab as dt  # noqa: E402
from mangalist.gui.download_tab import DownloadTab  # noqa: E402
from mangalist.gui.replaced_chapters import ConfirmReplaceDialog, ReplacedDialog  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from mangalist.gui.shell import GROUP_UPGRADES, GROUP_VOLUMES, WantedSeries  # noqa: E402
from mangalist.store.replacements import Batch, ReplacedFile  # noqa: E402

from .conftest import FakeBackend, candidate, qapp, wait_until  # noqa: E402,F401

FOLDER = "/lib/Example Upgrade"


def wanted(group=GROUP_UPGRADES, missing=("1", "2", "3"), gaps="Upgrade vol. 1-3", findable=True):
    return WantedSeries(5, FOLDER, "Example Upgrade", group, gaps, missing=missing, held=(), titles=("Example Upgrade",),
                        findable=findable)


def batch(id=1, status="pending", n=3, mode="delete"):
    files = tuple(ReplacedFile(f"{FOLDER}/Chapters/c00{i}.cbz", f"Chapters/c00{i}.cbz", 1000 + i,
                               "2026-10-01T00:00:00.000000+00:00", str(i), "1") for i in range(1, n + 1))
    return Batch(id=id, download_id=id, series_id=5, root_path="/lib", series_dir=FOLDER, volumes=("1",),
                 volume_files=((f"{FOLDER}/v01.cbz", 9000),), files=files, kept=(("Chapters/c004.cbz", "not in the filed volumes"),),
                 mode=mode, status=status, plan_id=None, holding_dir="/hold/x" if status == "held" else None,
                 purge_after="2030-11-08T12:00:00+00:00" if status == "held" else None, error=None,
                 created_at="", updated_at="")


class FakeReplaced:
    """The replaced-chapters service: canned batches, every call recorded (with the thread it ran on)."""

    def __init__(self, batches=(), mode="delete"):
        self.batches = list(batches)
        self.mode = mode
        self.calls = []
        self.threads = []

    def settings(self):
        return upgrades.ReplacedSettings(mode=self.mode)

    def open_batches(self):
        self.threads.append(threading.get_ident())
        return [b for b in self.batches if b.status in ("pending", "held")]

    def series_title(self, b):
        return os.path.basename(b.series_dir)

    def _set(self, bid, status):
        self.batches = [replace(b, status=status) if b.id == bid else b for b in self.batches]
        return next(b for b in self.batches if b.id == bid)

    def empty_now(self, bid):
        self.calls.append(("empty_now", bid))
        self.threads.append(threading.get_ident())
        return self._set(bid, "purged")

    def empty_now(self, bid):
        self.calls.append(("empty_now", bid))
        self.threads.append(threading.get_ident())
        return self._set(bid, "purged")

    def restore(self, bid):
        self.calls.append(("restore", bid))
        self.threads.append(threading.get_ident())
        return self._set(bid, "restored")

    def hold(self, bid):
        self.calls.append(("hold", bid))
        return self._set(bid, "held")

    def keep(self, bid):
        self.calls.append(("keep", bid))
        return self._set(bid, "declined")

    def delete(self, bid, paths):
        self.calls.append(("delete", bid, tuple(paths)))
        self._set(bid, "deleted")
        return [upgrades.DeleteOutcome(p, True) for p in paths]


@pytest.fixture(autouse=True)
def _cleanup():
    made = []
    for cls in (DownloadTab, ReplacedDialog):
        original = cls.__init__

        def tracking(self, *a, _original=original, **kw):
            _original(self, *a, **kw)
            made.append(self)

        cls.__init__ = tracking
        made.append((cls, original))
    yield
    for item in made:
        if isinstance(item, tuple):
            item[0].__init__ = item[1]
    for widget in [w for w in made if not isinstance(w, tuple)]:
        if isinstance(widget, DownloadTab):
            widget.stop()
        widget.deleteLater()


def make(qapp, series, service=None, backend=None, **kw):
    backend = backend or FakeBackend(results=[candidate("Example Upgrade v01-03 (Digital)", vol_from="1", vol_to="3",
                                                        covers_missing=("1", "2", "3"))])
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0,
                      replaced=service if service is not None else FakeReplaced(mode="holding"), **kw)
    tab.resize(1200, 800)
    tab.set_wanted(series)
    return tab, backend


def settle(qapp, tab):
    wait_until(qapp, lambda: tab._running is None and not tab._queue and not tab._delay.isActive())


def test_an_upgrade_is_searched_on_nyaa_and_sent_for_the_upgrade_volumes(qapp):
    tab, backend = make(qapp, [wanted()])
    heads = [tab.tree.topLevelItem(i) for i in range(tab.tree.topLevelItemCount())
             if tab.tree.topLevelItem(i).data(0, dt.ROLE_HEADER)]
    assert heads[-1].data(0, dt.ROLE_HEADER) == "UPGRADES · 1" and heads[-1].data(0, dt.ROLE_ASIDE) == "nyaa"
    tab.focus(FOLDER)
    settle(qapp, tab)
    wait_until(qapp, lambda: tab.releases.btn_send.isEnabled())
    assert backend.searched == [(("Example Upgrade",), ("1", "2", "3"), ())]
    note = tab.upgrade_note_text()
    assert note.startswith("Upgrade v01-v03:") and "holding folder, restorable for 30 days" in note
    assert tab.upgrade_label.isVisibleTo(tab)
    assert tab.releases.send_selected()
    wait_until(qapp, lambda: backend.sent)
    assert backend.sent[0][0] == 5 and backend.sent[0][2] == ("1", "2", "3")


def test_a_series_with_missing_volumes_and_upgrades_is_one_search(qapp):
    series = [wanted(GROUP_VOLUMES, ("7",), "Vol. 7"), wanted()]
    tab, backend = make(qapp, series, service=FakeReplaced(mode="delete"))
    # one series, two groups: one row per group (one group shown at a time), one search for all its volumes
    assert tab.count_label.text() == "1 series" and len(tab._rows[FOLDER]) == 1
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert backend.searched[0][1] == ("1", "2", "3", "7")
    assert "listed for you to confirm" in tab.upgrade_note_text()
    tab.set_checked(FOLDER)                                  # ticked in Volumes ...
    tab.show_group(GROUP_UPGRADES)                           # ... and so in Upgrades too
    assert len(tab._rows[FOLDER]) == 1 and tab._rows[FOLDER][0].checkState(0) == Qt.CheckState.Checked
    assert tab.checked_folders() == [FOLDER]


def test_an_upgrade_the_shell_does_not_offer_says_why_and_asks_nothing(qapp):
    tab, backend = make(qapp, [wanted(findable=False)])
    tab.focus(FOLDER)
    assert "cannot be upgraded from nyaa yet" in tab.releases.message_text.text()
    assert backend.searched == [] and not tab.upgrade_label.isVisibleTo(tab)


def test_the_replaced_line_shows_what_waits_and_is_loaded_off_the_ui_thread(qapp):
    service = FakeReplaced([batch(1, n=24), batch(2, "held", n=2, mode="holding")])
    tab, _ = make(qapp, [], service=service)
    assert not tab.replaced_bar.isVisibleTo(tab)
    tab.refresh_replaced()
    wait_until(qapp, lambda: tab.replaced_bar.isVisibleTo(tab))
    assert tab.replaced_bar.text_label.text() == "Replace 24 chapter files of 1 series with the volumes filed?"
    assert threading.get_ident() not in service.threads


def test_no_service_keeps_the_line_hidden(qapp):
    tab = DownloadTab(FakeBackend(), refresh_ms=0, search_delay_ms=0)
    tab.refresh_replaced()
    assert tab._replaced is None and not tab.replaced_bar.isVisibleTo(tab) and tab.replaced_dialog() is None


def test_delete_needs_the_confirmation_listing_every_file(qapp):
    answers = []
    service = FakeReplaced([batch(1, n=3)], mode="delete")
    tab, _ = make(qapp, [], service=service, confirm_delete=lambda parent, b: answers.pop(0))
    tab.replaced_bar.set_batches(service.open_batches())
    changed = []
    tab.library_changed.connect(changed.append)
    dialog = tab.replaced_dialog()
    assert dialog.btn_delete.isVisibleTo(dialog) and not dialog.btn_hold.isVisibleTo(dialog)
    assert dialog.files.count() == 3 and "Kept in the library (1)" in dialog.kept_label.text()
    answers.append(False)                                   # Cancel: nothing happens
    assert not dialog.delete_selected() and service.calls == []
    answers.append(True)
    assert dialog.delete_selected()
    wait_until(qapp, lambda: not dialog.busy())
    assert service.calls == [("delete", 1, tuple(f.path for f in batch(1).files))]
    assert changed == [[FOLDER]] and "Deleted 3 files" in dialog.message_label.text()
    assert dialog.table.rowCount() == 0                     # reloaded: nothing open any more
    dialog.reject()


def test_restore_keep_and_move_run_off_the_ui_thread(qapp):
    service = FakeReplaced([batch(1, "held", mode="holding"), batch(2, n=1, mode="holding")], mode="holding")
    tab, _ = make(qapp, [], service=service)
    tab.replaced_bar.set_batches(service.open_batches())
    dialog = tab.replaced_dialog()
    assert dialog.btn_restore.isVisibleTo(dialog) and not dialog.btn_keep.isVisibleTo(dialog)
    assert "Held in /hold/x" in dialog.where_label.text()
    assert dialog.restore_selected()
    wait_until(qapp, lambda: not dialog.busy())
    assert ("restore", 1) in service.calls and threading.get_ident() not in service.threads[-1:]
    assert dialog.table.rowCount() == 1 and dialog.btn_hold.isVisibleTo(dialog)    # batch 2 left, pending
    assert dialog.hold_selected()
    wait_until(qapp, lambda: not dialog.busy())
    assert ("hold", 2) in service.calls
    dialog.reject()


def test_the_confirmation_lists_every_file_and_cancel_is_the_default(qapp):
    b = batch(1, n=5)
    dialog = ConfirmReplaceDialog(b, "Example Upgrade")
    try:
        assert dialog.list.count() == 5 and all(f.path in dialog.list.item(i).text()
                                                for i, f in enumerate(b.files))
        assert dialog.cancel_button.isDefault() and not dialog.delete_button.isDefault()
        assert dialog.delete_button.text() == "Delete 5 files"
        dialog.cancel_button.click()
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.deleteLater()


def test_automation_settings_for_replaced_chapters(qapp, tmp_path):
    from mangalist.gui.settings_sections import AutomationPage
    from mangalist.services.mangapixer import open_cache

    from .conftest import FakeBackend as Backend

    store.reset_stores()
    db = store.get_store()
    library = tmp_path / "library" / "Manga"
    library.mkdir(parents=True)
    db.add_root(str(library), "Manga")
    page = AutomationPage(db, Backend(), open_cache(db), env={})
    try:
        assert page.hold_radio.isChecked() and page.holding_edit.text() == upgrades.DEFAULT_HOLDING_FOLDER
        assert page.days_combo.currentData() == 30 and page.days_combo.currentText() == "30 days"
        page.delete_radio.setChecked(True)
        assert upgrades.load_settings(db).mode == "delete" and not page.holding_edit.isEnabled()
        page.hold_radio.setChecked(True)
        page.holding_edit.setText(str(library / "replaced"))            # inside a root: refused, the old value back
        page.holding_edit.editingFinished.emit()
        assert "overlaps the library root" in page.replaced_status.text()
        assert page.holding_edit.text() == upgrades.DEFAULT_HOLDING_FOLDER
        good = str(tmp_path / "appdata" / "replaced")
        page.holding_edit.setText(good)
        page.holding_edit.editingFinished.emit()
        assert upgrades.load_settings(db).holding_folder == good and page.replaced_status.text() == "Holding folder saved."
        page.days_combo.setCurrentIndex(page.days_combo.findData(14))
        assert upgrades.load_settings(db).holding_days == 14
        upgrades.set_holding_days(db, 45)                                  # a value set elsewhere is offered too
        page.refresh()
        assert page.days_combo.currentText() == "45 days"
    finally:
        page.deleteLater()


def test_a_failed_batch_offers_try_again_and_keep(qapp):
    failed = replace(batch(1, mode="holding"), status="failed", error="nothing moved: another filesystem")
    service = FakeReplaced([failed], mode="holding")
    service.retry = lambda bid: service.calls.append(("retry", bid)) or service._set(bid, "held")
    service.open_batches = lambda: [b for b in service.batches if b.status in ("pending", "failed", "held")]
    tab, _ = make(qapp, [], service=service)
    tab.replaced_bar.set_batches(service.open_batches())
    assert "failed" in tab.replaced_bar.text_label.text()
    dialog = tab.replaced_dialog()
    assert dialog.btn_retry.isVisibleTo(dialog) and dialog.btn_keep.isVisibleTo(dialog)
    assert not dialog.btn_hold.isVisibleTo(dialog) and "another filesystem" in dialog.where_label.text()
    assert dialog.retry_selected()
    wait_until(qapp, lambda: not dialog.busy())
    assert service.calls == [("retry", 1)] and dialog.btn_restore.isVisibleTo(dialog)
    dialog.reject()


def test_empty_now_on_a_held_batch_asks_first(qapp):
    service = FakeReplaced([batch(1, "held", mode="holding")], mode="holding")
    tab, _ = make(qapp, [], service=service)
    tab.replaced_bar.set_batches(service.open_batches())
    dialog = tab.replaced_dialog()
    assert dialog.btn_empty.isVisibleTo(dialog)
    dialog._confirm_empty = lambda parent, b: False
    assert not dialog.empty_selected() and service.calls == []           # Cancel: nothing
    dialog._confirm_empty = lambda parent, b: True
    assert dialog.empty_selected()
    wait_until(qapp, lambda: not dialog.busy())
    assert service.calls == [("empty_now", 1)] and threading.get_ident() not in service.threads[-1:]
    dialog.reject()


def test_the_release_panel_says_upgrade_for_an_upgrade(qapp):
    from mangalist.gui.releases_panel import FOOTER_NOTE, UPGRADE_FOOTER_NOTE, wanted_text
    from mangalist.gui.volumes_target import VolumeTarget

    t = VolumeTarget(series_id=5, folder=FOLDER, title="S", titles=("S",), missing=("23", "24"), held=(),
                     upgrade=("23", "24"))
    assert wanted_text(t).startswith("Upgrade ") and "Missing" not in wanted_text(t)
    mixed = VolumeTarget(series_id=5, folder=FOLDER, title="S", titles=("S",), missing=("1", "7"), held=(),
                         upgrade=("1",))
    assert wanted_text(mixed).startswith("Missing ") and "upgrade" in wanted_text(mixed)
    series = [wanted(GROUP_VOLUMES, ("7",), "Vol. 7"), wanted()]
    tab, _ = make(qapp, series, service=FakeReplaced(mode="holding"))
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert tab.releases.note_label.text() == UPGRADE_FOOTER_NOTE != FOOTER_NOTE


def test_empty_now_on_a_held_batch_asks_first(qapp):
    service = FakeReplaced([batch(1, "held", mode="holding")], mode="holding")
    tab, _ = make(qapp, [], service=service)
    tab.replaced_bar.set_batches(service.open_batches())
    dialog = tab.replaced_dialog()
    assert dialog.btn_empty.isVisibleTo(dialog)
    dialog._confirm_empty = lambda parent, b: False
    assert not dialog.empty_selected() and service.calls == []           # Cancel: nothing
    dialog._confirm_empty = lambda parent, b: True
    assert dialog.empty_selected()
    wait_until(qapp, lambda: not dialog.busy())
    assert service.calls == [("empty_now", 1)] and threading.get_ident() not in service.threads[-1:]
    dialog.reject()


def test_the_release_panel_says_upgrade_for_an_upgrade(qapp):
    from mangalist.gui.releases_panel import FOOTER_NOTE, UPGRADE_FOOTER_NOTE, wanted_text
    from mangalist.gui.volumes_target import VolumeTarget

    t = VolumeTarget(series_id=5, folder=FOLDER, title="S", titles=("S",), missing=("23", "24"), held=(),
                     upgrade=("23", "24"))
    assert wanted_text(t).startswith("Upgrade ") and "Missing" not in wanted_text(t)
    mixed = VolumeTarget(series_id=5, folder=FOLDER, title="S", titles=("S",), missing=("1", "7"), held=(),
                         upgrade=("1",))
    assert wanted_text(mixed).startswith("Missing ") and "upgrade" in wanted_text(mixed)
    series = [wanted(GROUP_VOLUMES, ("7",), "Vol. 7"), wanted()]
    tab, _ = make(qapp, series, service=FakeReplaced(mode="holding"))
    tab.focus(FOLDER)
    settle(qapp, tab)
    assert tab.releases.note_label.text() == UPGRADE_FOOTER_NOTE != FOOTER_NOTE
