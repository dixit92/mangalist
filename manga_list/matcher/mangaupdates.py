"""MangaUpdates v1 JSON -> :class:`MatchCandidate` (port of MangaPixer 1.31.1's production mapping:
``MangaUpdatesProvider`` / ``MangaUpdatesMapping`` / ``MangaUpdatesStatusParser`` / ``MetadataText`` and
``AutoMatchLookup.ToCandidate``).

Since MangaPixer 1.27.0 its golden set replays recorded answers through this production mapping, so the
port maps exactly what MangaPixer's matcher sees: text flattened like ``MetadataText.Line`` (Markdown,
HTML and entities removed), alt titles de-duplicated against the title, the status line's volume count and
chapter total (1.27.0; "8 Volumes | 40 Chapters", 1.30.1), the English publishers' regular-edition totals
(1.27.0 / 1.30.0), the webtoon vote (unknown without categories, False without the webtoon category) and
the related records.

``AUTO_SEARCH_FILTER`` is the fixed filter of the automatic search (MangaPixer design: doujinshi and
novels otherwise drown the real matches). Manual search (the "Fix match" picker) stays unfiltered.
"""

from __future__ import annotations

import html
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import regex

from ._text import contains_ignore_case, distinct_ignore_case, eq_ignore_case
from .contracts import CandidateRelation, MatchCandidate, MetadataFormat

PROVIDER = "mangaupdates"
AUTO_SEARCH_FILTER = ("Novel", "Doujinshi", "Artbook", "Drama CD")
SEARCH_PAGE_SIZE = 10

WEBTOON_CATEGORY = "Webtoon/Webcomic"
MIN_WEBTOON_VOTES = 5
MAX_STATUS_TEXT_LENGTH = 1024

_I = regex.IGNORECASE
_M = regex.MULTILINE


def auto_search_filter(doujinshi_allowed: bool = False) -> List[str]:
    return [t for t in AUTO_SEARCH_FILTER if not (doujinshi_allowed and t == "Doujinshi")]


@dataclass(frozen=True)
class SearchPage:
    """One page of ``/series/search``: the mapped hits and the provider's total hit count."""

    hits: Tuple[MatchCandidate, ...]
    total_hits: int


def map_search_page(response: Dict[str, Any]) -> SearchPage:
    """A ``/series/search`` response body (``{"total_hits": n, "results": [...]}``)."""
    hits = [h for h in (map_search_hit(r) for r in _array(response, "results")) if h is not None]
    total = response.get("total_hits") if isinstance(response, dict) else None
    total = int(total) if _is_number(total) else len(hits)
    return SearchPage(tuple(hits), max(total, len(hits)))


def map_search_hit(result: Dict[str, Any]) -> Optional[MatchCandidate]:
    """One ``/series/search`` result (``{"record": ..., "hit_title": ...}``); None when it has no usable
    id or title (the production provider skips it)."""
    record = result.get("record") if isinstance(result, dict) else None
    if not isinstance(record, dict):
        return None
    series_id = record.get("series_id")
    if not _is_number(series_id) or int(series_id) <= 0:
        return None
    title = line(_str(record, "title"), 512)
    if title is None:
        return None
    hit = line(_str(result, "hit_title"), 512)
    type_ = line(_str(record, "type"), 32)
    return MatchCandidate(
        provider=PROVIDER,
        external_id=str(int(series_id)),
        title=title,
        alt_titles=(hit,) if hit is not None and not eq_ignore_case(hit, title) else (),
        format=format_of(type_),
        origin=type_,
        start_year=_year(_str(record, "year")),
    )


