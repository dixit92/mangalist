"""The releases panel's "Only the missing volumes" box (offscreen Qt, fake backend): ticked by default, the counts and
sizes, untick = whole pack, the Settings default, the fallbacks, and the file list read off the UI thread."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.contracts import DownloadStatus  # noqa: E402
from mangalist.downloads.partial import PackOutcome, PackSelection, choose_files  # noqa: E402
from mangalist.gui import releases_panel  # noqa: E402
from mangalist.gui.releases_panel import ReleasesPanel  # noqa: E402

from .conftest import FakeBackend, candidate, qapp, record, target, wait_until  # noqa: E402,F401

MB = 1024 * 1024
ROOT = "Example Series v01-08 (Digital)"
NAMES = [f"Example Series v{n:02d} (Digital).cbz" for n in range(1, 9)]


def pack(wanted, names=NAMES, size=10 * MB, extra=()):
    return choose_files([(f"{ROOT}/{n}", size) for n in names] + list(extra), wanted, "volumes")


class PartialBackend(FakeBackend):
    """FakeBackend that can read a pack's file list (and remembers what it was asked)."""

    def __init__(self, *, packs=None, default=True, **kw):
        super().__init__(**kw)
        self.packs = packs or {}                        # info hash -> PackSelection (or an Exception to raise)
        self.default = default
        self.inspected = []
        self.send_kwargs = []
        self.outcomes = {}
        self.inspect_gate = None

    def inspect_pack(self, cand, wanted_volumes):
        self.threads.append(threading.get_ident())
        self.inspected.append((cand.info_hash, tuple(wanted_volumes)))
        if self.inspect_gate is not None:
            self.inspect_gate.wait(10)
        answer = self.packs.get(cand.info_hash, pack(wanted_volumes))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def partial_default(self):
        return self.default

    def send(self, series_id, cand, wanted_volumes, target_dir, **kw):
        self.send_kwargs.append(kw)
        return super().send(series_id, cand, wanted_volumes, target_dir)

    def take_pack_outcome(self, info_hash):
        return self.outcomes.pop(info_hash, None)


@pytest.fixture(autouse=True)
def _fast_and_closed(monkeypatch):
    monkeypatch.setattr(releases_panel, "PACK_DELAY_MS", 0)
    made = []
    original = ReleasesPanel.__init__

    def tracking(self, *a, **kw):
        original(self, *a, **kw)
        made.append(self)

    monkeypatch.setattr(ReleasesPanel, "__init__", tracking)
    yield
    for panel in made:
        panel.stop()
        panel.deleteLater()


def open_panel(qapp, backend, confirm=None, results_ready=True, **kw):
    panel = ReleasesPanel(backend, confirm=confirm or (lambda p, t: True))
    panel.open_target(target(**kw))
    wait_until(qapp, lambda: panel._search_state == "done" and panel._placement is not None)
    if results_ready:
        wait_until(qapp, lambda: panel.pack_state() not in ("waiting", "reading"))
    return panel


def sent(qapp, panel):
    before = len(panel.sent_records)
    assert panel.send_selected()
    wait_until(qapp, lambda: len(panel.sent_records) > before or "Could not send" in panel.status_label.text())


# --- the default view --------------------------------------------------------------------------------------------

def test_the_box_is_ticked_by_default_and_shows_counts_and_sizes(qapp):
    backend = PartialBackend()
    panel = open_panel(qapp, backend)
    assert panel.pack_state() == "partial" and panel.partial_row.isVisibleTo(panel)
    assert panel.partial_check.isChecked() and panel.partial_check.isEnabled()
    assert panel.partial_check.text() == "Only the missing volumes (3 of 8 files, 30 MB of 80 MB)"
    assert "seeds only what it downloaded" in panel.partial_note.text()
    assert "seeds only what it downloaded" in panel.partial_check.toolTip()
    assert "Example Series v03 (Digital).cbz" in panel.partial_note.toolTip()
    assert backend.inspected == [("a" * 40, ("3", "4", "5"))]


def test_the_file_list_is_read_off_the_ui_thread(qapp):
    backend = PartialBackend()
    open_panel(qapp, backend)
    assert threading.get_ident() not in backend.threads


