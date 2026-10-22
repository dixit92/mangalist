"""What the List's (and the Download tab's) filter box finds besides a series' own titles: its alternative titles and its
creators (MangaPixer 1.26.0 "Alt-Title Search" and 1.45.0 "Facet Query" / "Author Search"). No Qt, no network.

**Alternative titles.** MangaPixer indexes every title of a linked record (the record's title and its alternative titles)
in an FTS5 *trigram* table and finds a series by a literal, case-insensitive substring of any of them
(``CatalogBrowseService.Search.cs``: the query is wrapped in double quotes, so FTS syntax in it is plain text). Its
"Series matches" row shows the SHORTEST matching title (``ORDER BY length(title), title``) as "aka <title>". Trigram means
what it says: a query of fewer than three characters matches no alternative title (:data:`MIN_ALT_CHARS`), and the match
folds case only - no accents, no punctuation, no spacing (``é`` is not ``e`` there; the shown titles are matched as before).

**Creators.** The typed field ``creator:Urasawa`` / ``creator:"Naoki Urasawa"`` (also ``creators:``; ``FacetSearchSyntax``:
a word or a double-quoted value, a space after the colon allowed, the name case-insensitive, an unknown prefix such as
``Re:Zero`` or a field with no value stays free text, at most ten fields, a value of at most 200 characters) names a
person by the key ``DeclaredFactsComparer.NameKey``: lower-case words without accents or punctuation, sorted - so
``ODA, Eiichiro`` and ``Eiichiro Oda`` are one name. MangaPixer compares the typed key to each of the creator's names
for equality; MangaList's filter box narrows as you type, so a typed word counts when it starts a word of the name
(``creator:Urasawa`` finds ``Naoki Urasawa``; the complete name finds it exactly as in MangaPixer). A creator's names are
the credited name and the author's other names: MangaPixer's export gives them (``record.creators``: name, other names)
for the series it knows; for a series it does not know they come from MangaUpdates (:mod:`mangalist.creator_names`). A
``Name (Other)`` / ``Name [Other]`` credit is every name in it (``NameKeys``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

MIN_ALT_CHARS = 3           # FTS5 trigram: a shorter query matches nothing
MAX_FIELDS = 10             # FacetSearchSyntax.MaxFields
MAX_VALUE_LENGTH = 200      # FacetSearchSyntax.MaxValueLength
CREATOR_FIELDS = frozenset({"creator", "creators"})

_BRACKETED = re.compile(r"[(\[](?P<inner>[^()\[\]]+)[)\]]")


# --- names ---------------------------------------------------------------------------------------------------


def fold_title(text: Optional[str]) -> str:
    """A title as the alternative-title match compares it: case-folded (trigram folds case, nothing else)."""
    return (text or "").casefold()


def name_key(name: Optional[str]) -> str:
    """``DeclaredFactsComparer.NameKey``: the words of *name* lower-cased, without accents (NFKD, combining marks
    dropped) and punctuation (anything that is not a letter or digit separates words), sorted, one space between."""
    words: List[str] = []
    current: List[str] = []
    for c in unicodedata.normalize("NFKD", name or ""):
        if unicodedata.category(c) == "Mn":
            continue
        if c.isalnum():
            current.append(c.lower())
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return " ".join(sorted(words))


def name_keys(name: Optional[str]) -> Tuple[str, ...]:
    """``DeclaredFactsComparer.NameKeys``: the keys a name is known by - the whole name, and for ``Name (Other)`` /
    ``Name [Other]`` also the name outside the brackets and each name inside them. Empty keys are dropped."""
    text = name or ""
    keys = [name_key(text)]
    outside = _BRACKETED.sub(" ", text)
    if outside != text:
        keys.append(name_key(outside))
        keys.extend(name_key(m.group("inner")) for m in _BRACKETED.finditer(text))
    return tuple(dict.fromkeys(k for k in keys if k))


# --- the query -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedQuery:
    """The filter box's text split into ``creator:`` values (as typed) and the free text rest (spaces joined as one)."""

    creators: Tuple[str, ...] = ()
    free: str = ""

    @property
    def empty(self) -> bool:
        return not self.creators and not self.free


def _read_value(text: str, at: int) -> Tuple[Optional[str], int]:
    """A quoted value (up to the closing quote; an unterminated quote runs to the end) or a bare word, from *at*."""
    if at >= len(text):
        return None, at
    if text[at] == '"':
        close = text.find('"', at + 1)
        return (text[at + 1:], len(text)) if close < 0 else (text[at + 1:close], close + 1)
    end = at
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[at:end], end


