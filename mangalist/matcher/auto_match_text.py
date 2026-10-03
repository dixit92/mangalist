"""Small name helpers shared by the detector, planner and scorer (port of MangaPixer 1.32.0
``AutoMatchText.cs``)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

import regex

from ._text import (
    any_letter,
    any_letter_or_digit,
    contains_ignore_case,
    eq_ignore_case,
    is_digit,
    is_letter,
    is_null_or_whitespace,
    nfkc,
    split_nonempty,
)
from .anatomy import is_release_tag
from .contracts import MetadataOrigin
from .normalizer import archive_base_title, scoring_form, split_unmatched_bracket_tags
from . import similarity

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
# 1.32.0: BD / European album tokens are volumes too (Tome / Tomo / Band / Deel / Album / Livre N, an upper-case T
# glued to the number).
_VOLUME_TOKEN = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:v|vol|vols|volume|volumes|tome|tomo|band|deel|album|livre)\.?\s*\d+"
    r"|(?-i:T)\d{1,3}(?![\p{L}\p{N}]))", _I)
# 1.32.0: "Issue 12" is a chapter too (like #12); "No. 12" is one only when nothing unit-like follows it
# (issue_number_of).
_CHAPTER_TOKEN = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:ch|chap|chapter|chapters|ep|episode)\.?\s*\d+|c\d+|#\s*\d+|issue\s*#?\s*\d+)", _I)

# "No. 12" / "N°12" after some title text ("No. 6" alone is a title) - an issue number only per issue_number_of.
_ISSUE_NO = regex.compile(
    r"(?<![\p{L}\p{N}])(?<=[\p{L}\p{N}][\s\-_.,]*)(?:no\.|n°)\s*(?P<n>\d{1,4}(?:\.\d{1,2})?)(?![\p{N}])", _I)

# "No. N" anywhere in a name (the folder rule: a folder's own "No. N" is part of its title).
_ANY_NO = regex.compile(r"(?<![\p{L}\p{N}])(?:no\.|n°)\s*(?P<n>\d{1,4})(?![\p{N}])", _I)

# A unit-like token after "No. N": a volume / chapter / episode / album token, c12, #12, T12, or a bare number.
_UNIT_LIKE_AFTER = regex.compile(
    r"(?<![\p{L}\p{N}.])(?:(?:v|vol|vols|volume|volumes|ch|chap|chapter|chapters|ep|episode|issue|tome|tomo|band"
    r"|deel|album|livre)\.?\s*#?\s*\d|c\d|#\s*\d|(?-i:T)\d|\d)", _I)

# A trailing "(disambiguator)" of a provider title: "Look Back (FUJIMOTO Tatsuki)", "Beyond (GYARO)".
_TRAILING_DISAMBIGUATOR = regex.compile(r"^(?P<head>.*\S)\s*\((?P<tag>[^()]{1,80})\)\s*$")

# Category folder words (1.27.0: the ONE category list): a folder named exactly one of these (whole name,
# case-insensitive) is the category hint of the folders below it. manga / manhwa / manhua / webtoon(s)
# also name an origin (origins_for_category); the hint only ever ADDS evidence. 1.32.0: the comics words
# (comic books, graphic novel(s), BD, bande(s) dessinee(s), fumetti, tebeos, historietas, stripboeken, US
# comics, European comics, eurocomics) - accents do not matter ("Bandes dessinées"). Not words on purpose
# (too ambiguous): strips, albums, webcomics.
CATEGORY_FOLDER_WORDS: Tuple[str, ...] = (
    "manga", "manhwa", "manhua", "webtoon", "webtoons", "comic", "comics", "doujin", "doujinshi",
    "comic books", "graphic novel", "graphic novels", "bd", "bande dessinee", "bandes dessinees", "fumetti", "tebeos",
    "historietas", "stripboeken", "us comics", "european comics", "eurocomics",
)

# Shelf words: generic sorting folders (status, format, "misc") that name no work and no creator. Never a
# category hint; with CATEGORY_FOLDER_WORDS they form the creator stop list ("Manga" exists as an author
# name on the provider side).
SHELF_WORDS: Tuple[str, ...] = (
    "one shots", "oneshots", "one shot", "oneshot", "anthology", "anthologies", "magazine", "magazines",
    "ongoing", "completed", "complete", "finished", "misc", "other", "others", "various", "unsorted",
    "new", "read", "unread", "hentai", "adult", "artbook", "artbooks", "novel", "novels", "light novels",
)

# Both subsets, compared by scoring form: a category or shelf word, never a creator.
_CATEGORY_WORDS = frozenset(scoring_form(w) for w in CATEGORY_FOLDER_WORDS + SHELF_WORDS)


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
    """A category or generic shelf word ("Manga", "Ongoing", "Doujinshi"), by scoring form."""
    return scoring_form(name) in _CATEGORY_WORDS


def is_category_folder_name(name: Optional[str]) -> bool:
    """True when a folder name, whole and trimmed, is a category folder word (1.27.0; accents ignored since
    1.32.0)."""
    if name is None:
        return False
    trimmed = name.strip()
    return (contains_ignore_case(CATEGORY_FOLDER_WORDS, trimmed)
            or contains_ignore_case(CATEGORY_FOLDER_WORDS, _without_accents(trimmed)))


def _without_accents(s: str) -> str:
    return unicodedata.normalize(
        "NFC", "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn"))


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
    evidence only. Since 1.27.0 plain separators are read too, in either order: ``Title by Author``,
    ``Title - Chapter | Author``, ``Author - Title`` (a name-like part of 1-4 words without digits next to
    the separator; a subtitle that looks like a name costs nothing either)."""
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

    # Plain separators, either order (1.27.0): "Title by Author", "Title - Chapter | Author", "Author - Title".
    for part in _separator_name_parts(rest):
        add(part)
    return tuple(hints) if any_letter(rest) else ()


_PIPE_SEPARATOR = regex.compile(r"\s*\|\s*")
_BY_SEPARATOR = regex.compile(r"\s+by\s+", _I)
_DASH_SEPARATOR = regex.compile(r"\s+[-–—]\s+")


def _is_name_like(text: Optional[str]) -> bool:
    """A plausible creator name next to a plain separator: 1-4 words, letters, no digits, not a category word."""
    t = text.strip() if text is not None else ""
    return (bool(t) and any_letter(t) and not any(is_digit(c) for c in t)
            and len(split_nonempty(t)) <= 4 and is_author_like(t, require_two_tokens=False)
            and _VOLUME_TOKEN.search(t) is None and _CHAPTER_TOKEN.search(t) is None)


def _separator_name_parts(rest: str) -> List[str]:
    """The name-like parts next to a pipe, a " by " or a spaced dash (the text after them, or the dash's
    first part)."""
    parts: List[str] = []
    pipe = _PIPE_SEPARATOR.split(rest)
    for part in pipe[1:]:
        if _is_name_like(part):
            parts.append(part.strip())
    head = pipe[0]
    by = list(_BY_SEPARATOR.finditer(head))
    if by:
        after = head[by[-1].end():]
        if _is_name_like(after):
            parts.append(after.strip())
    dash = _DASH_SEPARATOR.split(head)
    if len(dash) >= 2:
        if _is_name_like(dash[0]):
            parts.append(dash[0].strip())
        if _is_name_like(dash[-1]):
            parts.append(dash[-1].strip())
    return parts


