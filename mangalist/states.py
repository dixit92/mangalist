"""Rescan states: one state per series folder, its gaps and its flags (pure, no Qt).

:func:`compute_state` takes what the folder holds (an *inventory*, see :class:`InventoryLike`) and
what is known about the series (:class:`~mangalist.knowledge.SeriesKnowledge`) and answers one
:class:`State` (Product Design 5) with the gaps behind it (missing volume numbers, missing chapter
ranges incl. holes, volumes that would upgrade chapters held as scanlations - exact decimals) and the
flags Upcoming, Requested, Needs attention and Rename pending (files not named by the root's naming scheme, counted
by :meth:`mangalist.renamer.Renamer.pending_counts` and passed in).

MangaPixer's Completion answer is a cross-check only: MangaList's own computation decides the state of
its rows; when the two disagree, the state stays MangaList's and MangaPixer's answer is kept on the
result (for the tooltip).

The rules, in order (the full table is in the lane note):

1. An **empty folder** is wanted (A7): licensed with an English volume (or chapter) out -> *Wanted -
   official available*; licensed with nothing out yet -> *Wanted - awaiting release* (the announced
   date when one is known); not licensed -> *Wanted - scanlation only*; nothing known -> *Wanted*.
2. **Nothing known** (no linked record, or no number to compare with) -> *Can't tell*.
3. Gaps: **volumes** - an English volume that is out, not held as a volume and not wholly held as
   chapters is missing (in a folder that holds volumes, or holds nothing but volumes' worth); a volume
   whose chapters are all held as chapters is an **upgrade**. Without an English edition, the
   scanlation's latest volume is the target for a folder that holds volumes. **Chapters** (a folder
   that holds chapter files, or an unlicensed series) - every whole chapter from the first expected
   one up to the latest known one (MangaUpdates' latest chapter, the English chapter count, the
   finished total - the largest) that is held neither as a chapter nor inside a held volume, plus
   the latest chapter itself when it is a decimal (``24.5``).
4. State by priority: *Missing volumes* > *Missing chapters* > *Upgrade available* > *Complete*
   (finished and held whole in the edition the folder collects) > *Up to date*.

The inventory interface is the inventory lane's (``mangalist/inventory.py``); :class:`InventorySnapshot`
is a stand-in with the same attributes, and :func:`fallback_inventory_from_entry` builds one from what
a :class:`~mangalist.models.MangaEntry` already has (FALLBACK until the real inventory is wired).
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Collection, Iterable, List, Optional, Protocol, Sequence, Set, Tuple, runtime_checkable

from .knowledge import (
    ANSWER_CANT_TELL,
    ANSWER_FINISHED_MISSING,
    ANSWER_HAVE_IT_ALL,
    ANSWER_MISSING_SOME,
    ANSWER_UP_TO_DATE,
    LINK_DONT_MATCH,
    LINK_NOT_A_WORK,
    LINK_NOT_LOOKED_UP,
    LINK_UNMATCHED,
    SeriesKnowledge,
    VolumeInfo,
    fmt_num,
    next_announced,
    released_volumes,
    to_decimal,
)
from .split_chapters import splits_of


class State(str, Enum):
    WANTED_OFFICIAL = "Wanted - official available"
    WANTED_AWAITING = "Wanted - awaiting release"
    WANTED_SCANLATION = "Wanted - scanlation only"
    WANTED = "Wanted"                      # empty folder, nothing known about the series
    MISSING_VOLUMES = "Missing volumes"
    MISSING_CHAPTERS = "Missing chapters"
    UPGRADE = "Upgrade available"
    UP_TO_DATE = "Up to date"
    COMPLETE = "Complete"
    CANT_TELL = "Can't tell"               # a folder with files, nothing to compare them with
    NOT_A_SERIES = "Not a series"          # MangaPixer: DontMatch, CollectionAbout or an unknown link state

    @property
    def is_wanted(self) -> bool:
        return self in WANTED_STATES


WANTED_STATES = frozenset({State.WANTED_OFFICIAL, State.WANTED_AWAITING, State.WANTED_SCANLATION, State.WANTED})
MISSING_STATES = frozenset({State.MISSING_VOLUMES, State.MISSING_CHAPTERS})
# Display / sort order: what needs doing first.
STATE_ORDER: Tuple[State, ...] = (
    State.WANTED_OFFICIAL, State.WANTED_AWAITING, State.WANTED_SCANLATION, State.WANTED,
    State.MISSING_VOLUMES, State.MISSING_CHAPTERS, State.UPGRADE,
    State.UP_TO_DATE, State.COMPLETE, State.CANT_TELL, State.NOT_A_SERIES,
)

# Needs-attention reasons.
ATTENTION_UNMATCHED = "unmatched"
ATTENTION_REVIEW = "needs review"
ATTENTION_KIND = "needs kind"
ATTENTION_NOT_A_WORK = "not one work"
ATTENTION_LABELS = {
    ATTENTION_UNMATCHED: "No MangaUpdates match",
    ATTENTION_REVIEW: "Match needs review",
    ATTENTION_KIND: "Volumes or chapters? (bare numbers)",
    ATTENTION_NOT_A_WORK: "Not matched: the folder is not one work",
}


# --- inventory interface --------------------------------------------------------------------------


@runtime_checkable
class InventoryLike(Protocol):
    """What the inventory lane gives per series (numbers as Decimal or exact decimal strings):

    * ``held_volumes`` - volume numbers held as volume archives;
    * ``held_chapters`` - chapter numbers held as chapter archives (an element may also be a
      ``(from, to)`` pair for a range archive);
    * ``chapters_covered_by_volumes`` - chapter numbers the held volume archives collect (when known);
    * ``extras`` - extras / specials (not counted as gaps);
    * ``highest_volume`` / ``highest_chapter`` - the largest held numbers (or None);
    * ``unknown_kind_files`` - files with bare numbers (volumes or chapters?), a collection or a count.
    """

    held_volumes: Collection[Any]
    held_chapters: Collection[Any]
    chapters_covered_by_volumes: Collection[Any]
    extras: Collection[Any]
    highest_volume: Any
    highest_chapter: Any
    unknown_kind_files: Any


@dataclass
class InventorySnapshot:
    """A plain inventory with :class:`InventoryLike`'s attributes (tests and the fallback)."""

    held_volumes: Collection[Any] = ()
    held_chapters: Collection[Any] = ()
    chapters_covered_by_volumes: Collection[Any] = ()
    extras: Collection[Any] = ()
    highest_volume: Any = None
    highest_chapter: Any = None
    unknown_kind_files: Any = ()


