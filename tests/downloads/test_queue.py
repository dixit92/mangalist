"""The download queue (mangalist.downloads.queueing) over the real ledger and a FAKE qBittorrent: a send over the cap is
queued with everything needed to send it later, the hand-over sends in order as room frees up, the owner's overrides,
and the edge cases (cap lowered, sizes unknown, a release gone, no limit, order across restarts). Made-up names."""

from __future__ import annotations

import logging

import pytest

from mangalist import store
from mangalist.downloads import queueing as Q
from mangalist.downloads.budget import GB, SIZE_CLIENT, SIZE_RELEASE, SIZE_SELECTED
from mangalist.downloads.contracts import DownloadStatus as S, Placement
from mangalist.downloads.options import set_budget_gb
from mangalist.downloads.service import SendRefused
from mangalist.store.downloads import DownloadLedger

from .fakes import candidate, data

H1, H2, H3, H4 = ("1" * 40), ("2" * 40), ("3" * 40), ("4" * 40)


def rel(info_hash, size_gb, n=None):
    n = n or info_hash[0]
    return candidate(info_hash, title=f"Series A v0{n} (Digital)", size_bytes=int(size_gb * GB),
                     torrent_url=f"https://nyaa.example/download/{n}.torrent", vol_from="2", vol_to="2")


@pytest.fixture
def setup(ledger, series, qbt, tmp_path):
    sid, sdir = series
    set_budget_gb(ledger.store, 50)
    place = Placement(str(sdir), str(sdir), "volumes live in the series folder")
    save = str(tmp_path / "torrents" / "mangalist")

    def submit(info_hash, size_gb, **kw):
        return Q.submit(qbt, ledger, sid, rel(info_hash, size_gb), ["2"], place, save, **kw)
    return submit


def adds(qbt):
    return [c[1] for c in qbt.calls if c[0] == "add"]


def test_fits_is_sent_over_is_queued_with_all_it_needs(setup, ledger, qbt, caplog):
    with caplog.at_level(logging.INFO):
        a = setup(H1, 30)
        b = setup(H2, 30, only_missing=False)
    assert a.status == S.SENT and a.size_bytes == 30 * GB and a.size_source == SIZE_RELEASE
    assert b.status == S.QUEUED and b.queue_position == 1 and adds(qbt) == ["https://nyaa.example/download/1.torrent"]
    req = ledger.request(b.id)
    assert (req["torrent_url"], req["wanted_volumes"], req["only_missing"], req["budget_bytes"]) == \
        ("https://nyaa.example/download/2.torrent", ["2"], False, 30 * GB)
    assert b.target_dir and ledger.active() == [a]                     # arrivals never sees a queued record
    assert "queued, 1st in line: 30 GB does not fit (using 30 GB of 50 GB, 20 GB free)" in caplog.text


def test_a_release_already_queued_is_not_added_twice(setup):
    setup(H1, 45)
    setup(H2, 10)
    with pytest.raises(SendRefused, match="already queued"):
        setup(H2, 10)


def test_first_come_first_served_and_the_override(setup, ledger, qbt, caplog):
    setup(H1, 45)
    big = setup(H2, 10)
    small = setup(H3, 1)                        # would fit (5 GB free), but one waits ahead of it
    assert (big.status, small.status, small.queue_position) == (S.QUEUED, S.QUEUED, 2)
    with caplog.at_level(logging.INFO):
        now = setup(H4, 10, over_cap="send")    # the owner's "send now, past the cap"
    assert now.status == S.SENT and Q.budget_state(ledger).used_bytes == 55 * GB
    assert "sent PAST the cap on the owner's word" in caplog.text


def test_bigger_than_the_cap_alone_is_refused_unless_sent_anyway(setup, ledger, qbt):
    with pytest.raises(Q.OverBudget, match="62 GB, bigger than the download budget of 50 GB"):
        setup(H1, 62)
    assert ledger.all_records() == [] and adds(qbt) == []           # never silently queued or sent
    assert setup(H1, 62, over_cap="send").status == S.SENT


def test_hand_over_in_order_as_room_frees_up(setup, ledger, qbt, caplog):
    a = setup(H1, 40)
    b = setup(H2, 20)
    c = setup(H3, 5)
    report = Q.run_queue(qbt, ledger)
    assert report.sent == [] and [i for i, _ in report.waiting] == [b.id, c.id]          # nothing jumps ahead
    ledger.set_status(a.id, S.DOWNLOADED, expect=(S.SENT,))
    ledger.set_status(a.id, S.REMOVED, expect=(S.DOWNLOADED,))      # Remove Completed freed 40 GB
    with caplog.at_level(logging.INFO):
        report = Q.run_queue(qbt, ledger)
    assert report.sent == [b.id, c.id] and adds(qbt)[-2:] == ["https://nyaa.example/download/2.torrent",
                                                              "https://nyaa.example/download/3.torrent"]
    assert ledger.get(b.id).status == S.SENT and ledger.queued() == []
    assert "queued download %d (Series A v02 (Digital), 20 GB) handed to qBittorrent (was 1st in line); now using " \
           "20 GB of 50 GB" % b.id in caplog.text
    assert report.text() == "queue: 2 handed over; using 25 GB of 50 GB"


