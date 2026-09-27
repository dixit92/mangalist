"""Persistent cache for MangaUpdates lookups — backed by SQLite.

Database: ``mu_cache.db`` in the per-user data folder (``paths.data_dir()``).

Caching rules
-------------
- A confirmed match is never silently downgraded (use ``set_mu_confirmed``
  for explicit user toggles).
- Series identity fields (mu_id, mu_title, mu_url, mu_confirmed, mu_score,
  mu_associated, licensed, publisher_name) are persisted across restarts.
- ``mu_score_version`` records what ``mu_score`` means: rows written before
  the stage-2 matcher are version 1 (word-set Jaccard) and keep that value;
  new scores carry ``MU_SCORE_VERSION``. An older-version score
  is never compared with the stage-2 tiers. ``mu_band`` / ``mu_reasons`` hold
  the matcher's tier and reason names.
- Progress fields (publisher_chapters, publisher_volumes, publisher_status,
  scan_latest_chapter) are always refreshed from the MU API on every scan
  for confirmed matches, so Behind stays current.

Public API
----------
load_entry(folder)  -> dict | None
save_entry(folder, ...)
update_licensed(folder, licensed)
should_recheck_licensed(folder) -> bool
load_all() -> dict[str, dict]
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_DDL = """
CREATE TABLE IF NOT EXISTS mu_cache (
    folder             TEXT PRIMARY KEY,
    mu_id              INTEGER,
    mu_title           TEXT,
    mu_url             TEXT,
    licensed           INTEGER,          -- NULL / 0 / 1
    mu_confirmed       INTEGER NOT NULL DEFAULT 0,
    mu_associated      TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    mu_score           REAL    NOT NULL DEFAULT 0.0,
    scan_latest_chapter REAL,
    publisher_name     TEXT,
    publisher_chapters REAL,
    publisher_volumes  REAL,
    publisher_status   TEXT,
    scan_latest_volume REAL,
    anilist_id         INTEGER,
    anilist_chapters   REAL,
    anilist_volumes    REAL,
    completed_in_origin INTEGER,
    behind_override    TEXT,            -- NULL | 'done'
    mu_score_version   INTEGER NOT NULL DEFAULT 1,      -- 1 = legacy Jaccard, 2 = stage-2 matcher
    mu_band            TEXT,             -- NULL | 'auto' | 'review'
    mu_reasons         TEXT    NOT NULL DEFAULT '[]'    -- JSON array of matcher reason names
);
"""

# Columns added in later versions — applied via ALTER TABLE for existing DBs.
_MIGRATIONS = [
    "ALTER TABLE mu_cache ADD COLUMN scan_latest_volume REAL",
    "ALTER TABLE mu_cache ADD COLUMN anilist_id INTEGER",
    "ALTER TABLE mu_cache ADD COLUMN anilist_chapters REAL",
    "ALTER TABLE mu_cache ADD COLUMN anilist_volumes REAL",
    "ALTER TABLE mu_cache ADD COLUMN completed_in_origin INTEGER",
    "ALTER TABLE mu_cache ADD COLUMN behind_override TEXT",
    # Stage-2 matcher: existing rows keep their old Jaccard score, marked as version 1.
    "ALTER TABLE mu_cache ADD COLUMN mu_score_version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE mu_cache ADD COLUMN mu_band TEXT",
    "ALTER TABLE mu_cache ADD COLUMN mu_reasons TEXT NOT NULL DEFAULT '[]'",
]

# Version of the mu_score semantics written by this build. Bump when scoring semantics change.
#   1 = legacy word-set Jaccard (before the stage-2 matcher)
#   2 = stage-2 raw title score, MangaPixer 1.26.0 port (never released)
#   3 = MangaPixer 1.26.1 port (a "Title: Subtitle" head match is capped at 0.80)
MU_SCORE_VERSION = 3


def _connect() -> sqlite3.Connection:
    paths.ensure_data_dir()
    con = sqlite3.connect(paths.cache_file())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(_DDL)
    for sql in _MIGRATIONS:
        try:
            con.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    con.commit()
    return con


# ---------------------------------------------------------------------------
# Row → dict helper
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    # behind_override is already TEXT (None or 'done') — no conversion needed.
    # Convert INTEGER back to bool/None for licensed
    lic = d.get("licensed")
    d["licensed"] = None if lic is None else bool(lic)
    d["mu_confirmed"] = bool(d.get("mu_confirmed", 0))
    # Same for completed_in_origin
    co = d.get("completed_in_origin")
    d["completed_in_origin"] = None if co is None else bool(co)
    # Deserialise associated titles JSON array
    try:
        d["mu_associated"] = json.loads(d.get("mu_associated") or "[]")
    except (TypeError, ValueError):
        d["mu_associated"] = []
    try:
        d["mu_reasons"] = json.loads(d.get("mu_reasons") or "[]")
    except (TypeError, ValueError):
        d["mu_reasons"] = []
    d["mu_score_version"] = int(d.get("mu_score_version") or 1)
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_entry(folder: Path) -> Optional[Dict[str, Any]]:
    """Return cached data for *folder*, or None if not cached."""
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM mu_cache WHERE folder = ?", (str(folder),)
        ).fetchone()
    return _row_to_dict(row) if row else None


def save_entry(folder: Path, mu_id: int, mu_title: str, mu_url: str,
               licensed: Optional[bool], mu_confirmed: bool,
               mu_associated: Optional[list] = None,
               mu_score: float = 0.0,
               scan_latest_chapter: Optional[float] = None,
               publisher_name: Optional[str] = None,
               publisher_chapters: Optional[float] = None,
               publisher_volumes: Optional[float] = None,
               publisher_status: Optional[str] = None,
               scan_latest_volume: Optional[float] = None,
               anilist_id: Optional[int] = None,
               anilist_chapters: Optional[float] = None,
               anilist_volumes: Optional[float] = None,
               completed_in_origin: Optional[bool] = None,
               behind_override: Optional[str] = None,
               mu_score_version: int = MU_SCORE_VERSION,
               mu_band: Optional[str] = None,
               mu_reasons: Optional[list] = None) -> None:
    """Upsert a MangaUpdates match for *folder*.

    ``mu_score_version`` defaults to this build's version; pass the cached value
    when re-saving a row without re-scoring it (e.g. refreshing a confirmed match).
    """
    key = str(folder)
    # Never silently downgrade a confirmed match.
    if not mu_confirmed:
        with _connect() as con:
            existing = con.execute(
                "SELECT mu_confirmed FROM mu_cache WHERE folder = ?", (key,)
            ).fetchone()
        if existing and existing["mu_confirmed"]:
            mu_confirmed = True

    assoc_json = json.dumps(mu_associated or [], ensure_ascii=False)
    reasons_json = json.dumps(list(mu_reasons or []), ensure_ascii=False)
    with _connect() as con:
        con.execute("""
            INSERT INTO mu_cache
                (folder, mu_id, mu_title, mu_url, licensed, mu_confirmed,
                 mu_associated, mu_score, scan_latest_chapter,
                 publisher_name, publisher_chapters, publisher_volumes, publisher_status,
                 scan_latest_volume, anilist_id, anilist_chapters, anilist_volumes,
                 completed_in_origin, behind_override,
                 mu_score_version, mu_band, mu_reasons)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(folder) DO UPDATE SET
                mu_id              = excluded.mu_id,
                mu_title           = excluded.mu_title,
                mu_url             = excluded.mu_url,
                licensed           = excluded.licensed,
                mu_confirmed       = excluded.mu_confirmed,
                mu_associated      = excluded.mu_associated,
                mu_score           = excluded.mu_score,
                scan_latest_chapter= excluded.scan_latest_chapter,
                publisher_name     = excluded.publisher_name,
                publisher_chapters = excluded.publisher_chapters,
                publisher_volumes  = excluded.publisher_volumes,
                publisher_status   = excluded.publisher_status,
                scan_latest_volume = excluded.scan_latest_volume,
                anilist_id         = excluded.anilist_id,
                anilist_chapters   = excluded.anilist_chapters,
                anilist_volumes    = excluded.anilist_volumes,
                completed_in_origin= excluded.completed_in_origin,
                behind_override    = COALESCE(mu_cache.behind_override, excluded.behind_override),
                mu_score_version   = excluded.mu_score_version,
                mu_band            = excluded.mu_band,
                mu_reasons         = excluded.mu_reasons
        """, (
            key, mu_id, mu_title, mu_url,
            _bool_to_int(licensed), 1 if mu_confirmed else 0,
            assoc_json, mu_score, scan_latest_chapter,
            publisher_name, publisher_chapters, publisher_volumes, publisher_status,
            scan_latest_volume, anilist_id, anilist_chapters, anilist_volumes,
            _bool_to_int(completed_in_origin), behind_override,
            int(mu_score_version), mu_band, reasons_json,
        ))


def set_mu_confirmed(folder: Path, confirmed: bool) -> None:
    """Explicitly set the mu_confirmed flag for *folder*.

    Unlike ``save_entry``, this never silently overrides the caller's intent
    and only touches the single column.
    """
    with _connect() as con:
        con.execute(
            "UPDATE mu_cache SET mu_confirmed = ? WHERE folder = ?",
            (1 if confirmed else 0, str(folder)),
        )


def set_behind_override(folder: Path, value: Optional[str]) -> None:
    """Set or clear the behind_override for *folder* ('done' or None)."""
    with _connect() as con:
        con.execute(
            "UPDATE mu_cache SET behind_override = ? WHERE folder = ?",
            (value, str(folder)),
        )


def delete_entry(folder: Path) -> None:
    """Remove the cached MU match for *folder*, allowing a fresh lookup."""
    with _connect() as con:
        con.execute("DELETE FROM mu_cache WHERE folder = ?", (str(folder),))


def update_licensed(folder: Path, licensed: bool) -> None:
    """Update only the licensed flag for an already-matched entry."""
    with _connect() as con:
        con.execute(
            "UPDATE mu_cache SET licensed = ? WHERE folder = ?",
            (_bool_to_int(licensed), str(folder)),
        )


def should_recheck_licensed(folder: Path) -> bool:
    """Return True if this entry needs a licensed status refresh."""
    entry = load_entry(folder)
    if entry is None:
        return False
    return entry.get("licensed") is not True


def load_all() -> Dict[str, Any]:
    """Return all cache entries as a folder→dict mapping."""
    with _connect() as con:
        rows = con.execute("SELECT * FROM mu_cache").fetchall()
    return {row["folder"]: _row_to_dict(row) for row in rows}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bool_to_int(v: Optional[bool]) -> Optional[int]:
    return None if v is None else (1 if v else 0)


def _opt_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
