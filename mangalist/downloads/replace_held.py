"""Replace held chapters with another source's or group's copy, by hand (owner, 2026-11-22: "I want to replace chapters
already downloaded into ones from a different group ... essentially a chapter upgrade, replacing .1 .2 chapters from
one group on one scanlator site to another one"). No Qt.

The owner picks a source and confirms the series on it (the series' saved source is not changed); :func:`build` pairs
the source's chapters with the chapter files the series holds:

- the same chapter (a whole for a whole, a part for the same part): the new copy replaces that file;
- a whole chapter the series holds only in parts (``33`` for ``33.1`` and ``33.2``): the whole replaces every one of
  those parts - one form per chapter, as the checks keep it (:mod:`.chapter_forms`);
- listed but not offered, with the reason: a chapter the source has only in parts where the series holds it whole (a
  whole is never replaced by some of its parts), and a held file that holds several chapters (``151-165``).

The ticked ones are sent like any chapter (:func:`.chapters.send_chapters`), into the folder of the files they replace,
each record naming those files (:data:`KEY_REPLACES_FILES`) with the journal's newest plan then
(:data:`KEY_REPLACES_SINCE`): the chapter filing (:mod:`.chapter_arrivals`) moves them to the holding folder first - one
replaced-files batch, restorable from the Download tab and emptied after the holding period - files the new copy, and
puts them back when the new copy cannot be filed. Nothing here is automatic: the passes never replace a held chapter.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .chapters import ChapterSendOutcome, MangaMatch, _per_group, _plain, send_chapters, to_decimal
from .contracts import SuwayomiChapter

_log = logging.getLogger(__name__)

KEY_REPLACES_FILES = "replaces_files"     # a chapter record: the library files (absolute paths) its copy replaces
KEY_REPLACES_SINCE = "replaces_since"     # ... and the journal's newest plan when it was sent (later moves are followed)


@dataclass(frozen=True)
class HeldFile:
    path: str                       # absolute
    rel: str                        # relative to the series folder, '/' separators
    number: Decimal                 # the chapter it holds (a part: 33.1)


@dataclass
class ReplaceRow:
    """One chapter the source has and the series holds."""

    number: str                                     # the source's chapter number ("33")
    options: Tuple[SuwayomiChapter, ...] = ()       # the source's copies of it, one per group (the default first)
    held: Tuple[HeldFile, ...] = ()                 # the files it replaces
    note: str = ""                                  # what it does ("replaces the 2 parts"), or why it is not offered
    offered: bool = True

    @property
    def default(self) -> Optional[SuwayomiChapter]:
        return self.options[0] if self.options else None

    @property
    def folder(self) -> Optional[str]:
        """Where the new copy goes: the folder of the (first) file it replaces."""
        return os.path.dirname(self.held[0].path) if self.held else None


@dataclass
class ReplaceLookup:
    series_id: int
    match: Optional[MangaMatch] = None
    manga_title: str = ""
    source_name: str = ""
    rows: List[ReplaceRow] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def offered(self) -> List[ReplaceRow]:
        return [r for r in self.rows if r.offered]


def held_files(db, series_id: int) -> List[HeldFile]:
    """The series' chapter files that hold one chapter (or one part) each, as the library database read them."""
    from .placement import locate_series, series_units

    series, _root, series_dir = locate_series(db, series_id)
    out: List[HeldFile] = []
    for u in series_units(db, series):
        if u.kind not in ("chapter", "both"):
            continue
        start, end = to_decimal(u.ch_from), to_decimal(u.ch_to)
        rel = (u.rel_path or "").replace("\\", "/").strip("/")
        if start is None or not rel or (end is not None and end != start):
            continue                                # a range file (151-165): not one chapter
        out.append(HeldFile(os.path.join(series_dir, *rel.split("/")), rel, start))
    return out


def ranges_held(db, series_id: int) -> List[Tuple[Decimal, Decimal, str]]:
    """The series' chapter files that hold several chapters: (first, last, rel)."""
    from .placement import locate_series, series_units

    series, _root, _dir = locate_series(db, series_id)
    out = []
    for u in series_units(db, series):
        start, end = to_decimal(u.ch_from), to_decimal(u.ch_to)
        if u.kind in ("chapter", "both") and start is not None and end is not None and end != start:
            out.append((start, end, (u.rel_path or "").replace("\\", "/")))
    return out


