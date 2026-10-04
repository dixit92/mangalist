"""(l) The headless rescan records the scan like the GUI (phase-1 open issue 2): series rows, units with the kind
answers applied, archive rows, move detection, series carry-over, then the signature backfill."""

from __future__ import annotations

from mangalist import mu_cache
from mangalist.headless.jobs import JobContext, StoreRootsProvider, make_rescan

from .conftest import make_archive


def _run(**kw):
    return make_rescan(StoreRootsProvider(env={}), backfill_delay=0, **kw)(JobContext())


def test_l_headless_rescan_records_series_units_archives_and_signs(db, library):
    make_archive(library / "Series N" / "01.cbz", size=2001)
    make_archive(library / "Series N" / "02.cbz", size=2002)
    make_archive(library / "Series B" / "Series B v01.cbz", size=2003)
    root = db.add_root(str(library))
    res = _run()
    assert res.status == "ok", res
    (summary,) = res.extra["roots"]
    assert summary["recorded"] and summary["series"] == 2
    ident = res.extra["identity"]
    assert ident["archives"] == 3 and ident["new"] == 3
    assert ident["signatures"]["signed"] == 3 and ident["signatures"]["remaining"] == 0
    n = db.get_series(root.id, "Series N")
    assert n.status == "present" and [u.kind for u in db.list_units(n.id)] == ["unknown", "unknown"]
    assert all(a.signature for a in db.list_archives(root.id))

    db.set_series_kind(root.id, "Series N", "volumes")              # the owner answered in the GUI
    assert _run().status == "ok"
    assert [u.kind for u in db.list_units(n.id)] == ["volume", "volume"]   # the answer is applied headless too


def test_l_headless_rescan_carries_a_renamed_series(db, library):
    for i in range(3):
        make_archive(library / "Old Title" / f"Old Title v{i + 1:02}.cbz", size=2000 + i)
    root = db.add_root(str(library))
    _run()
    folder = library.resolve() / "Old Title"
    mu_cache.save_entry(folder, 12, "Linked", "", None, mu_confirmed=True)
    old_id = db.get_series(root.id, "Old Title").id
    (library / "Old Title").rename(library / "New Title")
    res = _run()
    assert res.extra["identity"]["carried"] == 1 and res.extra["identity"]["moves"] == 3
    row = db.get_series(root.id, "New Title")
    assert row.id == old_id and row.mu_id == 12
    assert mu_cache.load_entry(folder.with_name("New Title"))["mu_id"] == 12


def test_plain_paths_are_only_scanned(db, library, tmp_path):
    make_archive(library / "Series A" / "a.cbz")
    res = make_rescan(StoreRootsProvider(env={"MANGALIST_ROOTS": str(library)}))(JobContext())
    assert res.status == "ok" and "identity" not in res.extra
    assert res.extra["roots"][0]["recorded"] is False
    assert db.list_archives() == []


def test_backfill_can_be_left_out(db, library):
    make_archive(library / "Series A" / "a.cbz")
    db.add_root(str(library))
    res = _run(backfill=False)
    assert "signatures" not in res.extra["identity"]
    assert db.unsigned_count() == 1
