"""Schema versioning: created at the current version, additive, a newer database opened as is."""

from __future__ import annotations

import re
import sqlite3

from mangalist import paths, store
from mangalist.store import schema


def _tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_fresh_database_has_every_table_at_the_current_version(db):
    assert db.schema_version() == schema.SCHEMA_VERSION
    assert {"meta", "settings", "roots", "exclusions", "series", "units", "links_cache", "ledger",
            "journal_plans", "journal_steps"} <= _tables(paths.db_file())
    assert paths.db_file().name == "mangalist.db"


def test_reopening_runs_no_migration_twice(db):
    db.add_root(str(paths.data_dir().parent / "lib"), "Lib")
    store.reset_stores()
    again = store.get_store()
    assert again.schema_version() == schema.SCHEMA_VERSION
    assert [r.name for r in again.list_roots()] == ["Lib"]


def test_a_newer_database_is_used_without_downgrade(tmp_path):
    path = tmp_path / "newer.db"
    st = store.Store(path, import_legacy=False)
    st.ensure_initialized()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE roots ADD COLUMN from_the_future TEXT DEFAULT 'x'")
    con.execute(f"PRAGMA user_version={schema.SCHEMA_VERSION + 5}")
    con.commit()
    con.close()
    newer = store.Store(path, import_legacy=False)
    newer.add_root(str(tmp_path / "lib"))
    assert newer.schema_version() == schema.SCHEMA_VERSION + 5
    assert len(newer.list_roots()) == 1


def test_migrations_are_append_only_and_numbered():
    versions = [v for v, _ in schema.MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))
    for _, script in schema.MIGRATIONS:
        sql = re.sub(r"--[^\n]*", "", script).upper()
        assert not re.search(r"\bDROP\s+(TABLE|COLUMN|INDEX)\b", sql)
        assert not re.search(r"\bRENAME\s+(TO|COLUMN)\b", sql)
        assert not re.search(r"\bCHECK\s*\(", sql)


def test_nothing_is_created_until_used(tmp_path):
    st = store.Store(tmp_path / "later" / "x.db")
    assert not (tmp_path / "later").exists()
    st.list_roots()
    assert (tmp_path / "later" / "x.db").is_file()


def test_ledger_is_schema_only_but_writable(db):
    with db.connect() as con:
        con.execute("INSERT INTO ledger (created_at, updated_at, tool, request) VALUES (?,?,?,?)",
                    (store.utcnow(), store.utcnow(), "suwayomi", '{"chapters": ["12"]}'))
        assert con.execute("SELECT status FROM ledger").fetchone()[0] == "queued"
