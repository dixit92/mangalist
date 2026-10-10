"""The download budget in the GUI (offscreen Qt, fake backends; made-up names): the releases panel's confirmation, the
over-the-cap question and the bigger-than-the-cap alert, a queued release not sent twice, the In progress list's queued
rows, budget line and queue overrides, and the cap in Settings > Download sources."""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel  # noqa: E402

from mangalist import store  # noqa: E402
from mangalist.downloads import budget as B  # noqa: E402
from mangalist.downloads.budget import GB  # noqa: E402
from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.downloads.options import KEY_BUDGET_GB, get_budget_gb  # noqa: E402
from mangalist.gui import releases_panel  # noqa: E402
from mangalist.gui.downloads_list import ACT_DEQUEUE, ACT_SEND_NOW, ACT_TO_FRONT, DownloadsList  # noqa: E402
from mangalist.gui.releases_panel import ReleasesPanel  # noqa: E402
from mangalist.gui.settings_sections import SourcesPage  # noqa: E402

from .conftest import FakeBackend, candidate, qapp, record, target, wait_until  # noqa: E402,F401
from .test_partial_panel import PartialBackend, pack  # noqa: E402

MB = 1024 * 1024


def sized(rec, size, **kw):
    return dataclasses.replace(rec, size_bytes=size, size_source="release", **kw)


class BudgetBackend(PartialBackend):
    """A fake backend that keeps a download budget like the real one: ``send`` queues what does not fit unless told
    ``over_cap="send"``; the queue overrides change the fake records."""

    def __init__(self, *, cap_gb=50, **kw):
        super().__init__(**kw)
        self.cap_gb = cap_gb
        self.queue_calls = []

    def budget_status(self):
        return B.state_of(self.record_list, self.cap_gb)

    def send(self, series_id, cand, wanted_volumes, target_dir, **kw):
        self.send_kwargs.append(kw)
        size = kw.get("size_bytes") or cand.size_bytes
        state = self.budget_status()
        queue = kw.get("over_cap", "queue") == "queue" and state.verdict(size) != B.FITS
        rec = sized(record(id=len(self.record_list) + 1, series_id=series_id, wanted=wanted_volumes, title=cand.title,
                           status=S.QUEUED if queue else S.SENT), size)
        rec = dataclasses.replace(rec, info_hash=cand.info_hash, queue_position=len(state.queued) + 1 if queue else 0)
        self.record_list.append(rec)
        self.sent.append((series_id, cand.info_hash, tuple(wanted_volumes), target_dir))
        return rec

    def _change(self, record_id, **changes):
        i = next(i for i, r in enumerate(self.record_list) if r.id == record_id)
        self.record_list[i] = dataclasses.replace(self.record_list[i], **changes)
        return self.record_list[i]

    def send_queued_now(self, record_id):
        self.queue_calls.append(("send", record_id))
        return self._change(record_id, status=S.SENT, queue_position=0)

    def move_to_front(self, record_id):
        self.queue_calls.append(("front", record_id))
        return self._change(record_id, queue_position=1)

    def remove_from_queue(self, record_id):
        self.queue_calls.append(("remove", record_id))
        return self._change(record_id, status=S.CANCELLED, queue_position=0)


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


def open_panel(qapp, backend, *, choice="queue", confirm=True, partial=False):
    asked, confirmed = [], []
    panel = ReleasesPanel(backend, confirm=lambda p, t: confirmed.append(t) or confirm,
                          budget_choice=lambda p, kind, text: asked.append((kind, text)) or choice,
                          downloads_for=lambda sid: [r for r in backend.record_list if r.series_id == sid])
    panel.open_target(target())
    wait_until(qapp, lambda: panel._search_state == "done" and panel._placement is not None)
    wait_until(qapp, lambda: panel.pack_state() not in ("waiting", "reading"))
    if not partial and panel.partial_check.isEnabled():
        panel.partial_check.setChecked(False)
    panel.asked, panel.confirmed = asked, confirmed
    return panel


def send(qapp, panel):
    before = len(panel.sent_records)
    assert panel.send_selected()
    wait_until(qapp, lambda: len(panel.sent_records) > before or "Could not send" in panel.status_label.text())


def big(size_gb):
    return dataclasses.replace(candidate(), size_bytes=int(size_gb * GB))


# --- the releases panel --------------------------------------------------------------------------------------------

def test_a_send_that_fits_is_confirmed_as_before_with_a_budget_line(qapp):
    backend = BudgetBackend(results=[big(2)], records=[sized(record(9, series_id=8, status=S.FILED), 10 * GB)])
    panel = open_panel(qapp, backend)
    send(qapp, panel)
    assert panel.asked == [] and "Download budget: using 10 GB of 50 GB; this adds 2 GB." in panel.confirmed[0]
    assert backend.send_kwargs == [{"size_bytes": 2 * GB, "over_cap": "queue"}]
    assert panel.status_label.text().startswith("Sent to qBittorrent: ")


