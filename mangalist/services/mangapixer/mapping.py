"""Mapping a MangaList root to a MangaPixer library (and a trail prefix inside it).

MangaPixer never sends paths, only ``trail``s: the on-disk folder names below the library root. A
MangaList root is matched by its folder names: each series folder's root-relative path (and its parent
folders) is compared with the export's folder trails. A root may be the library root itself (prefix
``[]``) or a folder inside it (prefix e.g. ``["Shonen"]``): for every item trail ``T`` and every split
``T = prefix + rest``, a MangaList folder whose path equals ``rest`` votes for ``(library, prefix)``.
The candidate with the most matched MangaList folders wins (ties: more exact matches, then the shorter
prefix, then MangaPixer's library order). No match at all: no mapping.

Names are compared after Unicode NFC normalisation, case-sensitively first and case-insensitively
(``casefold``) as the fallback for a name that has no exact match.

The result reports ``matched`` / ``unmatched``: series folders that do / do not get an item (their own
or an ancestor's - see :mod:`.resolve`) under the chosen mapping.

A manual override per root (``Mapping.manual``) is never replaced by the automatic mapping; a manual
mapping with ``library_id=None`` means "do not use MangaPixer for this root". Libraries whose kind is
skipped by default (comic, graphic-novel, novel) are only candidates for a manual mapping with
``any_kind=True``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ...store.mangapixer import Mapping, MangaPixerCache, fold_key, nfc, trail_key

_log = logging.getLogger(__name__)

ListSeries = Callable[[int], List[str]]


@dataclass
class Candidate:
    library_id: str
    prefix: Tuple[str, ...]
    matched: int = 0               # MangaList folders (series or their parents) with an item there
    exact: int = 0                 # ... of them matched case-sensitively


@dataclass
class Proposal:
    library_id: Optional[str]
    prefix: List[str] = field(default_factory=list)
    matched: int = 0               # series folders that resolve to an item (own or inherited)
    unmatched: int = 0
    candidates: List[Candidate] = field(default_factory=list)   # best first (at most 10)


def _parts(rel_path: str) -> Tuple[str, ...]:
    return tuple(nfc(p) for p in str(rel_path).replace("\\", "/").split("/") if p and p != ".")


def _folders_with_parents(rel_paths: Iterable[str]) -> List[Tuple[str, ...]]:
    out = set()
    for rel in rel_paths:
        parts = _parts(rel)
        for n in range(1, len(parts) + 1):
            out.add(parts[:n])
    return sorted(out)


def score_library(library_id: str, trails: Iterable[Sequence[str]], rel_paths: Iterable[str]) -> List[Candidate]:
    """Every (prefix) candidate of one library, scored against the MangaList folder paths."""
    folders = _folders_with_parents(rel_paths)
    exact_set = {"/".join(f) for f in folders}
    by_fold: Dict[str, List[str]] = defaultdict(list)
    for f in folders:
        by_fold[fold_key(f)].append("/".join(f))
    # prefix -> {MangaList folder: exact?}
    votes: Dict[Tuple[str, ...], Dict[str, bool]] = defaultdict(dict)
    for trail in trails:
        t = tuple(nfc(p) for p in trail)
        for k in range(len(t)):
            rest = t[k:]
            key = "/".join(rest)
            hit = votes[t[:k]]
            if key in exact_set:
                hit[key] = True
            else:
                for folder in by_fold.get(fold_key(rest), ()):
                    hit.setdefault(folder, False)
    out = []
    for prefix, hits in votes.items():
        if hits:
            out.append(Candidate(library_id, prefix, matched=len(hits), exact=sum(1 for v in hits.values() if v)))
    return out


def _rank(c: Candidate, order: Dict[str, int]):
    return (-c.matched, -c.exact, len(c.prefix), order.get(c.library_id, 1 << 30), c.prefix)


def propose(cache: MangaPixerCache, rel_paths: Sequence[str], library_ids: Optional[Sequence[str]] = None) -> Proposal:
    """The automatic mapping for a root with these series folder paths (root-relative, ``/``)."""
    libs = cache.libraries_to_sync() if library_ids is None else [
        lib for lib in (cache.library(i) for i in library_ids) if lib is not None]
    order = {lib.id: n for n, lib in enumerate(libs)}
    candidates: List[Candidate] = []
    for lib in libs:
        trails = [row.trail for row in cache.items(lib.id)]
        candidates.extend(score_library(lib.id, trails, rel_paths))
    candidates.sort(key=lambda c: _rank(c, order))
    if not candidates:
        return Proposal(None, [], 0, len(list(rel_paths)), [])
    best = candidates[0]
    matched, unmatched = count_matches(cache, best.library_id, list(best.prefix), rel_paths)
    return Proposal(best.library_id, list(best.prefix), matched, unmatched, candidates[:10])


def count_matches(cache: MangaPixerCache, library_id: Optional[str], prefix: Sequence[str],
                  rel_paths: Sequence[str]) -> Tuple[int, int]:
    """(matched, unmatched) series folders: those that resolve to an item (own or inherited)."""
    from .resolve import LibraryIndex

    if not library_id:
        return 0, len(rel_paths)
    index = LibraryIndex.load(cache, library_id)
    matched = sum(1 for rel in rel_paths if index.lookup(list(prefix), rel) is not None)
    return matched, len(rel_paths) - matched


def _default_list_series(cache: MangaPixerCache) -> ListSeries:
    def list_series(root_id: int) -> List[str]:
        return [s.rel_path for s in cache.store.list_series(root_id) if getattr(s, "status", "present") == "present"]

    return list_series


def refresh_auto_mappings(cache: MangaPixerCache, list_series: Optional[ListSeries] = None) -> Dict[int, Mapping]:
    """Re-compute the automatic mapping of every root that has no manual override; manual mappings
    get fresh matched / unmatched counts. Returns every root's mapping."""
    list_series = list_series or _default_list_series(cache)
    current = cache.mappings()
    out: Dict[int, Mapping] = {}
    for root in cache.store.list_roots():
        rels = list_series(root.id)
        m = current.get(root.id)
        if m is not None and m.manual:
            m.matched, m.unmatched = count_matches(cache, m.library_id, m.prefix, rels)
        else:
            p = propose(cache, rels)
            m = Mapping(root_id=root.id, library_id=p.library_id, prefix=p.prefix, manual=False,
                        matched=p.matched, unmatched=p.unmatched)
            _log.info("MangaPixer: root %r -> %s (prefix %d deep): %d matched, %d unmatched", root.name,
                      p.library_id or "no library", len(p.prefix), p.matched, p.unmatched)
        out[root.id] = cache.save_mapping(m)
    return out


