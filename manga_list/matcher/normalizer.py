"""Title normalization (port of MangaPixer 1.32.0 ``TitleNormalizer.cs``).

Turns a folder or archive display name into clean title query variants plus hints. Pure and
deterministic; it only ever sees a display name, never the filesystem.

Pipeline for :func:`normalize`: NFKC (full-width to ASCII) -> strip a known archive extension ->
``_`` and ``.`` become spaces when the name has no spaces -> bracketed tags ``[...]``, ``(...)``,
``{...}`` are removed, EXCEPT a non-leading, trailing ``[English Title]`` of at least two words (a
second query variant, the Manga-List folder convention) and a ``(19xx|20xx)`` year (a year hint) ->
volume and chapter tokens are removed, edition words and phrases ("Master Edition", "Kanzenban")
are removed but kept as hints -> whitespace collapsed, edge punctuation trimmed.

Stage-2 helpers never change :attr:`NormalizedTitle.primary`: derived retrieval variants
(:func:`derived_variants`), sequel / part numbers (:func:`number_tokens`), the base title of an
archive name (:func:`archive_base_title`, :func:`archive_title`) and the head of numbered chapters
with subtitles (:func:`numbered_series_head`, 1.26.1), record-title subtitle heads and tails
(:func:`subtitle_head`, :func:`subtitle_tail`, 1.27.0 / 1.30.0) and the subtitle a name states
(:func:`name_subtitle`, 1.30.0). An unmatched bracket marks a tag on its outer side
(``Family Given] Title``, 1.26.1).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum
from functools import lru_cache
from typing import Iterable, List, Optional, Sequence, Tuple

import regex

from ._text import (
    any_letter,
    any_letter_or_digit,
    contains_ignore_case,
    distinct_ignore_case,
    eq_ignore_case,
    is_letter,
    is_letter_or_digit,
    is_null_or_whitespace,
    lower_invariant,
    nfkc,
)

_I = regex.IGNORECASE

_ARCHIVE_EXTENSIONS = (".cbz", ".zip", ".cbr", ".rar", ".cb7", ".7z", ".cbt", ".tar", ".pdf", ".epub")

_EDITION_WORDS = ("Omnibus", "Deluxe", "Complete", "Digital")
_EDITION_WORD_PATTERNS = {
    w: regex.compile(r"(?<![\p{L}\p{N}])" + w + r"(?![\p{L}\p{N}])", _I) for w in _EDITION_WORDS
}

# Named re-releases, removed before the single words above so "Complete Edition" goes as one phrase.
_EDITION_PHRASE = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:Master|Perfect|Deluxe|Collector'?s|Special|Anniversary|Complete|Definitive"
    r"|Ultimate|Legendary|Remastered|Library|Full[- ]Colou?r)\s+Edition|Kanzenban|Shinsou?ban|Aizou?ban|Bunkoban"
    r"|Wideban)(?![\p{L}\p{N}])", _I)

# Comics collected-format words (1.32.0): removed from the title and kept as hints, like the edition words. TPB / OGN
# and the French / German / Dutch "complete edition" words in any case; HC / GN only upper-case (two letters are too
# common). Absolute / Compendium are not removed: they are title words too ("Absolute Boyfriend").
_FORMAT_WORD = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?i:tpb|ogn|(?:l')?int[eé]grale|gesamtausgabe|integraal)|HC|GN)(?![\p{L}\p{N}])")

_SQUARE_GROUP = regex.compile(r"\[([^\[\]]*)\]")
_ANY_BRACKET_GROUP = regex.compile(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}")
_YEAR_GROUP = regex.compile(r"\((19\d{2}|20\d{2})\)")

# v01, v.1, vol 3, Vol. 3, Volume 1-5, volumes 2 - 4; 1.32.0 BD / European albums: Tome 3, Tomo 3, Band 3, Deel 3,
# Album 3, Livre 3, T03 (an upper-case T glued to the number).
_VOLUME_TOKEN = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:v|vol|vols|volume|volumes|tome|tomo|band|deel|album|livre)\.?\s*"
    r"|(?-i:T)(?=\d{1,3}(?![\p{L}\p{N}])))\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?(?![\p{L}\p{N}])", _I)

# Ch 12, ch.12, chap 3, Chapter 10.5, chapters 1-20, c003 (bare c only when glued to digits); 1.32.0 comic issues:
# Issue 12. "No. 12" is never removed: titles carry it ("Kaiju No. 8", "No. 6").
_CHAPTER_TOKEN = regex.compile(
    r"(?<![\p{L}\p{N}])(?:(?:ch|chap|chapter|chapters|issue\s*#?)\.?\s*\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?"
    r"|c\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)(?![\p{L}\p{N}])", _I)

_HASH_NUMBER = regex.compile(r"#\s*\d+(?:\.\d+)?")
_BARE_UNIT_WORD = regex.compile(r"(?<![\p{L}\p{N}])(?:chapter|chapters|volume|volumes)(?![\p{L}\p{N}])", _I)
_WHITESPACE = regex.compile(r"\s+")
_STRAY_BRACKET = regex.compile(r"[\[\](){}]")

# Subtitle separator: " - ", " – ", " — ", ": " (NFKC folds the full-width colon) or a tilde with or
# without spaces around it (1.27.0: English light-novel titles write "Title ~Subtitle~"; NFKC folds the
# wave dash).
_SUBTITLE_SEPARATOR = regex.compile(r"\s+[-–—]\s+|\s*[~\u301C]\s*|:\s+")

# The break before a record title's subtitle: any colon ("Title: Sub", "Title:re"), a tilde, or a
# spaced dash (1.27.0).
_SUBTITLE_BREAK = regex.compile(r":|\s*[~\u301C]|\s+[-–—]\s+")

# A sequel / part number: "Part 3", "Season 2", "Book II", "Arc 4", "Phase 2", "Stage 3", or a bare
# number / roman numeral II-X standing alone before the end or a subtitle separator (a tilde counts
# with or without a space after it: "Title 99 ~Subtitle~", 1.27.0). Four-digit numbers are years,
# never sequel numbers. (The group name "num" is used twice, as in the .NET pattern; the `regex`
# module allows that, `re` does not.)
_SEQUEL_NUMBER = regex.compile(
    r"(?<![\p{L}\p{N}/])(?:(?P<unit>part|season|book|arc|phase|stage)\s*\.?\s*(?P<num>\d{1,3}|[ivx]{1,4})"
    r"|(?P<num>\d{1,3}(?:\.\d)?|ii|iii|iv|v|vi|vii|viii|ix|x))"
    r"(?=\s*$|\s+[-–—]\s+|\s*[~\u301C]|:\s+|\s*[-–—:]\s*$)", _I)

# A leading unit number of an archive name: "001 - Title", "01. Title", "12) Title".
_LEADING_UNIT_NUMBER = regex.compile(r"^\d+(?:\.\d+)?\s*(?:[-.:)–—]\s*|$)")

# Trailing number run of an archive base title: " 01", " 1.5", " 01-03", " - 012", "_07".
_TRAILING_NUMBERS = regex.compile(r"(?:[\s\-_.#–—]+\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)+$")

_NO_LETTERS = regex.compile(r"^[\p{P}\p{S}\p{N}\s]*$")

# A standalone chapter-like number inside a name: "Title 025 Subtitle", "Title 012.5 Subtitle".
_STANDALONE_NUMBER = regex.compile(r"(?<![\p{L}\p{N}.])\d{1,4}(?:\.\d+)?(?![\p{L}\p{N}])")

# One parenthesized group after a trailing [English Title]: "(Family Given)".
_LONE_PAREN_GROUP = regex.compile(r"^\(([^()\[\]]{2,60})\)$")

_TRIM_CHARS = " -_.,:;~!|/+='\""
_TRIM_CHARS_NO_BANG = " -_.,:;~|/+='\""
_TRIM_EDGE_CHARS = " -_.,:;~!|/+='\"–—"

_ROMAN_NUMERALS = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")
_APOSTROPHES = frozenset("'‘’ʼ`´")
_BRACKET_CHARS = frozenset("[](){}")


class DerivedTitleKind(IntEnum):
    SUBTITLE_SPLIT = 0       # the text before a subtitle separator ("Title Words - Subtitle")
    SEQUEL_NUMBER_SPLIT = 1  # the title without its sequel / part number ("Title 2", "Title Part 3")


@dataclass(frozen=True)
class DerivedTitle:
    text: str
    kind: DerivedTitleKind


@dataclass(frozen=True)
class NormalizedTitle:
    """The primary query, all query variants (primary first, then a bracketed English title), an
    optional year hint, the edition words removed, retrieval-only derived variants, and the primary
    with the trailing ``!`` the name had (``BLAME!``) - a second search text, ignored by scoring."""

    primary: str
    variants: Tuple[str, ...]
    year_hint: Optional[int]
    edition_hints: Tuple[str, ...]
    derived: Tuple[DerivedTitle, ...] = field(default=())
    primary_with_exclamation: Optional[str] = None


_EMPTY = NormalizedTitle("", (), None, ())


def normalize(display_name: Optional[str]) -> NormalizedTitle:
    """Normalize a display name into query variants and hints. An empty or all-tag input yields an
    empty primary; never raises."""
    if is_null_or_whitespace(display_name):
        return _EMPTY
    return _normalize_cached(display_name)


@lru_cache(maxsize=8192)
def _normalize_cached(display_name: str) -> NormalizedTitle:
    s = nfkc(display_name).strip()
    s = _strip_archive_extension(s)

    if " " not in s:
        s = s.replace("_", " ").replace(".", " ")

    # (a) a non-leading, TRAILING [English Title] of >= 2 words becomes a second variant. Only the
    # last bracket group qualifies (a year group after it is allowed): a [Group] tag followed by
    # further tags, or nested inside another group, is a scanlation group, never a title.
    english_variant: Optional[str] = None
    squares = list(_SQUARE_GROUP.finditer(s))
    last = squares[-1] if squares else None
    if last is not None and last.start() > 0:  # a leading [Group] tag is never a title
        after = s[last.end():]
        inner = last.group(1).strip()
        after_no_year = _YEAR_GROUP.sub(" ", after).strip()
        tags_after = any(c in _BRACKET_CHARS for c in after_no_year)
        # "Title [English Title] (Family Given)": one creator-looking group after the English title
        # does not make it a scanlation tag (1.26.1). Release tags still do.
        if tags_after:
            from .anatomy import is_release_tag  # anatomy imports this module

            paren = _LONE_PAREN_GROUP.search(after_no_year)
            if paren and any_letter(paren.group(1)) and not is_release_tag(paren.group(1).strip()):
                tags_after = False
        if not tags_after and _count_words(inner) >= 2 and any_letter(inner):
            english_variant = inner

    # (b) a (19xx|20xx) year becomes a year hint.
    year_hint: Optional[int] = None
    year_match = _YEAR_GROUP.search(s)
    if year_match:
        year_hint = int(year_match.group(1))

    s = _remove_bracket_groups(s)

    edition_hints: List[str] = []
    primary = _clean_title(s, edition_hints)
    primary_as_written = _clean_title(s, [], keep_exclamation=True)
    variants: List[str] = []
    if primary:
        variants.append(primary)
    if english_variant is not None:
        english = _clean_title(english_variant, edition_hints)
        if english and not contains_ignore_case(variants, english):
            variants.append(english)

    derived: List[DerivedTitle] = []
    for variant in variants:
        for d in derived_variants(variant):
            if not contains_ignore_case(variants, d.text) and not any(eq_ignore_case(x.text, d.text) for x in derived):
                derived.append(d)

    return NormalizedTitle(
        primary=variants[0] if variants else "",
        variants=tuple(variants),
        year_hint=year_hint,
        edition_hints=tuple(distinct_ignore_case(edition_hints)),
        derived=tuple(derived),
        primary_with_exclamation=primary_as_written if primary and primary_as_written.endswith("!") else None,
    )


def derived_variants(title: Optional[str]) -> List[DerivedTitle]:
    """Retrieval-only variants derived from a clean title: the text before a subtitle separator when
    at least two words precede it, and the title without its sequel / part number. A derived variant
    finds the franchise record; scoring still penalizes the number it dropped."""
    result: List[DerivedTitle] = []
    if is_null_or_whitespace(title):
        return result
    t = _WHITESPACE.sub(" ", nfkc(title)).strip()

    sep = _SUBTITLE_SEPARATOR.search(t)
    if sep and sep.start() > 0:
        head = _trim_edges(t[:sep.start()])
        tail = _trim_edges(t[sep.end():])
        if _count_words(head) >= 2 and tail:
            result.append(DerivedTitle(head, DerivedTitleKind.SUBTITLE_SPLIT))

    number = _SEQUEL_NUMBER.search(t)
    if number and number.start() > 0:
        head = _trim_edges(t[:number.start()])
        if len(head) >= 3 and any_letter(head) and not any(eq_ignore_case(r.text, head) for r in result):
            result.append(DerivedTitle(head, DerivedTitleKind.SEQUEL_NUMBER_SPLIT))

    return result


def number_tokens(title: Optional[str]) -> Tuple[str, ...]:
    """The sequel / part numbers of a title, normalized and sorted (``Title 2`` -> ``("2",)``,
    ``Title Part III: Sub`` -> ``("3",)``). Leading numbers (``20th Century Boys``), numbers inside a
    name (``Ranma 1/2``) and years are not sequel numbers."""
    if is_null_or_whitespace(title):
        return ()
    return _number_tokens_cached(title)


@lru_cache(maxsize=8192)
def _number_tokens_cached(title: str) -> Tuple[str, ...]:
    t = _WHITESPACE.sub(" ", nfkc(title)).strip()
    result: List[str] = []
    for m in _SEQUEL_NUMBER.finditer(t):
        if m.start() == 0 and m.group("unit") is None:
            continue  # a leading bare number is part of the name
        n = _normalize_number(m.group("num"))
        if n is not None:
            result.append(n)
    result.sort()
    return tuple(result)


def subtitle_head(title: Optional[str]) -> Optional[str]:
    """The text before a record title's subtitle break (1.27.0): the first colon (``Title: Sub``,
    ``Title:re``), tilde (``Title ~Sub~``, ``Title~Sub~``) or spaced dash (``Title - Sub``). None when the
    title has no break, or nothing with a letter precedes it."""
    if is_null_or_whitespace(title):
        return None
    t = nfkc(title)
    m = _SUBTITLE_BREAK.search(t)
    if m is None or m.start() == 0:
        return None
    head = _trim_edges(t[:m.start()])
    return head if any_letter(head) and _trim_edges(t[m.end():]) else None


def name_subtitle(name: Optional[str]) -> Optional[str]:
    """The subtitle a NAME states after the separator its subtitle-split variant cuts at
    (``Title Words - Subtitle`` -> ``Subtitle``, 1.30.0); None when :func:`derived_variants` derives no
    subtitle split."""
    if is_null_or_whitespace(name):
        return None
    t = _WHITESPACE.sub(" ", nfkc(name)).strip()
    sep = _SUBTITLE_SEPARATOR.search(t)
    if sep is None or sep.start() == 0:
        return None
    head = _trim_edges(t[:sep.start()])
    tail = _trim_edges(t[sep.end():])
    return tail if _count_words(head) >= 2 and any_letter_or_digit(tail) else None


def subtitle_tail(title: Optional[str]) -> Optional[str]:
    """The subtitle of a RECORD title after its :func:`subtitle_head` break (``Title: Sub``,
    ``Title ~Sub~``, ``Title - Sub`` -> ``Sub``, 1.30.0); None when the title has no break."""
    if subtitle_head(title) is None:
        return None
    t = nfkc(title)
    m = _SUBTITLE_BREAK.search(t)
    tail = _trim_edges(t[m.end():]).rstrip("~\u301C ")
    return tail or None


def contains_number(text: Optional[str], number: str) -> bool:
    """True when ``number`` (a normalized :func:`number_tokens` value such as ``99``) stands as a whole
    number anywhere in ``text`` (``Title Level 99 ~Sub~``, ``Level 099``)."""
    if is_null_or_whitespace(text):
        return False
    return any(_normalize_number(m.group(0)) == number for m in _STANDALONE_NUMBER.finditer(nfkc(text)))


def archive_base_title(archive_name: Optional[str]) -> str:
    """The base title of an archive file name: bracket groups removed, cut before the first volume /
    chapter / ``#`` token, trailing numbers stripped (``Title 03`` -> ``Title``, so a numbered
    mini-series shares one base). Empty for a unit-named archive (``001 [chapter title]``,
    ``Vol 01``, ``01 - Subtitle``)."""
    if is_null_or_whitespace(archive_name):
        return ""
    return _archive_base_title_cached(archive_name)


@lru_cache(maxsize=16384)
def _archive_base_title_cached(archive_name: str) -> str:
    s = nfkc(archive_name).strip()
    s = _strip_archive_extension(s)
    if " " not in s:
        s = s.replace("_", " ").replace(".", " ")
    s = _WHITESPACE.sub(" ", _remove_bracket_groups(s)).strip()

    if _LEADING_UNIT_NUMBER.search(s):
        return ""

    cut = len(s)
    for token in (_VOLUME_TOKEN.search(s), _CHAPTER_TOKEN.search(s), _HASH_NUMBER.search(s)):
        if token and token.start() < cut:
            cut = token.start()
    s = s[:cut]

    s = _clean_title(s, [])
    s = _trim_edges(_TRAILING_NUMBERS.sub("", s))
    return "" if _NO_LETTERS.search(s) else s


def numbered_series_head(archive_names: Sequence[str], min_share: float) -> Optional[str]:
    """The title that numbered chapters of one work share in front of their number, when the names
    also carry a per-chapter subtitle (``Title 025 Subtitle``, ``Title 000 Oneshot``): the longest
    head that at least ``min_share`` of the archives have in front of a number that VARIES between
    them. None when no head qualifies. Bracket groups are ignored."""
    if len(archive_names) < 2:
        return None
    heads: dict = {}  # key -> [spellings, numbers seen, archives carrying it]
    for name in archive_names:
        s = nfkc(name or "").strip()
        s = _strip_archive_extension(s)
        if " " not in s:
            s = s.replace("_", " ").replace(".", " ")
        s = _WHITESPACE.sub(" ", _remove_bracket_groups(s)).strip()
        seen = set()
        for number in _STANDALONE_NUMBER.finditer(s):
            head = _trim_edges(s[:number.start()])
            key = scoring_form(head)
            if not key or _NO_LETTERS.search(head) or key in seen:
                continue
            seen.add(key)
            entry = heads.setdefault(key, [[], set(), 0])
            entry[0].append(head)
            entry[1].add(_normalize_number(number.group(0)) or number.group(0))
            entry[2] += 1
    qualifying = [(key, e) for key, e in heads.items()
                  if len(e[1]) >= 2 and e[2] / len(archive_names) >= min_share]
    if not qualifying:
        return None
    _, best = min(qualifying, key=lambda kv: (-kv[1][2], -len(kv[0]), kv[0]))
    return min(best[0])


def archive_title(archive_names: Iterable[str]) -> Optional[str]:
    """The dominant archive-derived title of a folder: the base title that at least half of the
    archives share (compared by scoring form), or None. Deterministic: the ordinal-first spelling of
    the winning base is returned."""
    total = 0
    groups: dict = {}
    for name in archive_names:
        total += 1
        base = archive_base_title(name)
        key = scoring_form(base)
        if not key:
            continue
        groups.setdefault(key, []).append(base)
    if total == 0 or not groups:
        return None
    key, spellings = min(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return min(spellings) if len(spellings) * 2 >= total else None


def scoring_form(text: Optional[str]) -> str:
    """The comparison form used only for scoring: casefold, strip diacritics (o-macron -> o), collapse
    long vowels (ou / oo -> o, uu -> u), unify the multiplication sign with ``x`` and ``&`` with
    ``and``, drop punctuation, collapse whitespace. Apostrophes are removed, not turned into spaces
    (``Ren'ai`` -> ``renai``). Both sides of a comparison go through it."""
    if is_null_or_whitespace(text):
        return ""
    return _scoring_form_cached(text)


@lru_cache(maxsize=65536)
def _scoring_form_cached(text: str) -> str:
    s = lower_invariant(nfkc(text).replace("×", "x").replace("&", " and "))

    # Strip diacritics: decompose, drop non-spacing marks.
    chars = []
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.category(ch) == "Mn" or ch in _APOSTROPHES:
            continue
        chars.append(ch if is_letter_or_digit(ch) else " ")

    s = unicodedata.normalize("NFC", "".join(chars))
    s = s.replace("ou", "o").replace("oo", "o").replace("uu", "u")
    return _WHITESPACE.sub(" ", s).strip()


def _clean_title(text: str, edition_hints: List[str], keep_exclamation: bool = False) -> str:
    s = _VOLUME_TOKEN.sub(" ", text)
    s = _CHAPTER_TOKEN.sub(" ", s)
    s = _HASH_NUMBER.sub(" ", s)
    s = _BARE_UNIT_WORD.sub(" ", s)

    for m in _EDITION_PHRASE.finditer(s):
        edition_hints.append(m.group(0))
    s = _EDITION_PHRASE.sub(" ", s)

    for m in _FORMAT_WORD.finditer(s):
        edition_hints.append(m.group(0))
    s = _FORMAT_WORD.sub(" ", s)

    for word in _EDITION_WORDS:
        pattern = _EDITION_WORD_PATTERNS[word]
        if pattern.search(s):
            edition_hints.append(word)
            s = pattern.sub(" ", s)

    s = _WHITESPACE.sub(" ", s).strip()
    if keep_exclamation:
        s = s.lstrip(_TRIM_CHARS).rstrip(_TRIM_CHARS_NO_BANG)
        return s if s.rstrip("!").rstrip() else ""
    return s.strip(_TRIM_CHARS)


def _remove_bracket_groups(s: str) -> str:
    # Every bracket group (nested groups: repeat until stable, bounded), then any bracket character
    # left without its partner.
    for _ in range(4):
        nxt = _ANY_BRACKET_GROUP.sub(" ", s)
        if nxt == s:
            break
        s = nxt
    rest, _, _ = split_unmatched_bracket_tags(s)
    return _STRAY_BRACKET.sub(" ", rest)


def split_unmatched_bracket_tags(s: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Unmatched tag brackets (1.26.1): ``Family Given] Title`` - a YACReader jump-bar convention - and
    ``Title [Family Given``. Call after balanced groups are removed, so every bracket left is unmatched:
    the text before a lone closing bracket at the start, and after a lone opening bracket at the end,
    is a tag. Returns (rest, leading tag, trailing tag). Only splits when both sides have letters, so a
    title is never emptied."""
    leading = trailing = None
    first_close = _first_index(s, "])}")
    first_open = _first_index(s, "[({")
    if (first_close > 0 and (first_open < 0 or first_open > first_close)
            and any_letter(s[:first_close]) and _has_title_text(s[first_close + 1:])):
        leading = s[:first_close].strip()
        s = s[first_close + 1:]
    last_open = max(s.rfind(c) for c in "[({")
    last_close = max(s.rfind(c) for c in "])}")
    if (last_open >= 0 and last_close < last_open
            and _has_title_text(s[:last_open]) and any_letter(s[last_open + 1:])):
        trailing = s[last_open + 1:].strip()
        s = s[:last_open]
    return s, leading, trailing


def _first_index(s: str, chars: str) -> int:
    found = [i for i in (s.find(c) for c in chars) if i >= 0]
    return min(found) if found else -1


def _has_title_text(s: str) -> bool:
    """At least two letters once volume / chapter / number tokens are gone ("v01" is not title text)."""
    rest = _HASH_NUMBER.sub(" ", _CHAPTER_TOKEN.sub(" ", _VOLUME_TOKEN.sub(" ", s)))
    return sum(1 for c in rest if is_letter(c)) >= 2


def _trim_edges(s: str) -> str:
    return s.strip().strip(_TRIM_EDGE_CHARS).strip()


def _normalize_number(raw: str) -> Optional[str]:
    lower = raw.lower()
    if lower in _ROMAN_NUMERALS:
        return str(_ROMAN_NUMERALS.index(lower) + 1)
    # decimal.TryParse(AllowDecimalPoint, InvariantCulture): ASCII digits and one point only.
    if not lower or any(c not in "0123456789." for c in lower) or lower.count(".") > 1 or lower == ".":
        return None
    d = Decimal(lower).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{d:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _strip_archive_extension(s: str) -> str:
    for ext in _ARCHIVE_EXTENSIONS:
        if len(s) > len(ext) and s[-len(ext):].lower() == ext:
            return s[:-len(ext)]
    return s


def _count_words(s: str) -> int:
    return sum(1 for p in s.split(" ") if p.strip())
