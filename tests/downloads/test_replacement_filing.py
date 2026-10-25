"""Filing a replacement (other releases, owner 2026-10-24: "Replace, keep old"): the owner's copies a release covers go
to the holding folder through the journal first (a replaced-files batch, restorable), then the release's files are
filed; the earlier downloads' torrents stay removable. Made-up names, a FAKE qBittorrent."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mangalist import store, upgrades
from mangalist.downloads.arrivals import run_arrivals
from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.store import Journal
from mangalist.store.replacements import ReplacementStore

from .fakes import FakeQbt, candidate, data, scan

SERIES = "Made Up Series"
OLD = {v: data(f"old v{v}", 4000 + int(v)) for v in ("1", "2", "3")}
NEW_HASH = "cd" * 20


def name(v: str, tag: str = "Old Group") -> str:
    return f"{SERIES} v{v.zfill(2)} ({tag}).cbz"


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def ledger(db):
    from mangalist.store.downloads import DownloadLedger

    return DownloadLedger(db)


@pytest.fixture
def holding(tmp_path, db) -> Path:
    folder = tmp_path / "appdata" / "replaced"
    upgrades.set_holding_folder(db, str(folder))
    return folder


@pytest.fixture
def library(tmp_path, db):
    """A series holding v01-v03 (each filed by an earlier download, still seeding) - (series id, folder, qbt,
    the earlier records)."""
    root = tmp_path / "library" / "Manga"
    sdir = root / SERIES
    sdir.mkdir(parents=True)
    (sdir / "notes.txt").write_text("the owner's own file")
    db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(db.list_roots()[0].id, SERIES).id
    qbt = FakeQbt(tmp_path / "torrents" / "mangalist")
    qbt.save_root.mkdir(parents=True)
    from mangalist.store.downloads import DownloadLedger

    ledger = DownloadLedger(db)
    earlier = []
    for v, payload in OLD.items():
        h = f"{int(v):02d}" * 20
        rec = ledger.create(sid, candidate(h, title=f"{SERIES} v{v.zfill(2)} (Old Group)", vol_from=v, vol_to=v,
                                           is_pack=False), [v], str(sdir))
        qbt.put(h, f"{SERIES} v{v.zfill(2)} (Old Group)", {name(v): payload})
        earlier.append(rec)
    run_arrivals(qbt, ledger)
    assert all(ledger.get(r.id).status == S.FILED for r in earlier)
    scan(db)
    return sid, sdir, qbt, earlier


def send_collection(ledger, sid, sdir, qbt, *, wanted=("1", "2", "3", "4"), replace=("1", "2", "3"),
                    files=None, tag="New Group"):
    files = files if files is not None else {name(v, tag): data(f"new v{v}", 6000 + int(v)) for v in ("1", "2", "3", "4")}
    rec = ledger.create(sid, candidate(NEW_HASH, title=f"{SERIES} v01-04 ({tag})", vol_from="1", vol_to="4"),
                        list(wanted), str(sdir), replace_volumes=list(replace))
    qbt.put(NEW_HASH, f"{SERIES} v01-04 ({tag})", files)
    return rec


def batches(db):
    return ReplacementStore(db).all()


def test_the_record_keeps_the_volumes_it_replaces(ledger, library):
    sid, sdir, qbt, _ = library
    rec = send_collection(ledger, sid, sdir, qbt, replace=("1", "2", "9"))
    assert ledger.get(rec.id).replace_volumes == ("1", "2")         # only wanted ones
    assert ledger.request(rec.id)["replace_volumes"] == ["1", "2"]


def test_a_newer_collection_replaces_the_old_copies_which_go_to_the_holding_folder(db, ledger, library, holding):
    sid, sdir, qbt, earlier = library
    rec = send_collection(ledger, sid, sdir, qbt)
    report = run_arrivals(qbt, ledger)
    out = ledger.get(rec.id)
    assert report.filed == [rec.id] and out.status == S.FILED
    assert sorted(out.filed_files) == [name(v, "New Group") for v in ("1", "2", "3", "4")]
    for v in ("1", "2", "3"):
        assert not (sdir / name(v)).exists()                         # the old copies left the library ...
    (batch,) = batches(db)
    assert batch.status == "held" and batch.download_id < 0          # ... as one held batch, not the record's own
    assert batch.volumes == ("1", "2", "3") and sorted(f.rel for f in batch.files) == [name(v) for v in "123"]
    assert all(f.chapters == "" for f in batch.files)
    held = {f.rel: Path(batch.holding_dir) for f in batch.files}
    found = sorted(p.name for p in Path(batch.holding_dir).rglob("*.cbz"))
    assert found == sorted(held) and all(p.read_bytes() == OLD[p.name.split(" v")[1][:2].lstrip("0")]
                                         for p in Path(batch.holding_dir).rglob("*.cbz"))
    assert (sdir / "notes.txt").read_text() == "the owner's own file"   # not covered: untouched
    plan = Journal(db).get_plan(batch.plan_id)
    assert {s.op for s in plan.steps} == {"hold"} and all(s.state == "done" for s in plan.steps)
    assert ledger.request(rec.id)["replace_batch"] == batch.id
    # the earlier torrents (their filed files now held) and the new one can all go at their seed goal
    for r in (*earlier, out):
        qbt.set(r.info_hash, state="stoppedUP")
    report = run_arrivals(qbt, ledger)
    assert sorted(report.removed) == sorted(r.id for r in (*earlier, out))
    for v in ("1", "2", "3", "4"):
        assert (sdir / name(v, "New Group")).exists()


def test_the_same_file_name_is_taken_over_after_the_old_copy_left(db, ledger, library, holding):
    # the naming scheme gives the new volume the old one's name: the hold comes first, so the link can take it
    sid, sdir, qbt, _ = library
    rec = send_collection(ledger, sid, sdir, qbt, wanted=("1",), replace=("1",),
                          files={name("1"): data("newer v1", 7777)})
    run_arrivals(qbt, ledger)
    assert ledger.get(rec.id).status == S.FILED
    assert (sdir / name("1")).read_bytes() == data("newer v1", 7777)
    (batch,) = batches(db)
    assert [f.rel for f in batch.files] == [name("1")]
    assert (sdir / name("2")).read_bytes() == OLD["2"]               # not chosen: stays


def test_restore_puts_the_old_copy_back_when_its_name_is_free(db, ledger, library, holding):
    sid, sdir, qbt, _ = library
    send_collection(ledger, sid, sdir, qbt, wanted=("2",), replace=("2",))
    run_arrivals(qbt, ledger)
    (batch,) = batches(db)
    after = upgrades.restore_batch(db, batch.id)
    assert after.status == "restored" and (sdir / name("2")).read_bytes() == OLD["2"]


def test_untick_a_volume_and_its_old_copy_stays_and_its_new_file_is_not_filed(db, ledger, library, holding):
    sid, sdir, qbt, _ = library
    rec = send_collection(ledger, sid, sdir, qbt, wanted=("1", "3", "4"), replace=("1", "3"))
    run_arrivals(qbt, ledger)
    out = ledger.get(rec.id)
    assert sorted(out.filed_files) == [name(v, "New Group") for v in ("1", "3", "4")]
    assert (sdir / name("2")).read_bytes() == OLD["2"] and not (sdir / name("2", "New Group")).exists()
    (batch,) = batches(db)
    assert batch.volumes == ("1", "3")


def test_a_file_holding_more_than_the_chosen_volumes_is_never_replaced(db, ledger, tmp_path, holding):
    root = tmp_path / "library" / "Manga"
    sdir = root / SERIES
    sdir.mkdir(parents=True)
    (sdir / f"{SERIES} v01-02 (Old Group).cbz").write_bytes(data("old omnibus"))
    db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(db.list_roots()[0].id, SERIES).id
    qbt = FakeQbt(tmp_path / "torrents")
    qbt.save_root.mkdir(parents=True)
    rec = send_collection(ledger, sid, sdir, qbt, wanted=("1",), replace=("1",))
    run_arrivals(qbt, ledger)
    out = ledger.get(rec.id)
    assert out.status == S.FAILED and "nothing filed" in out.error and "already held" in out.error
    assert (sdir / f"{SERIES} v01-02 (Old Group).cbz").exists() and batches(db) == []


def test_an_unusable_holding_folder_makes_it_wait_with_nothing_moved(db, ledger, library, holding, tmp_path):
    sid, sdir, qbt, _ = library
    rec = send_collection(ledger, sid, sdir, qbt)
    db.set_setting(upgrades.KEY_HOLDING_FOLDER, str(sdir.parent / "inside the root"))   # stored behind the check
    report = run_arrivals(qbt, ledger)
    assert ledger.get(rec.id).status == S.DOWNLOADED and batches(db) == []
    assert any(rid == rec.id and "holding folder" in why for rid, why in report.waiting)
    assert all((sdir / name(v)).exists() for v in "123")
    upgrades.set_holding_folder(db, str(holding))
    run_arrivals(qbt, ledger)
    assert ledger.get(rec.id).status == S.FILED and not (sdir / name("1")).exists()


def test_when_nothing_can_be_filed_the_old_copies_are_put_back(db, ledger, library, holding, monkeypatch):
    from mangalist.store.journal import StepRefused

    sid, sdir, qbt, _ = library
    rec = send_collection(ledger, sid, sdir, qbt)

    def refuse(self, *a, **kw):
        raise StepRefused("refused for the test")

    monkeypatch.setattr(Journal, "plan_links", refuse)
    run_arrivals(qbt, ledger)
    out = ledger.get(rec.id)
    assert out.status == S.FAILED and "put back" in out.error
    (batch,) = batches(db)
    assert batch.status == "restored" and all((sdir / name(v)).read_bytes() == OLD[v] for v in "123")


def test_a_pass_that_stops_after_the_hold_resumes_with_the_filing(db, ledger, library, holding, monkeypatch):
    sid, sdir, qbt, _ = library
    rec = send_collection(ledger, sid, sdir, qbt)
    real = Journal.plan_links

    def crash(self, *a, **kw):
        raise RuntimeError("the process stopped here")

    monkeypatch.setattr(Journal, "plan_links", crash)
    report = run_arrivals(qbt, ledger)
    assert report.errors and ledger.get(rec.id).status == S.DOWNLOADED
    (batch,) = batches(db)
    assert batch.status == "held"
    monkeypatch.setattr(Journal, "plan_links", real)
    run_arrivals(qbt, ledger)
    out = ledger.get(rec.id)
    assert out.status == S.FILED and len(out.filed_files) == 4 and len(batches(db)) == 1   # the same batch, once


def test_held_replaced_volumes_can_be_emptied_while_the_new_volumes_are_in_the_library(db, ledger, library, holding):
    sid, sdir, qbt, _ = library
    send_collection(ledger, sid, sdir, qbt)
    run_arrivals(qbt, ledger)
    (batch,) = batches(db)
    assert upgrades.volumes_in_library_problem(db, batch) is None
    after = upgrades.empty_now(db, batch.id)
    assert after.status == "purged" and not list(Path(batch.holding_dir).rglob("*.cbz"))
    assert all((sdir / name(v, "New Group")).exists() for v in "1234")


def test_chapters_the_new_volumes_hold_follow_the_upgrade_rules(db, ledger, tmp_path, holding, monkeypatch):
    from mangalist.knowledge import VolumeInfo

    root = tmp_path / "library" / "Manga"
    sdir = root / SERIES
    sdir.mkdir(parents=True)
    (sdir / name("1")).write_bytes(OLD["1"])
    for n in (4, 5, 6):
        (sdir / f"{SERIES} c{n:03d}.cbz").write_bytes(data(f"chapter {n}"))
    db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(db.list_roots()[0].id, SERIES).id
    monkeypatch.setattr(upgrades, "_knowledge_volumes", lambda db_, series: (
        (VolumeInfo("1", "1", "3"), VolumeInfo("2", "4", "6")), ""))
    qbt = FakeQbt(tmp_path / "torrents")
    qbt.save_root.mkdir(parents=True)
    rec = send_collection(ledger, sid, sdir, qbt, wanted=("1", "2"), replace=("1",),
                          files={name(v, "New Group"): data(f"new v{v}", 6000 + int(v)) for v in ("1", "2")})
    run_arrivals(qbt, ledger)
    assert ledger.get(rec.id).status == S.FILED
    report = upgrades.after_filing(db, ledger)
    assert report.files_moved == 3                                   # chapters 4-6: volume 2 holds them
    chapter_batch = ReplacementStore(db).for_download(rec.id)
    assert chapter_batch.status == "held" and len(chapter_batch.files) == 3
    assert [b.download_id < 0 for b in batches(db)].count(True) == 1     # the old v01 copy: its own batch
    assert not any(os.path.exists(sdir / f"{SERIES} c{n:03d}.cbz") for n in (4, 5, 6))
