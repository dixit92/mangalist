"""Tiny JSON config persistence (last folder, window size).

Stored as ``config.json`` in the per-user data folder (``paths.data_dir()``).
"""

from __future__ import annotations

import json
from typing import Any, Dict

from . import paths

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


def load() -> Dict[str, Any]:
    path = paths.config_file()
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULTS)
    merged = dict(_DEFAULTS)
    merged.update(data or {})
    # Ensure nested defaults
    win = dict(_DEFAULTS["window"])
    win.update(merged.get("window") or {})
    merged["window"] = win
    if not isinstance(merged.get("examined"), list):
        merged["examined"] = []
    if not isinstance(merged.get("hidden_columns"), list):
        merged["hidden_columns"] = list(_DEFAULTS["hidden_columns"])
    return merged


def save(cfg: Dict[str, Any]) -> None:
    try:
        paths.ensure_data_dir()
        paths.config_file().write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass
