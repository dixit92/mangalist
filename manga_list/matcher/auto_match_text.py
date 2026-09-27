"""Small name helpers shared by the detector, planner and scorer (port of ``AutoMatchText.cs``)."""

from __future__ import annotations

from typing import FrozenSet, Iterable, List, Optional, Tuple

import regex

from ._text import (
    any_letter,
    any_letter_or_digit,
    contains_ignore_case,
    is_digit,
    is_null_or_whitespace,
    nfkc,
    split_nonempty,
)
from .anatomy import is_release_tag
from .contracts import MetadataOrigin
from .normalizer import archive_base_title, scoring_form, split_unmatched_bracket_tags

_I = regex.IGNORECASE

# Unit subfolders: Volumes, Vol(s), Chapters, Ch, Extras, Specials, Side Stories, Oneshots, Bonus,
# Omake, Raw(s), Color(ed), Season(s) - optionally numbered or a range - a bare number or range, and
# Part / Arc / Book N WITHOUT a subtitle.
_UNIT_FOLDER = regex.compile(
    r"^(?:(?:volumes?|vols?|chapters?|chaps?|ch|extras?|specials?|side\s*stor(?:y|ies)|one-?shots?"
    r"|bonus(?:es)?|omake|raws?|colou?r(?:ed)?|seasons?)\.?(?:\s*\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)?"
    r"|(?:part|arc|book|season)\s*\.?\s*(?:\d+|[ivx]{1,4})|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)$", _I)

# Part / Arc / Book N followed by a subtitle: a separate work (numbered parts are separate records).
_PART_WITH_SUBTITLE = regex.compile(
    r"(?:^|\s)(?:part|arc|book)\s*\.?\s*(?:\d+|[ivx]{1,4})\s*(?:[-:~–—]\s*)?\p{L}", _I)

_BRACKET_GROUP = regex.compile(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}")
_YEAR_GROUP = regex.compile(r"[\(\[](19\d{2}|20\d{2})[\)\]]")
_VOLUME_TOKEN = regex.compile(r"(?<![\p{L}\p{N}])(?:v|vol|vols|volume|volumes)\.?\s*\d+", _I)
_CHAPTER_TOKEN = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:ch|chap|chapter|chapters|ep|episode)\.?\s*\d+|c\d+|#\s*\d+)", _I)

# A trailing "(disambiguator)" of a provider title: "Look Back (FUJIMOTO Tatsuki)", "Beyond (GYARO)".
_TRAILING_DISAMBIGUATOR = regex.compile(r"^(?P<head>.*\S)\s*\((?P<tag>[^()]{1,80})\)\s*$")

# Words that name a category or a generic shelf, never a creator ("Manga" exists as an author name on
# the provider side, so a naive author match needs this stop list).
_CATEGORY_WORDS = frozenset({
    "manga", "manhwa", "manhua", "webtoon", "webtoons", "comic", "comics", "doujin", "doujinshi",
    "one shots", "oneshots", "one shot", "oneshot", "anthology", "anthologies", "magazine", "magazines",
    "ongoing", "completed", "complete", "finished", "misc", "other", "others", "various", "unsorted",
    "new", "read", "unread", "hentai", "adult", "artbook", "artbooks", "novel", "novels", "light novels",
})


def is_unit_folder_name(name: Optional[str]) -> bool:
    """A unit subfolder (``Volumes``, ``Chapters 1-50``, ``Season 2``, ``Part 3``, ``12``)."""
    s = _bare(name)
    return bool(s) and _UNIT_FOLDER.search(s) is not None


def is_part_with_subtitle(name: Optional[str]) -> bool:
    """``Part|Arc|Book N`` followed by a subtitle (a separate work)."""
    return _PART_WITH_SUBTITLE.search(_bare(name)) is not None


def is_volume_folder_name(name: Optional[str]) -> bool:
    return is_unit_folder_name(name) and _bare(name)[:3].lower() == "vol"


def is_chapter_folder_name(name: Optional[str]) -> bool:
    return is_unit_folder_name(name) and _bare(name)[:2].lower() == "ch"