def rows_for(chapters: Sequence[SuwayomiChapter], held: Sequence[HeldFile],
             ranges: Sequence[Tuple[Decimal, Decimal, str]] = ()) -> List[ReplaceRow]:
    """The source's *chapters* paired with the series' *held* files (module docstring), ascending by number."""
    per = _per_group(chapters)
    counts = Counter(key for copies in per.values() for key in copies)
    by_number: Dict[Decimal, List[HeldFile]] = {}
    for f in held:
        by_number.setdefault(f.number, []).append(f)
    rows: List[ReplaceRow] = []
    for number in sorted(per):
        if number < 0:
            continue
        copies = sorted(per[number].items(), key=lambda kv: (-counts[kv[0]], kv[0]))
        options = tuple(ch for _k, ch in copies)
        text = _plain(str(number)) or str(number)
        whole = number == number.to_integral_value()
        exact = by_number.get(number, [])
        parts = [f for n, fs in by_number.items() if whole and n != number and int(n) == int(number) for f in fs]
        covered = next((r for r in ranges if r[0] <= number <= r[1]), None)
        if exact:
            rows.append(ReplaceRow(text, options, tuple(exact), "replaces the file held" if len(exact) == 1
                                   else f"replaces the {len(exact)} files held"))
        elif parts:
            parts.sort(key=lambda f: f.number)
            rows.append(ReplaceRow(text, options, tuple(parts), f"the whole chapter replaces the {len(parts)} parts held "
                                   f"({', '.join(_plain(str(f.number)) or '' for f in parts)})"))
        elif not whole and Decimal(int(number)) in by_number:
            rows.append(ReplaceRow(text, options, tuple(by_number[Decimal(int(number))]),
                                   f"the source has chapter {int(number)} in parts; the whole chapter held is not "
                                   "replaced by some of its parts", offered=False))
        elif covered is not None:
            rows.append(ReplaceRow(text, options, (), f"held in {covered[2]} with chapters "
                                   f"{_plain(str(covered[0]))}-{_plain(str(covered[1]))}; one chapter does not replace "
                                   "that file", offered=False))
    return rows


def build(client, db, series_id: int, match: MangaMatch) -> ReplaceLookup:
    """The replace rows of a confirmed *match* (its source's chapter list now) for the series."""
    lookup = ReplaceLookup(series_id=series_id, match=match)
    fetched = client.chapters(match.manga.id)
    lookup.manga_title = fetched.manga.title or match.manga.title
    lookup.source_name = fetched.source_name or match.source.display_name
    lookup.rows = rows_for(fetched.chapters, held_files(db, series_id), ranges_held(db, series_id))
    return lookup


def send_replacements(client, ledger, series_id: int, match: MangaMatch,
                      picks: Sequence[Tuple[SuwayomiChapter, Sequence[str]]], *, manga_title: str = "",
                      source_name: str = "", since: int = 0) -> ChapterSendOutcome:
    """Send the picked copies, each with the library files it replaces (absolute paths): one send per folder of
    those files (where the new copy is filed). *since*: the journal's newest plan now (the files are followed through
    later moves). The outcomes are merged."""
    from ..store.units import exact_number

    by_folder: Dict[str, List[Tuple[SuwayomiChapter, Sequence[str]]]] = {}
    for ch, paths in picks:
        if paths:
            by_folder.setdefault(os.path.dirname(paths[0]), []).append((ch, paths))
    out: Optional[ChapterSendOutcome] = None
    for folder, group in by_folder.items():
        replacing: Mapping[str, Mapping[str, object]] = {
            exact_number(ch.number): {KEY_REPLACES_FILES: [str(p) for p in paths], KEY_REPLACES_SINCE: int(since)}
            for ch, paths in group if exact_number(ch.number)}
        one = send_chapters(client, ledger, series_id, match, [ch for ch, _p in group], folder,
                            manga_title=manga_title, source_name=source_name, replacing=replacing)
        _log.info("Replace held chapters: series %s: %d copy(ies) from %s sent for %s", series_id, len(one.records),
                  source_name or match.source.display_name, folder)
        if out is None:
            out = one
        else:
            out.records.extend(one.records)
            out.skipped.extend(one.skipped)
            out.error = out.error or one.error
    return out if out is not None else ChapterSendOutcome()


__all__ = ["KEY_REPLACES_FILES", "KEY_REPLACES_SINCE", "HeldFile", "ReplaceLookup", "ReplaceRow", "build",
           "held_files", "ranges_held", "rows_for", "send_replacements"]