def map_series(s: Dict[str, Any], requested_id: Optional[str] = None) -> MatchCandidate:
    """A full ``/series/{id}`` record."""
    series_id = s.get("series_id")
    external_id = str(int(series_id)) if _is_number(series_id) and int(series_id) > 0 else (requested_id or "")
    title = line(_str(s, "title"), 512) or ""

    alt: List[str] = []
    seen = [title]
    for a in _array(s, "associated"):
        if len(alt) >= 100:
            break
        t = line(_str(a, "title"), 512)
        if t is not None and not contains_ignore_case(seen, t):
            seen.append(t)
            alt.append(t)

    creators: List[Tuple[str, str]] = []
    for author in _array(s, "authors"):
        if len(creators) >= 50:
            break
        name = line(_str(author, "name"), 256)
        if name is None:
            continue
        role = {"author": "author", "artist": "artist"}.get((_str(author, "type") or "").strip().lower(), "other")
        if not any(r == role and eq_ignore_case(n, name) for n, r in creators):
            creators.append((name, role))

    english_volumes: Optional[int] = None
    english_chapters: Optional[int] = None
    stored_publishers = 0
    for p in _array(s, "publishers"):
        if eq_ignore_case((_str(p, "type") or "").strip(), "English"):
            # 1.30.0: the regular edition only (an omnibus count is not the original's numbering).
            volumes, chapters = parse_publisher_edition(_str(p, "notes"))
            english_volumes = _max(english_volumes, volumes)
            english_chapters = _max(english_chapters, chapters)
        if stored_publishers >= 30:
            break
        if line(_str(p, "publisher_name"), 256) is not None:
            stored_publishers += 1

    relations: List[CandidateRelation] = []
    for r in _array(s, "related_series"):
        if len(relations) >= 50:
            break
        rid = r.get("related_series_id") if isinstance(r, dict) else None
        if not _is_number(rid) or int(rid) <= 0:
            continue
        relation = line(_str(r, "relation_type"), 64)
        relations.append(CandidateRelation(str(int(rid)), relation.lower() if relation is not None else "related"))

    type_ = line(_str(s, "type"), 32)
    volumes, total_chapters = parse_status(_str(s, "status"))
    latest = s.get("latest_chapter")
    return MatchCandidate(
        provider=PROVIDER,
        external_id=external_id,
        title=title,
        alt_titles=tuple(alt),
        format=format_of(type_),
        origin=type_,
        start_year=_year(_str(s, "year")),
        volumes=volumes,
        latest_chapter=int(latest) if _is_number(latest) and latest > 0 else None,
        authors=tuple(distinct_ignore_case(n for n, _ in creators)),
        relations=tuple(relations),
        webtoon=webtoon_of(s.get("categories")),
        total_chapters=total_chapters,
        english_volumes=english_volumes,
        english_chapters=english_chapters,
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


def webtoon_of(categories: Any) -> Optional[bool]:
    """Tri-state webtoon flag: None when the record carries no categories (unknown) or the webtoon category
    has fewer than MIN_WEBTOON_VOTES net votes; False when the webtoon category is absent."""
    if not isinstance(categories, list) or not categories:
        return None
    webtoon = next((c for c in categories
                    if isinstance(c, dict) and eq_ignore_case((_str(c, "category") or "").strip(), WEBTOON_CATEGORY)), None)
    if webtoon is None:
        return False
    plus, minus = webtoon.get("votes_plus"), webtoon.get("votes_minus")
    if _is_number(plus) and _is_number(minus):
        net = int(plus) - int(minus)
    else:
        votes = webtoon.get("votes")
        net = int(votes) if _is_number(votes) else 0
    return True if net >= MIN_WEBTOON_VOTES else None


# ---------------------------------------------------------------------------
# Status line and publisher notes (MangaUpdatesStatusParser)
# ---------------------------------------------------------------------------

_VOLUME_LINE = regex.compile(r"(\d{1,5})\s*(?:Volumes?|Vols?\.?)\b[^()\n]*\(([^()\n]{1,40})\)", _I)
_CHAPTER_TOTAL_LINE = regex.compile(r"(?:^|[|/])[ \t]*(\d{1,5})[ \t]*Chapters?\b", _I | _M)


def parse_status(status: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """The volume count and the chapter total of a free-text ``status`` ("43 Volumes (Ongoing)", "200
    Chapters + Prologue (Complete)  \\n15 Volumes (Complete)"). The chapter total is the first line that
    STARTS with "N Chapters", or names it after a volume total on the same line ("8 Volumes | 40 Chapters",
    1.30.1); season lines such as "S1: 110 Chapters" are parts of it, never the total."""
    text = flatten(status, MAX_STATUS_TEXT_LENGTH)
    if text is None:
        return None, None
    volumes = None
    m = _VOLUME_LINE.search(text)
    if m is not None and m.group(1).isascii() and int(m.group(1)) > 0:
        volumes = int(m.group(1))
    chapters = None
    c = _CHAPTER_TOTAL_LINE.search(text)
    if c is not None and c.group(1).isascii() and int(c.group(1)) > 0:
        chapters = int(c.group(1))
    return volumes, chapters


# "86 Chapters", "13+2 Volumes" (a sum), "18 Physical Volumes" (up to two words between the number and the unit).
_NOTES_CHAPTERS = regex.compile(
    r"(?<![\p{L}\p{N}.+])(\d{1,5})(?:\s*\+\s*(\d{1,5}))?(?:\.\d+)?\s*(?:[\p{L}-]+\s+){0,2}chapters?\b", _I)
_NOTES_VOLUMES = regex.compile(
    r"(?<![\p{L}\p{N}.+])(\d{1,5})(?:\s*\+\s*(\d{1,5}))?(?:\.\d+)?\s*(?:[\p{L}-]+\s+){0,2}(?:volumes?|vols?)\b", _I)
# An edition whose volume numbering is not the original's.
_OMNIBUS_EDITION = regex.compile(r"omnibus|\b\d\s*-?\s*in\s*-?\s*1\b|perfect\s+edition|deluxe|big\s+edition", _I)


def parse_publisher_edition(notes: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """The volume and chapter totals of a publisher's REGULAR edition from its ``notes`` ("10 Volumes / 60
    Chapters; Ongoing", "13+2 Volumes; Complete", "18 Physical Volumes"; 1.27.0). A note may list several
    editions split by ``|`` or a line break; an omnibus, N-in-1, perfect or deluxe edition numbers its
    volumes differently, so its counts are ignored (1.30.0). The largest stated number of each, or None."""
    text = flatten(notes, MAX_STATUS_TEXT_LENGTH)
    if text is None:
        return None, None
    volumes = chapters = None
    for segment in (p.strip() for p in regex.split(r"[|\n]", text)):
        if not segment:
            continue
        if _OMNIBUS_EDITION.search(segment):
            continue
        v = _largest(_NOTES_VOLUMES.finditer(segment))
        c = _largest(_NOTES_CHAPTERS.finditer(segment))
        if v is not None and (volumes is None or v > volumes):
            volumes = v
        if c is not None and (chapters is None or c > chapters):
            chapters = c
    return volumes, chapters


def _largest(matches: Iterable) -> Optional[int]:
    best = None
    for m in matches:
        if not m.group(1).isascii():
            continue
        n = int(m.group(1))
        if m.group(2) is not None and m.group(2).isascii():
            n += int(m.group(2))
        if n > 0 and (best is None or n > best):
            best = n
    return best


# ---------------------------------------------------------------------------
# Provider text -> plain text (MetadataText.Flatten / Line)
# ---------------------------------------------------------------------------

_MARKDOWN_LINK = regex.compile(r"\[([^\[\]]*)\]\((?:[^()\s]|\([^()\s]*\))*\)")
_HTML_BREAK = regex.compile(r"<\s*br\s*/?\s*>", _I)
_HTML_TAG = regex.compile(r"<[^<>]{0,200}>")
_HEADING_MARKER = regex.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]+|$)", _M)
_QUOTE_MARKER = regex.compile(r"^[ \t]*(?:>[ \t]?)+", _M)
_UNDERSCORE_EMPHASIS = regex.compile(r"(?<![\p{L}\p{N}_])_(?=[^\s_])([^_\n]*?[^\s_])_(?![\p{L}\p{N}_])")
_TRAILING_SPACES = regex.compile(r"[ \t]+\n")
_EXTRA_BLANK_LINES = regex.compile(r"\n{3,}")
_REPEATED_SPACES = regex.compile(r"[ \t]{2,}")


def flatten(value: Optional[str], max_length: int) -> Optional[str]:
    """Plain text of at most ``max_length`` characters (Markdown links reduced to their label, headings,
    quote markers, emphasis, HTML tags and entities removed), or None when nothing is left."""
    if value is None or not value.strip():
        return None
    s = value.replace("\r\n", "\n").replace("\r", "\n")
    s = _MARKDOWN_LINK.sub(r"\1", s)
    s = _HTML_BREAK.sub("\n", s)
    s = _HTML_TAG.sub("", s)
    s = html.unescape(s)
    s = _HEADING_MARKER.sub("", s)
    s = _QUOTE_MARKER.sub("", s)
    s = s.replace("**", "").replace("__", "").replace("*", "")
    s = _UNDERSCORE_EMPHASIS.sub(r"\1", s)
    s = "".join(c for c in s if c in "\n\t" or unicodedata.category(c) != "Cc")
    s = _TRAILING_SPACES.sub("\n", s)
    s = _REPEATED_SPACES.sub(" ", s)
    s = _EXTRA_BLANK_LINES.sub("\n\n", s).strip()
    if not s:
        return None
    return s if len(s) <= max_length else s[:max_length].rstrip()


def line(value: Optional[str], max_length: int) -> Optional[str]:
    """One line of plain text (:func:`flatten`, line breaks and tabs as spaces), or None."""
    flat = flatten(value, 1 << 30)
    if flat is None:
        return None
    flat = _REPEATED_SPACES.sub(" ", flat.replace("\n", " ").replace("\t", " ")).strip()
    if not flat:
        return None
    return flat if len(flat) <= max_length else flat[:max_length].rstrip()


def _max(a: Optional[int], b: Optional[int]) -> Optional[int]:
    return b if a is None else a if b is None else max(a, b)


def _year(year: Optional[str]) -> Optional[int]:
    s = (year or "").strip()
    if not s or not s.isascii() or not s.isdigit():
        return None
    y = int(s)
    return y if 1800 <= y <= 2200 else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _str(obj: Any, name: str) -> Optional[str]:
    value = obj.get(name) if isinstance(obj, dict) else None
    return value if isinstance(value, str) else None


def _array(obj: Any, name: str) -> Iterable[Any]:
    value = obj.get(name) if isinstance(obj, dict) else None
    return value if isinstance(value, list) else ()
