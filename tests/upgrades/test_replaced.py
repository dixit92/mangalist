"""The replaced chapters after a real filing (temporary library, fake qBittorrent): holding mode moves them through the
journal (restore = undo), keeps their layout outside every root and empties only expired batches from the holding
folder; delete mode waits for the owner's confirmation and deletes only through the guarded path; the headless job
records instead of deleting."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import pytest

from mangalist import upgrades
from mangalist.store.journal import Journal, StepRefused
from mangalist.store.lock import RootLock
from mangalist.store.replacements import ReplacementConflict, ReplacementStore

from ..downloads.fakes import data
from .conftest import SERIES, chapter_files, file_volumes, make_series, names

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
CH = ("001", "002", "003", "004", "005")


def batches(db):
    return ReplacementStore(db).all()


def test_holding_moves_exactly_the_covered_chapters_and_restore_puts_them_back(db, ledger, library, holding, known,
                                                                               tmp_path, caplog):
    sid, sdir = make_series(db, library, chapter_files(CH, "Chapters"))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    with caplog.at_level(logging.INFO):
        report = upgrades.after_filing(db, ledger, now=NOW)
    assert report.files_moved == 3 and "3 replaced chapter file(s) moved" in report.summary()
    (batch,) = batches(db)
    assert batch.status == "held" and batch.volumes == ("1",) and len(batch.files) == 3
    assert names(sdir) == ["Chapters/Series U c004.cbz", "Chapters/Series U c005.cbz", f"{SERIES} v01 (Digital).cbz"]
    # The holding folder keeps the root-relative layout, under the batch's own folder.
    top = holding / f"2026-10-09 batch {batch.id}"
    assert batch.holding_dir == str(top)
    assert names(top) == [f"Manga/{SERIES}/Chapters/{SERIES} c00{n}.cbz" for n in (1, 2, 3)]
    plan = Journal(db).get_plan(batch.plan_id)
    assert plan.status == "applied" and {s.op for s in plan.steps} == {"hold"} and plan.root_path == str(library)
    assert batch.purge_after == "2026-11-08T12:00:00+00:00"            # the default 30 days
    assert "moved" in caplog.text and "Series U c001.cbz" in caplog.text
    # Restore: the plan undone, the files back where they were, the batch's empty folders gone.
    after = upgrades.restore_batch(db, batch.id)
    assert after.status == "restored"
    assert len(names(sdir)) == 6 and not top.exists()
    assert Journal(db).get_plan(batch.plan_id).status == "undone"
    with pytest.raises(ReplacementConflict):
        upgrades.restore_batch(db, batch.id)


def test_a_download_is_looked_at_once_and_older_downloads_never(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    upgrades.after_filing(db, ledger, now=NOW)
    assert len(batches(db)) == 1
    # A database upgraded with downloads already filed: those are never looked at.
    ReplacementStore(db).store.set_meta("upgrades.first_download", "99")
    assert ReplacementStore(db).unexamined([5, 100]) == [100]


def test_no_volume_list_means_nothing_is_replaced(db, ledger, library, holding, known, tmp_path):
    known[:] = []
    sid, sdir = make_series(db, library, chapter_files(CH))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    assert batch.status == "nothing" and "no volume list" in batch.error and batch.files == ()
    assert len(names(sdir)) == 6


def test_a_volume_that_was_not_filed_replaces_nothing(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(("004", "005", "006", "007")))
    file_volumes(ledger, tmp_path, sid, sdir, ("2",))         # volume 2 = chapters 4-6
    upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    assert [f.chapters for f in batch.files] == ["4", "5", "6"]
    assert names(sdir) == [f"{SERIES} c007.cbz", f"{SERIES} v02 (Digital).cbz"]


def test_a_holding_folder_inside_a_root_is_refused_and_nothing_moves(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    for bad in (str(library / "replaced"), str(library), str(library.parent), "relative/path"):
        with pytest.raises(ValueError):
            upgrades.set_holding_folder(db, bad)
    assert upgrades.load_settings(db).holding_folder == str(holding)
    # A bad value stored behind the setter's back (an older build, a hand edit) is still refused when acting.
    db.set_setting(upgrades.KEY_HOLDING_FOLDER, str(sdir / "held"))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    report = upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    assert batch.status == "pending" and "overlaps the library root" in batch.error and report.pending == [batch.id]
    assert len(names(sdir)) == 6 and not (sdir / "held").exists()
    # Fixed: the next pass moves them.
    upgrades.set_holding_folder(db, str(holding))
    upgrades.after_filing(db, ledger, now=NOW)
    assert ReplacementStore(db).get(batch.id).status == "held" and len(names(sdir)) == 3


def test_a_holding_folder_inside_a_mangapixer_library_or_the_download_folder_is_refused(db, library, tmp_path):
    from mangalist.store.mangapixer import MangaPixerCache, Mapping

    root = db.add_root(str(library), "Manga")
    # The root is the folder "Manga" inside a MangaPixer library whose own folder is library/ (prefix ["Manga"]).
    MangaPixerCache(db).save_mapping(Mapping(root_id=root.id, library_id="lib0", prefix=["Manga"]))
    problem = upgrades.holding_problem(db, str(library.parent / "Other" / "replaced"))
    assert problem and "MangaPixer library" in problem
    db.set_setting("downloads.save_path", str(tmp_path / "torrents"))
    assert "download folder" in upgrades.holding_problem(db, str(tmp_path / "torrents" / "replaced"))
    assert upgrades.holding_problem(db, str(tmp_path / "appdata" / "replaced")) is None


def test_the_journal_refuses_unsafe_hold_steps(db, library, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(("001",)))
    journal = Journal(db)
    held = str(tmp_path / "held")
    with pytest.raises(StepRefused, match="overlaps the root"):
        journal.plan_holding("t", [str(sdir / f"{SERIES} c001.cbz")], str(library), str(sdir / "x"))
    with pytest.raises(StepRefused, match="not a plain file"):
        journal.plan_holding("t", [str(sdir)], str(library), held)
    with pytest.raises(StepRefused, match="outside the plan's root"):
        journal.plan_holding("t", [str(tmp_path / "elsewhere.cbz")], str(library), held)
    target = tmp_path / "outside.cbz"
    target.write_bytes(b"x")
    os.symlink(target, sdir / "link.cbz")
    with pytest.raises(StepRefused, match="symbolic link"):
        journal.plan_holding("t", [str(sdir / "link.cbz")], str(library), held)
    with pytest.raises(StepRefused, match="lock artefact"):
        journal.plan_holding("t", [str(library / ".mangalist.lock")], str(library), held)


def test_a_hold_interrupted_by_a_crash_is_recovered(db, library, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(("001", "002")))
    journal = Journal(db)
    held = tmp_path / "held"
    plan = journal.plan_holding("t", [str(p) for p in sorted(sdir.iterdir())], str(library), str(held))
    # Simulate a crash right after the first rename: the step is 'intent', the file already moved.
    step = plan.steps[0]
    journal._set_plan(plan.id, "applying")
    journal._set_step(step, "intent", created_dirs=[])
    os.makedirs(os.path.dirname(step.dst))
    os.rename(step.src, step.dst)
    with db.connect() as con:
        con.execute("UPDATE journal_plans SET pid = 999999999 WHERE id = ?", (plan.id,))
    (recovered,) = journal.recover()
    assert recovered.status == "interrupted" and [s.state for s in recovered.steps] == ["done", "planned"]
    assert journal.apply(plan.id).status == "applied" and names(sdir) == []
    assert journal.undo(plan.id).status == "undone" and len(names(sdir)) == 2


def test_a_root_busy_with_another_writer_waits_for_the_next_pass(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    other = RootLock(str(library), host="another-host", pid=4242)
    other.acquire()
    try:
        upgrades.after_filing(db, ledger, now=NOW)
        (batch,) = batches(db)
        assert batch.status == "pending" and "busy" in batch.error and len(names(sdir)) == 6
    finally:
        other.release()
    upgrades.after_filing(db, ledger, now=NOW)
    assert ReplacementStore(db).get(batch.id).status == "held" and len(names(sdir)) == 3


def test_a_file_changed_since_it_was_listed_is_left_in_the_library(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    upgrades.set_mode(db, upgrades.MODE_DELETE)              # list first, act later
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    (sdir / f"{SERIES} c002.cbz").write_bytes(data("changed", 7000))
    upgrades.set_mode(db, upgrades.MODE_HOLDING)
    upgrades.after_filing(db, ledger, now=NOW)
    batch = batches(db)[0]
    assert batch.status == "held"
    assert f"{SERIES} c002.cbz" in names(sdir) and f"{SERIES} c001.cbz" not in names(sdir)


def test_the_purge_empties_only_expired_batches_and_never_a_library(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH + ("007", "008")))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",), info_hash="aa" * 20)
    upgrades.after_filing(db, ledger, now=NOW)
    file_volumes(ledger, tmp_path, sid, sdir, ("3",), info_hash="bb" * 20)
    upgrades.after_filing(db, ledger, now=NOW + timedelta(days=20))
    first, second = batches(db)
    assert first.status == second.status == "held"
    library_before = names(sdir)
    purged = upgrades.purge_expired(db, now=NOW + timedelta(days=31))
    assert purged == [first.id]
    assert ReplacementStore(db).get(first.id).status == "purged" and not os.path.exists(first.holding_dir)
    assert ReplacementStore(db).get(second.id).status == "held" and len(names(holding)) == 2
    assert names(sdir) == library_before
    assert upgrades.purge_expired(db, now=NOW + timedelta(days=31)) == []


def test_a_tampered_record_cannot_make_the_purge_delete_library_files(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    plan = Journal(db).get_plan(batch.plan_id)
    victim = sdir / f"{SERIES} c004.cbz"
    # A step pointing at a library file: outside the batch's holding folder -> left alone.
    with db.connect() as con:
        con.execute("UPDATE journal_steps SET dst = ? WHERE id = ?", (str(victim), plan.steps[0].id))
    after = upgrades.purge_batch(db, batch.id)
    assert victim.exists() and after.status == "purged" and "left in the holding folder" in after.error
    # The batch's holding folder itself pointed into the library: refused as a whole.
    with db.connect() as con:
        con.execute("UPDATE replacements SET status = 'held', holding_dir = ? WHERE id = ?", (str(sdir), batch.id))
    after = upgrades.purge_batch(db, batch.id)
    assert after.status == "held" and "overlaps the library root" in after.error and len(names(sdir)) == 3


def test_delete_mode_waits_for_the_confirmation_then_deletes_every_listed_file(db, ledger, library, holding, known,
                                                                               tmp_path, caplog):
    sid, sdir = make_series(db, library, chapter_files(CH))
    upgrades.set_mode(db, upgrades.MODE_DELETE)
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    report = upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    assert batch.status == "pending" and report.pending == [batch.id] and len(names(sdir)) == 6
    assert "waiting for you" in report.summary()
    listed = [f.path for f in batch.files]
    # Not the full list -> nothing deleted.
    out = upgrades.delete_confirmed(db, batch.id, listed[:2])
    assert not any(o.deleted for o in out) and len(names(sdir)) == 6
    with caplog.at_level(logging.INFO):
        out = upgrades.delete_confirmed(db, batch.id, listed)
    assert all(o.deleted for o in out) and names(sdir) == [f"{SERIES} c004.cbz", f"{SERIES} c005.cbz",
                                                         f"{SERIES} v01 (Digital).cbz"]
    assert ReplacementStore(db).get(batch.id).status == "deleted" and "confirmed by the owner" in caplog.text
    assert not (library / ".mangalist.lock").exists()
    with pytest.raises(ReplacementConflict):
        upgrades.delete_confirmed(db, batch.id, listed)


def test_a_confirmed_delete_still_checks_every_guard(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    upgrades.set_mode(db, upgrades.MODE_DELETE)
    rec = file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    listed = [f.path for f in batch.files]
    # A changed file is refused on its own; the others go.
    (sdir / f"{SERIES} c003.cbz").write_bytes(data("changed", 6000))
    out = {os.path.basename(o.path): o for o in upgrades.delete_confirmed(db, batch.id, listed)}
    assert not out[f"{SERIES} c003.cbz"].deleted and "changed" in out[f"{SERIES} c003.cbz"].reason
    assert out[f"{SERIES} c001.cbz"].deleted
    assert "1 not deleted" in ReplacementStore(db).get(batch.id).error


def test_a_confirmed_delete_is_refused_when_the_volume_is_gone_or_the_root_busy(db, ledger, library, holding, known,
                                                                                tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    upgrades.set_mode(db, upgrades.MODE_DELETE)
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    listed = [f.path for f in batch.files]
    other = RootLock(str(library), host="another-host", pid=4242)
    other.acquire()
    try:
        out = upgrades.delete_confirmed(db, batch.id, listed)
    finally:
        other.release()
    assert not any(o.deleted for o in out) and "busy" in out[0].reason
    os.remove(sdir / f"{SERIES} v01 (Digital).cbz")
    out = upgrades.delete_confirmed(db, batch.id, listed)
    assert not any(o.deleted for o in out) and "no longer in the library" in out[0].reason
    assert len(names(sdir)) == 5 and ReplacementStore(db).get(batch.id).status == "pending"


def test_keep_answers_the_question_for_good(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    upgrades.set_mode(db, upgrades.MODE_DELETE)
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    assert upgrades.keep_batch(db, batch.id).status == "declined"
    upgrades.set_mode(db, upgrades.MODE_HOLDING)
    upgrades.after_filing(db, ledger, now=NOW)
    assert len(names(sdir)) == 6 and ReplacementStore(db).get(batch.id).status == "declined"


def test_the_headless_job_records_pending_and_never_deletes(db, ledger, library, holding, known, tmp_path):
    from mangalist.downloads.contracts import QbtConnection
    from mangalist.headless.downloads_job import make_downloads_job
    from mangalist.headless.jobs import JobContext

    from ..downloads.fakes import FakeQbt, candidate

    sid, sdir = make_series(db, library, chapter_files(CH))
    upgrades.set_mode(db, upgrades.MODE_DELETE)
    ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", "not-a-real-password"))
    qbt = FakeQbt(tmp_path / "torrents")
    qbt.save_root.mkdir()
    ledger.create(sid, candidate(title="Series U v01"), ["1"], str(sdir))
    qbt.put(candidate().info_hash, "pack", {f"{SERIES} v01.cbz": data("volume 1", 9000)})
    job = make_downloads_job(open_ledger=lambda: ledger, client_factory=lambda conn: qbt, after=None)
    result = job(JobContext())
    assert result.status == "ok" and "1 replacement(s) waiting for you" in result.message
    (batch,) = batches(db)
    assert batch.status == "pending" and len(names(sdir)) == 6
    # Holding mode: the job moves them on its own (reversible).
    upgrades.set_mode(db, upgrades.MODE_HOLDING)
    result = job(JobContext())
    assert "3 replaced chapter file(s) moved" in result.message and len(names(sdir)) == 3


def test_the_resolver_supplies_mangapixer_volume_list(db, ledger, library, holding, tmp_path):
    """No stand-in: MangaPixer's export item (made up) for the series, through the real resolver."""
    from mangalist.store.mangapixer import MangaPixerCache, Mapping

    from ..services.mangapixer.conftest import folder

    sid, sdir = make_series(db, library, chapter_files(CH))
    cache = MangaPixerCache(db)

    class Lib:
        id, display_name, kind = "lib0", "Manga", "manga"
        folder_count = item_count = last_scan_at = None

    item = folder("n01", [SERIES])
    item["volumes"]["items"] = [{"volume": "1", "chapters": {"from": "1", "to": "3"}}, {"volume": "2", "chapters": None}]
    cache.save_libraries([Lib()])
    cache.apply_page("lib0", [], [item])
    cache.save_mapping(Mapping(root_id=db.list_roots()[0].id, library_id="lib0"))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    assert batch.status == "held" and [f.chapters for f in batch.files] == ["1", "2", "3"]
    # Needs review in MangaPixer: never a guess.
    item = folder("n01", [SERIES], state="NeedsReview")
    cache.apply_page("lib0", [], [item])
    volumes, why = upgrades._knowledge_volumes(db, db.get_series(db.list_roots()[0].id, SERIES))
    assert volumes is None and "NeedsReview" in why


