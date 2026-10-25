"""The releases panel's "Only the missing volumes" box (offscreen Qt, fake backend): ticked by default, the counts and
sizes, untick = whole pack, the Settings default, the fallbacks, and the file list read off the UI thread."""

from __future__ import annotations

import dataclasses
import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.contracts import DownloadStatus  # noqa: E402
from mangalist.downloads.partial import PackOutcome, PackSelection, choose_files  # noqa: E402
from mangalist.gui import releases_panel  # noqa: E402
from mangalist.gui.releases_panel import ReleasesPanel  # noqa: E402

from .conftest import BackendError, FakeBackend, candidate, qapp, record, target, wait_until  # noqa: E402,F401

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


# --- what a numberless pack holds (owner, 2026-10-09: "read the contents of the torrent files") --------------------

def numberless(info_hash, title="Example Series (2019-2021) (Digital)"):
    return candidate(title, info_hash=info_hash, vol_from=None, vol_to=None, covers_missing=(), is_pack=True,
                     reasons=("pack without volume numbers: contents unknown", "Digital"))


def test_a_numberless_pack_shows_what_its_file_list_holds(qapp):
    backend = PartialBackend(results=[numberless("c" * 40)])
    panel = open_panel(qapp, backend)                                  # missing 3, 4, 5, 9; held 1, 2
    cells = [panel.table.item(0, c).text() for c in (releases_panel.COL_FILLS, releases_panel.COL_HELD)]
    assert cells == ["v03-v05", "v01-v02"]                             # the pack holds v01-v08: no v09
    sub = panel.table.item(0, releases_panel.COL_RELEASE).data(releases_panel.ROLE_SUB)
    assert sub == "file list: v01-v08, 8 files, Digital, trusted uploader"
    assert panel.table.item(0, releases_panel.COL_DATE).text() == "2026-09-30"


def test_the_other_numberless_packs_are_read_one_after_another_in_the_background(qapp):
    titled = candidate()                                               # selected first: its own read
    packs = [numberless(h * 40) for h in "cdefghi"]                    # 7 numberless, only MAX_BACKGROUND_PACKS read
    backend = PartialBackend(results=[titled] + packs)
    panel = open_panel(qapp, backend)
    limit = releases_panel.MAX_BACKGROUND_PACKS
    wait_until(qapp, lambda: len(backend.inspected) == 1 + limit)
    assert backend.inspected[0][0] == "a" * 40                         # the selected release first
    assert [h for h, _w in backend.inspected[1:]] == [p.info_hash for p in packs[:limit]]
    assert all(w == ("3", "4", "5", "9") for _h, w in backend.inspected[1:])
    fills = [panel.table.item(r, releases_panel.COL_FILLS).text() for r in range(1, len(packs) + 1)]
    assert fills == ["v03-v05"] * limit + ["?"] * (len(packs) - limit)
    assert threading.get_ident() not in backend.threads


def test_a_file_list_without_volume_numbers_leaves_the_question_marks(qapp):
    odd = choose_files([(f"{ROOT}/{n}.cbz", MB) for n in ("Extras", "Bonus art", "Afterword")], ("3", "4", "5", "9"),
                       "volumes")
    backend = PartialBackend(results=[numberless("c" * 40)], packs={"c" * 40: odd})
    panel = open_panel(qapp, backend)
    assert panel.table.item(0, releases_panel.COL_FILLS).text() == "-"
    sub = panel.table.item(0, releases_panel.COL_RELEASE).data(releases_panel.ROLE_SUB)
    assert sub.startswith("file list: no volume numbers in the 3 files")


# --- already in qBittorrent (owner, 2026-10-09) ----------------------------------------------------------------

def test_a_release_already_in_qbittorrent_is_named_and_cannot_be_sent_again(qapp):
    seeding = record(4, series_id=7, status=DownloadStatus.FILED, wanted=("1", "2"), title="Example Series v01-02")
    seeding = dataclasses.replace(seeding, info_hash="A" * 40)                   # the same torrent, any case
    panel = ReleasesPanel(PartialBackend(), confirm=lambda p, t: True, downloads_for=lambda sid: [seeding])
    panel.open_target(target())
    wait_until(qapp, lambda: panel._search_state == "done" and panel._placement is not None)
    assert panel.downloads_label.isVisibleTo(panel)
    assert panel.downloads_label.text() == "Already in qBittorrent - Seeding v01-v02: Example Series v01-02"
    assert panel.send_blocker() == ("This release is already in qBittorrent (Seeding v01-v02); "
                                    "it is not added twice.")
    assert not panel.btn_send.isEnabled()


def test_a_torrent_no_longer_in_qbittorrent_is_not_mentioned(qapp):
    gone = record(4, series_id=7, status=DownloadStatus.REMOVED, wanted=("1",))
    other = record(5, series_id=8, status=DownloadStatus.SENT)                  # another series
    panel = ReleasesPanel(PartialBackend(), confirm=lambda p, t: True, downloads_for=lambda sid: [gone, other])
    panel.open_target(target())
    wait_until(qapp, lambda: panel._search_state == "done" and panel._placement is not None)
    assert not panel.downloads_label.isVisibleTo(panel) and panel.downloads_label.text() == ""


