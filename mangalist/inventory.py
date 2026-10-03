"""Held units of one series: pure Python, no Qt, no I/O, exact decimals (never float).

Product Design 2 ("Inventory"): every archive is parsed into units by the layered parser; the series'
held units are the merge, where a volume that collects chapters counts once - known from a volume
list (MangaPixer's export ``volumes.items``) when one is given.

Interface (consumed by the rescan-states lane)
----------------------------------------------

``compute_inventory(units, volume_list=None) -> Inventory``
    From a series' unit rows (:class:`mangalist.store.units.Unit`, as stored by a scan or built with
    :func:`units_from_parsed`).
``inventory_of_entry(entry, volume_list=None) -> Inventory``
    The same for a scanner ``MangaEntry`` (its :attr:`~mangalist.models.MangaEntry.inventory_files`);
    ``entry.inventory(volume_list)`` calls it (cached without a list).
``units_from_parsed(rel_path, parsed, file_size=None) -> List[Unit]``
    The unit rows of one archive from its :class:`~mangalist.parsing.ParsedName`.
``units_of_entry(entry) -> Dict[str, List[Unit]]``
    ``{archive path relative to the series folder ('/' separators): rows}`` for every archive.

Volume list (optional input), MangaPixer's ``volumes.items`` shape::

    [{"volume": "5", "chapters": {"from": "38", "to": "46"}}, {"volume": "6", "chapters": None}, ...]

Numbers are strings (or ints / Decimals); a float is refused (``TypeError``) because it is not exact.
Other keys are ignored. An item without a readable volume is skipped (noted in ``notes``); a missing
or unreadable ``chapters`` means the coverage is unknown. A ``to`` without ``from`` (or the reverse)
covers that one chapter; ``from`` > ``to`` is read as unknown.

``Inventory`` (frozen dataclass)::

    volumes                   Tuple[Decimal, ...]   whole volumes held (volume archives, the volume
                                                    part of "v10 + 085-086"); sorted, distinct
    chapters                  Tuple[Decimal, ...]   chapters held as chapter archives (incl. the loose
                                                    chapters of "v10 + 085-086"); sorted, distinct.
                                                    Ranges ("c010-012") are expanded to their whole
                                                    numbers plus both ends ("12.5-14" -> 12.5, 13, 14).
    coverage                  Dict[Decimal, Optional[Span]]
                                                    per held volume: the chapters it collects (from the
                                                    volume list), None = unknown; empty without a list
    covered                   Tuple[Span, ...]      chapters collected by held volumes (overlaps merged).
                                                    A covered span is an interval: 45.5 is in 38-46.
    volumes_unknown_coverage  Tuple[Decimal, ...]   held volumes whose chapters are not known (no list,
                                                    not in the list, or "chapters": null)
    loose_in_volumes          Tuple[Decimal, ...]   chapter archives a held volume also collects
                                                    (counted once)
    extras                    Tuple[str, ...]       archives of extras: a chapter without its own number
                                                    ("Ch. Extra", "Omake", an FMD2 body with no Ch.)
    unknown                   Tuple[str, ...]       archives of unknown kind: bare numbers the series has
                                                    no "volumes or chapters?" answer for, unreadable names
    highest_volume            Optional[Decimal]     highest held volume
    highest_chapter           Optional[Decimal]     highest chapter held: a chapter archive or the end of
                                                    a covered span
    files                     Dict[Tuple[str, Decimal], Tuple[str, ...]]
                                                    every unit ("volume" | "chapter", number) -> the
                                                    archives holding it (a file once per unit)
    duplicates                Dict[Tuple[str, Decimal], Tuple[str, ...]]
                                                    the units of ``files`` held by 2+ archives
    volume_list_given         bool
    notes                     Tuple[str, ...]       diagnostics (skipped list items, capped ranges)

    has_volume(n) / has_chapter(n)      n: str | int | Decimal; has_chapter is True for a chapter
                                        archive with that number or a covered span containing it
    volume_ranges / chapter_ranges      Tuple[Span, ...] (runs of whole numbers; fractions alone)
    held_chapter_ranges                 chapter archives + covered spans, for display
    volumes_text / chapters_text / held_chapters_text   "1-3, 5" style strings (exact digits)

``Span(start, end)`` - Decimal, inclusive; ``str()`` gives ``"12.5"`` or ``"1-3"``.

Equal numbers compare equal as Decimals (``12.50`` == ``12.5``); the first spelling seen is kept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from .store.units import Unit

Number = Union[str, int, Decimal]

# A range longer than this is not expanded (only its two ends are held) - a misread, not a library.
MAX_RANGE_SPAN = 5000


# --- numbers and spans ---------------------------------------------------------------------------

def to_number(value: Any) -> Optional[Decimal]:
    """An exact unit number: ``"0012.50"`` -> ``Decimal("12.50")``; None for None / empty / not a
    non-negative number. A float raises ``TypeError`` (not exact)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        raise TypeError(f"unit numbers must be exact (str / int / Decimal), got the float {value!r}")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            d = Decimal(text)
        except InvalidOperation:
            return None
    if not d.is_finite() or d < 0:
        return None
    whole, _, frac = format(d, "f").partition(".")
    whole = whole.lstrip("0") or "0"
    return Decimal(f"{whole}.{frac}") if frac else Decimal(whole)


