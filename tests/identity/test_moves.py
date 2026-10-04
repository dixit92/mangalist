"""Archive move detection (port of DetectMovesAsync + CopiedElsewhereAsync, MovePairing): one-to-one by
signature only; duplicates and copies are ambiguous; size alone never pairs (cf. MangaPixer's
ScanMoveDetectionTests)."""

from __future__ import annotations

from datetime import datetime, timezone

from mangalist.identity import moves
from mangalist.identity.backfill import backfill_signatures
from mangalist.scanner import record_library_scan, scan_library

from .conftest import make_archive, record_with, scan, sign


def test_e_an_archive_renamed_inside_its_folder_keeps_its_row(db, library):
    old = make_archive(library / "Series A" / "ch01.cbz", seed="s1", size=300_000)
    make_archive(library / "Series A" / "ch02.cbz", seed="s2", size=300_001)
    root = db.add_root(str(library))
    scan(db)
    sign(db)
    before = db.archive_at(root.id, "Series A/ch01.cbz")
    old.rename(library / "Series A" / "Series A - Chapter 1.cbz")
    rep = scan(db).identity
    after = db.archive_at(root.id, "Series A/Series A - Chapter 1.cbz")
    assert after.id == before.id and after.signature == before.signature and after.status == "present"
    assert db.archive_at(root.id, "Series A/ch01.cbz") is None
    assert [(m.from_path, m.to_path, m.how) for m in rep.moves] == [
        ("Series A/ch01.cbz", "Series A/Series A - Chapter 1.cbz", "scan")]
    assert rep.archives_new == 0 and rep.carries == []              # same series: nothing to carry
    assert rep.hashed == 1 and rep.bytes_read == 128 * 1024


def test_same_size_different_content_is_not_a_move(db, library):
    old = make_archive(library / "Series A" / "ch01.cbz", seed="one", size=300_000)
    root = db.add_root(str(library))
    scan(db)
    sign(db)
    old_id = db.archive_at(root.id, "Series A/ch01.cbz").id
    old.unlink()
    make_archive(library / "Series B" / "other.cbz", seed="two", size=300_000)    # identical size, other bytes
    rep = scan(db).identity
    assert rep.moves == [] and rep.hashed == 1
    assert db.get_archive(old_id).status == "missing"
    assert db.archive_at(root.id, "Series B/other.cbz").id != old_id


def test_an_archive_without_a_signature_never_counts_as_a_move(db, library):
    old = make_archive(library / "Series A" / "ch01.cbz")
    root = db.add_root(str(library))
    scan(db)                                                         # no backfill: no signature
    old_id = db.archive_at(root.id, "Series A/ch01.cbz").id
    old.rename(library / "Series A" / "renamed.cbz")
    rep = scan(db).identity
    assert rep.moves == [] and rep.hashed == 0 and rep.archives_new == 1
    assert db.get_archive(old_id).status == "missing"


def test_f_identical_copies_are_ambiguous_but_a_single_moved_copy_is_not(db, library):
    a = make_archive(library / "Series A" / "a.cbz", seed="same", size=200_000)
    b = make_archive(library / "Series A" / "b.cbz", seed="same", size=200_000)   # byte-identical to a.cbz
    root = db.add_root(str(library))
    scan(db)
    sign(db)
    ids = {db.archive_at(root.id, "Series A/a.cbz").id, db.archive_at(root.id, "Series A/b.cbz").id}
    (library / "Series B").mkdir()
    a.rename(library / "Series B" / "a.cbz")
    b.rename(library / "Series B" / "b.cbz")
    rep = scan(db).identity
    assert rep.moves == [] and rep.ambiguous >= 1 and rep.archives_new == 2
    assert {db.get_archive(i).status for i in ids} == {"missing"}

    # A clean 1:1 case once the two old rows aged out of the move window.
    with db.connect() as con:
        con.execute("UPDATE archives SET missing_since = '2000-01-01T00:00:00+00:00' WHERE id IN (?, ?)", tuple(ids))
    sign(db)
    b2 = db.archive_at(root.id, "Series B/b.cbz")
    (library / "Series B" / "b.cbz").rename(library / "Series B" / "b-final.cbz")
    rep = scan(db).identity
    assert [(m.archive_id, m.to_path) for m in rep.moves] == [(b2.id, "Series B/b-final.cbz")]
    assert {db.get_archive(i).status for i in ids} == {"missing"}


def test_f_a_live_copy_that_appeared_after_the_old_one_was_last_seen_makes_it_ambiguous(db, library, monkeypatch):
    """CopiedElsewhereAsync: a second live copy that appeared after the old archive was last seen."""
    a = make_archive(library / "Series A" / "a.cbz", seed="copy", size=150_000)
    root = db.add_root(str(library))
    scan(db)
    sign(db)
    old_id = db.archive_at(root.id, "Series A/a.cbz").id
    data = a.read_bytes()
    a.unlink()
    scan(db)                                                          # a.cbz missing (a tombstone now)
    (library / "Series C").mkdir()
    (library / "Series C" / "copy.cbz").write_bytes(data)             # one copy appears, still being written
    monkeypatch.setattr(moves, "_default_signer", lambda path, size, mtime: None)
    rep = scan(db).identity
    assert rep.moves == [] and rep.archives_new == 1
    monkeypatch.undo()
    backfill_signatures(db, per_file_delay=0, follow_up=False)        # signed, not paired yet
    (library / "Series D").mkdir()
    (library / "Series D" / "another.cbz").write_bytes(data)          # ... and another copy
    rep = scan(db).identity
    assert rep.moves == [] and rep.ambiguous >= 1
    assert db.get_archive(old_id).status == "missing"                 # copies stay separate archives
    assert db.archive_at(root.id, "Series D/another.cbz").signature == db.get_archive(old_id).signature


