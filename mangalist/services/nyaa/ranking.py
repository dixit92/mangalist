"""Series matching and the D4 ranking: a nyaa :class:`RssItem` + its :class:`ParsedTitle` -> a ranked
:class:`~mangalist.downloads.contracts.NyaaCandidate` for one series. Pure; no I/O, no Qt.

**Series match.** A release belongs to the series only when its parsed series text equals one of the series' names
after MangaList's own title normalisation (:func:`mangalist.matcher.normalizer.normalize` - brackets, volume
tokens, edition words go - and :func:`~mangalist.matcher.normalizer.scoring_form` - case, diacritics, punctuation,
spacing). Equality, not similarity: ``Berserk of Gluttony`` must never pass for ``Berserk``. A leading ``The`` is
ignored on both sides.

**Ranking (D4, owner-approved).** In this order: how many of the series' MISSING volumes the release holds, then
Digital over scans, then trusted uploaders, then seeders; a remake is ranked down. ``rank`` is one number that sorts
exactly so (each part outweighs every later one). Releases whose volume numbers cannot be compared to the series'
(a pack without numbers, an Omnibus / Deluxe edition that re-numbers) count as half a volume: below any release
that certainly fills a missing volume, above one that certainly fills none.
"""

from __future__ import annotations

from decimal import Decimal
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple

from ...downloads.contracts import NyaaCandidate
from ...matcher.normalizer import normalize, scoring_form
from ...parsing.model import to_decimal
from .rss import RssItem
from .titles import ParsedTitle

_TIER = 1_000_000.0
_DIGITAL = 100_000.0
_TRUSTED = 10_000.0
_REMAKE = 5_000.0
_SEEDERS_CAP = 4_999
_UNCERTAIN_TIER = 0.5


# --- series match -------------------------------------------------------------------------------------

def _forms(title: Optional[str]) -> FrozenSet[str]:
    """The comparison forms of one title (its variants), without spaces and without a leading 'the'."""
    out = set()
    n = normalize(title)
    for text in n.variants + ((n.primary_with_exclamation,) if n.primary_with_exclamation else ()):
        form = scoring_form(text)
        if form.startswith("the "):
            form = form[4:]
        form = form.replace(" ", "")
        if form:
            out.add(form)
    return frozenset(out)


def series_forms(titles: Iterable[str]) -> FrozenSet[str]:
    """Every comparison form of the series' names (main title and alternatives)."""
    out: set = set()
    for t in titles:
        out |= _forms(t)
    return frozenset(out)


def matches_series(parsed: ParsedTitle, forms: FrozenSet[str]) -> bool:
    return bool(parsed.series) and bool(_forms(parsed.series) & forms)


# --- coverage -------------------------------------------------------------------------------------------

def _covered(volumes: Sequence[str], parsed: ParsedTitle) -> Tuple[str, ...]:
    """The entries of ``volumes`` (as given) the release holds. A range holds its whole numbers and its two ends
    (``v12-13`` does not hold ``12.5``). An edition that re-numbers holds none we can name."""
    if parsed.vol_from is None or parsed.vol_to is None or parsed.renumbered:
        return ()
    low, high = Decimal(parsed.vol_from), Decimal(parsed.vol_to)
    out: List[str] = []
    for v in volumes:
        d = to_decimal(v)
        if d is None or not low <= d <= high:
            continue
        if d == low or d == high or d == d.to_integral_value():
            out.append(v)
    return tuple(out)


_MAX_RANGE = 500    # a range wider than this is not expanded into volume numbers (a malformed title)


def _not_held(parsed: ParsedTitle, held: Sequence[str]) -> Tuple[str, ...]:
    """With no missing list (MangaList cannot tell which English volumes are out): the volumes of the release's range
    that are not held - its whole numbers and its two ends, as :func:`_covered` reads a range."""
    if parsed.vol_from is None or parsed.vol_to is None or parsed.renumbered:
        return ()
    low, high = Decimal(parsed.vol_from), Decimal(parsed.vol_to)
    if high < low or high - low > _MAX_RANGE:
        return ()
    have = {d for d in (to_decimal(v) for v in held) if d is not None}
    first = int(low) if low == low.to_integral_value() else int(low) + 1
    numbers = sorted({low, high} | {Decimal(n) for n in range(first, int(high) + 1)})
    return tuple(_plain(d) for d in numbers if d not in have)


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _uncertain(parsed: ParsedTitle) -> bool:
    return bool(parsed.renumbered) or (parsed.vol_from is None and parsed.is_pack)


