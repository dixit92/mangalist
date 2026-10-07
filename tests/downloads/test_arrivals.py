"""The arrivals pass with a FAKE qBittorrent: every transition, every refusal, idempotent, never deletes library
files, Remove Completed only for filed records in the ``mangalist`` category stopped at their seed goal."""

from __future__ import annotations

import errno
import os

import pytest

from mangalist.downloads.arrivals import run_arrivals, volumes_of
from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.store import Journal, LOCK_NAME, RootLock

from .fakes import HASH, candidate, data

PACK = {
    "Series A v01 (Digital).cbz": data("pack v01"),       # held already
    "Series A v02 (Digital).cbz": data("pack v02", 7000),
    "Series A v03 (Digital).cbz": data("pack v03", 9000),
    "Series A v04 (Digital).cbz": data("pack v04"),       # not wanted
    "release.nfo": b"info",
}


@pytest.fixture
def sent(ledger, series, qbt):
    """A SENT record for volumes 2 and 3 of 'Series A' (filed into the series folder) and its torrent."""
    sid, sdir = series
    rec = ledger.create(sid, candidate(), ["2", "3"], str(sdir))
    qbt.put(HASH, "Series A v01-04 (Digital)", PACK, state="downloading", progress=0.4)
    return rec


def status(ledger, rec):
    return ledger.get(rec.id).status


def test_the_whole_life_of_a_download(ledger, sent, qbt, series):
    _, sdir = series
    report = run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.SENT and report.waiting == [(sent.id, "downloading (40%)")]

    qbt.set(HASH, state="uploading", progress=1.0)
    report = run_arrivals(qbt, ledger)
    rec = ledger.get(sent.id)
    assert report.downloaded == [sent.id] and report.filed == [sent.id]
    assert rec.status == S.FILED and rec.copied is False and rec.error is None
    assert rec.filed_files == ("Series A v02 (Digital).cbz", "Series A v03 (Digital).cbz")
    torrent_dir = qbt.save_root / "Series A v01-04 (Digital)"
    for name in rec.filed_files:
        assert os.path.samefile(sdir / name, torrent_dir / name)              # hard links, names kept
    assert not (sdir / "Series A v04 (Digital).cbz").exists()                  # not wanted
    assert (sdir / "Series A v01 (Digital).cbz").read_bytes() == data("held v01")   # held one untouched
    assert qbt.deleted == [] and report.waiting[0][1].startswith("seeding")

    run_arrivals(qbt, ledger)                                                  # idempotent while seeding
    assert status(ledger, sent) == S.FILED and len(Journal(ledger.store).list_plans()) == 1

    qbt.set(HASH, state="stoppedUP")
    report = run_arrivals(qbt, ledger)
    assert report.removed == [sent.id] and status(ledger, sent) == S.REMOVED
    assert qbt.calls[-1] == ("delete", HASH, True) and not torrent_dir.exists()
    assert (sdir / "Series A v02 (Digital).cbz").read_bytes() == PACK["Series A v02 (Digital).cbz"]   # survives
    assert run_arrivals(qbt, ledger).checked == 0                              # nothing active any more


def test_v4_paused_up_counts_as_stopped(ledger, sent, qbt):
    qbt.set(HASH, state="pausedUP", progress=1.0)
    report = run_arrivals(qbt, ledger)
    assert report.filed == [sent.id] and report.removed == [sent.id]


def test_a_missing_torrent_fails_the_record(ledger, sent, qbt):
    del qbt.infos[HASH]
    report = run_arrivals(qbt, ledger)
    rec = ledger.get(sent.id)
    assert rec.status == S.FAILED and "no longer in qBittorrent's 'mangalist' category" in rec.error
    assert report.failed == [(sent.id, rec.error)] and qbt.deleted == []


