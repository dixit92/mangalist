"""The library database: ONE SQLite file (``mangalist.db``) in the data folder.

Tables: roots + exclusions, series (root-relative path + folder fingerprint + MangaUpdates identity),
units (the parser's output), links_cache (the old ``mu_cache.db`` rows), ledger (dispatch requests),
journal (filesystem plans), settings (the old ``config.json``), meta. Schema and migration rules:
:mod:`manga_list.store.schema`; the one-time import of the old files: :mod:`manga_list.store.migrate`.

No Qt here: the headless runner and the no-Qt CI job import this package.

Usage::

    from manga_list import store
    db = store.get_store()          # the data folder's database (created on first use)
    db.add_root("/data/manga", "Manga", exclusions=["@Oneshots/**"])
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from .. import paths
from .db import StoreBase, utcnow
from .exclusions import ExclusionSet, InvalidPattern, normalize_pattern
from .roots import Root, RootError, RootsMixin
from .schema import SCHEMA_VERSION
from .series import ScanRecord, Series, SeriesMixin, SeriesSeen, folder_fingerprint, seen_from_entries
from .settings import SettingsMixin
from .units import Unit, UnitError, UnitsMixin

_log = logging.getLogger(__name__)

__all__ = [
    "Store", "get_store", "reset_stores", "Root", "RootError", "Series", "SeriesSeen", "ScanRecord",
    "Unit", "UnitError", "ExclusionSet", "InvalidPattern", "normalize_pattern", "folder_fingerprint",
    "seen_from_entries", "SCHEMA_VERSION", "utcnow",
]


class Store(RootsMixin, SeriesMixin, UnitsMixin, SettingsMixin, StoreBase):
    """The library database at *path*. Old ``mu_cache.db`` / ``config.json`` files are looked for in
    *legacy_dir* (default: the database's folder) and imported once."""

    def __init__(self, path: Path, legacy_dir: Optional[Path] = None, import_legacy: bool = True):
        super().__init__(path)
        self.legacy_dir = Path(legacy_dir) if legacy_dir is not None else self.path.parent
        self._import_legacy = import_legacy

    def _after_init(self) -> None:
        if not self._import_legacy:
            return
        from . import migrate

        try:
            migrate.import_mu_cache(self, self.legacy_dir / paths.CACHE_NAME)
        except Exception:  # noqa: BLE001 - an import problem must never stop the app
            _log.exception("Importing the old MangaUpdates cache failed")
        try:
            migrate.import_config(self, self.legacy_dir / paths.CONFIG_NAME)
        except Exception:  # noqa: BLE001
            _log.exception("Importing the old settings failed")

    def import_legacy_now(self) -> None:
        """Re-check the old files (e.g. right after they were copied in); still once per file."""
        self.ensure_initialized()
        self._after_init()


_stores: Dict[str, Store] = {}
_stores_lock = threading.Lock()


def get_store(path: Optional[Path] = None) -> Store:
    """The shared :class:`Store` of *path* (default: the data folder's ``mangalist.db``)."""
    db = Path(path) if path is not None else paths.db_file()
    key = str(db)
    with _stores_lock:
        st = _stores.get(key)
        if st is None:
            st = _stores[key] = Store(db)
    return st


def reset_stores() -> None:
    """Forget the shared stores (tests; a changed data folder)."""
    with _stores_lock:
        _stores.clear()
