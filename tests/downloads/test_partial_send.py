"""send_pick with ``only_missing``: add STOPPED -> file list -> ``filePrio`` -> read back -> start, against a fake
qBittorrent. A failure in the middle never leaves a running torrent, a repeated send never adds twice, and arrivals
files a torrent whose skipped files were never downloaded."""

from __future__ import annotations

import os

import pytest

from mangalist.downloads.arrivals import run_arrivals
from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.downloads.contracts import Placement
from mangalist.downloads.service import PackSetupError, PackWait, SendRefused, send_pick

from .fakes import HASH, candidate, data
from .torrents import MB, PartialQbt

URL = "https://nyaa.example/download/1.torrent"
ROOT_NAME = "Series A v01-08 (Digital)"
NAMES = [f"Series A v{n:02d} (Digital).cbz" for n in range(1, 9)]


class Tick:
    """A clock that only moves when the code sleeps."""

    def __init__(self):
        self.t, self.slept = 0.0, []

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


@pytest.fixture
def tick():
    return Tick()


@pytest.fixture
def wait(tick):
    return PackWait(timeout=5.0, interval=0.5, sleep=tick.sleep, clock=tick.clock)


@pytest.fixture
def pq(tmp_path):
    save = tmp_path / "torrents" / "mangalist"
    save.mkdir(parents=True)
    q = PartialQbt(save)
    q.offer(URL, HASH, ROOT_NAME, [(n, 10 * MB) for n in NAMES])
    return q


@pytest.fixture
def pack_candidate():
    return candidate(title=ROOT_NAME, vol_from="1", vol_to="8", torrent_url=URL)


@pytest.fixture
def place(series):
    _, sdir = series
    return Placement(str(sdir), str(sdir), "volumes live in the series folder")


@pytest.fixture
def save(tmp_path):
    return str(tmp_path / "torrents" / "mangalist")


def partial(pq, ledger, series, place, save, wait, cand, wanted=("2", "3"), **kw):
    outcomes = []
    rec = send_pick(pq, ledger, series[0], cand, list(wanted), place, save, only_missing=True, wait=wait,
                    on_pack=outcomes.append, **kw)
    return rec, outcomes[0]


def names_of(call_ids):
    return [NAMES[i] for i in call_ids]


# --- the happy path ------------------------------------------------------------------------------------------

def test_stopped_add_then_priorities_then_start_in_that_order(pq, ledger, series, place, save, wait, pack_candidate):
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate)
    kinds = [c[0] for c in pq.calls]
    assert kinds == ["ensure_category", "add", "set_file_priority", "start"]
    assert pq.calls[1] == ("add", URL, "mangalist", True)                       # added STOPPED
    assert pq.states == ["stoppedDL"]                                           # ... and still stopped when started
    skip = pq.calls_named("set_file_priority")[0]
    assert skip[2] == (0, 3, 4, 5, 6, 7) and skip[3] == 0
    assert {n: p for n, p in pq.prio_of(HASH).items() if p} == {
        f"{ROOT_NAME}/{NAMES[1]}": 1, f"{ROOT_NAME}/{NAMES[2]}": 1}
    assert pq.infos[HASH].state == "downloading"
    assert rec.status == S.SENT and rec.wanted_volumes == ("2", "3") and ledger.active() == [rec]
    assert outcome.partial and (outcome.selection.kept_files, outcome.selection.total_files) == (2, 8)


def test_the_priorities_are_read_back_before_the_start(pq, ledger, series, place, save, wait, pack_candidate):
    partial(pq, ledger, series, place, save, wait, pack_candidate)
    kinds = [c[0] for c in pq.calls]
    assert kinds.index("start") > kinds.index("set_file_priority")


def test_waits_for_qbittorrent_to_list_the_torrent_and_its_files(pq, ledger, series, place, save, wait, tick,
                                                                 pack_candidate):
    pq.appear_after = 3
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert tick.slept and set(tick.slept) == {0.5} and outcome.partial
    assert rec.status == S.SENT


def test_the_file_ids_are_the_ones_qbittorrent_reports(pq, ledger, series, place, save, wait, pack_candidate):
    pq.offer(URL, HASH, ROOT_NAME, [(n, 10 * MB) for n in NAMES])
    pq.appear_after = 0
    partial(pq, ledger, series, place, save, wait, pack_candidate)
    rows = {r["name"]: r["index"] for r in pq.rows[HASH]}
    skipped = {n for n, p in pq.prio_of(HASH).items() if p == 0}
    assert skipped == {f"{ROOT_NAME}/{n}" for n in NAMES if n not in NAMES[1:3]}
    assert sorted(rows.values()) == list(range(8))


def test_unknown_files_are_kept_and_counted(pq, ledger, series, place, save, wait, pack_candidate):
    pq.packs[URL] = (HASH, ROOT_NAME, [(n, 10 * MB) for n in NAMES] + [("The Great Book.cbz", 3 * MB), ("cover.jpg", 1)])
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate)
    prio = pq.prio_of(HASH)
    assert prio[f"{ROOT_NAME}/The Great Book.cbz"] == 1 and prio[f"{ROOT_NAME}/cover.jpg"] == 0
    assert outcome.selection.unknown_kept == 1 and outcome.selection.kept_files == 3


