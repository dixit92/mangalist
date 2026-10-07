"""Schema 5: download records on the ledger, the qBittorrent connection (password kept out of settings), the
download settings, and the 4 -> 5 upgrade of a populated database."""

from __future__ import annotations

import logging
import sqlite3

import pytest

from mangalist import store
from mangalist.downloads.contracts import DownloadStatus, NyaaCandidate, QbtConnection
from mangalist.store import schema
from mangalist.store.downloads import (DEFAULT_SAVE_PATH, DownloadError, DownloadLedger, StatusConflict,
                                       normalize_volumes)
from mangalist.store.units import UnitError

SECRET = "pa55-not-a-real-one"
HASH = "ab" * 20


def candidate(info_hash: str = HASH, **kw) -> NyaaCandidate:
    base = dict(title="Series A v02-03 (Digital) (Group)", view_url="https://nyaa.example/view/1",
                torrent_url="https://nyaa.example/download/1.torrent", info_hash=info_hash, size_bytes=1000,
                seeders=5, leechers=0, downloads=10, trusted=False, remake=False,
                published="2026-10-01T00:00:00Z", category="3_1", vol_from="2", vol_to="3", is_pack=True)
    base.update(kw)
    return NyaaCandidate(**base)


@pytest.fixture
def ledger(db, tmp_path):
    root = db.add_root(str(tmp_path / "lib"), "Lib")
    db.record_scan(root.id, tmp_path / "lib", [store.SeriesSeen(f"S{i}", None, 0) for i in (5, 6)])
    return DownloadLedger(db)


def sid(db, n: int) -> int:
    return next(s.id for s in db.list_series() if s.rel_path == f"S{n}")


def test_schema_5_tables_and_columns(db):
    assert db.schema_version() == schema.SCHEMA_VERSION == 5
    with db.connect() as con:
        ledger_cols = {r[1] for r in con.execute("PRAGMA table_info(ledger)")}
        step_cols = {r[1] for r in con.execute("PRAGMA table_info(journal_steps)")}
        conn_cols = {r[1] for r in con.execute("PRAGMA table_info(qbittorrent_connection)")}
    assert {"filed_files", "copied", "plan_id"} <= ledger_cols
    assert {"how", "dst_ident"} <= step_cols
    assert {"base_url", "username", "password", "verify_tls", "updated_at"} <= conn_cols
    assert set(DownloadStatus.ALL) - {"cancelled", "failed"} <= set(schema.LEDGER_STATUS)


def test_a_populated_schema_4_database_upgrades_to_5_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    for version, script in schema.MIGRATIONS[:4]:
        con.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={version};\nCOMMIT;")
    con.execute("INSERT INTO roots (id, name, path, created_at, updated_at) VALUES (1, 'M', '/x', 't', 't')")
    con.execute("INSERT INTO series (id, root_id, rel_path, n_archives, status, first_seen_at, last_seen_at)"
                " VALUES (7, 1, 'Old Series', 1, 'present', 't', 't')")
    con.execute("INSERT INTO units (series_id, rel_path, kind, vol_from, updated_at)"
                " VALUES (7, 'Old Series v01.cbz', 'volume', '1', 't')")
    con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status)"
                " VALUES ('t', 't', 7, '{\"units\": [1]}', 'suwayomi', 'queued')")
    con.execute("INSERT INTO journal_plans (id, created_at, updated_at, reason, status) VALUES (3, 't', 't', 'manual',"
                " 'applied')")
    con.execute("INSERT INTO journal_steps (plan_id, seq, src, dst, state, updated_at) VALUES (3, 0, '/a', '/b',"
                " 'done', 't')")
    con.execute("INSERT INTO settings (key, value) VALUES ('theme', '\"dark\"')")
    con.commit()
    con.close()

    st = store.Store(path, import_legacy=False)
    assert st.schema_version() == 5
    assert [u.vol_from for u in st.list_units(7)] == ["1"]
    assert st.get_setting("theme") == "dark"
    with st.connect() as c:
        old = c.execute("SELECT * FROM ledger").fetchone()
    assert old["tool"] == "suwayomi" and old["status"] == "queued" and old["filed_files"] == "[]"
    assert old["copied"] == 0 and old["plan_id"] is None
    (plan,) = store.Journal(st).list_plans()
    assert plan.steps[0].op == "move" and plan.steps[0].how is None and plan.steps[0].state == "done"
    ledger = DownloadLedger(st)
    assert ledger.active() == [] and ledger.for_series(7) == []     # another tool's row is not a download
    assert ledger.connection() is None and ledger.remove_completed() is True


