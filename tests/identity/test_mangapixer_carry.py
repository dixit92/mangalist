"""(i) MangaPixer's carriedFrom re-attaches a missing MangaList series (optional layer; authoritative, never over
the target's own data, missing rows kept so a later sync or scan can still re-attach). Local fake MangaPixer."""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import pytest

from mangalist import mu_cache
from mangalist.identity.carry import missing_series
from mangalist.services.mangapixer import sync_all
from mangalist.store.mangapixer import MangaPixerCache
from tests.services.mangapixer.conftest import TOKEN, FakeMangaPixer, _handler, folder

from .conftest import make_archive, scan


@pytest.fixture
def fake():
    mp = FakeMangaPixer()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(mp))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    mp.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield mp
    finally:
        server.shutdown()
        server.server_close()


def _setup(db, library, fake):
    for i in range(3):
        make_archive(library / "Old Title" / f"Old Title v{i + 1:02}.cbz", size=2000 + i)
    make_archive(library / "Stable Series" / "Stable Series v01.cbz", size=3000)
    root = db.add_root(str(library))
    first = scan(db)                                     # no backfill: MangaList alone cannot recognise it
    old_folder = next(e.folder for e in first.entries if e.folder.name == "Old Title")
    mu_cache.save_entry(old_folder, 31, "Linked", "", None, mu_confirmed=True)
    db.set_series_kind(root.id, "Old Title", "chapters")
    old_id = db.get_series(root.id, "Old Title").id
    fake.items["lib0manga"] = [folder("n0001", ["Old Title"], updated="2026-10-01T00:00:00.000Z"),
                               folder("n0003", ["Stable Series"], updated="2026-10-01T00:00:00.000Z")]
    cache = MangaPixerCache(db)
    cache.set_connection(base_url=fake.url, token=TOKEN)
    assert sync_all(cache).status == "ok"
    assert cache.mapping(root.id).library_id == "lib0manga"
    return root, cache, old_folder, old_id


def _carry_in_mangapixer(fake):
    fake.items["lib0manga"] = [folder("n0003", ["Stable Series"], updated="2026-10-01T00:00:00.000Z"),
                               folder("n0005", ["New Title"], carried_from="n0001", updated="2026-10-05T00:00:00.000Z")]


def test_i_carried_from_reattaches_a_missing_series(db, library, fake):
    root, cache, old_folder, old_id = _setup(db, library, fake)
    (library / "Old Title").rename(library / "New Title")
    assert scan(db).renamed == []                                    # unsigned: MangaList cannot decide alone
    assert [m.id for m in missing_series(db)] == [old_id]           # kept missing: a later sync can re-attach
    _carry_in_mangapixer(fake)
    result = sync_all(cache)
    new_folder = old_folder.with_name("New Title")
    assert result.status == "ok" and result.renamed == [(str(old_folder), str(new_folder))]
    row = db.get_series(root.id, "New Title")
    assert row.id == old_id and row.kind_hint == "chapters" and row.mu_id == 31 and row.mu_confirmed
    assert mu_cache.load_entry(new_folder)["mu_id"] == 31
    assert missing_series(db) == []
    with db.connect() as con:
        (r,) = con.execute("SELECT outcome FROM mangapixer_carries").fetchall()
    assert r["outcome"] == "carried"
    assert result.summary()["series_carried"] == 1


def test_i_a_sync_before_mangalists_scan_waits_for_the_scan(db, library, fake):
    root, cache, old_folder, old_id = _setup(db, library, fake)
    (library / "Old Title").rename(library / "New Title")
    _carry_in_mangapixer(fake)
    assert sync_all(cache).renamed == []                             # MangaList still sees the old folder
    with db.connect() as con:
        assert con.execute("SELECT outcome FROM mangapixer_carries").fetchone()["outcome"] is None
    result = scan(db)                                                # now it sees the rename
    new_folder = old_folder.with_name("New Title")
    assert (old_folder, new_folder) in result.renamed
    assert db.get_series(root.id, "New Title").id == old_id


def test_i_never_over_the_targets_own_link(db, library, fake):
    root, cache, old_folder, old_id = _setup(db, library, fake)
    (library / "Old Title").rename(library / "New Title")
    scan(db)
    new_folder = old_folder.with_name("New Title")
    mu_cache.save_entry(new_folder, 99, "Own", "", None, mu_confirmed=False)
    _carry_in_mangapixer(fake)
    result = sync_all(cache)
    assert mu_cache.load_entry(new_folder)["mu_id"] == 99
    assert db.get_series(root.id, "Old Title").status == "missing"
    assert db.get_series(root.id, "New Title").kind_hint == "chapters"   # what T lacked moved
    assert [c.kept for c in result.carried] == [["link"]]


def test_a_pair_for_a_folder_mangalist_never_had_is_settled(db, library, fake):
    root, cache, old_folder, old_id = _setup(db, library, fake)
    fake.items["lib0manga"].append(folder("n0002", ["Not In MangaList"], updated="2026-10-02T00:00:00.000Z"))
    sync_all(cache, force_full=True)
    fake.items["lib0manga"] = [folder("n0001", ["Old Title"], updated="2026-10-01T00:00:00.000Z"),
                               folder("n0003", ["Stable Series"], updated="2026-10-01T00:00:00.000Z"),
                               folder("n0007", ["Elsewhere"], carried_from="n0002", updated="2026-10-05T00:00:00.000Z")]
    result = sync_all(cache)
    assert result.carried == []
    with db.connect() as con:
        rows = {r["old_node_id"]: (r["new_node_id"], r["outcome"], r["old_trail"]) for r in
                con.execute("SELECT * FROM mangapixer_carries")}
    assert rows == {"n0002": ("n0007", "nothing", '["Not In MangaList"]')}
    assert db.get_series(root.id, "Old Title").status == "present"


def test_pending_pairs_expire_after_the_move_window(db, library, fake):
    from mangalist.identity.mangapixer import apply_pending, record_carries

    root, cache, old_folder, old_id = _setup(db, library, fake)
    record_carries(db, "lib0manga", [("n0001", "n0009", ["Old Title"], ["Gone"])])
    with db.connect() as con:
        con.execute("UPDATE mangapixer_carries SET seen_at = '2000-01-01T00:00:00+00:00'")
    assert apply_pending(db) == []
    with db.connect() as con:
        assert con.execute("SELECT outcome FROM mangapixer_carries").fetchone()["outcome"] == "expired"
