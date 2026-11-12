"""Which chapters a held volume contains when the volume list does not say (pure rules + one database read; no Qt).

The rescan states (:func:`mangalist.states.compute_state`) learn which chapters a held volume collects from MangaPixer's
volume list (MangaDex's volume -> chapter map, through MangaPixer's export). When the source loses that map - MangaDex
took the series' chapters down and its aggregate came back empty - a mixed folder (volumes 1-9, loose chapters after)
whose list still maps volumes 1-6 counted the chapters of volumes 7-9 as missing chapters: the find pass asked for them
and the Download tab's chapters panel sent them, although the volumes hold them (owner, 2026-11-06: "fix the gap logic so
it knows what volumes 7-9 contain." -> "Yes").

**The evidence** (:func:`load_claims`, read once per wanted list / per scan in the window): what the series' own records
say about which volume a chapter belongs to - each a *claim* "chapter(s) *lo*-*hi* are in volume *v*":

1. **The chapter files' own names**, held now or ever held by the series: a chapter file named with its volume
   (``Ch. 0051.00 Vol. 007``) - the source's own chapter -> volume tag, written when the chapter was downloaded. Files
   held now are read from the scan's ``units`` rows; files that are gone (moved to the holding folder by an upgrade,
   set aside, deleted) from their ``archives`` row, which stays (status ``missing``) and is parsed again by name.
2. **A filed download's replacement batch** (``replacements`` with a download behind it, ``download_id`` > 0): each
   chapter file the filed volume replaced was recorded with that volume, by the mapped volume list of the time.

Not evidence: a batch set aside by hand (``download_id`` < 0) - it was made by MangaPixer's *estimated* ranges, which
are a guess from the folder's own files (on the owner's library, 2026-10-12, such a batch put chapters 76-86 in volume
9; the volumes hold 51-75), nor a batch the owner restored or declined; MangaPixer's estimated ranges themselves; any
guess from chapters per volume.

**The rule** (:func:`held_volume_contents`) - conservative, it never invents a gap and never hides one the records do
not account for:

- Only a HELD volume the volume list does not map gets recorded contents (the list wins wherever it maps a volume).
- A volume's recorded contents are the span from the lowest to the highest chapter its claims name (a volume collects
  consecutive chapters, as the list's own ``from``-``to`` ranges are read).
- The spans must fit: ordered by volume number and not overlapping any other volume's span - the list's mapped ranges
  and the other recorded spans alike. A span that does not fit (a chapter tagged with two volumes, numbering that
  restarts per volume) is dropped, and so is the recorded span it collides with: that volume stays unknown, as before.
- In the states the recorded contents only ever take chapters OFF the missing lists (``_Held.holds_chapter``): which
  rules apply, where the chapter comparison starts and every volume gap stay what the list alone makes them. A folder
  whose numbering restarts (season subfolders) or that holds a declared edition (an omnibus) uses none.

So a series holding volumes 1-9 and chapters 76+, whose list maps volumes 1-6 only and whose chapter files 51-75 (named
``Vol. 007`` ... ``Vol. 009``) went to the holding folder, wants nothing below 76 - while chapters 76-86, which no volume
claims, stay missing if they go too.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .knowledge import fmt_num, to_decimal

_log = logging.getLogger(__name__)

SOURCE_NAME = "name"            # a chapter file's own name says its volume (held now or before)
SOURCE_FILED = "filed"          # a filed download's replacement batch recorded it with that volume

# Replacement batches whose recorded volumes stand: the owner did not undo them (restored) or refuse them (declined).
TRUSTED_BATCH_STATUS = ("pending", "held", "purged", "deleted")


@dataclass(frozen=True)
class Claim:
    """Chapters *lo*-*hi* (exact, inclusive; ``lo == hi`` for one chapter) are in volume *volume*, says *source*."""

    volume: Decimal
    lo: Decimal
    hi: Decimal
    source: str = SOURCE_NAME


def claim(volume: Any, lo: Any, hi: Any = None, source: str = SOURCE_NAME) -> Optional[Claim]:
    """A :class:`Claim` from exact numbers (strings / ints / Decimals), or None when one is not a number."""
    v, a = to_decimal(volume), to_decimal(lo)
    b = to_decimal(hi) if hi is not None else a
    if v is None or a is None or b is None or v < 0 or a < 0 or b < 0:
        return None
    return Claim(v, min(a, b), max(a, b), source)


Span = Tuple[Decimal, Decimal]


def held_volume_contents(claims: Iterable[Claim], held_volumes: Iterable[Any],
                         mapped: Mapping[Any, Span]) -> Dict[Decimal, Span]:
    """``{held volume: (first, last chapter)}`` for the held volumes the volume list does not map, by *claims* (see the
    module docstring's rule). *mapped*: ``{volume: (from, to)}`` the list maps (held or not)."""
    held = {d for d in (to_decimal(v) for v in held_volumes) if d is not None}
    listed: Dict[Decimal, Span] = {}
    for v, (lo, hi) in mapped.items():
        dv, dlo, dhi = to_decimal(v), to_decimal(lo), to_decimal(hi)
        if dv is not None and dlo is not None and dhi is not None and dlo <= dhi:
            listed[dv] = (dlo, dhi)
    recorded: Dict[Decimal, Span] = {}
    for c in claims:
        if c is None or c.volume in listed:
            continue
        lo, hi = recorded.get(c.volume, (c.lo, c.hi))
        recorded[c.volume] = (min(lo, c.lo), max(hi, c.hi))
    if not recorded:
        return {}
    every = {**listed, **recorded}
    bad: Set[Decimal] = set()
    for v, (lo, hi) in recorded.items():
        for w, (wlo, whi) in every.items():
            if w == v:
                continue
            if (w < v and whi >= lo) or (w > v and wlo <= hi):
                bad.add(v)
                if w in recorded:
                    bad.add(w)
    return {v: span for v, span in sorted(recorded.items()) if v in held and v not in bad}


def contents_text(contents: Mapping[Decimal, Span]) -> str:
    """``"volume 7: chapters 51-58, volume 8: chapters 59-66"`` (exact digits)."""
    parts = []
    for v, (lo, hi) in sorted(contents.items()):
        parts.append(f"volume {fmt_num(v)}: chapter{'s' if lo != hi else ''} "
                     f"{fmt_num(lo)}{'-' + fmt_num(hi) if lo != hi else ''}")
    return ", ".join(parts)


# --- the series' records ---------------------------------------------------------------------------------------


def _name_claim(name: str, context: Any, size: Optional[int] = None) -> Optional[Claim]:
    """The claim a chapter file's name makes (``Ch. 0051.00 Vol. 007``), parsed as the scanner parses it; None for a
    name without its volume, a volume range, an extra or anything that is not a chapter file."""
    from .parsing import parse_name

    try:
        parsed = parse_name(name, context, file_size=size)
    except Exception:  # noqa: BLE001 - an unreadable old name is no evidence
        return None
    if str(getattr(parsed.kind, "value", parsed.kind)) != "chapter" or parsed.is_extra:
        return None
    if parsed.volume is None or parsed.chapter is None or parsed.volume.start != parsed.volume.end:
        return None
    return claim(parsed.volume.start, parsed.chapter.start, parsed.chapter.end, SOURCE_NAME)


def _batch_claims(files_json: Optional[str]) -> List[Claim]:
    """The claims of one replacement batch's ``files``: each file's ``chapters`` (``"12"``, ``"1-5"``, ``"1-3, 5"``)
    in its one ``volume`` (a file across two volumes says nothing about either alone)."""
    try:
        files = json.loads(files_json or "[]")
    except (TypeError, ValueError):
        return []
    out: List[Claim] = []
    for f in files if isinstance(files, list) else ():
        if not isinstance(f, Mapping):
            continue
        volume = str(f.get("volume") or "").strip()
        if not volume or "," in volume:
            continue
        numbers = [to_decimal(p) for p in str(f.get("chapters") or "").replace(",", "-").split("-")]
        numbers = [n for n in numbers if n is not None]
        if numbers:
            c = claim(volume, min(numbers), max(numbers), SOURCE_FILED)
            if c is not None:
                out.append(c)
    return out


def load_claims(db, series_ids: Optional[Iterable[int]] = None) -> Dict[int, Tuple[Claim, ...]]:
    """``{series id: its claims}`` from the library database (*series_ids* None: every series): the chapter files held
    now (the scan's ``units``), the chapter files that are gone (their ``archives`` rows, parsed by name with the
    series' context as the scanner parses) and the filed downloads' replacement batches. Read-only."""
    from .classifier import parse_folder_name
    from .scanner import _parse_context, _root_schemes

    wanted = None if series_ids is None else {int(s) for s in series_ids}
    out: Dict[int, List[Claim]] = {}

    def add(sid: Any, c: Optional[Claim]) -> None:
        if c is not None and sid is not None and (wanted is None or sid in wanted):
            out.setdefault(int(sid), []).append(c)

    roots = {r.id: r for r in db.list_roots()}
    with db.connect() as con:
        for r in con.execute("SELECT series_id, vol_from, vol_to, ch_from, ch_to FROM units"
                             " WHERE kind = 'chapter' AND vol_from IS NOT NULL AND ch_from IS NOT NULL"):
            if r["vol_to"] is None or to_decimal(r["vol_to"]) == to_decimal(r["vol_from"]):
                add(r["series_id"], claim(r["vol_from"], r["ch_from"], r["ch_to"], SOURCE_NAME))
        series = {r["id"]: r for r in con.execute("SELECT id, root_id, rel_path, kind_hint FROM series")}
        gone = con.execute("SELECT series_id, rel_path, size FROM archives"
                           " WHERE status = 'missing' AND series_id IS NOT NULL").fetchall()
        marks = ",".join("?" * len(TRUSTED_BATCH_STATUS))
        batches = con.execute(f"SELECT series_id, files FROM replacements WHERE download_id > 0"
                              f" AND series_id IS NOT NULL AND status IN ({marks})", TRUSTED_BATCH_STATUS).fetchall()
    contexts: Dict[int, Any] = {}
    for a in gone:
        sid = a["series_id"]
        if wanted is not None and sid not in wanted:
            continue
        row = series.get(sid)
        if row is None:
            continue
        if sid not in contexts:
            root = roots.get(row["root_id"])
            title, _ = parse_folder_name(row["rel_path"].rsplit("/", 1)[-1])
            contexts[sid] = _parse_context(title, row["kind_hint"], _root_schemes(root) if root is not None else ())
        add(sid, _name_claim(a["rel_path"].rsplit("/", 1)[-1], contexts[sid], a["size"]))
    for b in batches:
        for c in _batch_claims(b["files"]):
            add(b["series_id"], c)
    return {sid: tuple(cs) for sid, cs in out.items()}


def safe_load_claims(db, series_ids: Optional[Iterable[int]] = None) -> Dict[int, Tuple[Claim, ...]]:
    """:func:`load_claims`, or no claims at all when the records cannot be read (logged): the states are then what the
    volume list alone makes them - never a gap more."""
    try:
        return load_claims(db, series_ids)
    except Exception:  # noqa: BLE001
        _log.warning("Volume contents: the series' records could not be read; the volume list alone applies",
                     exc_info=True)
        return {}


class ClaimsByFolder:
    """The window's view of :func:`load_claims`, by series folder: read once per scan (then cached), Qt-free - the
    states' worker thread reads it as the List computes. :meth:`reset` after a scan or a batch."""

    def __init__(self, store):
        self.store = store
        self._claims: Optional[Dict[int, Tuple[Claim, ...]]] = None
        self._series: Dict[str, Optional[int]] = {}

    def reset(self) -> None:
        self._claims = None
        self._series = {}

    def of(self, folder) -> Tuple[Claim, ...]:
        claims = self._claims
        if claims is None:
            claims = self._claims = safe_load_claims(self.store)
        if not claims:
            return ()
        key = str(folder)
        if key not in self._series:
            try:
                row = self.store.series_for_folder(key)
            except Exception:  # noqa: BLE001 - not scanned into the store: no records
                row = None
            self._series[key] = row.id if row is not None else None
        sid = self._series[key]
        return claims.get(sid, ()) if sid is not None else ()


__all__ = [
    "Claim", "ClaimsByFolder", "SOURCE_FILED", "SOURCE_NAME", "claim", "contents_text", "held_volume_contents",
    "load_claims", "safe_load_claims",
]
