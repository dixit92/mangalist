"""Archive rows (schema 4): one per archive, upserted by every recorded scan, missing ones kept."""

from __future__ import annotations

import os
import sqlite3

from mangalist import store
from mangalist.store import schema

from .conftest import make_archive, scan, sign


def test_schema_4_adds_the_identity_tables(db):
    assert db.schema_version() == schema.SCHEMA_VERSION >= 4
    with db.connect() as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        cols = {r[1] for r in con.execute("PRAGMA table_info(series)")}
    assert {"archives", "archive_moves", "series_carries", "mangapixer_carries"} <= tables
    assert {"fingerprint", "missing_since", "kind_hint"} <= cols


def test_a_schema_3_database_upgrades_to_4_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    for version, script in schema.MIGRATIONS[:3]:
        con.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={version};\nCOMMIT;")
    con.execute("INSERT INTO roots (id, name, path, created_at, updated_at) VALUES (1, 'M', '/x', 't', 't')")
    con.execute("INSERT INTO series (root_id, rel_path, fingerprint, n_archives, status, first_seen_at, last_seen_at,"
                " kind_hint) VALUES (1, 'Old Series', 'v1:1:ab', 1, 'present', 't', 't', 'volumes')")
    con.commit()
    con.close()
    st = store.Store(path, import_legacy=False)
    assert st.schema_version() == schema.SCHEMA_VERSION
    (row,) = st.list_series(1)
    assert row.rel_path == "Old Series" and row.kind_hint == "volumes" and row.fingerprint == "v1:1:ab"
    assert row.missing_since is None and st.list_archives() == []


def test_scan_upserts_one_row_per_archive_with_size_mtime_and_series(db, library):
    a = make_archive(library / "Series A" / "Series A v01.cbz")
    make_archive(library / "Series A" / "Extras" / "Series A v01 extra.cbz", size=5000)
    make_archive(library / "Series B" / "Series B c001.cbz")
    root = db.add_root(str(library))
    result = scan(db)
    rows = {r.rel_path: r for r in db.list_archives(root.id)}
    assert set(rows) == {"Series A/Series A v01.cbz", "Series A/Extras/Series A v01 extra.cbz",
                         "Series B/Series B c001.cbz"}
    ra = rows["Series A/Series A v01.cbz"]
    assert ra.size == 4000 and ra.mtime_ns == os.stat(a).st_mtime_ns and ra.status == "present"
    assert ra.series_id == db.get_series(root.id, "Series A").id and ra.signature is None
    assert result.identity.archives_new == 3 and result.identity.scan == 1
    # A second scan of the same tree changes nothing but the last seen.
    again = scan(db)
    assert again.identity.archives_new == 0 and again.identity.archives_missing == 0 and again.identity.scan == 2
    assert {r.id for r in db.list_archives(root.id)} == {r.id for r in rows.values()}


def test_vanished_archives_are_kept_missing_and_come_back(db, library):
    a = make_archive(library / "Series A" / "a1.cbz")
    make_archive(library / "Series A" / "a2.cbz")
    root = db.add_root(str(library))
    scan(db)
    data = a.read_bytes()
    a.unlink()
    rep = scan(db).identity
    assert rep.archives_missing == 1
    gone = db.archive_at(root.id, "Series A/a1.cbz")
    assert gone.status == "missing" and gone.missing_since and gone.series_id is not None
    a.write_bytes(data)
    os.utime(a, ns=(gone.mtime_ns, gone.mtime_ns))
    scan(db)
    back = db.archive_at(root.id, "Series A/a1.cbz")
    assert back.id == gone.id and back.status == "present" and back.missing_since is None


def test_units_stay_computed_from_names(db, library):
    make_archive(library / "Series A" / "Series A v01.cbz")
    root = db.add_root(str(library))
    scan(db)
    sid = db.get_series(root.id, "Series A").id
    assert [u.kind for u in db.list_units(sid)] == ["volume"]


def test_nested_series_archives_belong_to_the_subseries(db, library):
    make_archive(library / "Franchise" / "Franchise v01.cbz")
    make_archive(library / "Franchise" / "Side Story" / "Side Story v01.cbz", size=4100)
    root = db.add_root(str(library))
    scan(db)
    parent = db.get_series(root.id, "Franchise").id
    side = db.get_series(root.id, "Franchise/Side Story").id
    rows = {r.rel_path: r.series_id for r in db.list_archives(root.id)}
    assert rows == {"Franchise/Franchise v01.cbz": parent, "Franchise/Side Story/Side Story v01.cbz": side}


def test_an_unreachable_root_keeps_its_archives_present(db, library, library2):
    make_archive(library / "Series A" / "a1.cbz")
    root = db.add_root(str(library))
    db.add_root(str(library2))
    scan(db)
    hidden = library.with_name("Manga-offline")
    library.rename(hidden)                   # the share is offline
    result = scan(db)
    assert result.errors
    assert db.archive_at(root.id, "Series A/a1.cbz").status == "present"
    assert db.get_series(root.id, "Series A").status == "present"
    hidden.rename(library)


def test_a_file_changed_in_place_loses_its_signature_at_the_next_scan(db, library):
    a = make_archive(library / "Series A" / "a1.cbz")
    root = db.add_root(str(library))
    scan(db)
    assert sign(db).signed == 1
    signed = db.archive_at(root.id, "Series A/a1.cbz").signature
    assert signed and signed.startswith("v1:4000:")
    a.write_bytes(b"ComicInfo written into it" + a.read_bytes())       # same path, new content
    rep = scan(db).identity
    row = db.archive_at(root.id, "Series A/a1.cbz")
    assert rep.archives_changed == 1 and row.signature is None and row.size == 4025