def test_move_to_front_and_remove_from_the_queue(setup, ledger, qbt):
    setup(H1, 49.5)
    b, c, d = setup(H2, 1), setup(H3, 1), setup(H4, 1)
    Q.move_to_front(ledger, d.id)
    assert [r.id for r in ledger.queued()] == [d.id, b.id, c.id]
    assert ledger.get(d.id).queue_position == 1 and ledger.get(c.id).queue_position == 3
    Q.remove_from_queue(ledger, b.id)
    assert ledger.get(b.id).status == S.CANCELLED and [r.queue_position for r in ledger.queued()] == [1, 2]
    set_budget_gb(ledger.store, 51)
    report = Q.run_queue(qbt, ledger)
    assert report.sent == [d.id]                # 1.5 GB free: the one moved to the front goes first
    with pytest.raises(Exception):
        Q.move_to_front(ledger, b.id)           # no longer queued


def test_send_now_from_the_queue(setup, ledger, qbt, caplog):
    setup(H1, 45)
    b = setup(H2, 20)
    with caplog.at_level(logging.INFO):
        out = Q.send_now(qbt, ledger, b.id)
    assert out.status == S.SENT and adds(qbt)[-1] == "https://nyaa.example/download/2.torrent"
    assert "PAST the cap, on the owner's word" in caplog.text
    with pytest.raises(SendRefused, match="only a queued download"):
        Q.send_now(qbt, ledger, b.id)


def test_a_queued_release_that_is_gone_fails_visibly_and_does_not_block(setup, ledger, qbt):
    a = setup(H1, 49)
    b, c = setup(H2, 2), setup(H3, 2)
    ledger.set_status(a.id, S.CANCELLED, expect=(S.SENT,))
    qbt.rejected.add("https://nyaa.example/download/2.torrent")
    report = Q.run_queue(qbt, ledger)
    got = ledger.get(b.id)
    assert got.status == S.FAILED and "not sent from the queue" in got.error and not got.in_client
    assert report.failed[0][0] == b.id and report.sent == [c.id]
    assert Q.budget_state(ledger).used_bytes == 2 * GB                 # the failed one never counted


def test_qbittorrent_down_leaves_the_queue_as_it_was(setup, ledger, qbt):
    a = setup(H1, 49)
    b, c = setup(H2, 2), setup(H3, 2)
    ledger.set_status(a.id, S.CANCELLED, expect=(S.SENT,))
    qbt.add_error = ConnectionError("connection refused")
    report = Q.run_queue(qbt, ledger)
    assert report.error and "cannot be reached" in report.error and report.sent == []
    assert [(r.id, r.queue_position) for r in ledger.queued()] == [(b.id, 1), (c.id, 2)]
    qbt.add_error = None
    qbt.unreachable = True                      # cannot even list: nothing changes, no error raised
    assert "could not list" in Q.run_queue(qbt, ledger).error
    with pytest.raises(Q.ClientDown):
        Q.send_now(type("Down", (), {"ensure_category": lambda *a: (_ for _ in ()).throw(ConnectionError("x"))})(),
                   ledger, b.id)
    assert ledger.get(b.id).status == S.QUEUED


def test_a_target_folder_gone_fails_that_item_only(setup, ledger, qbt, series):
    sid, sdir = series
    a = setup(H1, 49)
    b, c = setup(H2, 2), setup(H3, 2)
    ledger.set_status(a.id, S.CANCELLED, expect=(S.SENT,))
    with ledger.connect() as con:                    # the record's folder no longer inside the series folder
        con.execute("UPDATE ledger SET destination = ? WHERE id = ?", ("/elsewhere", b.id))
    report = Q.run_queue(qbt, ledger)
    assert ledger.get(b.id).status == S.FAILED and report.sent == [c.id]


def test_cap_lowered_below_usage_nothing_is_removed_new_sends_queue(setup, ledger, qbt):
    a, b = setup(H1, 20), setup(H2, 20)
    set_budget_gb(ledger.store, 10)
    assert [ledger.get(i).status for i in (a.id, b.id)] == [S.SENT, S.SENT] and qbt.deleted == []
    assert Q.budget_state(ledger).over
    assert setup(H3, 1).status == S.QUEUED
    assert Q.run_queue(qbt, ledger).sent == []


def test_cap_lowered_under_a_queued_item_it_waits_for_the_owner_and_the_rest_go_on(setup, ledger, qbt):
    a = setup(H1, 45)
    big, small = setup(H2, 30), setup(H3, 2)
    ledger.set_status(a.id, S.CANCELLED, expect=(S.SENT,))
    set_budget_gb(ledger.store, 20)
    report = Q.run_queue(qbt, ledger)
    assert report.sent == [small.id]
    got = ledger.get(big.id)
    assert got.status == S.QUEUED and got.error.startswith("bigger than the 20 GB cap on its own (30 GB)")
    set_budget_gb(ledger.store, 50)
    assert Q.run_queue(qbt, ledger).sent == [big.id] and ledger.get(big.id).error is None