@dataclass(frozen=True)
class _Held:
    volumes: frozenset
    chapters: frozenset            # loose chapter archives (exact numbers)
    chapter_ranges: Tuple[Tuple[Decimal, Decimal], ...]
    covered: frozenset             # chapters inside held volumes (inventory + volume list)
    highest_volume: Optional[Decimal]
    highest_chapter: Optional[Decimal]
    n_unknown: int
    split_missing: Tuple[Decimal, ...] = ()   # parts of a split chapter missing below the highest part held

    def holds_chapter(self, c: Decimal) -> bool:
        if c in self.chapters or c in self.covered:
            return True
        return any(lo <= c <= hi for lo, hi in self.chapter_ranges)

    @property
    def has_volumes(self) -> bool:
        return bool(self.volumes)

    @property
    def has_chapters(self) -> bool:
        return bool(self.chapters or self.chapter_ranges)


def _decimals(values: Iterable[Any]) -> Tuple[Set[Decimal], List[Tuple[Decimal, Decimal]]]:
    singles: Set[Decimal] = set()
    ranges: List[Tuple[Decimal, Decimal]] = []
    for v in values or ():
        if isinstance(v, (tuple, list)) and len(v) == 2:
            lo, hi = to_decimal(v[0]), to_decimal(v[1])
            if lo is not None and hi is not None:
                lo, hi = min(lo, hi), max(lo, hi)
                if lo == hi:
                    singles.add(lo)
                else:
                    ranges.append((lo, hi))
            continue
        d = to_decimal(v)
        if d is not None:
            singles.add(d)
    return singles, ranges


def _count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except TypeError:
        return 0