def test_a_torrent_moved_to_another_category_is_never_touched(ledger, sent, qbt, series):
    qbt.set(HASH, state="stoppedUP", progress=1.0)
    run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.REMOVED                                  # control: in the category

    sid, sdir = series
    other = ledger.create(sid, candidate(info_hash="cd" * 20), ["4"], str(sdir))
    qbt.put("cd" * 20, "Series A v04", {"Series A v04.cbz": data("v04")}, state="stoppedUP", category="other")
    report = run_arrivals(qbt, ledger)
    assert status(ledger, other) == S.FAILED and qbt.deleted == [HASH] and report.filed == []


def test_a_lying_client_cannot_make_a_foreign_torrent_removed(ledger, sent, qbt, monkeypatch):
    """Even if the client listed a torrent of another category, the pass refuses to file or delete it."""
    qbt.set(HASH, state="stoppedUP", progress=1.0, category="tv")
    monkeypatch.setattr(qbt, "torrents", lambda category: list(qbt.infos.values()))
    run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.FAILED and qbt.deleted == []


def test_filed_but_still_seeding_is_not_removed(ledger, sent, qbt):
    for state in ("uploading", "stalledUP", "queuedUP", "forcedUP", "stoppedDL"):
        qbt.set(HASH, state=state, progress=1.0)
        run_arrivals(qbt, ledger)
        assert status(ledger, sent) == S.FILED and qbt.deleted == []


def test_stopped_by_hand_before_the_seed_goal_is_not_removed(ledger, sent, qbt):
    # The Sonarr / Radarr rule: only a stop at the seed goal counts; the owner's own pause keeps the torrent.
    qbt.set(HASH, state="stoppedUP", progress=1.0, ratio=0.4, seeding_time=600)
    report = run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.FILED and qbt.deleted == []
    assert any("stopped before its seed goal (ratio 0.40 of 2)" in why for _, why in report.waiting)
    qbt.set(HASH, ratio=2.0)                                        # the goal reached: removed at the next pass
    run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.REMOVED and qbt.deleted == [HASH]


def test_a_torrent_without_any_seed_goal_is_never_removed(ledger, sent, qbt):
    qbt.set(HASH, state="stoppedUP", progress=1.0, max_ratio=-1.0, max_seeding_time=-1)
    run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.FILED and qbt.deleted == []


def test_a_missing_or_changed_library_file_blocks_the_removal(ledger, sent, qbt, series):
    _, sdir = series
    qbt.set(HASH, state="uploading", progress=1.0)
    run_arrivals(qbt, ledger)
    filed = sdir / "Series A v03 (Digital).cbz"
    moved = sdir.parent / "moved-by-the-owner.cbz"
    os.rename(filed, moved)                                                    # the owner moved it away
    qbt.set(HASH, state="stoppedUP")
    report = run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.FILED and qbt.deleted == []
    assert "no longer in the library" in report.waiting[0][1]

    filed.write_bytes(b"short")                                                # a different file there now
    report = run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.FILED and qbt.deleted == [] and "not the filed size" in report.waiting[0][1]


def test_a_failed_record_is_never_removed(ledger, series, qbt):
    sid, sdir = series
    rec = ledger.create(sid, candidate(), ["7"], str(sdir))                    # the pack has no volume 7
    qbt.put(HASH, "Series A v01-04 (Digital)", PACK, state="stoppedUP")
    report = run_arrivals(qbt, ledger)
    rec = ledger.get(rec.id)
    assert rec.status == S.FAILED and "none of the torrent's archives" in rec.error and "not wanted" in rec.error
    assert report.failed and qbt.deleted == [] and (qbt.save_root / "Series A v01-04 (Digital)").is_dir()
    run_arrivals(qbt, ledger)
    assert qbt.deleted == [] and Journal(ledger.store).list_plans() == []


def test_remove_completed_off_keeps_the_torrent(ledger, sent, qbt):
    qbt.set(HASH, state="stoppedUP", progress=1.0)
    ledger.set_remove_completed(False)
    report = run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.FILED and qbt.deleted == []
    assert report.waiting == [(sent.id, "filed; Remove Completed is off")]
    assert run_arrivals(qbt, ledger, remove_completed=True).removed == [sent.id]