def _plain(d: Decimal) -> str:
    return format(d, "f")


def _is_whole(d: Decimal) -> bool:
    return d == d.to_integral_value()


@dataclass(frozen=True, order=True)
class Span:
    """An inclusive range of unit numbers (``start == end`` for one unit)."""

    start: Decimal
    end: Decimal

    def __contains__(self, n: object) -> bool:
        d = n if isinstance(n, Decimal) else to_number(n)
        return d is not None and self.start <= d <= self.end

    def __str__(self) -> str:
        return _plain(self.start) if self.start == self.end else f"{_plain(self.start)}-{_plain(self.end)}"


def ranges(numbers: Iterable[Decimal]) -> Tuple[Span, ...]:
    """Runs of consecutive whole numbers as spans; a fractional number is a span of its own
    (``1, 2, 3, 3.5, 4`` -> ``1-4, 3.5``). Sorted by start."""
    distinct = sorted(set(numbers))
    out: List[Span] = []
    run: Optional[List[Decimal]] = None
    for n in (d for d in distinct if _is_whole(d)):
        if run is not None and n == run[1] + 1:
            run[1] = n
        else:
            if run is not None:
                out.append(Span(run[0], run[1]))
            run = [n, n]
    if run is not None:
        out.append(Span(run[0], run[1]))
    out.extend(Span(d, d) for d in distinct if not _is_whole(d))
    return tuple(sorted(out))


def format_spans(spans: Iterable[Span]) -> str:
    """``"1-3, 5, 12.5"``."""
    return ", ".join(str(s) for s in spans)


def expand(start: Decimal, end: Optional[Decimal] = None) -> Tuple[Tuple[Decimal, ...], bool]:
    """The unit numbers a range holds: both ends plus the whole numbers between
    (``10-12`` -> 10, 11, 12; ``12.5-14`` -> 12.5, 13, 14). Second value: False when the range was too
    long to expand (only its ends are returned)."""
    if end is None or end == start:
        return (start,), True
    lo, hi = (start, end) if start <= end else (end, start)
    if hi - lo > MAX_RANGE_SPAN:
        return (lo, hi), False
    out = [lo]
    w = Decimal(math.floor(lo) + 1)
    while w < hi:
        out.append(w)
        w += 1
    out.append(hi)
    return tuple(out), True


def _merge_intervals(spans: Iterable[Span]) -> Tuple[Span, ...]:
    out: List[Span] = []
    for s in sorted(spans):
        if out and s.start <= out[-1].end:
            if s.end > out[-1].end:
                out[-1] = Span(out[-1].start, s.end)
        else:
            out.append(s)
    return tuple(out)


