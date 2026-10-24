"""MangaList's database backed up together with the extension host's (transition audit D4): a labelled pair, made by
hand or when MangaList starts on a new version (the newest two automatic pairs kept). Temporary files only."""

from __future__ import annotations

import sqlite3

import pytest

from mangalist import store
from mangalist.store import backups


@pytest.fixture
def data(tmp_path):
    store.reset_stores()
    db = store.get_store(tmp_path / "mangalist.db")
    db.set_setting("theme", "dark")
    host = tmp_path / "extension-host" / "host.db"
    host.parent.mkdir()
    con = sqlite3.connect(host)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE host_info (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO host_info VALUES ('instance', 'db-1')")
    con.commit()                                   # left open: committed content still in the WAL is copied too
    yield tmp_path, db
    con.close()


def test_a_labelled_pair_holds_both_databases(data):
    folder, db = data
    written = backups.snapshot(folder, "pre-2026.10.57", db_file=db.path)
    assert [p.name for p in written] == ["mangalist-pre-2026.10.57.db", "extension-host-pre-2026.10.57.db"]
    host = sqlite3.connect(written[1]).execute("SELECT value FROM host_info").fetchone()[0]
    assert host == "db-1"
    assert sqlite3.connect(written[0]).execute("SELECT value FROM settings WHERE key='theme'").fetchone()[0] == '"dark"'
    with pytest.raises(FileExistsError):
        backups.snapshot(folder, "pre-2026.10.57", db_file=db.path)
    with pytest.raises(ValueError):
        backups.snapshot(folder, "../evil", db_file=db.path)


def test_a_new_version_makes_an_automatic_pair_and_keeps_the_newest_two(data):
    from datetime import datetime

    folder, db = data
    assert backups.at_version_change(db, folder, "2026.10.56") == []           # first run: only noted
    assert backups.at_version_change(db, folder, "2026.10.56") == []
    for n, version in enumerate(("2026.10.57", "2026.10.58", "2026.10.59")):
        made = backups.at_version_change(db, folder, version, now=datetime(2026, 10, 24, 12, n))
        assert len(made) == 2
    names = sorted(p.name for p in (folder / "backups").iterdir())
    assert len(names) == 4 and all("2026.10.56-to" not in n for n in names)
    (folder / "backups" / "mangalist-pre-by-hand.db").write_bytes(b"x")
    backups.prune_auto(folder, keep=0)
    assert [p.name for p in (folder / "backups").iterdir()] == ["mangalist-pre-by-hand.db"]


def test_the_command_line(data, monkeypatch, capsys):
    folder, _db = data
    monkeypatch.setenv("MANGALIST_DATA_DIR", str(folder))
    assert backups.main(["by-hand-1"]) == 0
    assert "extension-host-by-hand-1.db" in capsys.readouterr().out
    assert backups.main([]) == 2
