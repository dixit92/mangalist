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
ARCHIVE_STATUS = ("present", "missing")
ARCHIVE_MOVE_HOW = ("scan", "pairing", "journal")            # how an archive move was recognised
SERIES_CARRY_HOW = ("archives", "mangapixer", "manual", "fingerprint")
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

# MangaPixer source (phase 1): the connection, the libraries, the cached export items, the per-library
# sync state and the root -> library mapping. New tables only (mangalist.store.mangapixer).
_V2_MANGAPIXER = """
-- One row (id = 1): the MangaPixer server. The token lives here, not in ``settings``: config.load() /
-- config.save() copy every settings key around, and the token must never travel with them.
CREATE TABLE IF NOT EXISTS mangapixer_connection (
    id                INTEGER PRIMARY KEY,
    base_url          TEXT,
    token             TEXT,                           -- never logged, never shown again after entry
    verify_tls        INTEGER NOT NULL DEFAULT 1,     -- 0 = accept any certificate (opt-in)
    ca_file           TEXT,                           -- a CA bundle for a self-signed MangaPixer certificate
    token_rejected_at TEXT,                           -- set on HTTP 401; nothing syncs until a new token / a manual retry
    updated_at        TEXT    NOT NULL
);

-- MangaPixer's libraries as /api/v1/export/libraries lists them.
CREATE TABLE IF NOT EXISTS mangapixer_libraries (
    id            TEXT PRIMARY KEY,                   -- MangaPixer's library id
    display_name  TEXT    NOT NULL,
    kind          TEXT,                               -- manga | manhwa | ... | NULL
    folder_count  INTEGER,
    item_count    INTEGER,
    last_scan_at  TEXT,
    present       INTEGER NOT NULL DEFAULT 1,         -- 0 once MangaPixer no longer lists it
    position      INTEGER NOT NULL DEFAULT 0,
    seen_at       TEXT    NOT NULL
);

-- The export's items (folders only), keyed by (library, nodeId); the item JSON as MangaPixer sent it.
CREATE TABLE IF NOT EXISTS mangapixer_items (
    library_id  TEXT    NOT NULL,
    node_id     TEXT    NOT NULL,
    trail       TEXT    NOT NULL,                     -- JSON array of on-disk names below the library root
    trail_key   TEXT    NOT NULL,                     -- NFC names joined with '/'
    trail_fold  TEXT    NOT NULL,                     -- trail_key casefolded (the fallback lookup)
    link_state  TEXT,
    updated_at  TEXT,                                 -- the item's updatedAt (MangaPixer's clock)
    item        TEXT    NOT NULL,
    synced_at   TEXT    NOT NULL,
    PRIMARY KEY (library_id, node_id)
);
CREATE INDEX IF NOT EXISTS mangapixer_items_by_trail ON mangapixer_items (library_id, trail_key);
CREATE INDEX IF NOT EXISTS mangapixer_items_by_fold ON mangapixer_items (library_id, trail_fold);

-- Per library: the FIRST page's serverTime of the last complete sync (the next updatedSince).
CREATE TABLE IF NOT EXISTS mangapixer_sync (
    library_id    TEXT PRIMARY KEY,
    server_time   TEXT,                               -- NULL = the next sync is a full one
    last_full_at  TEXT,                               -- MangaList's clock, for display
    last_sync_at  TEXT,
    last_status   TEXT,                               -- ok | error
    last_error    TEXT,
    last_mode     TEXT                                -- full | incremental
);

-- Root -> MangaPixer library (+ trail prefix). manual = 1: the owner's override, never re-computed.
CREATE TABLE IF NOT EXISTS mangapixer_mappings (
    root_id     INTEGER PRIMARY KEY REFERENCES roots(id) ON DELETE CASCADE,
    library_id  TEXT,                                 -- NULL = not mapped (manual: "do not use MangaPixer")
    prefix      TEXT    NOT NULL DEFAULT '[]',        -- JSON array: the root's trail inside the library
    manual      INTEGER NOT NULL DEFAULT 0,
    any_kind    INTEGER NOT NULL DEFAULT 0,           -- 1 = use it although the library's kind is skipped by default
    matched     INTEGER,
    unmatched   INTEGER,
    updated_at  TEXT    NOT NULL
);
"""