def _held(inv: Any, knowledge: Optional[SeriesKnowledge]) -> _Held:
    vols, _ = _decimals(getattr(inv, "held_volumes", ()) or ())
    chs, ranges = _decimals(getattr(inv, "held_chapters", ()) or ())
    # Split chapters (2.1 + 2.2 = chapter 2), MangaPixer's rule: the chapter counts as held.
    splits = splits_of(chs, ranges)
    chs = set(chs) | {Decimal(n) for n in splits.chapters}
    covered, cov_ranges = _decimals(getattr(inv, "chapters_covered_by_volumes", ()) or ())
    # A held volume that MangaPixer's volume list maps to chapters covers those chapters (counted once).
    if knowledge is not None:
        for v in knowledge.volumes:
            if to_decimal(v.volume) in vols and v.has_chapters:
                cov_ranges.append((to_decimal(v.chapters_from), to_decimal(v.chapters_to)))
    for lo, hi in cov_ranges:
        covered |= _whole_numbers(lo, hi) | {lo, hi}
    hv = to_decimal(getattr(inv, "highest_volume", None))
    hc = to_decimal(getattr(inv, "highest_chapter", None))
    if hv is None and vols:
        hv = max(vols)
    if hc is None and (chs or ranges):
        hc = max(list(chs) + [hi for _, hi in ranges])
    return _Held(frozenset(vols), frozenset(chs), tuple(ranges), frozenset(covered), hv, hc,
                 _count(getattr(inv, "unknown_kind_files", None)), splits.missing_parts)


def _whole_numbers(lo: Decimal, hi: Decimal) -> Set[Decimal]:
    start = int(lo) if lo == lo.to_integral_value() else int(lo) + 1
    if lo < 0:
        start = max(start, 0)
    return {Decimal(n) for n in range(start, int(hi) + 1)}


# --- result ----------------------------------------------------------------------------------------

GAP_VOLUME = "volume"
GAP_CHAPTER = "chapter"
GAP_UPGRADE = "upgrade"


@dataclass(frozen=True)
class Gap:
    kind: str                      # GAP_VOLUME | GAP_CHAPTER | GAP_UPGRADE
    start: str                     # exact number
    end: Optional[str] = None      # a range's last number (None for one unit)
    note: Optional[str] = None     # e.g. "English 2025-07", "chapters 17-24.5"

    @property
    def label(self) -> str:
        unit = "Ch." if self.kind == GAP_CHAPTER else "Vol."
        rng = self.start if self.end is None else f"{self.start}-{self.end}"
        return f"{unit} {rng}"

    @property
    def size(self) -> int:
        """Whole units in the gap (a decimal single counts 1)."""
        if self.end is None:
            return 1
        lo, hi = to_decimal(self.start), to_decimal(self.end)
        return max(1, int(hi) - int(lo) + 1) if lo is not None and hi is not None else 1


