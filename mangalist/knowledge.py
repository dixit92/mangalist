"""What MangaList knows about one series, from whichever source knows it (pure, no Qt).

A :class:`SeriesKnowledge` is everything a rescan state (:mod:`mangalist.states`) and the official
sources (:mod:`mangalist.official_sources`) are computed from: the linked record's identity, the
origin's status and totals, the latest chapter (an exact string), the English edition (publishers
with their volume / chapter totals and status, translation complete), the per-volume list with
English dates, MangaPixer's Completion answer when MangaPixer computed one, the official links and
when the record was read.

Two adapters build one:

* :func:`from_mangapixer_item` - an item of MangaPixer's metadata export (``schemaVersion`` 1, the
  documented shape; unknown fields are ignored);
* :func:`from_own_matcher` - MangaList's own matcher: a :class:`~mangalist.models.MangaEntry`'s
  MangaUpdates fields (as cached today), optionally the full MangaUpdates series record and the
  AniList answer.

Unit numbers stay exact: they are kept as the strings the source gave (``"24.5"``, ``"291.999"``) and
compared as :class:`~decimal.Decimal` (:func:`to_decimal`), never as floats.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SOURCE_MANGAPIXER = "mangapixer"
SOURCE_OWN_MATCHER = "own-matcher"

# Link states, as MangaPixer names them; the own matcher's bands map onto them (see from_own_matcher).
LINK_CONFIRMED = "Confirmed"
LINK_AUTO = "Auto"
LINK_NEEDS_REVIEW = "NeedsReview"
LINK_DONT_MATCH = "DontMatch"
LINK_COLLECTION_ABOUT = "CollectionAbout"  # MangaPixer 1.34.0: a folder of works ABOUT a series (fan works)

# MangaPixer link states that make a folder a series. Every other state - DontMatch, CollectionAbout and any
# state MangaList does not know yet - means "not a series": no own matching, no numbers from the record, and
# it stops inheritance (MangaPixer's docs/metadata-export.md, Versioning, 1.34.0; agreed 2026-10-03).
MANGAPIXER_SERIES_STATES = frozenset({LINK_CONFIRMED, LINK_AUTO, LINK_NEEDS_REVIEW})
MANGAPIXER_KNOWN_STATES = MANGAPIXER_SERIES_STATES | {LINK_DONT_MATCH, LINK_COLLECTION_ABOUT}
LINK_UNMATCHED = "Unmatched"        # own matcher only: looked up, no confident match
LINK_NOT_A_WORK = "NotAWork"        # own matcher only: the folder is not one work
LINK_NOT_LOOKED_UP = "NotLookedUp"  # own matcher only: no MangaUpdates lookup yet

# MangaPixer Completion answers (one answer, five values).
ANSWER_HAVE_IT_ALL = "HaveItAll"
ANSWER_FINISHED_MISSING = "FinishedMissing"
ANSWER_UP_TO_DATE = "UpToDate"
ANSWER_MISSING_SOME = "MissingSome"
ANSWER_CANT_TELL = "CantTell"

DATE_RELEASED = "released"
DATE_ANNOUNCED = "announced"


# --- exact numbers -------------------------------------------------------------------------------


def to_decimal(value: Any) -> Optional[Decimal]:
    """An exact number from a string, int, Decimal or float (floats through their shortest repr, so
    ``12.5`` stays ``12.5``); None for anything that is not a finite number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, float):
        value = repr(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def fmt_num(value: Any) -> str:
    """An exact number for display: ``Decimal('12.0')`` -> ``12``, ``24.50`` -> ``24.5``,
    ``291.999`` stays; '' for no number."""
    d = to_decimal(value)
    if d is None:
        return ""
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def exact_str(value: Any) -> Optional[str]:
    """The exact string form of a unit number (or None): what MangaList stores and compares."""
    d = to_decimal(value)
    return None if d is None else fmt_num(d)


# --- partial English dates -----------------------------------------------------------------------

_RE_DATE = re.compile(r"^\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?")


def parse_partial_date(text: Optional[str]) -> Optional[Tuple[int, ...]]:
    """``"2025-07-01"`` -> (2025, 7, 1), ``"2025-07"`` -> (2025, 7), ``"2025"`` -> (2025,); None when it
    is not a date (MangaPixer may send a year-month or a year only)."""
    if not text:
        return None
    m = _RE_DATE.match(str(text))
    if not m:
        return None
    parts = tuple(int(g) for g in m.groups() if g is not None)
    if len(parts) >= 2 and not 1 <= parts[1] <= 12:
        return None
    if len(parts) == 3:
        try:
            _dt.date(*parts)
        except ValueError:
            return None
    return parts


def date_is_released(text: Optional[str], today: _dt.date) -> Optional[bool]:
    """True when the (possibly partial) date is not after *today*, compared at the date's own
    precision (``"2026-10"`` counts as released on any day of October 2026, ``"2026"`` during 2026);
    False when it lies after today; None when it is not a date."""
    parts = parse_partial_date(text)
    if parts is None:
        return None
    today_parts = (today.year, today.month, today.day)[: len(parts)]
    return parts <= today_parts


# --- the knowledge -------------------------------------------------------------------------------


@dataclass(frozen=True)
class EnglishPublisher:
    name: str
    volumes: Optional[Decimal] = None
    chapters: Optional[Decimal] = None
    status: Optional[str] = None       # "Ongoing" | "Completed" | "Cancelled" | ... (as the source says)
    omnibus: bool = False
    publisher_id: Optional[int] = None

    def __post_init__(self):
        for name in ("volumes", "chapters"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))

    @property
    def finished(self) -> bool:
        return (self.status or "").strip().lower() in ("completed", "complete", "finished")