def test_volumes_held_by_the_time_of_filing_are_skipped(ledger, sent, qbt, series):
    """Volume 2 arrived by another way after the last scan: only volume 3 is filed."""
    _, sdir = series
    (sdir / "Series A - Volume 2.cbz").write_bytes(data("other v02"))
    qbt.set(HASH, state="uploading", progress=1.0)
    run_arrivals(qbt, ledger)
    rec = ledger.get(sent.id)
    assert rec.status == S.FILED and rec.filed_files == ("Series A v03 (Digital).cbz",)
    assert not (sdir / "Series A v02 (Digital).cbz").exists()


def test_everything_wanted_already_held_fails_without_touching_anything(ledger, series, qbt):
    sid, sdir = series
    rec = ledger.create(sid, candidate(), ["1"], str(sdir))
    qbt.put(HASH, "Series A v01-04 (Digital)", PACK)
    run_arrivals(qbt, ledger)
    rec = ledger.get(rec.id)
    assert rec.status == S.FAILED and "already held" in rec.error and qbt.deleted == []


def test_unfinished_files_and_non_archives_are_never_filed(ledger, series, qbt):
    sid, sdir = series
    rec = ledger.create(sid, candidate(), ["2", "3"], str(sdir))
    qbt.put(HASH, "Pack", PACK, partial={"Series A v03 (Digital).cbz": 0.0})  # v03 not selected / not finished
    run_arrivals(qbt, ledger)
    assert ledger.get(rec.id).filed_files == ("Series A v02 (Digital).cbz",)
    assert not (sdir / "release.nfo").exists()


def test_a_vanished_or_foreign_target_folder_fails(ledger, series, qbt, db, tmp_path):
    sid, sdir = series
    gone = ledger.create(sid, candidate(), ["2"], str(sdir / "Volumes"))
    qbt.put(HASH, "Pack", PACK)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    foreign = ledger.create(sid, candidate(info_hash="cd" * 20), ["3"], str(outside))
    qbt.put("cd" * 20, "Pack 2", {"Series A v03.cbz": data("v03")})
    run_arrivals(qbt, ledger)
    assert "is gone" in ledger.get(gone.id).error
    assert "no longer inside the series folder" in ledger.get(foreign.id).error
    assert sorted(p.name for p in sdir.iterdir()) == ["Series A v01 (Digital).cbz"]


def test_a_root_no_longer_configured_fails(ledger, sent, qbt, db):
    qbt.set(HASH, state="uploading", progress=1.0)
    (root,) = db.list_roots()
    db.remove_root(root.id)
    run_arrivals(qbt, ledger)
    rec = ledger.get(sent.id)
    assert rec.status == S.FAILED and "library database" in rec.error