def creator_split_titles(display_name: Optional[str]) -> Tuple[str, ...]:
    """The title part of a name whose author is written with a plain separator (1.27.0): the text before
    `` | Author`` or `` by Author``, and the text after ``Author - `` (a name-like first part). Empty when
    the name has none. Retrieval only (``QueryVariantKind.CREATOR_SPLIT``)."""
    if is_null_or_whitespace(display_name):
        return ()
    rest = _ARCHIVE_EXTENSION.sub("", nfkc(display_name).strip()).strip()
    rest = _bare(rest)
    result: List[str] = []

    def add(title: Optional[str]) -> None:
        t = title.strip() if title is not None else ""
        if t and sum(1 for c in t if is_letter(c)) >= 2 and not contains_ignore_case(result, t):
            result.append(t)

    pipe = _PIPE_SEPARATOR.split(rest)
    if len(pipe) >= 2 and any(_is_name_like(p) for p in pipe[1:]):
        add(pipe[0])
    head = pipe[0]
    by = list(_BY_SEPARATOR.finditer(head))
    if by and _is_name_like(head[by[-1].end():]):
        add(head[:by[-1].start()])
    # "Author - Title" only when no other form named the author, the first part is a name of 2+ words, and
    # real title text follows (not just "Chapter 012").
    dash = _DASH_SEPARATOR.search(head)
    if (not result and dash is not None and dash.start() > 0 and _is_name_like(head[:dash.start()])
            and len(split_nonempty(head[:dash.start()])) >= 2):
        after = _CHAPTER_TOKEN.sub(" ", _VOLUME_TOKEN.sub(" ", head[dash.end():]))
        if sum(1 for c in after if is_letter(c)) >= 2:
            add(head[dash.end():])
    return tuple(result)


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


def is_person_tag(tag: Optional[str]) -> bool:
    """A disambiguator that names a person the MangaUpdates way (1.30.0): at least two words, one of them an
    upper-case family name (``HATA Kenjiro``, ``JO Yongseok``) - not a format or edition note (``Webtoon``,
    ``Pre-serialization``, ``Novel``)."""
    return (is_author_like(tag, require_two_tokens=True)
            and any(len(w) >= 2 and all(unicodedata.category(c) == "Lu" for c in w)
                    for w in split_nonempty(tag)))


# Score factor of a title that matches only once its trailing "(disambiguator)" is removed and the tag is
# not known to name the record's own author (1.29.0): MangaUpdates adds the author to every same-named
# title ("Fly Me to the Moon (HATA Kenjiro)"), so the stripped alias is real evidence - but several works
# share the name, so on its own it stays below every automatic-link threshold.
DISAMBIGUATED_ALIAS_FACTOR = 0.88


def disambiguated_aliases(other_titles: Iterable[Optional[str]],
                          authors: Optional[Iterable[str]]) -> List[Tuple[str, float]]:
    """The stripped forms of a record's OTHER titles (alternative titles, a search hit's matched title) that
    carry a trailing ``(disambiguator)``, each with its score factor (1.29.0): 1 when the tag names one of
    the record's ``authors``, else :data:`DISAMBIGUATED_ALIAS_FACTOR` - a tag that names someone else, or
    authors not known yet (a search hit), never makes a clean 1.00."""
    known = [a for a in (authors or ()) if not is_null_or_whitespace(a)]
    result: List[Tuple[str, float]] = []
    for title in other_titles:
        bare = without_disambiguator(title)
        tag = disambiguator_tag(title)
        if bare is None or tag is None:
            continue
        factor = 1.0 if any(names_equal(a, tag) for a in known) else DISAMBIGUATED_ALIAS_FACTOR
        i = next((k for k, r in enumerate(result) if eq_ignore_case(r[0], bare)), -1)
        if i < 0:
            result.append((bare, factor))
        elif factor > result[i][1]:
            result[i] = (bare, factor)
    return result


