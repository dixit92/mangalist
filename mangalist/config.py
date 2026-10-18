"""Tiny settings persistence (last folder, window size, ...).

Stored in the ``settings`` table of the library database (:mod:`mangalist.store`) in the per-user data
folder (``paths.data_dir()``), one JSON value per top-level key. The ``config.json`` of older builds is
imported once (its Manga Root becoming root #1) and left in place. Asking for the settings never creates
the data folder: with neither the database nor an old ``config.json`` there, the defaults are returned.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, Mapping

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


class Settings(dict):
    """The settings as :func:`load` read them, remembering those values: :func:`save` writes back only the keys this
    copy changed. A window keeps its copy for hours while other parts of MangaList write their own keys (the Renamer's
    converted libraries, a scan to ask MangaPixer for, ...); writing every key back put the old values over theirs
    (owner, 2026-10-18: two libraries the owner had converted were not "converted" any more - the window's save after
    the rename wrote the list it had read at startup)."""

    def __init__(self, values: Mapping[str, Any]):
        super().__init__(values)
        self._as_saved: Dict[str, str] = {k: _frozen(v) for k, v in self.items()}

    def changed(self) -> Dict[str, Any]:
        """The keys whose values differ from what was read or last saved (nested values compared by content)."""
        return {k: v for k, v in self.items() if self._as_saved.get(k) != _frozen(v)}

    def mark_saved(self, keys) -> None:
        for k in keys:
            if k in self:
                self._as_saved[k] = _frozen(self[k])


def _frozen(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def load() -> "Settings":
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
    return Settings(merged)


def save(cfg: Dict[str, Any]) -> None:
    """Save *cfg*: from :func:`load`, only the keys changed since it was read (or last saved); a plain dict - every key."""
    values = cfg.changed() if isinstance(cfg, Settings) else dict(cfg)
    if not values:
        return
    try:
        store.get_store().set_settings(values)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _log.warning("Could not save the settings", exc_info=True)
        return
    if isinstance(cfg, Settings):
        cfg.mark_saved(values)


def save_values(values: Mapping[str, Any]) -> None:
    """Save only these keys (a splitter's sizes, the library picked): :func:`save` rewrites every key, the examined list
    and the column layout included, which made a library switch or a drag pay for all of them."""
    try:
        store.get_store().set_settings(dict(values))
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _log.warning("Could not save the settings", exc_info=True)
