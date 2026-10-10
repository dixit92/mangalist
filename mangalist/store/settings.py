"""Application settings: one JSON value per top-level key (what ``config.json`` held)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Mapping

_log = logging.getLogger(__name__)

_MISSING = object()


class SettingsMixin:
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.set_settings({key: value})

    def set_settings(self, values: Mapping[str, Any]) -> None:
        rows = [(str(k), json.dumps(v, ensure_ascii=False)) for k, v in values.items()]
        with self.connect() as con:
            con.executemany("INSERT INTO settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", rows)
        # The keys only: a value may be a path, a name or (for a service) something private.
        _log.debug("Settings stored: %s", ", ".join(sorted(str(k) for k in values)))

    def delete_setting(self, key: str) -> None:
        """Forget *key* (the next read gives the default again)."""
        with self.connect() as con:
            con.execute("DELETE FROM settings WHERE key = ?", (key,))

    def all_settings(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        with self.connect() as con:
            for row in con.execute("SELECT key, value FROM settings"):
                try:
                    out[row["key"]] = json.loads(row["value"])
                except (TypeError, ValueError):
                    continue
        return out