# --- queued sends (owner, 2026-10-15: "It should be queued.") -------------------------------------------------------

OTHER = dict(series_id=8, folder="/lib/Other Series", title="Other Series", titles=("Other Series",),
             missing=("1", "2"), held=())


class GatedBackend(PartialBackend):
    """A backend whose ``send`` of a release waits until its gate is set (so a test can send while another runs); it
    remembers the order the sends started in and can fail one."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.gates = {}                                 # info hash -> threading.Event its send waits for
        self.started = []
        self.fail = {}                                  # info hash -> the error its send raises

    def send(self, series_id, cand, wanted_volumes, target_dir, **kw):
        self.started.append((series_id, cand.info_hash))
        gate = self.gates.get(cand.info_hash)
        if gate is not None:
            gate.wait(10)
        if cand.info_hash in self.fail:
            raise BackendError(self.fail[cand.info_hash])
        return super().send(series_id, cand, wanted_volumes, target_dir, **kw)

RELEASE_A = candidate(info_hash="a" * 40)
RELEASE_A2 = candidate("Example Series v09 (Digital) (Group)", info_hash="d" * 40, vol_from="9", vol_to="9",
                       covers_missing=("9",))
RELEASE_B = candidate("Other Series v01-02 (Digital) (Group)", info_hash="b" * 40, vol_from="1", vol_to="2",
                      covers_missing=("1", "2"))


def show(qapp, panel, which, results):
    """Switch the (managed) panel to a series, as the Download tab does when its search is done."""
    tgt = target(**which)
    placement = releases_panel.Placement(tgt.folder, tgt.folder, "volumes live in the series folder")
    panel.show_outcome(releases_panel.SearchOutcome(tgt, placement, None, list(results)))
    wait_until(qapp, lambda: panel.pack_state() not in ("waiting", "reading"))


def two_series(qapp):
    """Series A's release is being sent (held at its gate); the panel then shows series B."""
    backend = GatedBackend()
    backend.gates = {"a" * 40: threading.Event(), "d" * 40: threading.Event(), "b" * 40: threading.Event()}
    panel = ReleasesPanel(backend, confirm=lambda p, t: True, managed=True)
    show(qapp, panel, {}, [RELEASE_A, RELEASE_A2])
    assert panel.send_selected()
    wait_until(qapp, lambda: backend.started == [(7, "a" * 40)])
    show(qapp, panel, OTHER, [RELEASE_B])
    return backend, panel


def test_a_send_while_another_runs_is_queued_and_sent_after_it_as_clicked(qapp):
    backend, panel = two_series(qapp)
    emitted = []
    panel.sent.connect(emitted.append)
    assert panel.send_blocker() is None and panel.btn_send.isEnabled()          # no waiting for series A's send
    assert "after the send before it" in panel.btn_send.toolTip()
    assert panel.send_selected()                                                # queued, not refused
    assert [j.candidate.info_hash for j in panel.queued_sends()] == ["b" * 40]
    assert panel.status_label.text() == ("Queued to send (1st): Other Series v01-02 (Digital) (Group) - MangaList "
                                         "sends it after the one before it.")
    qapp.processEvents()
    assert backend.started == [(7, "a" * 40)]                                   # one send at a time
    backend.gates["b" * 40].set()
    backend.gates["a" * 40].set()
    wait_until(qapp, lambda: len(panel.sent_records) == 2)
    assert backend.started == [(7, "a" * 40), (8, "b" * 40)]
    assert backend.sent[1] == (8, "b" * 40, ("1", "2"), "/lib/Other Series")    # what was chosen at the click
    assert backend.send_kwargs[1] == {"only_missing": True}
    assert [r.series_id for r in emitted] == [7, 8] and panel.queued_sends() == []
    assert panel.status_label.text().startswith("Sent to qBittorrent: Other Series v01-02 (Digital) (Group).")


def test_each_series_shows_its_own_send_status(qapp):
    backend, panel = two_series(qapp)
    assert panel.status_label.text() == ""                                     # not series A's "Sending..."
    panel.send_selected()
    show(qapp, panel, {}, [RELEASE_A, RELEASE_A2])
    assert panel.status_label.text() == ("Sending to qBittorrent: Example Series v03-05 (Digital) (Group)... "
                                         "(1 more queued)")
    show(qapp, panel, OTHER, [RELEASE_B])
    assert panel.status_label.text().startswith("Queued to send (1st): Other Series")
    backend.gates["a" * 40].set()
    wait_until(qapp, lambda: len(panel.sent_records) == 1)
    assert panel.status_label.text().startswith("Sending to qBittorrent: Other Series v01-02")
    show(qapp, panel, {}, [RELEASE_A, RELEASE_A2])
    assert panel.status_label.text().startswith("Sent to qBittorrent: Example Series v03-05 (Digital) (Group).")
    assert panel.status_label.property("tone") == "ok"
    backend.gates["b" * 40].set()
    wait_until(qapp, lambda: len(panel.sent_records) == 2)


def test_a_queued_release_is_not_queued_twice(qapp):
    backend, panel = two_series(qapp)
    show(qapp, panel, {}, [RELEASE_A, RELEASE_A2])
    assert panel.send_blocker() == "This release is being sent to qBittorrent now."
    panel.table.selectRow(1)
    wait_until(qapp, lambda: panel.pack_state() not in ("waiting", "reading"))
    assert panel.send_selected()
    assert panel.send_blocker() == "This release is already queued to send; it is not queued twice."
    assert not panel.btn_send.isEnabled() and not panel.send_selected()
    assert len(panel.queued_sends()) == 1
    assert "Queued to send (1st): Example Series v09 (Digital) (Group)" in panel.status_label.text()
    for gate in backend.gates.values():
        gate.set()
    wait_until(qapp, lambda: len(panel.sent_records) == 2)
    assert panel.send_blocker() == "This release was already sent."             # as before


def test_a_failed_send_does_not_stop_the_queue(qapp):
    backend, panel = two_series(qapp)
    backend.fail["a" * 40] = "qBittorrent refused the login"
    panel.send_selected()
    backend.gates["a" * 40].set()
    backend.gates["b" * 40].set()
    wait_until(qapp, lambda: len(panel.sent_records) == 1)
    assert backend.started == [(7, "a" * 40), (8, "b" * 40)] and panel.sent_records[0].series_id == 8
    show(qapp, panel, {}, [RELEASE_A, RELEASE_A2])
    assert panel.status_label.text() == ("Could not send Example Series v03-05 (Digital) (Group): "
                                         "qBittorrent refused the login")
    assert panel.status_label.property("tone") == "bad"
    assert panel.send_blocker() is None                                          # it can be sent again


def test_stop_drops_the_queued_sends_and_says_so_in_the_log(qapp, caplog):
    backend, panel = two_series(qapp)
    panel.send_selected()
    with caplog.at_level("INFO", logger="mangalist.gui.releases_panel"):
        panel.stop()
    assert panel.queued_sends() == [] and panel._sending is None
    assert "1 queued send(s) dropped: Other Series v01-02 (Digital) (Group)" in caplog.text
    backend.gates["a" * 40].set()
    backend.gates["b" * 40].set()
    for _ in range(20):
        qapp.processEvents()
    assert backend.started == [(7, "a" * 40)] and panel.sent_records == []


def test_a_queued_send_can_be_cancelled_from_its_link(qapp):
    """Owner, 2026-10-15: a "Cancel" link in the status line takes a queued send off the queue."""
    backend, panel = two_series(qapp)
    assert panel.send_selected()                                                # series B's release: queued
    assert panel.cancel_links() == [f"cancel:{panel.queued_sends()[0].ident}"]
    assert "Cancel</a> the queued send (1st): Other Series v01-02 (Digital) (Group)" in panel.cancel_label.text()
    assert not panel.cancel_label.isHidden()
    show(qapp, panel, {}, [RELEASE_A, RELEASE_A2])                              # series A: its send runs - no link
    assert panel.cancel_links() == [] and panel.cancel_label.isHidden()
    show(qapp, panel, OTHER, [RELEASE_B])
    panel.cancel_label.linkActivated.emit(panel.cancel_links()[0])               # the owner clicks Cancel
    assert panel.queued_sends() == [] and panel.cancel_links() == []
    assert panel.status_label.text() == ("Cancelled the queued send of Other Series v01-02 (Digital) (Group): nothing "
                                         "was sent.")
    assert panel.send_blocker() is None                                         # it can be sent again
    assert not panel.cancel_queued(12345)
    backend.gates["a" * 40].set()
    wait_until(qapp, lambda: len(panel.sent_records) == 1)
    qapp.processEvents()
    assert backend.started == [(7, "a" * 40)]                                   # the cancelled one never went


# --- releases that are not comic volumes are refused (owner, 2026-10-24) ------------------------------------------

def test_a_release_of_epub_files_is_refused_with_its_reason(qapp):
    epubs = choose_files([(f"Made Up Novel v{n:02d} [Pub] [Grp].epub", 7 * MB) for n in (1, 2, 3)],
                         ["3", "4", "5"], "volumes")
    backend = PartialBackend(packs={"a" * 40: epubs})
    panel = open_panel(qapp, backend)
    assert panel.pack_state() == "refused" and panel.partial_row.isVisibleTo(panel)
    assert panel.partial_note.text().startswith("Not a volume release: its files are e-books or audio")
    assert panel.send_blocker().startswith("Not a volume release") and not panel.btn_send.isEnabled()
    panel.partial_check.setChecked(False)                   # unticking does not make it sendable
    assert panel.send_blocker() is not None


def test_a_bare_number_chapter_pack_is_refused(qapp):
    chapters = choose_files([(f"Made Up Webtoon {n:03d} (Digital).cbz", 10 * MB) for n in range(1, 159)],
                            ["3", "4", "5"], None)
    backend = PartialBackend(packs={"a" * 40: chapters})
    panel = open_panel(qapp, backend)
    assert panel.pack_state() == "refused" and "its files are chapters, not volumes" in panel.send_blocker()
