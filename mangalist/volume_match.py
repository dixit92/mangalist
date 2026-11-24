"""Content-based volume-to-chapter matching: prove which loose chapter files lie inside a volume archive by perceptual
page hashes (port of MangaPixer's ``VolumeContentMatcher``, 1.44.0, over :mod:`mangalist.page_hash`).

Why: MangaPixer's volume list comes from MangaDex's aggregate (and Wikipedia), which can be missing or incomplete - for
example, chapters 1-3 uploaded with no volume tag on MangaDex so Volume 1 only lists ``4.1-5``. Comparing the volume
archive's 64-bit page hashes directly against the series' chapter files proves which chapters the volume actually holds:

- Volume-only pages (covers, colour inserts, table of contents, inter-chapter dividers, afterwords) are ignored because
  the matcher finds the longest strictly increasing chain of ``(chapter_page, volume_page)`` matches anywhere inside the
  volume.
- Chapter-only pages (scanlator credits, recruitment pages, merged double-page spreads) are tolerated up to
  ``max(3, 25 %)`` of the chapter's pages, provided at least :data:`MIN_RUN_PAGES` (5) pages match in order with no jump
  wider than :data:`MAX_JUMP` (5) pages.
- Hashes are cached in ``page_hashes`` (:mod:`mangalist.store.page_hashes`) by content signature so an unchanged archive
  is never decoded twice.

Pure matching rules + cached archive hashing; no Qt. Pillow is imported lazily through :mod:`mangalist.page_hash`.
"""

from __future__ import annotations

import bisect
import logging
import os
import stat
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from . import page_hash
from .identity.signature import is_usable, signature_if_unchanged
from .knowledge import fmt_num, to_decimal
from .page_hash import MAX_VOLUME_PAGES, STATUS_OK, ArchivePages, distance
from .store import page_hashes

_log = logging.getLogger(__name__)

# MangaPixer's VolumeContentMatcher constants (measured on the owner's library: 4,467 real pairs, 0 false chapters).
PAGE_MAX_DISTANCE = 16
MIN_RUN_PAGES = 5
MAX_JUMP = 5
MIN_ALLOWED_UNMATCHED = 3
MAX_UNMATCHED_SHARE = 0.25
MAX_PAGES_BEFORE = 8
MAX_PAGES_AFTER = 16
MAX_PAIRS = 20_000

# Series with more loose chapter files than this narrow the initial pass to plausible candidates and expand outward
# from any match (avoids hashing hundreds of unrelated chapters on a long-running series).
CANDIDATE_ALL_LIMIT = 80
_EXPAND_MISS_LIMIT = 5
_MAX_RANGE = 1000


@dataclass(frozen=True)
class HashedVolume:
    """One hashed volume archive: its key (path), single volume number (None for a multi-volume pack) and volumes."""

    key: str
    volume: Optional[Decimal]
    volumes: Tuple[Decimal, ...]
    pages: Tuple[Optional[int], ...]


@dataclass(frozen=True)
class HashedChapter:
    """One hashed chapter archive: its series-relative path, chapter span text and page hashes."""

    rel: str
    chapters: str
    pages: Tuple[Optional[int], ...]


@dataclass(frozen=True)
class MatchedChapter:
    """A chapter archive proven inside one (or two adjacent) volume archives."""

    rel: str
    chapters: str
    volumes: Tuple[Decimal, ...]
    matched: int
    pages: int
    first_page: int
    last_page: int
    split: bool = False


# --- pure matching (VolumeContentMatcher port) ------------------------------------------------------------------