@dataclass(frozen=True)
class VolumeInfo:
    """One volume of the edition MangaPixer's volume list describes; numbers are exact strings."""

    volume: str
    chapters_from: Optional[str] = None
    chapters_to: Optional[str] = None
    english_date: Optional[str] = None       # "2025-07-01", "2025-07" or "2025"
    english_date_kind: Optional[str] = None  # "released" | "announced" (as of the export rebuild)
    isbn: Optional[str] = None
    title: Optional[str] = None

    @property
    def has_chapters(self) -> bool:
        return self.chapters_from is not None and self.chapters_to is not None

    def released(self, today: _dt.date) -> Optional[bool]:
        """Is the English volume out on *today*? The date decides (the export's kind can be stale);
        without a date, the kind; None when neither says."""
        by_date = date_is_released(self.english_date, today)
        if by_date is not None:
            return by_date
        if self.english_date_kind == DATE_RELEASED:
            return True
        if self.english_date_kind == DATE_ANNOUNCED:
            return False
        return None


@dataclass(frozen=True)
class Completion:
    """MangaPixer's Completion answer (a cross-check for MangaList's own state)."""

    answer: str
    reason: Optional[str] = None
    upgrade_available: bool = False
    upgrade_volumes: Tuple[str, ...] = ()
    computed_at: Optional[str] = None


@dataclass(frozen=True)
class OfficialLink:
    """An official source: ``kind`` publisher | reader | store | search. ``url`` is None for a name
    MangaList knows without a page (a MangaUpdates English publisher)."""

    kind: str
    label: str
    url: Optional[str]
    source: str

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {"kind": self.kind, "label": self.label, "url": self.url, "source": self.source}


