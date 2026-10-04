"""Background signature backfill (port of ContentSignatureBackfill): throttled, resumable, path + size + mtime."""

from __future__ import annotations

import os

from mangalist.identity.backfill import backfill_signatures
from mangalist.identity.signature import compute_file

from .conftest import make_archive, scan, sign


def _tree(db, library, n=5):
    for i in range(n):
        make_archive(library / "Series A" / f"a{i:02}.cbz", size=1000 + i)
    return db.add_root(str(library))


def test_signs_every_present_archive_with_mangapixers_signature(db, library):
    root = _tree(db, library)
    scan(db)
    assert db.unsigned_count() == 5
    res = sign(db)
    assert (res.looked, res.signed, res.skipped, res.remaining) == (5, 5, 0, 0)
    assert res.bytes_read == sum(1000 + i for i in range(5))
    for r in db.list_archives(root.id):
        assert r.signature == compute_file(library / r.rel_path)
    assert sign(db).looked == 0                                   # nothing left: a cheap no-op


def test_throttled_per_file_and_reports_progress(db, library):
    _tree(db, library, 3)
    scan(db)
    slept, seen = [], []
    res = backfill_signatures(db, per_file_delay=0.02, sleep=slept.append, progress=lambda d, t: seen.append((d, t)))
    assert res.signed == 3 and slept == [0.02] * 3
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_resumable_stop_and_limit(db, library):
    _tree(db, library, 5)
    scan(db)
    calls = {"n": 0}

    def stop_after_two():
        calls["n"] += 1
        return calls["n"] > 3          # the pass checks before the batch and before each file

    first = backfill_signatures(db, per_file_delay=0, should_stop=stop_after_two)
    assert first.stopped and first.signed == 2 and first.remaining == 3
    second = backfill_signatures(db, per_file_delay=0, limit=2, batch_size=1)
    assert second.signed == 2 and second.remaining == 1
    assert sign(db).signed == 1 and db.unsigned_count() == 0


def test_k_a_file_changed_in_place_loses_its_signature_and_is_signed_again(db, library):
    root = _tree(db, library, 1)
    scan(db)
    sign(db)
    path = library / "Series A" / "a00.cbz"
    before = db.archive_at(root.id, "Series A/a00.cbz")
    path.write_bytes(path.read_bytes()[:-10] + b"0123456789")     # same size, new tail
    os.utime(path, ns=(before.mtime_ns + 10**9, before.mtime_ns + 10**9))
    scan(db)
    assert db.archive_at(root.id, "Series A/a00.cbz").signature is None
    assert sign(db).signed == 1
    after = db.archive_at(root.id, "Series A/a00.cbz")
    assert after.id == before.id and after.signature == compute_file(path) != before.signature


def test_a_file_changed_since_the_scan_is_left_for_the_next_scan(db, library):
    root = _tree(db, library, 1)
    scan(db)
    path = library / "Series A" / "a00.cbz"
    path.write_bytes(b"still being written")                     # stamp no longer matches the row
    res = sign(db)
    assert res.signed == 0 and res.skipped == 1
    assert db.archive_at(root.id, "Series A/a00.cbz").signature is None


def test_an_unreachable_root_is_skipped_without_reading(db, library):
    _tree(db, library, 2)
    scan(db)
    hidden = library.with_name("offline")
    library.rename(hidden)
    res = sign(db)
    assert res.signed == 0 and res.skipped == 2 and res.bytes_read == 0
    hidden.rename(library)
    assert sign(db).signed == 2


def test_missing_archives_are_not_signed(db, library):
    root = _tree(db, library, 2)
    scan(db)
    (library / "Series A" / "a00.cbz").unlink()
    scan(db)
    assert sign(db).signed == 1
    assert db.archive_at(root.id, "Series A/a00.cbz").signature is None
