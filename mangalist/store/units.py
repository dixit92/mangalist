"""Units: what each archive of a series holds, as the parser reads it (schema + basic CRUD).

Volume / chapter numbers are exact decimal strings, never floats: ``"12"``, ``"12.5"``, ``"3.99"``.
Leading zeros are dropped (``"0003"`` -> ``"3"``) so equal numbers compare equal as text; the file's
own index token is kept as written in ``idx``. A unit whose kind the name does not state (a bare
``01.cbz`` before the series' "volumes or chapters?" answer) has kind ``unknown`` and its number in
``num_from`` / ``num_to`` (schema 3).

A scan stores every archive's units through :meth:`UnitsMixin.sync_units`: one transaction per root,
archives whose units changed are rewritten, archives that are gone lose their rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .db import utcnow
from .schema import UNIT_KINDS

_DECIMAL = re.compile(r"^\d+(?:\.\d+)?$")


class UnitError(ValueError):
    pass


def exact_number(value) -> Optional[str]:
    """The canonical decimal string of *value* (str or int; floats are refused - they are not exact)."""
    if value is None:
        return None
    if isinstance(value, float):
        raise UnitError(f"unit numbers must be exact (str / int), got the float {value!r}")
    text = str(value).strip()
    if not _DECIMAL.match(text):
        raise UnitError(f"not a non-negative decimal number: {value!r}")
    whole, _, frac = text.partition(".")
    whole = whole.lstrip("0") or "0"
    return f"{whole}.{frac}" if frac else whole


@dataclass
class Unit:
    rel_path: str                    # the archive, relative to the series folder ('/' separators)
    kind: str                        # schema.UNIT_KINDS
    vol_from: Optional[str] = None
    vol_to: Optional[str] = None
    ch_from: Optional[str] = None
    ch_to: Optional[str] = None
    num_from: Optional[str] = None   # kind 'unknown' only: the bare number the name gives
    num_to: Optional[str] = None
    group_name: Optional[str] = None
    title: Optional[str] = None
    idx: Optional[str] = None
    seq: int = 0
    parser: Optional[str] = None
    file_size: Optional[int] = None
    id: Optional[int] = None
    series_id: Optional[int] = None

    def normalized(self) -> "Unit":
        if self.kind not in UNIT_KINDS:
            raise UnitError(f"unknown unit kind {self.kind!r} (one of {', '.join(UNIT_KINDS)})")
        rel = (self.rel_path or "").replace("\\", "/").strip("/")
        if not rel:
            raise UnitError("a unit needs its archive path")
        vf, vt = exact_number(self.vol_from), exact_number(self.vol_to)
        cf, ct = exact_number(self.ch_from), exact_number(self.ch_to)
        nf, nt = exact_number(self.num_from), exact_number(self.num_to)
        for lo, hi, what in ((vf, vt, "volume"), (cf, ct, "chapter"), (nf, nt, "number")):
            if hi is not None and lo is None:
                raise UnitError(f"a {what} range needs its start")
            if lo is not None and hi is not None and Decimal(hi) < Decimal(lo):
                raise UnitError(f"{what} range {lo}-{hi} runs backwards")
        return Unit(rel_path=rel, kind=self.kind, vol_from=vf, vol_to=vt, ch_from=cf, ch_to=ct,
                    num_from=nf, num_to=nt, group_name=self.group_name, title=self.title, idx=self.idx, seq=int(self.seq),
                    parser=self.parser, file_size=self.file_size, id=self.id, series_id=self.series_id)


_COLS = ("rel_path", "seq", "kind", "vol_from", "vol_to", "ch_from", "ch_to", "num_from", "num_to", "group_name",
         "title", "idx", "parser", "file_size")
# What decides whether an archive's stored units changed (ids and timestamps left out).
_SAME = tuple(c for c in _COLS if c != "rel_path")


def _unit_of(r) -> Unit:
    return Unit(id=r["id"], series_id=r["series_id"], **{c: r[c] for c in _COLS})


def _prepare(rel_path: str, units: Iterable[Unit]) -> Tuple[str, List[Unit]]:
    norm = [u.normalized() for u in units]
    rel = (rel_path or "").replace("\\", "/").strip("/")
    for i, u in enumerate(norm):
        if u.rel_path != rel:
            raise UnitError(f"unit for {u.rel_path!r} given while replacing {rel!r}")
        u.seq = i
    return rel, norm


@dataclass
class UnitSync:
    """What :meth:`UnitsMixin.sync_units` did, counted in archives: rewritten (new or changed),
    removed (gone from the folder), unchanged (left alone)."""

    rewritten: int = 0
    removed: int = 0
    unchanged: int = 0


class UnitsMixin:
    def list_units(self, series_id: int, rel_path: Optional[str] = None) -> List[Unit]:
        sql = "SELECT * FROM units WHERE series_id = ?"
        args: list = [series_id]
        if rel_path is not None:
            sql += " AND rel_path = ?"
            args.append(rel_path)
        with self.connect() as con:
            rows = con.execute(sql + " ORDER BY rel_path, seq", args).fetchall()
        return [_unit_of(r) for r in rows]

    def replace_units(self, series_id: int, rel_path: str, units: Iterable[Unit]) -> List[Unit]:
        """Replace everything stored for one archive with *units* (validated first; all or nothing)."""
        rel, norm = _prepare(rel_path, units)
        now = utcnow()
        with self.connect() as con:
            con.execute("DELETE FROM units WHERE series_id = ? AND rel_path = ?", (series_id, rel))
            _insert(con, series_id, norm, now)
        return norm

    def sync_units(self, by_series: Mapping[int, Mapping[str, Sequence[Unit]]]) -> UnitSync:
        """Make the stored units of each series in *by_series* exactly ``{archive rel path: units}``
        (one transaction; everything validated before anything is written).

        An archive whose units are unchanged is left alone; a changed one is rewritten; an archive of
        the series that is not in its mapping (gone from the folder) loses its rows. Series not in
        *by_series* are not touched.
        """
        prepared: Dict[int, Dict[str, List[Unit]]] = {}
        for sid, archives in by_series.items():
            per: Dict[str, List[Unit]] = {}
            for rel_path, units in archives.items():
                rel, norm = _prepare(rel_path, units)
                per[rel] = norm
            prepared[int(sid)] = per
        out = UnitSync()
        now = utcnow()
        with self.connect() as con:
            for sid, archives in prepared.items():
                stored: Dict[str, List[tuple]] = {}
                for r in con.execute("SELECT * FROM units WHERE series_id = ? ORDER BY rel_path, seq", (sid,)):
                    stored.setdefault(r["rel_path"], []).append(tuple(r[c] for c in _SAME))
                for rel in stored.keys() - archives.keys():
                    con.execute("DELETE FROM units WHERE series_id = ? AND rel_path = ?", (sid, rel))
                    out.removed += 1
                for rel, norm in archives.items():
                    if rel in stored and stored[rel] == [tuple(getattr(u, c) for c in _SAME) for u in norm]:
                        out.unchanged += 1
                        continue
                    if rel in stored:
                        con.execute("DELETE FROM units WHERE series_id = ? AND rel_path = ?", (sid, rel))
                    _insert(con, sid, norm, now)
                    out.rewritten += 1
        return out

    def delete_units(self, series_id: int, rel_path: Optional[str] = None) -> int:
        with self.connect() as con:
            if rel_path is None:
                cur = con.execute("DELETE FROM units WHERE series_id = ?", (series_id,))
            else:
                cur = con.execute("DELETE FROM units WHERE series_id = ? AND rel_path = ?", (series_id, rel_path))
        return cur.rowcount


def _insert(con, series_id: int, units: List[Unit], now: str) -> None:
    for u in units:
        cur = con.execute(
            f"INSERT INTO units (series_id, {', '.join(_COLS)}, updated_at) VALUES (?{',?' * len(_COLS)}, ?)",
            (series_id, *[getattr(u, c) for c in _COLS], now))
        u.id, u.series_id = int(cur.lastrowid), series_id
