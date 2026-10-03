"""One-time, non-destructive import of the files older builds kept in the data folder.

- ``mu_cache.db`` (the MangaUpdates cache) -> ``links_cache``: every row whose folder is not in the
  links cache yet is copied with the columns both sides have (a column the old file lacks takes the
  new table's default, so a pre-stage-2 score stays ``mu_score_version`` 1). Rows already in the links
  cache are never overwritten.
- ``config.json`` (settings) -> ``settings``: each top-level key not set yet. When the database has no
  root yet, the configured Manga Root (``last_root``) becomes root #1.

Each import runs once (recorded in ``meta``: when, and how many rows); a file that does not exist yet is
not recorded, so a later copy (e.g. :func:`mangalist.paths.migrate_legacy_data`) is still imported.
Both old files are opened read-only and left in place.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional

from .db import utcnow

_log = logging.getLogger(__name__)

META_MU_CACHE = "import.mu_cache"
META_CONFIG = "import.config"


def _open_readonly(path: Path) -> sqlite3.Connection:
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return con
    except sqlite3.Error:
        # A WAL file without its -shm in a read-only open can fail; a plain open only reads here.
        return sqlite3.connect(path)


def import_mu_cache(store, cache_path: Path, force: bool = False) -> Optional[int]:
    """Copy *cache_path*'s rows into ``links_cache``. Returns the number of rows added, or None when
    skipped (already imported, or no file)."""
    cache_path = Path(cache_path)
    if not force and store.get_meta(META_MU_CACHE):
        return None
    if not cache_path.is_file():
        return None
    try:
        with closing(_open_readonly(cache_path)) as old:
            old.row_factory = sqlite3.Row
            if not old.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mu_cache'").fetchone():
                rows, old_cols = [], []
            else:
                old_cols = [r[1] for r in old.execute("PRAGMA table_info(mu_cache)")]
                rows = old.execute("SELECT * FROM mu_cache").fetchall()
    except sqlite3.Error:
        _log.warning("Could not read the old MangaUpdates cache %s; not imported", cache_path, exc_info=True)
        return None
    added = 0
    with store.connect() as con:
        new_cols = {r[1] for r in con.execute("PRAGMA table_info(links_cache)")}
        cols = [c for c in old_cols if c in new_cols]
        if "folder" in cols:
            sql = (f"INSERT OR IGNORE INTO links_cache ({', '.join(cols)}) "
                   f"VALUES ({', '.join('?' for _ in cols)})")
            for row in rows:
                cur = con.execute(sql, [row[c] for c in cols])
                added += cur.rowcount
        con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (META_MU_CACHE, json.dumps({"at": utcnow(), "rows": len(rows), "added": added})))
    _log.info("Imported %d of %d MangaUpdates cache rows from %s", added, len(rows), cache_path)
    return added


def import_config(store, config_path: Path, force: bool = False) -> Optional[int]:
    """Copy *config_path*'s settings into ``settings`` and make its Manga Root root #1. Returns the
    number of settings added, or None when skipped."""
    config_path = Path(config_path)
    if not force and store.get_meta(META_CONFIG):
        return None
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _log.warning("Could not read the old settings %s; not imported", config_path, exc_info=True)
        data = None
    if not isinstance(data, dict):
        data = {}
    present = set(store.all_settings())
    fresh = {k: v for k, v in data.items() if k not in present}
    if fresh:
        store.set_settings(fresh)
    root_added = None
    last_root = str(data.get("last_root") or "").strip()
    if last_root and not store.list_roots():
        try:
            root_added = store.add_root(last_root).path
        except ValueError:
            _log.warning("The configured Manga Root %r could not become a root", last_root, exc_info=True)
    store.set_meta(META_CONFIG, json.dumps({"at": utcnow(), "keys": len(fresh), "root": root_added}))
    _log.info("Imported %d settings from %s%s", len(fresh), config_path,
              f"; root #1 = {root_added}" if root_added else "")
    return len(fresh)