def parse_query(text: Optional[str]) -> ParsedQuery:
    """``FacetSearchSyntax.Parse`` for the one field MangaList has (``creator:``): ``creator:"Naoki Urasawa" monster`` ->
    one creator value and the free text ``monster``. Anything else - another prefix, a field with no value, a value
    over :data:`MAX_VALUE_LENGTH`, the eleventh field - stays free text, so a plain search never changes meaning by
    accident."""
    if not text or not text.strip():
        return ParsedQuery()
    fields: List[str] = []
    free: List[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        start, colon = i, -1
        while i < n and not text[i].isspace() and text[i] != '"':
            if text[i] == ":" and colon < 0:
                colon = i
            i += 1
        if colon > start and len(fields) < MAX_FIELDS and text[start:colon].lower() in CREATOR_FIELDS:
            end = i
            if colon + 1 < i:
                value: Optional[str] = text[colon + 1:i]
            else:                   # the word ends at the colon: a quoted value or the next word
                j = i
                while j < n and text[j] == " ":
                    j += 1
                value, end = _read_value(text, j)
            value = value.strip() if value is not None else None
            if value and len(value) <= MAX_VALUE_LENGTH:
                fields.append(value)
                i = end
                continue
        if i < n and text[i] == '"':        # not a field: the token (and a quoted part glued to it) is free text
            i = _read_value(text, i)[1]
        free.append(text[start:i])
    return ParsedQuery(tuple(fields), " ".join(free).strip())


# --- alternative titles --------------------------------------------------------------------------------------


def distinct_titles(titles: Iterable[Optional[str]]) -> Tuple[str, ...]:
    """*titles* trimmed, empty ones and case-insensitive repeats dropped, in order."""
    seen = set()
    out: List[str] = []
    for t in titles:
        text = (t or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return tuple(out)


def alt_title_hit(needle: str, titles: Sequence[str], folded: Optional[Sequence[str]] = None) -> Optional[str]:
    """The alternative title *needle* (case-folded text) is part of - the shortest, then the first in code point order,
    as MangaPixer shows it - or None. Fewer than :data:`MIN_ALT_CHARS` characters match nothing. *folded*: *titles*
    case-folded already (the List keeps them per row)."""
    if len(needle) < MIN_ALT_CHARS:
        return None
    best: Optional[str] = None
    for i, title in enumerate(titles):
        if needle in (folded[i] if folded is not None else title.casefold()):
            if best is None or (len(title), title) < (len(best), best):
                best = title
    return best


# --- creators ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Creator:
    """One credited person: the credited *name* and every name they are known by (*names*: the credited name, an author's
    other names), each with its :func:`name_keys` split into words."""

    name: str
    names: Tuple[str, ...]
    forms: Tuple[Tuple[str, Tuple[str, ...]], ...]      # (a name, the words of each of its keys) flattened: name -> keys

    @classmethod
    def of(cls, name: str, others: Iterable[str] = ()) -> "Creator":
        names = distinct_titles((name, *others))
        forms: List[Tuple[str, Tuple[str, ...]]] = []
        for n in names:
            forms.extend((n, tuple(k.split())) for k in name_keys(n))
        return cls(name, names, tuple(forms))


def creators_from_export(pairs: Iterable[Tuple[str, Iterable[str]]]) -> Tuple[Creator, ...]:
    """The creators of a MangaPixer record (``knowledge.creators``: name and other names of each)."""
    return tuple(Creator.of(name, others) for name, others in pairs)


def creator_words(value: str) -> Tuple[str, ...]:
    """The words of a typed creator value (its :func:`name_key`); () when it holds no letter or digit."""
    return tuple(name_key(value).split())


def _words_match(typed: Tuple[str, ...], words: Tuple[str, ...]) -> bool:
    return all(any(w.startswith(t) for w in words) for t in typed)


def creator_hit(typed: Tuple[str, ...], creators: Sequence[Creator]) -> Optional[Tuple[Creator, str]]:
    """The first creator one of whose names holds every typed word (a word starts a word of the name; the whole name in
    any order and with any accents or punctuation does as in MangaPixer), with the name that matched - or None.
    *typed*: :func:`creator_words`; no words match nothing (MangaPixer: an empty key resolves to no creator)."""
    if not typed:
        return None
    for c in creators:
        for name, words in c.forms:
            if _words_match(typed, words):
                return c, name
    return None


def creator_hint(creator: Creator, matched_name: str) -> str:
    """``by Naoki Urasawa`` - or, matched through another of their names, ``by Naoki Urasawa (aka 浦沢直樹)``."""
    if matched_name == creator.name:
        return f"by {creator.name}"
    return f"by {creator.name} (aka {matched_name})"


class CreatorIndex:
    """The creators of the series MangaList knows names for: MangaUpdates series id -> :class:`Creator` list (kept as
    :mod:`mangalist.creator_names` stored them; built per series on first use, then kept)."""

    def __init__(self, series: Optional[Mapping[str, Mapping]] = None, authors: Optional[Mapping[str, Mapping]] = None):
        self._series: Mapping[str, Mapping] = series or {}
        self._authors: Mapping[str, Mapping] = authors or {}
        self._built: Dict[str, Tuple[Creator, ...]] = {}

    def __bool__(self) -> bool:
        return bool(self._series)

    def known(self, mu_id: Optional[object]) -> bool:
        """Is this series' credit list stored (even an empty one)?"""
        return mu_id is not None and str(mu_id) in self._series

    def known_count(self, ids: Iterable[object]) -> int:
        return sum(1 for i in ids if self.known(i))

    def creators_of(self, mu_id: Optional[object]) -> Tuple[Creator, ...]:
        if mu_id is None:
            return ()
        key = str(mu_id)
        got = self._built.get(key)
        if got is None:
            got = self._built[key] = self._build(key)
        return got

    def _build(self, key: str) -> Tuple[Creator, ...]:
        record = self._series.get(key)
        if not isinstance(record, Mapping):
            return ()
        out: List[Creator] = []
        for credit in record.get("a") or ():
            try:
                author_id, name = credit[0], str(credit[1] or "").strip()
            except (IndexError, TypeError):
                continue
            if not name:
                continue
            author = self._authors.get(str(author_id)) if author_id is not None else None
            others = author.get("n") or () if isinstance(author, Mapping) else ()
            out.append(Creator.of(name, (str(o) for o in others)))
        return tuple(out)


# --- the filter box -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TextFilter:
    """The filter box's text, read once per change: the free text (case-folded, trimmed) a row's shown titles or alternative
    titles must hold, and the typed words of each ``creator:`` value its creators must hold."""

    needle: str = ""
    creators: Tuple[Tuple[str, ...], ...] = ()

    @classmethod
    def of(cls, text: Optional[str]) -> "TextFilter":
        parsed = parse_query(text)
        return cls(fold_title(parsed.free).strip(), tuple(creator_words(v) for v in parsed.creators))

    @property
    def empty(self) -> bool:
        return not self.needle and not self.creators

    def match(self, own: str, titles: Sequence[str] = (), folded: Optional[Sequence[str]] = None,
              creators: Callable[[], Sequence[Creator]] = lambda: ()) -> Optional[Tuple[str, ...]]:
        """Does a series pass? *own*: its shown titles and kind, case-folded (what the box always searched); *titles* /
        *folded*: its record's alternative titles; *creators*: asked only when a ``creator:`` value was typed. None when
        it does not pass; otherwise why, beyond its own titles: ``("aka Delicious in Dungeon", "by Naoki Urasawa")`` -
        empty when it passes by its own titles alone."""
        why: List[str] = []
        if self.needle and self.needle not in own:
            alt = alt_title_hit(self.needle, titles, folded)
            if alt is None:
                return None
            why.append(f"aka {alt}")
        if self.creators:
            credited = creators()
            for typed in self.creators:
                hit = creator_hit(typed, credited)
                if hit is None:
                    return None
                why.append(creator_hint(*hit))
        return tuple(dict.fromkeys(why))


def hint_text(why: Sequence[str]) -> str:
    """The line under a row that passed through other names: ``aka X`` / ``by Y`` / ``aka X  ·  by Y``."""
    return "  ·  ".join(why)


#: The filter boxes' wording (the List's placeholder says what it searches; the tip says how).
SEARCH_PLACEHOLDER = "Filter title, other titles, creator:"
SEARCH_TIP = ("Finds a series by its title, English title, kind or any other title of its MangaUpdates record "
              "(shown under the title as \"aka ...\"; three letters or more).\n"
              "creator:Urasawa or creator:\"Naoki Urasawa\" finds the series of a writer or artist, "
              "also by their other names; add words to narrow it further.\n"
              "Creator names come from MangaPixer's export, or from MangaUpdates (after a lookup) for series MangaPixer does not know.")