def test_send_waits_for_the_file_list_while_the_box_is_ticked(qapp):
    backend = PartialBackend()
    backend.inspect_gate = threading.Event()
    panel = open_panel(qapp, backend, results_ready=False)
    wait_until(qapp, lambda: panel.pack_state() == "reading")
    assert panel.partial_note.text() == "Reading the release's file list..."
    assert panel.partial_check.isEnabled() and panel.partial_check.isChecked()
    assert panel.send_blocker().startswith("Reading the release's file list")
    assert not panel.btn_send.isEnabled() and not panel.send_selected()
    backend.inspect_gate.set()
    wait_until(qapp, lambda: panel.pack_state() == "partial")
    assert panel.send_blocker() is None and panel.btn_send.isEnabled()


def test_unticking_while_it_reads_sends_the_whole_pack_at_once(qapp):
    backend = PartialBackend()
    backend.inspect_gate = threading.Event()
    panel = open_panel(qapp, backend, results_ready=False)
    wait_until(qapp, lambda: panel.pack_state() == "reading")
    panel.partial_check.setChecked(False)           # the owner unticks it
    assert panel.send_blocker() is None and panel.btn_send.isEnabled()
    asked = []
    panel._confirm = lambda p, t: asked.append(t) or True
    sent(qapp, panel)
    assert "Download: the whole pack\n" in asked[0] and "could not be read" not in asked[0]
    assert backend.send_kwargs == [{}]              # the whole pack, plain call
    backend.inspect_gate.set()
    wait_until(qapp, lambda: panel.pack_state() == "partial")
    assert not panel.partial_check.isChecked()      # the late answer does not tick it again


# --- ticked / unticked ------------------------------------------------------------------------------------------

def test_ticked_sends_only_the_missing_volumes_and_says_so_in_the_confirmation(qapp):
    backend = PartialBackend()
    asked = []
    panel = open_panel(qapp, backend, confirm=lambda p, t: asked.append(t) or True)
    sent(qapp, panel)
    assert backend.send_kwargs == [{"only_missing": True}]
    assert "Download: only the missing volumes (3 of 8 files, 30 MB of 80 MB)" in asked[0]
    assert "Volumes: v03-v05" in asked[0] and "Target folder: /lib/Example Series" in asked[0]


def test_unticked_sends_the_whole_pack(qapp):
    backend = PartialBackend()
    asked = []
    panel = open_panel(qapp, backend, confirm=lambda p, t: asked.append(t) or True)
    panel.partial_check.setChecked(False)
    assert panel.partial_note.text() == "The whole pack will be downloaded (8 files, 80 MB)."
    assert panel.partial_check.text() == "Only the missing volumes (3 of 8 files, 30 MB of 80 MB)"    # the choice stays shown
    assert not panel.partial_requested()
    sent(qapp, panel)
    assert backend.send_kwargs == [{}]                                      # no only_missing: the plain call
    assert "Download: the whole pack (8 files, 80 MB)" in asked[0]


def test_the_settings_default_decides_where_the_box_starts(qapp):
    backend = PartialBackend(default=False)
    panel = open_panel(qapp, backend)
    assert panel.pack_state() == "partial" and not panel.partial_check.isChecked()
    assert panel.partial_check.isEnabled() and not panel.partial_requested()
    sent(qapp, panel)
    assert backend.send_kwargs == [{}]
    panel.partial_check.setChecked(True)
    assert panel.partial_requested()


def test_every_release_starts_from_the_default_again(qapp):
    second = candidate("Example Series v03 (Print)", info_hash="b" * 40, vol_from="3", vol_to="3",
                       covers_missing=("3",))
    backend = PartialBackend(results=[candidate(), second])
    panel = open_panel(qapp, backend)
    panel.partial_check.setChecked(False)
    panel.table.selectRow(1)
    wait_until(qapp, lambda: panel.pack_state() != "waiting" and panel.pack_state() != "reading")
    assert panel._partial_on is True                                        # "untick per send"
    panel.table.selectRow(0)
    assert panel.partial_check.isChecked() and panel.pack_state() == "partial"