@dataclass(frozen=True)
class SeriesState:
    state: State
    gaps: Tuple[Gap, ...] = ()
    reasons: Tuple[str, ...] = ()                # why this state (short sentences)
    upcoming: bool = False
    upcoming_date: Optional[str] = None          # the next announced English date
    upcoming_volume: Optional[str] = None
    requested: bool = False                      # placeholder until the dispatch ledger is used
    needs_attention: Tuple[str, ...] = ()        # ATTENTION_* reasons
    rename_pending: bool = False                 # files not named by the root's scheme (mangalist.renamer)
    rename_count: int = 0                        # how many (0: not counted)
    source: Optional[str] = None                 # the knowledge's source
    mangapixer_answer: Optional[str] = None
    mangapixer_reason: Optional[str] = None
    mangapixer_disagrees: bool = False

    # -- convenience -----------------------------------------------------------------------------

    @property
    def is_wanted(self) -> bool:
        return self.state in WANTED_STATES

    def gaps_of(self, kind: str) -> Tuple[Gap, ...]:
        return tuple(g for g in self.gaps if g.kind == kind)

    @property
    def missing_volumes(self) -> Tuple[str, ...]:
        return tuple(g.start for g in self.gaps_of(GAP_VOLUME))

    @property
    def upgrade_volumes(self) -> Tuple[str, ...]:
        return tuple(g.start for g in self.gaps_of(GAP_UPGRADE))

    @property
    def missing_chapters(self) -> Tuple[Tuple[str, Optional[str]], ...]:
        return tuple((g.start, g.end) for g in self.gaps_of(GAP_CHAPTER))

    @property
    def n_missing(self) -> int:
        return sum(g.size for g in self.gaps if g.kind != GAP_UPGRADE)

    @property
    def upgrade_available(self) -> bool:
        """Some official volume is out that the folder holds only as chapters (or not mapped yet)."""
        return any(g.kind == GAP_UPGRADE for g in self.gaps)

    @property
    def complete_with_upgrade(self) -> bool:
        """Owner rule (c), 2026-10-03: a Complete series whose official volumes are (being) released is
        "Complete + Upgrade available"."""
        return self.state == State.COMPLETE and self.upgrade_available

    @property
    def flags(self) -> Tuple[str, ...]:
        out = []
        if self.complete_with_upgrade:
            out.append("Upgrade available")
        if self.upcoming:
            out.append("Upcoming")
        if self.requested:
            out.append("Requested")
        if self.needs_attention:
            out.append("Needs attention")
        if self.rename_pending:
            out.append("Rename pending")
        return tuple(out)

    @property
    def gaps_text(self) -> str:
        """Short form for a table cell: ``Vol. 3, 5-6 · Ch. 10-12, 24.5 · Upgrade vol. 1-2``."""
        parts = []
        vols = _compress(self.missing_volumes)
        if vols:
            parts.append("Vol. " + ", ".join(vols))
        chs = [s if e is None else f"{s}-{e}" for s, e in self.missing_chapters]
        if chs:
            parts.append("Ch. " + ", ".join(chs))
        ups = _compress(self.upgrade_volumes)
        if ups:
            parts.append("Upgrade vol. " + ", ".join(ups))
        return "  ·  ".join(parts)

    @property
    def state_text(self) -> str:
        return self.state.value

    def mangapixer_text(self) -> Optional[str]:
        if not self.mangapixer_answer:
            return None
        text = f"MangaPixer: {self.mangapixer_answer}"
        if self.mangapixer_reason and self.mangapixer_reason != "None":
            text += f" ({self.mangapixer_reason})"
        return text

    def tooltip(self) -> str:
        lines = [self.state.value]
        lines += [f"  {r}" for r in self.reasons]
        if self.upcoming:
            when = self.upcoming_date or "date not known"
            vol = f"vol. {self.upcoming_volume} " if self.upcoming_volume else ""
            lines.append(f"Upcoming: English {vol}{when}")
        for a in self.needs_attention:
            lines.append(f"Needs attention: {ATTENTION_LABELS.get(a, a)}")
        if self.requested:
            lines.append("Requested: dispatched, not arrived yet")
        if self.rename_pending:
            n = f"{self.rename_count} file{'s' if self.rename_count != 1 else ''}" if self.rename_count else "Files"
            lines.append(f"Rename pending: {n} not named by the scheme (right-click: Rename to the scheme)")
        mp = self.mangapixer_text()
        if mp and self.mangapixer_disagrees:
            lines.append(f"{mp} - differs; MangaList's own count is shown")
        elif mp:
            lines.append(mp)
        return "\n".join(lines)

    def gaps_tooltip(self) -> str:
        if not self.gaps:
            return ""
        lines = []
        for kind, title in ((GAP_VOLUME, "Missing volumes"), (GAP_CHAPTER, "Missing chapters"),
                            (GAP_UPGRADE, "Upgrade available (held as chapters)")):
            gaps = self.gaps_of(kind)
            if gaps:
                lines.append(f"{title}:")
                lines += [f"  {g.label}" + (f"  ({g.note})" if g.note else "") for g in gaps]
        return "\n".join(lines)

    @property
    def sort_key(self) -> Tuple[int, int]:
        """Rank by STATE_ORDER, then more missing first."""
        return (STATE_ORDER.index(self.state), -self.n_missing)


def _compress(numbers: Sequence[str]) -> List[str]:
    """``["1","2","3","5","6.5"]`` -> ``["1-3", "5", "6.5"]`` (whole numbers in a row merge)."""
    out: List[str] = []
    run: List[Decimal] = []

    def flush():
        if run:
            out.append(fmt_num(run[0]) if len(run) == 1 else f"{fmt_num(run[0])}-{fmt_num(run[-1])}")
            run.clear()

    for s in numbers:
        d = to_decimal(s)
        if d is None:
            continue
        if d != d.to_integral_value():
            flush()
            out.append(fmt_num(d))
            continue
        if run and d == run[-1] + 1:
            run.append(d)
        else:
            flush()
            run.append(d)
    flush()
    return out


