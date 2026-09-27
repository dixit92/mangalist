"""MangaUpdates v1 JSON -> :class:`MatchCandidate` (the mapping MangaPixer's provider and its golden
fixtures use: title, alt titles, type, start year, volume count and chapter total from the status
line, latest chapter, authors, relations, webtoon vote).

``AUTO_SEARCH_FILTER`` is the fixed filter of the automatic search (MangaPixer design: doujinshi
and novels otherwise drown the real matches). Manual search (the "Fix match" picker) stays unfiltered.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import regex

from ._text import distinct_ignore_case, eq_ignore_case
from .contracts import CandidateRelation, MatchCandidate, MetadataFormat

PROVIDER = "mangaupdates"
AUTO_SEARCH_FILTER = ("Novel", "Doujinshi", "Artbook", "Drama CD")

_I = regex.IGNORECASE
_VOLUME_LINE = regex.compile(r"(\d{1,5})\s*(?:Volumes?|Vols?\.?)\b[^()\n]*\(", _I)
_CHAPTER_TOTAL = regex.compile(r"^\s*(\d{1,5})\s*Chapters?\b", _I)


def auto_search_filter(doujinshi_allowed: bool = False) -> List[str]:
    return [t for t in AUTO_SEARCH_FILTER if not (doujinshi_allowed and t == "Doujinshi")]


def map_search_hit(result: Dict[str, Any]) -> MatchCandidate:
    """One ``/series/search`` result (``{"record": ..., "hit_title": ...}``)."""
    record = result.get("record") or {}
    title = record.get("title") or ""
    hit = _str(result, "hit_title")
    type_ = _str(record, "type")
    return MatchCandidate(
        provider=PROVIDER,
        external_id=_id(record.get("series_id")),
        title=title,
        alt_titles=(hit,) if hit is not None and not eq_ignore_case(hit, title) else (),
        format=format_of(type_),
        origin=type_,
        start_year=_year(_str(record, "year")),
    )


def map_series(s: Dict[str, Any]) -> MatchCandidate:
    """A full ``/series/{id}`` record."""
    type_ = _str(s, "type")
    status = _str(s, "status") or ""
    volumes = None
    m = _VOLUME_LINE.search(status)
    if m and m.group(1).isascii() and int(m.group(1)) > 0:
        volumes = int(m.group(1))
    latest = s.get("latest_chapter")
    latest_chapter = int(latest) if _is_number(latest) and latest > 0 else None
    total = None
    t = _CHAPTER_TOTAL.search(status)
    if t and t.group(1).isascii() and int(t.group(1)) > 0:
        total = int(t.group(1))
    authors = distinct_ignore_case(n for n in (_str(a, "name") for a in _array(s, "authors")) if n is not None)
    relations = tuple(
        CandidateRelation(_id(r.get("related_series_id")), _str(r, "relation_type") or "")
        for r in _array(s, "related_series")
        if isinstance(r, dict) and r.get("related_series_id") is not None)
    webtoon: Optional[bool] = None
    for c in _array(s, "categories"):
        if _str(c, "category") == "Webtoon/Webcomic":
            webtoon = True if (int(c.get("votes_plus") or 0) - int(c.get("votes_minus") or 0)) >= 5 else None
    return MatchCandidate(
        provider=PROVIDER,
        external_id=_id(s.get("series_id")),
        title=s.get("title") or "",
        alt_titles=tuple(t for t in (_str(a, "title") for a in _array(s, "associated")) if t is not None),
        format=format_of(type_),
        origin=type_,
        start_year=_year(_str(s, "year")),
        volumes=volumes,
        latest_chapter=latest_chapter,
        authors=tuple(authors),
        relations=relations,
        webtoon=webtoon,
        total_chapters=total,
    )


def format_of(type_: Optional[str]) -> Optional[MetadataFormat]:
    key = (type_ or "").strip().lower()
    if not key:
        return None
    return {
        "novel": MetadataFormat.NOVEL,
        "artbook": MetadataFormat.ARTBOOK,
        "doujinshi": MetadataFormat.DOUJINSHI,
        "drama cd": MetadataFormat.AUDIO,
    }.get(key, MetadataFormat.COMIC)


def _year(year: Optional[str]) -> Optional[int]:
    s = (year or "").strip()
    if not s or not s.isascii() or not s.isdigit():
        return None
    y = int(s)
    return y if 1800 <= y <= 2200 else None


def _id(value: Any) -> str:
    if _is_number(value):
        return str(int(value))
    return value if isinstance(value, str) else ""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _str(obj: Any, name: str) -> Optional[str]:
    value = obj.get(name) if isinstance(obj, dict) else None
    return value if isinstance(value, str) else None


def _array(obj: Any, name: str) -> Iterable[Any]:
    value = obj.get(name) if isinstance(obj, dict) else None
    return value if isinstance(value, list) else ()
