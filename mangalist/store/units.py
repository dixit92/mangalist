"""Units: what each archive of a series holds, as the parser reads it (schema + basic CRUD).

Volume / chapter numbers are exact decimal strings, never floats: ``"12"``, ``"12.5"``, ``"3.99"``.
Leading zeros are dropped (``"0003"`` -> ``"3"``) so equal numbers compare equal as text; the file's
own index token is kept as written in ``idx``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List, Optional

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
        for lo, hi, what in ((vf, vt, "volume"), (cf, ct, "chapter")):
            if hi is not None and lo is None:
                raise UnitError(f"a {what} range needs its start")
            if lo is not None and hi is not None and Decimal(hi) < Decimal(lo):
                raise UnitError(f"{what} range {lo}-{hi} runs backwards")
        return Unit(rel_path=rel, kind=self.kind, vol_from=vf, vol_to=vt, ch_from=cf, ch_to=ct,
                    group_name=self.group_name, title=self.title, idx=self.idx, seq=int(self.seq),
                    parser=self.parser, file_size=self.file_size, id=self.id, series_id=self.series_id)


_COLS = ("rel_path", "seq", "kind", "vol_from", "vol_to", "ch_from", "ch_to", "group_name", "title", "idx",
         "parser", "file_size")


class UnitsMixin:
    def list_units(self, series_id: int, rel_path: Optional[str] = None) -> List[Unit]:
        sql = "SELECT * FROM units WHERE series_id = ?"
        args: list = [series_id]
        if rel_path is not None:
            sql += " AND rel_path = ?"
            args.append(rel_path)
        with self.connect() as con:
            rows = con.execute(sql + " ORDER BY rel_path, seq", args).fetchall()
        return [Unit(id=r["id"], series_id=r["series_id"], **{c: r[c] for c in _COLS}) for r in rows]

    def replace_units(self, series_id: int, rel_path: str, units: Iterable[Unit]) -> List[Unit]:
        """Replace everything stored for one archive with *units* (validated first; all or nothing)."""
        norm = [u.normalized() for u in units]
        rel = (rel_path or "").replace("\\", "/").strip("/")
        for i, u in enumerate(norm):
            if u.rel_path != rel:
                raise UnitError(f"unit for {u.rel_path!r} given while replacing {rel!r}")
            u.seq = i
        now = utcnow()
        with self.connect() as con:
            con.execute("DELETE FROM units WHERE series_id = ? AND rel_path = ?", (series_id, rel))
            for u in norm:
                cur = con.execute(
                    f"INSERT INTO units (series_id, {', '.join(_COLS)}, updated_at) VALUES (?{',?' * len(_COLS)}, ?)",
                    (series_id, *[getattr(u, c) for c in _COLS], now))
                u.id, u.series_id = int(cur.lastrowid), series_id
        return norm

    def delete_units(self, series_id: int, rel_path: Optional[str] = None) -> int:
        with self.connect() as con:
            if rel_path is None:
                cur = con.execute("DELETE FROM units WHERE series_id = ?", (series_id,))
            else:
                cur = con.execute("DELETE FROM units WHERE series_id = ? AND rel_path = ?", (series_id, rel_path))
        return cur.rowcount