def best_run(chapter: Sequence[Optional[int]], volume: Sequence[Optional[int]]) -> List[Tuple[int, int]]:
    """Longest in-order run of ``(chapter_page, volume_page)`` pairs within :data:`PAGE_MAX_DISTANCE` bits, cut where
    either side skips more than :data:`MAX_JUMP` pages, keeping the longest piece (and extending it only once the core
    reaches :data:`MIN_RUN_PAGES`)."""
    pairs: List[Tuple[int, int]] = []
    vol_len = len(volume)
    for i, a in enumerate(chapter):
        if a is None:
            continue
        # Descending volume page within one chapter page: strictly increasing LIS picks at most one partner per page.
        for j in range(vol_len - 1, -1, -1):
            b = volume[j]
            if b is not None and distance(a, b) <= PAGE_MAX_DISTANCE:
                pairs.append((i, j))
    if not pairs:
        return []

    tails: List[int] = []
    tail_idx: List[int] = []
    prev = [-1] * len(pairs)
    for k, (_ci, vj) in enumerate(pairs):
        pos = bisect.bisect_left(tails, vj)
        if pos == len(tails):
            tails.append(vj)
            tail_idx.append(k)
        else:
            tails[pos] = vj
            tail_idx[pos] = k
        if pos > 0:
            prev[k] = tail_idx[pos - 1]

    chain: List[Tuple[int, int]] = []
    k = tail_idx[-1]
    while k >= 0:
        chain.append(pairs[k])
        k = prev[k]
    chain.reverse()

    best_start, best_len = 0, 1
    start = 0
    for k in range(1, len(chain) + 1):
        if (k < len(chain)
                and chain[k][0] - chain[k - 1][0] <= MAX_JUMP + 1
                and chain[k][1] - chain[k - 1][1] <= MAX_JUMP + 1):
            continue
        if k - start > best_len:
            best_start, best_len = start, k - start
        start = k

    core = chain[best_start:best_start + best_len]
    return _extend(core, chapter, volume) if len(core) >= MIN_RUN_PAGES else core


def _extend(piece: List[Tuple[int, int]], chapter: Sequence[Optional[int]],
            volume: Sequence[Optional[int]]) -> List[Tuple[int, int]]:
    """Grow a core run at its ends by matching pages within :data:`MAX_JUMP` (recovers edges lost to a distant tie)."""
    def matches(i: int, j: int) -> bool:
        a, b = chapter[i], volume[j]
        return a is not None and b is not None and distance(a, b) <= PAGE_MAX_DISTANCE

    while True:
        ci, vj = piece[0]
        grown = False
        for i in range(ci - 1, max(-1, ci - MAX_JUMP - 2), -1):
            for j in range(vj - 1, max(-1, vj - MAX_JUMP - 2), -1):
                if matches(i, j):
                    piece.insert(0, (i, j))
                    grown = True
                    break
            if grown:
                break
        if not grown:
            break

    ch_len, vol_len = len(chapter), len(volume)
    while True:
        ci, vj = piece[-1]
        grown = False
        for i in range(ci + 1, min(ch_len, ci + MAX_JUMP + 2)):
            for j in range(vj + 1, min(vol_len, vj + MAX_JUMP + 2)):
                if matches(i, j):
                    piece.append((i, j))
                    grown = True
                    break
            if grown:
                break
        if not grown:
            break

    return piece


def proves(matched: int, pages: int) -> bool:
    """True when *matched* in-order pages prove a chapter of *pages* pages lies inside a volume."""
    return matched >= MIN_RUN_PAGES and (pages - matched) <= max(MIN_ALLOWED_UNMATCHED, pages * MAX_UNMATCHED_SHARE)


