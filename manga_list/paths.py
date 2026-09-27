"""Where Manga-List keeps its runtime files.

Installed apps cannot write next to their executable (Program Files, a macOS ``.app`` bundle, an
AppImage are read-only), so settings, the MangaUpdates cache and logs live in per-user folders:

=========  ================================================  ==========================================
Platform   Data (``config.json``, ``mu_cache.db``)            Logs
=========  ================================================  ==========================================
Windows    ``%LOCALAPPDATA%\\MangaList``                      ``%LOCALAPPDATA%\\MangaList\\Logs``
macOS      ``~/Library/Application Support/MangaList``        ``~/Library/Logs/MangaList``
Linux      ``$XDG_DATA_HOME/MangaList`` (``~/.local/share``)  ``$XDG_STATE_HOME/MangaList/log``
=========  ================================================  ==========================================

Two exceptions, checked in this order:

- ``MANGA_LIST_DATA_DIR`` (environment): data in that folder, logs in its ``logs`` subfolder.
- Portable mode: a file named ``portable`` (or ``portable.txt``) next to the executable keeps
  ``data/`` and ``logs/`` beside it, as before. The Windows zip ships with the marker.

Versions before this change always wrote ``data/`` next to the executable (or the source tree).
:func:`migrate_legacy_data` copies that folder into the per-user location once, on first start,
and leaves the old folder untouched.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import platformdirs

_log = logging.getLogger(__name__)

APP_NAME = "MangaList"
ENV_DATA_DIR = "MANGA_LIST_DATA_DIR"
PORTABLE_MARKERS = ("portable", "portable.txt")
CONFIG_NAME = "config.json"
CACHE_NAME = "mu_cache.db"
MIGRATION_NOTE = "migrated-from.txt"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """The folder of the executable (frozen build) or the source checkout (``python -m manga_list``)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable() -> bool:
    """A frozen build with a ``portable`` marker file next to its executable."""
    return is_frozen() and any((app_dir() / m).is_file() for m in PORTABLE_MARKERS)


def _env_dir() -> Optional[Path]:
    value = os.environ.get(ENV_DATA_DIR, "").strip()
    return Path(value).expanduser() if value else None


def data_dir() -> Path:
    """Folder of ``config.json`` and ``mu_cache.db`` (not created here)."""
    env = _env_dir()
    if env is not None:
        return env
    if is_portable():
        return app_dir() / "data"
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=False))


def log_dir() -> Path:
    """Folder of the rotating log files (not created here)."""
    env = _env_dir()
    if env is not None:
        return env / "logs"
    if is_portable():
        return app_dir() / "logs"
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


def config_file() -> Path:
    return data_dir() / CONFIG_NAME


def cache_file() -> Path:
    return data_dir() / CACHE_NAME


def ensure_data_dir() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def legacy_data_dirs() -> list:
    """Where versions before the per-user location kept their data: ``data/`` next to the executable
    (frozen) or in the source checkout."""
    return [app_dir() / "data"]


def migrate_legacy_data(target: Optional[Path] = None,
                        sources: Optional[Iterable[Path]] = None) -> Optional[Path]:
    """Copy settings and the MangaUpdates cache from the old location into the per-user folder, once.

    Runs only when the per-user folder has no settings and no cache yet (so it never overwrites and
    happens at most once) and is skipped for the environment override and portable mode, whose
    folder IS the old location. The old folder is left as it was. Returns the folder copied from, or
    None when nothing was migrated.
    """
    if target is None:
        if _env_dir() is not None or is_portable():
            return None
        target = data_dir()
    if any((target / name).exists() for name in (CONFIG_NAME, CACHE_NAME, MIGRATION_NOTE)):
        return None
    for source in (legacy_data_dirs() if sources is None else sources):
        source = Path(source)
        try:
            if source.resolve() == target.resolve():
                continue
        except OSError:
            continue
        has_config = (source / CONFIG_NAME).is_file()
        has_cache = (source / CACHE_NAME).is_file()
        if not (has_config or has_cache):
            continue
        target.mkdir(parents=True, exist_ok=True)
        if has_config:
            shutil.copy2(source / CONFIG_NAME, target / CONFIG_NAME)
        if has_cache:
            _copy_sqlite(source / CACHE_NAME, target / CACHE_NAME)
        (target / MIGRATION_NOTE).write_text(
            f"Settings and cache copied from {source} on {datetime.now():%Y-%m-%d %H:%M}.\n"
            "The old folder was left in place; delete it once this version works for you.\n",
            encoding="utf-8")
        _log.info("Migrated settings/cache from %s to %s", source, target)
        return source
    return None


def _copy_sqlite(src: Path, dst: Path) -> None:
    """Copy a (WAL-mode) SQLite database consistently: the backup API reads committed WAL content too.
    Falls back to copying the database and its -wal / -shm files."""
    try:
        with closing(sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)) as source, \
                closing(sqlite3.connect(dst)) as target:
            source.backup(target)
        return
    except sqlite3.Error:
        _log.warning("SQLite backup of %s failed; copying the files instead", src, exc_info=True)
        if dst.exists():
            dst.unlink()
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(src) + suffix)
        if f.is_file():
            shutil.copy2(f, Path(str(dst) + suffix))
