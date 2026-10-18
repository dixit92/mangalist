"""One form per chapter: a whole chapter ``N`` or its parts ``N.1``, ``N.2`` ... - never both (owner, 2026-10-18: "Check:
one form per chapter"). No Qt.

Why: two scanlation groups can release one series, one in whole chapters and one in parts, and a source such as
MangaFire lists both under one label ("unofficial"), so the group cannot tell them apart. The owner's series held the
parts up to ``37.2``; the source listed ``38``, ``38.2``, ``39.1`` (its ``38.1`` was on no source the owner enabled).
"[the parts are] the better release" - and the whole ``38`` stands in for the missing ``38.1``.

**What is a part** (:func:`mangalist.split_chapters.part_numbers`): the split rule's tenths (:mod:`mangalist.split_chapters`) - one decimal,
``.1`` ... ``.4``, and ``.5`` or above only next to a ``.4`` (else it is an extra, ``10.5``) - wherever the run starts
(``38.2`` alone is a part of 38 here: the split rule's "starts at .1" decides whether parts *count as* chapter N, not
whether they are parts).

**The series' form** (:func:`held_form`): the series holds parts when the newest chapter it holds is held in parts, or
most of its last :data:`RECENT` chapters are; otherwise it holds whole chapters. A chapter is held in parts when its
parts are held without the whole, or next to it as the split model counts parts (from ``.2``, or ``.1`` and ``.2``); a
lone ``N.1`` next to a whole ``N`` is an extra.

**Choosing** (:func:`choose`), for a chapter ``N`` the source lists both whole and in parts:

- the series holds parts: the parts - and the whole ``N`` too, as a **stand-in**, when the source's parts of ``N`` do
  not start at ``.1`` (``38`` + ``38.2``: the whole 38 stands in for the missing ``38.1``);
- the series holds whole chapters: the whole ``N``, never its parts. A part of a whole chapter the series holds is not
  taken either.

Never a whole ``N`` together with ``N.1``. The form not taken is listed with its reason (unticked in the chapters panel,
never sent by a pass or the scheduled check).

**Late parts** (:func:`late_parts`): a part ``N.k`` the source lists and the series lacks, while the series holds other
parts of ``N`` (``38.1`` listed later, ``38.2`` held - with or without the stand-in whole ``38``), is wanted although it
is numbered below the newest chapter held. Only parts of a chapter whose other parts are held, and only among the
series' last :data:`RECENT` chapters (an old chapter split differently by another group is not asked for); never an
older whole chapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set

from ..split_chapters import part_numbers

#: How many of the series' newest chapters decide its form, and how far back a late part is looked for.
RECENT = 5


def _plain(number: Decimal) -> str:
    return str(int(number)) if number == number.to_integral_value() else format(number.normalize(), "f")


def _tenth(number: Decimal) -> Optional[int]:
    """The tenth of a one-decimal number (``38.2`` -> 2); None for a whole number or ``12.25``."""
    if number <= 0:
        return None
    t = (number - Decimal(int(number))) * 10
    if t == 0 or t != t.to_integral_value():
        return None
    return int(t)


def is_whole(number: Decimal) -> bool:
    return number == number.to_integral_value()


def is_part(number: Decimal, among: Iterable[Decimal]) -> bool:
    """Whether *number* is a part of its chapter, read with the other numbers *among* (they decide ``.5`` and above)."""
    parts = part_numbers(set(among) | {number})
    return number in parts.get(int(number), ())


@dataclass(frozen=True)
class HeldForm:
    parts: bool                         # the series holds parts (else whole chapters)
    reason: str                         # e.g. "the series holds its chapters in parts (newest: ch 37.1, 37.2)"


def _held_in_parts(n: int, held: Set[Decimal], parts: List[Decimal]) -> bool:
    """Whether the series holds chapter *n* in parts: parts without the whole chapter, or next to it parts the split
    model counts (from ``.2``, the whole being the first part; or ``.1`` and ``.2`` onwards) - a lone ``N.1`` next to a
    whole ``N`` is an extra (MangaPixer's rule), not a sign of parts."""
    if not parts:
        return False
    if Decimal(n) not in held:
        return True
    return _tenth(parts[0]) == 2 or (_tenth(parts[0]) == 1 and len(parts) > 1)


def held_form(held: Iterable[Decimal]) -> HeldForm:
    """The form the series holds its chapters in (see the module docstring). *held*: the chapter numbers it holds."""
    held = {n for n in held if n is not None}
    parts = part_numbers(held)
    chapters = sorted({int(n) for n in held if is_whole(n)} | set(parts))
    if not chapters:
        return HeldForm(False, "the series holds no chapter yet")
    recent = chapters[-RECENT:]
    in_parts = [n for n in recent if _held_in_parts(n, held, parts.get(n, []))]
    newest = recent[-1]
    if newest in in_parts or 2 * len(in_parts) > len(recent):
        shown = ", ".join(_plain(p) for p in parts[in_parts[-1]])
        return HeldForm(True, f"the series holds its chapters in parts (ch {shown})")
    return HeldForm(False, "the series holds whole chapters")


def recent_chapters(held: Iterable[Decimal]) -> List[int]:
    """The series' last :data:`RECENT` chapters ``N`` (held whole or in parts), ascending."""
    held = {n for n in held if n is not None}
    return sorted({int(n) for n in held if is_whole(n)} | set(part_numbers(held)))[-RECENT:]


@dataclass
class FormChoice:
    take: List[Decimal] = field(default_factory=list)          # the numbers taken, ascending
    notes: Dict[Decimal, str] = field(default_factory=dict)    # why, for a taken number that needs a word
    others: Dict[Decimal, str] = field(default_factory=dict)   # the other form, not taken, and why
    form: Optional[HeldForm] = None


def choose(listed: Iterable[Decimal], held: Iterable[Decimal], wanted: Iterable[Decimal]) -> FormChoice:
    """One form per chapter among *wanted* (the numbers asked about: chapters after the newest held, a gap's whole
    chapters, late parts), from what the source *listed* and the series *held* (see the module docstring). A whole
    chapter of *wanted* the source lists whole and in parts brings its parts in when they are the form taken."""
    listed = {n for n in listed if n is not None}
    held = {n for n in held if n is not None}
    wanted = {n for n in wanted if n is not None}
    form = held_form(held)
    out = FormChoice(form=form)
    source_parts = part_numbers(listed)
    held_parts = part_numbers(held)
    candidates = set(wanted)
    for n in wanted:
        if is_whole(n) and n in listed and form.parts:
            candidates.update(p for p in source_parts.get(int(n), ()) if p not in held)
    take: Set[Decimal] = set()
    for c in candidates:
        n = int(c)
        if is_whole(c):
            parts = [p for p in source_parts.get(n, ()) if p in candidates]
            if c not in listed or not parts:
                take.add(c)                 # one form only (a whole the source lacks: the caller's own rule)
                if form.parts and c in listed and not held_parts.get(n):
                    out.notes[c] = "whole: the source lists no parts of it"
                continue
            if not form.parts:
                take.add(c)
                continue
            first = source_parts[n][0]
            if _tenth(first) == 1:
                out.others[c] = (f"the whole chapter; its parts {', '.join(_plain(p) for p in parts)} are taken - "
                                 f"{form.reason}")
            else:
                take.add(c)
                missing = ", ".join(_plain(Decimal(n) + Decimal(t) / 10) for t in range(1, _tenth(first)))
                out.notes[c] = f"stands in for the missing part {missing} (the source lists ch {n} from {_plain(first)})"
            continue
        if c not in source_parts.get(n, ()) and not is_part(c, held | listed | {c}):
            take.add(c)                     # an extra (38.5) or a number like 12.25: not a part, taken as before
            continue
        whole = Decimal(n)
        # a whole-chapter series takes no part of a whole it holds or takes - unless it holds a part of it already (a
        # part N.2 after a held N.1 counts: MAL-Sync parity, owner 2026-10-18)
        if not form.parts and not held_parts.get(n) and (whole in held or (whole in candidates and whole in listed)):
            out.others[c] = (f"a part of ch {n}; the whole ch {n} is "
                             f"{'held' if whole in held else 'taken'} - {form.reason}")
            continue
        take.add(c)                         # the series holds parts, or the source has ch N in parts only
    out.take = sorted(take)
    return out


def late_parts(listed: Iterable[Decimal], held: Iterable[Decimal], newest: Optional[Decimal]) -> Dict[Decimal, str]:
    """Late parts the source lists (see the module docstring): number -> why, e.g. ``a late part of ch 38 (held: 38,
    38.2)``. Parts numbered after *newest* are not late (they are simply new)."""
    listed = {n for n in listed if n is not None}
    held = {n for n in held if n is not None}
    held_parts = part_numbers(held)
    recent = set(recent_chapters(held))
    out: Dict[Decimal, str] = {}
    for n, own in held_parts.items():
        if n not in recent:
            continue
        around = {x for x in listed | held if int(x) == n}
        for p in part_numbers(around).get(n, ()):
            if p in held or p not in listed or (newest is not None and p > newest):
                continue
            have = sorted(x for x in held if int(x) == n and (is_whole(x) or x in own))
            out[p] = f"a late part of ch {n} (held: {', '.join(_plain(x) for x in have)})"
    return out


def is_late_part(number: Decimal, held: Iterable[Decimal]) -> bool:
    """Whether *number* would be a late part of the series (it lacks it, holds other parts of its chapter, among its
    last chapters) - the rule :func:`late_parts` applies, for a finding that offered one."""
    held = {n for n in held if n is not None}
    if number in held or is_whole(number):
        return False
    n = int(number)
    return n in recent_chapters(held) and bool(part_numbers(held).get(n)) and is_part(number, held | {number})


__all__ = ["FormChoice", "HeldForm", "RECENT", "choose", "held_form", "is_late_part", "is_part", "is_whole",
           "late_parts", "part_numbers", "recent_chapters"]