def match_volumes(volumes: Sequence[HashedVolume], chapters: Sequence[HashedChapter]) -> Dict[str, MatchedChapter]:
    """Match *chapters* against *volumes* (sorted by volume number). Returns ``{rel: MatchedChapter}`` for every
    chapter archive proven inside one volume (or split across two consecutive single-volume archives)."""
    if not volumes or not chapters or len(volumes) * len(chapters) > MAX_PAIRS:
        return {}

    ordered = sorted(volumes, key=lambda v: (v.volumes[0] if v.volumes else Decimal(0), v.key))
    runs: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    owner: Dict[int, Tuple[Tuple[Decimal, ...], List[Tuple[int, int]], bool]] = {}

    for c_idx, ch in enumerate(chapters):
        total = len(ch.pages)
        if total < MIN_RUN_PAGES:
            continue
        for v_idx, vol in enumerate(ordered):
            run = best_run(ch.pages, vol.pages)
            runs[(c_idx, v_idx)] = run
            if proves(len(run), total):
                prev = owner.get(c_idx)
                if prev is None or len(run) > len(prev[1]):
                    owner[c_idx] = (vol.volumes, run, False)

    # A chapter straddling two adjacent single-volume archives (e.g. a range file across v1 and v2):
    # proven only when BOTH adjacent volumes are present and the head closes v while the tail opens v+1.
    for c_idx, ch in enumerate(chapters):
        if c_idx in owner:
            continue
        total = len(ch.pages)
        if total < MIN_RUN_PAGES:
            continue
        for v_idx in range(len(ordered) - 1):
            v_cur, v_nxt = ordered[v_idx], ordered[v_idx + 1]
            if v_cur.volume is None or v_nxt.volume is None or v_nxt.volume != v_cur.volume + 1:
                continue
            head = runs.get((c_idx, v_idx), [])
            tail = runs.get((c_idx, v_idx + 1), [])
            if not head or not tail or head[-1][0] >= tail[0][0]:
                continue
            if head[-1][1] < len(v_cur.pages) - 1 - MAX_PAGES_AFTER or tail[0][1] > MAX_PAGES_BEFORE:
                continue
            if not proves(len(head) + len(tail), total):
                continue
            combined_vols = tuple(sorted(set(v_cur.volumes) | set(v_nxt.volumes)))
            owner[c_idx] = (combined_vols, head + tail, True)
            break

    out: Dict[str, MatchedChapter] = {}
    for c_idx, (vols, run, is_split) in owner.items():
        ch = chapters[c_idx]
        out[ch.rel] = MatchedChapter(
            rel=ch.rel,
            chapters=ch.chapters,
            volumes=vols,
            matched=len(run),
            pages=len(ch.pages),
            first_page=run[0][1],
            last_page=run[-1][1],
            split=is_split,
        )
    return out


# --- cached hashing against a series on disk --------------------------------------------------------------------


def _plain_below(path: str, top: str) -> Optional[os.stat_result]:
    top = os.path.normpath(top)
    path = os.path.normpath(path)
    if not os.path.normcase(path).startswith(os.path.normcase(top.rstrip(os.sep)) + os.sep):
        return None
    here, st = top, None
    for part in path[len(top.rstrip(os.sep)) + 1:].split(os.sep):
        here = os.path.join(here, part)
        try:
            st = os.lstat(here)
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode):
            return None
    return st if st is not None and stat.S_ISREG(st.st_mode) else None


def _numbers(lo: Decimal, hi: Decimal) -> Optional[List[Decimal]]:
    if hi < lo:
        lo, hi = hi, lo
    if hi - lo > _MAX_RANGE:
        return None
    out = {lo, hi}
    n = lo.to_integral_value() if lo == lo.to_integral_value() else int(lo) + 1
    while Decimal(n) <= hi:
        out.add(Decimal(n))
        n += 1
    return sorted(out)


def _span_text(numbers: Sequence[Decimal]) -> str:
    return fmt_num(numbers[0]) if len(numbers) == 1 else f"{fmt_num(numbers[0])}-{fmt_num(numbers[-1])}"


@dataclass(frozen=True)
class _ChapterCand:
    rel: str
    path: str
    chapters_text: str
    nums: Tuple[Decimal, ...]
    vol_tags: Tuple[Decimal, ...]


class _PageCache:
    """Read-through page-hash loader backed by ``archives`` signatures and ``page_hashes``."""

    def __init__(self, db, series_id: Optional[int]) -> None:
        self.db = db
        self._rows: Dict[str, object] = {}
        self._sigs: Dict[str, Optional[str]] = {}
        self._pages: Dict[str, Optional[ArchivePages]] = {}
        if series_id is not None:
            roots = {r.id: r.path for r in db.list_roots()}
            for a in db.list_archives(series_id=series_id, status="present"):
                root = roots.get(a.root_id)
                if root is not None:
                    self._rows[os.path.normpath(str(Path(root).joinpath(*a.rel_path.split("/"))))] = a

    def _signature(self, path: str, st: os.stat_result) -> Optional[str]:
        key = os.path.normpath(path)
        if key in self._sigs:
            return self._sigs[key]
        stamp = (int(st.st_size), int(st.st_mtime_ns))
        row = self._rows.get(key)
        sig = None
        if row is not None and (row.size, row.mtime_ns) == stamp and is_usable(row.signature, row.size):
            sig = row.signature
        if sig is None:
            sig = signature_if_unchanged(path, *stamp)
        self._sigs[key] = sig
        return sig

    def preload(self, items: Iterable[Tuple[str, os.stat_result]]) -> None:
        sigs = [s for p, st in items if (s := self._signature(p, st))]
        if not sigs:
            return
        for sig, pages in page_hashes.cached_pages(self.db, sigs).items():
            self._pages[sig] = pages

    def load(self, path: str, st: os.stat_result, *, max_pages: Optional[int] = None) -> Optional[ArchivePages]:
        sig = self._signature(path, st)
        if sig is not None and sig in self._pages:
            return self._pages[sig]
        pages = page_hash.hash_archive(path, expect=(int(st.st_size), int(st.st_mtime_ns)), max_pages=max_pages)
        if sig is not None:
            if page_hashes.store_pages(self.db, sig, pages):
                self._pages[sig] = pages
        return pages if pages.ok else None


