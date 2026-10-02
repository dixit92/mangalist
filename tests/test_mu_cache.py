"""``mu_cache``: the stage-2 score-version migration (old Jaccard scores stay marked as version 1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from manga_list import mu_cache, paths

# The schema as shipped before the stage-2 matcher (no mu_score_version / mu_band / mu_reasons).
_LEGACY_DDL = """
CREATE TABLE mu_cache (
    folder TEXT PRIMARY KEY, mu_id INTEGER, mu_title TEXT, mu_url TEXT, licensed INTEGER,
    mu_confirmed INTEGER NOT NULL DEFAULT 0, mu_associated TEXT NOT NULL DEFAULT '[]',
    mu_score REAL NOT NULL DEFAULT 0.0, scan_latest_chapter REAL, publisher_name TEXT,
    publisher_chapters REAL, publisher_volumes REAL, publisher_status TEXT, scan_latest_volume REAL,
    anilist_id INTEGER, anilist_chapters REAL, anilist_volumes REAL, completed_in_origin INTEGER,
    behind_override TEXT
);
"""


@pytest.fixture
def cache_db() -> Path:
    # The per-test data folder (conftest.py sets MANGA_LIST_DATA_DIR).
    return paths.cache_file()


def test_legacy_rows_are_marked_version_1(cache_db):
    cache_db.parent.mkdir()
    con = sqlite3.connect(cache_db)
    con.executescript(_LEGACY_DDL)
    con.execute("INSERT INTO mu_cache (folder, mu_id, mu_title, mu_score, mu_confirmed) VALUES (?,?,?,?,?)",
                (str(Path("/library/Manga/A")), 1, "Legacy Title", 1.0, 0))  # keyed like mu_cache does
    con.commit()
    con.close()

    row = mu_cache.load_entry(Path("/library/Manga/A"))
    assert row["mu_score"] == 1.0
    assert row["mu_score_version"] == 1
    assert row["mu_band"] is None
    assert row["mu_reasons"] == []


def test_new_scores_are_written_as_the_current_version(cache_db):
    folder = Path("/library/Manga/B")
    mu_cache.save_entry(folder, 2, "Title", "", None, mu_confirmed=False, mu_score=0.93,
                        mu_band="review", mu_reasons=["CloseSecond"])
    row = mu_cache.load_entry(folder)
    assert mu_cache.MU_SCORE_VERSION == 4  # MangaPixer 1.31.1 port
    assert row["mu_score_version"] == mu_cache.MU_SCORE_VERSION
    assert row["mu_band"] == "review"
    assert row["mu_reasons"] == ["CloseSecond"]


def test_resaving_without_rescoring_keeps_the_stored_version(cache_db):
    folder = Path("/library/Manga/C")
    mu_cache.save_entry(folder, 3, "Title", "", None, mu_confirmed=True, mu_score=0.4, mu_score_version=1)
    assert mu_cache.load_entry(folder)["mu_score_version"] == 1
    # Re-scored later: the new version replaces it.
    mu_cache.save_entry(folder, 3, "Title", "", None, mu_confirmed=True, mu_score=1.0, mu_band="auto")
    row = mu_cache.load_entry(folder)
    assert row["mu_score_version"] == mu_cache.MU_SCORE_VERSION and row["mu_confirmed"] is True