def _chapter_gaps(missing: Iterable[Decimal]) -> List[Gap]:
    gaps: List[Gap] = []
    run: List[Decimal] = []

    def flush():
        if run:
            gaps.append(Gap(GAP_CHAPTER, fmt_num(run[0]), fmt_num(run[-1]) if len(run) > 1 else None))
            run.clear()

    for d in sorted(set(missing)):
        if d != d.to_integral_value():
            flush()
            gaps.append(Gap(GAP_CHAPTER, fmt_num(d)))
            continue
        if run and d == run[-1] + 1:
            run.append(d)
        else:
            flush()
            run.append(d)
    flush()
    return gaps


# --- MangaPixer cross-check -----------------------------------------------------------------------

_AGREES = {
    ANSWER_MISSING_SOME: WANTED_STATES | MISSING_STATES,
    ANSWER_FINISHED_MISSING: WANTED_STATES | MISSING_STATES,
    ANSWER_UP_TO_DATE: frozenset({State.UP_TO_DATE, State.UPGRADE}),
    ANSWER_HAVE_IT_ALL: frozenset({State.COMPLETE, State.UPGRADE}),
}


def mangapixer_agrees(state: State, answer: Optional[str]) -> Optional[bool]:
    """Does MangaPixer's Completion *answer* say the same as *state*? None when there is nothing to
    compare (CantTell, an unknown answer, or MangaList cannot tell either)."""
    if not answer or answer == ANSWER_CANT_TELL or state == State.CANT_TELL:
        return None
    allowed = _AGREES.get(answer)
    if allowed is None:
        return None
    return state in allowed


# --- the rules -------------------------------------------------------------------------------------