def test_a_holding_folder_on_another_filesystem_fails_visibly_and_can_be_retried(db, ledger, library, holding, known,
                                                                                 tmp_path, monkeypatch):
    import errno

    from mangalist.store import journal as journal_module

    sid, sdir = make_series(db, library, chapter_files(CH))
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    real = journal_module.rename_noreplace

    def exdev(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link", src, None, dst)

    monkeypatch.setattr(journal_module, "rename_noreplace", exdev)
    report = upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    assert batch.status == "failed" and "another filesystem" in batch.error and report.failed
    assert len(names(sdir)) == 6                                        # nothing copied, nothing deleted
    service = upgrades.ReplacedChapters(db)
    assert [b.id for b in service.open_batches()] == [batch.id]         # the owner sees it
    monkeypatch.setattr(journal_module, "rename_noreplace", real)
    assert service.retry(batch.id).status == "held" and len(names(sdir)) == 3


def test_a_root_no_longer_configured_is_never_written(db, ledger, library, holding, known, tmp_path):
    sid, sdir = make_series(db, library, chapter_files(CH))
    upgrades.set_mode(db, upgrades.MODE_DELETE)
    file_volumes(ledger, tmp_path, sid, sdir, ("1",))
    upgrades.after_filing(db, ledger, now=NOW)
    (batch,) = batches(db)
    with db.connect() as con:
        con.execute("UPDATE replacements SET root_path = ? WHERE id = ?", (str(tmp_path / "gone"), batch.id))
    out = upgrades.delete_confirmed(db, batch.id, [f.path for f in batch.files])
    assert not any(o.deleted for o in out) and "no longer configured" in out[0].reason
    upgrades.set_mode(db, upgrades.MODE_HOLDING)
    after = upgrades.hold_batch(db, batch.id)
    assert after.status == "failed" and "no longer configured" in after.error and len(names(sdir)) == 6
    assert upgrades.keep_batch(db, batch.id).status == "declined"