def best_title_score(queries: Iterable[str], main_title: Optional[str], other_titles: Iterable[Optional[str]],
                     authors: Optional[Iterable[str]] = None) -> float:
    """The best title similarity of a record for display ranking (1.29.0): its main and other titles as
    written, the main title without its disambiguator, and the other titles' :func:`disambiguated_aliases`
    with their factors."""
    others = [t for t in other_titles if not is_null_or_whitespace(t)]
    plain: List[str] = []
    if not is_null_or_whitespace(main_title):
        plain.append(main_title)
    plain.extend(others)
    stripped_main = without_disambiguator(main_title)
    if stripped_main is not None:
        plain.append(stripped_main)
    query_list = list(queries)
    result = similarity.best(query_list, plain)
    for title, factor in disambiguated_aliases(others, authors):
        result = max(result, factor * similarity.best(query_list, [title]))
    return result


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
            and _CHAPTER_TOKEN.search(archive_name) is None and issue_number_of(archive_name) is None)


def is_chapter_like(archive_name: Optional[str]) -> bool:
    """An archive name that names a chapter: a chapter token, or a unit-named archive without a
    volume token (``001 [chapter title]``)."""
    if archive_name is None:
        return False
    if _CHAPTER_TOKEN.search(archive_name) or issue_number_of(archive_name) is not None:
        return True
    return (_VOLUME_TOKEN.search(archive_name) is None and archive_base_title(archive_name) == ""
            and any(is_digit(c) for c in archive_name))


# Unit numbers (1.27.0 count rule): the number after a volume / chapter token, the upper end of a range.
_VOLUME_NUMBER = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:v|vol|vols|volume|volumes|tome|tomo|band|deel|album|livre)\.?\s*"
    r"|(?-i:T)(?=\d{1,3}(?![\p{L}\p{N}])))(?P<n>\d{1,4})(?:\.\d+)?"
    r"(?:\s*-\s*(?P<m>\d{1,4})(?:\.\d+)?)?(?![\p{N}])", _I)
_CHAPTER_NUMBER = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:ch|chap|chapter|chapters|ep|episode)\.?\s*|c|#\s*|issue\s*#?\s*)(?P<n>\d{1,4})(?:\.\d+)?"
    r"(?:\s*-\s*(?P<m>\d{1,4})(?:\.\d+)?)?(?![\p{N}])", _I)
_LEADING_NUMBER = regex.compile(r"^\s*(?P<n>\d{1,4})(?:\.\d+)?(?![\p{N}])")


def volume_number_of(archive_name: Optional[str]) -> Optional[int]:
    """The highest volume number a volume-like archive name states (``Title v03`` -> 3, ``Vol. 01-05`` -> 5,
    ``v02.5`` -> 2, so an extra never inflates it), or None."""
    if archive_name is None or not is_volume_like(archive_name):
        return None
    return _highest_number(_VOLUME_NUMBER.finditer(archive_name))


def chapter_number_of(archive_name: Optional[str]) -> Optional[int]:
    """The highest chapter number a chapter-like archive name states (``Title - Chapter 012`` -> 12,
    ``c045.5`` -> 45, ``001 [Chapter Title]`` -> 1), or None. A leading 19xx / 20xx is a year."""
    if archive_name is None or not is_chapter_like(archive_name):
        return None
    n = _highest_number(_CHAPTER_NUMBER.finditer(archive_name))
    if n is not None:
        return n
    issue = issue_number_of(archive_name)
    if issue is not None:
        return int(issue.to_integral_value(rounding="ROUND_DOWN"))
    return _leading_number(archive_name)


def bare_number_of(archive_name: Optional[str]) -> Optional[int]:
    """The bare leading number of a name without a volume / chapter token and without a title (``01.cbz``,
    ``012 [Title]``) - the unit number of an archive inside a ``Volumes`` / ``Chapters`` subfolder - or None."""
    if (archive_name is None or _VOLUME_TOKEN.search(archive_name) or _CHAPTER_TOKEN.search(archive_name)
            or issue_number_of(archive_name) is not None or not is_chapter_like(archive_name)):
        return None
    return _leading_number(archive_name)


def _leading_number(archive_name: str) -> Optional[int]:
    bare = _bare(_ARCHIVE_EXTENSION.sub("", archive_name))
    m = _LEADING_NUMBER.search(bare)
    if m is None or _YEAR_ONLY.search(m.group("n")):
        return None
    return int(m.group("n"))