@dataclass(frozen=True)
class SeriesKnowledge:
    source: str                                 # SOURCE_MANGAPIXER | SOURCE_OWN_MATCHER
    link_state: str = LINK_NOT_LOOKED_UP
    # record identity
    mu_id: Optional[str] = None
    mu_url: Optional[str] = None
    title: Optional[str] = None
    english_title: Optional[str] = None         # for store searches; may be None
    alt_titles: Tuple[str, ...] = ()
    series_type: Optional[str] = None           # "Manga", "Manhwa", ...
    # origin
    origin_status: Optional[str] = None         # "Ongoing" | "Complete" | ...
    origin_volumes: Optional[Decimal] = None
    completed_in_origin: Optional[bool] = None
    latest_chapter: Optional[str] = None        # exact string ("41", "12.5")
    total_chapters: Optional[Decimal] = None
    scan_latest_volume: Optional[Decimal] = None  # own matcher: MangaUpdates releases
    # English edition
    licensed_en: Optional[bool] = None
    english_publishers: Tuple[EnglishPublisher, ...] = ()
    translation_complete: Optional[bool] = None
    volumes: Tuple[VolumeInfo, ...] = ()
    # MangaPixer
    completion: Optional[Completion] = None
    official_links: Tuple[OfficialLink, ...] = ()
    # AniList companion
    anilist_id: Optional[int] = None
    anilist_chapters: Optional[Decimal] = None
    anilist_volumes: Optional[Decimal] = None
    anilist_links: Tuple[Mapping[str, Any], ...] = ()   # raw externalLinks rows
    # freshness
    fetched_at: Optional[str] = None
    next_due_at: Optional[str] = None

    def __post_init__(self):
        # Numbers are exact: whatever a caller passes (int, float, str) becomes a Decimal.
        for name in _DECIMAL_FIELDS:
            object.__setattr__(self, name, to_decimal(getattr(self, name)))
        object.__setattr__(self, "latest_chapter", exact_str(self.latest_chapter))

    # -- derived -------------------------------------------------------------------------------

    @property
    def matched(self) -> bool:
        """A record is linked (Confirmed or Auto) - numbers can be compared."""
        return self.link_state in (LINK_CONFIRMED, LINK_AUTO) and (self.mu_id is not None or self.title is not None)

    @property
    def not_a_series(self) -> bool:
        """MangaPixer says this folder is not a series (DontMatch, CollectionAbout or a state MangaList does
        not know): nothing is computed for it and MangaList never matches it itself."""
        return self.source == SOURCE_MANGAPIXER and self.link_state not in MANGAPIXER_SERIES_STATES

    @property
    def not_a_series_reason(self) -> Optional[str]:
        if not self.not_a_series:
            return None
        if self.link_state == LINK_COLLECTION_ABOUT:
            return f"Collection about {self.title}" if self.title else "Collection about a series"
        if self.link_state == LINK_DONT_MATCH:
            return "Don't match (in MangaPixer)"
        return f"Unknown MangaPixer link state {self.link_state!r} - treated as not a series"

    @property
    def needs_review(self) -> bool:
        return self.link_state == LINK_NEEDS_REVIEW

    @property
    def licensed(self) -> bool:
        return self.licensed_en is True or bool(self.english_publishers)

    @property
    def latest_chapter_decimal(self) -> Optional[Decimal]:
        return to_decimal(self.latest_chapter)

    @property
    def finished_in_origin(self) -> bool:
        if self.completed_in_origin is not None:
            return self.completed_in_origin
        return (self.origin_status or "").strip().lower() in ("complete", "completed", "finished")

    @property
    def english_finished(self) -> bool:
        """The English edition has ended (translation complete, or a publisher says Completed)."""
        return self.translation_complete is True or any(p.finished for p in self.english_publishers)

    @property
    def publisher_volumes(self) -> Optional[Decimal]:
        vals = [p.volumes for p in self.english_publishers if p.volumes is not None]
        return max(vals) if vals else None

    @property
    def publisher_chapters(self) -> Optional[Decimal]:
        vals = [p.chapters for p in self.english_publishers if p.chapters is not None]
        return max(vals) if vals else None

    @property
    def search_title(self) -> Optional[str]:
        return self.english_title or self.title


_DECIMAL_FIELDS = ("origin_volumes", "total_chapters", "scan_latest_volume", "anilist_chapters", "anilist_volumes")


