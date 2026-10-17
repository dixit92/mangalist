"""Unbundled Chapters: the per-series setting that turns a volume series into volumes + the chapters after its last
volume (backlog, decided 2026-10-11; owner: "there should be a setting to convert a volume-only series to a volumes +
unbundled chapters series, with both upgrade and new missing chapter downloads applicable", "Auto mode should be
allowed"). No Qt here: the List, the wanted list and the headless find pass read it alike.

**What it does** is the states' business (:func:`mangalist.states.compute_state`, ``unbundled=``): the chapters after
the newest held (or released) volume's last chapter up to the chapter target become Missing chapters (added to what
the folder already wants: the setting never takes a gap away); the volume side is unchanged, except that a missing
volume of the original edition not out in English, whose chapters it now gets, is kept as an upgrade (owner,
2026-10-16, question 73: "Keep both, treat the volume as an upgrade"). The wanted list then
lists the series under Missing chapters as well, and the find pass and the Download tab treat those chapters like any
chapter series' (the saved Suwayomi source, the series' group, its automatic-downloads mode - Auto included).

**Storage: no schema change.** One ``meta`` row per series that has it on - ``series.unbundled.<series id>`` - like the
Suwayomi choices of a series (``suwayomi.series.<id>``, :class:`mangalist.store.downloads.SuwayomiSettings`): keyed by
the series row, so the setting follows the series when its folder is renamed or carried, and an older build simply
ignores the row. Off = no row (the default for every series). The value records when it was switched on (JSON) and,
where the volume list does not map the series' newest held volume, the owner's **first chapter to get** (owner,
2026-10-15, question 14: asked once, stored per series, editable later): ``{"on_since": ..., "first_chapter": "61",
"for_volume": "7", "first_set": ...}`` - ``for_volume`` is the newest held volume when it was given, so a newer unmapped
volume arriving later makes the states ask again instead of wanting chapters that volume may hold. Switching the
setting off forgets the answer with the row.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, Optional, Set

from .store.db import utcnow

_log = logging.getLogger(__name__)

META_PREFIX = "series.unbundled."       # + the series id


@dataclass(frozen=True)
class UnbundledChoice:
    """A series' Unbundled Chapters setting as the states read it (:func:`mangalist.states.compute_state`'s
    ``unbundled=``; always true - a series without the setting has no choice at all): the owner's first chapter to get
    and the newest held volume it was given for (both None until asked)."""

    first_chapter: Optional[Decimal] = None
    for_volume: Optional[Decimal] = None


def _number(value) -> Optional[Decimal]:
    try:
        d = Decimal(str(value).strip()) if value is not None else None
    except (InvalidOperation, ValueError):
        return None
    return d if d is not None and d.is_finite() and d > 0 else None


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def choice_of(value: Optional[str]) -> UnbundledChoice:
    """The :class:`UnbundledChoice` a ``meta`` value holds (an unreadable value: on, nothing answered - logged)."""
    try:
        data = json.loads(value) if value else {}
    except (TypeError, ValueError):
        _log.warning("Unbundled chapters: unreadable setting %r; read as on without a first chapter", value)
        data = {}
    if not isinstance(data, dict):
        data = {}
    first = _number(data.get("first_chapter"))
    return UnbundledChoice(first, _number(data.get("for_volume")) if first is not None else None)


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

    def choices(self) -> Dict[int, UnbundledChoice]:
        """Every series with the setting on -> its :class:`UnbundledChoice` (one query, like :meth:`series_ids`)."""
        with self.store.connect() as con:
            rows = con.execute("SELECT key, value FROM meta WHERE key LIKE ? AND value IS NOT NULL",
                               (META_PREFIX + "%",)).fetchall()
        out: Dict[int, UnbundledChoice] = {}
        for row in rows:
            tail = row[0][len(META_PREFIX):]
            if tail.isdigit():
                out[int(tail)] = choice_of(row[1])
        return out

    def choice(self, series_id: Optional[int]) -> Optional[UnbundledChoice]:
        """The series' choice (None: the setting is off)."""
        if series_id is None:
            return None
        value = self.store.get_meta(f"{META_PREFIX}{int(series_id)}")
        return None if value is None else choice_of(value)

    def set_first_chapter(self, series_id: int, first_chapter, for_volume) -> bool:
        """Store the owner's first chapter to get for the series (given while *for_volume* was its newest held volume);
        False when the setting is off for it (nothing stored - the answer belongs to the setting)."""
        first, volume = _number(first_chapter), _number(for_volume)
        if first is None:
            raise ValueError(f"not a chapter number: {first_chapter!r}")
        key = f"{META_PREFIX}{int(series_id)}"
        with self.store.connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key = ? AND value IS NOT NULL", (key,)).fetchone()
            if row is None:
                return False
            try:
                data = json.loads(row[0]) if row[0] else {}
            except (TypeError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.update(first_chapter=_plain(first), for_volume=_plain(volume) if volume is not None else None,
                        first_set=utcnow())
            con.execute("UPDATE meta SET value = ? WHERE key = ?", (json.dumps(data), key))
        _log.info("Unbundled chapters: first chapter to get %s for series %d (newest held volume %s)",
                  _plain(first), int(series_id), _plain(volume) if volume is not None else "?")
        return True

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


def unbundled_choices(store) -> Dict[int, UnbundledChoice]:
    """:meth:`UnbundledChapters.choices`, or {} when it cannot be read (logged), as :func:`unbundled_ids`."""
    try:
        return UnbundledChapters(store).choices()
    except Exception:  # noqa: BLE001
        _log.warning("Unbundled chapters: the setting could not be read; every series is compared without it",
                     exc_info=True)
        return {}


class UnbundledFolders:
    """The List's view of the setting, by series folder: which shown folders have it on (read once per scan, then
    cached), and switching it for a selection. Qt-free; the window holds one."""

    def __init__(self, store):
        self.store = store
        self._ids: Optional[Set[int]] = None
        self._series: dict = {}         # folder -> series id (None: not scanned into the store yet)
        self._choices: Optional[Dict[int, UnbundledChoice]] = None

    def reset(self) -> None:
        """Forget what was read (after a scan: folders may have been added, renamed or carried)."""
        self._ids = None
        self._series = {}
        self._choices = None

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

    def choice(self, folder) -> Optional[UnbundledChoice]:
        """The series' choice - what the List's states read (None: off; read once per scan, like :meth:`is_on`)."""
        if not self.is_on(folder):
            return None
        if self._choices is None:
            self._choices = unbundled_choices(self.store)
        return self._choices.get(self.series_id(folder)) or UnbundledChoice()

    def set_first_chapter(self, folder, first_chapter, for_volume) -> bool:
        """:meth:`UnbundledChapters.set_first_chapter` for *folder*'s series (False: not scanned, or the setting is
        off). Raises what the store raises."""
        sid = self.series_id(folder)
        if sid is None:
            return False
        done = UnbundledChapters(self.store).set_first_chapter(sid, first_chapter, for_volume)
        self._choices = None
        return done

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
        self._choices = None
        if self._ids is None:
            self._ids = unbundled_ids(self.store)
        else:
            self._ids = (self._ids | ids) if on else (self._ids - ids)
        return len(ids)


__all__ = ["META_PREFIX", "UnbundledChapters", "UnbundledChoice", "UnbundledFolders", "choice_of", "unbundled_choices",
           "unbundled_ids"]
