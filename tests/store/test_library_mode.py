"""Schema 8: a library's default automatic-download mode (owner, 2026-10-13: "Can I set Automatic Downloads
[Off/Notify/Auto] per library?") - the migration, ``Root.auto_mode`` and the order a series' mode is resolved in: its own,
else its library's, else the default setting. Made-up libraries and series."""

from __future__ import annotations

import sqlite3

import pytest

from mangalist import store
from mangalist.store import RootError, schema
from mangalist.store.findings import MODE_AUTO, MODE_NOTIFY, MODE_OFF, FindingStore


def _columns(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def test_schema_8_adds_the_column(db):
    assert db.schema_version() == schema.SCHEMA_VERSION >= 8
    with db.connect() as con:
        assert "auto_mode" in _columns(con, "roots")


def test_a_schema_7_database_upgrades_and_keeps_everything_else(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    for version, script in schema.MIGRATIONS[:7]:
        con.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={version};\nCOMMIT;")
    con.execute("INSERT INTO roots (id, name, path, created_at, updated_at) VALUES (1, 'M', '/x', 't', 't')")
    con.execute("INSERT INTO series (id, root_id, rel_path, n_archives, status, first_seen_at, last_seen_at, auto_mode)"
                " VALUES (7, 1, 'Old Series', 1, 'present', 't', 't', 'auto')")
    con.commit()
    tables_before = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    roots_before = _columns(con, "roots")
    con.close()

    st = store.Store(path, import_legacy=False)
    assert st.schema_version() == schema.SCHEMA_VERSION >= 8
    with st.connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        # schema 9 adds the English titles cache, schema 11 the page-hash cache and the dismissed overlaps
        assert tables - {"english_titles", "page_hashes", "overlap_dismissals"} == tables_before
        assert _columns(c, "roots") == roots_before | {"auto_mode"}
        assert c.execute("SELECT auto_mode FROM roots").fetchone()[0] is None      # existing libraries: the default
    root = st.list_roots()[0]
    assert root.auto_mode is None and root.name == "M"
    assert FindingStore(st).modes() == {7: MODE_AUTO}


def test_root_auto_mode_round_trip_and_validation(db, library):
    root = db.add_root(str(library), "Manga", auto_mode=MODE_OFF)
    assert db.get_root(root.id).auto_mode == MODE_OFF
    root.auto_mode = MODE_AUTO
    db.update_root(root)
    assert db.get_root(root.id).auto_mode == MODE_AUTO
    root.auto_mode = "default"                  # the editor's "use the default": stored as NULL
    db.update_root(root)
    assert db.get_root(root.id).auto_mode is None
    root.auto_mode = "sometimes"
    with pytest.raises(RootError, match="automatic downloads"):
        db.update_root(root)
    assert db.get_root(root.id).auto_mode is None


@pytest.fixture
def two_libraries(db, tmp_path):
    """Two libraries with two series each: (store, {name: series id}, (root a, root b))."""
    from mangalist.scanner import record_library_scan, scan_library

    roots = []
    for lib in ("Library One", "Library Two"):
        folder = tmp_path / "libs" / lib
        for name in ("Series A", "Series B"):
            (folder / name).mkdir(parents=True)
            (folder / name / "Ch. 0001.00.cbz").write_bytes(b"x")
        roots.append(db.add_root(str(folder), lib))
    record_library_scan(db, scan_library(db.list_roots(), db=db))
    ids = {f"{r.name}/{n}": db.get_series(r.id, n).id for r in roots for n in ("Series A", "Series B")}
    return db, ids, roots


def test_modes_resolve_own_then_library_then_the_default(two_libraries):
    db, ids, (one, two) = two_libraries
    fs = FindingStore(db)
    a1, b1, a2, b2 = (ids["Library One/Series A"], ids["Library One/Series B"], ids["Library Two/Series A"],
                      ids["Library Two/Series B"])
    before = fs.modes()
    assert set(before.values()) == {MODE_NOTIFY}            # no library sets one: every series as before (the default)
    one.auto_mode = MODE_OFF
    db.update_root(one)
    fs.set_mode([b1], MODE_AUTO)
    assert fs.modes() == {a1: MODE_OFF, b1: MODE_AUTO, a2: MODE_NOTIFY, b2: MODE_NOTIFY}
    assert fs.modes([a1, a2]) == {a1: MODE_OFF, a2: MODE_NOTIFY}
    assert fs.mode(a1) == MODE_OFF and fs.mode(b1) == MODE_AUTO and fs.mode(a2) == MODE_NOTIFY
    assert fs.default_for(a1) == (MODE_OFF, True) and fs.default_for(b1) == (MODE_OFF, True)
    assert fs.default_for(a2) == (MODE_NOTIFY, False)
    assert fs.defaults([a1, a2, 999]) == {a1: (MODE_OFF, True), a2: (MODE_NOTIFY, False)}
    assert fs.default_for(999) == (MODE_NOTIFY, False)          # a series the store does not know: the setting
    fs.set_default_mode(MODE_AUTO)                              # the setting changes only the libraries without one
    assert fs.modes() == {a1: MODE_OFF, b1: MODE_AUTO, a2: MODE_AUTO, b2: MODE_AUTO}
    fs.set_mode([b1], None)
    assert fs.mode(b1) == MODE_OFF                               # back to its library's