def _merge_display(spans: Iterable[Span]) -> Tuple[Span, ...]:
    """Overlapping spans merged, and whole-number spans that touch (``1-3`` + ``4-6``)."""
    out: List[Span] = []
    for s in sorted(spans):
        if out and (s.start <= out[-1].end
                    or (_is_whole(s.start) and _is_whole(out[-1].end) and s.start == out[-1].end + 1)):
            if s.end > out[-1].end:
                out[-1] = Span(out[-1].start, s.end)
        else:
            out.append(s)
    return tuple(out)


# --- the volume list -----------------------------------------------------------------------------

def read_volume_list(items: Optional[Iterable[Any]]) -> Tuple[Dict[Decimal, Optional[Span]], List[str]]:
    """``{volume: chapters it collects (None = unknown)}`` from MangaPixer's ``volumes.items`` shape,
    plus notes about skipped items. The first item of a volume wins."""
    out: Dict[Decimal, Optional[Span]] = {}
    notes: List[str] = []
    if items is None:
        return out, notes
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            notes.append(f"volume list item {i} is not an object; skipped")
            continue
        vol = to_number(item.get("volume"))
        if vol is None:
            notes.append(f"volume list item {i} has no readable volume; skipped")
            continue
        if vol in out:
            notes.append(f"volume {_plain(vol)} listed twice; the first item kept")
            continue
        out[vol] = _coverage(item.get("chapters"))
    return out, notes


def _coverage(chapters: Any) -> Optional[Span]:
    if not isinstance(chapters, Mapping):
        return None
    lo, hi = to_number(chapters.get("from")), to_number(chapters.get("to"))
    if lo is None and hi is None:
        return None
    lo = hi if lo is None else lo
    hi = lo if hi is None else hi
    if hi < lo:
        return None
    return Span(lo, hi)


# --- the inventory -------------------------------------------------------------------------------

UnitKey = Tuple[str, Decimal]   # ("volume" | "chapter", number)


@dataclass(frozen=True)
class Inventory:
    """A series' held units - see the module docstring for every field."""

    volumes: Tuple[Decimal, ...] = ()
    chapters: Tuple[Decimal, ...] = ()
    coverage: Dict[Decimal, Optional[Span]] = field(default_factory=dict)
    covered: Tuple[Span, ...] = ()
    volumes_unknown_coverage: Tuple[Decimal, ...] = ()
    loose_in_volumes: Tuple[Decimal, ...] = ()
    extras: Tuple[str, ...] = ()
    unknown: Tuple[str, ...] = ()
    highest_volume: Optional[Decimal] = None
    highest_chapter: Optional[Decimal] = None
    files: Dict[UnitKey, Tuple[str, ...]] = field(default_factory=dict)
    duplicates: Dict[UnitKey, Tuple[str, ...]] = field(default_factory=dict)
    volume_list_given: bool = False
    notes: Tuple[str, ...] = field(default=(), compare=False)

    def has_volume(self, n: Number) -> bool:
        d = to_number(n)
        return d is not None and d in set(self.volumes)

    def has_chapter(self, n: Number) -> bool:
        d = to_number(n)
        if d is None:
            return False
        return d in set(self.chapters) or any(d in s for s in self.covered)

    @property
    def volume_ranges(self) -> Tuple[Span, ...]:
        return ranges(self.volumes)

    @property
    def chapter_ranges(self) -> Tuple[Span, ...]:
        return ranges(self.chapters)

    @property
    def held_chapter_ranges(self) -> Tuple[Span, ...]:
        return _merge_display(self.chapter_ranges + self.covered)

    @property
    def volumes_text(self) -> str:
        return format_spans(self.volume_ranges)

    @property
    def chapters_text(self) -> str:
        return format_spans(self.chapter_ranges)

    @property
    def held_chapters_text(self) -> str:
        return format_spans(self.held_chapter_ranges)

    @property
    def is_empty(self) -> bool:
        return not (self.volumes or self.chapters or self.extras or self.unknown)


