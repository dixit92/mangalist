"""Schema of the library database, as an ordered list of additive migrations.

Rules (so an older build can still open a database a newer build has upgraded):

- A migration only ADDS: new tables, new columns (``ALTER TABLE ... ADD COLUMN`` with a default), new
  indexes. Never drop, rename or narrow anything; never rewrite rows a previous build relies on.
- No ``CHECK`` constraints: SQLite cannot relax one without rebuilding the table, so allowed values are
  validated in Python (the sets below), where they can grow.
- ``PRAGMA user_version`` holds the number of the last migration applied. A database with a HIGHER
  number than this build knows is opened as is (its extra columns are ignored) and never downgraded.
- Paths inside a root are stored root-relative with ``/`` separators, so a Windows instance and a
  Linux / Unraid instance describe the same library the same way.
"""

from __future__ import annotations

from typing import List, Tuple

# --- Allowed values (validated in Python) --------------------------------------------------------------

ORIGIN_HINTS = ("manga", "manhwa", "webcomic")  # or None: no hint
ENFORCE_NAMING = ("off", "ask", "automatic")
ENFORCE_NAMING_DEFAULT = "ask"  # Design Decisions C2 (owner: ask by default)
SERIES_STATUS = ("present", "missing")
UNIT_KINDS = ("volume", "chapter", "extra", "oneshot", "unknown")
SERIES_KIND_HINTS = ("volumes", "chapters")  # or None: not answered yet (C12)
LEDGER_STATUS = ("queued", "dispatched", "completed", "failed", "cancelled")
PLAN_STATUS = ("planned", "applying", "applied", "failed", "interrupted", "undoing", "undone", "undo_failed")
STEP_STATE = ("planned", "intent", "done", "failed", "undo_intent", "undone")

# --- Migrations ----------------------------------------------------------------------------------------

_V1 = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Application settings (what config.json held): one JSON value per top-level key.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- A root is a folder whose direct children are series folders.
CREATE TABLE IF NOT EXISTS roots (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,                 -- display name
    path            TEXT    NOT NULL UNIQUE,          -- as this instance sees it (e.g. /data/... in Docker)
    origin_hint     TEXT,                             -- NULL | manga | manhwa | webcomic (matcher evidence only)
    enforce_naming  TEXT    NOT NULL DEFAULT 'ask',   -- off | ask | automatic
    naming_scheme   TEXT,                             -- NULL = the global scheme (phase 2)
    staging_folder  TEXT,                             -- NULL = none; arrivals (phase 3)
    position        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- Root-relative glob patterns; an excluded path is never scanned.
CREATE TABLE IF NOT EXISTS exclusions (
    id        INTEGER PRIMARY KEY,
    root_id   INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    pattern   TEXT    NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (root_id, pattern)
);

-- One row per series folder seen in a root. The fingerprint lets a renamed folder keep its row (and so
-- its MangaUpdates link).
CREATE TABLE IF NOT EXISTS series (
    id            INTEGER PRIMARY KEY,
    root_id       INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    rel_path      TEXT    NOT NULL,
    fingerprint   TEXT,                               -- NULL for a folder without archives
    n_archives    INTEGER NOT NULL DEFAULT 0,
    mu_id         INTEGER,
    mu_confirmed  INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'present', -- present | missing
    first_seen_at TEXT    NOT NULL,
    last_seen_at  TEXT    NOT NULL,
    UNIQUE (root_id, rel_path)
);
CREATE INDEX IF NOT EXISTS series_by_fingerprint ON series (root_id, fingerprint);

-- The parser's output: one row per unit an archive holds (a volume archive may hold a chapter range).
-- Numbers are exact decimal strings ('12', '12.5', '0003.99' is stored as '3.99'): never floats.
CREATE TABLE IF NOT EXISTS units (
    id          INTEGER PRIMARY KEY,
    series_id   INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    rel_path    TEXT    NOT NULL,                     -- the archive, relative to the series folder
    seq         INTEGER NOT NULL DEFAULT 0,           -- order of the unit within the archive
    kind        TEXT    NOT NULL,                     -- volume | chapter | extra | oneshot | unknown
    vol_from    TEXT,
    vol_to      TEXT,
    ch_from     TEXT,
    ch_to       TEXT,
    group_name  TEXT,
    title       TEXT,
    idx         TEXT,                                 -- the file's index token (e.g. FMD2's), as written
    parser      TEXT,                                 -- which parser layer produced the row
    file_size   INTEGER,
    updated_at  TEXT    NOT NULL,
    UNIQUE (series_id, rel_path, seq)
);