def test_a_single_file_release_is_just_started(pq, ledger, series, place, save, wait):
    cand = candidate(title="Series A v02", vol_from="2", vol_to="2", is_pack=False, torrent_url=URL)
    pq.packs[URL] = (HASH, "Series A v02 (Digital).cbz", [("Series A v02 (Digital).cbz", 10 * MB)])
    rec, outcome = partial(pq, ledger, series, place, save, wait, cand, wanted=("2",))
    assert [c[0] for c in pq.calls] == ["ensure_category", "add", "start"] and not outcome.partial


# --- the fallbacks ------------------------------------------------------------------------------------------

def test_nothing_to_leave_out_starts_the_whole_pack(pq, ledger, series, place, save, wait, pack_candidate):
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate, wanted=tuple(str(n) for n in range(1, 9)))
    assert pq.calls_named("set_file_priority") == [] and pq.calls_named("start") and not outcome.partial
    assert pq.infos[HASH].state == "downloading"


def test_no_file_names_a_wanted_volume_so_the_whole_pack_goes_with_the_reason(pq, ledger, series, place, save, wait,
                                                                              pack_candidate):
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate, wanted=("42",))
    assert pq.calls_named("set_file_priority") == [] and not outcome.partial
    assert "none of the files names a missing volume" in outcome.note and rec.status == S.SENT


def test_a_magnet_only_release_is_sent_whole_without_a_stopped_add(pq, ledger, series, place, save, wait):
    cand = candidate(title=ROOT_NAME, vol_from="1", vol_to="8", torrent_url="")
    pq.offer(cand.magnet, HASH, ROOT_NAME, [(n, 10 * MB) for n in NAMES])
    rec, outcome = partial(pq, ledger, series, place, save, wait, cand)
    assert pq.calls[1] == ("add", cand.magnet, "mangalist", False)
    assert [c[0] for c in pq.calls] == ["ensure_category", "add"] and not outcome.partial
    assert "magnet" in outcome.note and rec.status == S.SENT


def test_the_whole_pack_send_is_what_it_always_was(pq, ledger, series, place, save):
    cand = candidate(title=ROOT_NAME, vol_from="1", vol_to="8", torrent_url=URL)
    rec = send_pick(pq, ledger, series[0], cand, ["2", "3"], place, save)
    assert pq.calls == [("ensure_category", "mangalist", save), ("add", URL, "mangalist", False)]
    assert rec.status == S.SENT


# --- failure in the middle -------------------------------------------------------------------------------------

def test_a_timeout_removes_what_was_added_and_starts_nothing(pq, ledger, series, place, save, wait, pack_candidate):
    pq.appear_after = 10 ** 6
    with pytest.raises(PackSetupError, match="within 5 s.*not started"):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert pq.calls_named("start") == [] and pq.calls_named("delete") == [("delete", HASH, False)]
    assert pq.infos == {} and ledger.active() == []


def test_priorities_that_fail_leave_nothing_running_and_no_record(pq, ledger, series, place, save, wait, pack_candidate):
    pq.fail["set_file_priority"] = RuntimeError("qBittorrent refused filePrio")
    with pytest.raises(PackSetupError, match="refused filePrio.*not started"):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert pq.calls_named("start") == []
    assert ("stop", HASH) in pq.calls and pq.calls_named("delete") == [("delete", HASH, False)]
    assert pq.infos == {} and ledger.active() == []


def test_priorities_that_do_not_stick_are_caught_before_the_start(pq, ledger, series, place, save, wait, pack_candidate):
    pq.ignore_priorities = True
    with pytest.raises(PackSetupError, match="did not keep the priority"):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert pq.calls_named("start") == [] and pq.infos == {}


def test_a_failed_start_stops_and_removes_the_torrent(pq, ledger, series, place, save, wait, pack_candidate):
    pq.fail["start"] = RuntimeError("could not start")
    with pytest.raises(PackSetupError):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert ("stop", HASH) in pq.calls and pq.infos == {} and ledger.active() == []


def test_a_torrent_that_cannot_be_removed_stays_stopped_never_running(pq, ledger, series, place, save, wait,
                                                                      pack_candidate):
    pq.fail["set_file_priority"] = RuntimeError("boom")
    pq.fail["delete"] = RuntimeError("cannot delete")
    with pytest.raises(PackSetupError):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert pq.infos[HASH].state == "stoppedDL" and pq.calls_named("start") == []


def test_a_leftover_stopped_torrent_is_finished_by_the_next_send(pq, ledger, series, place, save, wait, pack_candidate):
    pq.fail["set_file_priority"] = RuntimeError("boom")
    pq.fail["delete"] = RuntimeError("cannot delete")
    with pytest.raises(PackSetupError):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    pq.fail.clear()
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert len(pq.calls_named("add")) == 1                                       # never added twice
    assert outcome.partial and pq.infos[HASH].state == "downloading" and rec.status == S.SENT
    assert sum(1 for p in pq.prio_of(HASH).values() if p) == 2