def test_exdev_files_by_copy_and_records_it(ledger, sent, qbt, db, series):
    _, sdir = series

    def exdev(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    qbt.set(HASH, state="stoppedUP", progress=1.0)
    report = run_arrivals(qbt, ledger, journal=Journal(db, link=exdev))
    rec = ledger.get(sent.id)
    assert report.copied == [sent.id] and rec.copied is True
    assert rec.status == S.REMOVED and (sdir / "Series A v03 (Digital).cbz").read_bytes() == \
        PACK["Series A v03 (Digital).cbz"]


def test_qbittorrent_unreachable_changes_nothing(ledger, sent, qbt):
    qbt.unreachable = True
    report = run_arrivals(qbt, ledger)
    assert "could not list" in report.error and status(ledger, sent) == S.SENT


def test_a_root_locked_by_another_instance_waits_then_files(ledger, sent, qbt, db, series):
    _, sdir = series
    (root,) = db.list_roots()
    other = RootLock(root.path, host="another-instance")
    other.acquire()
    qbt.set(HASH, state="uploading", progress=1.0)
    report = run_arrivals(qbt, ledger)
    assert status(ledger, sent) == S.DOWNLOADED and "locked" in report.waiting[0][1]
    assert ledger.plan_id(sent.id) is not None and not (sdir / "Series A v02 (Digital).cbz").exists()
    other.release()
    report = run_arrivals(qbt, ledger)
    assert report.filed == [sent.id] and len(Journal(db).list_plans()) == 1      # the same plan, resumed
    assert not (sdir.parent / LOCK_NAME).exists()


def test_a_crash_after_filing_is_settled_by_the_next_pass(ledger, sent, qbt, db, monkeypatch):
    """The plan applied but the process died before the record said FILED."""
    qbt.set(HASH, state="uploading", progress=1.0)
    real = ledger.set_status

    def crash_on_filed(record_id, status, **kw):
        if status == S.FILED:
            raise KeyboardInterrupt("power cut")
        return real(record_id, status, **kw)

    monkeypatch.setattr(ledger, "set_status", crash_on_filed)
    with pytest.raises(KeyboardInterrupt):
        run_arrivals(qbt, ledger)
    monkeypatch.undo()
    assert status(ledger, sent) == S.DOWNLOADED
    report = run_arrivals(qbt, ledger)
    assert report.filed == [sent.id] and len(ledger.get(sent.id).filed_files) == 2
    assert len(Journal(db).list_plans()) == 1


def test_a_crash_mid_plan_is_recovered_and_resumed(ledger, sent, qbt, db, series):
    _, sdir = series
    qbt.set(HASH, state="uploading", progress=1.0)
    journal = Journal(db)
    calls = []

    def die_on_second(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise KeyboardInterrupt("power cut")
        os.link(src, dst)

    with pytest.raises(KeyboardInterrupt):
        run_arrivals(qbt, ledger, journal=Journal(db, link=die_on_second))
    (plan,) = journal.list_plans()
    assert plan.status == "applying" and [s.state for s in plan.steps] == ["done", "intent"]
    with db.connect() as con:                                                  # the crashed process is gone
        con.execute("UPDATE journal_plans SET pid = -1")
    report = run_arrivals(qbt, ledger)
    assert report.filed == [sent.id] and ledger.get(sent.id).filed_files == (
        "Series A v02 (Digital).cbz", "Series A v03 (Digital).cbz")


def test_torrent_data_inside_a_library_root_is_never_deleted(ledger, series, db, tmp_path):
    """A misconfigured save path inside the library: filing works, deleting is refused."""
    from .fakes import FakeQbt

    sid, sdir = series
    (root,) = db.list_roots()
    inside = FakeQbt(sdir.parent / "_torrents")
    inside.save_root.mkdir()
    rec = ledger.create(sid, candidate(), ["2"], str(sdir))
    inside.put(HASH, "Pack", PACK, state="stoppedUP")
    report = run_arrivals(inside, ledger)
    assert status(ledger, rec) == S.FILED and inside.deleted == []
    assert "overlaps the library root" in report.waiting[0][1]


def test_one_bad_record_does_not_stop_the_others(ledger, series, qbt, monkeypatch):
    sid, sdir = series
    first = ledger.create(sid, candidate(), ["2"], str(sdir))
    second = ledger.create(sid, candidate(info_hash="cd" * 20), ["3"], str(sdir))
    qbt.put(HASH, "Pack", PACK)
    qbt.put("cd" * 20, "Pack 2", {"Series A v03.cbz": data("v03")})
    real = qbt.files

    def broken(info_hash):
        if info_hash == HASH:
            raise RuntimeError("bad answer")
        return real(info_hash)

    monkeypatch.setattr(qbt, "files", broken)
    report = run_arrivals(qbt, ledger)
    assert report.errors and report.errors[0][0] == first.id and status(ledger, first) == S.DOWNLOADED
    assert status(ledger, second) == S.FILED


def test_volume_numbers_of_names():
    assert volumes_of("Series A v03 (Digital).cbz") == {3}
    assert volumes_of("Series A v01-03.cbz") == {1, 2, 3}
    assert volumes_of("Series A c012.cbz") == set()
    assert volumes_of("07.cbz") == set() and volumes_of("07.cbz", "volumes") == {7}