# --- MangaPixer export item -> knowledge -------------------------------------------------------


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    text = str(v).strip()
    return text or None


def _publishers_from_mangapixer(rows: Any) -> Tuple[EnglishPublisher, ...]:
    out: List[EnglishPublisher] = []
    for p in rows or ():
        if not isinstance(p, Mapping) or not _s(p.get("name")):
            continue
        out.append(EnglishPublisher(
            name=_s(p.get("name")) or "",
            volumes=to_decimal(p.get("volumes")),
            chapters=to_decimal(p.get("chapters")),
            status=_s(p.get("status")),
            omnibus=bool(p.get("omnibus")),
        ))
    return tuple(out)


def _volumes_from_mangapixer(block: Any) -> Tuple[VolumeInfo, ...]:
    if not isinstance(block, Mapping):
        return ()
    out: List[VolumeInfo] = []
    for v in block.get("items") or ():
        if not isinstance(v, Mapping) or exact_str(v.get("volume")) is None:
            continue
        ch = v.get("chapters") if isinstance(v.get("chapters"), Mapping) else None
        out.append(VolumeInfo(
            volume=exact_str(v.get("volume")) or "",
            chapters_from=exact_str(ch.get("from")) if ch else None,
            chapters_to=exact_str(ch.get("to")) if ch else None,
            english_date=_s(v.get("englishDate")),
            english_date_kind=_s(v.get("englishDateKind")),
            isbn=_s(v.get("isbn")),
            title=_s(v.get("title")),
        ))
    return tuple(out)


def _completion_from_mangapixer(block: Any) -> Optional[Completion]:
    if not isinstance(block, Mapping) or not _s(block.get("answer")):
        return None
    upgrade = tuple(s for s in (exact_str(v) for v in block.get("upgradeVolumes") or ()) if s is not None)
    return Completion(
        answer=_s(block.get("answer")) or "",
        reason=_s(block.get("reason")),
        upgrade_available=bool(block.get("upgradeAvailable")) or bool(upgrade),
        upgrade_volumes=upgrade,
        computed_at=_s(block.get("computedAt")),
    )


def _links_from_mangapixer(rows: Any) -> Tuple[OfficialLink, ...]:
    out: List[OfficialLink] = []
    for row in rows or ():
        if not isinstance(row, Mapping) or not _s(row.get("url")):
            continue
        out.append(OfficialLink(
            kind=_s(row.get("kind")) or "publisher",
            label=_s(row.get("label")) or _s(row.get("url")) or "",
            url=_s(row.get("url")),
            source=_s(row.get("source")) or SOURCE_MANGAPIXER,
        ))
    return tuple(out)