# Phase 1, inventory lane: the per-series "volumes or chapters?" answer (Design Decisions C12) and the
# number of a unit whose kind the name does not state (a bare ``01.cbz`` before that answer).
_V3_INVENTORY = """
ALTER TABLE series ADD COLUMN kind_hint TEXT;          -- NULL | volumes | chapters (asked once, C12)
ALTER TABLE units  ADD COLUMN num_from  TEXT;          -- kind 'unknown': the bare number (exact decimal)
ALTER TABLE units  ADD COLUMN num_to    TEXT;
"""

# Series identity: one row per archive (MangaPixer's v1 content signature, filled by a background backfill),
# the archive moves recognised, the series carry-overs, and MangaPixer's carriedFrom pairs
# (mangalist.store.archives, mangalist.identity). The series fingerprint column stays (legacy re-link only).
_V4_IDENTITY = """
CREATE TABLE IF NOT EXISTS archives (
    id              INTEGER PRIMARY KEY,
    root_id         INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    series_id       INTEGER REFERENCES series(id) ON DELETE SET NULL,
    rel_path        TEXT    NOT NULL,                 -- root-relative, '/' separators
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    signature       TEXT,                             -- v1:<len>:<sha256>; NULL until signed or after a change
    status          TEXT    NOT NULL DEFAULT 'present', -- present | missing
    first_seen_at   TEXT    NOT NULL,
    last_seen_at    TEXT    NOT NULL,
    missing_since   TEXT,
    first_seen_scan INTEGER NOT NULL DEFAULT 0,       -- scan revision (meta 'identity.scan') that first saw it
    last_seen_scan  INTEGER NOT NULL DEFAULT 0,       -- ... and last saw it present
    UNIQUE (root_id, rel_path)
);
CREATE INDEX IF NOT EXISTS archives_by_series ON archives (series_id);
CREATE INDEX IF NOT EXISTS archives_by_size ON archives (size);
CREATE INDEX IF NOT EXISTS archives_by_signature ON archives (signature);
CREATE INDEX IF NOT EXISTS archives_by_status ON archives (status, signature);

-- Archive moves recognised (scan / after-the-fact pairing / journal): the ledger series carry-over reads.
CREATE TABLE IF NOT EXISTS archive_moves (
    id              INTEGER PRIMARY KEY,
    archive_id      INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
    from_series_id  INTEGER,                          -- no foreign key: kept after a series row is gone
    to_series_id    INTEGER,
    from_root_id    INTEGER,
    from_path       TEXT    NOT NULL,
    to_root_id      INTEGER,
    to_path         TEXT    NOT NULL,
    how             TEXT    NOT NULL,                 -- scan | pairing | journal
    scan            INTEGER,
    at              TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS archive_moves_by_from ON archive_moves (from_series_id);

-- A vanished series' data carried to a live folder (or re-attached / kept), for the record.
CREATE TABLE IF NOT EXISTS series_carries (
    id              INTEGER PRIMARY KEY,
    from_series_id  INTEGER,
    to_series_id    INTEGER,
    from_root_id    INTEGER,
    from_path       TEXT,
    to_root_id      INTEGER,
    to_path         TEXT,
    how             TEXT    NOT NULL,                 -- archives | mangapixer | manual | fingerprint
    moved           TEXT    NOT NULL DEFAULT '[]',    -- JSON: what moved (row, link, kind, examined)
    kept            TEXT    NOT NULL DEFAULT '[]',    -- JSON: what stayed on the old row (the target had its own)
    at              TEXT    NOT NULL
);

-- MangaPixer's carriedFrom pairs as a sync saw them. The old node's item row is re-keyed away by the sync,
-- so its trail is kept here until a MangaList series can be carried (or the window ends).
CREATE TABLE IF NOT EXISTS mangapixer_carries (
    library_id   TEXT    NOT NULL,
    old_node_id  TEXT    NOT NULL,
    new_node_id  TEXT    NOT NULL,
    old_trail    TEXT    NOT NULL,                    -- JSON array
    new_trail    TEXT,                                -- JSON array (the item's trail when seen)
    seen_at      TEXT    NOT NULL,
    applied_at   TEXT,
    outcome      TEXT,                                -- NULL = pending | carried | kept | nothing
    PRIMARY KEY (library_id, old_node_id)
);

ALTER TABLE series ADD COLUMN missing_since TEXT;
"""

# (version, script). Append only; never edit a shipped entry.
MIGRATIONS: List[Tuple[int, str]] = [
    (1, _V1),
    (2, _V2_MANGAPIXER),
    (3, _V3_INVENTORY),
    (4, _V4_IDENTITY),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]