def test_a_release_looked_at_before_is_not_read_again(qapp):
    second = candidate("Example Series v03 (Print)", info_hash="b" * 40, vol_from="3", vol_to="3",
                       covers_missing=("3",))
    backend = PartialBackend(results=[candidate(), second])
    panel = open_panel(qapp, backend)
    panel.table.selectRow(1)
    wait_until(qapp, lambda: len(backend.inspected) == 2 and panel.pack_state() not in ("waiting", "reading"))
    panel.table.selectRow(0)
    qapp.processEvents()
    assert panel.pack_state() == "partial" and len(backend.inspected) == 2


def test_rapid_selection_reads_only_the_release_it_settles_on(qapp, monkeypatch):
    monkeypatch.setattr(releases_panel, "PACK_DELAY_MS", 60)
    extra = [candidate(f"Example Series v0{i} (Print)", info_hash=f"{i:x}" * 40, vol_from="3", vol_to="3",
                       covers_missing=("3",)) for i in range(1, 4)]
    backend = PartialBackend(results=[candidate()] + extra)
    panel = ReleasesPanel(backend, confirm=lambda p, t: True)
    panel.open_target(target())
    wait_until(qapp, lambda: panel._search_state == "done")
    for row in (1, 2, 3, 2):
        panel.table.selectRow(row)
    wait_until(qapp, lambda: panel.pack_state() not in ("waiting", "reading"))
    assert backend.inspected == [("2" * 40, ("3",))]                        # the first row's timer was cancelled too


def test_the_outcome_of_the_send_is_told(qapp):
    backend = PartialBackend()
    panel = open_panel(qapp, backend)
    backend.outcomes["a" * 40] = PackOutcome(True, pack(("3", "4", "5")))
    sent(qapp, panel)
    assert panel.status_label.text().startswith("Sent to qBittorrent:")
    assert "Only the missing volumes are downloaded (3 of 8 files, 30 MB of 80 MB)." in panel.status_label.text()


def test_a_whole_pack_outcome_gives_its_reason(qapp):
    backend = PartialBackend()
    panel = open_panel(qapp, backend)
    backend.outcomes["a" * 40] = PackOutcome(False, PackSelection(), "it was in qBittorrent already, so it was left as it is")
    sent(qapp, panel)
    assert "The whole pack is downloaded (it was in qBittorrent already" in panel.status_label.text()


# --- the fallbacks --------------------------------------------------------------------------------------------

def test_an_unreadable_file_list_says_so_and_sends_the_whole_pack(qapp):
    problem = PackSelection(wanted=("3",), problem="nyaa did not give the torrent file (nyaa.si did not answer)")
    backend = PartialBackend(packs={"a" * 40: problem})
    asked = []
    panel = open_panel(qapp, backend, confirm=lambda p, t: asked.append(t) or True)
    assert panel.pack_state() == "failed" and panel.partial_row.isVisibleTo(panel)
    assert not panel.partial_check.isChecked() and not panel.partial_check.isEnabled()
    assert "could not be read (nyaa did not give the torrent file" in panel.partial_note.text()
    assert "The whole pack will be downloaded" in panel.partial_note.text()
    assert panel.partial_note.property("tone") == "warn"
    assert panel.send_blocker() is None
    sent(qapp, panel)
    assert backend.send_kwargs == [{}] and "the whole pack (its file list could not be read)" in asked[0]


def test_a_backend_error_while_reading_is_the_same_fallback(qapp):
    from mangalist.gui.downloads_backend import BackendError

    backend = PartialBackend(packs={"a" * 40: BackendError("the disk is on fire")})
    panel = open_panel(qapp, backend)
    assert panel.pack_state() == "failed" and "the disk is on fire" in panel.partial_note.text()
    sent(qapp, panel)
    assert backend.send_kwargs == [{}]


def test_selecting_a_release_again_retries_an_unreadable_list(qapp):
    problem = PackSelection(wanted=("3",), problem="nyaa did not answer")
    second = candidate("Example Series v03 (Print)", info_hash="b" * 40, vol_from="3", vol_to="3", covers_missing=("3",))
    backend = PartialBackend(results=[candidate(), second], packs={"a" * 40: problem})
    panel = open_panel(qapp, backend)
    assert panel.pack_state() == "failed"
    backend.packs["a" * 40] = pack(("3", "4", "5"))
    panel.table.selectRow(1)
    wait_until(qapp, lambda: panel.pack_state() not in ("waiting", "reading"))
    panel.table.selectRow(0)
    wait_until(qapp, lambda: panel.pack_state() == "partial")
    assert panel.partial_check.isChecked()