def _chapter_candidates(units: Sequence, series_dir: str, root_path: str) -> List[_ChapterCand]:
    by_file: Dict[str, List] = defaultdict(list)
    for u in units:
        by_file[(u.rel_path or "").replace("\\", "/")].append(u)
    out: List[_ChapterCand] = []
    for rel, rows in sorted(by_file.items()):
        if not rows or any(r.kind != "chapter" for r in rows):
            continue
        nums: List[Decimal] = []
        valid = True
        for r in rows:
            lo = to_decimal(r.ch_from)
            hi = to_decimal(r.ch_to if r.ch_to is not None else r.ch_from)
            if lo is None or hi is None:
                valid = False
                break
            expanded = _numbers(lo, hi)
            if expanded is None:
                valid = False
                break
            nums.extend(expanded)
        if not valid or not nums:
            continue
        uniq_nums = tuple(sorted(set(nums)))
        tags: Set[Decimal] = set()
        for r in rows:
            t_lo = to_decimal(r.vol_from)
            t_hi = to_decimal(r.vol_to if r.vol_to is not None else r.vol_from)
            if t_lo is not None:
                tags.update(_numbers(t_lo, t_hi if t_hi is not None else t_lo) or [t_lo])
        path = os.path.join(series_dir, *rel.split("/"))
        if _plain_below(path, root_path) is None:
            continue
        out.append(_ChapterCand(rel=rel, path=path, chapters_text=_span_text(uniq_nums), nums=uniq_nums,
                                vol_tags=tuple(sorted(tags))))
    out.sort(key=lambda c: (c.nums[0], c.rel))
    return out


def _seed_indices(cands: Sequence[_ChapterCand], target_vols: Set[Decimal],
                  hint_spans: Mapping[Decimal, Tuple[Decimal, Decimal]]) -> Set[int]:
    """For very large chapter folders, select plausible candidate indices first before outward expansion."""
    seeds: Set[int] = set()
    windows: List[Tuple[Decimal, Decimal]] = []
    for v in target_vols:
        if v in hint_spans:
            lo, hi = hint_spans[v]
            windows.append((lo - Decimal(20), hi + Decimal(20)))
        else:
            lo = max(Decimal(0), (v - Decimal(1)) * Decimal(3))
            hi = v * Decimal(20) + Decimal(20)
            windows.append((lo, hi))
    for idx, c in enumerate(cands):
        if any(t in target_vols for t in c.vol_tags):
            seeds.add(idx)
            continue
        lo, hi = c.nums[0], c.nums[-1]
        if any(w_lo <= hi and lo <= w_hi for w_lo, w_hi in windows):
            seeds.add(idx)
    return seeds


