"""Chapter downloads through Suwayomi (the Suwayomi MVP, owner decisions 2026-10-09 / 10): find the series in Suwayomi,
list its missing chapters with the scanlation groups that have them, pick the default group, and send the picks.
Filing the finished chapters is :mod:`.chapter_arrivals`. No Qt here.

**Finding the series** (:func:`find_manga`; owner: "MangaDex FIRST"):

1. A match stored for the series (the owner confirmed it, or MangaDex's id found it before) whose source is still allowed.
2. MangaDex by id: the MangaDex id MangaPixer links (``companions.mangadex`` of the folder's own Confirmed / Auto item;
   :func:`mangadex_id_for`) searched as ``id:<uuid>`` on each allowed MangaDex source - a manga whose URL ends with that id
   is the series, no question asked.
3. Otherwise - or when MangaDex has none of the missing chapters - a title search on the other allowed sources, in the
   owner's order (Settings > Download sources > Suwayomi sources): the results are CANDIDATES the owner must confirm
   before anything is listed or sent (:data:`HOW_TITLE`); the confirmed one is stored for the series.

Sources are stored by Suwayomi's numeric source id, never by name. Without a stored choice of sources MangaList uses the
MangaDex sources alone (English first).

**The chapters** (:func:`chapter_rows`): per missing chapter number, the copies the source has - one per scanlation group
(the latest upload of a group wins) - and the default pick: the series' group when it has the chapter, else the group with
the most of the missing chapters. **Extras** (:func:`extra_numbers`; owner, 2026-10-10: MangaFire had 71.5 and other
extras the panel never offered): the source's decimal chapters within the folder's chapter range that the folder does not
hold and that are not counted as missing - listed after their neighbours, unticked. **The series' group** (:func:`default_group`; owner: "the folder's usual group"): the
owner's stored choice for the series when the source offers it; else the group the folder's own chapter files use around
the first missing chapter (:func:`mangalist.duplicates.usual_group`'s rule: the nearest neighbours, else the folder's
clear favourite); else the group with the most of the missing chapters.

**Sending** (:func:`send_chapters`): the series is added to Suwayomi's library (best effort), one SENT ledger record per
chapter is written first (tool ``suwayomi``; a chapter already in hand is skipped), then the chapters are enqueued; when
the enqueue fails every record of the send becomes FAILED with Suwayomi's reason. Chapter downloads are outside the
download budget.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contracts import (
    ChapterClient,
    DownloadRecord,
    DownloadStatus,
    MangaChapters,
    Placement,
    QueuedChapter,
    SuwayomiChapter,
    SuwayomiManga,
    SuwayomiSource,
)

_log = logging.getLogger(__name__)

HOW_MANGADEX = "mangadex-id"        # found by the MangaDex id MangaPixer links: no confirmation needed
HOW_CONFIRMED = "confirmed"         # a title match the owner confirmed
HOW_TITLE = "title"                 # a title search result: a candidate until the owner confirms it

MAX_CANDIDATES_PER_SOURCE = 5
MAX_TITLE_QUERIES = 2               # the main title, then one alternative when the first finds nothing

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


# --- chapter names ----------------------------------------------------------------------------------------------

_NAME = re.compile(
    r"^\s*(?:(?:vol(?:ume)?|v)\.?\s*(?P<vol>\d+(?:\.\d+)?)\s*[,:]?\s*)?"
    r"(?:(?:ch(?:apter)?|ep(?:isode)?|c)\.?\s*(?P<ch>\d+(?:\.\d+)?)(?:\s*-\s*\d+(?:\.\d+)?)?)"
    r"\s*(?:[-:–—]\s*(?P<title>.*?))?\s*$", re.I)


def split_chapter_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    """(volume, title) a source's chapter name states: ``Vol.1 Ch.2 - Sailor Shock`` -> ``('1', 'Sailor Shock')``;
    ``Vol.8 Ch.953.5`` -> ``('8', None)``; ``Chapter 102`` -> ``(None, None)``; a name that is not a chapter label at all
    (``The Beginning``) is all title. The volume is an exact decimal string."""
    text = (name or "").strip()
    if not text:
        return None, None
    m = _NAME.match(text)
    if m is None:
        return None, text
    vol = m.group("vol")
    title = (m.group("title") or "").strip() or None
    return (_plain(vol) if vol else None), title


def _plain(number: str) -> Optional[str]:
    try:
        d = Decimal(number)
    except (InvalidOperation, ValueError):
        return None
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def to_decimal(number: Optional[str]) -> Optional[Decimal]:
    if number is None:
        return None
    try:
        d = Decimal(str(number))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d >= 0 else None


def group_key(group: Optional[str]) -> str:
    return (group or "").strip().casefold()


# --- where chapters go ------------------------------------------------------------------------------------------

def chapter_placement(series_dir: str, units: Sequence) -> Placement:
    """Where the series' new chapter archives go: the one folder its chapter files live in (the series folder itself or
    a subfolder such as ``Chapters``); chapters spread over unit subfolders (``Season 01`` ... ``Season 04``, ``Part 2``)
    -> the one holding the highest chapter, the latest season (owner, 2026-10-10), the others offered too; spread over
    other folders -> ambiguous (the options, most chapters first, the series folder too); no chapter files -> the series
    folder."""
    from .placement import _folder, _is_archive, _options

    series_dir = os.path.normpath(os.path.abspath(series_dir))
    if not os.path.isdir(series_dir):
        return Placement(series_dir, None, "the series folder is not there (moved, or its root is not mounted); "
                                           "rescan first")
    folders: Counter = Counter()
    highest: Dict[str, Decimal] = {}
    for u in units:
        if getattr(u, "kind", None) in ("chapter", "both"):
            rel = (u.rel_path or "").replace("\\", "/").strip("/")
            if rel and _is_archive(rel):
                folder = os.path.dirname(rel)
                folders[folder] += 1
                number = to_decimal(getattr(u, "ch_to", None) or getattr(u, "ch_from", None))
                if number is not None and number > highest.get(folder, Decimal(-1)):
                    highest[folder] = number
    if len(folders) == 1:
        (folder,) = folders
        target = _folder(series_dir, folder)
        if target is None or not os.path.isdir(target):
            return Placement(series_dir, None, f"the chapters' folder {folder or '(series folder)'!r} is gone; rescan "
                                               "first")
        return Placement(series_dir, target, "chapters live in the series folder" if not folder
                         else f'chapters live in "{folder}"')
    subs = [f for f in folders if f]
    if len(subs) > 1 and all(_is_unit_subfolder(f.split("/")[0]) for f in subs) and highest:
        latest = max(subs, key=lambda f: (highest.get(f, Decimal(-1)), f))
        target = _folder(series_dir, latest)
        if target is not None and os.path.isdir(target):
            ordered = sorted(folders, key=lambda f: (-highest.get(f, Decimal(-1)), f))
            return Placement(series_dir, target, f'chapters live in {len(subs)} season / part folders; new ones go to '
                                                 f'"{latest}", the one with the latest chapters',
                             _options(series_dir, ordered + ([""] if "" not in ordered else [])))
    if len(folders) > 1:
        ordered = [f for f, _ in sorted(folders.items(), key=lambda kv: (-kv[1], kv[0]))]
        return Placement(series_dir, None, f"chapters are spread over {len(folders)} folders; choose one",
                         _options(series_dir, ordered + ([""] if "" not in ordered else [])))
    return Placement(series_dir, series_dir, "no chapters held yet; the series folder itself")


def _is_unit_subfolder(name: str) -> bool:
    from ..scanner import _is_unit_subfolder as unit

    return unit(name)


def chapter_placement_for(db, series_id: int) -> Placement:
    from .placement import locate_series, series_units

    series, _root, series_dir = locate_series(db, series_id)
    return chapter_placement(series_dir, series_units(db, series))


def held_chapter_groups(db, series_id: int) -> Dict[Decimal, List[str]]:
    """The series' chapter files by number: ``{102: ['Group A'], 103: ['Group A', 'Group B']}`` - the groups their
    names state (a file naming none adds nothing). Ranges count by their start."""
    from .placement import locate_series, series_units

    series, _root, _dir = locate_series(db, series_id)
    out: Dict[Decimal, List[str]] = {}
    for u in series_units(db, series):
        if getattr(u, "kind", None) not in ("chapter", "both"):
            continue
        number = to_decimal(u.ch_from)
        if number is None:
            continue
        groups = out.setdefault(number, [])
        if u.group_name:
            groups.append(u.group_name)
    return out


def held_chapter_span(db, series_id: int) -> Tuple[Optional[Decimal], bool]:
    """``(the newest chapter the series' files hold, whether it holds volume files)`` - None when no chapter file
    states a number. Ranges count by their end."""
    from .placement import locate_series, series_units

    series, _root, _dir = locate_series(db, series_id)
    newest: Optional[Decimal] = None
    volumes = False
    for u in series_units(db, series):
        kind = getattr(u, "kind", None)
        if kind == "volume":
            volumes = True
            continue
        if kind not in ("chapter", "both"):
            continue
        for value in (u.ch_from, u.ch_to):
            number = to_decimal(value)
            if number is not None and (newest is None or number > newest):
                newest = number
    return newest, volumes


def newest_held_chapter(db, series_id: int) -> Tuple[Optional[Decimal], str]:
    """The newest chapter the series holds - in a chapter file, or at the end of its newest volume by MangaPixer's
    volume list (owner, 2026-10-11: "Can I check for new chapters on a volumes-based series") - and, when it cannot be
    told, why ('' otherwise)."""
    from .placement import locate_series, series_units

    newest, _volumes = held_chapter_span(db, series_id)
    series, _root, _dir = locate_series(db, series_id)
    tops = [to_decimal(u.vol_to or u.vol_from) for u in series_units(db, series)
            if getattr(u, "kind", None) in ("volume", "both")]
    tops = [t for t in tops if t is not None]
    if not tops:
        return newest, "" if newest is not None else "no file of this series states a chapter number"
    top = max(tops)
    from ..states import last_volume_chapter
    from ..upgrades import _knowledge_volumes

    listed, why = _knowledge_volumes(db, series)
    end = last_volume_chapter(listed or (), top)       # the states' unbundled chapters count from the same chapter
    if end is not None and (newest is None or end > newest):
        newest = end
    if newest is None:
        return None, (f"the volume list does not say which chapter volume {top} ends with"
                      if not why else f"{why}, so the chapter volume {top} ends with is not known")
    return newest, ""


def newer_numbers(chapters: Sequence[SuwayomiChapter], newest: Optional[Decimal]) -> List[str]:
    """The chapter numbers the source lists after *newest* (all of them when None), oldest first, each once in the
    source's own spelling; a chapter without a number is left out."""
    seen: Dict[Decimal, str] = {}
    for c in chapters:
        number = to_decimal(c.number)
        if number is None or number < 0 or (newest is not None and number <= newest):
            continue
        seen.setdefault(number, c.number)
    return [seen[n] for n in sorted(seen)]


# --- the series' group ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class GroupChoice:
    group: Optional[str]                # None: the source names no groups at all
    reason: str                         # e.g. "your choice for this series", "the folder's usual group"


def default_group(available: Mapping[str, int], held: Mapping[Decimal, Sequence[str]], missing: Sequence[Decimal],
                  stored: Optional[str] = None) -> GroupChoice:
    """The group the missing chapters are taken from (see the module docstring). *available*: group -> how many of the
    missing chapters it has (a group named '' = no group)."""
    from ..duplicates import usual_group

    named = {g: n for g, n in available.items() if g}
    by_key = {group_key(g): g for g in named}
    if stored and group_key(stored) in by_key:
        return GroupChoice(by_key[group_key(stored)], "your choice for this series")
    if not named:
        return GroupChoice(None, "the source names no scanlation groups")
    if len(named) == 1:
        (only,) = named
        return GroupChoice(only, "the only group with these chapters")
    if missing and held:
        first = min(missing)
        # usual_group tells copies apart: give it every available group and a nameless one, so "some have it" holds.
        pick = usual_group({n: list(gs) for n, gs in held.items()}, first, [*named, None])
        if pick and group_key(pick) in by_key:
            return GroupChoice(by_key[group_key(pick)], "the folder's usual group")
    best = sorted(named.items(), key=lambda kv: (-kv[1], group_key(kv[0])))[0][0]
    return GroupChoice(best, "the group with the most of these chapters")


# --- the rows the chapters panel shows --------------------------------------------------------------------------

@dataclass(frozen=True)
class ChapterRow:
    number: str                                     # exact
    options: Tuple[SuwayomiChapter, ...] = ()       # one per group, the default first
    default: Optional[SuwayomiChapter] = None       # None: the source does not have this chapter
    in_hand: Optional[DownloadRecord] = None        # a chapter download already has it (not sent again)
    extra: bool = False                             # an extra (``71.5``) the source has: offered, not counted missing
    part_of: Optional[str] = None                   # a part (``62.1``) of the missing chapter ``62`` the source splits
    note: str = ""                                  # why this form of the chapter (:mod:`.chapter_forms`): a stand-in,
                                                    # a late part; on an ``extra`` row, why the other form is not taken

    @property
    def available(self) -> bool:
        return bool(self.options)


def _per_group(chapters: Iterable[SuwayomiChapter]) -> Dict[Decimal, Dict[str, SuwayomiChapter]]:
    """number -> group key -> the chapter (the latest upload of a group wins, then the higher source order)."""
    out: Dict[Decimal, Dict[str, SuwayomiChapter]] = {}
    for ch in chapters:
        number = to_decimal(ch.number)
        if number is None:
            continue
        groups = out.setdefault(number, {})
        key = group_key(ch.scanlator)
        old = groups.get(key)
        if old is None or (_int(ch.upload_date), ch.source_order) > (_int(old.upload_date), old.source_order):
            groups[key] = ch
    return out


def _int(text: str) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


def group_counts(chapters: Iterable[SuwayomiChapter], missing: Sequence[Decimal]) -> Dict[str, int]:
    """group -> how many of the *missing* chapters it has ('' = chapters without a group)."""
    wanted = set(missing)
    counts: Counter = Counter()
    names: Dict[str, str] = {}
    for number, groups in _per_group(chapters).items():
        if number not in wanted:
            continue
        for key, ch in groups.items():
            counts[key] += 1
            names.setdefault(key, (ch.scanlator or "").strip())
    return {names[k]: n for k, n in counts.items()}


def source_splits(chapters: Iterable[SuwayomiChapter]):
    """MangaPixer's split-chapter rule (:func:`mangalist.split_chapters.splits_of`) on the source's list: the parts the
    source numbers ``N.1``, ``N.2`` ... are chapter N when they alone would count as chapter N once held (owner,
    2026-10-13: "We need to bring .1 .2 rules on parity with MangaPixer" - a source that splits chapter 62 into 62.1 and
    62.2 offered them as extras and called 62 "not on the source"; the folder counts 62.1 + 62.2 as chapter 62)."""
    from ..split_chapters import splits_of

    return splits_of(n for n in _per_group(chapters) if n != n.to_integral_value())


def extra_numbers(chapters: Iterable[SuwayomiChapter], missing: Sequence[str],
                  held: Iterable[Decimal]) -> List[Decimal]:
    """The source's extras worth offering: decimal chapters (``71.5``) between the folder's first and last chapter (held
    or missing) that the folder does not hold and that are not missing already. Chapters inside held volumes are below
    the first loose chapter, so they are never offered. Parts of a split chapter (:func:`source_splits`) are not
    extras: a missing chapter's parts are its own rows, a held chapter's parts are not wanted."""
    wanted = {d for d in (to_decimal(n) for n in missing) if d is not None}
    have = set(held)
    known = wanted | have
    if not known:
        return []
    lo, hi = min(known), max(known)
    parts = source_splits(chapters).parts
    return sorted({n for n in _per_group(chapters) if n != n.to_integral_value() and lo <= n <= hi
                   and n not in wanted and n not in have and n not in parts})


def chapter_rows(chapters: Sequence[SuwayomiChapter], missing: Sequence[str], group: Optional[str],
                 in_hand: Optional[Mapping[str, DownloadRecord]] = None,
                 extras: Sequence[Decimal] = (), held: Optional[Iterable[Decimal]] = None) -> List[ChapterRow]:
    """One row per missing chapter number and per *extra* (:func:`extra_numbers`), ascending (see the module
    docstring). *held*: the chapter numbers the series holds - with them, one form per chapter
    (:func:`.chapter_forms.choose`; owner, 2026-10-18): a chapter the source lists whole and in parts is taken in the
    series' form, the whole as a stand-in when the source's parts do not start at ``.1``; the other form, when it was
    asked about, is a row of its own - an unticked ``extra`` saying why it is not taken."""
    from .chapter_forms import choose, late_parts

    per = _per_group(chapters)
    missing_numbers = {d for d in (to_decimal(n) for n in missing) if d is not None}
    notes: Dict[Decimal, str] = {}
    others: Dict[Decimal, str] = {}
    chosen = set(missing_numbers)
    if held is not None:
        held_numbers = {n for n in held if n is not None}
        choice = choose(per, held_numbers, missing_numbers)
        chosen = set(choice.take)
        others = {n: why for n, why in choice.others.items() if n in missing_numbers}
        notes = dict(choice.notes)
        for n, why in late_parts(per, held_numbers, max(held_numbers, default=None)).items():
            if n in chosen and n in missing_numbers:
                notes.setdefault(n, why)
    # a missing chapter the source has only in parts: its parts stand in for it (source_splits)
    splits = source_splits(chapters)
    part_of: Dict[Decimal, Decimal] = {}
    for p in splits.parts:
        whole = Decimal(int(p))
        if whole in missing_numbers and not per.get(whole):
            part_of[p] = whole
    for p in chosen:                    # ... or in parts the series' form takes (the whole is the other form)
        whole = Decimal(int(p))
        if p != whole and whole in missing_numbers and whole not in chosen:
            part_of[p] = whole
    stand_ins = set(part_of.values())
    numbers = sorted((chosen - stand_ins) | set(part_of) | set(others) | set(extras))
    extra = set(extras) | set(others)
    counts = Counter(key for n in numbers for key in per.get(n, {}))
    want = group_key(group)
    in_hand = in_hand or {}
    rows = []
    for number in numbers:
        text = _plain(str(number)) or str(number)
        copies = per.get(number, {})
        ordered = sorted(copies.items(), key=lambda kv: (kv[0] != want, -counts[kv[0]], kv[0]))
        options = tuple(ch for _k, ch in ordered)
        whole = part_of.get(number)
        rows.append(ChapterRow(text, options, options[0] if options else None, in_hand.get(text), number in extra,
                               _plain(str(whole)) if whole is not None else None,
                               others.get(number) or notes.get(number, "")))
    return rows


# --- finding the series in Suwayomi -----------------------------------------------------------------------------

@dataclass(frozen=True)
class MangaMatch:
    source: SuwayomiSource
    manga: SuwayomiManga
    how: str                                        # HOW_*

    @property
    def confirmed(self) -> bool:
        return self.how in (HOW_MANGADEX, HOW_CONFIRMED)


@dataclass
class FindResult:
    match: Optional[MangaMatch] = None              # confirmed (or stored): its chapters can be listed and sent
    candidates: List[MangaMatch] = field(default_factory=list)      # title matches waiting for the owner
    notes: List[str] = field(default_factory=list)  # what was looked at, for the panel ("MangaDex: no id linked")


def allowed_sources(installed: Sequence[SuwayomiSource], chosen: Optional[Sequence[str]],
                    languages: Optional[Sequence[str]] = ("all", "en")) -> List[SuwayomiSource]:
    """The sources MangaList may use, in order: the owner's choice (ids still installed), else MangaDex alone in the
    owner's *languages* (nyaa's: English by default) - not MangaDex in every language (owner, 2026-10-10: every
    MangaDex language came ticked, "I have to manually untick all the others")."""
    by_id = {s.id: s for s in installed}
    if chosen is not None:
        return [by_id[i] for i in chosen if i in by_id]
    langs = set(languages or ())
    mangadex = [s for s in installed if s.is_mangadex and (not langs or s.lang in langs)]
    return sorted(mangadex, key=lambda s: (s.lang != "en", s.display_name.casefold()))


def mangadex_released_for(db, series_id: int) -> Optional[Tuple[str, int]]:
    """(language, how many chapters) MangaDex lists for the series in MangaPixer's ``companions.mangadexReleased``
    (1.42.0), else None. MangaPixer reads MangaDex's aggregate with its unavailable chapters (taken down, usually on a
    licence): chapters there that Suwayomi does not list were taken down; none at all - MangaDex never had any."""
    from ..services.mangapixer import open_cache
    from ..services.mangapixer.resolve import resolve
    from .placement import locate_series

    try:
        series, root, _dir = locate_series(db, series_id)
        res = resolve(open_cache(db), root.id, series.rel_path)
    except Exception:  # noqa: BLE001 - no MangaPixer data: the reason stays general
        _log.debug("Chapters: no MangaPixer item for series %s", series_id, exc_info=True)
        return None
    if res is None or res.inherited or res.link_state not in ("Confirmed", "Auto"):
        return None
    from ..knowledge import mangadex_released_of

    released = mangadex_released_of(res.item)
    return (released.language, released.chapters) if released is not None else None


@dataclass(frozen=True)
class Linked:
    """The MangaPixer export item linked to a series' own folder, and the MangaPixer library it is in."""

    library_id: str
    item: Mapping[str, Any]


def linked_item(db, series_id: int) -> Optional[Mapping[str, Any]]:
    """The MangaPixer export item linked to the series' own folder (Confirmed / Auto, not inherited), else None."""
    found = linked(db, series_id)
    return found.item if found is not None else None


def linked(db, series_id: int) -> Optional[Linked]:
    """:func:`linked_item` with its MangaPixer library, else None."""
    from ..services.mangapixer import open_cache
    from ..services.mangapixer.resolve import resolve
    from .placement import locate_series

    try:
        series, root, _dir = locate_series(db, series_id)
        res = resolve(open_cache(db), root.id, series.rel_path)
    except Exception:  # noqa: BLE001 - no MangaPixer data: the title search with confirmation takes over
        _log.debug("Chapters: no MangaPixer item for series %s", series_id, exc_info=True)
        return None
    if res is None or res.inherited or res.link_state not in ("Confirmed", "Auto"):
        return None
    return Linked(str(res.library_id), res.item) if isinstance(res.item, Mapping) else None


def mangadex_id_of(item: Optional[Mapping[str, Any]]) -> Optional[str]:
    """``companions.mangadex`` of a MangaPixer export item (a MangaDex manga uuid, lower case), else None."""
    companions = item.get("companions") if isinstance(item, Mapping) else None
    value = companions.get("mangadex") if isinstance(companions, Mapping) else None
    return value.strip().lower() if isinstance(value, str) and _UUID.fullmatch(value.strip()) else None


def mangadex_id_for(db, series_id: int) -> Optional[str]:
    """The MangaDex id MangaPixer links to the series' own folder (its Confirmed / Auto item), else None."""
    return mangadex_id_of(linked_item(db, series_id))


def mangadex_id_in_url(url: Optional[str]) -> Optional[str]:
    """The MangaDex manga uuid in a MangaDex source's manga url (Suwayomi's MangaDex extension: ``/manga/<uuid>``)."""
    found = _UUID.search(str(url or ""))
    return found.group(0).lower() if found else None


def find_manga(client: ChapterClient, sources: Sequence[SuwayomiSource], *, mangadex_id: Optional[str],
               titles: Sequence[str], stored: Optional[Mapping[str, object]] = None,
               title_search: bool = True) -> FindResult:
    """Find the series in Suwayomi (see the module docstring). *stored*: the series' stored choice (``source_id``,
    ``manga_id``, ``manga_title``, ``how``). *title_search*: also search the other sources by title when no confirmed
    match is found."""
    out = FindResult()
    by_id = {s.id: s for s in sources}
    stored = stored or {}
    sid, mid = str(stored.get("source_id") or ""), stored.get("manga_id")
    if sid in by_id and isinstance(mid, int) and stored.get("how") in (HOW_MANGADEX, HOW_CONFIRMED):
        out.match = MangaMatch(by_id[sid], SuwayomiManga(id=mid, title=str(stored.get("manga_title") or ""), url="",
                                                         source_id=sid), str(stored.get("how")))
        return out
    mangadex = [s for s in sources if s.is_mangadex]
    if mangadex_id and mangadex:
        for source in mangadex:
            found = [m for m in client.search(source.id, f"id:{mangadex_id}")
                     if m.url.rstrip("/").lower().endswith(mangadex_id.lower())]
            if found:
                out.match = MangaMatch(source, found[0], HOW_MANGADEX)
                return out
        out.notes.append("MangaDex has no manga with the MangaDex id MangaPixer links")
    elif mangadex:
        out.notes.append("MangaPixer links no MangaDex id to this series")
    if title_search:
        out.candidates = title_candidates(client, [s for s in sources if not (mangadex_id and s.is_mangadex)], titles)
    return out


def title_candidates(client: ChapterClient, sources: Sequence[SuwayomiSource], titles: Sequence[str]) -> List[MangaMatch]:
    """Title search results on *sources*, in source order: candidates the owner must confirm. A source that fails is
    skipped (logged); the others still answer."""
    out: List[MangaMatch] = []
    queries = [t for t in dict.fromkeys(t.strip() for t in titles if t and t.strip())][:MAX_TITLE_QUERIES]
    for source in sources:
        for query in queries:
            try:
                found = client.search(source.id, query)
            except Exception as exc:  # noqa: BLE001 - one source down must not hide the others
                _log.info("Chapters: %s search failed (%s)", source.display_name, type(exc).__name__)
                break
            if found:
                out.extend(MangaMatch(source, m, HOW_TITLE) for m in found[:MAX_CANDIDATES_PER_SOURCE])
                break
    return out


# --- one lookup for the chapters panel ---------------------------------------------------------------------------

@dataclass
class ChapterLookup:
    """What the chapters panel shows for one series."""

    series_id: int
    missing: Tuple[str, ...]
    match: Optional[MangaMatch] = None
    candidates: List[MangaMatch] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    manga_title: str = ""                           # as Suwayomi fetched it (its download folder's name)
    manga_url: str = ""                             # the manga's url on its source (MangaDex: ``/manga/<uuid>``)
    source_name: str = ""                           # the source's display name (its download folder's name)
    rows: List[ChapterRow] = field(default_factory=list)
    group: GroupChoice = GroupChoice(None, "")
    groups: Dict[str, int] = field(default_factory=dict)   # group -> how many missing chapters it has
    queued_in_suwayomi: Tuple[str, ...] = ()        # missing chapter numbers already in Suwayomi's download queue
    placement: Optional[Placement] = None
    placement_error: Optional[str] = None
    error: Optional[str] = None                     # the lookup failed (Suwayomi unreachable, ...)
    source_empty: bool = False                      # the matched manga lists no chapters at all on its source
    newer: bool = False                             # "Check for new chapters": *missing* is what the source lists
    newer_than: Optional[str] = None                # ... after this chapter, the newest held (None: none held)

    @property
    def available(self) -> List[ChapterRow]:
        return [r for r in self.rows if r.available and r.in_hand is None]

    @property
    def not_available(self) -> List[str]:
        return [r.number for r in self.rows if not r.available and r.in_hand is None]


def build_lookup(client: ChapterClient, *, series_id: int, missing: Sequence[str], match: MangaMatch,
                 held: Mapping[Decimal, Sequence[str]], stored_group: Optional[str],
                 in_hand: Optional[Mapping[str, DownloadRecord]] = None,
                 fetched: Optional[MangaChapters] = None, with_extras: bool = True,
                 queue: Optional[Sequence[QueuedChapter]] = None) -> ChapterLookup:
    """The chapters of a confirmed *match* against the series' *missing* chapters (*fetched*: the source's list when
    the caller has it already; *with_extras*: also offer the side chapters around them, e.g. 71.5; *queue*: Suwayomi's
    download queue when the caller read it already - the scheduled chapter check reads it once per run)."""
    if fetched is None:
        fetched = client.chapters(match.manga.id)
    numbers = [d for d in (to_decimal(n) for n in missing) if d is not None]
    counts = group_counts(fetched.chapters, numbers)
    choice = default_group(counts, held, numbers, stored_group)
    extras = extra_numbers(fetched.chapters, missing, held) if with_extras else ()
    rows = chapter_rows(fetched.chapters, missing, choice.group, in_hand, extras, held=held)
    queued: Tuple[str, ...] = ()
    try:
        ids = {c.id: c for c in fetched.chapters}
        wanted = set(numbers) | set(extras) | {to_decimal(r.number) for r in rows}
        queued = tuple(sorted({ids[q.chapter_id].number for q in (client.queue() if queue is None else queue)
                               if q.chapter_id in ids and to_decimal(ids[q.chapter_id].number) in wanted},
                              key=lambda n: to_decimal(n) or Decimal(0)))
    except Exception:  # noqa: BLE001 - the queue is a courtesy note
        _log.info("Chapters: Suwayomi's download queue could not be read", exc_info=True)
    return ChapterLookup(series_id=series_id, missing=tuple(missing), match=match,
                         manga_title=fetched.manga.title or match.manga.title,
                         manga_url=fetched.manga.url or match.manga.url,
                         source_name=fetched.source_name or match.source.display_name, rows=rows, group=choice,
                         groups=counts, queued_in_suwayomi=queued, source_empty=not fetched.chapters)


def newer_lookup(client: ChapterClient, db, *, series_id: int, match: MangaMatch,
                 held: Mapping[Decimal, Sequence[str]], stored_group: Optional[str],
                 in_hand: Optional[Mapping[str, DownloadRecord]] = None,
                 queue: Optional[Sequence[QueuedChapter]] = None) -> ChapterLookup:
    """"Check for new chapters" (owner, 2026-10-11) for a confirmed *match*: the source's chapters fetched now, and the
    ones numbered after the newest chapter held (:func:`newest_held_chapter`, :func:`newer_numbers`) as the lookup's
    *missing* - with the series' group by the usual default rule, extras never (every number after the newest held is
    new, a part ``N.2`` after a held ``N.1`` too: MAL-Sync parity, owner 2026-10-18). When the newest held chapter
    cannot be told, nothing is listed and the note says why. The manual check (the chapters panel) and the scheduled
    chapter check (:mod:`mangalist.downloads.chapter_check`) both use it.

    One form per chapter (:mod:`.chapter_forms`; owner, 2026-10-18, "Check: one form per chapter"): a chapter the source
    lists whole and in parts is taken in the form the series holds (the whole as a stand-in when the source's parts do
    not start at ``.1``); the other form is a row that says why it is not taken, and not in *missing*. A **late part**
    (``38.1`` listed after ``38.2`` was filed) is new too, although it is numbered below the newest held chapter."""
    fetched = client.chapters(match.manga.id)
    notes: List[str] = []
    try:
        newest, why = newest_held_chapter(db, series_id)
    except LookupError:
        newest, why = None, ""
    if newest is None and why and why != "no file of this series states a chapter number":
        missing: Tuple[str, ...] = ()
        notes.append(why)
    else:
        from .chapter_forms import late_parts

        spelled = {to_decimal(c.number): c.number for c in fetched.chapters if to_decimal(c.number) is not None}
        late = late_parts(spelled, held, newest)
        found = list(newer_numbers(fetched.chapters, newest)) + [spelled[n] for n in late]
        missing = tuple(sorted(dict.fromkeys(found), key=lambda n: to_decimal(n) or Decimal(0)))
    built = build_lookup(client, series_id=series_id, missing=missing, match=match, held=held,
                         stored_group=stored_group, in_hand=in_hand, fetched=fetched, with_extras=False, queue=queue)
    other_form = {to_decimal(r.number) for r in built.rows if r.extra}
    built.missing = tuple(n for n in missing if to_decimal(n) not in other_form)
    built.notes = notes
    built.newer, built.newer_than = True, (_plain(str(newest)) if newest is not None else None)
    return built


def newer_text(lookup: ChapterLookup) -> str:
    """One line on what a "Check for new chapters" lookup found, for the log: ``ch 38, 38.2 after ch 37.2 on MangaFire
    (EN) (Alpha Scans)``, ``nothing after ch 37.2 on ...``, or why nothing could be listed."""
    from ..gui.volumes_target import numbers_text

    where = f"on {lookup.source_name or (lookup.match.source.display_name if lookup.match else '?')}"
    after = f"after ch {lookup.newer_than}" if lookup.newer_than is not None else "(no chapter held)"
    group = f" ({lookup.group.group})" if lookup.group.group else ""
    if not lookup.missing:
        why = f" - {'; '.join(lookup.notes)}" if lookup.notes else ""
        return f"nothing {after} {where}{why}"
    text = f"ch {numbers_text(list(lookup.missing))} {after} {where}{group}"
    held = [r.number for r in lookup.rows if r.in_hand is not None]
    if held:
        text += f"; already sent: ch {numbers_text(held)}"
    said = [f"ch {r.number} {r.note}" for r in lookup.rows if r.note and not r.extra]
    others = [f"ch {r.number} ({r.note})" for r in lookup.rows if r.note and r.extra]
    if said:
        text += "; " + "; ".join(said)
    if others:
        text += "; not taken: " + ", ".join(others)
    if lookup.queued_in_suwayomi:
        text += f"; in Suwayomi's queue: ch {numbers_text(list(lookup.queued_in_suwayomi))}"
    return text


# --- sending ---------------------------------------------------------------------------------------------------

class ChapterSendRefused(ValueError):
    """The picks cannot be sent as given (nothing was enqueued)."""


@dataclass
class ChapterSendOutcome:
    records: List[DownloadRecord] = field(default_factory=list)     # SENT (or FAILED when the enqueue failed)
    skipped: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def text(self) -> str:
        from ..gui.volumes_target import numbers_text

        sent = [c for r in self.records if r.status == DownloadStatus.SENT for c in r.wanted_chapters]
        parts = []
        if sent:
            parts.append(f"Sent ch {numbers_text(sent)} to Suwayomi")
        if self.error:
            parts.append(f"Suwayomi refused the download: {self.error}")
        if self.skipped:
            parts.append(f"skipped {len(self.skipped)} ({'; '.join(self.skipped[:3])})")
        return "; ".join(parts) or "Nothing was sent."


def send_chapters(client: ChapterClient, ledger, series_id: int, match: MangaMatch, picks: Sequence[SuwayomiChapter],
                  target_dir: str, *, manga_title: str = "", source_name: str = "") -> ChapterSendOutcome:
    """Record and enqueue the picked chapters (see the module docstring). *ledger*: a Suwayomi ledger."""
    from ..store.downloads import StatusConflict

    if not match.confirmed:
        raise ChapterSendRefused("this match has not been confirmed yet")
    if not picks:
        raise ChapterSendRefused("pick at least one chapter")
    if not target_dir or not os.path.isabs(target_dir) or not os.path.isdir(target_dir):
        raise ChapterSendRefused(f"the target folder {target_dir!r} is not there")
    try:
        client.add_to_library(match.manga.id)       # Suwayomi keeps its library entry (and its chapter list) for it
    except Exception as exc:  # noqa: BLE001 - not needed for the download itself
        _log.info("Chapters: adding manga %s to Suwayomi's library failed (%s)", match.manga.id, type(exc).__name__)
    source = SuwayomiSource(id=match.source.id, name=match.source.name,
                            display_name=source_name or match.source.display_name, lang=match.source.lang,
                            extension=match.source.extension)
    manga = SuwayomiManga(id=match.manga.id, title=manga_title or match.manga.title, url=match.manga.url,
                          source_id=match.source.id)
    batch = uuid.uuid4().hex[:12]
    records, skipped = ledger.create_chapters(series_id, picks, source=source, manga=manga, target_dir=target_dir,
                                              batch=batch)
    out = ChapterSendOutcome(records=list(records), skipped=list(skipped))
    if not records:
        return out
    try:
        client.enqueue([int(r.info_hash) for r in records])
    except Exception as exc:  # noqa: BLE001 - every record of this send says why
        why = str(exc) or type(exc).__name__
        out.error = why
        failed = []
        for rec in records:
            try:
                failed.append(ledger.set_status(rec.id, DownloadStatus.FAILED, expect=(DownloadStatus.SENT,),
                                                error=f"Suwayomi did not take the download: {why}"))
            except StatusConflict:
                failed.append(rec)
        out.records = failed
        _log.warning("Chapters: series %s: Suwayomi did not take %d chapter(s): %s", series_id, len(records), why)
        return out
    _log.info("Chapters: series %s: sent %d chapter(s) from %s to Suwayomi (batch %s)%s", series_id, len(records),
              source.display_name, batch, f"; skipped {len(skipped)}" if skipped else "")
    return out
