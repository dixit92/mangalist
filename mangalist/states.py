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
   Without an English volume out, every chapter up to the latest known one is a gap (Suwayomi's); with
   chapters known and no volume announced (a webtoon) the state is plain *Wanted*.
2. **Nothing known** (no linked record, or no number to compare with) -> *Can't tell*.
3. Gaps: **volumes** - an English volume that is out, not held as a volume and not wholly held as
   chapters is missing (in a folder that holds volumes, or holds nothing but volumes' worth); a volume
   whose chapters are all held as chapters is an **upgrade** (a listed chapter 0 - a prologue MangaDex puts in
   volume 1 - only when a file numbered 0 is held: MangaPixer 1.43.2's rule, owner 2026-10-16). Without an English edition, the
   scanlation's latest volume is the target for a folder that holds volumes. **Chapters** (a folder
   that holds chapter files; an unlicensed folder of volumes only with Unbundled Chapters on, below) -
   every whole chapter from the first expected one (1; a lone chapter 0 is never missing) up to the latest known one (MangaUpdates' latest
   chapter, the English chapter count, the finished total - the largest) that is held neither as a
   chapter nor inside a held volume, plus the latest chapter itself when it is a decimal (``24.5``).
   An **unlicensed folder that holds volumes only**, Unbundled Chapters off (owner, 2026-10-15:
   "Start clean: they all stop wanting chapters until you tick Unbundled on the ones you want" - the
   older rule compared every unlicensed series' chapters, so a folder of volumes 1-15 showed "Missing
   chapters 54-94"), compares no chapter: the chapters out count as volumes ("They shouldn't be
   marked as completed"). The volume list's (original edition) volumes whose chapters are all out and
   that the folder lacks are *Missing volumes*; chapters out past the last volume known (held, such a
   volume, or a scanlation volume the list maps) are one open-ended gap after it (``Vol. 16+``,
   :attr:`Gap.open_end`); the reason says so and points to Unbundled chapters. Where the list does not
   say which chapters the newest held volume holds, no chapter is compared; the original edition's volume count
   (MangaUpdates) gives the volume gaps instead - the volumes up to it that the folder lacks (owner, 2026-10-16,
   question 83: "yes"); without a count nothing is compared, as before.
4. State by priority: *Missing volumes* > *Missing chapters* > *Upgrade available* > *Complete*
   (finished and held whole in the edition the folder collects) > *Up to date*.

What MangaPixer declares for the folder comes first (MangaPixer 1.39.0, the backlog's "Honour MangaPixer's declared
edition and tracking off"): **Track completion off** -> *Not tracked* (no gap, no Complete; out of To get and the find
pass); a **declared edition** with its own volume count (an omnibus / master / deluxe edition) -> its volumes 1..N are
counted by the volume files the folder holds, no upgrade is offered and the state names the edition
(:attr:`SeriesState.edition`).

**MangaDex's released chapters** (MangaPixer 1.42.0 ``companions.mangadexReleased``, its highest whole chapter) fill in
only where nothing else tells: when MangaUpdates, the English edition and a finished total give no chapter target, or
when without it the state would be *Can't tell*. Not "the highest wins" everywhere: MangaPixer reads MangaDex's list
with its unavailable (taken-down) chapters, and on the owner's library (2026-10-13) 19 of the 95 series it would raise
list chapters MangaDex no longer serves (the lane note "MangaPixer Export Fields" has the measurement). Where MangaPixer
(1.43.0) exports the **readable** list too (``mangadexReleased.readable``, taken-down chapters left out), its highest
whole chapter is a chapter target for every series instead - the largest of the targets wins - and the released
list's latest chapter is not used (owner, 2026-10-13, answer 8); nothing readable adds no target.

**Unbundled Chapters** (a per-series setting, :mod:`mangalist.unbundled`; owner, 2026-10-11: "a setting to convert a
volume-only series to a volumes + unbundled chapters series, with both upgrade and new missing chapter downloads
applicable"; for an unlicensed series, the only way its folder of volumes wants chapters - rule 3): for a folder that
holds volumes, the chapters after the last volume - where the newest volume that is
held, missing or an upgrade ends by MangaPixer's volume list - up to the chapter target are compared too (*Missing
chapters*). The volume side is unchanged, and the setting only ever adds gaps. A chapter a held volume collects is never
wanted; in a folder of volumes only, nor is one a released volume the folder lacks collects (that volume is the gap).
When a later volume comes out, the chapters it collects become an *Upgrade* (every one held) or it is a *Missing
volume* (some not held), as in any folder holding chapters, and the unbundled range moves past it. Where the list does
not say which chapter the newest held volume ends with, the setting asks the owner once for "the first chapter to get"
(owner, 2026-10-15, question 14: "Yes"; stored per series, editable later, :mod:`mangalist.unbundled`): with that answer
the chapters from it are compared, and those between the newest mapped volume's end and it count as held by the
unmapped volumes; without it (or once a newer unmapped volume arrived after it was given) the folder is compared as
without the setting, and the series needs attention (:attr:`SeriesState.unbundled_ask`). A volume that is not out in
English (the original edition's volume, compared where there is no English volume list) does not move the start: with
the setting on its chapters are wanted as chapters too (owner, 2026-10-15, question 15: "the English scanlation may be
at parity with the original source"), and the volume is kept as an *upgrade* rather than a missing volume (owner,
2026-10-16, question 73: "Keep both, treat the volume as an upgrade") - the series reads *Missing chapters* while its
chapters are missing and *Upgrade available* once they are held; a volume whose chapters lie before where the unbundled
chapters start stays a missing volume (only it brings them). A Complete series may have the setting (question
16: "Allow it - then the unbundled chapters can be upgraded once the volumes are out").

The inventory interface is the inventory lane's (``mangalist/inventory.py``); :class:`InventorySnapshot`
is a stand-in with the same attributes, and :func:`fallback_inventory_from_entry` builds one from what
a :class:`~mangalist.models.MangaEntry` already has (FALLBACK until the real inventory is wired).
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, replace
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
    NOT_TRACKED = "Not tracked"            # MangaPixer: "Track completion" is off for the folder (1.39.0)
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
    State.UP_TO_DATE, State.COMPLETE, State.CANT_TELL, State.NOT_TRACKED, State.NOT_A_SERIES,
)

# Needs-attention reasons.
ATTENTION_UNMATCHED = "unmatched"
ATTENTION_REVIEW = "needs review"
ATTENTION_KIND = "needs kind"
ATTENTION_NOT_A_WORK = "not one work"
ATTENTION_IN_VOLUMES = "chapters in volumes"
ATTENTION_UNBUNDLED_START = "unbundled start"
ATTENTION_LABELS = {
    ATTENTION_UNMATCHED: "No MangaUpdates match",
    ATTENTION_REVIEW: "Match needs review",
    ATTENTION_KIND: "Volumes or chapters? (bare numbers)",
    ATTENTION_NOT_A_WORK: "Not matched: the folder is not one work",
    ATTENTION_IN_VOLUMES: "Chapter files inside volumes you hold - row menu: Set aside chapters its volumes hold…",
    ATTENTION_UNBUNDLED_START: "Unbundled chapters: where the newest volume ends is not known - row menu: "
                               "First chapter to get…",
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
    inside_volumes: int = 0        # loose chapter files inside a held volume's mapped range (owner, 2026-10-14)

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
    # Loose chapter files a held volume already holds by the volume list's MAPPED range - however the volume arrived
    # (owner, 2026-10-14: volumes that came in outside MangaList's downloads kept their chapter files; the upgrade step
    # only looks at its own downloads). Counted for a hint only; nothing moves until the owner sets them aside.
    spans = [(to_decimal(v.chapters_from), to_decimal(v.chapters_to)) for v in (knowledge.volumes if knowledge else ())
             if to_decimal(v.volume) in vols and v.has_chapters]
    spans = [(lo, hi) for lo, hi in spans if lo is not None and hi is not None and lo <= hi]

    parts = splits_of(chs, ranges).parts if spans else frozenset()    # N.1, N.2 ... count by chapter N (N.5: extra)

    def _inside(lo: Decimal, hi: Decimal) -> bool:
        whole = lo.to_integral_value(rounding="ROUND_FLOOR")
        return any((a <= lo and hi <= b) or (lo == hi and lo in parts and a <= whole <= b) for a, b in spans)

    inside = sum(1 for c in chs if _inside(c, c)) + sum(1 for lo, hi in ranges if _inside(lo, hi)) if spans else 0
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
        # A volume whose chapters start at N.1 - part 1 of a chapter released in parts - is where chapter N begins: it
        # counts as covered (owner, 2026-10-11: every volume of a complete series held, the list's volume 3 "11.1-16",
        # and chapter 11 showed as missing). A start like N.5 (an extra) says nothing about chapter N.
        if lo is not None and lo != lo.to_integral_value() and lo - lo.to_integral_value(rounding="ROUND_FLOOR") == Decimal("0.1"):
            covered.add(lo.to_integral_value(rounding="ROUND_FLOOR"))
    hv = to_decimal(getattr(inv, "highest_volume", None))
    hc = to_decimal(getattr(inv, "highest_chapter", None))
    if hv is None and vols:
        hv = max(vols)
    if hc is None and (chs or ranges):
        hc = max(list(chs) + [hi for _, hi in ranges])
    return _Held(frozenset(vols), frozenset(chs), tuple(ranges), frozenset(covered), hv, hc,
                 _count(getattr(inv, "unknown_kind_files", None)), splits.missing_parts, inside)


def _whole_numbers(lo: Decimal, hi: Decimal) -> Set[Decimal]:
    start = int(lo) if lo == lo.to_integral_value() else int(lo) + 1
    if lo < 0:
        start = max(start, 0)
    return {Decimal(n) for n in range(start, int(hi) + 1)}


# --- result ----------------------------------------------------------------------------------------

GAP_VOLUME = "volume"
GAP_CHAPTER = "chapter"
GAP_UPGRADE = "upgrade"

UNBUNDLED_FLAG = "Unbundled chapters"   # the flag a series with the Unbundled Chapters setting on carries


@dataclass(frozen=True)
class Gap:
    kind: str                      # GAP_VOLUME | GAP_CHAPTER | GAP_UPGRADE
    start: str                     # exact number
    end: Optional[str] = None      # a range's last number (None for one unit)
    note: Optional[str] = None     # e.g. "English 2025-07", "chapters 17-24.5"
    open_end: bool = False         # "Vol. 16+": this volume and the ones after it, how many not known (an unlicensed
                                   # volume folder's chapters past the volumes the volume list knows)

    @property
    def label(self) -> str:
        unit = "Ch." if self.kind == GAP_CHAPTER else "Vol."
        rng = self.start if self.end is None else f"{self.start}-{self.end}"
        return f"{unit} {rng}{'+' if self.open_end else ''}"

    @property
    def size(self) -> int:
        """Whole units in the gap (a decimal single counts 1)."""
        if self.end is None:
            return 1
        lo, hi = to_decimal(self.start), to_decimal(self.end)
        return max(1, int(hi) - int(lo) + 1) if lo is not None and hi is not None else 1


@dataclass(frozen=True)
class UnbundledAsk:
    """The Unbundled Chapters question "the first chapter to get" of a series whose newest held volume the volume list
    does not map (owner, 2026-10-15, question 14): what the prompt shows, and the answer in force (:attr:`current`).
    Exact numbers as text."""

    volume: str                          # the newest held volume (the list does not say where it ends)
    mapped_volume: Optional[str] = None  # the newest volume below it that the list maps ...
    mapped_end: Optional[str] = None     # ... and the chapter that one ends with
    suggestion: int = 1                  # an estimate of the first chapter after :attr:`volume` (chapters per volume)
    current: Optional[str] = None        # the owner's answer in force (None: not given yet, or given before a newer
                                         # unmapped volume arrived - :attr:`given_for`)
    given_for: Optional[str] = None      # the newest held volume when the answer was given

    @property
    def needed(self) -> bool:
        """No answer in force: the chapters after the volumes are not compared until the owner gives one."""
        return self.current is None

    @property
    def stale(self) -> bool:
        """An answer was given, for an older newest volume: a newer unmapped volume arrived since."""
        return self.current is None and self.given_for is not None


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
    edition: Optional[str] = None                # the edition declared in MangaPixer, e.g. "Master edition"
    unbundled: bool = False                      # the series' Unbundled Chapters setting is on (mangalist.unbundled)
    unbundled_ask: Optional[UnbundledAsk] = None # where its unbundled chapters start depends on the owner's answer

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
        if self.edition:
            out.append(self.edition)
        if self.complete_with_upgrade:
            out.append("Upgrade available")
        if self.unbundled:
            out.append(UNBUNDLED_FLAG)
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
    def missing_volumes_text(self) -> str:
        """The missing volume numbers as text: ``3, 5-6``; an open-ended gap last: ``3, 16+`` (:attr:`Gap.open_end`)."""
        gaps = self.gaps_of(GAP_VOLUME)
        vols = _compress([g.start for g in gaps if not g.open_end]) + [f"{g.start}+" for g in gaps if g.open_end]
        return ", ".join(vols)

    @property
    def gaps_text(self) -> str:
        """Short form for a table cell: ``Vol. 3, 5-6 · Ch. 10-12, 24.5 · Upgrade vol. 1-2`` (``Vol. 16+``: an
        open-ended volume gap)."""
        parts = []
        vols = self.missing_volumes_text
        if vols:
            parts.append("Vol. " + vols)
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
    restarts: Collection[str] = (),
    unbundled: Any = False,
) -> SeriesState:
    """The state of one series folder.

    *inventory* is an :class:`InventoryLike` (None = nothing held); *knowledge* None = nothing known.
    *folder_empty*: the folder holds no archive at all (A7: wanted). *needs_kind*: the owner has not
    said yet whether the folder's bare numbers are volumes or chapters (C12). *requested* and
    *rename_pending* are passed through as flags (*rename_count*: how many files the renamer would rename;
    a count alone also sets the flag).
    *behind_override* ``"done"``: the owner marked the series up to date - number gaps are kept for the
    tooltip but the state is Up to date (or Complete).
    *restarts*: ``"chapter"`` / ``"volume"`` when those numbers start again in another folder of the series (``Season 1``
    and ``Season 2`` both from 1 - :attr:`mangalist.models.MangaEntry.restarting_units`): they are not compared with one
    total, so no gap of that kind is invented (MangaPixer gives no verdict either).
    *unbundled*: the series' Unbundled Chapters setting (:mod:`mangalist.unbundled`) - a folder holding volumes also
    wants the chapters after its last volume (see the module docstring); off (the default) changes nothing - except that
    an unlicensed folder of volumes only then counts the chapters out past its volumes as volume gaps. True, or
    the setting's :class:`~mangalist.unbundled.UnbundledChoice` (any true object): its ``first_chapter`` /
    ``for_volume`` are the owner's "first chapter to get", used where the volume list does not map the newest held
    volume.

    MangaDex's readable English chapters (:attr:`~mangalist.knowledge.SeriesKnowledge.mangadex_readable`, MangaPixer
    1.43.0), where known: their highest whole chapter joins the chapter target of every series (the largest wins, as
    with the other sources); nothing readable adds no target, and the released list's latest chapter is then not used
    at all. Where they are not known yet, MangaDex's highest whole English chapter listed
    (:attr:`~mangalist.knowledge.SeriesKnowledge.mangadex_latest`, taken-down ones included) is a chapter target only
    where nothing else tells (see the module docstring): the state is computed without it first, and again with it
    when there is no other target or the first answer is *Can't tell*.
    """
    args = dict(folder_empty=folder_empty, needs_kind=needs_kind, requested=requested, rename_pending=rename_pending,
                rename_count=rename_count, behind_override=behind_override, today=today or _dt.date.today(),
                restarts=restarts, unbundled=unbundled)
    st = _evaluate(inventory, knowledge, **args)
    k = knowledge
    if k is None or not k.matched or k.tracking_off or k.edition_label or st.state in (State.NOT_A_SERIES,
                                                                                       State.NOT_TRACKED):
        return st
    readable = k.mangadex_readable
    if readable is not None:
        # Owner, 2026-10-13 (answer 8): the readable list has only chapters a source can give, so it counts everywhere -
        # the new chapters MangaUpdates has not recorded yet show, and taken-down chapters make no gap.
        extra = readable.latest_whole
        if extra is not None and k.finished_in_origin and k.total_chapters is not None and extra > k.total_chapters:
            # a finished series' known total caps it: MangaDex's readable list can number an extra after the last
            # chapter, and a Complete series must not turn Missing chapters for it (integrator, 2026-10-17)
            extra = k.total_chapters
        base = _chapter_target(k)
        if extra is None or (base is not None and extra <= base):
            return st
        alt = _evaluate(inventory, knowledge, extra_target=extra, **args)
        if alt.state == st.state and alt.gaps == st.gaps:
            return st
        told = "nothing else gives a latest chapter" if base is None else f"MangaUpdates says {fmt_num(base)}"
        return replace(alt, reasons=alt.reasons + (f"MangaDex has readable English chapters up to {fmt_num(extra)} "
                                                   f"({told})",))
    extra = k.mangadex_latest
    if extra is None:
        return st
    base = _chapter_target(k)
    if base is not None and not (extra > base and st.state == State.CANT_TELL):
        return st
    alt = _evaluate(inventory, knowledge, extra_target=extra, **args)
    if base is not None and alt.state == State.CANT_TELL:
        return st
    if alt.state == st.state and alt.gaps == st.gaps:
        return st
    told = "nothing else gives a latest chapter" if base is None else f"MangaUpdates says {fmt_num(base)}"
    why = f"MangaDex lists English chapters up to {fmt_num(extra)} ({told})"
    return replace(alt, reasons=alt.reasons + (why,))


def _evaluate(
    inventory: Any,
    knowledge: Optional[SeriesKnowledge],
    *,
    folder_empty: bool,
    needs_kind: bool,
    requested: bool,
    rename_pending: bool,
    rename_count: int,
    behind_override: Optional[str],
    today: _dt.date,
    restarts: Collection[str],
    unbundled: Any = False,
    extra_target: Optional[Decimal] = None,
) -> SeriesState:
    """:func:`compute_state`'s rules; *extra_target* joins the chapter target's candidates (MangaDex's latest)."""
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
    if held.inside_volumes:
        attention.append(ATTENTION_IN_VOLUMES)

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
        edition=k.edition_label if k is not None else None,
        unbundled=bool(unbundled),
    )
    if k is not None and k.tracking_off:
        # MangaPixer 1.39.0 "Track completion: off": the folder is a series (named, renamed, matched) but has no
        # answer - nothing missing, no upgrade, not Complete, not wanted even when empty (the backlog's "Honour
        # MangaPixer's declared edition and tracking off"; MangaPixer answers Can't tell / NotTracked).
        return SeriesState(state=State.NOT_TRACKED, reasons=("Track completion is off for this folder in MangaPixer",),
                           needs_attention=common["needs_attention"], rename_pending=rename_pending,
                           rename_count=rename_count, source=k.source, unbundled=bool(unbundled))
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

    if known and k.edition_label is not None:
        return _edition_state(k, held, folder_empty=folder_empty, behind_override=behind_override, finish=finish)

    # 1. Empty folder = wanted.
    if folder_empty:
        if not known:
            return finish(State.WANTED, reasons=["Empty folder; the series is not matched yet"])
        if k.licensed:
            if out_vols:
                return finish(State.WANTED_OFFICIAL, [Gap(GAP_VOLUME, v.volume, note=_date_note(v)) for v in out_vols],
                              [f"Empty folder; {len(out_vols)} English volume(s) out"])
            # No English volume (a webtoon never gets one): the chapters up to the latest known one, which the
            # Download tab offers from Suwayomi (owner, 2026-10-11: an empty licensed webtoon offered nothing)
            target = _chapter_target(k, extra_target)
            gaps = _chapter_gaps(_empty_folder_chapters(target)) if target is not None else []
            latest = f"; latest chapter {fmt_num(target)}" if target is not None else ""
            if (k.publisher_chapters or 0) > 0:
                return finish(State.WANTED_OFFICIAL, gaps, [f"Empty folder; English chapters out{latest}"])
            if gaps and not (upcoming_vol and upcoming_vol.english_date):
                # chapters out and no volume announced (a webtoon): plain Wanted (owner, 2026-10-11)
                return finish(State.WANTED, gaps, [f"Empty folder; licensed, no English volume announced{latest}"])
            when = f" (announced {upcoming_vol.english_date})" if upcoming_vol and upcoming_vol.english_date else ""
            return finish(State.WANTED_AWAITING, gaps,
                          [f"Empty folder; licensed, no English volume out yet{when}{latest}"])
        # Scanlation only: every chapter up to the latest known one is missing - the Download tab's Missing chapters
        # group offers them from Suwayomi (owner, 2026-10-10: "should be fixed now that we have a scanlation provider").
        target = _chapter_target(k, extra_target)
        if target is None:
            return finish(State.WANTED_SCANLATION, reasons=["Empty folder; not licensed in English"])
        return finish(State.WANTED_SCANLATION, _chapter_gaps(_empty_folder_chapters(target)),
                      [f"Empty folder; not licensed in English; latest chapter {fmt_num(target)}"])

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
    original_only: Set[Decimal] = set()            # missing volumes of the original edition, not out in English
    if "volume" in restarts:
        reasons.append("Volume numbers start again in each folder of the series - not compared")
    elif k.licensed and out_vols:
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
    elif k.licensed and held.has_volumes and not out_vols and _jp_volumes_out(k, extra_target):
        # No English volume list (the English edition is chapters in an app): the original edition's volumes whose
        # chapters are all out in English - nyaa often has the English chapters compiled into them (owner, 2026-10-11:
        # "if English volume list is not available but JP is, compare with JP").
        compared = True
        jp_out = _jp_volumes_out(k, extra_target)
        for v in jp_out:
            vd = to_decimal(v.volume)
            if vd is None or vd in held.volumes:
                continue
            if _range_held(held, to_decimal(v.chapters_from), to_decimal(v.chapters_to)):
                gaps.append(Gap(GAP_UPGRADE, v.volume, note=f"chapters {v.chapters_from}-{v.chapters_to} held"))
            else:
                gaps.append(Gap(GAP_VOLUME, v.volume, note=f"original volume; chapters {v.chapters_from}-"
                                                            f"{v.chapters_to} out in English"))
                original_only.add(vd)
        reasons.append(f"No English volume list: {len(jp_out)} original volume(s) whose chapters are out in English")
    elif not k.licensed and held.has_volumes and _scan_volume_total(k):
        compared = True
        total = _scan_volume_total(k)
        for n in range(1, int(total) + 1):
            if Decimal(n) not in held.volumes:
                gaps.append(Gap(GAP_VOLUME, str(n), note="scanlation"))
        reasons.append(f"Scanlation volumes up to {fmt_num(total)}")
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
    wanted_chapters: Set[Decimal] = set()          # the chapter numbers the gaps below ask for
    target = _chapter_target(k, extra_target)
    if "chapter" in restarts:
        reasons.append("Chapter numbers start again in each folder of the series (e.g. each season) - not compared")
        target = None
    unbundled_missing: Optional[List[Decimal]] = None
    unbundled_target = target
    if unbundled and held.has_volumes and target is not None:
        # Unbundled Chapters (the series' setting): the chapters after the last volume, whatever the folder holds so
        # far - added to what the rules below compare (the setting never takes a gap away). Where the last volume ends
        # is not known, the owner's "first chapter to get" says where to start; without it the folder is compared as
        # without the setting (and needs attention).
        start = _unbundled_start(k, held, gaps, unbundled, original_only)
        reasons.append(start.why)
        if start.ask is not None:
            common["unbundled_ask"] = start.ask
            if start.ask.needed:
                common["needs_attention"] = tuple(dict.fromkeys(common["needs_attention"] + (ATTENTION_UNBUNDLED_START,)))
        if start.after is not None:
            compared = chapters_checked = True
            if start.assumed:
                # the owner's answer: the unmapped volumes hold the chapters up to it (never wanted as chapters)
                held = replace(held, covered=held.covered | frozenset(start.assumed))
            unbundled_missing = [c for c in _whole_numbers(start.after, target) if start.wants(c)
                                 and not held.holds_chapter(c)]
            if target != target.to_integral_value() and start.wants(target) and not held.holds_chapter(target):
                unbundled_missing.append(target)
            unbundled_missing += [p for p in held.split_missing if start.wants(p) and p <= target
                                  and p not in unbundled_missing and not held.holds_chapter(p)]
    # A volumes-only folder holding volumes the volume list does not map: which chapters those hold is unknown, so the
    # chapter numbers are not compared (owner, 2026-10-11: every volume of a finished 4-koma held, the list mapped only
    # volumes 1-3, and MangaUpdates' strip count 717 made "missing ch 261-717").
    unmapped_target: Optional[Decimal] = None      # the chapter target set aside because of that
    if target is not None and held.has_volumes and not held.has_chapters:
        mapped = {to_decimal(v.volume) for v in (k.volumes or ()) if v.has_chapters}
        unmapped = sorted(v for v in held.volumes if v not in mapped)
        if unmapped and (not mapped or max(unmapped) > max(mapped)):
            if not unbundled:           # the unbundled reason has said so already
                reasons.append(f"The volume list does not say which chapters volume(s) {_span(unmapped)} hold - "
                               "chapters not compared")
            target, unmapped_target = None, target
    # An unlicensed folder of volumes only compares chapters with Unbundled Chapters on (owner, 2026-10-15: "Start
    # clean: they all stop wanting chapters until you tick Unbundled on the ones you want"); without it the chapters out
    # past its volumes are volume gaps (below).
    volumes_only = held.has_volumes and not held.has_chapters
    if target is not None and (held.has_chapters or not held.has_volumes or (not k.licensed and unbundled)):
        if held.has_chapters or not held.has_volumes or held.covered:
            compared = True
            start = Decimal(1)
            if held.has_volumes and not held.covered and held.has_chapters:
                # Volumes whose chapters are unknown: count from the first loose chapter held.
                start = min(list(held.chapters) + [lo for lo, _ in held.chapter_ranges])
            missing = [c for c in _whole_numbers(start, target) if not held.holds_chapter(c)]
            if target != target.to_integral_value() and not held.holds_chapter(target):
                missing.append(target)
            # a missing part the folder holds after all (a range file, a volume) is not missing (owner, 2026-10-14)
            missing += [p for p in held.split_missing if p <= target and p not in missing and not held.holds_chapter(p)]
            if unbundled_missing is not None:
                missing, unbundled_missing = sorted(set(missing) | set(unbundled_missing)), None
            gaps += _chapter_gaps(missing)
            wanted_chapters.update(missing)
            chapters_checked = True
            reasons.append(f"Latest chapter {fmt_num(target)}")
    elif target is not None and volumes_only and not k.licensed:
        compared = True
        more, why = _unlicensed_volume_gaps(k, held, gaps, target, extra_target)
        gaps += more
        reasons.append(why)
    elif unmapped_target is not None and volumes_only and not k.licensed and not unbundled and k.origin_volumes:
        # The newest held volume is one the list does not map (owner, 2026-10-16, question 83: "yes"): the original
        # edition's volume count gives the volume gaps instead - no chapter is compared, and with Unbundled chapters on
        # the "first chapter to get" says where the chapters start (above).
        compared = True
        more, why = _unlicensed_volume_gaps(k, held, gaps, unmapped_target, extra_target, count=k.origin_volumes)
        gaps += more
        reasons.append(why)
    elif (target is not None and held.has_volumes and k.licensed and not out_vols
          and not _jp_volumes_out(k, extra_target)):
        reasons.append("No English volume list to compare with")

    if unbundled_missing is not None:           # a folder the rules above do not compare on chapters (volumes only)
        gaps += _chapter_gaps(unbundled_missing)
        wanted_chapters.update(unbundled_missing)
        reasons.append(f"Latest chapter {fmt_num(unbundled_target)}")

    # Owner, 2026-10-16 (question 73: "Keep both, treat the volume as an upgrade"): with Unbundled chapters on, a missing
    # volume of the original edition that is not out in English, whose chapters are all held or wanted as chapters, is
    # an upgrade - its chapters come as chapters, and the volume replaces them when it comes (the find pass looks for it
    # as it looks for any upgrade). Once every chapter is held it is the plain upgrade of rule 3.
    if unbundled and original_only and wanted_chapters:
        gaps = [_original_as_upgrade(g, k, held, wanted_chapters) if g.kind == GAP_VOLUME
                and to_decimal(g.start) in original_only else g for g in gaps]

    # Owner rule (b), 2026-10-03: a chapter-only folder of a licensed series never shows Missing
    # volumes; a released volume is an upgrade - provided every chapter is present.
    if unmapped_for_chapters and chapters_checked and not any(g.kind == GAP_CHAPTER for g in gaps):
        gaps += [Gap(GAP_UPGRADE, v.volume, note="every chapter held; the volume is out") for v in unmapped_for_chapters]

    if not compared:
        why = [r for r in reasons if "start again" in r]
        return finish(State.CANT_TELL, reasons=why or ["No volume or chapter numbers to compare with"])

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


def _holds_zero(held: _Held) -> bool:
    """The folder holds a file numbered 0 (``0``, ``0.5``, a range from 0) or a volume that collects it."""
    return (any(Decimal(0) <= c < 1 for c in held.chapters | held.covered)
            or any(lo < 1 and hi >= 0 for lo, hi in held.chapter_ranges))


def _counted(held: _Held, numbers: Set[Decimal]) -> Set[Decimal]:
    """The chapters of *numbers* (a volume's mapped chapters) that must be held for the volume to count as held as
    chapters. A listed chapter 0 (``0``, ``0.5``) - a prologue, a pilot, an announcement MangaDex lists in volume 1, and
    many releases have none - counts only when a file numbered 0 is held (owner, 2026-10-16, MangaPixer 1.43.2's rule:
    a volume "0-6" with chapters 1-6 held showed chapter 0 as missing): so a volume "0-6" is held as chapters once 1-6
    are. A volume that maps nothing but chapter 0 keeps it (else nothing would be needed at all)."""
    rest = {c for c in numbers if not Decimal(0) <= c < 1}
    if rest == numbers or not rest or _holds_zero(held):
        return numbers
    return rest


def _range_held(held: _Held, lo: Optional[Decimal], hi: Optional[Decimal]) -> bool:
    """Every chapter the volume list maps to a volume (``lo``-``hi``: both ends and the whole numbers between) is held -
    a listed chapter 0 only when a file numbered 0 is (:func:`_counted`)."""
    if lo is None or hi is None:
        return False
    numbers = _counted(held, _whole_numbers(lo, hi) | {lo, hi})
    return all(held.holds_chapter(c) for c in numbers)


def _original_as_upgrade(g: Gap, k: SeriesKnowledge, held: _Held, wanted: Collection[Decimal]) -> Gap:
    """A missing original-edition volume *g* (not out in English) as an upgrade when Unbundled chapters gets its
    chapters: every whole chapter the volume list maps to it is held or *wanted* as a chapter (a listed chapter 0 only
    when a file numbered 0 is held, :func:`_counted`). Else *g* as it is (its chapters lie before where the unbundled
    chapters start, so only the volume brings them)."""
    v = next((x for x in k.volumes or () if x.has_chapters and to_decimal(x.volume) == to_decimal(g.start)), None)
    lo = to_decimal(v.chapters_from) if v is not None else None
    hi = to_decimal(v.chapters_to) if v is not None else None
    numbers = _counted(held, _whole_numbers(lo, hi)) if lo is not None and hi is not None and lo <= hi else set()
    if not numbers or not all(c in wanted or held.holds_chapter(c) for c in numbers):
        return g
    return Gap(GAP_UPGRADE, g.start, note=f"original volume; chapters {v.chapters_from}-{v.chapters_to} come as chapters "
                                          "(Unbundled chapters) - the volume replaces them")


def _unlicensed_volume_gaps(k: SeriesKnowledge, held: _Held, gaps: Sequence[Gap], target: Decimal,
                            extra_target: Optional[Decimal] = None,
                            count: Optional[Decimal] = None) -> Tuple[List[Gap], str]:
    """The volume gaps of an unlicensed folder that holds volumes only, Unbundled Chapters off (owner, 2026-10-15: "They
    shouldn't be marked as completed"), and the reason line: the chapters out (up to *target*) are not wanted as
    chapters, but they keep the series from Up to date / Complete as volumes. The volume list's (original edition)
    volumes whose chapters are all out and that the folder lacks are gaps (as :func:`_jp_volumes_out` counts them);
    chapters out past the last volume known - held, such a volume, or a scanlation volume already a gap (*gaps*) - make
    one open-ended gap after it (``Vol. 16+``, :attr:`Gap.open_end`), provided the list says which chapter that volume
    ends with (a scanlation volume it does not map may hold them). The caller has made sure the newest held volume is
    mapped - or gives *count*, the original edition's volume count, when the list does not map it (owner, 2026-10-16,
    question 83: "yes"): then the volumes 1..*count* the folder lacks are gaps too (the list maps none of them past the
    newest held volume, so whether their chapters are out in English is not known), and no chapter range is said for
    the newest held volume."""
    out: List[Gap] = []
    have = {to_decimal(g.start) for g in gaps if g.kind in (GAP_VOLUME, GAP_UPGRADE)}
    for v in _jp_volumes_out(k, extra_target):
        vd = to_decimal(v.volume)
        if vd is None or vd in held.volumes or vd in have:
            continue
        if _range_held(held, to_decimal(v.chapters_from), to_decimal(v.chapters_to)):
            continue                            # the held volumes collect its chapters after all
        out.append(Gap(GAP_VOLUME, v.volume, note=f"original edition, chapters {v.chapters_from}-{v.chapters_to}; "
                                                  "not licensed in English"))
    if count is not None:
        listed = {to_decimal(g.start) for g in out}
        for n in range(1, int(count) + 1):
            vd = Decimal(n)
            if vd in held.volumes or vd in have or vd in listed:
                continue
            out.append(Gap(GAP_VOLUME, str(n), note=f"original edition, {fmt_num(count)} volumes; not licensed in "
                                                    "English"))
        out.sort(key=lambda g: to_decimal(g.start) or 0)
    top = max(held.volumes)
    last = max([top] + [to_decimal(g.start) for g in out] + [d for d in have if d is not None])
    end = last_volume_chapter(k.volumes, last)
    # a finished series whose last volume known is its final one (the original edition's volume count): chapters the
    # volume list does not map belong to that volume - no "Vol. 9+" (owner, 2026-10-17: a complete series of 8 volumes,
    # all held; MangaDex's list maps volume 8 to 36-38 only, MangaUpdates says 40 chapters)
    final = (k.finished_in_origin and k.origin_volumes is not None
             and last >= to_decimal(k.origin_volumes))
    if (end is not None and target > end and not final
            and any(v.has_chapters and to_decimal(v.volume) == last for v in k.volumes)):
        out.append(Gap(GAP_VOLUME, fmt_num(last.to_integral_value(rounding="ROUND_FLOOR") + 1), open_end=True,
                       note=f"{_chapters_text(_first_after(end), target)} out past volume {fmt_num(last)}; not "
                            "licensed in English"))
    if not out and count is not None:
        return out, f"The folder holds every volume of the original edition ({fmt_num(count)})"
    if not out:
        return out, f"Latest chapter {fmt_num(target)}"         # the volumes held collect every chapter out
    top_end = last_volume_chapter(k.volumes, top) if count is None else None
    if count is not None:
        past = f"the original edition has {fmt_num(count)} volumes"
    elif top_end is not None and target > top_end:
        lo = _first_after(top_end)
        past = f"{_chapters_text(lo, target)} {'are' if lo < target else 'is'} out past volume {fmt_num(top)}"
    else:
        past = "the volume list has volumes out, by their chapters, that the folder lacks"
    why = (f"Not licensed in English and the folder holds volumes only: {past} - counted as missing volumes, not "
           "chapters; turn on Unbundled chapters to get them as chapters")
    return out, why


def _chapters_text(lo: Decimal, hi: Decimal) -> str:
    """``chapters 54-94``, or ``chapter 94`` when *lo* is not below *hi*."""
    return f"chapters {fmt_num(lo)}-{fmt_num(hi)}" if lo < hi else f"chapter {fmt_num(hi)}"


def _first_after(end: Decimal) -> Decimal:
    """The first whole chapter after *end* (``53`` -> 54, ``53.5`` -> 54)."""
    return end.to_integral_value(rounding="ROUND_FLOOR") + 1


def _scan_volume_total(k: SeriesKnowledge) -> Optional[Decimal]:
    """How many volumes a series with no English edition has in scanlation: MangaUpdates' latest scanlated volume, or -
    when its translation is complete and the series finished - its volume count in the original language."""
    cands = [k.scan_latest_volume]
    if k.translation_complete is True and k.finished_in_origin:
        cands.append(k.origin_volumes)
    cands = [d for d in cands if d is not None and d > 0]
    return max(cands) if cands else None


def _span(numbers: Sequence[Decimal]) -> str:
    return fmt_num(numbers[0]) if len(numbers) == 1 else f"{fmt_num(numbers[0])}-{fmt_num(numbers[-1])}"


def _jp_volumes_out(k: SeriesKnowledge, extra_target: Optional[Decimal] = None) -> List[VolumeInfo]:
    """The volume list's (original edition) volumes whose last chapter is out in English - the English chapter count
    or the latest scanlated chapter, whichever is higher. A volume without a chapter range, a volume 0 (a special)
    and every volume of a series with an English omnibus edition (numbered its own way) are not counted."""
    target = _chapter_target(k, extra_target)
    if target is None or any(p.omnibus for p in k.english_publishers):
        return []                               # an omnibus edition numbers its volumes its own way
    out = []
    for v in k.volumes or ():
        number = to_decimal(v.volume)
        last = to_decimal(v.chapters_to) if v.has_chapters else None
        if number is not None and number >= 1 and last is not None and last <= target:     # volume 0: a special
            out.append(v)
    return out


def last_volume_chapter(volumes: Iterable[VolumeInfo], top: Optional[Decimal]) -> Optional[Decimal]:
    """The chapter the volumes up to *top* end with by the volume list: the largest ``chapters_to`` of a listed volume
    numbered *top* or lower (None: the list maps none of them). "Check for new chapters" counts from it
    (:func:`mangalist.downloads.chapters.newest_held_chapter`), and so do unbundled chapters."""
    if top is None:
        return None
    ends = [to_decimal(v.chapters_to) for v in volumes or () if v.has_chapters
            and (to_decimal(v.volume) or Decimal(-1)) <= top]
    ends = [e for e in ends if e is not None]
    return max(ends) if ends else None


@dataclass(frozen=True)
class _UnbundledStart:
    """Where a folder's unbundled chapters start: after :attr:`after` (or from it, :attr:`inclusive` - the owner's
    "first chapter to get"), the reason line, the question behind it (None: the volume list says), and the chapters
    the owner's answer puts in the unmapped volumes (:attr:`assumed`). :attr:`after` None: not compared."""

    after: Optional[Decimal]
    why: str
    inclusive: bool = False
    ask: Optional[UnbundledAsk] = None
    assumed: Tuple[Decimal, ...] = ()

    def wants(self, c: Decimal) -> bool:
        return self.after is not None and (c >= self.after if self.inclusive else c > self.after)


def _newest_mapped(volumes: Iterable[VolumeInfo], top: Decimal) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """``(the newest volume numbered *top* or lower that the list maps, the chapter the volumes up to it end with)``."""
    numbers = [to_decimal(v.volume) for v in volumes or () if v.has_chapters]
    numbers = [n for n in numbers if n is not None and n <= top]
    if not numbers:
        return None, None
    newest = max(numbers)
    return newest, last_volume_chapter(volumes, newest)


def unbundled_suggestion(volumes: Iterable[VolumeInfo], top: Decimal, target: Optional[Decimal] = None,
                         origin_volumes: Optional[Decimal] = None) -> int:
    """An estimate of the first chapter after volume *top* when the list does not map it: the newest mapped volume
    below's end, plus its chapters per volume for each volume after it; when the list maps none, the latest chapter
    (*target*) over the original edition's volume count (*origin_volumes*) per volume; else 1. At most the chapter after
    *target* (nothing more to get yet). Only
    a suggestion for the prompt - the owner's answer is what counts."""
    newest, end = _newest_mapped(volumes, top)
    if newest is not None and end is not None and newest >= 1:
        guess = int(end + (top - newest) * (end / newest)) + 1
    elif target is not None and origin_volumes and origin_volumes >= top:
        guess = int(top * (target / origin_volumes)) + 1
    else:
        return 1
    return max(1, min(guess, int(target) + 1)) if target is not None else max(1, guess)


def _unbundled_start(k: SeriesKnowledge, held: _Held, gaps: Sequence[Gap], choice: Any,
                     original_only: Collection[Decimal] = ()) -> _UnbundledStart:
    """Where a folder's unbundled chapters start (see :class:`_UnbundledStart`).

    The last volume is the newest one the folder holds, or lacks while it is out in English (a missing volume or an
    upgrade): the chapters a held volume collects are held, and those of a released volume the folder lacks come with
    that volume - so neither is wanted as chapters. A missing volume of the original edition that is not out in English
    (*original_only*) does not count: its chapters are wanted as chapters (owner, 2026-10-15). Its end comes from
    MangaPixer's volume list. When the list does not map the newest held volume, the owner's "first chapter to get"
    (*choice*'s ``first_chapter``, given while ``for_volume`` was the newest held volume) says where to start - a guess
    could ask for chapters that volume holds; without an answer in force the caller compares the folder as without the
    setting."""
    top = max(held.volumes)
    mapped = {to_decimal(v.volume): v for v in k.volumes or () if v.has_chapters}
    ask: Optional[UnbundledAsk] = None
    assumed: Tuple[Decimal, ...] = ()
    if top in mapped:
        after, last, inclusive = last_volume_chapter(k.volumes, top), top, False
    else:
        first = to_decimal(getattr(choice, "first_chapter", None))
        given_for = to_decimal(getattr(choice, "for_volume", None))
        newest, end = _newest_mapped(k.volumes, top)
        stale = first is not None and given_for is not None and top > given_for
        ask = UnbundledAsk(volume=fmt_num(top), mapped_volume=fmt_num(newest) if newest is not None else None,
                           mapped_end=fmt_num(end) if end is not None else None,
                           suggestion=unbundled_suggestion(k.volumes, top, _chapter_target(k), k.origin_volumes),
                           current=fmt_num(first) if first is not None and not stale else None,
                           given_for=fmt_num(given_for) if given_for is not None else None)
        if first is None or first <= 0:
            return _UnbundledStart(None, f"Unbundled chapters: the volume list does not say which chapter volume "
                                         f"{fmt_num(top)} ends with - set the first chapter to get", ask=ask)
        if stale:
            return _UnbundledStart(None, f"Unbundled chapters: volume {fmt_num(top)} arrived after the first chapter "
                                         f"to get ({fmt_num(first)}) was set for volume {fmt_num(given_for)} - set it "
                                         "again", ask=ask)
        after, last, inclusive = first, None, True
        below = end if end is not None else Decimal(0)
        assumed = tuple(sorted(c for c in _whole_numbers(max(below, Decimal(0)), first) if below < c < first))
    for g in gaps:
        number = to_decimal(g.start)
        if g.kind == GAP_VOLUME and number in original_only:
            continue                            # not out in English: its chapters are wanted as chapters
        v = mapped.get(number) if g.kind in (GAP_VOLUME, GAP_UPGRADE) else None
        end = to_decimal(v.chapters_to) if v is not None else None
        if end is not None and (after is None or end > after or (inclusive and end >= after)):
            after, last, inclusive = end, number, False
    if after is None:
        return _UnbundledStart(None, f"Unbundled chapters: the volume list does not say which chapter volume "
                                     f"{fmt_num(top)} ends with", ask=ask)
    if inclusive:
        why = (f"Unbundled chapters from chapter {fmt_num(after)} (your first chapter to get; the volume list does not "
               f"say which chapter volume {fmt_num(top)} ends with)")
    else:
        why = f"Unbundled chapters after chapter {fmt_num(after)} (the end of volume {fmt_num(last)})"
    return _UnbundledStart(after, why, inclusive, ask, assumed)


def _empty_folder_chapters(target: Decimal) -> List[Decimal]:
    """Every whole chapter 1..*target*, plus *target* itself when it is a decimal (``12.5``)."""
    missing = sorted(_whole_numbers(Decimal(1), target))
    if target != target.to_integral_value():
        missing.append(target)
    return missing


def _chapter_target(k: SeriesKnowledge, extra: Optional[Decimal] = None) -> Optional[Decimal]:
    """The last chapter that exists anywhere: the largest of MangaUpdates' latest chapter, the English
    chapter count and the finished total (and *extra*: MangaDex's latest, where :func:`compute_state` lets it count)."""
    cands = [d for d in (k.latest_chapter_decimal, k.publisher_chapters,
                         k.total_chapters if k.finished_in_origin else None, extra) if d is not None and d > 0]
    return max(cands) if cands else None


def _edition_state(k: SeriesKnowledge, held: _Held, *, folder_empty: bool, behind_override: Optional[str],
                   finish) -> SeriesState:
    """A folder of a declared edition (MangaPixer 1.39.0 ``completion.volumeTotalOverride``, e.g. a master edition of 6
    volumes): its volumes 1..N counted by the volume FILES held - the regular edition's list says nothing about which
    chapters an edition volume holds, so loose chapters are not compared and no volume is an upgrade (MangaPixer's rule:
    "the edition's volume count replaces the regular edition's volumes in every volume answer"). Complete once every
    volume is held and the series has ended in its origin. Owner, 2026-10-12, on a master edition folder whose missing
    volumes came from the regular edition: "Is this a MangaPixer job?" - MangaPixer declares, MangaList counts."""
    label, total = k.edition_label, int(k.edition_volumes)
    declared = f"{label}: {total} volume{'s' if total != 1 else ''} (declared in MangaPixer)"
    numbers = [Decimal(n) for n in range(1, total + 1)]
    if folder_empty:
        state = State.WANTED_OFFICIAL if k.licensed else State.WANTED
        return finish(state, [Gap(GAP_VOLUME, fmt_num(n), note=label) for n in numbers], [f"Empty folder; {declared}"])
    if not held.has_volumes:
        return finish(State.CANT_TELL, reasons=[f"{declared}; the folder holds no volume file to count"])
    gaps = [Gap(GAP_VOLUME, fmt_num(n), note=label) for n in numbers if n not in held.volumes]
    reasons = [declared]
    if held.has_chapters:
        reasons.append("Chapter files are not compared with an edition's volumes")
    complete = not gaps and k.finished_in_origin
    if behind_override == "done":
        reasons.append("Marked up to date by the owner")
        return finish(State.COMPLETE if complete else State.UP_TO_DATE, gaps, reasons)
    if gaps:
        return finish(State.MISSING_VOLUMES, gaps, reasons)
    if complete:
        return finish(State.COMPLETE, (), reasons + ["Finished, and held whole"])
    return finish(State.UP_TO_DATE, (), reasons)


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
    if (not k.licensed and held.has_volumes and not held.has_chapters and k.origin_volumes is not None
            and all(Decimal(n) in held.volumes for n in range(1, int(k.origin_volumes) + 1))):
        return True                             # every volume of the finished original edition (owner, 2026-10-17)
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
                         restarts=getattr(entry, "restarting_units", ()),
                         behind_override=getattr(entry, "behind_override", None), today=today)
