"""The MangaPixer tables (migration 2), the stored connection, and the headless job."""

from __future__ import annotations

import logging
import sqlite3

from mangalist import config, paths
from mangalist.headless.jobs import JobContext
from mangalist.headless.mangapixer_job import JOB_NAME, make_mangapixer_sync
from mangalist.store import schema

from .conftest import TOKEN, add_root_with_series, folder


def _tables():
    con = sqlite3.connect(paths.db_file())
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_migration_adds_the_tables(db):
    assert db.schema_version() == schema.SCHEMA_VERSION >= 2
    assert {"mangapixer_connection", "mangapixer_libraries", "mangapixer_items", "mangapixer_sync",
            "mangapixer_mappings"} <= _tables()
    assert dict(schema.MIGRATIONS)[2].count("CREATE TABLE") == 5


def test_a_version_1_database_is_upgraded(tmp_path):
    from mangalist import store

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(dict(schema.MIGRATIONS)[1] + "\nPRAGMA user_version=1;")
    con.execute("INSERT INTO settings (key, value) VALUES ('last_root', '\"x\"')")
    con.commit()
    con.close()
    st = store.Store(path, import_legacy=False)
    assert st.schema_version() == schema.SCHEMA_VERSION
    assert st.get_setting("last_root") == "x"


def test_token_is_stored_apart_from_the_settings(cache):
    conn = cache.set_connection(base_url="http://nas:8080", token=f"  {TOKEN}  ")
    assert conn.has_token and cache.token() == TOKEN
    assert TOKEN not in repr(conn)
    assert all(TOKEN not in str(v) for v in cache.store.all_settings().values())
    assert all(TOKEN not in str(v) for v in config.load().values())
    cache.set_connection(base_url="http://nas:8081")             # token kept when not given
    assert cache.token() == TOKEN
    cache.set_connection(token=None)
    assert cache.token() is None and not cache.connection().has_token


def test_tls_settings(cache):
    assert cache.connection().verify is True
    cache.set_connection(base_url="https://mp", verify_tls=False)
    assert cache.connection().verify is False
    cache.set_connection(ca_file="/certs/mp.pem")
    assert cache.connection().verify == "/certs/mp.pem"
    cache.set_connection(ca_file=None, verify_tls=True)
    assert cache.connection().verify is True


def test_mapping_rows_go_with_their_root(cache, db, tmp_path):
    from mangalist.store.mangapixer import Mapping

    root = add_root_with_series(db, tmp_path, "M", [])
    cache.save_mapping(Mapping(root.id, "lib", ["A"], manual=True))
    assert cache.mapping(root.id).prefix == ["A"]
    db.remove_root(root.id)
    assert cache.mapping(root.id) is None


def test_job_syncs_and_reports(connected, fake, client_factory, db, tmp_path):
    fake.items["lib0manga"] = [folder("n0001", ["One"]), folder("n0002", ["Two"]),
                               folder("n0003", ["Extra.cbz"], kind="archive")]
    root = add_root_with_series(db, tmp_path, "M", ["One", "Two", "Three"])
    job = make_mangapixer_sync(open_cache=lambda: connected, client_factory=lambda c: client_factory(fake.url))
    res = job(JobContext())
    assert JOB_NAME == "mangapixer-sync"
    assert res.status == "ok", res.message
    assert res.extra["libraries"][0]["items"] == 2 and res.extra["libraries"][0]["archives_dropped"] == 1
    assert res.extra["mappings"][str(root.id)] == {"library": "lib0manga", "prefix": [], "manual": False,
                                                    "matched": 2, "unmatched": 1}


def test_job_skips_without_a_source_and_after_a_401(cache, fake, client_factory, caplog):
    caplog.set_level(logging.DEBUG)
    job = make_mangapixer_sync(open_cache=lambda: cache, client_factory=lambda c: client_factory(fake.url))
    assert job(JobContext()).status == "skipped"
    cache.set_connection(base_url=fake.url, token=TOKEN)
    fake.token = "mpx_other"
    assert job(JobContext()).status == "error"
    assert job(JobContext()).status == "skipped"     # no automatic retry with the refused token
    assert len(fake.requests) == 1
    assert TOKEN not in caplog.text


def test_job_uses_the_stored_connection_by_default(connected, fake):
    fake.items["lib0manga"] = [folder("n0001", ["One"])]
    res = make_mangapixer_sync(open_cache=lambda: connected)(JobContext())
    assert res.status == "ok" and fake.requests[-1]["headers"]["authorization"] == f"Bearer {TOKEN}"