def compute_state(
    inventory: Any,
    knowledge: Optional[SeriesKnowledge],
    *,
    folder_empty: bool,
    needs_kind: bool = False,
    requested: bool = False,
    rename_pending: bool = False,
    rename_count: int = 0,
    behind_override: Optional[str] = None,
    today: Optional[_dt.date] = None,
) -> SeriesState:
    """The state of one series folder.

    *inventory* is an :class:`InventoryLike` (None = nothing held); *knowledge* None = nothing known.
    *folder_empty*: the folder holds no archive at all (A7: wanted). *needs_kind*: the owner has not
    said yet whether the folder's bare numbers are volumes or chapters (C12). *requested* and
    *rename_pending* are passed through as flags (*rename_count*: how many files the renamer would rename;
    a count alone also sets the flag).
    *behind_override* ``"done"``: the owner marked the series up to date - number gaps are kept for the
    tooltip but the state is Up to date (or Complete).
    """
    today = today or _dt.date.today()
    rename_count = max(0, int(rename_count or 0))
    rename_pending = bool(rename_pending or rename_count)
    k = knowledge
    if k is not None and k.not_a_series:
        # MangaPixer says this folder is not a series (DontMatch, CollectionAbout, or a link state this build does
        # not know): no numbers, no gaps, not wanted even when empty, nothing to review.
        return SeriesState(state=State.NOT_A_SERIES, reasons=(k.not_a_series_reason,), source=k.source,
                           rename_pending=rename_pending, rename_count=rename_count)
    held = _held(inventory, k) if inventory is not None else _held(InventorySnapshot(), k)

    attention: List[str] = []
    if k is None or k.link_state in (LINK_UNMATCHED, LINK_NOT_LOOKED_UP):
        if k is None or k.link_state == LINK_UNMATCHED:
            attention.append(ATTENTION_UNMATCHED)
    elif k.link_state == LINK_NOT_A_WORK:
        attention.append(ATTENTION_NOT_A_WORK)
    elif k.needs_review:
        attention.append(ATTENTION_REVIEW)
    if needs_kind or (held.n_unknown > 0 and not folder_empty):
        attention.append(ATTENTION_KIND)

    upcoming_vol = next_announced(k.volumes, today) if k is not None else None
    common = dict(
        upcoming=upcoming_vol is not None,
        upcoming_date=upcoming_vol.english_date if upcoming_vol else None,
        upcoming_volume=upcoming_vol.volume if upcoming_vol else None,
        requested=requested,
        needs_attention=tuple(dict.fromkeys(attention)),
        rename_pending=rename_pending,
        rename_count=rename_count,
        source=k.source if k is not None else None,
    )
    completion = k.completion if k is not None else None

    def finish(state: State, gaps: Sequence[Gap] = (), reasons: Sequence[str] = ()) -> SeriesState:
        answer = completion.answer if completion else None
        agrees = mangapixer_agrees(state, answer)
        return SeriesState(
            state=state, gaps=tuple(gaps), reasons=tuple(reasons),
            mangapixer_answer=answer, mangapixer_reason=completion.reason if completion else None,
            mangapixer_disagrees=agrees is False, **common,
        )

    known = k is not None and k.matched

    # English volumes out: the volume list's English dates, else the English publishers' volume count (1..N) - also
    # when the list has no English date at all (e.g. MangaPixer could not reach its English-dates source).
    out_vols: List[VolumeInfo] = released_volumes(k.volumes, today) if known else []
    if known and k.licensed and k.publisher_volumes and not any(v.english_date for v in k.volumes):
        out_vols = [VolumeInfo(volume=fmt_num(n)) for n in range(1, int(k.publisher_volumes) + 1)]

    # 1. Empty folder = wanted.
    if folder_empty:
        if not known:
            return finish(State.WANTED, reasons=["Empty folder; the series is not matched yet"])
        if k.licensed:
            if out_vols or (k.publisher_chapters or 0) > 0:
                what = f"{len(out_vols)} English volume(s) out" if out_vols else "English chapters out"
                return finish(State.WANTED_OFFICIAL, [Gap(GAP_VOLUME, v.volume, note=_date_note(v)) for v in out_vols],
                              [f"Empty folder; {what}"])
            when = f" (announced {upcoming_vol.english_date})" if upcoming_vol and upcoming_vol.english_date else ""
            return finish(State.WANTED_AWAITING, reasons=[f"Empty folder; licensed, no English volume out yet{when}"])
        return finish(State.WANTED_SCANLATION, reasons=["Empty folder; not licensed in English"])

    if not known:
        why = ("The series is not matched" if k is None or k.link_state != LINK_DONT_MATCH
               else "Set to Don't match in MangaPixer")
        return finish(State.CANT_TELL, reasons=[why])
    if not held.has_volumes and not held.has_chapters:
        return finish(State.CANT_TELL, reasons=["No file says whether it is a volume or a chapter"])

    gaps: List[Gap] = []
    reasons: List[str] = []
    compared = False

    # 2. Volumes.
    volume_collector = held.has_volumes or not held.has_chapters
    mixed = held.has_volumes and held.has_chapters
    top_volume = max(held.volumes) if held.volumes else None
    unmapped_for_chapters: List[VolumeInfo] = []   # rule (b): upgrades once every chapter is held
    if k.licensed and out_vols:
        compared = True
        for v in out_vols:
            vd = to_decimal(v.volume)
            if vd is None or vd in held.volumes:
                continue
            if v.has_chapters and _range_held(held, to_decimal(v.chapters_from), to_decimal(v.chapters_to)):
                gaps.append(Gap(GAP_UPGRADE, v.volume, note=f"chapters {v.chapters_from}-{v.chapters_to} held"))
            elif mixed and not v.has_chapters and top_volume is not None and vd > top_volume:
                # Owner rule (a), 2026-10-03: a newer volume whose chapters are not mapped yet is out
                # now - Upgrade available (the mapping may come later), not Missing volumes.
                gaps.append(Gap(GAP_UPGRADE, v.volume, note="volume out; its chapters are not mapped yet"))
            elif volume_collector:
                gaps.append(Gap(GAP_VOLUME, v.volume, note=_date_note(v)))
            elif not v.has_chapters:
                unmapped_for_chapters.append(v)
        reasons.append(f"{len(out_vols)} English volume(s) out")
    elif not k.licensed and held.has_volumes and k.scan_latest_volume:
        compared = True
        for n in range(1, int(k.scan_latest_volume) + 1):
            if Decimal(n) not in held.volumes:
                gaps.append(Gap(GAP_VOLUME, str(n), note="scanlation"))
        reasons.append(f"Scanlation volumes up to {fmt_num(k.scan_latest_volume)}")
    # MangaPixer's upgrade volumes feed Upgrade available (volumes it found held only as chapters).
    if completion and completion.upgrade_volumes:
        have = {g.start for g in gaps if g.kind == GAP_UPGRADE}
        for v in completion.upgrade_volumes:
            vd = to_decimal(v)
            if vd is not None and vd not in held.volumes and fmt_num(vd) not in have:
                gaps = [g for g in gaps if not (g.kind == GAP_VOLUME and to_decimal(g.start) == vd)]
                gaps.append(Gap(GAP_UPGRADE, fmt_num(vd), note="MangaPixer"))

    # 3. Chapters.
    chapters_checked = False
    target = _chapter_target(k)
    if target is not None and (held.has_chapters or not k.licensed or not held.has_volumes):
        if held.has_chapters or not held.has_volumes or held.covered:
            compared = True
            start = Decimal(1)
            if held.has_volumes and not held.covered and held.has_chapters:
                # Volumes whose chapters are unknown: count from the first loose chapter held.
                start = min(list(held.chapters) + [lo for lo, _ in held.chapter_ranges])
            missing = [c for c in _whole_numbers(start, target) if not held.holds_chapter(c)]
            if target != target.to_integral_value() and not held.holds_chapter(target):
                missing.append(target)
            missing += [p for p in held.split_missing if p <= target and p not in missing]
            gaps += _chapter_gaps(missing)
            chapters_checked = True
            reasons.append(f"Latest chapter {fmt_num(target)}")
    elif target is not None and held.has_volumes and k.licensed and not out_vols:
        reasons.append("No English volume list to compare with")

    # Owner rule (b), 2026-10-03: a chapter-only folder of a licensed series never shows Missing
    # volumes; a released volume is an upgrade - provided every chapter is present.
    if unmapped_for_chapters and chapters_checked and not any(g.kind == GAP_CHAPTER for g in gaps):
        gaps += [Gap(GAP_UPGRADE, v.volume, note="every chapter held; the volume is out") for v in unmapped_for_chapters]

    if not compared:
        return finish(State.CANT_TELL, reasons=["No volume or chapter numbers to compare with"])

    gaps = _sorted_gaps(gaps)
    missing_vol = any(g.kind == GAP_VOLUME for g in gaps)
    missing_ch = any(g.kind == GAP_CHAPTER for g in gaps)
    upgrade = any(g.kind == GAP_UPGRADE for g in gaps)
    complete = _is_complete(k, held, missing_vol or missing_ch)
    if behind_override == "done":
        reasons.append("Marked up to date by the owner")
        return finish(State.COMPLETE if complete else State.UP_TO_DATE, gaps, reasons)
    if missing_vol:
        return finish(State.MISSING_VOLUMES, gaps, reasons)
    if missing_ch:
        return finish(State.MISSING_CHAPTERS, gaps, reasons)
    if complete:
        # Owner rule (c), 2026-10-03: finished in origin and every chapter held = Complete, also when
        # official volumes are (being) released - then "Complete + Upgrade available" (a flag).
        return finish(State.COMPLETE, gaps, reasons + ["Finished, and held whole"])
    if upgrade:
        return finish(State.UPGRADE, gaps, reasons)
    return finish(State.UP_TO_DATE, gaps, reasons)


