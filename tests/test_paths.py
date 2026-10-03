"""``mangalist.paths``: per-user data location, overrides, portable mode and the one-time
migration from the old ``data/`` folder next to the program."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import platformdirs
import pytest

from mangalist import config, mu_cache, paths


def _legacy_folder(root: Path) -> Path:
    """An old-layout data folder: config.json plus a WAL-mode cache with one row."""
    legacy = root / "data"
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text(json.dumps({"last_root": "/library/Manga", "examined": ["/library/Manga/A"]}),
                                        encoding="utf-8")
    con = sqlite3.connect(legacy / "mu_cache.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE mu_cache (folder TEXT PRIMARY KEY, mu_id INTEGER, mu_title TEXT, "
                "mu_confirmed INTEGER NOT NULL DEFAULT 0, mu_score REAL NOT NULL DEFAULT 0.0)")
    # Keyed like mu_cache does: str(Path) (backslashes on Windows).
    con.execute("INSERT INTO mu_cache VALUES (?, 1, 'Some Title', 1, 0.5)", (str(Path("/library/Manga/A")),))
    con.commit()
    con.close()
    return legacy


@pytest.fixture
def per_user(tmp_path, monkeypatch) -> Path:
    """No override: the platform default, redirected into tmp_path."""
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)
    monkeypatch.delenv(paths.LEGACY_ENV_DATA_DIR, raising=False)
    target = tmp_path / "user" / "MangaList"
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *a, **k: str(target))
    monkeypatch.setattr(platformdirs, "user_log_dir", lambda *a, **k: str(target / "Logs"))
    return target


def _frozen(monkeypatch, exe_dir: Path) -> None:
    exe_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "MangaList.exe"))


def test_environment_override(tmp_path):
    # conftest.py sets MANGALIST_DATA_DIR for every test.
    assert paths.data_dir() == tmp_path / "data"
    assert paths.log_dir() == tmp_path / "data" / "logs"
    assert paths.config_file() == tmp_path / "data" / "config.json"
    assert paths.cache_file() == tmp_path / "data" / "mu_cache.db"


def test_legacy_environment_name_is_still_honoured(tmp_path, monkeypatch):
    # Releases before the MangaList rename read MANGA_LIST_DATA_DIR; the new name wins when both are set.
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)
    monkeypatch.setenv(paths.LEGACY_ENV_DATA_DIR, str(tmp_path / "old"))
    assert paths.data_dir() == tmp_path / "old"
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "new"))
    assert paths.data_dir() == tmp_path / "new"


def test_default_is_the_platform_user_folder(monkeypatch):
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)
    monkeypatch.delenv(paths.LEGACY_ENV_DATA_DIR, raising=False)
    assert paths.data_dir() == Path(platformdirs.user_data_dir("MangaList", appauthor=False, roaming=False))
    assert paths.log_dir() == Path(platformdirs.user_log_dir("MangaList", appauthor=False))
    assert not paths.is_portable()


def test_nothing_is_created_by_just_asking(tmp_path):
    paths.data_dir(), paths.log_dir(), config.load()
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("marker", ["portable", "portable.txt"])
def test_portable_marker_keeps_data_next_to_the_executable(tmp_path, monkeypatch, per_user, marker):
    exe_dir = tmp_path / "MangaList"
    _frozen(monkeypatch, exe_dir)
    assert paths.data_dir() == per_user          # no marker: per-user
    (exe_dir / marker).write_text("", encoding="utf-8")
    assert paths.is_portable()
    assert paths.data_dir() == exe_dir / "data"
    assert paths.log_dir() == exe_dir / "logs"
    _legacy_folder(exe_dir)
    assert paths.migrate_legacy_data() is None   # its folder IS the old location


def test_source_checkout_data_is_migrated_once(tmp_path, monkeypatch, per_user):
    checkout = tmp_path / "checkout"
    legacy = _legacy_folder(checkout)
    monkeypatch.setattr(paths, "app_dir", lambda: checkout)

    assert paths.migrate_legacy_data() == legacy
    assert config.load()["last_root"] == "/library/Manga"
    row = mu_cache.load_entry(Path("/library/Manga/A"))
    assert row["mu_title"] == "Some Title" and row["mu_confirmed"] is True
    assert row["mu_score_version"] == 1                       # an old score stays marked legacy
    assert (per_user / paths.MIGRATION_NOTE).is_file()
    # The old folder is untouched.
    assert (legacy / "config.json").is_file() and (legacy / "mu_cache.db").is_file()

    # Second start: nothing happens, even after the old folder changes.
    (legacy / "config.json").write_text(json.dumps({"last_root": "/elsewhere"}), encoding="utf-8")
    assert paths.migrate_legacy_data() is None
    assert config.load()["last_root"] == "/library/Manga"


def test_frozen_build_migrates_data_next_to_the_old_exe(tmp_path, monkeypatch, per_user):
    exe_dir = tmp_path / "old-download"
    _frozen(monkeypatch, exe_dir)
    legacy = _legacy_folder(exe_dir)
    assert paths.migrate_legacy_data() == legacy
    assert (per_user / "mu_cache.db").is_file()


def test_existing_per_user_data_is_never_overwritten(tmp_path, monkeypatch, per_user):
    checkout = tmp_path / "checkout"
    _legacy_folder(checkout)
    monkeypatch.setattr(paths, "app_dir", lambda: checkout)
    per_user.mkdir(parents=True)
    (per_user / "config.json").write_text(json.dumps({"last_root": "/mine"}), encoding="utf-8")
    assert paths.migrate_legacy_data() is None
    assert config.load()["last_root"] == "/mine"


def test_no_old_folder_means_no_migration(tmp_path, monkeypatch, per_user):
    monkeypatch.setattr(paths, "app_dir", lambda: tmp_path / "empty")
    assert paths.migrate_legacy_data() is None
    assert not per_user.exists()