def _highest_number(matches) -> Optional[int]:
    best: Optional[int] = None
    for m in matches:
        value = int(m.group("m") if m.group("m") is not None else m.group("n"))
        if best is None or value > best:
            best = value
    return best


@dataclass(frozen=True)
class UnitNumbers:
    """The unit numbers one archive name states (1.29.0, :func:`units_of`): decimals kept (``c045.5`` ->
    45.5), both numbers of a name that states both (``v03 c012``), a range as start / end (``Vol. 01-05`` ->
    1 / 5; the end is None when the name states one number). ``is_extra``: the name's own unit is
    fractional - the chapter when it states one, otherwise the volume. All None: no unit number."""

    volume: Optional[Decimal] = None
    volume_end: Optional[Decimal] = None
    chapter: Optional[Decimal] = None
    chapter_end: Optional[Decimal] = None
    is_extra: bool = False

    @property
    def is_empty(self) -> bool:
        return self.volume is None and self.chapter is None


# Unit numbers v2 (1.29.0): decimals kept, a range as start / end (the end may repeat the token: "v01-v05").
# <t> is the token, so a bracketed single-letter token ("[v2]", a release revision) can be told apart.
_VOLUME_UNIT = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?P<t>volumes|volume|vols|vol|v|tome|tomo|band|deel|album|livre)\.?\s*"
    r"|(?P<t>(?-i:T))(?=\d{1,3}(?![\p{N}])))(?P<n>\d{1,4}(?:\.\d{1,2})?)"
    r"(?:\s*-\s*(?:(?:volumes|volume|vols|vol|v|tome|tomo|band|deel|album|livre)\.?\s*|(?-i:T))?"
    r"(?P<m>\d{1,4}(?:\.\d{1,2})?))?(?![\p{N}])", _I)
_CHAPTER_UNIT = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?P<t>chapters|chapter|chap|ch|episode|ep)\.?\s*|(?P<t>c)|(?P<t>#)\s*|(?P<t>issue)\s*#?\s*)"
    r"(?P<n>\d{1,4}(?:\.\d{1,2})?)(?:\s*-\s*(?:(?:chapters|chapter|chap|ch|episode|ep)\.?\s*|c|#\s*)?"
    r"(?P<m>\d{1,4}(?:\.\d{1,2})?))?(?![\p{N}])", _I)
_LEADING_UNIT = regex.compile(r"^\s*(?P<n>\d{1,4}(?:\.\d{1,2})?)(?:\s*-\s*(?P<m>\d{1,4}(?:\.\d{1,2})?))?(?![\p{N}])")

# Comics extras (1.32.0): Annual / FCBD (Free Comic Book Day) with or without a number, Special / One-Shot only with
# their own number ("Special #1"; "Special Edition" is an edition, a bare "Special" a title word).
_EXTRA_MARKER = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:annual|fcbd|free\s+comic\s+book\s+day)(?:\s*#?\s*(?P<n>\d{1,4}(?:\.\d{1,2})?))?"
    r"|(?:specials?|one-?shots?)\s*#?\s*(?P<n>\d{1,4}(?:\.\d{1,2})?))(?![\p{L}\p{N}])", _I)