def test_no_file_naming_a_missing_volume_keeps_the_whole_pack_with_the_reason(qapp):
    whole = pack(("9",))
    backend = PartialBackend(packs={"a" * 40: whole})
    panel = open_panel(qapp, backend)
    assert panel.pack_state() == "whole" and panel.partial_row.isVisibleTo(panel)
    assert panel.partial_note.text() == "None of the files names a missing volume, so the whole pack is kept."
    assert not panel.partial_check.isEnabled() and not panel.partial_requested()
    sent(qapp, panel)
    assert backend.send_kwargs == [{}]


def test_files_whose_names_say_no_volume_are_counted_in_the_note(qapp):
    mixed = pack(("3", "4", "5"), extra=[(f"{ROOT}/The Great Book.cbz", 5 * MB)])
    backend = PartialBackend(packs={"a" * 40: mixed})
    panel = open_panel(qapp, backend)
    assert panel.pack_state() == "partial"
    assert "1 file kept because its name does not say a volume." in panel.partial_note.text()
    assert panel.partial_check.text() == "Only the missing volumes (4 of 9 files, 35 MB of 85 MB)"


def test_a_wanted_volume_the_pack_lacks_is_named(qapp):
    backend = PartialBackend(packs={"a" * 40: pack(("3", "4", "5", "11"))})
    panel = open_panel(qapp, backend)
    assert "Not in this pack: v11." in panel.partial_note.text()


def test_a_pack_with_nothing_to_leave_out_shows_no_box(qapp):
    one = choose_files([("Example Series v03 (Digital).cbz", 5 * MB)], ["3"], "volumes")
    backend = PartialBackend(packs={"a" * 40: one})
    asked = []
    panel = open_panel(qapp, backend, confirm=lambda p, t: asked.append(t) or True)
    assert panel.pack_state() == "whole" and not panel.partial_row.isVisibleTo(panel)
    sent(qapp, panel)
    assert backend.send_kwargs == [{}] and "Download: the whole pack (1 file, 5 MB)" in asked[0]


def test_a_backend_that_cannot_read_file_lists_shows_no_box_and_changes_nothing(qapp):
    backend = FakeBackend()
    asked = []
    panel = open_panel(qapp, backend, confirm=lambda p, t: asked.append(t) or True)
    assert panel.pack_state() == "none" and not panel.partial_row.isVisibleTo(panel)
    assert not panel.partial_requested() and "Download:" not in "".join(asked)
    sent(qapp, panel)
    assert len(backend.sent) == 1 and "The whole pack" not in panel.status_label.text()


# --- stale answers and tidying -------------------------------------------------------------------------------

def test_an_answer_for_a_series_the_owner_left_is_dropped(qapp):
    backend = PartialBackend()
    backend.inspect_gate = threading.Event()
    panel = open_panel(qapp, backend, results_ready=False)
    wait_until(qapp, lambda: panel.pack_state() == "reading")
    panel.show_message("", "Select a series to see its releases.")           # moved away
    backend.inspect_gate.set()
    qapp.processEvents()
    assert panel.pack_state() == "none" and not panel.partial_row.isVisibleTo(panel)
    panel.stop()


def test_a_late_answer_for_another_target_is_ignored(qapp):
    backend = PartialBackend()
    panel = open_panel(qapp, backend)
    panel._on_pack("a" * 40, 999, PackSelection(problem="stale"))             # series 999: not the open one
    assert panel.pack_state() == "partial"
    panel._on_pack_error("a" * 40, 999, "stale")
    assert panel.pack_state() == "partial"


def test_search_again_forgets_the_file_lists(qapp):
    backend = PartialBackend()
    panel = open_panel(qapp, backend)
    assert panel._packs
    from mangalist.gui.releases_panel import SearchOutcome

    panel.show_outcome(SearchOutcome(target=target(), placement=backend.placement_answer, candidates=[candidate()]))
    wait_until(qapp, lambda: panel.pack_state() == "partial" and len(backend.inspected) == 2)