def _list(volumes: Sequence[str], limit: int = 6) -> str:
    if len(volumes) <= limit:
        return ", ".join(volumes)
    return f"{volumes[0]} ... {volumes[-1]}"


# --- one candidate --------------------------------------------------------------------------------------

def build_candidate(item: RssItem, parsed: ParsedTitle, missing: Sequence[str], held: Sequence[str]
                    ) -> NyaaCandidate:
    # The missing list may be unknown (empty): then every volume of the release that is not held counts.
    covers_missing = _covered(missing, parsed) if missing else _not_held(parsed, held)
    covers_held = _covered(held, parsed)
    uncertain = _uncertain(parsed)
    tier = float(len(covers_missing)) or (_UNCERTAIN_TIER if uncertain else 0.0)

    rank = (tier * _TIER + (_DIGITAL if parsed.digital else 0.0) + (_TRUSTED if item.trusted else 0.0)
            - (_REMAKE if item.remake else 0.0) + min(item.seeders, _SEEDERS_CAP))

    reasons: List[str] = []
    if covers_missing:
        n = len(covers_missing)
        what = "missing volume" if missing else "volume you do not have"
        reasons.append(f"covers {n} {what}{'s' if n != 1 else ''} ({_list(covers_missing)})")
    elif parsed.renumbered:
        reasons.append(f"{parsed.renumbered} edition: its volume numbers may differ from the series'")
    elif parsed.vol_from is None and parsed.is_pack:
        reasons.append("pack without volume numbers: contents unknown")
    elif parsed.vol_from is None:
        reasons.append("volumes not stated in the title")
    if covers_held:
        n = len(covers_held)
        reasons.append(f"includes {n} volume{'s' if n != 1 else ''} you already have ({_list(covers_held)})")
    reasons.append("Digital" if parsed.digital else "scan release (not Digital)")
    if item.trusted:
        reasons.append("trusted uploader")
    if item.remake:
        reasons.append("remake (ranked down)")
    if parsed.not_comic:
        reasons.append("light novel / EPUB / audiobook, not a manga release")
    reasons.append(f"{item.seeders} seeder{'s' if item.seeders != 1 else ''}")

    return NyaaCandidate(
        title=item.title, view_url=item.view_url, torrent_url=item.torrent_url, info_hash=item.info_hash,
        size_bytes=item.size_bytes, seeders=item.seeders, leechers=item.leechers, downloads=item.downloads,
        trusted=item.trusted, remake=item.remake, published=item.published, category=item.category,
        vol_from=parsed.vol_from, vol_to=parsed.vol_to, digital=parsed.digital, group=parsed.group,
        is_pack=parsed.is_pack, not_comic=parsed.not_comic, covers_missing=covers_missing,
        covers_held=covers_held, rank=rank, reasons=tuple(reasons))


def fills_nothing(candidate: NyaaCandidate, parsed: ParsedTitle, missing: Sequence[str]) -> bool:
    """True when the release certainly holds none of the missing volumes - or, with no missing list, only volumes
    already held (its numbers are known and comparable)."""
    return not candidate.covers_missing and not _uncertain(parsed) and parsed.vol_from is not None


def order(candidates: Iterable[NyaaCandidate]) -> List[NyaaCandidate]:
    """Best first: rank, then the newer upload, then the title (stable and deterministic)."""
    by_title = sorted(candidates, key=lambda c: c.title)
    by_date = sorted(by_title, key=lambda c: c.published, reverse=True)
    return sorted(by_date, key=lambda c: c.rank, reverse=True)


__all__ = ["build_candidate", "fills_nothing", "matches_series", "order", "series_forms"]