def is_category_word(name: Optional[str]) -> bool:
    """A category or generic shelf word ("Manga", "Ongoing", "Doujinshi")."""
    return scoring_form(name) in _CATEGORY_WORDS


def is_author_like(name: Optional[str], require_two_tokens: bool) -> bool:
    """A plausible creator name: at least two tokens (or one token of 4+ characters for a tag the
    archive names carry) and not a category word."""
    key = scoring_form(name)
    if len(key) < 2 or key in _CATEGORY_WORDS:
        return False
    tokens = len(split_nonempty(key))
    return tokens >= 2 if require_two_tokens else (tokens >= 2 or len(key) >= 4)


def names_equal(a: Optional[str], b: Optional[str]) -> bool:
    """Two creator names are the same: equal scoring forms, the same tokens in a different order
    ("Family Given" vs "Given Family"), or equal once spaces are removed."""
    x = scoring_form(a)
    y = scoring_form(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return (sorted(split_nonempty(x)) == sorted(split_nonempty(y))
            or x.replace(" ", "") == y.replace(" ", ""))


def contains_tokens(text: Optional[str], part: Optional[str]) -> bool:
    """``text`` contains ``part`` on token boundaries (scoring forms)."""
    t = scoring_form(text)
    p = scoring_form(part)
    return bool(p) and bool(t) and (" " + p + " ") in (" " + t + " ")


_ARCHIVE_EXTENSION = regex.compile(r"\.(?:cbz|zip|cbr|rar|cb7|7z|cbt|tar|pdf|epub)$", _I)
_YEAR_ONLY = regex.compile(r"^(?:19|20)\d{2}$")
_INNER_CIRCLE_ARTIST = regex.compile(r"^(?P<circle>[^()]*?)\s*\((?P<artist>[^()]+)\)\s*$")


def creator_hints(display_name: Optional[str]) -> Tuple[str, ...]:
    """Creator hints of a display name (1.26.1): the text of every bracket group anywhere in the name
    (``[Family Given] Title``, ``Title [Family Given]``, ``Title [English Title] (Family Given)``;
    ``[Circle (Artist)]`` gives both names), and of unmatched brackets (``Family Given] Title``, a
    YACReader jump-bar convention, and ``Title [Family Given``). Years, release tags, unit markers and
    groups without letters are skipped; a name that is nothing but tags gives none. The scorer only
    uses a hint when a record's authors (or its ``(AUTHOR Name)`` disambiguator) name it - positive
    evidence only. Plain separators (``Author - Title``) are not read."""
    if is_null_or_whitespace(display_name):
        return ()
    rest = _ARCHIVE_EXTENSION.sub("", nfkc(display_name).strip()).strip()
    hints: List[str] = []

    def add(text: Optional[str]) -> None:
        text = text.strip() if text is not None else None
        if (not text or not any_letter(text) or _YEAR_ONLY.search(text) or is_release_tag(text)
                or _VOLUME_TOKEN.search(text) or _CHAPTER_TOKEN.search(text)
                or len(split_nonempty(text)) > 5 or contains_ignore_case(hints, text)):
            return
        hints.append(text)

    for _ in range(4):
        groups = list(_BRACKET_GROUP.finditer(rest))
        if not groups:
            break
        for g in groups:
            inner = g.group(0)[1:-1]
            ca = _INNER_CIRCLE_ARTIST.search(inner)
            if ca:
                add(ca.group("circle"))  # "[Circle (Artist)]" gives both names
                add(ca.group("artist"))
            else:
                add(inner)
        rest = _BRACKET_GROUP.sub(" ", rest)
    rest, leading, trailing = split_unmatched_bracket_tags(rest)
    add(leading)
    add(trailing)
    return tuple(hints) if any_letter(rest) else ()


def disambiguator_tag(title: Optional[str]) -> Optional[str]:
    """The trailing ``(disambiguator)`` of a provider title (``Sprite (OOBA Douzu)`` -> ``OOBA Douzu``),
    or None."""
    if is_null_or_whitespace(title):
        return None
    m = _TRAILING_DISAMBIGUATOR.search(title)
    if m and any_letter(m.group("tag")) and any_letter_or_digit(m.group("head")):
        return m.group("tag").strip()
    return None


def without_disambiguator(title: Optional[str]) -> Optional[str]:
    """A provider title without its trailing disambiguator (``Look Back (FUJIMOTO Tatsuki)`` ->
    ``Look Back``); None when there is none."""
    if is_null_or_whitespace(title):
        return None
    m = _TRAILING_DISAMBIGUATOR.search(title)
    if m and any_letter_or_digit(m.group("tag")) and any_letter_or_digit(m.group("head")):
        return m.group("head").strip()
    return None


def earliest_year(names: Iterable[Optional[str]]) -> Optional[int]:
    """The earliest ``(19xx|20xx)`` / ``[19xx|20xx]`` year in the names, or None."""
    result: Optional[int] = None
    for name in names:
        for m in _YEAR_GROUP.finditer(name or ""):
            y = int(m.group(1))
            if result is None or y < result:
                result = y
    return result


def is_volume_like(archive_name: Optional[str]) -> bool:
    """An archive name that names a volume (a volume token and no chapter token)."""
    return (archive_name is not None and _VOLUME_TOKEN.search(archive_name) is not None
            and _CHAPTER_TOKEN.search(archive_name) is None)


def is_chapter_like(archive_name: Optional[str]) -> bool:
    """An archive name that names a chapter: a chapter token, or a unit-named archive without a
    volume token (``001 [chapter title]``)."""
    if archive_name is None:
        return False
    if _CHAPTER_TOKEN.search(archive_name):
        return True
    return (_VOLUME_TOKEN.search(archive_name) is None and archive_base_title(archive_name) == ""
            and any(is_digit(c) for c in archive_name))


_JAPAN = frozenset({MetadataOrigin.Japan})
_KOREA = frozenset({MetadataOrigin.Korea})
_CHINA = frozenset({MetadataOrigin.ChinaTaiwan})
_WEBTOON = frozenset({MetadataOrigin.Korea, MetadataOrigin.ChinaTaiwan})


def origins_for_category(category_hint: Optional[str]) -> Optional[FrozenSet[MetadataOrigin]]:
    """The origins a category hint allows (``manga`` -> Japan, ``manhwa`` -> Korea, ``manhua`` ->
    China/Taiwan, ``webtoon(s)`` -> Korea or China/Taiwan); None when it says nothing about origin."""
    key = scoring_form(category_hint)
    if key in ("manga", "japanese manga"):
        return _JAPAN
    if key in ("manhwa", "korean manhwa"):
        return _KOREA
    if key in ("manhua", "chinese manhua"):
        return _CHINA
    if key in ("webtoon", "webtoons"):
        return _WEBTOON
    return None


_ORIGIN_BY_NAME = {o.name.lower(): o for o in MetadataOrigin}
_ORIGIN_BY_TYPE = {
    "manga": MetadataOrigin.Japan,
    "manhwa": MetadataOrigin.Korea,
    "manhua": MetadataOrigin.ChinaTaiwan,
    "oel": MetadataOrigin.EnglishOriginal,
}


def parse_origin(origin: Optional[str]) -> Optional[MetadataOrigin]:
    """A provider type ("Manga", "Manhwa", "Manhua", "OEL") or a ``MetadataOrigin`` name; None when
    unknown or a format word."""
    if is_null_or_whitespace(origin):
        return None
    s = origin.strip()
    by_name = _ORIGIN_BY_NAME.get(s.lower())
    if by_name is not None:
        return by_name
    return _ORIGIN_BY_TYPE.get(s.lower())


def _bare(name: Optional[str]) -> str:
    if is_null_or_whitespace(name):
        return ""
    s = nfkc(name)
    for _ in range(3):
        nxt = _BRACKET_GROUP.sub(" ", s)
        if nxt == s:
            break
        s = nxt
    return " ".join(split_nonempty(s)).strip(" -_.")