def test_a_file_still_being_written_never_matches(db, library):
    a = make_archive(library / "Series A" / "ch01.cbz", size=300_000)
    root = db.add_root(str(library))
    scan(db)
    sign(db)
    old_id = db.archive_at(root.id, "Series A/ch01.cbz").id
    a.rename(library / "Series A" / "copy.cbz")
    calls = []

    def growing(path, size, mtime_ns):          # the stamp changed while hashing (a copy in progress)
        calls.append(path)
        return None

    rep = record_with(db, signer=growing)
    assert calls and rep.moves == [] and rep.archives_new == 1
    assert db.get_archive(old_id).status == "missing"


def test_the_move_window_is_configurable(db, library):
    a = make_archive(library / "Series A" / "ch01.cbz", size=300_000)
    root = db.add_root(str(library))
    scan(db)
    sign(db)
    old_id = db.archive_at(root.id, "Series A/ch01.cbz").id
    data = a.read_bytes()
    a.unlink()
    scan(db)
    with db.connect() as con:                     # missing for 10 days
        con.execute("UPDATE archives SET missing_since = '2026-01-01T00:00:00+00:00' WHERE id = ?", (old_id,))
    db.set_setting("move_window_days", 5)
    assert db.move_window_days() == 5
    (library / "Series A" / "back.cbz").write_bytes(data)
    now = datetime(2026, 1, 11, tzinfo=timezone.utc)
    rep = record_with(db, now=now)
    assert rep.moves == [] and rep.hashed == 0                       # outside the window: not even read
    (library / "Series A" / "back.cbz").rename(library / "Series A" / "back2.cbz")
    db.set_setting("move_window_days", 30)
    with db.connect() as con:
        con.execute("DELETE FROM archives WHERE rel_path = 'Series A/back.cbz'")
    rep = record_with(db, now=now)
    assert [m.archive_id for m in rep.moves] == [old_id]


def test_after_the_fact_a_root_that_was_offline_pairs_once_it_is_back(db, library, library2):
    """The destination is scanned first (MovePairingService): the source root was offline then."""
    a = make_archive(library / "Series A" / "ch01.cbz", size=250_000)
    root = db.add_root(str(library))
    root2 = db.add_root(str(library2))
    scan(db)
    sign(db)
    old_id = db.archive_at(root.id, "Series A/ch01.cbz").id
    hidden = library.with_name("offline")
    library.rename(hidden)                                           # source share offline ...
    (library2 / "Series A").mkdir()
    (library2 / "Series A" / "ch01.cbz").write_bytes(a.read_bytes() if a.exists() else
                                                     (hidden / "Series A" / "ch01.cbz").read_bytes())
    scan(db)                                                         # ... destination seen as a new archive
    new_row = db.archive_at(root2.id, "Series A/ch01.cbz")
    assert new_row is not None and new_row.id != old_id
    (hidden / "Series A" / "ch01.cbz").unlink()
    hidden.rename(library)                                           # back online, the file gone from it
    rep = scan(db).identity
    paired = [m for m in rep.moves if m.how == "pairing"]
    assert [(m.archive_id, m.to_root_id, m.to_path) for m in paired] == [(old_id, root2.id, "Series A/ch01.cbz")]
    assert db.get_archive(new_row.id) is None                        # folded into the old row
    moved = db.get_archive(old_id)
    assert moved.status == "present" and moved.root_id == root2.id


def test_a_copy_seen_side_by_side_is_never_paired_after_the_fact(db, library):
    a = make_archive(library / "Series A" / "ch01.cbz", size=250_000)
    root = db.add_root(str(library))
    scan(db)
    sign(db)
    (library / "Series B").mkdir()
    (library / "Series B" / "ch01.cbz").write_bytes(a.read_bytes())   # a copy, both seen by one scan
    scan(db)
    sign(db)
    old_id = db.archive_at(root.id, "Series A/ch01.cbz").id
    a.unlink()                                                        # then the original is deleted
    rep = scan(db).identity
    assert rep.moves == []
    assert db.get_archive(old_id).status == "missing"


def test_hashing_reads_nothing_when_the_pool_is_empty(db, library):
    for i in range(3):
        make_archive(library / "Series A" / f"a{i}.cbz", size=1000 + i)
    db.add_root(str(library))
    rep = scan(db).identity
    assert rep.hashed == 0 and rep.bytes_read == 0


def test_record_library_scan_reports_renamed_series(db, library):
    for i in range(3):
        make_archive(library / "Old" / f"v{i}.cbz", size=1000 + i)
    db.add_root(str(library))
    scan(db)
    sign(db)
    (library / "Old").rename(library / "New")
    result = scan_library(db.list_roots(), db=db)
    renamed = record_library_scan(db, result)
    assert renamed == [(library.resolve() / "Old", library.resolve() / "New")]