def test_create_get_and_list(ledger, db):
    rec = ledger.create(sid(ledger.store, 5), candidate(info_hash=HASH.upper()), ["03", "2", "3"], "/lib/Series A")
    assert rec.status == DownloadStatus.SENT and rec.info_hash == HASH
    assert rec.wanted_volumes == ("3", "2") and rec.target_dir == "/lib/Series A"
    assert rec.title.startswith("Series A") and rec.filed_files == () and rec.copied is False
    assert ledger.get(rec.id) == rec and ledger.for_series(sid(ledger.store, 5)) == [rec] and ledger.active() == [rec]
    assert ledger.request(rec.id)["torrent_url"].endswith(".torrent")
    assert ledger.active_for_hash(HASH) == rec
    with db.connect() as con:
        row = con.execute("SELECT tool, external_ref, destination FROM ledger WHERE id = ?", (rec.id,)).fetchone()
    assert tuple(row) == ("qbittorrent", HASH, "/lib/Series A")


def test_create_refusals(ledger):
    ledger.create(sid(ledger.store, 5), candidate(), ["2"], "/lib/S")
    with pytest.raises(DownloadError, match="already being tracked"):
        ledger.create(sid(ledger.store, 6), candidate(), ["2"], "/lib/T")
    with pytest.raises(DownloadError, match="at least one"):
        ledger.create(sid(ledger.store, 5), candidate(info_hash="cd" * 20), [], "/lib/S")
    with pytest.raises(UnitError, match="float"):
        normalize_volumes([2.5])
    with pytest.raises(DownloadError, match="info hash"):
        ledger.create(sid(ledger.store, 5), candidate(info_hash=""), ["2"], "/lib/S")


def test_transitions_are_compare_and_set(ledger):
    rec = ledger.create(sid(ledger.store, 5), candidate(), ["2"], "/lib/S")
    rec = ledger.set_status(rec.id, DownloadStatus.DOWNLOADED, expect=[DownloadStatus.SENT])
    with pytest.raises(StatusConflict):
        ledger.set_status(rec.id, DownloadStatus.DOWNLOADED, expect=[DownloadStatus.SENT])
    rec = ledger.set_status(rec.id, DownloadStatus.FILED, expect=[DownloadStatus.DOWNLOADED],
                            filed_files=["Volumes/v02.cbz"], copied=True)
    assert rec.filed_files == ("Volumes/v02.cbz",) and rec.copied is True
    with pytest.raises(StatusConflict):
        ledger.cancel(rec.id)                                          # a filed record is not cancelled
    ledger.set_status(rec.id, DownloadStatus.REMOVED, expect=[DownloadStatus.FILED])
    assert ledger.active() == [] and ledger.active_for_hash(HASH) is None
    ledger.create(sid(ledger.store, 5), candidate(), ["2"], "/lib/S")                      # the same torrent again is fine now


def test_attach_plan_only_once_and_only_while_downloaded(ledger):
    rec = ledger.create(sid(ledger.store, 5), candidate(), ["2"], "/lib/S")
    with pytest.raises(StatusConflict):
        ledger.attach_plan(rec.id, 1)
    ledger.set_status(rec.id, DownloadStatus.DOWNLOADED, expect=[DownloadStatus.SENT])
    ledger.attach_plan(rec.id, 1)
    assert ledger.plan_id(rec.id) == 1
    with pytest.raises(StatusConflict):
        ledger.attach_plan(rec.id, 2)


def test_the_password_stays_out_of_settings_logs_and_reprs(ledger, db, caplog):
    with caplog.at_level(logging.DEBUG):
        ledger.save_connection(QbtConnection("http://qbt.example:8080", "admin", SECRET, verify_tls=False))
        conn = ledger.connection()
    assert conn == QbtConnection("http://qbt.example:8080", "admin", SECRET, False)
    assert SECRET not in repr(conn) and SECRET not in caplog.text
    assert SECRET not in repr(db.all_settings())
    with db.connect() as con:
        assert all(SECRET not in str(v) for _, v in con.execute("SELECT key, value FROM settings"))
    ledger.forget_connection()
    assert ledger.connection() is None


def test_download_settings(ledger, db):
    assert ledger.save_path() == DEFAULT_SAVE_PATH == "/data/appdata/torrents/mangalist"
    assert ledger.remove_completed() is True
    ledger.set_save_path("/data/appdata/torrents/other")
    ledger.set_remove_completed(False)
    assert db.get_setting("downloads.save_path") == "/data/appdata/torrents/other"
    assert ledger.save_path() == "/data/appdata/torrents/other" and ledger.remove_completed() is False
    ledger.set_save_path("  ")
    assert ledger.save_path() == DEFAULT_SAVE_PATH