-- The MangaUpdates links cache (the rows of the old mu_cache.db, same columns), keyed by the absolute
-- folder path as the GUI shows it.
CREATE TABLE IF NOT EXISTS links_cache (
    folder              TEXT PRIMARY KEY,
    mu_id               INTEGER,
    mu_title            TEXT,
    mu_url              TEXT,
    licensed            INTEGER,
    mu_confirmed        INTEGER NOT NULL DEFAULT 0,
    mu_associated       TEXT    NOT NULL DEFAULT '[]',
    mu_score            REAL    NOT NULL DEFAULT 0.0,
    scan_latest_chapter REAL,
    publisher_name      TEXT,
    publisher_chapters  REAL,
    publisher_volumes   REAL,
    publisher_status    TEXT,
    scan_latest_volume  REAL,
    anilist_id          INTEGER,
    anilist_chapters    REAL,
    anilist_volumes     REAL,
    completed_in_origin INTEGER,
    behind_override     TEXT,
    mu_score_version    INTEGER NOT NULL DEFAULT 1,
    mu_band             TEXT,
    mu_reasons          TEXT    NOT NULL DEFAULT '[]'
);

-- Dispatch requests handed to an external tool (phase 3+; schema only).
CREATE TABLE IF NOT EXISTS ledger (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    series_id     INTEGER REFERENCES series(id) ON DELETE SET NULL,
    request       TEXT    NOT NULL DEFAULT '{}',      -- JSON: the units wanted
    tool          TEXT    NOT NULL,                   -- e.g. suwayomi | qbittorrent
    destination   TEXT,
    status        TEXT    NOT NULL DEFAULT 'queued',  -- queued | dispatched | completed | failed | cancelled
    batch         TEXT,                               -- the scheduled batch it belongs to
    scheduled_for TEXT,
    external_ref  TEXT,
    error         TEXT
);

-- The filesystem journal: a plan of moves, each step written ahead (intent) and confirmed (done).
CREATE TABLE IF NOT EXISTS journal_plans (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    reason      TEXT    NOT NULL,                     -- enforcement | import | upgrade | manual | ...
    root_path   TEXT,                                 -- every step must lie inside it (NULL = unchecked)
    status      TEXT    NOT NULL DEFAULT 'planned',
    note        TEXT,
    host        TEXT,
    pid         INTEGER
);
CREATE TABLE IF NOT EXISTS journal_steps (
    id            INTEGER PRIMARY KEY,
    plan_id       INTEGER NOT NULL REFERENCES journal_plans(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    op            TEXT    NOT NULL DEFAULT 'move',
    src           TEXT    NOT NULL,
    dst           TEXT    NOT NULL,
    is_dir        INTEGER NOT NULL DEFAULT 0,
    src_size      INTEGER,
    src_signature TEXT,                               -- content signature of a file when planned
    created_dirs  TEXT    NOT NULL DEFAULT '[]',      -- JSON: parent folders this step created
    state         TEXT    NOT NULL DEFAULT 'planned',
    error         TEXT,
    updated_at    TEXT    NOT NULL,
    UNIQUE (plan_id, seq)
);
"""

# Phase 1, inventory lane: the per-series "volumes or chapters?" answer (Design Decisions C12) and the
# number of a unit whose kind the name does not state (a bare ``01.cbz`` before that answer).
_V2_INVENTORY = """
ALTER TABLE series ADD COLUMN kind_hint TEXT;          -- NULL | volumes | chapters (asked once, C12)
ALTER TABLE units  ADD COLUMN num_from  TEXT;          -- kind 'unknown': the bare number (exact decimal)
ALTER TABLE units  ADD COLUMN num_to    TEXT;
"""

# (version, script). Append only; never edit a shipped entry.
MIGRATIONS: List[Tuple[int, str]] = [
    (1, _V1),
    (2, _V2_INVENTORY),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]