def units_of(archive_name: Optional[str]) -> UnitNumbers:
    """Every unit number an archive name states (1.29.0): ``Title v03 c012`` -> volume 3, chapter 12;
    ``c045.5`` -> chapter 45.5, an extra; ``Vol. 01-05`` -> volumes 1 to 5; ``001 [Chapter Title]`` ->
    chapter 1 (a bare leading number of a name without a title; a leading 19xx / 20xx is a year). Tokens
    inside brackets are read only when the rest of the name states none, and then never a single-letter
    token (``[v2]`` is a release revision). A range whose end is a year is one number. 1.32.0 comics grammar:
    BD / European album tokens (``Tome 3``, ``T03``, ``Band 3``, ``Deel 3``) are volumes, ``Issue 12`` /
    ``No. 12`` chapters (like ``#12``), and an ``Annual`` / ``FCBD`` / ``Special #N`` / ``One-Shot N`` issue is
    an extra (``Saga Annual 2`` -> chapter 2, an extra)."""
    if is_null_or_whitespace(archive_name):
        return UnitNumbers()
    name = _ARCHIVE_EXTENSION.sub("", nfkc(archive_name).strip())
    outside = _bare(name)

    volume = _range_of(_VOLUME_UNIT.finditer(outside), allow_short_token=True)
    chapter = _range_of(_chapter_units(outside, volume is not None), allow_short_token=True)
    if chapter is None:
        issue = issue_number_of(archive_name)
        if issue is not None:
            chapter = (issue, None)
    if volume is None and chapter is None:
        # Only brackets name a unit ("Title (Vol. 3)"); a single letter there is a revision, not a unit.
        volume = _range_of(_VOLUME_UNIT.finditer(name), allow_short_token=False)
        chapter = _range_of(_chapter_units(name, volume is not None), allow_short_token=False)
    if volume is None and chapter is None and is_chapter_like(archive_name):
        lead = _LEADING_UNIT.search(outside)
        if lead is not None and not _YEAR_ONLY.search(lead.group("n")):
            chapter = _range_of([lead], allow_short_token=True)

    if chapter is not None:
        extra = chapter[0] != chapter[0].to_integral_value(rounding="ROUND_DOWN")
    else:
        extra = volume is not None and volume[0] != volume[0].to_integral_value(rounding="ROUND_DOWN")
    # A comics extra (1.32.0): "Saga Annual #2", "Saga Annual 2", "Saga Special #1" is chapter-like but never a
    # numbered issue - like a .5 chapter it is never missing and never fills a whole number. "FCBD 2019" names a
    # year, not a unit.
    marker = _EXTRA_MARKER.search(outside)
    if marker is not None:
        own = marker.group("n")
        if chapter is None and own is not None and not _YEAR_ONLY.search(own):
            chapter = (Decimal(own), None)
        extra = extra or chapter is not None
    return UnitNumbers(volume[0] if volume else None, volume[1] if volume else None,
                       chapter[0] if chapter else None, chapter[1] if chapter else None, extra)


def _chapter_units(text: str, states_volume: bool) -> List:
    """The chapter tokens of a name (1.31.1): "Episode N" / "Ep N" next to a VOLUME token names a part or arc
    of the series, not a chapter (each arc's volumes restart at 1), so it is not read as a chapter there;
    without a volume token it stays a chapter (webtoons: ``Episode 45``)."""
    return [m for m in _CHAPTER_UNIT.finditer(text)
            if not (states_volume and (m.group("t") or "").lower().startswith("ep"))]


def _range_of(matches: Iterable, allow_short_token: bool) -> Optional[Tuple[Decimal, Optional[Decimal]]]:
    """The lowest start and the highest end over the matches; the end is None when it is not above the start."""
    start: Optional[Decimal] = None
    end: Optional[Decimal] = None
    for m in matches:
        token = m.groupdict().get("t")
        if not allow_short_token and token is not None and len(token) == 1:
            continue
        n = Decimal(m.group("n"))
        high = n
        if m.group("m") is not None:
            e = Decimal(m.group("m"))
            # "Title v03 - 2019": a year, not the end of a range.
            if e > n and not (n < 1900 and _YEAR_ONLY.search(m.group("m"))):
                high = e
        start = n if start is None else min(start, n)
        end = high if end is None else max(end, high)
    if start is None:
        return None
    return start, (end if end is not None and end > start else None)