def test_over_the_cap_asks_and_queueing_is_the_default(qapp):
    backend = BudgetBackend(results=[big(5)], records=[sized(record(9, series_id=8, status=S.FILED), 48 * GB)])
    panel = open_panel(qapp, backend, choice="queue")
    send(qapp, panel)
    (kind, text), = panel.asked
    assert kind == "over" and panel.confirmed == []                     # one question, not two
    assert text.startswith("This would go over the download budget: MangaList is using 48 GB of 50 GB, and this "
                           "adds 5 GB.")
    assert "Series: Example Series" in text and "Target folder: /lib/Example Series" in text
    assert backend.send_kwargs == [{"size_bytes": 5 * GB, "over_cap": "queue"}]
    assert panel.status_label.text().startswith("Queued: Example Series v03-05 (Digital) (Group) (1st in line).")
    assert "already queued (Queued v03-v05)" in panel.send_blocker()
    assert panel.downloads_label.text().startswith("Already queued or in qBittorrent - Queued v03-v05: ")


def test_over_the_cap_the_owner_can_send_now_or_cancel(qapp):
    backend = BudgetBackend(results=[big(5)], records=[sized(record(9, series_id=8, status=S.FILED), 48 * GB)])
    panel = open_panel(qapp, backend, choice="cancel")
    assert not panel.send_selected() and backend.send_kwargs == []
    panel.ask_budget = lambda p, kind, text: "send"
    send(qapp, panel)
    assert backend.send_kwargs == [{"size_bytes": 5 * GB, "over_cap": "send"}]
    assert backend.record_list[-1].status == S.SENT


def test_bigger_than_the_cap_alone_is_an_alert_that_names_size_and_cap(qapp):
    backend = BudgetBackend(results=[big(62)])
    panel = open_panel(qapp, backend, choice="queue")              # "queue" is not an answer here: cancelled
    assert not panel.send_selected() and backend.send_kwargs == []
    (kind, text), = panel.asked
    assert kind == "too big" and text.startswith("This release is 62 GB - bigger than the whole download budget of "
                                                 "50 GB on its own")
    panel.ask_budget = lambda p, kind, text: "send"
    send(qapp, panel)
    assert backend.send_kwargs == [{"size_bytes": 62 * GB, "over_cap": "send"}]


def test_others_waiting_in_the_queue_come_first(qapp):
    queued = sized(record(9, series_id=8, status=S.QUEUED), 30 * GB)
    backend = BudgetBackend(results=[big(1)], records=[dataclasses.replace(queued, queue_position=1)])
    panel = open_panel(qapp, backend)
    send(qapp, panel)
    assert panel.asked[0][1].startswith("1 download is already waiting in the queue, so this one would wait behind it")


def test_no_limit_asks_nothing_about_a_cap(qapp):
    backend = BudgetBackend(results=[big(900)], cap_gb=0)
    panel = open_panel(qapp, backend)
    send(qapp, panel)
    assert panel.asked == [] and "Download budget" not in panel.confirmed[0]


def test_a_partial_send_counts_its_selected_files(qapp):
    backend = BudgetBackend(results=[big(60)], records=[sized(record(9, series_id=8, status=S.FILED), 40 * GB)])
    panel = open_panel(qapp, backend, partial=True)
    assert panel.partial_requested() and panel.send_size(panel.selected_candidate()) == 30 * MB
    send(qapp, panel)
    assert panel.asked == [] and backend.send_kwargs == [{"size_bytes": 30 * MB, "over_cap": "queue",
                                                          "only_missing": True}]


def test_a_backend_without_a_budget_is_called_as_before(qapp):
    backend = FakeBackend()
    asked = []
    panel = ReleasesPanel(backend, confirm=lambda p, t: True, budget_choice=lambda *a: asked.append(a) or "send")
    panel.open_target(target())
    wait_until(qapp, lambda: panel._search_state == "done" and panel._placement is not None)
    send(qapp, panel)
    assert asked == [] and len(backend.sent) == 1


# --- the In progress list --------------------------------------------------------------------------------------------

def queued_backend():
    first = dataclasses.replace(sized(record(1, status=S.QUEUED, title="Release One"), 20 * GB), queue_position=1)
    second = dataclasses.replace(sized(record(2, status=S.QUEUED, title="Release Two"), 5 * GB), queue_position=2)
    seeding = sized(record(3, status=S.FILED, title="Release Three"), 45 * GB)
    return BudgetBackend(records=[first, second, seeding])


def test_queued_rows_show_their_place_and_the_budget_line(qapp):
    backend = queued_backend()
    lst = DownloadsList(backend)
    wait_until(qapp, lambda: lst.table.rowCount() == 3)
    pills = [lst.table.cellWidget(r, 2).findChild(QLabel) for r in range(3)]          # newest first
    assert [p.text() for p in pills] == ["Filed v03-v05 - seeding", "Queued - 2nd in line", "Queued - 1st in line"]
    assert pills[1].property("badge") == "muted"
    assert "Queued, 2nd in line" in pills[1].toolTip() and "5 GB (the release's size" in pills[1].toolTip()
    assert lst.budget_label.text() == "· Using 45 GB of 50 GB; 2 downloads queued"
    lst.stop()
    lst.deleteLater()