def from_mangapixer_item(item: Mapping[str, Any], *, english_title: Optional[str] = None) -> SeriesKnowledge:
    """Knowledge from one item of MangaPixer's metadata export (the documented v1 item shape).

    ``record`` is None for Needs review and Don't match (only the link state is known then); a record
    of a provider MangaList does not know (``gcd``) is ignored like a missing one."""
    link = item.get("link") if isinstance(item.get("link"), Mapping) else {}
    state = _s(link.get("state")) or LINK_NOT_LOOKED_UP
    record = item.get("record") if isinstance(item.get("record"), Mapping) else None
    if record is not None and (_s(record.get("provider")) or "mangaupdates") != "mangaupdates":
        record = None
    companions = item.get("companions") if isinstance(item.get("companions"), Mapping) else {}
    anilist = companions.get("anilist") if isinstance(companions.get("anilist"), Mapping) else None
    refresh = item.get("refresh") if isinstance(item.get("refresh"), Mapping) else {}
    if state not in MANGAPIXER_SERIES_STATES:
        # Not a series: keep only the label (CollectionAbout's record names the series it is about) - never a
        # number, volume list, completion or link from it.
        return SeriesKnowledge(source=SOURCE_MANGAPIXER, link_state=state,
                               mu_id=_s(record.get("externalId")) if record else None,
                               mu_url=_s(record.get("siteUrl")) if record else None,
                               title=_s(record.get("title")) if record else None)
    rec = record or {}
    return SeriesKnowledge(
        source=SOURCE_MANGAPIXER,
        link_state=state,
        mu_id=_s(rec.get("externalId")),
        mu_url=_s(rec.get("siteUrl")),
        title=_s(rec.get("title")),
        english_title=english_title,
        alt_titles=tuple(t for t in (_s(a) for a in rec.get("altTitles") or ()) if t),
        series_type=_s(rec.get("type")),
        origin_status=_s(rec.get("originStatus")),
        origin_volumes=to_decimal(rec.get("originVolumes")),
        completed_in_origin=rec.get("completedInOrigin") if isinstance(rec.get("completedInOrigin"), bool) else None,
        latest_chapter=exact_str(rec.get("latestChapter")),
        total_chapters=to_decimal(rec.get("totalChapters")),
        licensed_en=rec.get("licensedEn") if isinstance(rec.get("licensedEn"), bool) else None,
        english_publishers=_publishers_from_mangapixer(rec.get("englishPublishers")),
        translation_complete=(rec.get("translationComplete")
                              if isinstance(rec.get("translationComplete"), bool) else None),
        volumes=_volumes_from_mangapixer(item.get("volumes")),
        completion=_completion_from_mangapixer(item.get("completion")),
        official_links=_links_from_mangapixer(item.get("officialLinks")),
        anilist_id=anilist.get("id") if anilist else None,
        anilist_chapters=to_decimal(anilist.get("chapters")) if anilist else None,
        anilist_volumes=to_decimal(anilist.get("volumes")) if anilist else None,
        fetched_at=_s(rec.get("fetchedAt")) or _s(refresh.get("lastFetchedAt")),
        next_due_at=_s(refresh.get("nextDueAt")),
    )


# --- own matcher (MangaEntry + cached MangaUpdates / AniList data) -> knowledge ------------------


def _own_link_state(entry: Any) -> str:
    band = getattr(entry, "mu_band", None)
    if getattr(entry, "mu_id", None) is None:
        if band == "not_a_work":
            return LINK_NOT_A_WORK
        if band == "unmatched":
            return LINK_UNMATCHED
        return LINK_NOT_LOOKED_UP
    if getattr(entry, "mu_confirmed", False):
        return LINK_CONFIRMED
    if band == "review":
        return LINK_NEEDS_REVIEW
    return LINK_AUTO


def _publishers_from_mu(series_data: Mapping[str, Any]) -> Tuple[EnglishPublisher, ...]:
    from .mu_progress import parse_publisher_notes

    out: List[EnglishPublisher] = []
    for p in series_data.get("publishers") or ():
        if not isinstance(p, Mapping) or (p.get("type") or "").lower() != "english":
            continue
        name = _s(p.get("publisher_name"))
        if not name:
            continue
        ch, vol, status = parse_publisher_notes(p.get("notes") or "")
        pid = p.get("publisher_id")
        out.append(EnglishPublisher(name=name, volumes=to_decimal(vol), chapters=to_decimal(ch), status=status,
                                    publisher_id=pid if isinstance(pid, int) else None))
    return tuple(out)


def _anilist_links(anilist: Optional[Mapping[str, Any]]) -> Tuple[Mapping[str, Any], ...]:
    if not anilist:
        return ()
    rows = anilist.get("external_links")
    if rows is None:
        rows = anilist.get("externalLinks")
    return tuple(r for r in rows or () if isinstance(r, Mapping))