def _date_note(v: VolumeInfo) -> Optional[str]:
    return f"English {v.english_date}" if v.english_date else None


def _range_held(held: _Held, lo: Optional[Decimal], hi: Optional[Decimal]) -> bool:
    if lo is None or hi is None:
        return False
    numbers = _whole_numbers(lo, hi) | {lo, hi}
    return all(held.holds_chapter(c) for c in numbers)


def _chapter_target(k: SeriesKnowledge) -> Optional[Decimal]:
    """The last chapter that exists anywhere: the largest of MangaUpdates' latest chapter, the English
    chapter count and the finished total."""
    cands = [d for d in (k.latest_chapter_decimal, k.publisher_chapters,
                         k.total_chapters if k.finished_in_origin else None) if d is not None and d > 0]
    return max(cands) if cands else None


def _is_complete(k: SeriesKnowledge, held: _Held, missing: bool) -> bool:
    """Finished, and the folder holds the whole edition it collects."""
    if missing:
        return False
    if k.licensed and held.has_volumes and not held.has_chapters:
        return k.english_finished
    if not k.finished_in_origin:
        return False
    if k.translation_complete is True:
        return True
    if k.total_chapters is not None and held.highest_chapter is not None:
        return held.highest_chapter >= k.total_chapters
    return k.licensed and k.english_finished