def test_no_limit_hands_everything_over(setup, ledger, qbt):
    setup(H1, 45)
    b, c = setup(H2, 30), setup(H3, 30)
    set_budget_gb(ledger.store, 0)
    assert Q.run_queue(qbt, ledger).sent == [b.id, c.id]
    assert setup(H4, 900).status == S.SENT


def test_sizes_come_from_qbittorrent_once_it_reports_them(setup, ledger, qbt):
    a = setup(H1, 30)
    assert a.size_source == SIZE_RELEASE
    qbt.put(H1, "Pack", {"Series A v02.cbz": data("v02")}, state="downloading", progress=0.1, size=0)
    Q.run_queue(qbt, ledger)
    assert ledger.get(a.id).size_bytes == 30 * GB                      # not reported yet: the release's size stays
    qbt.set(H1, size=12 * GB)
    report = Q.run_queue(qbt, ledger)
    got = ledger.get(a.id)
    assert report.sized == [a.id] and (got.size_bytes, got.size_source) == (12 * GB, SIZE_CLIENT)
    assert Q.run_queue(qbt, ledger).sized == []                        # written once


def test_a_failed_download_counts_while_its_torrent_is_in_qbittorrent(setup, ledger, qbt):
    a = setup(H1, 30)
    qbt.put(H1, "Pack", {"Series A v02.cbz": data("v02")}, size=30 * GB)
    ledger.set_status(a.id, S.FAILED, expect=(S.SENT,), error="none of the torrent's archives is a wanted volume")
    Q.run_queue(qbt, ledger)
    assert Q.budget_state(ledger).used_bytes == 30 * GB                # left alone in qBittorrent: still on the disk
    qbt.delete(H1, delete_files=False)                                  # the owner removed it there
    report = Q.run_queue(qbt, ledger)
    assert report.released == [a.id] and Q.budget_state(ledger).used_bytes == 0


def test_a_failed_release_sent_again_is_not_counted_twice(setup, ledger, qbt):
    a = setup(H1, 30)
    qbt.put(H1, "Pack", {"Series A v02.cbz": data("v02")}, size=30 * GB)
    ledger.set_status(a.id, S.FAILED, expect=(S.SENT,), error="nothing filed")
    again = setup(H1, 30)                       # already in qBittorrent: recorded, not added twice
    Q.run_queue(qbt, ledger)
    assert again.status == S.SENT and Q.budget_state(ledger).used_bytes == 30 * GB


def test_a_partial_send_counts_its_selected_files(setup, ledger, qbt):
    rec = setup(H1, 60, size_bytes=5 * GB, size_source=SIZE_SELECTED, only_missing=False)
    assert rec.status == S.SENT and (rec.size_bytes, rec.size_source) == (5 * GB, SIZE_SELECTED)
    queued = setup(H2, 100, size_bytes=46 * GB, size_source=SIZE_SELECTED, only_missing=True)
    assert queued.status == S.QUEUED and ledger.request(queued.id)["only_missing"] is True


def test_queue_order_is_stable_across_restarts(setup, ledger, tmp_path):
    setup(H1, 49.5)
    b, c, d = setup(H2, 5), setup(H3, 5), setup(H4, 5)
    Q.move_to_front(ledger, c.id)
    order = [r.id for r in ledger.queued()]
    path = ledger.store.path
    store.reset_stores()                                    # a new process: only the database file is shared
    reopened = DownloadLedger(store.get_store(path))
    assert [r.id for r in reopened.queued()] == order == [c.id, b.id, d.id]
    assert [r.queue_position for r in reopened.all_records() if r.status == S.QUEUED] == [2, 1, 3]


def test_a_queued_record_rebuilds_its_release():
    from mangalist.downloads.contracts import DownloadRecord

    rec = DownloadRecord(1, 7, H1, "Series A v02 (Digital)", ("2",), "/lib/S", S.QUEUED, "t", "t")
    c = Q.candidate_of(rec, {"torrent_url": "https://nyaa.example/d/1.torrent", "view_url": "https://nyaa.example/v/1",
                             "size_bytes": 5, "vol_from": "2", "vol_to": "2", "is_pack": False, "trusted": True,
                             "group": "Group", "covers_missing": ["2"]})
    assert (c.info_hash, c.torrent_url, c.vol_from, c.size_bytes, c.trusted, c.covers_missing) == \
        (H1, "https://nyaa.example/d/1.torrent", "2", 5, True, ("2",))
    assert Q.candidate_of(rec, {}).torrent_url == "" and Q.candidate_of(rec, {"vol_from": True}).vol_from is None