def compute_inventory(units: Iterable[Unit], volume_list: Optional[Iterable[Any]] = None) -> Inventory:
    """A series' :class:`Inventory` from its unit rows (any order), optionally merged with a volume
    list (MangaPixer's ``volumes.items`` shape, see the module docstring)."""
    # Insertion-ordered sets (dict keys): an archive is listed once per unit.
    files: Dict[UnitKey, Dict[str, None]] = {}
    extras: Dict[str, None] = {}
    unknown: Dict[str, None] = {}
    notes: List[str] = []

    def hold(kind: str, lo: Optional[str], hi: Optional[str], rel: str) -> bool:
        start = to_number(lo)
        if start is None:
            return False
        nums, whole = expand(start, to_number(hi))
        if not whole:
            notes.append(f"{rel}: {kind} range {lo}-{hi} too long; only its ends counted")
        for n in nums:
            files.setdefault((kind, n), {})[rel] = None
        return True

    for u in units:
        rel = u.rel_path
        if u.kind == "volume":
            if not hold("volume", u.vol_from, u.vol_to, rel):
                unknown[rel] = None
        elif u.kind == "chapter":
            if not hold("chapter", u.ch_from, u.ch_to, rel):
                extras[rel] = None
        elif u.kind in ("extra", "oneshot"):
            extras[rel] = None
        else:
            unknown[rel] = None

    # Keep the first spelling of each number ("12.50" vs "12.5" are one unit).
    volumes = tuple(sorted(n for k, n in files if k == "volume"))
    chapters = tuple(sorted(n for k, n in files if k == "chapter"))

    listed, list_notes = read_volume_list(volume_list)
    notes.extend(list_notes)
    coverage: Dict[Decimal, Optional[Span]] = {}
    if volume_list is not None:
        for v in volumes:
            coverage[v] = listed.get(v)
    covered = _merge_intervals(s for s in coverage.values() if s is not None)
    unknown_cov = tuple(v for v in volumes if coverage.get(v) is None)
    loose_in = tuple(c for c in chapters if any(c in s for s in covered))

    ch_tops = list(chapters[-1:]) + [s.end for s in covered]
    frozen_files = {k: tuple(v) for k, v in sorted(files.items())}
    return Inventory(
        volumes=volumes, chapters=chapters, coverage=coverage, covered=covered,
        volumes_unknown_coverage=unknown_cov, loose_in_volumes=loose_in,
        extras=tuple(extras), unknown=tuple(unknown),
        highest_volume=volumes[-1] if volumes else None,
        highest_chapter=max(ch_tops) if ch_tops else None,
        files=frozen_files, duplicates={k: v for k, v in frozen_files.items() if len(v) > 1},
        volume_list_given=volume_list is not None, notes=tuple(notes))


# --- from parser results -------------------------------------------------------------------------

def _kind(parsed: Any) -> str:
    return str(getattr(parsed.kind, "value", parsed.kind))


