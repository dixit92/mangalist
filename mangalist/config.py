"""Tiny settings persistence (last folder, window size, ...).

Stored in the ``settings`` table of the library database (:mod:`mangalist.store`) in the per-user data
folder (``paths.data_dir()``), one JSON value per top-level key. The ``config.json`` of older builds is
imported once (its Manga Root becoming root #1) and left in place. Asking for the settings never creates
the data folder: with neither the database nor an old ``config.json`` there, the defaults are returned.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict

from . import paths, store

_log = logging.getLogger(__name__)

_DEFAULTS: Dict[str, Any] = {
    "last_root": "",
    "window": {"w": 1200, "h": 720},
    # List of absolute folder paths the user has marked as examined.
    "examined": [],
    # Whether to automatically start MU lookup after a scan.
    "mu_autostart": False,
    # Column names that are hidden by default.
    "hidden_columns": ["Vol %", "Ch %", "Both %"],
    # QHeaderView state as hex string — persists column order/widths.
    "column_state": "",
    # QSplitter sizes [left_px, right_px]; empty = use defaults.
    "splitter_sizes": [],
}


def _stored() -> Dict[str, Any]:
    """The saved settings, or {} when nothing was ever saved (nothing is created then)."""
    if not (paths.db_file().exists() or paths.config_file().exists() or paths.cache_file().exists()):
        return {}
    try:
        return store.get_store().all_settings()
    except (OSError, sqlite3.Error):
        _log.warning("Could not read the settings; using the defaults", exc_info=True)
        return {}


def load() -> Dict[str, Any]:
    data = _stored()
    merged = dict(_DEFAULTS)
    merged.update(data or {})
    # Ensure nested defaults
    win = dict(_DEFAULTS["window"])
    win.update(merged.get("window") if isinstance(merged.get("window"), dict) else {})
    merged["window"] = win
    if not isinstance(merged.get("examined"), list):
        merged["examined"] = []
    if not isinstance(merged.get("hidden_columns"), list):
        merged["hidden_columns"] = list(_DEFAULTS["hidden_columns"])
    return merged


def save(cfg: Dict[str, Any]) -> None:
    try:
        store.get_store().set_settings(dict(cfg))
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _log.warning("Could not save the settings", exc_info=True)