def test_qbittorrent_unreachable_before_the_add_adds_nothing(pq, ledger, series, place, save, wait, pack_candidate):
    pq.unreachable = True
    with pytest.raises(ConnectionError):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert pq.calls_named("add") == [] and ledger.active() == []


def test_a_refused_add_of_a_torrent_that_is_not_there_stays_an_error(pq, ledger, series, place, save, wait,
                                                                     pack_candidate):
    pq.fail["add"] = RuntimeError("invalid torrent")
    with pytest.raises(RuntimeError, match="invalid torrent"):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert pq.calls_named("start") == [] and ledger.active() == []


# --- repeated sends --------------------------------------------------------------------------------------------

def test_a_repeated_send_is_refused_and_adds_nothing(pq, ledger, series, place, save, wait, pack_candidate):
    partial(pq, ledger, series, place, save, wait, pack_candidate)
    before = list(pq.calls)
    with pytest.raises(SendRefused, match="already being downloaded"):
        partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert pq.calls == before


def test_a_send_whose_record_was_lost_is_recorded_not_added_again(pq, ledger, series, place, save, wait,
                                                                  pack_candidate, monkeypatch):
    real_create = ledger.create

    def crash(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(ledger, "create", crash)
    with pytest.raises(OSError):
        partial(pq, ledger, series, place, save, wait, pack_candidate)               # started, but never recorded
    assert pq.infos[HASH].state == "downloading"
    monkeypatch.setattr(ledger, "create", real_create)
    before = len(pq.calls)
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate)
    assert len(pq.calls_named("add")) == 1 and rec.status == S.SENT and not outcome.partial
    assert "already in qBittorrent" in outcome.note or "left as it is" in outcome.note
    assert [c[0] for c in pq.calls[before:]] == ["ensure_category"]                  # nothing was touched


def test_a_torrent_that_is_there_with_a_wanted_file_switched_off_gets_it_switched_on(pq, ledger, series, place, save,
                                                                                    wait, pack_candidate):
    pq.add(URL, category="mangalist", stopped=True)
    pq.start(HASH)
    for r in pq.rows[HASH]:
        r["priority"] = 0                                                           # an earlier send skipped everything else
    pq.calls.clear()
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate, wanted=("4",))
    assert pq.calls_named("add") == [] and not outcome.partial
    assert pq.calls_named("set_file_priority") == [("set_file_priority", HASH, (3,), 1)]
    assert pq.calls_named("start")
    assert rec.status == S.SENT


def test_a_whole_pack_send_of_a_torrent_with_skipped_files_switches_them_on(pq, ledger, series, place, save,
                                                                             pack_candidate):
    pq.add(URL, category="mangalist", stopped=True)
    pq.start(HASH)
    for r in pq.rows[HASH][3:]:
        r["priority"] = 0
    pq.fail["add"] = RuntimeError("qBittorrent did not add the torrent: already in qBittorrent")
    rec = send_pick(pq, ledger, series[0], pack_candidate, ["5"], Placement(*[str(series[1])] * 2, "x"), save)
    assert all(r["priority"] == 1 for r in pq.rows[HASH]) and rec.status == S.SENT


# --- arrivals -----------------------------------------------------------------------------------------------------

def test_arrivals_files_a_partial_torrent_whose_skipped_files_are_absent(pq, ledger, series, place, save, wait,
                                                                         pack_candidate):
    sid, sdir = series
    rec, outcome = partial(pq, ledger, series, place, save, wait, pack_candidate)
    run1 = run_arrivals(pq, ledger)
    assert run1.filed == [] and run1.checked == 1                                  # still downloading: waits

    payloads = {f"{ROOT_NAME}/{n}": data(n, 4000 + i) for i, n in enumerate(NAMES)}
    pq.finish(HASH, payloads)
    on_disk = sorted(p.name for p in (pq.save_root / ROOT_NAME).iterdir())
    assert on_disk == [NAMES[1], NAMES[2]]                                          # skipped files were never written
    assert pq.infos[HASH].progress == 1.0 and any(r["progress"] == 0.0 for r in pq.rows[HASH])

    report = run_arrivals(pq, ledger)
    assert report.downloaded == [rec.id] and report.filed == [rec.id] and report.failed == []
    done = ledger.get(rec.id)
    assert done.status == S.FILED and done.filed_files == (NAMES[1], NAMES[2])
    for name in done.filed_files:
        assert os.path.samefile(sdir / name, pq.save_root / ROOT_NAME / name)
    assert not (sdir / NAMES[3]).exists()

    from dataclasses import replace
    pq.infos[HASH] = replace(pq.infos[HASH], state="stoppedUP", ratio=2.0)           # stopped at its seed goal
    report = run_arrivals(pq, ledger)
    assert report.removed == [rec.id] and pq.deleted == [HASH]
    assert (sdir / NAMES[1]).read_bytes() == payloads[f"{ROOT_NAME}/{NAMES[1]}"]      # the library files survive