def from_own_matcher(entry: Any, mu_series_data: Optional[Mapping[str, Any]] = None,
                     anilist: Optional[Mapping[str, Any]] = None) -> SeriesKnowledge:
    """Knowledge from MangaList's own matcher: the MangaUpdates fields a MangaEntry carries (the
    cached ones), refined by the full MangaUpdates series record (*mu_series_data*: every English
    publisher, the origin status) and the AniList answer (*anilist*, ``anilist_client``'s normalised
    dict incl. ``external_links``) when the caller has them."""
    mu = mu_series_data if isinstance(mu_series_data, Mapping) else None
    pubs: Tuple[EnglishPublisher, ...] = _publishers_from_mu(mu) if mu else ()
    if not pubs and getattr(entry, "publisher_name", None):
        pubs = (EnglishPublisher(
            name=entry.publisher_name,
            volumes=to_decimal(entry.publisher_volumes),
            chapters=to_decimal(entry.publisher_chapters),
            status=entry.publisher_status,
        ),)
    elif not pubs and getattr(entry, "licensed", None) is True and (
            entry.publisher_volumes is not None or entry.publisher_chapters is not None):
        pubs = (EnglishPublisher(name="", volumes=to_decimal(entry.publisher_volumes),
                                 chapters=to_decimal(entry.publisher_chapters), status=entry.publisher_status),)
    latest = exact_str(mu.get("latest_chapter")) if mu and mu.get("latest_chapter") is not None else None
    if latest is None:
        latest = exact_str(getattr(entry, "scan_latest_chapter", None))
    completed = mu.get("completed") if mu and isinstance(mu.get("completed"), bool) else None
    if completed is None:
        completed = getattr(entry, "completed_in_origin", None)
    licensed = mu.get("licensed") if mu and isinstance(mu.get("licensed"), bool) else None
    if licensed is None:
        licensed = getattr(entry, "licensed", None)
    alt = tuple(getattr(entry, "mu_associated", None) or ())
    if mu and not alt:
        alt = tuple(t for t in (_s(a.get("title")) for a in mu.get("associated") or () if isinstance(a, Mapping)) if t)
    al_title = None
    if anilist:
        al_title = _s(anilist.get("english_title"))
    english = al_title or getattr(entry, "english_title", None)
    mu_id = getattr(entry, "mu_id", None)
    return SeriesKnowledge(
        source=SOURCE_OWN_MATCHER,
        link_state=_own_link_state(entry),
        mu_id=str(mu_id) if mu_id is not None else None,
        mu_url=getattr(entry, "mu_url", None) or (_s(mu.get("url")) if mu else None),
        title=getattr(entry, "mu_title", None) or (_s(mu.get("title")) if mu else None),
        english_title=english,
        alt_titles=alt,
        series_type=_s(mu.get("type")) if mu else None,
        origin_status=_s(mu.get("status")) if mu else None,
        completed_in_origin=completed,
        latest_chapter=latest,
        scan_latest_volume=to_decimal(getattr(entry, "scan_latest_volume", None)),
        licensed_en=licensed,
        english_publishers=pubs,
        anilist_id=(anilist.get("id") if anilist else None) or getattr(entry, "anilist_id", None),
        anilist_chapters=to_decimal(anilist.get("chapters") if anilist else getattr(entry, "anilist_chapters", None)),
        anilist_volumes=to_decimal(anilist.get("volumes") if anilist else getattr(entry, "anilist_volumes", None)),
        anilist_links=_anilist_links(anilist),
        # The series' total, so a finished series can be Complete (owner rule c): AniList states chapter
        # totals only for finished series, which is exactly when a total is needed.
        total_chapters=(to_decimal(anilist.get("chapters") if anilist else getattr(entry, "anilist_chapters", None))
                        if completed else None),
    )


def next_announced(volumes: Sequence[VolumeInfo], today: _dt.date) -> Optional[VolumeInfo]:
    """The first English volume announced for after *today* (the next one coming), or None."""
    upcoming = [v for v in volumes if v.released(today) is False]
    if not upcoming:
        return None
    return min(upcoming, key=lambda v: (parse_partial_date(v.english_date) or (9999,), to_decimal(v.volume) or 0))


def released_volumes(volumes: Iterable[VolumeInfo], today: _dt.date) -> List[VolumeInfo]:
    return [v for v in volumes if v.released(today) is True]