def menu_answer(texts, pick):
    def answer(menu, pos):
        acts = {a.text(): a for a in menu.actions()}
        texts.append({t: a.isEnabled() for t, a in acts.items()})
        return acts.get(pick)
    return answer


def test_the_queue_overrides_from_the_row_menu(qapp):
    backend = queued_backend()
    confirms = []
    lst = DownloadsList(backend, confirm_queue=lambda action, rec: confirms.append((action, rec.id)) or True)
    wait_until(qapp, lambda: lst.table.rowCount() == 3)
    menus = []
    row_of = {r.id: i for i, r in enumerate(lst.records)}
    pos = lambda rid: lst.table.visualItemRect(lst.table.item(row_of[rid], 0)).center()    # noqa: E731

    lst._exec_menu = menu_answer(menus, ACT_TO_FRONT)
    lst._on_context_menu(pos(2))
    assert menus[-1] == {ACT_SEND_NOW: True, ACT_TO_FRONT: True, ACT_DEQUEUE: True}
    wait_until(qapp, lambda: lst._call is None and lst.btn_refresh.isEnabled())
    assert backend.queue_calls == [("front", 2)] and confirms == []                       # moving asks nothing
    assert "Moved to the front of the queue: Release Two." in lst.status_label.text()

    lst._exec_menu = menu_answer(menus, ACT_SEND_NOW)
    row_of = {r.id: i for i, r in enumerate(lst.records)}
    lst._on_context_menu(pos(1))
    wait_until(qapp, lambda: lst._call is None and lst.btn_refresh.isEnabled())
    assert backend.queue_calls[-1] == ("send", 1) and confirms == [(ACT_SEND_NOW, 1)]
    assert "Sent to qBittorrent past the download budget: Release One." in lst.status_label.text()

    lst._exec_menu = menu_answer(menus, ACT_DEQUEUE)
    row_of = {r.id: i for i, r in enumerate(lst.records)}
    lst._on_context_menu(pos(2))
    wait_until(qapp, lambda: lst._call is None and lst.btn_refresh.isEnabled())
    assert backend.queue_calls[-1] == ("remove", 2) and confirms[-1] == (ACT_DEQUEUE, 2)
    lst.stop()
    lst.deleteLater()


def test_the_first_in_line_cannot_move_to_the_front_and_a_no_changes_nothing(qapp):
    backend = queued_backend()
    lst = DownloadsList(backend, confirm_queue=lambda action, rec: False)
    wait_until(qapp, lambda: lst.table.rowCount() == 3)
    menus = []
    lst._exec_menu = menu_answer(menus, None)
    first_row = next(i for i, r in enumerate(lst.records) if r.id == 1)
    lst._on_context_menu(lst.table.visualItemRect(lst.table.item(first_row, 0)).center())
    assert menus == [{ACT_SEND_NOW: True, ACT_TO_FRONT: False, ACT_DEQUEUE: True}]
    assert not lst.queue_action(ACT_SEND_NOW, lst.records[first_row])
    assert not lst.queue_action(ACT_DEQUEUE, lst.records[first_row]) and backend.queue_calls == []
    filed_row = next(i for i, r in enumerate(lst.records) if r.id == 3)
    lst._on_context_menu(lst.table.visualItemRect(lst.table.item(filed_row, 0)).center())
    assert menus[-1] == {"Remove now…": True}                    # a filed download keeps its own menu
    lst.stop()
    lst.deleteLater()


def test_a_send_now_that_fails_says_why(qapp):
    backend = queued_backend()
    backend.send_queued_now = lambda rid: backend._change(rid, status=S.FAILED, error="not sent from the queue: gone")
    lst = DownloadsList(backend, confirm_queue=lambda action, rec: True)
    wait_until(qapp, lambda: lst.table.rowCount() == 3)
    rec = next(r for r in lst.records if r.id == 1)
    assert lst.queue_action(ACT_SEND_NOW, rec)
    wait_until(qapp, lambda: lst._call is None and lst.btn_refresh.isEnabled())
    assert lst.status_label.text() == "Could not send it: not sent from the queue: gone"
    lst.stop()
    lst.deleteLater()


# --- Settings > Download sources -------------------------------------------------------------------------------------

@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


def test_the_cap_in_settings(qapp, db):
    backend = queued_backend()
    backend.cap_gb = 50
    page = SourcesPage(db, backend)
    try:
        assert page.budget_spin.value() == 50 and db.get_setting(KEY_BUDGET_GB, None) is None    # loading writes nothing
        assert page.budget_usage.text() == "Using 45 GB of 50 GB; 2 queued"
        page.budget_spin.setValue(120)
        assert get_budget_gb(db) == 120
        page.budget_spin.setValue(0)
        assert get_budget_gb(db) == 0 and page.budget_spin.text() == "no limit"
        again = SourcesPage(db, None)
        assert again.budget_spin.value() == 0 and not again.budget_spin.isEnabled()
        assert not again.budget_usage.text()
        again.deleteLater()
    finally:
        page.deleteLater()