def set_manual_mapping(cache: MangaPixerCache, root_id: int, library_id: Optional[str],
                       prefix: Sequence[str] = (), any_kind: bool = False,
                       list_series: Optional[ListSeries] = None) -> Mapping:
    """The owner's override for one root (``library_id=None``: do not use MangaPixer for it)."""
    list_series = list_series or _default_list_series(cache)
    rels = list_series(root_id)
    prefix = [nfc(p) for p in prefix if str(p).strip()]
    matched, unmatched = count_matches(cache, library_id, prefix, rels)
    return cache.save_mapping(Mapping(root_id=root_id, library_id=library_id, prefix=prefix, manual=True,
                                      any_kind=bool(any_kind), matched=matched, unmatched=unmatched))


def clear_manual_mapping(cache: MangaPixerCache, root_id: int, list_series: Optional[ListSeries] = None) -> Mapping:
    """Back to the automatic mapping for one root."""
    list_series = list_series or _default_list_series(cache)
    rels = list_series(root_id)
    p = propose(cache, rels)
    return cache.save_mapping(Mapping(root_id=root_id, library_id=p.library_id, prefix=p.prefix, manual=False,
                                      matched=p.matched, unmatched=p.unmatched))


__all__ = ["Candidate", "Proposal", "score_library", "propose", "count_matches", "refresh_auto_mappings",
           "set_manual_mapping", "clear_manual_mapping", "trail_key"]
