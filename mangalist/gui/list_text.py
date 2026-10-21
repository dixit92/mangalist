"""Qt-free rules of the List tab: what the Download tab is handed (:class:`~mangalist.gui.shell.WantedSeries` per
series and group), and the details panel's texts (Holds, English, the match line).

One function decides each so the table, the details panel, the Download tab's list and the tests cannot drift apart.
Numbers stay exact decimal strings throughout; nothing goes through a float.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from ..alias_search import distinct_titles
from ..knowledge import (LINK_AUTO, LINK_COLLECTION_ABOUT, LINK_CONFIRMED, LINK_DONT_MATCH, LINK_NEEDS_REVIEW,
                         LINK_NOT_A_WORK, LINK_NOT_LOOKED_UP, LINK_UNMATCHED, SOURCE_MANGAPIXER, SeriesKnowledge,
                         fmt_num, to_decimal)
from ..states import SeriesState
from .shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, WantedSeries
from .volumes_target import Availability, numbers_text, search_titles

#: The reason shown for a group that cannot be searched yet (the Download tab greys them out).
REASON_CHAPTERS = "needs Suwayomi"
MAX_EXPANDED = 5000             # a chapter range wider than this is cut (a broken number, not a real gap)

_LINK_WORDS = {
    LINK_CONFIRMED: "confirmed",
    LINK_AUTO: "matched",
    LINK_NEEDS_REVIEW: "needs review",
    LINK_DONT_MATCH: "not a match",
    LINK_COLLECTION_ABOUT: "a collection about a series",
    LINK_UNMATCHED: "no match",
    LINK_NOT_A_WORK: "not one work",
    LINK_NOT_LOOKED_UP: "not looked up yet",
}


def expand_numbers(items: Iterable) -> Tuple[str, ...]:
    """Single numbers and ``(from, to)`` ranges -> every exact number, ascending, without repeats. A range takes its
    whole numbers from ``from`` up and its own end (``(17, 24.5)`` -> 17 ... 24, 24.5)."""
    out = set()
    for item in items or ():
        if isinstance(item, (tuple, list)) and len(item) == 2:
            lo, hi = to_decimal(item[0]), to_decimal(item[1])
            if lo is None or hi is None:
                continue
            lo, hi = min(lo, hi), max(lo, hi)
            out.add(lo)
            n = lo.to_integral_value() if lo == lo.to_integral_value() else lo.to_integral_value() + 1
            steps = 0
            while n <= hi and steps < MAX_EXPANDED:
                out.add(n)
                n += 1
                steps += 1
            out.add(hi)
            continue
        d = to_decimal(item)
        if d is not None:
            out.add(d)
    return tuple(fmt_num(d) for d in sorted(out))


def chapter_numbers(ranges: Sequence[Tuple[str, Optional[str]]]) -> Tuple[str, ...]:
    """A state's missing chapters (``(start, end-or-None)``) as exact numbers."""
    return expand_numbers(s if e is None else (s, e) for s, e in ranges)


def holds_text(volumes: Iterable, chapters: Iterable = ()) -> str:
    """``Volumes 1-13``, ``Chapters 1-40``, ``Volumes 1-3 · Chapters 25-40``; ``Nothing`` for an empty folder."""
    parts = []
    vols = numbers_text(expand_numbers(volumes))
    if vols:
        parts.append(f"Volumes {vols}")
    chs = numbers_text(expand_numbers(chapters))
    if chs:
        parts.append(f"Chapters {chs}")
    return " · ".join(parts) or "Nothing"


def english_text(knowledge: Optional[SeriesKnowledge]) -> str:
    """The English edition in a few words: the publisher (``+N`` more), ``Not licensed``, or empty when unknown."""
    if knowledge is None:
        return ""
    names: List[str] = []
    for pub in knowledge.english_publishers:
        if pub.name and pub.name not in names:
            names.append(pub.name)
    if names:
        return names[0] + (f" (+{len(names) - 1})" if len(names) > 1 else "")
    if knowledge.licensed_en is True:
        return "Licensed"
    if knowledge.licensed_en is False:
        return "Not licensed"
    return ""


def match_text(knowledge: Optional[SeriesKnowledge]) -> str:
    """Where the series' data comes from and how sure the link is: ``MangaPixer: confirmed``,
    ``MangaUpdates: needs review``; ``MangaPixer: confirmed (MangaDex record)`` for a series MangaUpdates does not list
    (MangaPixer 1.42.0)."""
    if knowledge is None:
        return ""
    who = "MangaPixer" if knowledge.source == SOURCE_MANGAPIXER else "MangaUpdates"
    text = f"{who}: {_LINK_WORDS.get(knowledge.link_state, knowledge.link_state)}"
    return text + " (MangaDex record)" if knowledge.mangadex_record else text


def same_title(a: Optional[str], b: Optional[str]) -> bool:
    """Whether two titles say the same thing, ignoring case, spaces and punctuation (``Ashen Prince`` and
    ``ashen prince!`` do; ``Brine and Bone`` and ``Brine & Bone`` do not): the matched series' title is shown under the
    folder's only when it says something new (UI taste review 2026-10-16: half of each row repeated the title)."""
    def key(text: Optional[str]) -> str:
        raw = (text or "").strip()
        letters = "".join(ch for ch in raw.casefold() if ch.isalnum())
        return letters or raw.casefold()
    return key(a) == key(b)