def _sorted_gaps(gaps: Iterable[Gap]) -> List[Gap]:
    order = {GAP_VOLUME: 0, GAP_CHAPTER: 1, GAP_UPGRADE: 2}
    seen = set()
    out = []
    for g in sorted(gaps, key=lambda g: (order[g.kind], to_decimal(g.start) or 0)):
        key = (g.kind, g.start, g.end)
        if key not in seen:
            seen.add(key)
            out.append(g)
    return out


# --- FALLBACK: inventory from a MangaEntry (until the inventory lane is wired) ---------------------
#
# The integrator replaces this with mangalist.inventory's real per-series inventory. It reads only what
# a MangaEntry already has: each file's name and the classifier's volume / chapter flags.

_RE_FB_VOL = re.compile(r"(?<![A-Za-z])(?:vol(?:ume)?\.?|v)\s*[_\-.]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_RE_FB_CH = re.compile(
    r"(?<![A-Za-z])(?:ch(?:apter|ap|p|\.)?|c)\s*[_\-.]?\s*(\d{1,4}(?:\.\d+)?)(?:\s*[-–]\s*(\d{1,4}(?:\.\d+)?))?",
    re.IGNORECASE)


def fallback_inventory_from_entry(entry: Any) -> InventorySnapshot:
    """FALLBACK inventory from a MangaEntry's file names (the classifier's per-file flags): a volume
    archive holds its volume (a ``v01 (c001-008)`` name also covers those chapters), a chapter archive
    its chapter (or range), a file with neither token is of unknown kind."""
    vols: Set[str] = set()
    chs: List[Any] = []
    covered: List[Any] = []
    unknown: List[str] = []
    for hit in getattr(entry, "files", ()) or ():
        name = hit.path.name
        stem = name.rsplit(".", 1)[0] if "." in name else name
        vm = _RE_FB_VOL.search(stem)
        cm = _RE_FB_CH.search(stem)
        ch_lo = cm.group(1) if cm else None
        ch_hi = cm.group(2) if cm else None
        is_range = ch_hi is not None and to_decimal(ch_hi) != to_decimal(ch_lo)
        if vm and (not cm or is_range or not getattr(hit, "has_chapter", True)):
            vols.add(fmt_num(vm.group(1)))
            if cm:
                covered.append((ch_lo, ch_hi or ch_lo))
        elif cm and getattr(hit, "has_chapter", True):
            chs.append((ch_lo, ch_hi) if is_range else fmt_num(ch_lo))
        elif vm:
            vols.add(fmt_num(vm.group(1)))
        else:
            unknown.append(name)
    return InventorySnapshot(held_volumes=sorted(vols, key=to_decimal), held_chapters=chs,
                             chapters_covered_by_volumes=covered, unknown_kind_files=unknown)


def state_for_entry(entry: Any, *, inventory: Any = None, knowledge: Optional[SeriesKnowledge] = None,
                    needs_kind: bool = False, today: Optional[_dt.date] = None) -> SeriesState:
    """The state of a MangaEntry row: the given inventory / knowledge, else the FALLBACK inventory
    (:func:`fallback_inventory_from_entry`) and the own matcher's knowledge."""
    from .knowledge import from_own_matcher

    if inventory is None:
        inventory = fallback_inventory_from_entry(entry)
    if knowledge is None:
        knowledge = from_own_matcher(entry)
    files = getattr(entry, "files", None) or ()
    return compute_state(inventory, knowledge, folder_empty=len(files) == 0, needs_kind=needs_kind,
                         behind_override=getattr(entry, "behind_override", None), today=today)