def issue_number_of(archive_name: Optional[str]) -> Optional[Decimal]:
    """The issue number a ``No. 12`` / ``N°12`` token states (1.32.0), or None. It is one only after some title
    text (``No. 6`` alone is a title) and only when nothing unit-like follows it outside brackets - no volume /
    chapter / episode / album token, no ``#12`` / ``c012``, no bare number: ``Monster No. 8 v01 c003`` and
    ``Robot No. 9 - Chapter 12`` carry ``No. N`` in their title (well-known manga do). A folder's own ``No. N``
    is handled by :func:`mask_folder_title_number`."""
    if is_null_or_whitespace(archive_name):
        return None
    outside = _bare(_ARCHIVE_EXTENSION.sub("", nfkc(archive_name).strip()))
    m = _ISSUE_NO.search(outside)
    if m is None or _UNIT_LIKE_AFTER.search(outside[m.end():]) is not None:
        return None
    return Decimal(m.group("n"))


def mask_folder_title_number(archive_name: str, folder_name: Optional[str]) -> str:
    """The archive name for unit parsing, with the FOLDER's own ``No. N`` masked (1.32.0): in a folder named
    ``Robot No. 9``, ``Robot No. 9.cbz`` names the work, not issue 9. The same number (leading zeros aside) is
    rewritten as a glued ``No9``, which no unit rule reads. Unchanged when the folder name has no ``No. N``."""
    if archive_name is None:
        raise TypeError("archive_name")
    if is_null_or_whitespace(folder_name):
        return archive_name
    numbers = {int(m.group("n")) for m in _ANY_NO.finditer(nfkc(folder_name))}
    if not numbers:
        return archive_name

    def mask(m) -> str:
        n = int(m.group("n"))
        return "No" + str(n) if n in numbers else m.group(0)

    return _ANY_NO.sub(mask, nfkc(archive_name))


def _build_category_origins() -> Dict[str, FrozenSet[MetadataOrigin]]:
    """The category words keyed by their SCORING form (1.32.0 fix): the hint is compared in scoring form, which
    folds long vowels ("webtoon" -> "webton"), so the words must be folded the same way - before 1.32.0 a
    Webtoon(s) folder never gave its origin."""
    table: Dict[str, FrozenSet[MetadataOrigin]] = {}

    def add(origins: Tuple[MetadataOrigin, ...], *words: str) -> None:
        for word in words:
            table[scoring_form(word)] = frozenset(origins)

    add((MetadataOrigin.Japan,), "manga", "japanese manga")
    add((MetadataOrigin.Korea,), "manhwa", "korean manhwa")
    add((MetadataOrigin.ChinaTaiwan,), "manhua", "chinese manhua")
    add((MetadataOrigin.Korea, MetadataOrigin.ChinaTaiwan), "webtoon", "webtoons")
    add((MetadataOrigin.French,), "bd", "bande dessinee", "bandes dessinees")
    add((MetadataOrigin.Spanish,), "tebeos", "historietas")
    add((MetadataOrigin.Italian,), "fumetti")
    add((MetadataOrigin.Dutch,), "stripboeken")
    add((MetadataOrigin.EnglishOriginal,), "us comics")
    return table


_CATEGORY_ORIGINS = _build_category_origins()


def origins_for_category(category_hint: Optional[str]) -> Optional[FrozenSet[MetadataOrigin]]:
    """The origins a category hint allows: ``manga`` -> Japan, ``manhwa`` -> Korea, ``manhua`` -> China/Taiwan,
    ``webtoon(s)`` -> Korea or China/Taiwan; 1.32.0 comics words by language: ``bd`` / ``bande(s) dessinee(s)``
    -> French (which covers Belgium), ``tebeos`` / ``historietas`` -> Spanish, ``fumetti`` -> Italian,
    ``stripboeken`` -> Dutch, ``us comics`` -> English-original (no US / UK split). None when the hint says
    nothing about origin (``comics``, ``graphic novels``, ``european comics``...)."""
    return _CATEGORY_ORIGINS.get(scoring_form(category_hint))


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