def match_series_volumes(
    db,
    series_id: Optional[int],
    root_path: str,
    series_dir: str,
    volume_entries: Sequence[Tuple[Sequence[Decimal], str]],
    units: Sequence,
    *,
    hint_spans: Optional[Mapping[Decimal, Tuple[Decimal, Decimal]]] = None,
) -> Dict[str, MatchedChapter]:
    """Prove which chapter files in *units* lie inside *volume_entries* ``[(vol_numbers, abs_path), ...]`` using
    cached 64-bit perceptual page hashes. Returns ``{rel: MatchedChapter}`` (empty when Pillow is absent or no volume
    archive can be hashed as a zip/cbz)."""
    if not volume_entries or not units or not page_hash.pillow_available():
        return {}

    vol_stats: List[Tuple[Tuple[Decimal, ...], str, os.stat_result]] = []
    for vols, path in volume_entries:
        clean_vols = tuple(sorted({d for d in (to_decimal(v) for v in vols) if d is not None}))
        if not clean_vols:
            continue
        st = _plain_below(path, root_path)
        if st is not None:
            vol_stats.append((clean_vols, path, st))
    if not vol_stats:
        return {}

    cands = _chapter_candidates(units, series_dir, root_path)
    if not cands:
        return {}

    cache = _PageCache(db, series_id)
    cache.preload((p, st) for _vs, p, st in vol_stats)

    hashed_vols: List[HashedVolume] = []
    for vols, path, st in vol_stats:
        pages = cache.load(path, st, max_pages=MAX_VOLUME_PAGES)
        if pages is not None and pages.ok and pages.pages >= MIN_RUN_PAGES:
            hashed_vols.append(HashedVolume(
                key=path,
                volume=vols[0] if len(vols) == 1 else None,
                volumes=vols,
                pages=pages.hashes,
            ))
    if not hashed_vols:
        return {}

    target_vols: Set[Decimal] = {v for hv in hashed_vols for v in hv.volumes}

    def load_cands(indices: Iterable[int]) -> List[HashedChapter]:
        stat_list: List[Tuple[_ChapterCand, os.stat_result]] = []
        for idx in indices:
            c = cands[idx]
            st = _plain_below(c.path, root_path)
            if st is not None:
                stat_list.append((c, st))
        cache.preload((c.path, st) for c, st in stat_list)
        loaded: List[HashedChapter] = []
        for c, st in stat_list:
            pages = cache.load(c.path, st)
            if pages is not None and pages.ok and pages.pages >= MIN_RUN_PAGES:
                loaded.append(HashedChapter(rel=c.rel, chapters=c.chapters_text, pages=pages.hashes))
        return loaded

    if len(cands) <= CANDIDATE_ALL_LIMIT:
        return match_volumes(hashed_vols, load_cands(range(len(cands))))

    # Large folder: hash seed window first, then expand outward from any matched chapter until misses stop it.
    checked = _seed_indices(cands, target_vols, hint_spans or {})
    if not checked:
        checked = set(range(min(len(cands), CANDIDATE_ALL_LIMIT)))
    hashed_by_idx: Dict[int, HashedChapter] = {}
    for hc in load_cands(sorted(checked)):
        for idx, c in enumerate(cands):
            if c.rel == hc.rel:
                hashed_by_idx[idx] = hc
                break
    matched = match_volumes(hashed_vols, list(hashed_by_idx.values()))

    if matched:
        matched_indices = sorted(idx for idx, c in enumerate(cands) if c.rel in matched)
        frontier_pairs = [(matched_indices[0], -1), (matched_indices[-1], 1)]
        for origin, step in frontier_pairs:
            misses = 0
            idx = origin + step
            while 0 <= idx < len(cands) and misses < _EXPAND_MISS_LIMIT:
                if idx not in checked:
                    checked.add(idx)
                    extra = load_cands([idx])
                    if extra:
                        hashed_by_idx[idx] = extra[0]
                        hit = match_volumes(hashed_vols, extra)
                        if hit:
                            matched.update(hit)
                            misses = 0
                            idx += step
                            continue
                elif cands[idx].rel in matched:
                    misses = 0
                    idx += step
                    continue
                misses += 1
                idx += step
        # Re-run across all loaded candidates together so cross-volume split chapters see both sides cleanly.
        matched = match_volumes(hashed_vols, [hashed_by_idx[i] for i in sorted(hashed_by_idx)])

    return matched


__all__ = [
    "CANDIDATE_ALL_LIMIT", "MAX_JUMP", "MAX_PAGES_AFTER", "MAX_PAGES_BEFORE", "MAX_PAIRS", "MAX_UNMATCHED_SHARE",
    "MIN_ALLOWED_UNMATCHED", "MIN_RUN_PAGES", "PAGE_MAX_DISTANCE", "HashedChapter", "HashedVolume", "MatchedChapter",
    "best_run", "match_series_volumes", "match_volumes", "proves",
]
