"""The one-time import of mu_cache.db and config.json: idempotent, non-destructive."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mangalist import config, mu_cache, paths, store
from mangalist.store import migrate

_LEGACY_DDL = """
CREATE TABLE mu_cache (
    folder TEXT PRIMARY KEY, mu_id INTEGER, mu_title TEXT, mu_url TEXT, licensed INTEGER,
    mu_confirmed INTEGER NOT NULL DEFAULT 0, mu_associated TEXT NOT NULL DEFAULT '[]',
    mu_score REAL NOT NULL DEFAULT 0.0, behind_override TEXT
);
"""


def _legacy_files(folder: Path, rows=(("/library/Manga/A", 1, "Title A", 1),), cfg=None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(folder / paths.CACHE_NAME)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_LEGACY_DDL)
    for f, mid, title, conf in rows:
        con.execute("INSERT INTO mu_cache (folder, mu_id, mu_title, mu_confirmed, mu_associated) VALUES (?,?,?,?,?)",
                    (str(Path(f)), mid, title, conf, '["Alt"]'))
    con.commit()
    con.close()
    cfg = {"last_root": "/library/Manga", "examined": ["/library/Manga/A"], "mu_autostart": True} \
        if cfg is None else cfg
    (folder / paths.CONFIG_NAME).write_text(json.dumps(cfg), encoding="utf-8")


def _bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_first_open_imports_cache_settings_and_root_1():
    data = paths.data_dir()
    _legacy_files(data)
    before = (_bytes(data / paths.CACHE_NAME), _bytes(data / paths.CONFIG_NAME))
    store.reset_stores()

    row = mu_cache.load_entry(Path("/library/Manga/A"))
    assert row["mu_title"] == "Title A" and row["mu_confirmed"] is True
    assert row["mu_associated"] == ["Alt"] and row["mu_score_version"] == 1   # an old score stays legacy
    cfg = config.load()
    assert cfg["mu_autostart"] is True and cfg["examined"] == ["/library/Manga/A"]
    roots = store.get_store().list_roots()
    assert [(r.id, r.path, r.name) for r in roots] == [(1, str(Path("/library/Manga").absolute()), "Manga")]
    # Old files kept, unchanged in content.
    assert (data / paths.CACHE_NAME).is_file() and (data / paths.CONFIG_NAME).is_file()
    assert _bytes(data / paths.CONFIG_NAME) == before[1]
    con = sqlite3.connect(data / paths.CACHE_NAME)
    assert con.execute("SELECT COUNT(*) FROM mu_cache").fetchone()[0] == 1
    con.close()


def test_import_is_idempotent_and_never_overwrites():
    data = paths.data_dir()
    _legacy_files(data)
    st = store.get_store()
    st.ensure_initialized()
    mu_cache.save_entry(Path("/library/Manga/A"), 2, "Changed since", "", None, mu_confirmed=False)
    # Running the imports again (even forced) adds nothing and overwrites nothing.
    assert migrate.import_mu_cache(st, data / paths.CACHE_NAME) is None
    assert migrate.import_mu_cache(st, data / paths.CACHE_NAME, force=True) == 0
    assert migrate.import_config(st, data / paths.CONFIG_NAME) is None
    config.save({**config.load(), "mu_autostart": False})
    assert migrate.import_config(st, data / paths.CONFIG_NAME, force=True) == 0
    assert mu_cache.load_entry(Path("/library/Manga/A"))["mu_title"] == "Changed since"
    assert config.load()["mu_autostart"] is False
    assert len(st.list_roots()) == 1
    assert len(mu_cache.load_all()) == 1


def test_a_cache_copied_in_later_is_still_imported():
    st = store.get_store()
    st.list_roots()                         # database created with no old files around
    assert st.get_meta(migrate.META_MU_CACHE) is None
    _legacy_files(paths.data_dir(), rows=(("/library/Manga/B", 5, "Title B", 0),), cfg={})
    st.import_legacy_now()
    assert mu_cache.load_entry(Path("/library/Manga/B"))["mu_id"] == 5
    assert st.list_roots() == []            # an empty config has no root
    st.import_legacy_now()                  # once per file
    assert len(mu_cache.load_all()) == 1


def test_no_root_is_added_when_roots_exist(tmp_path):
    data = paths.data_dir()
    _legacy_files(data)
    st = store.Store(paths.db_file(), import_legacy=False)
    other = tmp_path / "other"
    other.mkdir()
    st.add_root(str(other))
    assert migrate.import_config(st, data / paths.CONFIG_NAME) == 3
    assert [r.path for r in st.list_roots()] == [str(other)]


def test_a_broken_old_file_does_not_stop_the_app():
    data = paths.data_dir()
    data.mkdir(parents=True)
    (data / paths.CACHE_NAME).write_bytes(b"this is not sqlite")
    (data / paths.CONFIG_NAME).write_text("{ not json", encoding="utf-8")
    assert mu_cache.load_all() == {}
    assert config.load()["last_root"] == ""


def test_the_full_current_schema_imports_every_column():
    """A cache written by the current release (all 22 columns) loses nothing."""
    data = paths.data_dir()
    data.mkdir(parents=True)
    con = sqlite3.connect(data / paths.CACHE_NAME)
    con.execute(mu_cache._DDL)
    con.execute("INSERT INTO mu_cache (folder, mu_id, mu_title, mu_url, licensed, mu_confirmed, mu_associated,"
                " mu_score, scan_latest_chapter, publisher_name, publisher_chapters, publisher_volumes,"
                " publisher_status, scan_latest_volume, anilist_id, anilist_chapters, anilist_volumes,"
                " completed_in_origin, behind_override, mu_score_version, mu_band, mu_reasons)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(Path("/library/Manga/C")), 3, "C", "https://example.invalid/c", 1, 1, "[]", 0.97, 120.5,
                 "Pub", 100.0, 10.0, "Ongoing", 11.0, 77, 130.0, 12.0, 0, "done", 4, "auto", '["Exact"]'))
    con.commit()
    con.close()
    row = mu_cache.load_entry(Path("/library/Manga/C"))
    assert row["licensed"] is True and row["scan_latest_chapter"] == 120.5 and row["publisher_name"] == "Pub"
    assert row["anilist_id"] == 77 and row["completed_in_origin"] is False and row["behind_override"] == "done"
    assert row["mu_score_version"] == 4 and row["mu_band"] == "auto" and row["mu_reasons"] == ["Exact"]


def test_settings_never_create_the_data_folder():
    config.load()
    assert not paths.data_dir().exists()