def units_from_parsed(rel_path: str, parsed: Any, file_size: Optional[int] = None) -> List[Unit]:
    """The unit rows of one archive (``rel_path`` relative to the series folder) from its
    :class:`~mangalist.parsing.ParsedName`:

    - volume: one ``volume`` row (``vol_from`` / ``vol_to``);
    - chapter: one ``chapter`` row (``ch_from`` / ``ch_to``; ``vol_*`` = the volume it belongs to),
      or one ``extra`` row when the chapter has no number;
    - both (``v10 + 085-086``): a ``volume`` row and a ``chapter`` row;
    - unknown: one ``unknown`` row, with a bare number in ``num_from`` / ``num_to``.

    Numbers are exact decimal strings; ``vol_to`` / ``ch_to`` equal the start for a single unit.
    """
    rel = str(rel_path).replace("\\", "/").strip("/")
    common = dict(rel_path=rel, group_name=parsed.group, title=parsed.title,
                  idx=None if parsed.index is None else str(parsed.index),
                  parser=str(getattr(parsed.layer, "value", parsed.layer)), file_size=file_size)

    def rng(r) -> Tuple[Optional[str], Optional[str]]:
        return (None, None) if r is None else (_plain(r.start), _plain(r.end))

    kind = _kind(parsed)
    vf, vt = rng(parsed.volume)
    cf, ct = rng(parsed.chapter)
    if kind == "volume" and vf is not None:
        return [Unit(kind="volume", vol_from=vf, vol_to=vt, **common)]
    if kind == "both" and (vf is not None or cf is not None):
        out = []
        if vf is not None:
            out.append(Unit(kind="volume", vol_from=vf, vol_to=vt, **common))
        if cf is not None:
            out.append(Unit(kind="chapter", ch_from=cf, ch_to=ct, **common))
        return out
    if kind == "chapter":
        if cf is None:
            return [Unit(kind="extra", vol_from=vf, vol_to=vt, **common)]
        return [Unit(kind="chapter", vol_from=vf, vol_to=vt, ch_from=cf, ch_to=ct, **common)]
    nf, nt = rng(parsed.number)
    return [Unit(kind="unknown", num_from=nf, num_to=nt, **common)]


def _rel(path: Path, folder: Path) -> str:
    try:
        return Path(path).relative_to(folder).as_posix()
    except ValueError:
        return Path(path).name


def _parsed_of(hit: Any, kind_hint: Optional[str]) -> Any:
    if hit.parsed is not None:
        return hit.parsed
    from .parsing import ParseContext, parse_name

    return parse_name(Path(hit.path).name, ParseContext(kind_hint=kind_hint), file_size=hit.size)


def units_of_entry(entry: Any) -> Dict[str, List[Unit]]:
    """``{archive path relative to the series folder: unit rows}`` for the entry's own archives
    (a hit the scanner did not parse is parsed here with the entry's kind hint)."""
    folder = Path(entry.folder)
    out: Dict[str, List[Unit]] = {}
    for hit in entry.inventory_files:
        rel = _rel(hit.path, folder)
        out[rel] = units_from_parsed(rel, _parsed_of(hit, entry.kind_hint), hit.size)
    return out


def inventory_of_entry(entry: Any, volume_list: Optional[Iterable[Any]] = None) -> Inventory:
    """:func:`compute_inventory` of a scanner ``MangaEntry``."""
    return compute_inventory((u for us in units_of_entry(entry).values() for u in us), volume_list)


def inventory_of_parsed(names: Sequence[Tuple[str, Any]],
                        volume_list: Optional[Iterable[Any]] = None) -> Inventory:
    """:func:`compute_inventory` of ``(archive rel path, ParsedName)`` pairs (tests, tools)."""
    return compute_inventory((u for rel, p in names for u in units_from_parsed(rel, p)), volume_list)


def as_state_inventory(inv: Inventory) -> Any:
    """*inv* in the shape the rescan states read (:class:`mangalist.states.InventoryLike`): held volume
    and chapter numbers, the chapters held volumes collect as ``(from, to)`` pairs, extras, the highest
    numbers and the files whose kind is not known yet."""
    from .states import InventorySnapshot  # states does not import this module; no cycle

    return InventorySnapshot(
        held_volumes=tuple(inv.volumes),
        held_chapters=tuple(inv.chapters),
        chapters_covered_by_volumes=tuple((s.start, s.end) for s in inv.covered),
        extras=tuple(inv.extras),
        highest_volume=inv.highest_volume,
        highest_chapter=inv.highest_chapter,
        unknown_kind_files=tuple(inv.unknown),
    )


__all__ = [
    "as_state_inventory", "Inventory", "Span", "UnitKey", "compute_inventory", "expand", "format_spans", "inventory_of_entry",
    "inventory_of_parsed", "ranges", "read_volume_list", "to_number", "units_from_parsed", "units_of_entry",
    "MAX_RANGE_SPAN",
]