def group_gaps_text(state: SeriesState, group: str) -> str:
    """One group's part of the gaps: ``Vol. 21-23``, ``Ch. 41-44``, ``Upgrade vol. 13``."""
    if group == GROUP_VOLUMES:
        nums = state.missing_volumes_text                   # ``16+``: an open-ended gap (an unlicensed volume folder)
        return f"Vol. {nums}" if nums else ""
    if group == GROUP_UPGRADES:
        nums = numbers_text(state.upgrade_volumes)
        return f"Upgrade vol. {nums}" if nums else ""
    chs = [s if e is None else f"{s}-{e}" for s, e in state.missing_chapters]
    return f"Ch. {', '.join(chs)}" if chs else ""


def wanted_series(*, folder: str, title: str, english_title: Optional[str], state: Optional[SeriesState],
                  knowledge: Optional[SeriesKnowledge], held: Sequence[str], series_id: Optional[int],
                  volumes: Optional[Availability]) -> List[WantedSeries]:
    """What one series adds to the Download tab's "To get" list: one entry per kind of gap it has - missing
    volumes and volumes that would upgrade chapters (both searchable when *volumes*, the nyaa rule's answer, says so)
    and missing chapters (Suwayomi, later). Nothing for a series without gaps."""
    if state is None or not state.gaps:
        return []
    if knowledge is not None:
        titles = search_titles(knowledge, english_title, title)
    else:
        titles = tuple(dict.fromkeys(t.strip() for t in (english_title, title) if t and t.strip()))
    common = dict(series_id=series_id, folder=folder, title=title, held=tuple(held), titles=titles)
    if knowledge is not None:       # the filter box also finds a series by any other title of its record, or its creator
        common.update(alt_titles=distinct_titles((knowledge.title, knowledge.english_title, *knowledge.alt_titles)),
                      mu_id=knowledge.mu_id)
    out: List[WantedSeries] = []
    if state.missing_volumes:
        findable = bool(volumes is not None and volumes.enabled)
        reason = "" if findable else (volumes.reason if volumes is not None else "downloads are off")
        out.append(WantedSeries(group=GROUP_VOLUMES, gaps=group_gaps_text(state, GROUP_VOLUMES),
                                missing=tuple(state.missing_volumes), findable=findable, reason=reason, **common))
    if state.missing_chapters:
        out.append(WantedSeries(group=GROUP_CHAPTERS, gaps=group_gaps_text(state, GROUP_CHAPTERS),
                                missing=chapter_numbers(state.missing_chapters), findable=False,
                                reason=REASON_CHAPTERS, **common))
    if state.upgrade_volumes:                   # the same nyaa rule as missing volumes (the volumes cycle, lane A)
        findable = bool(volumes is not None and volumes.enabled)
        reason = "" if findable else (volumes.reason if volumes is not None else "downloads are off")
        out.append(WantedSeries(group=GROUP_UPGRADES, gaps=group_gaps_text(state, GROUP_UPGRADES),
                                missing=tuple(state.upgrade_volumes), findable=findable, reason=reason, **common))
    return out


#: The row menu's Unbundled Chapters entry (a tick; also on a selection) and its help.
UNBUNDLED_MENU = "Unbundled chapters (volumes + the chapters after the last volume)"
UNBUNDLED_HELP = ("Also get the chapters after the last volume - Missing chapters from the series' Suwayomi source, "
                  "Automatic downloads included - before the next volume is out; when it is, it replaces them as an "
                  "upgrade. Where MangaPixer's volume list does not say where the last volume ends, it asks once for "
                  "the first chapter to get.")
#: The row menu's entry to give or change the first chapter to get (a series whose newest volume the list does not map).
UNBUNDLED_FIRST_MENU = "First chapter to get…"
UNBUNDLED_FIRST_HELP = ("Unbundled chapters: the first chapter after the volumes you hold - MangaPixer's volume list does "
                        "not say which chapter the newest one ends with")
#: The List's More filters entry (owner, 2026-10-15, question 17).
UNBUNDLED_FILTER = "Unbundled chapters"


def unbundled_detail(on: bool) -> str:
    """The details panel's flag for a series with Unbundled Chapters on."""
    return "Unbundled chapters: the chapters after the last volume are wanted too" if on else ""


def unbundled_status(on: bool, n: int) -> str:
    """The status line after the row menu switched the setting for *n* series (0: none of them is scanned yet)."""
    if n == 0:
        return "Unbundled chapters: these folders are not scanned yet."
    series = f"{n} series" if n != 1 else "1 series"
    return (f"Unbundled chapters on for {series}: the chapters after the last volume are wanted too" if on
            else f"Unbundled chapters off for {series}: volumes only")


def unbundled_first_status(first, volume: str, n_waiting: int = 0) -> str:
    """The status line after the "first chapter to get" prompt (*first* None: asked later), or - *n_waiting* - after
    the setting was switched on for a selection whose series *n_waiting* still need it."""
    if n_waiting:
        series = f"{n_waiting} series need" if n_waiting != 1 else "1 series needs"
        return (f" - {series} the first chapter to get (the volume list does not say where the newest volume ends): "
                f"row menu, {UNBUNDLED_FIRST_MENU}")
    if first is None:
        return (f"Unbundled chapters: no first chapter to get yet - the chapters after volume {volume} are not "
                f"compared until you give one (row menu, {UNBUNDLED_FIRST_MENU})")
    return f"Unbundled chapters from chapter {first} (the first chapter to get after volume {volume})"


def wanted_label(groups: Iterable[str]) -> str:
    """The details panel's call to action for a series in these groups."""
    groups = set(groups)
    if GROUP_VOLUMES in groups:
        return "Get the missing volumes"
    if GROUP_CHAPTERS in groups:
        return "Get the missing chapters"
    if GROUP_UPGRADES in groups:
        return "Get the volume upgrades"
    return ""
