"""Same series in two folders: one series (by its MangaUpdates match) held in two separate series folders, in one library
folder or across library folders - and the parts of a franchise kept in different libraries (owner, 2026-10-17, on a
series held in "Manga-Ongoing" and "Other-Manga-Ongoing": "duplicate detection doesn't work across library roots";
then, on the parts of a franchise: "it is possible the user wants to keep different parts of the franchise in different
libraries. I do it based on completed state. Flag + merge the others and notify the user if a franchise entry is split
across libraries"). No Qt: the Duplicates view (:mod:`mangalist.gui.duplicates_view`) shows what
:func:`find_same_series` finds; the merge is :mod:`mangalist.series_merge`.

**What counts as the same series.** Every present series of every library folder, grouped by the record its match
links: the MangaUpdates id (a MangaDex record that MangaPixer linked, for a series MangaUpdates does not list: its
MangaDex id). The match is the one the List uses - MangaPixer's for a folder it knows (its own item or the item of a
folder above it: a subseries inherits), else MangaList's own matcher's - and only a TRUSTWORTHY one counts: link state
Confirmed or Auto (either source). Not counted: Needs review (the match may be wrong), Unmatched, Not looked up, Not a
work, MangaPixer's "Don't match" and "Collection about", and any state MangaList does not know. Two folders that
merely carry the same title are never grouped.

**Merging needs Confirmed matches** (owner, 2026-10-17: "Confirmed only"): every duplicate is LISTED, but "Merge
into..." is offered only when both sides' matches are Confirmed (MangaPixer-linked Confirmed, or the own matcher's
Confirmed); a side on an Auto match shows why (:attr:`SamePair.merge_blocked`) and the merge itself refuses it
(:func:`mangalist.series_merge.check_merge`).

**How a group is read** (conservative: a merge is offered only where the two folders are plainly two copies of one
series). Each series lies in a *unit*: its library folder and the folder at the top of it (the series folder itself,
or the franchise folder it is a part of).

- Inside one unit, a series folder and a folder INSIDE it with the same name (``Name`` and ``Name/Name``; the names are
  compared without case, punctuation or spaces, and by the title before an English ``[...]`` too) are a
  **duplicate**: the inner one is a stray copy.
- Inside one unit, different names (``Franchise/Part 1`` and ``Franchise/Part 2``; ``Name`` and ``Name/Art Books``) are
  **parts of one franchise**: never listed (a part inherits its franchise's match, so they share it by design).
- Two units whose top folders are both the series folder itself (two top-level series folders - in one library folder
  or in two) are a **duplicate**: flagged, and the owner may merge one into the other.
- Two units where at least one side holds the series only as a part inside a franchise folder are a **franchise
  split**: parts of one franchise in different libraries (or different top folders). Never offered a merge - the
  owner may keep parts apart on purpose (by completed state) - only shown, so the split is known.

A group of three or more copies gives one duplicate per two of them. What each side shows (library, folder, files,
match, the origin's status) comes along, so the owner can tell which copy to keep.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .knowledge import (
    LINK_AUTO,
    LINK_CONFIRMED,
    PROVIDER_MANGADEX,
    SOURCE_MANGAPIXER,
    SeriesKnowledge,
    from_mangapixer_item,
    from_own_matcher,
)

_log = logging.getLogger(__name__)

#: The link states a match must have to be grouped (either source).
TRUSTED_STATES = frozenset({LINK_CONFIRMED, LINK_AUTO})

KIND_DUPLICATE = "duplicate"
KIND_NESTED = "nested"                  # a duplicate: a series folder and a same-named folder inside it


@dataclass(frozen=True)
class SeriesCopy:
    """One series folder of a group: where it lies and what the owner needs to tell the copies apart."""

    series_id: int
    root_id: int
    root_name: str
    rel_path: str                       # inside its library folder, '/'-separated
    folder: str                         # absolute, under the library folder as the scanner keys it (resolved)
    files: int                          # archives as the last scan recorded them
    title: str                          # the record's title (else the folder's name)
    link: str                           # "MangaPixer: Confirmed", "Own match: Auto" ...
    origin_status: Optional[str] = None  # "Complete", "Ongoing" ... as the record says
    confirmed: bool = False             # the match is Confirmed (MangaPixer's or the own matcher's), not Auto

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]

    @property
    def top(self) -> str:
        return self.rel_path.split("/", 1)[0]

    @property
    def is_part(self) -> bool:
        """A part inside a franchise folder (not a top-level series folder)."""
        return "/" in self.rel_path

    @property
    def where(self) -> str:
        return f"{self.root_name} / {self.rel_path}"


@dataclass(frozen=True)
class SamePair:
    """Two folders of one series: a duplicate the owner may merge (one folder's files into the other)."""

    key: str                            # the record: "mu:<id>" or "mangadex:<id>"
    title: str
    a: SeriesCopy
    b: SeriesCopy
    kind: str = KIND_DUPLICATE          # KIND_DUPLICATE | KIND_NESTED (b lies inside a, under the same name)

    @property
    def copies(self) -> Tuple[SeriesCopy, SeriesCopy]:
        return (self.a, self.b)

    @property
    def folders(self) -> Tuple[str, str]:
        return (self.a.folder, self.b.folder)

    @property
    def across_libraries(self) -> bool:
        return self.a.root_id != self.b.root_id

    @property
    def merge_blocked(self) -> Optional[str]:
        """Why "Merge into..." is not offered (None: it is): both sides must have a CONFIRMED match (owner,
        2026-10-17: "Confirmed only") - an Auto match may be wrong, and a merge would mix two series."""
        return merge_refusal(self.copies)

    def merge_choices(self) -> List[Tuple[SeriesCopy, SeriesCopy]]:
        """The merges offered: (folder merged away, folder kept). A nested copy only goes into the folder it lies in -
        the other way the kept folder would have to swallow its own parent."""
        if self.kind == KIND_NESTED:
            return [(self.b, self.a)]
        return [(self.b, self.a), (self.a, self.b)]


@dataclass(frozen=True)
class FranchiseSplit:
    """Parts of one franchise in different libraries (or top folders): shown, never merged."""

    key: str
    title: str
    copies: Tuple[SeriesCopy, ...]

    @property
    def folders(self) -> Tuple[str, ...]:
        return tuple(c.folder for c in self.copies)


@dataclass
class SameSeriesReport:
    pairs: List[SamePair] = field(default_factory=list)
    splits: List[FranchiseSplit] = field(default_factory=list)

    def scoped(self, root_ids: Optional[Iterable[int]]) -> "SameSeriesReport":
        """Only what touches the library folders *root_ids* (None: all): a pair or split with one side there - its
        other side may lie in any library (that is the point)."""
        if root_ids is None:
            return SameSeriesReport(list(self.pairs), list(self.splits))
        ids = set(root_ids)
        return SameSeriesReport([p for p in self.pairs if p.a.root_id in ids or p.b.root_id in ids],
                                [s for s in self.splits if any(c.root_id in ids for c in s.copies)])

    def others_by_folder(self) -> Dict[str, List[SeriesCopy]]:
        """folder -> the other copies of its series it forms a duplicate with (the List's Duplicates cell)."""
        out: Dict[str, List[SeriesCopy]] = {}
        for p in self.pairs:
            out.setdefault(p.a.folder, []).append(p.b)
            out.setdefault(p.b.folder, []).append(p.a)
        return out


def merge_refusal(copies: Sequence[SeriesCopy]) -> Optional[str]:
    """Why these copies of one series may not be merged yet: a side whose match is only Auto (either source)."""
    auto = [c for c in copies if not c.confirmed]
    if not auto:
        return None
    names = " and ".join(f"{c.root_name} / {c.name}" for c in auto)
    return f"confirm the match of {names} first (it is an Auto match; a merge needs both matches Confirmed)"


# --- names -------------------------------------------------------------------------------------------------------

_NOT_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _plain(text: str) -> str:
    return _NOT_WORD.sub("", text or "").casefold()


def same_name(a: str, b: str) -> bool:
    """Two folder names name the same thing: equal without case, punctuation and spaces - as written, or by the title
    before an English ``[...]`` (:func:`mangalist.classifier.parse_folder_name`)."""
    from .classifier import parse_folder_name

    if _plain(a) and _plain(a) == _plain(b):
        return True
    ta, tb = _plain(parse_folder_name(a)[0]), _plain(parse_folder_name(b)[0])
    return bool(ta) and ta == tb


# --- the match ---------------------------------------------------------------------------------------------------


def match_key(knowledge: Optional[SeriesKnowledge]) -> Optional[str]:
    """The record a trustworthy match links ("mu:<id>", "mangadex:<id>"), or None (no match, or not one to trust)."""
    if knowledge is None or knowledge.not_a_series or knowledge.link_state not in TRUSTED_STATES:
        return None
    if knowledge.mu_id:
        return f"mu:{str(knowledge.mu_id).strip()}"
    if knowledge.record_provider == PROVIDER_MANGADEX and knowledge.mangadex_id:
        return f"mangadex:{str(knowledge.mangadex_id).strip()}"
    return None


def _link_text(k: SeriesKnowledge) -> str:
    source = "MangaPixer" if k.source == SOURCE_MANGAPIXER else "Own match"
    return f"{source}: {k.link_state}"


def _copies(db) -> List[Tuple[str, SeriesCopy]]:
    """(record key, copy) of every present series with a trustworthy match, across every library folder: the series
    rows of the last scan, their own-matcher links and MangaPixer's items (no state is computed, no disk is read
    beyond resolving the library folders)."""
    return [(key, copy) for key, copy in _matches(db) if key is not None]


def matches_of(db, series_ids) -> Dict[int, Tuple[Optional[str], SeriesCopy]]:
    """series id -> (record key or None, its copy) for *series_ids* (present series only): what the merge checks."""
    wanted = {int(i) for i in series_ids if i is not None}
    return {copy.series_id: (key, copy) for key, copy in _matches(db, wanted)}


def _matches(db, only: Optional[set] = None) -> List[Tuple[Optional[str], SeriesCopy]]:
    """(record key - None without a trustworthy match -, copy) of every present series (*only*: of these ids)."""
    from .models import MangaEntry
    from .wanted import apply_links_cache, load_links_cache, mangapixer_item, open_resolver, resolved_roots

    try:
        links = load_links_cache(db)
    except Exception:  # noqa: BLE001 - without the links only MangaPixer's matches count
        _log.warning("Same series: the MangaUpdates links could not be read", exc_info=True)
        links = {}
    try:
        resolver = open_resolver(db)
    except Exception:  # noqa: BLE001 - a broken MangaPixer cache leaves the own matcher's links
        _log.warning("Same series: MangaPixer's data unavailable; the own matcher's links are used", exc_info=True)
        resolver = None
    dirs = resolved_roots(db)
    out: List[Tuple[Optional[str], SeriesCopy]] = []
    for root in db.list_roots():
        stored, base = dirs.get(root.id, (root.path, Path(root.path)))
        for row in db.list_series(root.id):
            if row.status != "present" or (only is not None and row.id not in only):
                continue
            folder = base.joinpath(*row.rel_path.split("/"))
            entry = MangaEntry(folder=folder, title=folder.name, english_title=None, root_id=root.id)
            cached = links.get(str(folder))
            if cached:
                apply_links_cache(entry, cached)
            item = None
            if resolver is not None:
                try:
                    item = mangapixer_item(resolver, root.id, stored, folder, resolved_root=base, own=entry)
                except Exception:  # noqa: BLE001 - a folder MangaPixer cannot place: the own matcher's link
                    _log.debug("Same series: no MangaPixer item for %s", folder, exc_info=True)
            knowledge = from_mangapixer_item(item) if item is not None else from_own_matcher(entry)
            out.append((match_key(knowledge), SeriesCopy(
                series_id=int(row.id), root_id=int(root.id), root_name=str(root.name), rel_path=row.rel_path,
                folder=str(folder), files=int(row.n_archives or 0), title=knowledge.title or folder.name,
                link=_link_text(knowledge), origin_status=knowledge.origin_status,
                confirmed=knowledge.link_state == LINK_CONFIRMED)))
    return out


# --- the rule ----------------------------------------------------------------------------------------------------


def _inside(inner: SeriesCopy, outer: SeriesCopy) -> bool:
    return inner.root_id == outer.root_id and inner.rel_path.startswith(outer.rel_path.rstrip("/") + "/")


def classify(key: str, copies: Sequence[SeriesCopy]) -> Tuple[List[SamePair], Optional[FranchiseSplit]]:
    """The duplicates and the franchise split (if any) in one group of copies of the record *key* (see the module
    docstring for the rules)."""
    copies = sorted(copies, key=lambda c: (c.root_name.casefold(), c.root_id, c.rel_path.casefold(), c.rel_path))
    title = next((c.title for c in copies if c.title), copies[0].name if copies else "")
    units: Dict[Tuple[int, str], List[SeriesCopy]] = {}
    for c in copies:
        units.setdefault((c.root_id, c.top), []).append(c)
    pairs: List[SamePair] = []
    for members in units.values():                          # a folder and a same-named folder inside it
        for outer in members:
            for inner in members:
                if inner is not outer and _inside(inner, outer) and same_name(inner.name, outer.name):
                    pairs.append(SamePair(key, title, outer, inner, KIND_NESTED))
    split: List[SeriesCopy] = []
    for (u1, m1), (u2, m2) in combinations(units.items(), 2):
        top1 = next((c for c in m1 if not c.is_part), None)
        top2 = next((c for c in m2 if not c.is_part), None)
        if top1 is not None and top2 is not None:          # two top-level series folders
            pairs.append(SamePair(key, title, top1, top2, KIND_DUPLICATE))
            continue
        for c in m1 + m2:                                   # a part of a franchise on one side at least
            if c not in split:
                split.append(c)
    franchise = FranchiseSplit(key, title, tuple(split)) if split else None
    return pairs, franchise


def find_same_series(db) -> SameSeriesReport:
    """Every series held in two folders (duplicates, each with its merge choices) and every franchise split across
    libraries, over every library folder. Sorted by title."""
    groups: Dict[str, List[SeriesCopy]] = {}
    for key, copy in _copies(db):
        groups.setdefault(key, []).append(copy)
    report = SameSeriesReport()
    for key, copies in groups.items():
        if len(copies) < 2:
            continue
        pairs, split = classify(key, copies)
        report.pairs += pairs
        if split is not None:
            report.splits.append(split)
    report.pairs.sort(key=lambda p: (p.title.casefold(), p.a.where.casefold(), p.b.where.casefold()))
    report.splits.sort(key=lambda s: (s.title.casefold(), s.key))
    _log.info("Same series: %d duplicate%s in two folders, %d franchise split%s across libraries", len(report.pairs),
              "" if len(report.pairs) == 1 else "s", len(report.splits), "" if len(report.splits) == 1 else "s")
    return report


__all__ = ["TRUSTED_STATES", "KIND_DUPLICATE", "KIND_NESTED", "SeriesCopy", "SamePair", "FranchiseSplit",
           "SameSeriesReport", "same_name", "match_key", "merge_refusal", "matches_of", "classify", "find_same_series"]
