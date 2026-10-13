"""Unbundled Chapters: the per-series setting that turns a volume series into volumes + the chapters after its last
volume (backlog, decided 2026-10-11; owner: "there should be a setting to convert a volume-only series to a volumes +
unbundled chapters series, with both upgrade and new missing chapter downloads applicable", "Auto mode should be
allowed"). No Qt here: the List, the wanted list and the headless find pass read it alike.

**What it does** is the states' business (:func:`mangalist.states.compute_state`, ``unbundled=``): the chapters after
the newest held (or released) volume's last chapter up to the chapter target become Missing chapters (added to what
the folder already wants: the setting never takes a gap away); the volume side is unchanged. The wanted list then
lists the series under Missing chapters as well, and the find pass and the Download tab treat those chapters like any
chapter series' (the saved Suwayomi source, the series' group, its automatic-downloads mode - Auto included).

**Storage: no schema change.** One ``meta`` row per series that has it on - ``series.unbundled.<series id>`` - like the
Suwayomi choices of a series (``suwayomi.series.<id>``, :class:`mangalist.store.downloads.SuwayomiSettings`): keyed by
the series row, so the setting follows the series when its folder is renamed or carried, and an older build simply
ignores the row. Off = no row (the default for every series). The value records when it was switched on (JSON).
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, Optional, Set

from .store.db import utcnow

_log = logging.getLogger(__name__)

META_PREFIX = "series.unbundled."       # + the series id


class UnbundledChapters:
    """Read and set the Unbundled Chapters setting of library series (by series id) in *store*'s ``meta`` table."""

    def __init__(self, store):
        self.store = store

    def is_on(self, series_id: Optional[int]) -> bool:
        if series_id is None:
            return False
        return self.store.get_meta(f"{META_PREFIX}{int(series_id)}") is not None

    def series_ids(self) -> Set[int]:
        """Every series id with the setting on (one query; the wanted list reads it once per pass)."""
        with self.store.connect() as con:
            rows = con.execute("SELECT key FROM meta WHERE key LIKE ? AND value IS NOT NULL",
                               (META_PREFIX + "%",)).fetchall()
        out: Set[int] = set()
        for row in rows:
            tail = row[0][len(META_PREFIX):]
            if tail.isdigit():
                out.add(int(tail))
        return out

    def set(self, series_ids: Iterable[int], on: bool) -> int:
        """Switch the setting on or off for *series_ids*; how many series changed."""
        ids = sorted({int(i) for i in series_ids})
        changed = 0
        with self.store.connect() as con:
            for sid in ids:
                key = f"{META_PREFIX}{sid}"
                had = con.execute("SELECT 1 FROM meta WHERE key = ? AND value IS NOT NULL", (key,)).fetchone()
                if on and had is None:
                    con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                                "value = excluded.value", (key, json.dumps({"on_since": utcnow()})))
                    changed += 1
                elif not on and had is not None:
                    con.execute("DELETE FROM meta WHERE key = ?", (key,))
                    changed += 1
        _log.info("Unbundled chapters %s for %d series (%d changed)", "on" if on else "off", len(ids), changed)
        return changed


def unbundled_ids(store) -> Set[int]:
    """:meth:`UnbundledChapters.series_ids`, or the empty set when it cannot be read (logged): a broken read must never
    invent gaps - without it every series is compared as before."""
    try:
        return UnbundledChapters(store).series_ids()
    except Exception:  # noqa: BLE001
        _log.warning("Unbundled chapters: the setting could not be read; every series is compared without it",
                     exc_info=True)
        return set()


class UnbundledFolders:
    """The List's view of the setting, by series folder: which shown folders have it on (read once per scan, then
    cached), and switching it for a selection. Qt-free; the window holds one."""

    def __init__(self, store):
        self.store = store
        self._ids: Optional[Set[int]] = None
        self._series: dict = {}         # folder -> series id (None: not scanned into the store yet)

    def reset(self) -> None:
        """Forget what was read (after a scan: folders may have been added, renamed or carried)."""
        self._ids = None
        self._series = {}

    def series_id(self, folder) -> Optional[int]:
        key = str(folder)
        if key not in self._series:
            try:
                row = self.store.series_for_folder(key)
            except Exception:  # noqa: BLE001 - that row is read as off
                _log.debug("Unbundled chapters: no series row for %s", key, exc_info=True)
                row = None
            self._series[key] = row.id if row is not None else None
        return self._series[key]

    def is_on(self, folder) -> bool:
        if self._ids is None:
            self._ids = unbundled_ids(self.store)
        if not self._ids:
            return False                # the default everywhere: no lookup at all
        sid = self.series_id(folder)
        return sid is not None and sid in self._ids

    def all_on(self, folders: Iterable) -> bool:
        """Every one of *folders* (at least one) has the setting on - the row menu's tick."""
        folders = list(folders)
        return bool(folders) and all(self.is_on(f) for f in folders)

    def set(self, folders: Iterable, on: bool) -> int:
        """Switch the setting for the series of *folders* (those not scanned yet are skipped); how many series it
        covers. Raises what the store raises."""
        ids = {sid for sid in (self.series_id(f) for f in folders) if sid is not None}
        if not ids:
            return 0
        UnbundledChapters(self.store).set(ids, on)
        if self._ids is None:
            self._ids = unbundled_ids(self.store)
        else:
            self._ids = (self._ids | ids) if on else (self._ids - ids)
        return len(ids)


__all__ = ["META_PREFIX", "UnbundledChapters", "UnbundledFolders", "unbundled_ids"]
